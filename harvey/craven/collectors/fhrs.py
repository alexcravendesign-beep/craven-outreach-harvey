"""FHRS — the Food Hygiene Rating Scheme (Food Standards Agency, open data).

Every UK food business is registered with its council and rated; new venues
appear as "AwaitingInspection" before their first visit — the fit-out window
and the strongest trigger in the catalogue. The API needs no key: one call
per local authority returns every establishment (pageSize 5000), so we pull
the authorities covering the pilot area, cache them, index by postcode and by
a coarse lat/lng grid, and match our venues locally. Candidates are the
establishments at the venue's postcode or within a short walk of its own
coordinates (the register and Overture often disagree on the postcode of
one building); the score is name similarity, nudged down when the register
puts the business more than 50 m away. A match is recorded only above the
confidence floor, with the FHRS page as evidence; a no-match is remembered.
"""

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from harvey.craven.collectors.common import (GeoGrid, companies_needing, days_since, haversine_m, mark_enrichment,
                                             name_similarity, normalise_postcode, parse_latlng,
                                             pois_by_external_ids)
from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.craven.fhrs")

API = "https://api.ratings.food.gov.uk"
HEADERS = {"x-api-version": "2", "accept": "application/json", "user-agent": "craven-outreach/0.1 (FHRS open data)"}
CACHE_DIR = PROJECT_ROOT / "data" / "fhrs"
SOURCE = "fhrs"
EVIDENCE = "https://ratings.food.gov.uk/business/en-GB/{id}"
RATING_KEYS = ("Hygiene", "Structural", "ConfidenceInManagement")
DEFAULT_RADIUS_M = 120.0
# Beyond this distance the register's point and ours disagree enough to cost
# a little confidence: 0.0005/m, capped so a same-words match (0.9) never
# drops under the 0.85 floor on distance alone.
PENALTY_FREE_M = 50.0
PENALTY_PER_M = 0.0005
PENALTY_CAP = 0.04


@dataclass
class FhrsMatch:
    establishment: dict | None
    score: float                    # name similarity after the distance penalty, 0..1
    distance_m: float | None        # register point to venue point; None when either side lacks one
    postcode_match: bool            # the candidate came in on the postcode index

    @property
    def detail(self) -> str:
        if self.establishment is None:
            return f"best {self.score:.2f}"
        via = "postcode" if self.postcode_match else "radius"
        dist = f", {self.distance_m:.0f} m" if self.distance_m is not None else ""
        return f"{self.score:.2f} via {via}{dist}"


class FhrsClient:
    def __init__(self, authority_ids, cache_days: int = 7, transport=None, cache_dir: Path | None = None,
                 radius_m: float = DEFAULT_RADIUS_M):
        self.authority_ids = list(authority_ids)
        self.cache_days = cache_days
        self.transport = transport
        self.cache_dir = cache_dir or CACHE_DIR
        self.radius_m = float(radius_m)
        self._index: dict[str, list[dict]] | None = None   # full postcode -> establishments
        self._grid: GeoGrid | None = None                  # geocoded establishments by coarse cell

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
            grid = GeoGrid(self.radius_m)
            located = 0
            for lid in self.authority_ids:
                for e in await self._authority(lid):
                    idx.setdefault(normalise_postcode(e.get("PostCode") or ""), []).append(e)
                    point = establishment_point(e)
                    if point:
                        grid.add(point[0], point[1], e)
                        located += 1
            self._index, self._grid = idx, grid
            logger.info(f"FHRS index: {sum(len(v) for v in idx.values())} establishments, {located} geocoded")
        return self._index

    async def grid(self) -> GeoGrid:
        await self.index()
        return self._grid

    async def candidates(self, postcode: str, lat=None, lng=None) -> list[tuple[dict, float | None, bool]]:
        """[(establishment, distance_m, postcode_match)]: everything at the
        normalised postcode plus everything within the radius of the point."""
        pc = normalise_postcode(postcode)
        point = parse_latlng(lat, lng)
        out: dict[int, tuple[dict, float | None, bool]] = {}
        for e in (await self.index()).get(pc, []) if pc else []:
            d = None
            ep = establishment_point(e)
            if point and ep:
                d = haversine_m(point[0], point[1], ep[0], ep[1])
            out[id(e)] = (e, d, True)
        if point:
            for d, e in (await self.grid()).near(point[0], point[1]):
                if id(e) not in out:
                    out[id(e)] = (e, d, False)
        return list(out.values())

    async def best_match(self, name: str, postcode: str, lat=None, lng=None) -> FhrsMatch:
        """Best candidate by name similarity; distance costs a little beyond
        PENALTY_FREE_M. Ties go to the nearer establishment."""
        best, best_rank = FhrsMatch(None, 0.0, None, False), (0.0, float("-inf"))
        for e, d, by_pc in await self.candidates(postcode, lat, lng):
            s = name_similarity(name, e.get("BusinessName") or "")
            if d is not None and d > PENALTY_FREE_M:
                s = round(max(0.0, s - min(PENALTY_CAP, PENALTY_PER_M * (d - PENALTY_FREE_M))), 3)
            rank = (s, -d if d is not None else float("-inf"))   # higher score, then nearer, then located
            if rank > best_rank:
                best, best_rank = FhrsMatch(e, s, d, by_pc), rank
        return best

    async def match(self, name: str, postcode: str, lat=None, lng=None) -> tuple[dict | None, float]:
        """Best establishment for this venue by name similarity (see best_match)."""
        m = await self.best_match(name, postcode, lat, lng)
        return m.establishment, m.score


def establishment_point(est: dict) -> tuple[float, float] | None:
    g = est.get("geocode") or {}
    return parse_latlng(g.get("latitude"), g.get("longitude"))


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
    client = client or FhrsClient(cfg.fhrs_authority_ids, cfg.fhrs_cache_days, radius_m=cfg.fhrs_match_radius_m)
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
            m = await client.best_match(c["name"], postcode, poi.get("lat"), poi.get("lng"))
            est, score = m.establishment, m.score
            if est is None or score < cfg.fhrs_match_floor:
                await mark_enrichment(state, c["id"], SOURCE, False, f"no match at {postcode} ({m.detail})")
                continue
            rows = [r for r in observations_for(est, c["id"], score, run_id) if r["signal_code"] in confirmed]
            if rows:
                obs_total += await state.add_observations(rows, run_id=run_id)
            matched += 1
            await mark_enrichment(state, c["id"], SOURCE, True, f"FHRSID {est.get('FHRSID')} ({m.detail}) {est.get('RatingValue')}")
        await state.finish_run(run_id, status="completed", records=matched, cost_usd=0.0)
    except Exception as e:
        await state.finish_run(run_id, status="failed", records=matched, cost_usd=0.0, error=str(e))
        raise
    logger.info(f"FHRS: {matched}/{len(companies)} matched, {obs_total} observation(s)")
    return {"companies": len(companies), "matched": matched, "observations": obs_total}
