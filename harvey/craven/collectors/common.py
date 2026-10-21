"""Shared pieces for the Craven enrichment collectors."""

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from harvey.craven.supabase import SupabaseRest

STOPWORDS = {"the", "ltd", "limited", "llp", "plc", "and", "&", "of", "at", "co", "company", "bar", "cafe",
             "café", "restaurant", "hotel", "inn", "pub", "kitchen", "takeaway", "coffee", "shop", "lounge",
             "house", "arms", "grill", "chicken", "pizza", "indian", "chinese", "fish", "chips", "tap", "room", "old", "new"}


def normalise_name(name: str) -> str:
    n = (name or "").lower()
    n = n.replace("&", " and ").replace("'", "").replace("’", "")
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def name_tokens(name: str) -> set[str]:
    return {t for t in normalise_name(name).split() if t not in STOPWORDS and len(t) > 1}


def name_similarity(a: str, b: str) -> float:
    """0..1: character similarity of the normalised names, lifted when the
    distinctive tokens of one are all inside the other."""
    na, nb = normalise_name(a), normalise_name(b)
    if not na or not nb:
        return 0.0
    ratio = SequenceMatcher(None, na, nb).ratio()
    ta, tb = name_tokens(a), name_tokens(b)
    if ta and tb and ta == tb:
        ratio = max(ratio, 0.9)          # same distinctive words: a real match
    elif ta and tb and (ta <= tb or tb <= ta) and min(len(ta), len(tb)) >= 2:
        ratio = max(ratio, 0.9)          # two or more distinctive words contained in the other name
    elif ta and tb:
        # a shared common word ("waterfront", "chicken", "mill") is not
        # evidence on its own: partial overlap can never clear the floor
        overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
        ratio = max(ratio, 0.5 + 0.3 * overlap) if overlap >= 0.5 else ratio
        ratio = min(ratio, 0.84)
    return round(min(ratio, 1.0), 3)


def normalise_postcode(pc: str) -> str:
    p = re.sub(r"\s+", "", (pc or "").upper())
    return f"{p[:-3]} {p[-3:]}" if len(p) >= 5 else p


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def mark_enrichment(state, company_id: str, source: str, matched: bool, detail: str = "") -> None:
    async with state._connect() as db:
        await db.execute(
            "INSERT INTO craven_enrichment (company_id, source, checked_at, matched, detail) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(company_id, source) DO UPDATE SET checked_at = excluded.checked_at, "
            "matched = excluded.matched, detail = excluded.detail",
            (company_id, source, now_iso(), 1 if matched else 0, detail[:200]))
        await db.commit()


async def companies_needing(state, source: str, recheck_days: int, limit: int,
                            source_prefix: str = "discover:overture_supabase") -> list[dict]:
    """Companies from the owned data not yet tried for ``source`` (or tried
    longer ago than recheck_days). Returns id, name, industry, external_id, domain, location."""
    async with state._connect() as db:
        async with db.execute(
            """SELECT c.id, c.name, c.industry, c.external_id, c.domain, c.location, c.website
               FROM companies c LEFT JOIN craven_enrichment e ON e.company_id = c.id AND e.source = ?
               WHERE c.source = ? AND (e.checked_at IS NULL OR e.checked_at < datetime('now', ?))
               ORDER BY e.checked_at IS NOT NULL, c.created_at LIMIT ?""",
            (source, source_prefix, f"-{int(recheck_days)} days", int(limit))) as cur:
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in await cur.fetchall()]


async def pois_by_external_ids(supabase: SupabaseRest | None, external_ids: list[str]) -> dict[str, dict]:
    """The owned pois rows (postcode, lat, lng, name) for 'overture:<id>' keys."""
    if supabase is None or not external_ids:
        return {}
    ids = [x.split(":", 1)[1] for x in external_ids if x.startswith("overture:")]
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 150):
        chunk = ids[i:i + 150]
        rows = await supabase.select("pois", {"select": "id,name,postcode,lat,lng,address,locality",
                                              "id": "in.(" + ",".join(chunk) + ")"})
        for r in rows:
            out[f"overture:{r['id']}"] = r
    return out


def days_since(date_text: str) -> int | None:
    try:
        d = datetime.fromisoformat(date_text[:19]).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    if d.year < 1950:
        return None
    return max(0, (datetime.now(timezone.utc) - d).days)


# ── Shared by the licensing and planning collectors ──

POSTCODE_RE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b")
STREET_TOKENS = {"road", "street", "lane", "avenue", "close", "crescent", "square", "place", "terrace", "way",
                 "drive", "park", "hill", "heol", "stryd", "ffordd", "high", "main", "north", "south", "east", "west",
                 "swansea", "neath", "llanelli", "bridgend", "porthcawl", "carmarthen", "ammanford", "gorseinon",
                 "mumbles", "morriston", "sketty", "killay", "uplands", "maesteg", "pontardawe", "port", "talbot"}


def find_postcode(text: str) -> str:
    """The last full UK postcode in a piece of text, normalised; '' if none."""
    hits = POSTCODE_RE.findall((text or "").upper())
    return f"{hits[-1][0]} {hits[-1][1]}" if hits else ""


def outcode(postcode: str) -> str:
    pc = normalise_postcode(postcode)
    return pc.split(" ")[0] if pc else ""


def slugify(text: str, limit: int = 60) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit].rstrip("-")


def parse_uk_date(text: str) -> str:
    """'11/11/2026' or '29 October 2026' -> '2026-11-11'; '' when unreadable."""
    t = (text or "").strip()
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        return f"{y:04d}-{mo:02d}-{d:02d}" if 1 <= mo <= 12 and 1 <= d <= 31 else ""
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", t)
    if m:
        months = ("january", "february", "march", "april", "may", "june", "july", "august",
                  "september", "october", "november", "december")
        name = m.group(2).lower()
        for i, mn in enumerate(months, 1):
            if mn.startswith(name[:3]):
                return f"{int(m.group(3)):04d}-{i:02d}-{int(m.group(1)):02d}"
    return ""


async def pois_by_postcodes(supabase: SupabaseRest | None, postcodes: list[str]) -> dict[str, list[dict]]:
    """The owned pois rows at each full postcode (normalised key), for
    matching a council record to venues we already know."""
    if supabase is None or not postcodes:
        return {}
    wanted = sorted({normalise_postcode(p) for p in postcodes if normalise_postcode(p)})
    out: dict[str, list[dict]] = {}
    for i in range(0, len(wanted), 100):
        chunk = wanted[i:i + 100]
        rows = await supabase.select("pois", {"select": "id,name,postcode,address,locality",
                                              "postcode": "in.(" + ",".join(f'"{p}"' for p in chunk) + ")"})
        for r in rows:
            out.setdefault(normalise_postcode(r.get("postcode") or ""), []).append(r)
    return out


def best_poi_by_name(premises_name: str, pois: list[dict], floor: float = 0.85) -> tuple[dict | None, float]:
    """The poi at this postcode whose name is this premises (FHRS rule: name
    similarity at the same full postcode, one shared common word is never
    enough). Ties below a clear winner return nothing."""
    if not premises_name:
        return None, 0.0
    scored = sorted(((name_similarity(premises_name, p.get("name") or ""), p) for p in pois),
                    key=lambda x: x[0], reverse=True)
    if not scored or scored[0][0] < floor:
        return None, scored[0][0] if scored else 0.0
    if len(scored) > 1 and scored[1][0] >= floor and scored[1][0] == scored[0][0]:
        return None, scored[0][0]           # two venues equally likely: do not guess
    return scored[0][1], scored[0][0]


def best_poi_in_text(text: str, pois: list[dict]) -> tuple[dict | None, float]:
    """For records with no separate premises name (planning: the venue name
    is fused into the address). A poi matches when all its distinctive name
    tokens appear in the text; a single token must be four letters or more
    and lead the text. Confidence 0.92 for two or more tokens, 0.86 for one."""
    words = normalise_name(text).split()
    present = set(words)
    best, conf = None, 0.0
    for p in pois:
        # a venue named after its street ("Gower Road Cafe") must not match
        # every application on that street at the postcode
        toks = name_tokens(p.get("name") or "") - STREET_TOKENS
        if not toks or not toks <= present:
            continue
        if len(toks) >= 2:
            score = 0.92
        else:
            tok = next(iter(toks))
            if len(tok) < 4 or not words or words[0] != tok:
                continue
            score = 0.86
        if score > conf:
            best, conf = p, score
        elif score == conf:
            best = None                      # ambiguous between two venues
    return best, conf
