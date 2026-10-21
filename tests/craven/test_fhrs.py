import json

import httpx
import pytest

from harvey.craven.collectors.common import name_similarity, normalise_postcode
from harvey.craven.collectors.fhrs import FhrsClient, enrich_fhrs, observations_for
from harvey.craven.signals import install_signals
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog
from tests.craven.conftest import FakeSupabase

SWANSEA = [
    {"FHRSID": 1001, "BusinessName": "The Swan Inn", "BusinessType": "Pub/bar/nightclub", "BusinessTypeID": 7843,
     "PostCode": "SA1 1AA", "RatingValue": "5", "RatingDate": "2024-03-01T00:00:00", "NewRatingPending": False,
     "scores": {"Hygiene": 0, "Structural": 5, "ConfidenceInManagement": 5}},
    {"FHRSID": 1002, "BusinessName": "Velvet Green Cafe", "BusinessType": "Restaurant/Cafe/Canteen", "BusinessTypeID": 1,
     "PostCode": "SA1 1AA", "RatingValue": "AwaitingInspection", "RatingDate": "1901-01-01T00:00:00",
     "NewRatingPending": False, "scores": {"Hygiene": None, "Structural": None, "ConfidenceInManagement": None}},
    {"FHRSID": 1003, "BusinessName": "Completely Different Trading Ltd", "BusinessType": "Retailers - other",
     "PostCode": "SA1 1AB", "RatingValue": "3", "RatingDate": "2023-01-01T00:00:00", "scores": {"Hygiene": 10, "Structural": 10, "ConfidenceInManagement": 10}},
]


def fhrs_transport(calls):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        assert req.headers["x-api-version"] == "2"
        return httpx.Response(200, json={"meta": {"totalCount": len(SWANSEA), "totalPages": 1}, "establishments": SWANSEA})
    return httpx.MockTransport(handler)


def test_name_similarity_is_forgiving_but_not_blind():
    assert name_similarity("The Swan", "The Swan Inn") >= 0.9
    assert name_similarity("Swan Inn", "The Swan Inn Ltd") >= 0.9
    assert name_similarity("The Swan", "Completely Different Trading Ltd") < 0.5
    assert normalise_postcode("sa11aa") == "SA1 1AA" and normalise_postcode(" SA1  1AA ") == "SA1 1AA"


def test_observations_for_a_rated_and_an_awaiting_establishment():
    rated = {r["signal_code"]: r for r in observations_for(SWANSEA[0], "c1", 0.9, "r1")}
    assert rated["FHRS_RATING"]["value_num"] == 5.0 and rated["FHRS_AWAITING_INSPECTION"]["value_num"] == 0.0
    assert rated["FHRS_STRUCTURAL_SCORE"]["value_num"] == 5.0 and rated["FHRS_BUSINESS_TYPE"]["value_text"] == "Pub/bar/nightclub"
    assert rated["FHRS_INSPECTION_AGE_DAYS"]["value_num"] > 300
    assert rated["FHRS_RATING"]["evidence_url"].endswith("/business/en-GB/1001") and rated["FHRS_RATING"]["confidence"] == 0.9
    awaiting = {r["signal_code"]: r for r in observations_for(SWANSEA[1], "c2", 0.95, "r1")}
    assert awaiting["FHRS_AWAITING_INSPECTION"]["value_num"] == 1.0
    assert "FHRS_RATING" not in awaiting and "FHRS_INSPECTION_AGE_DAYS" not in awaiting and "FHRS_HYGIENE_SCORE" not in awaiting


@pytest.mark.asyncio
async def test_client_caches_an_authority_and_matches_by_postcode_then_name(tmp_path):
    calls = []
    client = FhrsClient([351], cache_days=7, transport=fhrs_transport(calls), cache_dir=tmp_path)
    est, score = await client.match("The Swan", "SA1 1AA")
    assert est["FHRSID"] == 1001 and score >= 0.9
    est, score = await client.match("Velvet Green Coffee", "sa1 1aa")
    assert est["FHRSID"] == 1002 and score >= 0.75
    est, score = await client.match("The Swan", "SA9 9ZZ")
    assert est is None and score == 0.0
    other = FhrsClient([351], cache_days=7, transport=fhrs_transport(calls), cache_dir=tmp_path)
    await other.match("The Swan", "SA1 1AA")
    assert len(calls) == 1   # second client read the cache file


@pytest.mark.asyncio
async def test_enrich_records_matches_and_remembers_no_matches(state, settings, tmp_path):
    from harvey.craven.schema import ensure_schema
    await ensure_schema(state)
    install_signals(); await seed_signal_catalog(state)
    for code in ("FHRS_RATING", "FHRS_STRUCTURAL_SCORE", "FHRS_AWAITING_INSPECTION", "FHRS_BUSINESS_TYPE", "FHRS_INSPECTION_AGE_DAYS"):
        await state.set_signal_status(code, "confirmed")
    swan = await state.add_company(Company(name="The Swan", domain="theswan.pub", industry="pub", source="discover:overture_supabase", external_id="overture:a1"))
    velvet = await state.add_company(Company(name="Velvet Green Cafe", industry="cafe", source="discover:overture_supabase", external_id="overture:a2"))
    nomatch = await state.add_company(Company(name="Nowhere Grill", industry="restaurant", source="discover:overture_supabase", external_id="overture:a3"))
    sb = FakeSupabase(pois=[{"id": "a1", "postcode": "SA1 1AA", "name": "The Swan"}, {"id": "a2", "postcode": "SA1 1AA"}, {"id": "a3", "postcode": "SA1 1AB"}])
    client = FhrsClient([351], transport=fhrs_transport([]), cache_dir=tmp_path)

    out = await enrich_fhrs(state, settings, sb.client(), limit=50, client=client)
    assert out == {"companies": 3, "matched": 2, "observations": 7}   # swan: rating, awaiting=0, structural, type, age (5); velvet: awaiting=1, type (2); hygiene/management unconfirmed
    assert await state.cohort(require=["FHRS_AWAITING_INSPECTION"]) == [velvet]   # the Swan's 0 is not a positive
    assert await state.cohort(require=["FHRS_RATING"]) == [swan]
    async with state._connect() as db:
        async with db.execute("SELECT company_id, matched, detail FROM craven_enrichment WHERE source='fhrs' ORDER BY company_id") as cur:
            rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
    assert rows[swan][0] == 1 and "FHRSID 1001" in rows[swan][1]
    assert rows[nomatch][0] == 0 and "no match" in rows[nomatch][1]
    # a second pass finds nothing left to do
    assert (await enrich_fhrs(state, settings, sb.client(), limit=50, client=client))["companies"] == 0
