import pytest

from harvey.craven.lawful_basis import LawfulBasisStore
from tests.craven.conftest import FakeSupabase


@pytest.mark.asyncio
async def test_unsigned_or_unknown_ref_is_not_signed(state):
    lia = LawfulBasisStore(state, "craven", None)
    assert not await lia.is_signed("lia-1")
    assert not await lia.is_signed("")


@pytest.mark.asyncio
async def test_sign_records_who_and_when_and_mirrors_to_supabase(state):
    sb = FakeSupabase()
    lia = LawfulBasisStore(state, "craven", sb.client())
    rec = await lia.sign("lia-1", "Nick Lewis", doc_url="8zero8/Company/outreach-lia.md")
    assert rec["signed_by"] == "Nick Lewis" and rec["signed_at"]
    assert await lia.is_signed("lia-1")
    table, rows, headers = sb.posts[-1]
    assert table == "oe_lawful_basis" and rows[0]["ref_id"] == "lia-1"
    assert headers["prefer"] == "resolution=merge-duplicates"


@pytest.mark.asyncio
async def test_sign_needs_a_signer(state):
    lia = LawfulBasisStore(state, "craven", None)
    with pytest.raises(ValueError):
        await lia.sign("lia-1", "   ")
