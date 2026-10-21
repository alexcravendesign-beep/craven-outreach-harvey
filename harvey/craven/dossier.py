"""Venue dossiers: one model call per venue over its own pages.

The deterministic profiler guesses an equipment class from keywords at low
confidence. The dossier reads the venue's homepage, menu and contact pages
once and returns structured facts with the line that proves each: what
they sell, service style, the kit the menu implies, what they are proud
of, an honest one-line hook, and any business address printed on the
site. Stored as a VENUE_DOSSIER observation (JSON) plus a high-confidence
MENU_EQUIPMENT_CLASS and, when printed on the site, a ROLE_MAILBOX.
Runs on the operator's subscription through Harvey's Brain, under its
quota gate; it must run from the GUI session on the mini (keychain).
"""

import asyncio
import json
import logging

from harvey.craven import compliance
from harvey.craven.collectors.common import mark_enrichment
from harvey.craven.pages import CONTACT_PATHS, MENU_PATHS, VenuePages, page_text

logger = logging.getLogger("harvey.craven.dossier")

SOURCE = "dossier"
EQUIPMENT_CLASSES = ("display_freezer", "bottle_cooler", "serve_over", "display_chiller", "cellar_cooling",
                     "prep_counter", "multideck", "none")

DOSSIER_SIGNAL = {
    "code": "VENUE_DOSSIER", "label": "Venue dossier (model-read)",
    "description": ("What the venue sells, how it serves, the kit its menu implies with the proving line, what it is "
                    "proud of, and an honest one-line hook: one model call over the venue's own pages. "
                    "The writer's richest source."),
    "category": "profile", "value_type": "text", "collector": SOURCE,
    "cost_note": "free (one model call per venue on your subscription)", "confidence_floor": 0.0,
}

PROMPT = """You are reading a UK hospitality venue's own website for a local commercial refrigeration supplier
(Craven Cooling, Swansea). Extract facts only. Never invent; if a field is unknown, use null or [].

Venue name: {name}
Overture category: {category}

PAGES (text):
{pages}

Return ONLY JSON with these keys:
{{
  "what_they_sell": "one plain sentence (cuisine / offer / who it is for)",
  "service_style": "one of: table service | counter | takeaway | bar-led | mixed | unknown",
  "equipment": [{{"class": "one of {classes}", "evidence": "a short phrase copied from the page that implies it"}}],
  "proud_of": "one sentence on what the venue clearly values or boasts about, or null",
  "hook": "one honest sentence a local supplier could open with, grounded only in the pages, or null",
  "business_emails": ["addresses printed on the pages, verbatim, that are the business's own, else []"],
  "seasonal_note": "anything the pages say about events, Christmas, bookings, opening hours changes, or null"
}}"""


def parse_dossier(result) -> dict | None:
    if not isinstance(result, dict):
        return None
    d = {
        "what_they_sell": (result.get("what_they_sell") or "")[:300] or None,
        "service_style": (result.get("service_style") or "unknown")[:30],
        "equipment": [], "proud_of": (result.get("proud_of") or None),
        "hook": (result.get("hook") or None), "business_emails": [], "seasonal_note": (result.get("seasonal_note") or None),
    }
    for e in result.get("equipment") or []:
        if isinstance(e, dict) and e.get("class") in EQUIPMENT_CLASSES and e["class"] != "none":
            d["equipment"].append({"class": e["class"], "evidence": str(e.get("evidence") or "")[:160]})
    for e in result.get("business_emails") or []:
        if isinstance(e, str) and "@" in e:
            d["business_emails"].append(e.strip().lower())
    return d


async def companies_needing_dossier(state, recheck_days: int, limit: int) -> list[dict]:
    async with state._connect() as db:
        async with db.execute(
            """SELECT c.id, c.name, c.website, c.industry FROM companies c
               LEFT JOIN craven_enrichment e ON e.company_id = c.id AND e.source = ?
               WHERE c.website != '' AND c.source = 'discover:overture_supabase'
                 AND (e.checked_at IS NULL OR e.checked_at < datetime('now', ?))
               ORDER BY e.checked_at IS NOT NULL, c.created_at LIMIT ?""",
            (SOURCE, f"-{int(recheck_days)} days", int(limit))) as cur:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]


async def build_dossiers(state, brain, settings, limit: int = 100, pages: VenuePages | None = None,
                         max_percent: float = 50.0) -> dict:
    pages = pages or VenuePages()
    confirmed = await state.confirmed_signal_codes()
    role_parts = settings.compliance.role_local_parts
    companies = await companies_needing_dossier(state, settings.enrichment.recheck_days, limit)
    if not companies:
        return {"companies": 0, "dossiers": 0, "mailboxes": 0}
    run_id = await state.start_run("dossier", provider="claude", params={"companies": len(companies)})
    done = mailboxes = 0
    try:
        for c in companies:
            while not await brain.is_within_budget(max_percent=max_percent):
                logger.info("dossier: over the subscription budget; waiting 10 minutes")
                await asyncio.sleep(600)
            fetched = await pages.fetch(c["website"], CONTACT_PATHS + MENU_PATHS)
            if not fetched:
                await mark_enrichment(state, c["id"], SOURCE, False, "site unreachable")
                continue
            prompt = PROMPT.format(name=c["name"], category=c.get("industry") or "", pages=page_text(fetched),
                                   classes=" | ".join(EQUIPMENT_CLASSES))
            result = await brain.think_json(prompt, session_id="harvey-dossier", agent="dossier", task="venue_dossier")
            d = parse_dossier(result)
            if d is None:
                await mark_enrichment(state, c["id"], SOURCE, False, "no usable reply from the model")
                continue
            rows = []
            base = {"company_id": c["id"], "collector": SOURCE, "run_id": run_id, "value_num": None,
                    "evidence_url": next(iter(fetched)), "confidence": 0.9}
            if "VENUE_DOSSIER" in confirmed:
                rows.append({**base, "signal_code": "VENUE_DOSSIER", "value_text": json.dumps(d, ensure_ascii=False)[:4000]})
            if d["equipment"] and "MENU_EQUIPMENT_CLASS" in confirmed:
                top = d["equipment"][0]
                rows.append({**base, "signal_code": "MENU_EQUIPMENT_CLASS", "value_text": top["class"],
                             "confidence": 0.9})
            added_mailbox = ""
            if "ROLE_MAILBOX" in confirmed:
                for e in d["business_emails"]:
                    ok, why = compliance.is_business_mailbox(e, c["name"], c["website"], role_parts)
                    if ok:
                        rows.append({**base, "signal_code": "ROLE_MAILBOX", "value_text": e, "confidence": 0.85})
                        added_mailbox = e
                        break
            if rows:
                await state.add_observations(rows, run_id=run_id)
            if added_mailbox:
                mailboxes += 1
            done += 1
            await mark_enrichment(state, c["id"], SOURCE, True,
                                  f"{(d['what_they_sell'] or '')[:60]} | {[e['class'] for e in d['equipment']][:2]} | {added_mailbox or '-'}")
            logger.info(f"dossier: {c['name']} -> {[e['class'] for e in d['equipment']][:2]} {added_mailbox or ''}")
        await state.finish_run(run_id, status="completed", records=done, cost_usd=0.0)
    except Exception as e:
        await state.finish_run(run_id, status="failed", records=done, cost_usd=0.0, error=str(e))
        raise
    logger.info(f"dossiers: {done}/{len(companies)} built, {mailboxes} mailbox(es) found")
    return {"companies": len(companies), "dossiers": done, "mailboxes": mailboxes}
