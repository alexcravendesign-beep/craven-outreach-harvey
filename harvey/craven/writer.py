"""Craven's Writer: Harvey's Writer, grounded in the venue's observations.

Harvey grounds email 1 in company.description / tech_stack / signals_json,
none of which the observation model fills. This Writer renders the
company's latest observations as the facts the model may use, in the
Signal Playbook's lead order, and never hands the model the codes that
must not appear in copy (hygiene and management scores, the mailbox and
compliance facts).
"""

import logging

from harvey.agents.writer import Writer

logger = logging.getLogger("harvey.craven.writer")

# Playbook lead order; the first present leads the email.
# "Awaiting inspection" was dropped (Nick, 21 Oct 2026): a new registration
# may be a new venue or new hands at an old one; too ambiguous to write from.
LEAD_ORDER = ("CH_NEW_INCORPORATION_DAYS", "CH_OFFICER_CHANGE_DAYS",
              "FHRS_STRUCTURAL_SCORE", "FHRS_INSPECTION_AGE_DAYS", "MENU_EQUIPMENT_CLASS", "VENUE_CATEGORY")
NEVER_IN_COPY = {"FHRS_HYGIENE_SCORE", "FHRS_MANAGEMENT_SCORE", "ROLE_MAILBOX", "INDEPENDENT_VENUE",
                 "CH_INCORPORATED", "EMAIL_STATUS", "FOUND_IN_SERP", "FHRS_AWAITING_INSPECTION"}
# VENUE_DOSSIER is rendered separately (below the lead), never as a lead itself.
MENU_CONFIDENCE_FLOOR = 0.8

EQUIPMENT_WORDS = {
    "display_freezer": "an ice cream or display freezer", "bottle_cooler": "bottle coolers and a back-bar fridge",
    "serve_over": "a serve-over counter", "display_chiller": "a cake or patisserie display chiller",
    "cellar_cooling": "cellar cooling for cask ale", "prep_counter": "a refrigerated prep counter",
    "multideck": "an open-front multideck chiller",
}
CATEGORY_WORDS = {
    "pub": "a pub", "gastropub": "a gastropub", "bar": "a bar", "cafe": "a cafe", "coffee_shop": "a coffee shop",
    "restaurant": "a restaurant", "hotel": "a hotel", "bed_and_breakfast": "a bed and breakfast",
    "fast_food_restaurant": "a takeaway", "fish_and_chips_restaurant": "a fish and chip shop",
    "pizza_restaurant": "a pizza place", "indian_restaurant": "an Indian restaurant",
    "chinese_restaurant": "a Chinese restaurant", "bakery": "a bakery", "butcher": "a butcher's",
}


def fact_lines(observations: list[dict]) -> tuple[list[str], str]:
    """Plain-language facts from the latest observations, lead first.
    Returns (lines, lead_code)."""
    latest: dict[str, dict] = {}
    for o in observations:
        code = o.get("signal_code")
        if code in NEVER_IN_COPY or not code:
            continue
        if code not in latest or (o.get("observed_at") or "") > (latest[code].get("observed_at") or ""):
            latest[code] = o

    def present(code: str) -> bool:
        o = latest.get(code)
        if not o:
            return False
        if code == "FHRS_AWAITING_INSPECTION":
            return (o.get("value_num") or 0) == 1
        if code == "FHRS_STRUCTURAL_SCORE":
            return (o.get("value_num") or 0) >= 10
        if code == "FHRS_INSPECTION_AGE_DAYS":
            return (o.get("value_num") or 0) > 540
        if code == "CH_NEW_INCORPORATION_DAYS":
            return (o.get("value_num") or 999) < 120
        if code == "CH_OFFICER_CHANGE_DAYS":
            return (o.get("value_num") or 999) < 180
        if code == "MENU_EQUIPMENT_CLASS":
            return (o.get("confidence") or 0) >= MENU_CONFIDENCE_FLOOR and bool(o.get("value_text"))
        return True

    lines: list[str] = []
    lead = ""
    for code in LEAD_ORDER:
        if not present(code):
            continue
        o = latest[code]
        if code == "FHRS_AWAITING_INSPECTION":
            line = "The venue is newly registered and waiting for its first food hygiene inspection: it is opening, or has just opened (lead signal: fit-out window)."
        elif code == "CH_NEW_INCORPORATION_DAYS":
            line = f"A new company behind the venue was set up about {int(o['value_num'])} days ago (lead signal: opening soon)."
        elif code == "CH_OFFICER_CHANGE_DAYS":
            line = f"The venue changed hands or management about {int(o['value_num'])} days ago (lead signal: new owner, refit likely)."
        elif code == "FHRS_STRUCTURAL_SCORE":
            line = "Their last inspection noted the condition of the premises and equipment; older chilled storage is the usual cause (lead signal: equipment condition; never mention the inspection or any rating)."
        elif code == "FHRS_INSPECTION_AGE_DAYS":
            line = f"Their last inspection was about {int(o['value_num'] / 30)} months ago, so a visit is likely due (angle: temperature records and reliability, offered as help)."
        elif code == "MENU_EQUIPMENT_CLASS":
            klass = o.get("value_text") or ""
            line = f"Their menu or website shows they run {EQUIPMENT_WORDS.get(klass, klass)} (angle: the kit the menu depends on)."
        else:
            cat = o.get("value_text") or ""
            line = f"The venue is {CATEGORY_WORDS.get(cat, cat.replace('_', ' '))}."
        if not lead:
            lead = code
            lines.append(f"- LEAD: {line}")
        else:
            lines.append(f"- {line}")
    dossier = latest.get("VENUE_DOSSIER")
    if dossier and dossier.get("value_text"):
        try:
            import json as _json
            d = _json.loads(dossier["value_text"])
        except ValueError:
            d = {}
        if d.get("what_they_sell"):
            lines.append(f"- From their own website: {d['what_they_sell']}")
        for eq in (d.get("equipment") or [])[:2]:
            lines.append(f"- Their site implies {EQUIPMENT_WORDS.get(eq.get('class'), eq.get('class'))} (\"{eq.get('evidence')}\").")
        if d.get("proud_of"):
            lines.append(f"- They clearly value: {d['proud_of']}")
        if d.get("seasonal_note"):
            lines.append(f"- Their site says: {d['seasonal_note']}")
        if d.get("hook"):
            lines.append(f"- A possible honest opening (rewrite in your own words): {d['hook']}")
    energy = latest.get("ENERGY_CLAIM")
    if energy and (energy.get("value_num") or 0) == 1:
        lines.append("- Their website talks about energy or sustainability (second-sentence angle: running cost, with the real figure from product_knowledge only).")
    rating = latest.get("FHRS_RATING")
    if rating and (rating.get("value_num") or 0) == 5:
        lines.append("- They run a tight kitchen (second sentence only, as respect; never mention the rating itself).")
    return lines, lead


def season_lines(today=None) -> list[str]:
    """The date and what trade is coming, so the model never writes about the
    wrong season."""
    from datetime import date
    d = today or date.today()
    m = d.month
    if m in (10, 11):
        coming = "late autumn; the Christmas party bookings and December trade are a few weeks away, the busiest fortnight of the year for any kitchen or bar"
    elif m == 12:
        coming = "December; the Christmas trade is on now and kit that fails this month costs a fortnight of takings"
    elif m in (1, 2):
        coming = "the quiet weeks after Christmas; the time venues replace tired kit before Easter and the spring trade"
    elif m in (3, 4, 5):
        coming = "spring; the run-up to the summer season, when fridges work hardest"
    elif m in (6, 7, 8):
        coming = "summer; the hottest months for refrigeration and the busiest for coastal trade"
    else:
        coming = "early autumn; the summer rush is over and the Christmas bookings start soon"
    return [f"- Today is {d.strftime('%-d %B %Y')}: {coming}."]


SHOWROOM_FACTS = [
    "- We are Craven Cooling, a family refrigeration firm with a showroom at Unit 1, 304 Carmarthen Road, Swansea (ten minutes from most of Swansea). Tefcold-family kit is on the floor to see before buying.",
    "- Lisa gives prices the same day, ex VAT, with no visit needed. Kyle and the engineers install and service across South Wales and can look at tired kit before a busy period.",
]


class CravenWriter(Writer):
    async def _facts_for(self, prospect) -> tuple[list[str], str]:
        if not prospect.company_id:
            return [], ""
        try:
            obs = await self.state.get_observations(company_id=prospect.company_id, limit=500)
        except Exception as e:
            logger.debug(f"observations unavailable for {prospect.company_id}: {e}")
            return [], ""
        return fact_lines(obs)

    def _email1_prompt(self, prospect, venue_facts: list[str]) -> str:
        cfg = self.config
        base = self.brain.load_prompt(
            "writer",
            product_name=cfg.product.name, product_description=cfg.product.description,
            product_benefits="\n".join(f"- {b}" for b in cfg.product.key_benefits),
            product_pricing=cfg.product.pricing, persona_name=cfg.persona.name,
            persona_company=cfg.persona.company, persona_role=cfg.persona.role,
            persona_tone=cfg.persona.tone,
        ) or ""
        if self.skills:
            base += "\n\n" + self.skills
        facts = [f"- Venue: {prospect.company or 'the venue'} (this goes to its shared mailbox; no first name exists)"]
        facts += venue_facts + season_lines() + SHOWROOM_FACTS
        return base + f"""

Write the FIRST email Craven Cooling sends to this venue, following the CRAVEN COOLING RULES above
(four parts: who is writing and where from; why today, from the LEAD fact and the season; what we can
do for them, concretely; one easy ask). 80 to 120 words. Plain text. No links.

FACTS (everything you may say about them and about us; nothing else exists):
{chr(10).join(facts)}

Return ONLY JSON: {{"subject": "...", "body": "..."}}"""

    async def _write_personal_email(self, prospect) -> dict | None:
        venue_facts, lead = await self._facts_for(prospect)
        if not venue_facts:
            logger.info(f"Writer (Craven): no usable signal for {prospect.company}; no draft.")
            return None
        result = await self.brain.think_json(self._email1_prompt(prospect, venue_facts),
                                             session_id="harvey-writer", agent="writer", task="personal_email")
        if not isinstance(result, dict):
            return None
        subject = str(result.get("subject") or "").strip()
        body = str(result.get("body") or "").strip()
        if not subject or not body:
            return None
        return {"subject": subject[:120], "body": body[:2000], "lead": lead}


async def draft_one(state, brain, config, env, company_id: str, *, settings=None) -> dict | None:
    """One grounded first email for a company's mailbox prospect, staged in
    the Outbox as pending_review regardless of the prospect's email status.
    For reading drafts before any verifier or mailbox exists; it never sends
    (there is no provider) and the gate still judges it at send time."""
    from datetime import datetime, timezone
    writer = CravenWriter(brain, state, config, env)
    writer.skills = brain.load_skills_for_agent("writer")
    company = await state.get_company(company_id)
    if company is None:
        return None
    prospects = [p for p in await state.get_contacts_for_company(company_id)]
    if not prospects:
        logger.info(f"draft: {company.name} has no prospect (mailbox) yet.")
        return None
    prospect = prospects[0]
    draft = await writer._write_personal_email(prospect)
    if not draft:
        return None
    item_id = await state.add_outbox_item(
        prospect_id=prospect.id, campaign_id="", step=1, to_email=prospect.email,
        subject=draft["subject"], body=draft["body"],
        send_at=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        status="pending_review", provider=getattr(config.channels.email, "provider", "") or "")
    return {"outbox_id": item_id, "company": company.name, "to": prospect.email,
            "email_status": prospect.email_status, "lead": draft.get("lead", ""),
            "subject": draft["subject"], "body": draft["body"]}
