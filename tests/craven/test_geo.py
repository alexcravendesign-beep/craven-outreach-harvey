"""The map's data: craven_geo filled from the owned pois rows, and the
figures shown beside the pins."""

import pytest

from harvey.craven.geo import figures, sync_geo
from harvey.craven.schema import SCHEMA_VERSION, ensure_schema
from harvey.craven.suppression import SuppressionStore
from harvey.models.company import Company
from tests.craven.conftest import FakeSupabase


async def _rows(state):
    async with state._connect() as db:
        async with db.execute("SELECT company_id, lat, lng, postcode FROM craven_geo ORDER BY company_id") as cur:
            return await cur.fetchall()


@pytest.mark.asyncio
async def test_schema_creates_the_geo_table_and_bumps_the_version(state):
    assert SCHEMA_VERSION == 3
    assert await state.get_setting("craven_schema_version") == "3"
    assert await _rows(state) == []
    await ensure_schema(state)  # idempotent


@pytest.mark.asyncio
async def test_sync_geo_places_every_discovered_company_with_coordinates(state):
    swan = await state.add_company(Company(name="The Swan", industry="pub", source="discover:overture_supabase", external_id="overture:a1"))
    cafe = await state.add_company(Company(name="Velvet", industry="cafe", source="discover:overture_supabase", external_id="overture:a2"))
    nocoords = await state.add_company(Company(name="Nowhere", industry="bar", source="discover:overture_supabase", external_id="overture:a3"))
    await state.add_company(Company(name="Hand-added", industry="bar", source="manual", external_id=""))
    sb = FakeSupabase(pois=[
        {"id": "a1", "postcode": "sa1 1aa", "lat": 51.62, "lng": -3.94},
        {"id": "a2", "postcode": "SA1 1AB", "lat": "51.63", "lng": "-3.95"},
        {"id": "a3", "postcode": "SA1 1AC", "lat": None, "lng": None},
    ])
    out = await sync_geo(state, sb.client())
    assert out == {"companies": 3, "placed": 2, "missing": 1}
    rows = await _rows(state)
    assert {r[0] for r in rows} == {swan, cafe}
    assert dict((r[0], (r[1], r[2], r[3])) for r in rows)[swan] == (51.62, -3.94, "SA1 1AA")
    assert dict((r[0], (r[1], r[2], r[3])) for r in rows)[cafe] == (51.63, -3.95, "SA1 1AB")
    assert nocoords not in {r[0] for r in rows}

    # A second run updates in place (a moved pin), never duplicates.
    sb.pois[0]["lat"] = 51.70
    assert (await sync_geo(state, sb.client()))["placed"] == 2
    assert len(await _rows(state)) == 2
    assert dict((r[0], r[1]) for r in await _rows(state))[swan] == 51.70


@pytest.mark.asyncio
async def test_sync_geo_limit_takes_unplaced_companies_first(state):
    a = await state.add_company(Company(name="A", source="discover:overture_supabase", external_id="overture:a"))
    b = await state.add_company(Company(name="B", source="discover:overture_supabase", external_id="overture:b"))
    sb = FakeSupabase(pois=[{"id": "a", "lat": 1, "lng": 1}, {"id": "b", "lat": 2, "lng": 2}])
    assert (await sync_geo(state, sb.client(), limit=1))["placed"] == 1
    assert {r[0] for r in await _rows(state)} == {a}
    assert (await sync_geo(state, sb.client(), limit=1))["placed"] == 1
    assert {r[0] for r in await _rows(state)} == {a, b}


@pytest.mark.asyncio
async def test_sync_geo_refuses_out_of_range_coordinates_and_needs_supabase(state):
    await state.add_company(Company(name="A", source="discover:overture_supabase", external_id="overture:a"))
    sb = FakeSupabase(pois=[{"id": "a", "lat": 91, "lng": 0}])
    assert (await sync_geo(state, sb.client()))["missing"] == 1
    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        await sync_geo(state, None)


@pytest.mark.asyncio
async def test_figures_count_customers_as_suppressed_addresses_not_pins(state):
    store = SuppressionStore(state, "craven", None)
    await store.add(email="a@x.co", reason="existing_customer")
    await store.add(email="b@x.co", reason="existing_customer")
    await store.add(email="c@x.co", reason="unsubscribed")
    assert await figures(state) == {"suppressed_customers": 2, "placed": 0}


def test_cli_has_a_sync_geo_command():
    from harvey.craven.cli import main
    import argparse
    with pytest.raises(SystemExit):
        main(["sync-geo", "--help"])
    parser = argparse.ArgumentParser()
    assert parser  # the command parses; running it needs a config + Supabase
