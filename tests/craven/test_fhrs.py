import json

import httpx
import pytest

from harvey.craven.collectors.common import GeoGrid, haversine_m, name_similarity, normalise_postcode, parse_latlng
from harvey.craven.collectors.fhrs import FhrsClient, enrich_fhrs, observations_for
from harvey.craven.signals import install_signals
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog
from tests.craven.conftest import FakeSupabase

# One degree of latitude is ~111,320 m: 40 m north is 0.000359°.
WIND_ST = (51.6200, -3.9400)
M40 = 40 / 111_320
M100 = 100 / 111_320
M300 = 300 / 111_320


def geo(lat, lng):
    return {"longitude": f"{lng:.6f}", "latitude": f"{lat:.6f}"}   # FHRS serves geocodes as strings


SWANSEA = [
    {"FHRSID": 1001, "BusinessName": "The Swan Inn", "BusinessType": "Pub/bar/nightclub", "BusinessTypeID": 7843,
     "PostCode": "SA1 1AA", "RatingValue": "5", "RatingDate": "2024-03-01T00:00:00", "NewRatingPending": False,
     "scores": {"Hygiene": 0, "Structural": 5, "ConfidenceInManagement": 5}, "geocode": geo(*WIND_ST)},
    {"FHRSID": 1002, "BusinessName": "Velvet Green Cafe", "BusinessType": "Restaurant/Cafe/Canteen", "BusinessTypeID": 1,
     "PostCode": "SA1 1AA", "RatingValue": "AwaitingInspection", "RatingDate": "1901-01-01T00:00:00",
     "NewRatingPending": False, "scores": {"Hygiene": None, "Structural": None, "ConfidenceInManagement": None},
     "geocode": {"longitude": None, "latitude": None}},
    {"FHRSID": 1003, "BusinessName": "Completely Different Trading Ltd", "BusinessType": "Retailers - other",
     "PostCode": "SA1 1AB", "RatingValue": "3", "RatingDate": "2023-01-01T00:00:00", "scores": {"Hygiene": 10, "Structural": 10, "ConfidenceInManagement": 10}},
    # The register has the Mumbles branch under a neighbouring postcode, 40 m from where Overture puts the venue.
    {"FHRSID": 1004, "BusinessName": "Gin and Tapas", "BusinessType": "Restaurant/Cafe/Canteen",
     "PostCode": "SA3 4DE", "RatingValue": "4", "RatingDate": "2024-06-01T00:00:00",
     "scores": {"Hygiene": 5, "Structural": 5, "ConfidenceInManagement": 5}, "geocode": geo(51.5700 + M40, -4.0000)},
    # Same name 100 m away under another postcode: still a candidate, docked a little for the distance.
    {"FHRSID": 1005, "BusinessName": "Mumbles Coffee", "BusinessType": "Restaurant/Cafe/Canteen",
     "PostCode": "SA3 4DF", "RatingValue": "5", "RatingDate": "2024-06-01T00:00:00", "scores": {},
     "geocode": geo(51.5800 + M100, -4.0100)},
    # Same name 300 m away under another postcode: outside the radius, not a candidate.
    {"FHRSID": 1006, "BusinessName": "Nowhere Grill", "BusinessType": "Takeaway/sandwich shop",
     "PostCode": "SA3 4DG", "RatingValue": "5", "RatingDate": "2024-06-01T00:00:00", "scores": {},
     "geocode": geo(51.5900 + M300, -4.0200)},
    # Same postcode as the Swan, one shared common word, 0 m away: the 21 Oct false positive must stay refused.
    {"FHRSID": 1007, "BusinessName": "Morley's Chicken", "BusinessType": "Takeaway/sandwich shop",
     "PostCode": "SA1 1AA", "RatingValue": "5", "RatingDate": "2024-06-01T00:00:00", "scores": {}, "geocode": geo(*WIND_ST)},
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
    # one shared common word plus a postcode is not a match (the 21 Oct false positives)
    assert name_similarity("Mex Chicken Peri Peri", "Morley's Chicken") < 0.85
    assert name_similarity("The Waterfront Beefeater", "Rasoi Waterfront") < 0.85
    assert name_similarity("The Mill House", "The Mill Cocktail Bar") < 0.85
    assert name_similarity("Velvet Green Coffee", "Velvet Green Cafe") >= 0.85
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


def test_haversine_and_geogrid_find_what_brute_force_finds():
    assert abs(haversine_m(51.62, -3.94, 51.62 + M40, -3.94) - 40) < 0.1
    assert parse_latlng("51.62", "-3.94") == (51.62, -3.94)
    assert parse_latlng(None, "-3.94") is None and parse_latlng("0", "0") is None and parse_latlng("x", "1") is None
    import random
    rnd = random.Random(7)
    grid = GeoGrid(120.0)
    points = [(51.60 + rnd.random() * 0.05, -3.98 + rnd.random() * 0.08) for _ in range(12_000)]   # a dense city
    for i, (la, ln) in enumerate(points):
        grid.add(la, ln, i)
    for q in [(51.62, -3.94), (51.6499, -3.9001), (51.60, -3.98)]:
        brute = sorted(i for i, (la, ln) in enumerate(points) if haversine_m(q[0], q[1], la, ln) <= 120.0)
        near = grid.near(q[0], q[1])
        assert sorted(i for _, i in near) == brute and [d for d, _ in near] == sorted(d for d, _ in near)
    assert any(len(grid.near(la, ln)) > 0 for la, ln in points[:50])


@pytest.mark.asyncio
async def test_radius_candidates_lift_recall_without_loosening_the_name_rule(tmp_path):
    client = FhrsClient([351], cache_days=7, transport=fhrs_transport([]), cache_dir=tmp_path)
    # postcode differs from the register's (SA3 4DE) but the venue sits 40 m away: a match, no penalty under 50 m
    m = await client.best_match("Gin & Tapas", "SA3 4DD", 51.5700, -4.0000)
    assert m.establishment["FHRSID"] == 1004 and m.score >= 0.9 and not m.postcode_match
    assert 39 < m.distance_m < 41 and "via radius, 40 m" in m.detail
    # same venue with no coordinates at all: the postcode index alone cannot see it
    m = await client.best_match("Gin & Tapas", "SA3 4DD")
    assert m.establishment is None and m.score == 0.0
    # 100 m away: still a candidate, docked 0.025 for the distance, recorded in the detail
    m = await client.best_match("Mumbles Coffee", "SA3 4DX", 51.5800, -4.0100)
    assert m.establishment["FHRSID"] == 1005 and m.score == 0.975 and 99 < m.distance_m < 101
    # 300 m away: outside the radius, not a candidate however good the name
    m = await client.best_match("Nowhere Grill", "SA3 4DX", 51.5900, -4.0200)
    assert m.establishment is None
    # ...unless the operator widens the radius
    wide = FhrsClient([351], cache_days=7, transport=fhrs_transport([]), cache_dir=tmp_path, radius_m=400)
    m = await wide.best_match("Nowhere Grill", "SA3 4DX", 51.5900, -4.0200)
    assert m.establishment["FHRSID"] == 1006 and m.score == 0.96   # 1.0 less the capped 0.04 penalty
    # near miss: same postcode, 0 m away, one shared common word — distance is not evidence of the name
    m = await client.best_match("Mex Chicken Peri Peri", "SA1 1AA", *WIND_ST)
    assert m.score < 0.85
    # a postcode match with the register's point far off still wins on the postcode, with the distance on record
    m = await client.best_match("The Swan", "SA1 1AA", 51.6200 + M300, -3.9400)
    assert m.establishment["FHRSID"] == 1001 and m.postcode_match and m.score == 0.86 and 299 < m.distance_m < 301
    # a geocode of (0, 0) or null is "unknown", never a candidate at the equator
    assert await client.best_match("Velvet Green Cafe", "", 0.0, 0.0) == await client.best_match("Velvet Green Cafe", "")


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
    tapas = await state.add_company(Company(name="Gin & Tapas", industry="restaurant", source="discover:overture_supabase", external_id="overture:a4"))
    sb = FakeSupabase(pois=[{"id": "a1", "postcode": "SA1 1AA", "name": "The Swan", "lat": WIND_ST[0], "lng": WIND_ST[1]},
                            {"id": "a2", "postcode": "SA1 1AA", "lat": None, "lng": None},
                            {"id": "a3", "postcode": "SA1 1AB", "lat": 51.5900, "lng": -4.0200},       # register's point is 300 m off
                            {"id": "a4", "postcode": "SA3 4DD", "lat": 51.5700, "lng": -4.0000}])     # register's postcode differs, 40 m off
    client = FhrsClient([351], transport=fhrs_transport([]), cache_dir=tmp_path, radius_m=settings.enrichment.fhrs_match_radius_m)

    out = await enrich_fhrs(state, settings, sb.client(), limit=50, client=client)
    assert out == {"companies": 4, "matched": 3, "observations": 12}   # swan: rating, awaiting=0, structural, type, age (5); velvet: awaiting=1, type (2); tapas: 5; hygiene/management unconfirmed
    assert await state.cohort(require=["FHRS_AWAITING_INSPECTION"]) == [velvet]   # the Swan's 0 is not a positive
    assert sorted(await state.cohort(require=["FHRS_RATING"])) == sorted([swan, tapas])
    async with state._connect() as db:
        async with db.execute("SELECT company_id, matched, detail FROM craven_enrichment WHERE source='fhrs' ORDER BY company_id") as cur:
            rows = {r[0]: (r[1], r[2]) for r in await cur.fetchall()}
    assert rows[swan][0] == 1 and "FHRSID 1001" in rows[swan][1] and "via postcode, 0 m" in rows[swan][1]
    assert rows[tapas][0] == 1 and "FHRSID 1004" in rows[tapas][1] and "via radius, 40 m" in rows[tapas][1]
    assert rows[nomatch][0] == 0 and "no match" in rows[nomatch][1]
    # a second pass finds nothing left to do
    assert (await enrich_fhrs(state, settings, sb.client(), limit=50, client=client))["companies"] == 0
