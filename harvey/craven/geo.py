"""Where the venues are: the ``craven_geo`` mirror of the owned pois rows,
and the figures the dashboard map shows beside its pins.

Coordinates live in Supabase ``public.pois`` (lat, lng, postcode), keyed by
the company's ``external_id`` (``overture:<poi id>``). ``sync_geo`` copies
them into SQLite once; the map reads the table only (see dashboard_ext).
"""

import logging

from harvey.craven.collectors.common import now_iso, pois_by_external_ids

logger = logging.getLogger("harvey.craven.geo")

DISCOVERY_SOURCE = "discover:overture_supabase"


async def companies_to_place(state, limit: int | None = None) -> list[dict]:
    """Every discovered company with an Overture external id, those not yet
    placed first."""
    sql = ("SELECT c.id, c.external_id FROM companies c "
           "LEFT JOIN craven_geo g ON g.company_id = c.id "
           "WHERE c.source = ? AND c.external_id LIKE 'overture:%' "
           "ORDER BY g.synced_at IS NOT NULL, c.created_at")
    params: list = [DISCOVERY_SOURCE]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(int(limit))
    async with state._connect() as db:
        async with db.execute(sql, params) as cur:
            return [{"id": r[0], "external_id": r[1]} for r in await cur.fetchall()]


async def sync_geo(state, supabase, limit: int | None = None) -> dict:
    """Fill craven_geo from pois for every discovered company. Returns
    counts: companies looked at, placed (upserted), and missing (no pois
    row or no coordinates)."""
    if supabase is None:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not set.")
    companies = await companies_to_place(state, limit)
    pois = await pois_by_external_ids(supabase, [c["external_id"] for c in companies])
    stamp = now_iso()
    rows, missing = [], 0
    for c in companies:
        poi = pois.get(c["external_id"]) or {}
        lat, lng = poi.get("lat"), poi.get("lng")
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            missing += 1
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            missing += 1
            continue
        rows.append((c["id"], lat, lng, str(poi.get("postcode") or "").strip().upper(), stamp))
    async with state._connect() as db:
        await db.executemany(
            "INSERT INTO craven_geo (company_id, lat, lng, postcode, synced_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(company_id) DO UPDATE SET lat = excluded.lat, lng = excluded.lng, "
            "postcode = excluded.postcode, synced_at = excluded.synced_at", rows)
        await db.commit()
    logger.info(f"sync-geo: {len(companies)} companies, {len(rows)} placed, {missing} without coordinates.")
    return {"companies": len(companies), "placed": len(rows), "missing": missing}


async def figures(state) -> dict:
    """Counts shown beside the map. Existing customers are suppressed
    addresses, never companies, so they are a figure and never a pin."""
    async with state._connect() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM craven_suppressions WHERE reason = 'existing_customer'") as cur:
            customers = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM craven_geo") as cur:
            placed = (await cur.fetchone())[0]
    return {"suppressed_customers": customers, "placed": placed}
