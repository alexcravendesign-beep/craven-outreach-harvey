import pytest

from harvey.craven.suppression import SuppressionStore
from tests.craven.conftest import FakeSupabase, customer_rows


@pytest.mark.asyncio
async def test_sync_pages_through_the_whole_list(state):
    sb = FakeSupabase(customer_rows(2345))
    store = SuppressionStore(state, "craven", sb.client())
    assert await store.is_stale(24)
    merged = await store.sync()
    assert merged == 2345
    assert await store.count() == 2345
    assert sb.gets == 3                      # 1000 + 1000 + 345
    assert not await store.is_stale(24)
    assert await store.is_suppressed("Customer7@Example.co.uk")
    assert not await store.is_suppressed("info@newplace.co.uk")


@pytest.mark.asyncio
async def test_second_sync_is_incremental_on_the_watermark(state):
    rows = customer_rows(3)
    sb = FakeSupabase(rows)
    store = SuppressionStore(state, "craven", sb.client())
    await store.sync()
    sb.suppression.append({"email": "late@optout.co.uk", "domain": None, "reason": "unsubscribe",
                           "at": "2026-10-21T10:00:00+00:00"})
    merged = await store.sync()
    assert merged == 1
    assert await store.is_suppressed("late@optout.co.uk")


@pytest.mark.asyncio
async def test_domain_rows_suppress_every_address_there(state):
    sb = FakeSupabase([{"email": None, "domain": "Chain.co.uk", "reason": "manual", "at": "2026-01-01T00:00:00+00:00"}])
    store = SuppressionStore(state, "craven", sb.client())
    await store.sync()
    assert await store.is_suppressed("anyone@chain.co.uk")


@pytest.mark.asyncio
async def test_add_writes_local_and_pushes_to_the_system_of_record(state):
    sb = FakeSupabase()
    store = SuppressionStore(state, "craven", sb.client())
    await store.add(email="Gone@x.co", reason="bounce")
    assert await store.is_suppressed("gone@x.co")
    table, rows, headers = sb.posts[-1]
    assert table == "oe_suppression"
    assert rows == [{"tenant": "craven", "email": "gone@x.co", "domain": None, "reason": "bounce"}]
    assert headers["prefer"] == "resolution=ignore-duplicates"


@pytest.mark.asyncio
async def test_sync_without_supabase_raises_and_leaves_the_mirror_stale(state):
    store = SuppressionStore(state, "craven", None)
    with pytest.raises(RuntimeError):
        await store.sync()
    assert await store.is_stale(24)
