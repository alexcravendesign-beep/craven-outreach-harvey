"""Find a venue's own mailbox on its website when the owned data has none.

Homepage + contact pages, every address found, filtered by the business
mailbox rule (role local part, the venue's own domain, or named after the
venue). Nothing is guessed; only addresses printed on the site count.
"""

import logging

from harvey.craven import compliance
from harvey.craven.collectors.common import mark_enrichment, now_iso
from harvey.craven.pages import CONTACT_PATHS, VenuePages, emails_in

logger = logging.getLogger("harvey.craven.mailbox_scrape")

SOURCE = "mailbox_scrape"
PREFERRED = ("info", "enquiries", "enquiry", "bookings", "hello", "contact", "reservations", "office", "sales")


def pick_business_mailbox(candidates: list[str], venue_name: str, website: str, role_parts) -> tuple[str, str]:
    """(email, reason) for the best business mailbox among candidates, or ("", "")."""
    scored = []
    for e in candidates:
        ok, why = compliance.is_business_mailbox(e, venue_name, website, role_parts)
        if ok:
            local = compliance.local_part(e)
            rank = PREFERRED.index(local) if local in PREFERRED else len(PREFERRED)
            scored.append((rank, 0 if why == "role local part" else 1, e, why))
    if not scored:
        return "", ""
    scored.sort()
    return scored[0][2], scored[0][3]


async def companies_without_mailbox(state, recheck_days: int, limit: int) -> list[dict]:
    async with state._connect() as db:
        async with db.execute(
            """SELECT c.id, c.name, c.website FROM companies c
               LEFT JOIN craven_enrichment e ON e.company_id = c.id AND e.source = ?
               WHERE c.website != '' AND c.source = 'discover:overture_supabase'
                 AND NOT EXISTS (SELECT 1 FROM observations o WHERE o.company_id = c.id AND o.signal_code = 'ROLE_MAILBOX')
                 AND (e.checked_at IS NULL OR e.checked_at < datetime('now', ?))
               ORDER BY e.checked_at IS NOT NULL, c.created_at LIMIT ?""",
            (SOURCE, f"-{int(recheck_days)} days", int(limit))) as cur:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]


async def scrape_mailboxes(state, settings, limit: int = 200, pages: VenuePages | None = None) -> dict:
    pages = pages or VenuePages()
    role_parts = settings.compliance.role_local_parts
    confirmed = await state.confirmed_signal_codes()
    companies = await companies_without_mailbox(state, settings.enrichment.recheck_days, limit)
    if not companies:
        return {"companies": 0, "found": 0}
    run_id = await state.start_run("mailbox_scrape", provider="http", params={"companies": len(companies)})
    found = 0
    try:
        for c in companies:
            fetched = await pages.fetch(c["website"], CONTACT_PATHS)
            candidates = []
            evidence = ""
            for url, html in fetched.items():
                for e in emails_in(html):
                    if e not in candidates:
                        candidates.append(e)
                        evidence = evidence or url
            email, why = pick_business_mailbox(candidates, c["name"], c["website"], role_parts)
            if not email:
                await mark_enrichment(state, c["id"], SOURCE, False,
                                      f"{len(fetched)} page(s), {len(candidates)} address(es), none the venue's")
                continue
            if "ROLE_MAILBOX" in confirmed:
                await state.add_observations([{
                    "company_id": c["id"], "signal_code": "ROLE_MAILBOX", "collector": SOURCE,
                    "value_num": None, "value_text": email, "confidence": 0.95 if why == "role local part" else 0.85,
                    "evidence_url": evidence or c["website"], "run_id": run_id}], run_id=run_id)
            await mark_enrichment(state, c["id"], SOURCE, True, f"{email} ({why})")
            found += 1
            logger.info(f"mailbox: {c['name']} -> {email} ({why})")
        await state.finish_run(run_id, status="completed", records=found, cost_usd=0.0)
    except Exception as e:
        await state.finish_run(run_id, status="failed", records=found, cost_usd=0.0, error=str(e))
        raise
    logger.info(f"mailbox scrape: {found}/{len(companies)} found")
    return {"companies": len(companies), "found": found}
