"""Planning applications — change of use to a food or drink venue, and refits
of existing venues — via PlanIt (planit.org.uk), which scrapes every council
portal in the pilot area into one JSON shape.

A change of use to a restaurant, cafe, takeaway, bar, pub or hotel says a
venue is coming months before it has a name, a mailbox or a hygiene
registration; works at an existing venue say a refit is coming. The
classifier reads the council's description and keeps only hospitality
*destinations* (a restaurant turning into flats is a venue closing, not
opening). See SOURCES-licensing-planning.md for the API, its fields and its
rate limit: one call per council per run, cached for a day, and a 429 ends
the run.

Matching: full postcode -> the pois we own there -> a poi whose distinctive
name tokens all appear in the application's address or description. No
match creates a company (discover:planning, external_id plan:<area>:<ref>).
"""

import json
import logging
import re
import time
from datetime import date
from pathlib import Path

import httpx

from harvey.craven.collectors.common import (best_poi_in_text, mark_enrichment, normalise_postcode, outcode,
                                             pois_by_postcodes, slugify)
from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.craven.planning")

API = "https://www.planit.org.uk/api/applics/json"
SOURCE = "planning"
CACHE_DIR = PROJECT_ROOT / "data" / "planning"
USER_AGENT = "craven-outreach/0.1 (planning signals; contact: alexcravendesign@gmail.com)"

SKIP_APP_TYPES = {"Conditions", "Amendment"}
SKIP_APP_STATES = {"Rejected", "Withdrawn"}

HOSPITALITY = re.compile(
    r"restaurant|caf[eé]\b|cafes?\b|coffee shop|take[- ]?away|hot food|food and drink|class a3\b|\ba3\b|"
    r"\bbars?\b|public house|\bpubs?\b|hotel|guest ?house|bed and breakfast|b&b|bistro|pizzeria|"
    r"delicatessen|\bdeli\b|bakery|micro ?pub|tap ?room|brewery|food hall|wine bar|cocktail|night ?club|"
    r"function room|commercial kitchen|dark kitchen|ice cream|gelato|servery|\bchippy\b|fish and chip|"
    r"diner\b|steakhouse|kebab|pizza|bistro|tea ?room|sandwich bar|juice bar|canteen|"
    r"(?:rugby|cricket|golf|football|social|sports|bowls|working men'?s?|workingmen'?s?) club|club ?house|\binn\b|\barms\b",
    re.I)
NOT_HOSPITALITY = re.compile(
    r"dwelling|\bflats?\b|apartment|\bhmo\b|residential|\bc3\b|\bc4\b|\bc2\b|care home|nursing|\boffice|\bb1\b|\bb8\b|"
    r"storage|retail|\bshop\b|\ba1\b|\ba2\b|\bd1\b|\bd2\b|\bgym\b|nail|beauty|showroom|\bgarage\b|sports facility|"
    r"soft play|golf simulator|tattoo|salon|clinic|dental|surgery|place of worship|school|nursery|student",
    re.I)
CHANGE_OF_USE = re.compile(
    r"(?:change of use|conversion of|convert(?:ed|ing)?|change the use|to be used as|re-?use)[^.;]*?\b"
    r"(?:to form|to become|to create|to provide|to include|into|to|as)\b\s*(.+)", re.I | re.S)
NEW_BUILD = re.compile(r"(?:construction|erection|new build|creation|formation|provision) of (?:a |an |new )?([^.;]+)", re.I)
WORKS = re.compile(
    r"extension|extend|refurbish|alteration|extract(?:ion|or)|\bflue\b|servery|terrace|outdoor seating|"
    r"kitchen|canopy|installation|install\b|demoli|re-?roof|shopfront|conversion of (?:an? )?(?:outbuilding|garage|store|barn)|"
    r"beer garden|smoking shelter|cellar|decking|marquee", re.I)
PRE_APP = re.compile(r"^\s*pre[- ]?app", re.I)


def _first_hit(regex: re.Pattern, text: str) -> int:
    m = regex.search(text)
    return m.start() if m else 10 ** 9


def _trim_use(text: str) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    t = re.split(r"[.;,]| with | including | together | and (?:the )?(?:installation|erection|construction|associated)", t, 1)[0]
    t = re.sub(r"\((?:use )?class [a-z0-9/ ]+\)", "", t, flags=re.I)
    t = re.sub(r"^(?:include|incorporate|accommodate|provide|form|create)\s+", "", t, flags=re.I)
    t = re.sub(r"\s+", " ", t).strip(" ,-")
    if re.fullmatch(r"(?:use )?class a3", t, re.I):
        t = "a food and drink use (Class A3)"
    return t[:100]


def _without_asides(text: str) -> str:
    """Drop bracketed asides that do not name a use class: '(general retail
    shop ancillary to the adjoining public house)' must not supply the 'to'."""
    return re.sub(r"\((?![^)]*class)[^)]*\)", "", text, flags=re.I)


def classify(description: str, address: str = "") -> tuple[str, str] | None:
    """('change_of_use', proposed use) | ('refit', works) | None.

    A change of use counts only when the destination is a hospitality use and
    no non-hospitality use is named before it; works count only at an address
    or in a description that names a venue. Pre-application enquiries are
    kept and flagged in the detail."""
    desc = re.sub(r"\s+", " ", description or "")
    if not desc:
        return None
    pre = bool(PRE_APP.match(desc))
    body = PRE_APP.sub("", desc).strip(" -:")
    m = CHANGE_OF_USE.search(_without_asides(body))
    if m:
        dest = m.group(1)
        if _first_hit(HOSPITALITY, dest) < _first_hit(NOT_HOSPITALITY, dest):
            return "change_of_use", ("pre-application: " if pre else "") + _trim_use(dest)
        return None                      # a venue becoming flats, offices, a shop: not for us
    m = NEW_BUILD.search(_without_asides(body))
    if m:
        built = _trim_use(m.group(1))            # what is built, not everything after it
        if HOSPITALITY.search(built) and _first_hit(HOSPITALITY, built) < _first_hit(NOT_HOSPITALITY, built):
            return "change_of_use", ("pre-application: " if pre else "") + "new build: " + built
    if WORKS.search(body) and (HOSPITALITY.search(address or "") or HOSPITALITY.search(body)):
        if NOT_HOSPITALITY.search(body) and not HOSPITALITY.search(address or ""):
            return None                  # "kitchen extension" at a dwelling
        return "refit", ("pre-application: " if pre else "") + re.sub(r"\s+", " ", body)[:140].rstrip(" ,")
    return None


class PlanItRateLimited(RuntimeError):
    def __init__(self, retry_after: int):
        super().__init__(f"PlanIt rate limit (HTTP 429); retry after {retry_after}s")
        self.retry_after = retry_after


class PlanItClient:
    def __init__(self, recent_days: int = 120, search: str = "", cache_days: int = 1, transport=None,
                 cache_dir: Path | None = None, sleeper=time.sleep):
        self.recent_days = recent_days
        self.search = search
        self.cache_days = cache_days
        self.transport = transport
        self.cache_dir = cache_dir or CACHE_DIR
        self.sleeper = sleeper
        self._calls = 0

    async def applications(self, area: str) -> list[dict]:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{slugify(area)}-{date.today().isoformat()}.json"
        if path.exists() and time.time() - path.stat().st_mtime < self.cache_days * 86400:
            return json.loads(path.read_text(encoding="utf-8"))
        if self._calls:
            self.sleeper(3.0)
        self._calls += 1
        params = {"auth": area, "recent": self.recent_days, "pg_sz": 300}
        if self.search:
            params["search"] = self.search
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0), transport=self.transport,
                                     headers={"user-agent": USER_AGENT, "accept": "application/json"}) as c:
            r = await c.get(API, params=params)
            if r.status_code == 429:
                raise PlanItRateLimited(int(r.headers.get("retry-after") or 300))
            r.raise_for_status()
            data = r.json()
        if "error" in data:
            raise RuntimeError(f"PlanIt {area}: {data['error']}")
        records = data.get("records") or []
        if data.get("total", 0) > len(records):
            logger.warning(f"PlanIt {area}: {data['total']} applications match; only the first {len(records)} read")
        path.write_text(json.dumps(records), encoding="utf-8")
        logger.info(f"PlanIt {area}: {len(records)} applications cached")
        return records


def relevant(records: list[dict]) -> list[tuple[dict, str, str]]:
    """(record, kind, detail) for the applications worth a signal."""
    out = []
    for r in records:
        if r.get("app_type") in SKIP_APP_TYPES or r.get("app_state") in SKIP_APP_STATES:
            continue
        hit = classify(r.get("description") or "", r.get("address") or "")
        if hit:
            out.append((r, hit[0], hit[1]))
    return out


def external_id(r: dict) -> str:
    return f"plan:{slugify(r.get('area_name') or '')}:{(r.get('uid') or r.get('name') or '').replace(' ', '')}"


def observation_for(r: dict, kind: str, detail: str, company_id: str, confidence: float, run_id: str) -> dict:
    url = r.get("url") or r.get("link") or ""
    when = f" (application dated {r['start_date']})" if r.get("start_date") else ""
    code = "PLANNING_CHANGE_OF_USE" if kind == "change_of_use" else "PLANNING_REFIT"
    return {"company_id": company_id, "signal_code": code, "collector": SOURCE, "confidence": confidence,
            "evidence_url": url, "run_id": run_id, "value_num": None, "value_text": (detail + when)[:240]}


def venue_name_from(r: dict) -> str:
    """A name for a company created from an application: the address up to
    the postcode, which starts with the venue name when there is one."""
    addr = re.sub(r"\s+", " ", r.get("address") or "").strip()
    pc = normalise_postcode(r.get("postcode") or "")
    if pc:
        addr = re.sub(re.escape(pc[:-4]) + r"\s*" + re.escape(pc[-3:]) + r"\s*$", "", addr, flags=re.I).strip(" ,")
    return addr[:120] or external_id(r)


async def record_applications(state, supabase, items: list[tuple[dict, str, str]], outcodes: set[str],
                              run_id: str, confirmed: set[str]) -> dict:
    from harvey.models.company import Company
    kept = [(r, k, d) for r, k, d in items if not outcodes or outcode(r.get("postcode") or "") in outcodes]
    pois = await pois_by_postcodes(supabase, [r.get("postcode") or "" for r, _, _ in kept])
    matched = created = skipped = obs_total = 0
    for r, kind, detail in kept:
        pc = normalise_postcode(r.get("postcode") or "")
        text = f"{r.get('address') or ''} {r.get('description') or ''}"
        poi, conf = best_poi_in_text(text, pois.get(pc, [])) if pc else (None, 0.0)
        company_id = ""
        if poi is not None:
            company = await state.get_company_by_external_id(f"overture:{poi['id']}")
            if company is not None:
                company_id = company.id
        ext = external_id(r)
        is_match = bool(company_id)
        if is_match:
            confidence = conf
        else:
            existing = await state.get_company_by_external_id(ext)
            if existing is not None:
                company_id = existing.id
            else:
                company_id = await state.add_company(Company(
                    name=venue_name_from(r), industry=f"planning_{kind}", location=outcode(pc),
                    source="discover:planning", source_url=r.get("url") or r.get("link") or "", external_id=ext,
                    notes=(r.get("description") or "")[:200]))
                created += 1
            confidence = 1.0
        marker = f"planning:{ext}"
        async with state._connect() as db:
            async with db.execute("SELECT 1 FROM craven_enrichment WHERE company_id = ? AND source = ?",
                                  (company_id, marker)) as cur:
                if await cur.fetchone():
                    skipped += 1
                    continue
        row = observation_for(r, kind, detail, company_id, confidence, run_id)
        if row["signal_code"] in confirmed:
            written = await state.add_observations([row], run_id=run_id)
            obs_total += written
            if written:
                # the marker only once the observation exists: an application seen before
                # its signal was confirmed is recorded on the first run after confirmation
                await mark_enrichment(state, company_id, marker, True, f"{kind}: {detail} ({confidence:.2f})")
        if is_match:
            matched += 1
    return {"applications": len(items), "in_area": len(kept), "matched": matched, "created": created,
            "already_recorded": skipped, "observations": obs_total}


async def enrich_planning(state, settings, supabase, areas: list[str] | None = None,
                          client: PlanItClient | None = None) -> dict:
    """One PlanIt call per council, classify, match or create, record. A rate
    limit ends the run cleanly; the next run resumes from the cache."""
    cfg = settings.enrichment
    areas = areas or cfg.planning_areas
    client = client or PlanItClient(cfg.planning_recent_days, cfg.planning_search, cfg.planning_cache_days)
    confirmed = await state.confirmed_signal_codes()
    outcodes = {o.upper() for o in cfg.area_outcodes}
    run_id = await state.start_run("planning", provider="planit", params={"areas": areas, "recent": cfg.planning_recent_days})
    out: dict = {}
    total = 0
    try:
        for area in areas:
            try:
                records = await client.applications(area)
            except PlanItRateLimited as e:
                logger.warning(f"PlanIt: {e}; stopping this run, the next one carries on")
                out[area] = {"rate_limited": e.retry_after}
                break
            out[area] = await record_applications(state, supabase, relevant(records), outcodes, run_id, confirmed)
            total += out[area]["observations"]
        await state.finish_run(run_id, status="completed", records=total, cost_usd=0.0)
    except Exception as e:
        await state.finish_run(run_id, status="failed", records=total, cost_usd=0.0, error=str(e))
        raise
    logger.info(f"planning: {out}")
    return out
