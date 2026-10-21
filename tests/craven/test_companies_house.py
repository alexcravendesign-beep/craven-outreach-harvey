import json

import httpx
import pytest

from harvey.craven.collectors.companies_house import (CompaniesHouseClient, RateLimiter, enrich_companies_house,
                                                      match_confidence, sweep_new_incorporations)
from harvey.craven.signals import install_signals
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog
from tests.craven.conftest import FakeSupabase


def test_match_confidence_needs_evidence_beyond_the_name():
    hit = {"title": "THE SWAN INN (SWANSEA) LTD", "company_number": "12345678", "address": {"postal_code": "SA1 1AA"}, "sic_codes": ["56302"]}
    assert match_confidence("The Swan", "SA1 1AA", hit) >= 0.8
    name_only = {"title": "THE SWAN INN LTD", "company_number": "1", "address": {"postal_code": "M1 1AA"}, "sic_codes": ["62012"]}
    assert match_confidence("The Swan", "SA1 1AA", name_only) == 0.0
    wrong_name = {"title": "ACME WIDGETS LTD", "address": {"postal_code": "SA1 1AA"}, "sic_codes": ["56302"]}
    assert match_confidence("The Swan", "SA1 1AA", wrong_name) == 0.0
    area_only = {"title": "THE SWAN INN LTD", "address": {"postal_code": "SA1 9ZZ"}, "sic_codes": []}
    assert 0.0 < match_confidence("The Swan", "SA1 1AA", area_only) < 0.8


@pytest.mark.asyncio
async def test_rate_limiter_blocks_the_601st_call_in_the_window():
    now = [0.0]; slept = []

    async def sleeper(s):
        slept.append(s); now[0] += s
    rl = RateLimiter(limit=3, window=10.0, clock=lambda: now[0], sleeper=sleeper)
    for _ in range(3):
        await rl.wait()
    assert slept == []
    await rl.wait()
    assert slept and slept[0] >= 10.0


def ch_transport(log):
    def handler(req: httpx.Request) -> httpx.Response:
        log.append(req.url.path)
        assert req.headers.get("authorization", "").startswith("Basic ")
        if req.url.path == "/search/companies":
            q = req.url.params.get("q", "")
            if "Swan" in q:
                return httpx.Response(200, json={"items": [
                    {"title": "THE SWAN INN (SWANSEA) LTD", "company_number": "12345678", "date_of_creation": "2026-06-01",
                     "address": {"postal_code": "SA1 1AA"}, "sic_codes": ["56302"]},
                    {"title": "SWAN HOLDINGS LTD", "company_number": "99", "address": {"postal_code": "EC1A 1AA"}, "sic_codes": ["64209"]}]})
            return httpx.Response(200, json={"items": []})
        if req.url.path == "/company/12345678/officers":
            return httpx.Response(200, json={"items": [{"appointed_on": "2026-06-01"}, {"appointed_on": "2026-09-15"}]})
        if req.url.path == "/advanced-search/companies":
            return httpx.Response(200, json={"items": [
                {"company_name": "NEW BAY BISTRO LTD", "company_number": "55555555", "date_of_creation": "2026-09-01",
                 "registered_office_address": {"postal_code": "SA1 3AA"}, "sic_codes": ["56101"]}]})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_enrich_records_strong_matches_only(state, settings):
    from harvey.craven.schema import ensure_schema
    await ensure_schema(state)
    install_signals(); await seed_signal_catalog(state)
    for code in ("CH_INCORPORATED", "CH_NEW_INCORPORATION_DAYS", "CH_OFFICER_CHANGE_DAYS"):
        await state.set_signal_status(code, "confirmed")
    swan = await state.add_company(Company(name="The Swan", industry="pub", source="discover:overture_supabase", external_id="overture:a1"))
    other = await state.add_company(Company(name="Nowhere Grill", industry="restaurant", source="discover:overture_supabase", external_id="overture:a3"))
    sb = FakeSupabase(pois=[{"id": "a1", "postcode": "SA1 1AA", "locality": "Swansea"}, {"id": "a3", "postcode": "SA1 1AB", "locality": "Swansea"}])
    log = []
    client = CompaniesHouseClient("test-key", transport=ch_transport(log))
    out = await enrich_companies_house(state, settings, sb.client(), client=client, limit=10)
    assert out == {"companies": 2, "matched": 1, "observations": 3}
    obs = {o["signal_code"]: o for o in await state.get_observations(company_id=swan)}
    assert obs["CH_INCORPORATED"]["value_text"] == "12345678" and obs["CH_INCORPORATED"]["confidence"] >= 0.8
    assert obs["CH_NEW_INCORPORATION_DAYS"]["value_num"] >= 100
    assert obs["CH_OFFICER_CHANGE_DAYS"]["value_num"] < obs["CH_NEW_INCORPORATION_DAYS"]["value_num"]
    assert await state.get_observations(company_id=other) == []
    assert "/company/12345678/officers" in log


@pytest.mark.asyncio
async def test_without_a_key_enrichment_skips_quietly(state, settings, monkeypatch):
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    assert await enrich_companies_house(state, settings, None, client=None) == {"skipped": "no key"}


@pytest.mark.asyncio
async def test_sweep_creates_new_hospitality_companies_once(state, settings):
    from harvey.craven.schema import ensure_schema
    await ensure_schema(state)
    install_signals(); await seed_signal_catalog(state)
    for code in ("CH_INCORPORATED", "CH_NEW_INCORPORATION_DAYS"):
        await state.set_signal_status(code, "confirmed")
    client = CompaniesHouseClient("test-key", transport=ch_transport([]))
    first = await sweep_new_incorporations(state, settings, client=client)
    assert first["created"] == 1 and first["known"] == 0
    c = await state.get_company_by_external_id("ch:55555555")
    assert c.name == "NEW BAY BISTRO LTD" and c.source == "discover:companies_house" and c.location == "SA1"
    assert await state.cohort(require=["CH_NEW_INCORPORATION_DAYS"]) == [c.id]
    second = await sweep_new_incorporations(state, settings, client=client)
    assert second["created"] == 0 and second["known"] == 1
