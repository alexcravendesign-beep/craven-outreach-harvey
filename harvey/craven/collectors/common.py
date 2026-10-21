"""Shared pieces for the Craven enrichment collectors."""

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

from harvey.craven.supabase import SupabaseRest

STOPWORDS = {"the", "ltd", "limited", "llp", "plc", "and", "&", "of", "at", "co", "company", "bar", "cafe",
             "café", "restaurant", "hotel", "inn", "pub", "kitchen", "takeaway", "coffee", "shop", "lounge"}


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
    if ta and tb and (ta <= tb or tb <= ta):
        ratio = max(ratio, 0.9)
    elif ta and tb:
        overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
        ratio = max(ratio, 0.6 + 0.3 * overlap) if overlap >= 0.5 else ratio
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
