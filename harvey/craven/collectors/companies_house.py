"""Companies House — the UK register, free API key, 600 requests per 5 minutes.

Two jobs. (1) For a venue we know, find its registered company: that proves
it is a corporate subscriber under PECR (CH_INCORPORATED, recorded only on a
strong match) and gives the age of the company and of its newest officer.
(2) Sweep recent hospitality incorporations in the area: an "opening soon"
company the venue data cannot know about yet.

A search hit counts as a match only with evidence beyond the name: the
registered office or a SIC code in the hospitality set, or the postcode
area. Weak matches are not recorded; nothing is invented.
"""

import asyncio
import logging
import os
import time
from collections import deque

import httpx

from harvey.craven.collectors.common import (companies_needing, days_since, mark_enrichment, name_similarity,
                                             normalise_postcode, pois_by_external_ids)

logger = logging.getLogger("harvey.craven.companies_house")

API = "https://api.company-information.service.gov.uk"
SOURCE = "companies_house"
EVIDENCE = "https://find-and-update.company-information.service.gov.uk/company/{number}"
HOSPITALITY_SIC_PREFIXES = ("5610", "5621", "5629", "5630", "5510", "5520", "4711", "4721", "4722", "4724")


class RateLimiter:
    """At most ``limit`` calls per ``window`` seconds (Companies House: 600 / 300 s)."""

    def __init__(self, limit: int = 600, window: float = 300.0, clock=time.monotonic, sleeper=asyncio.sleep):
        self.limit, self.window, self.clock, self.sleeper = limit, window, clock, sleeper
        self.calls: deque[float] = deque()

    async def wait(self) -> None:
        while True:
            now = self.clock()
            while self.calls and now - self.calls[0] >= self.window:
                self.calls.popleft()
            if len(self.calls) < self.limit:
                self.calls.append(now)
                return
            await self.sleeper(self.window - (now - self.calls[0]) + 0.05)


class CompaniesHouseClient:
    def __init__(self, api_key: str, transport=None, limiter: RateLimiter | None = None):
        self.api_key = api_key
        self.transport = transport
        self.limiter = limiter or RateLimiter()

    @classmethod
    def from_env(cls, env=None):
        key = (env if env is not None else os.environ).get("COMPANIES_HOUSE_API_KEY", "").strip()
        return cls(key) if key else None

    async def _get(self, path: str, params: dict) -> dict:
        await self.limiter.wait()
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), transport=self.transport,
                                     auth=(self.api_key, "")) as c:
            r = await c.get(f"{API}{path}", params=params)
            if r.status_code == 429:
                raise RuntimeError("Companies House rate limit hit (429)")
            r.raise_for_status()
            return r.json()

    async def search(self, name: str, town: str, size: int = 10) -> list[dict]:
        data = await self._get("/search/companies", {"q": f"{name} {town}".strip(), "items_per_page": size})
        return data.get("items") or []

    async def company(self, number: str) -> dict:
        return await self._get(f"/company/{number}", {})

    async def officers(self, number: str) -> list[dict]:
        data = await self._get(f"/company/{number}/officers", {"items_per_page": 50})
        return data.get("items") or []

    async def advanced_search(self, sic_codes: list[str], incorporated_from: str, location: str,
                              start_index: int = 0, size: int = 100) -> list[dict]:
        data = await self._get("/advanced-search/companies", {
            "sic_codes": ",".join(sic_codes), "incorporated_from": incorporated_from,
            "location": location, "company_status": "active", "size": size, "start_index": start_index})
        return data.get("items") or []


def match_confidence(venue_name: str, venue_postcode: str, item: dict) -> float:
    """How sure we are that a search hit is this venue's company."""
    sim = name_similarity(venue_name, item.get("title") or item.get("company_name") or "")
    if sim < 0.6:
        return 0.0
    addr = item.get("address") or item.get("registered_office_address") or {}
    pc = normalise_postcode(addr.get("postal_code") or "")
    venue_pc = normalise_postcode(venue_postcode)
    evidence = 0.0
    if venue_pc and pc == venue_pc:
        evidence = 0.3
    elif venue_pc and pc and pc.split(" ")[0] == venue_pc.split(" ")[0]:
        evidence = 0.15
    sics = item.get("sic_codes") or []
    if any(str(c).startswith(HOSPITALITY_SIC_PREFIXES) for c in sics):
        evidence = max(evidence, 0.2) if evidence < 0.3 else evidence + 0.05
    if evidence == 0.0:
        return 0.0  # a name alone is never enough
    return round(min(1.0, 0.55 * sim + 0.45 * (evidence / 0.35)), 3)


async def enrich_companies_house(state, settings, supabase, client: CompaniesHouseClient | None = None,
                                 limit: int = 100) -> dict:
    cfg = settings.enrichment
    client = client or CompaniesHouseClient.from_env()
    if client is None:
        logger.info("Companies House: no COMPANIES_HOUSE_API_KEY; skipping (free key at developer.company-information.service.gov.uk)")
        return {"skipped": "no key"}
    confirmed = await state.confirmed_signal_codes()
    companies = await companies_needing(state, SOURCE, cfg.recheck_days, limit)
    if not companies:
        return {"companies": 0, "matched": 0, "observations": 0}
    pois = await pois_by_external_ids(supabase, [c["external_id"] for c in companies])
    run_id = await state.start_run("companies_house", provider="companies_house", params={"companies": len(companies)})
    matched = obs_total = 0
    try:
        for c in companies:
            poi = pois.get(c["external_id"]) or {}
            postcode = poi.get("postcode") or ""
            try:
                items = await client.search(c["name"], poi.get("locality") or cfg.companies_house_location)
            except Exception as e:
                logger.warning(f"Companies House search failed for {c['name']}: {e}")
                continue
            best, conf = None, 0.0
            for it in items:
                s = match_confidence(c["name"], postcode, it)
                if s > conf:
                    best, conf = it, s
            if best is None or conf < 0.8:
                await mark_enrichment(state, c["id"], SOURCE, False, f"best {conf:.2f}")
                continue
            number = best.get("company_number") or ""
            url = EVIDENCE.format(number=number)
            base = {"company_id": c["id"], "collector": SOURCE, "confidence": conf, "evidence_url": url,
                    "run_id": run_id, "value_num": None, "value_text": ""}
            rows = [{**base, "signal_code": "CH_INCORPORATED", "value_text": number}]
            age = days_since(best.get("date_of_creation") or "")
            if age is not None:
                rows.append({**base, "signal_code": "CH_NEW_INCORPORATION_DAYS", "value_num": float(age)})
            try:
                officers = await client.officers(number)
                newest = max((o.get("appointed_on") or "" for o in officers if not o.get("resigned_on")), default="")
                oage = days_since(newest) if newest else None
                if oage is not None:
                    rows.append({**base, "signal_code": "CH_OFFICER_CHANGE_DAYS", "value_num": float(oage)})
            except Exception as e:
                logger.debug(f"officers lookup failed for {number}: {e}")
            rows = [r for r in rows if r["signal_code"] in confirmed]
            if rows:
                obs_total += await state.add_observations(rows, run_id=run_id)
            matched += 1
            await mark_enrichment(state, c["id"], SOURCE, True, f"{number} ({conf:.2f})")
        await state.finish_run(run_id, status="completed", records=matched, cost_usd=0.0)
    except Exception as e:
        await state.finish_run(run_id, status="failed", records=matched, cost_usd=0.0, error=str(e))
        raise
    logger.info(f"Companies House: {matched}/{len(companies)} matched, {obs_total} observation(s)")
    return {"companies": len(companies), "matched": matched, "observations": obs_total}


async def sweep_new_incorporations(state, settings, client: CompaniesHouseClient | None = None) -> dict:
    """New hospitality companies in the area: companies the venue data can't
    know yet. Creates them (source discover:companies_house) with
    CH_INCORPORATED + CH_NEW_INCORPORATION_DAYS."""
    from datetime import date, timedelta
    from harvey.models.company import Company
    cfg = settings.enrichment
    client = client or CompaniesHouseClient.from_env()
    if client is None:
        return {"skipped": "no key"}
    confirmed = await state.confirmed_signal_codes()
    since = (date.today() - timedelta(days=cfg.companies_house_sweep_days)).isoformat()
    run_id = await state.start_run("companies_house_sweep", provider="companies_house",
                                   params={"since": since, "location": cfg.companies_house_location})
    created = known = 0
    start = 0
    try:
        while True:
            items = await client.advanced_search(cfg.companies_house_sic, since, cfg.companies_house_location, start)
            if not items:
                break
            for it in items:
                number = it.get("company_number") or ""
                if not number:
                    continue
                ext = f"ch:{number}"
                existing = await state.get_company_by_external_id(ext)
                if existing:
                    known += 1
                    continue
                addr = it.get("registered_office_address") or {}
                cid = await state.add_company(Company(
                    name=it.get("company_name") or number, industry="new_hospitality_company",
                    location=normalise_postcode(addr.get("postal_code") or "").split(" ")[0],
                    source="discover:companies_house", source_url=EVIDENCE.format(number=number), external_id=ext))
                base = {"company_id": cid, "collector": SOURCE, "confidence": 1.0, "run_id": run_id,
                        "evidence_url": EVIDENCE.format(number=number), "value_num": None, "value_text": ""}
                rows = [{**base, "signal_code": "CH_INCORPORATED", "value_text": number}]
                age = days_since(it.get("date_of_creation") or "")
                if age is not None:
                    rows.append({**base, "signal_code": "CH_NEW_INCORPORATION_DAYS", "value_num": float(age)})
                rows = [r for r in rows if r["signal_code"] in confirmed]
                if rows:
                    await state.add_observations(rows, run_id=run_id)
                created += 1
            if len(items) < 100:
                break
            start += len(items)
        await state.finish_run(run_id, status="completed", records=created, cost_usd=0.0)
    except Exception as e:
        await state.finish_run(run_id, status="failed", records=created, cost_usd=0.0, error=str(e))
        raise
    logger.info(f"Companies House sweep since {since}: {created} new, {known} known")
    return {"created": created, "known": known, "since": since}
