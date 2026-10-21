"""FHRS — the Food Hygiene Rating Scheme (Food Standards Agency, open data).

Every UK food business is registered with its council and rated; new venues
appear as "AwaitingInspection" before their first visit — the fit-out window
and the strongest trigger in the catalogue. The API needs no key: one call
per local authority returns every establishment (pageSize 5000), so we pull
the authorities covering the pilot area, cache them, index by postcode and
match our venues locally. A match is recorded only above the confidence
floor, with the FHRS page as evidence; a no-match is remembered.
"""

import json
import logging
import time
from pathlib import Path

import httpx

from harvey.craven.collectors.common import (companies_needing, days_since, mark_enrichment, name_similarity,
                                             normalise_postcode, pois_by_external_ids)
from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.craven.fhrs")

API = "https://api.ratings.food.gov.uk"
HEADERS = {"x-api-version": "2", "accept": "application/json", "user-agent": "craven-outreach/0.1 (FHRS open data)"}
CACHE_DIR = PROJECT_ROOT / "data" / "fhrs"
SOURCE = "fhrs"
EVIDENCE = "https://ratings.food.gov.uk/business/en-GB/{id}"
RATING_KEYS = ("Hygiene", "Structural", "ConfidenceInManagement")


class FhrsClient:
    def __init__(self, authority_ids, cache_days: int = 7, transport=None, cache_dir: Path | None = None):
        self.authority_ids = list(authority_ids)
        self.cache_days = cache_days
        self.transport = transport
        self.cache_dir = cache_dir or CACHE_DIR
        self._index: dict[str, list[dict]] | None = None   # full postcode -> establishments

    async def _authority(self, lid: int) -> list[dict]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"authority-{lid}.json"
        if path.exists() and time.time() - path.stat().st_mtime < self.cache_days * 86400:
            return json.loads(path.read_text(encoding="utf-8"))
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), transport=self.transport, headers=HEADERS) as c:
            r = await c.get(f"{API}/Establishments", params={"localAuthorityId": lid, "pageSize": 5000, "pageNumber": 1})
            r.raise_for_status()
            data = r.json()
        ests = data.get("establishments") or []
        meta = data.get("meta") or {}
        if meta.get("totalPages", 1) > 1:
            logger.warning(f"FHRS authority {lid}: {meta.get('totalCount')} establishments exceed one page; only the first 5000 cached")
        path.write_text(json.dumps(ests), encoding="utf-8")
        logger.info(f"FHRS authority {lid}: {len(ests)} establishments cached")
        return ests

    async def index(self) -> dict[str, list[dict]]:
        if self._index is None:
            idx: dict[str, list[dict]] = {}
            for lid in self.authority_ids:
                for e in await self._authority(lid):
                    idx.setdefault(normalise_postcode(e.get("PostCode") or ""), []).append(e)
            self._index = idx
        return self._index

    async def match(self, name: str, postcode: str) -> tuple[dict | None, float]:
        """Best establishment at this postcode by name similarity."""
        pc = normalise_postcode(postcode)
        if not pc:
            return None, 0.0
        best, score = None, 0.0
        for e in (await self.index()).get(pc, []):
            s = name_similarity(name, e.get("BusinessName") or "")
            if s > score:
                best, score = e, s
        return best, score


def observations_for(est: dict, company_id: str, confidence: float, run_id: str) -> list[dict]:
    """Rows for one matched establishment (codes the catalogue defines)."""
    url = EVIDENCE.format(id=est.get("FHRSID"))
    base = {"company_id": company_id, "collector": SOURCE, "confidence": confidence, "evidence_url": url,
            "run_id": run_id, "value_num": None, "value_text": ""}
    rows = []
    rating = str(est.get("RatingValue") or "")
    if rating.isdigit():
        rows.append({**base, "signal_code": "FHRS_RATING", "value_num": float(rating)})
        rows.append({**base, "signal_code": "FHRS_AWAITING_INSPECTION", "value_num": 0.0})
    elif rating.lower() == "awaitinginspection":
        rows.append({**base, "signal_code": "FHRS_AWAITING_INSPECTION", "value_num": 1.0, "value_text": rating})
    scores = est.get("scores") or {}
    for key, code in zip(RATING_KEYS, ("FHRS_HYGIENE_SCORE", "FHRS_STRUCTURAL_SCORE", "FHRS_MANAGEMENT_SCORE")):
        if scores.get(key) is not None:
            rows.append({**base, "signal_code": code, "value_num": float(scores[key])})
    if est.get("BusinessType"):
        rows.append({**base, "signal_code": "FHRS_BUSINESS_TYPE", "value_text": est["BusinessType"]})
    age = days_since(est.get("RatingDate") or "")
    if age is not None and rating.isdigit():
        rows.append({**base, "signal_code": "FHRS_INSPECTION_AGE_DAYS", "value_num": float(age)})
    return rows


async def enrich_fhrs(state, settings, supabase, limit: int = 200, client: FhrsClient | None = None) -> dict:
    """Match owned-data venues to FHRS and record the signals. Returns counts."""
    cfg = settings.enrichment
    client = client or FhrsClient(cfg.fhrs_authority_ids, cfg.fhrs_cache_days)
    confirmed = await state.confirmed_signal_codes()
    companies = await companies_needing(state, SOURCE, cfg.recheck_days, limit)
    if not companies:
        return {"companies": 0, "matched": 0, "observations": 0}
    pois = await pois_by_external_ids(supabase, [c["external_id"] for c in companies])
    run_id = await state.start_run("fhrs", provider="fhrs", params={"companies": len(companies)})
    matched = obs_total = 0
    try:
        for c in companies:
            poi = pois.get(c["external_id"]) or {}
            postcode = poi.get("postcode") or ""
            est, score = await client.match(c["name"], postcode)
            if est is None or score < cfg.fhrs_match_floor:
                await mark_enrichment(state, c["id"], SOURCE, False, f"no match at {postcode} (best {score:.2f})")
                continue
            rows = [r for r in observations_for(est, c["id"], score, run_id) if r["signal_code"] in confirmed]
            if rows:
                obs_total += await state.add_observations(rows, run_id=run_id)
            matched += 1
            await mark_enrichment(state, c["id"], SOURCE, True, f"FHRSID {est.get('FHRSID')} ({score:.2f}) {est.get('RatingValue')}")
        await state.finish_run(run_id, status="completed", records=matched, cost_usd=0.0)
    except Exception as e:
        await state.finish_run(run_id, status="failed", records=matched, cost_usd=0.0, error=str(e))
        raise
    logger.info(f"FHRS: {matched}/{len(companies)} matched, {obs_total} observation(s)")
    return {"companies": len(companies), "matched": matched, "observations": obs_total}
