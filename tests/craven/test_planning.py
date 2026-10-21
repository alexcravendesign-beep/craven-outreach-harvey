"""Planning applications via PlanIt: the classifier against real council
descriptions (fixture captured 21 Oct 2026), the client's cache and rate
limit, matching by postcode + venue tokens, creation, idempotent reruns."""

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from harvey.craven.collectors.planning import (PlanItClient, PlanItRateLimited, classify, enrich_planning, external_id,
                                               relevant, venue_name_from)
from harvey.craven.signals import install_signals
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog
from tests.craven.conftest import FakeSupabase

FIX = Path(__file__).parent / "fixtures"
SAMPLES = json.loads((FIX / "planit_samples.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("description,address,expected", [
    ("Change of use of the ground floor from a shop (Class A1) to a daytime café (Class A3), with installation of a kitchen extract duct", "440 Gower Road Killay Swansea SA2 7AJ", ("change_of_use", "a daytime café")),
    ("Proposed change of use of A1 shop to A3 hot food cafe with minor internal alterations.", "90A John Street, Porthcawl", ("change_of_use", "A3 hot food cafe")),
    ("Change of use from retail shop to a takeaway", "WYE COTTAGE, NORTH STREET, RHAYADER", ("change_of_use", "a takeaway")),
    ("Change of use of ground floor unit from class A1 (shop) to class A3 (food and drink), alterations to shopfront", "188 Water Street", ("change_of_use", "a food and drink use (Class A3)")),
    ("Partial change of use of gym to include café", "The Fox Complex", ("change_of_use", "café")),
    ("Construction of a commercial kitchen and ancillary café with associated access and car parking provision", "Land north of Parc Y Lan", ("change_of_use", "new build: commercial kitchen and ancillary café")),
    ("PRE APP - Convert a retail & warehouse unit into a small hostel or bed and breakfast hotel with new front extension", "208 Oxford Street", ("change_of_use", "pre-application: a small hostel or bed and breakfast hotel")),
    ("Refurbishment and construction of five storey rear extensions with basement and roof top bar extension", "Grand Hotel Ivey Place Swansea SA1 1NX", ("refit", "Refurbishment and construction of five storey rear extensions with basement and roof top bar extension")),
    ("First floor extension on top of existing kitchen, ground floor extension to create cellar and first floor balconies", "Gorseinon Rugby Club Lime Street Gorseinon", ("refit", "First floor extension on top of existing kitchen, ground floor extension to create cellar and first floor balconies")),
    # venues closing or unrelated uses must not become leads
    ("Change of use of restaurant and flat to a 6 person HMO (Class C4) and a 7 person HMO (Unique Use)", "Pantygwydr Restaurant Oxford Street Swansea", None),
    ("Change of use of first floor restaurant to a beauty training academy & treatment rooms", "Crown Inn 344 Birchgrove Road", None),
    ("Change of use from cafe back to residential", "The Brothers Cafe, 15 Ogwy Street", None),
    ("Convert and extend public house to form two dwellings", "Greenmeadow Inn Green Meadow Terrace", None),
    ("Change of use from betting shop (general retail shop ancilliary to the adjoining public house) to a single dwelling", "The Harry Watkins, 2 Millfield Road", None),
    ("Change of use from a shop (class A1) to a nail bar (unique use)", "6 Angel Place Neath", None),
    ("Single storey rear kitchen and bathroom extension", "Bryncelyn, Thornhill Road, Cwmgwili, Llanelli", None),
    ("Demolition of existing kitchen extension, part demolition of the existing garage & proposed single storey rear / side extensions.", "10 Castle Avenue Mumbles", None),
    ("Change of use from factory to a children's soft play facility with an ancillary café.", "UNIT 11, WOODLANDS BUSINESS PARK", None),
    ("", "Anywhere", None),
])
def test_classify_keeps_hospitality_destinations_only(description, address, expected):
    assert classify(description, address) == expected


def test_relevant_skips_conditions_amendments_and_lost_applications():
    swansea = SAMPLES["Swansea"]["records"]
    kept = {r["uid"]: (k, d) for r, k, d in relevant(swansea)}
    assert "2026/1681/DOC" not in kept                 # Conditions
    assert kept["2026/1564/FUL"] == ("change_of_use", "a daytime café")
    assert kept["2026/1062/FUL"][0] == "refit"
    assert "2026/1354/FUL" not in kept                 # restaurant to HMO
    rejected = dict(swansea[0], app_state="Rejected", description="Change of use from shop to cafe")
    assert relevant([rejected]) == []


def test_external_id_and_created_name():
    r = next(r for r in SAMPLES["Bridgend"]["records"] if r["uid"] == "P/26/482/FUL")
    assert external_id(r) == "plan:bridgend:P/26/482/FUL"
    assert venue_name_from(r) == "90A John Street, Porthcawl"
    r = next(r for r in SAMPLES["Neath"]["records"] if r["uid"] == "P2026/0534")
    assert external_id(r) == "plan:neath:P2026/0534" and venue_name_from(r) == "188 Water Street Aberavon Neath Port Talbot"


def planit_transport(calls: list[dict], limited: set[str] = frozenset()):
    def handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        calls.append(q)
        assert req.headers["user-agent"].startswith("craven-outreach/")
        assert q["pg_sz"] == "300" and q["recent"] and "change of use" in q.get("search", "")
        if q["auth"] in limited:
            return httpx.Response(429, json={"error": "Rate limits exceeded, try again in 271s"}, headers={"retry-after": "271"})
        if q["auth"] not in SAMPLES:
            return httpx.Response(200, json={"error": f"P0001: No match for '{q['auth']}'"})
        return httpx.Response(200, json=SAMPLES[q["auth"]])
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_client_caches_per_day_and_surfaces_the_rate_limit(tmp_path):
    calls = []
    client = PlanItClient(recent_days=120, search='"change of use" or cafe', transport=planit_transport(calls, limited={"Neath"}),
                          cache_dir=tmp_path, sleeper=lambda s: None)
    recs = await client.applications("Swansea")
    assert len(recs) == 9 and (tmp_path / f"swansea-{date.today().isoformat()}.json").exists()
    assert len(await client.applications("Swansea")) == 9 and len(calls) == 1
    with pytest.raises(PlanItRateLimited) as e:
        await client.applications("Neath")
    assert e.value.retry_after == 271
    with pytest.raises(RuntimeError, match="No match"):
        await client.applications("Neath Port Talbot")


async def _seed(state):
    install_signals(); await seed_signal_catalog(state)
    for code in ("PLANNING_CHANGE_OF_USE", "PLANNING_REFIT"):
        await state.set_signal_status(code, "confirmed")


@pytest.mark.asyncio
async def test_enrich_planning_matches_creates_and_reruns_cleanly(state, settings, tmp_path):
    await _seed(state)
    rugby = await state.add_company(Company(name="Gorseinon Rugby Club", industry="bar", source="discover:overture_supabase", external_id="overture:r1"))
    gower = await state.add_company(Company(name="Gower Road Cafe", industry="cafe", source="discover:overture_supabase", external_id="overture:g1"))
    sb = FakeSupabase(pois=[{"id": "r1", "name": "Gorseinon Rugby Club", "postcode": "SA4 4AD"},
                            {"id": "g1", "name": "Gower Road Cafe", "postcode": "SA2 7AJ"},      # named after its street: not a match
                            {"id": "x1", "name": "Lime Street Chippy", "postcode": "SA4 4AD"}])
    calls = []
    client = PlanItClient(transport=planit_transport(calls, limited={"Powys"}), cache_dir=tmp_path, sleeper=lambda s: None,
                          search="change of use")
    out = await enrich_planning(state, settings, sb.client(), areas=["Swansea", "Neath", "Bridgend", "Powys", "Carmarthenshire"], client=client)
    assert out["Swansea"] == {"applications": 2, "in_area": 2, "matched": 1, "created": 1, "already_recorded": 0, "observations": 2}
    assert out["Neath"]["created"] == 2 and out["Bridgend"]["created"] == 2
    assert out["Powys"] == {"rate_limited": 271} and "Carmarthenshire" not in out       # the 429 ended the run
    refit = await state.get_observations(company_id=rugby, signal_code="PLANNING_REFIT")
    assert len(refit) == 1 and refit[0]["confidence"] == 0.92 and refit[0]["evidence_url"].startswith("https://property.swansea.gov.uk/")
    assert "application dated 2026-06-25" in refit[0]["value_text"]
    assert await state.get_observations(company_id=gower) == []
    cafe = await state.get_company_by_external_id("plan:swansea:2026/1564/FUL")
    assert cafe.name == "440 Gower Road Killay Swansea" and cafe.source == "discover:planning" and cafe.location == "SA2"
    assert cafe.industry == "planning_change_of_use"
    cou = await state.get_observations(company_id=cafe.id)
    assert cou[0]["signal_code"] == "PLANNING_CHANGE_OF_USE" and cou[0]["value_text"].startswith("a daytime café")
    assert await state.get_company_by_external_id("plan:bridgend:P/26/373/FUL") is None   # cafe back to residential
    # second run: cache hit, nothing recorded twice
    again = await enrich_planning(state, settings, sb.client(), areas=["Swansea"], client=client)
    assert again["Swansea"]["already_recorded"] == 2 and again["Swansea"]["observations"] == 0 and again["Swansea"]["created"] == 0
    assert len(await state.get_observations(company_id=rugby)) == 1


@pytest.mark.asyncio
async def test_application_seen_before_confirmation_is_recorded_once_confirmed(state, settings):
    """The marker is written only with the observation: a run while the signal
    is still unconfirmed leaves the application open, and the first run after
    confirmation records it (once)."""
    from harvey.craven.collectors.planning import record_applications
    install_signals(); await seed_signal_catalog(state)
    sb = FakeSupabase(pois=[])
    r = next(r for r in SAMPLES["Bridgend"]["records"] if r["uid"] == "P/26/482/FUL")
    items = [(r, "change_of_use", "A3 hot food cafe")]
    run_id = await state.start_run("planning", provider="test")
    before = await record_applications(state, sb.client(), items, set(), run_id, await state.confirmed_signal_codes())
    assert before["created"] == 1 and before["observations"] == 0 and before["already_recorded"] == 0
    company = await state.get_company_by_external_id("plan:bridgend:P/26/482/FUL")
    assert await state.get_observations(company_id=company.id) == []
    async with state._connect() as db:
        async with db.execute("SELECT 1 FROM craven_enrichment WHERE company_id = ? AND source = ?",
                              (company.id, "planning:plan:bridgend:P/26/482/FUL")) as cur:
            assert await cur.fetchone() is None                        # no marker without the observation
    await state.set_signal_status("PLANNING_CHANGE_OF_USE", "confirmed")
    after = await record_applications(state, sb.client(), items, set(), run_id, await state.confirmed_signal_codes())
    assert after["created"] == 0 and after["observations"] == 1 and after["already_recorded"] == 0
    obs = await state.get_observations(company_id=company.id)
    assert len(obs) == 1 and obs[0]["signal_code"] == "PLANNING_CHANGE_OF_USE"
    again = await record_applications(state, sb.client(), items, set(), run_id, await state.confirmed_signal_codes())
    assert again["already_recorded"] == 1 and again["observations"] == 0
    assert len(await state.get_observations(company_id=company.id)) == 1
