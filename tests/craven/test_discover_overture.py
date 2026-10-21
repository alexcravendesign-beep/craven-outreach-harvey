"""Discovery from the owned Overture data: term→category mapping, social and
page-builder hosts, chains, suppressed customers, role mailboxes; then the
whole Harvey discovery run and the Craven Scout on top of it."""

import pytest

from harvey.collectors import discover as D
from harvey.collectors.discover import DiscoveryQuery
from harvey.craven.discover_overture import OvertureSupabase, categories_for, website_kind
from harvey.craven.signals import install_signals
from harvey.craven.suppression import SuppressionStore
from harvey.integrations.email_finder import EmailResult
from harvey.signals import seed_signal_catalog
from tests.craven.conftest import FakeSupabase


def _poi(i, name, category, postcode="SA1 1AA", website="", email="", phone="01792 000000"):
    return {"id": i, "name": name, "category": category, "confidence": 0.9, "lat": 51.6, "lng": -3.9,
            "address": f"{i} High Street", "postcode": postcode, "locality": "Swansea",
            "phone": phone, "website": website or None, "email": email or None}


POIS = [
    _poi(1, "The Swan", "pub", website="https://www.theswan.pub", email="info@theswan.pub"),
    _poi(2, "Mumbles Coffee", "coffee_shop", website="https://www.facebook.com/mumblescoffee", email="jo.bloggs@gmail.com"),
    _poi(3, "Bay Bakes", "bakery", website="https://baybakes.wixsite.com/home", email="hello@baybakes.co.uk"),
    _poi(4, "Costa Uplands", "coffee_shop", website="https://www.costa.co.uk/"),
    _poi(5, "Costa Quadrant", "coffee_shop", website="https://www.costa.co.uk/"),
    _poi(6, "Costa Marina", "coffee_shop", website="https://www.costa.co.uk/"),
    _poi(7, "Old Customer Cafe", "cafe", website="https://oldcustomer.co.uk", email="info@oldcustomer.co.uk"),
    _poi(8, "Gower Thai", "thai_restaurant", website="https://gowerthai.co.uk", email="bookings@gowerthai.co.uk"),
    _poi(9, "Not Swansea", "pub", postcode="BD23 1AA", website="https://elsewhere.pub", email="info@elsewhere.pub"),
    _poi(10, "Joe's Bar Uplands", "bar", website="https://joesbars.co.uk", email="info@joesbars.co.uk"),
    _poi(11, "Joe's Bar Marina", "bar", website="https://joesbars.co.uk"),
]


def test_terms_map_to_overture_category_patterns():
    assert categories_for("pub")[:3] == ["pub", "gastropub", "brewery"] and "irish_pub" in categories_for("pub")
    assert "*_restaurant" in categories_for("restaurant")
    assert categories_for("Coffee Shop") == ["coffee_shop"]
    assert categories_for("") == []


def test_website_kinds():
    assert website_kind("https://www.theswan.pub") == "own"
    assert website_kind("https://www.facebook.com/x") == "social"
    assert website_kind("https://m.facebook.com/x") == "social"
    assert website_kind("https://x.wixsite.com/home") == "builder"
    assert website_kind("") == "none"


async def _provider(state, pois=POIS, suppressed=()):
    from harvey.craven.schema import ensure_schema
    await ensure_schema(state)
    store = SuppressionStore(state, "craven", None)
    for e in suppressed:
        await store._put_local([{"kind": "email", "value": e, "reason": "existing_customer"}], source="test")
    sb = FakeSupabase(pois=pois)
    return OvertureSupabase(supabase=sb.client(), state_factory=lambda: state, tenant="craven",
                            chain_domain_threshold=2, area_outcodes=("SA1",)), sb


@pytest.mark.asyncio
async def test_fetch_shapes_venues_honestly(state):
    provider, _ = await _provider(state, suppressed=("info@oldcustomer.co.uk",))
    found = {b.name: b for b in await provider.fetch(None, {}, DiscoveryQuery(term="cafe", location="SA1"))}
    assert "Old Customer Cafe" not in found                       # suppressed customer never becomes a company
    assert "Not Swansea" not in found and "The Swan" not in found  # outcode and category filters
    social = found["Mumbles Coffee"]
    assert social.website == "" and social.domain == "" and social.has_website is False
    assert not [o for o in social.observations if o["signal_code"] == "ROLE_MAILBOX"]  # personal address: never recorded
    builder = found["Bay Bakes"] if "Bay Bakes" in found else None
    assert builder is None  # bakery is not a cafe term
    costa = [found[n] for n in ("Costa Uplands", "Costa Quadrant", "Costa Marina")]
    assert all(c.domain == "" and c.website.startswith("https://www.costa") for c in costa)
    assert all(next(o["value_num"] for o in c.observations if o["signal_code"] == "INDEPENDENT_VENUE") == 0.0 for c in costa)
    assert {c.external_id for c in costa} == {"overture:4", "overture:5", "overture:6"}
    bars = {b.name: b for b in await provider.fetch(None, {}, DiscoveryQuery(term="bar", location="SA1"))}
    two_site = [bars["Joe's Bar Uplands"], bars["Joe's Bar Marina"]]
    assert all(b.domain == "" and b.website == "https://joesbars.co.uk" for b in two_site)   # distinct companies
    assert all(next(o["value_num"] for o in b.observations if o["signal_code"] == "INDEPENDENT_VENUE") == 1.0 for b in two_site)


@pytest.mark.asyncio
async def test_fetch_records_role_mailboxes_and_builder_sites(state):
    provider, _ = await _provider(state)
    pubs = {b.name: b for b in await provider.fetch(None, {}, DiscoveryQuery(term="pub", location="SA1"))}
    swan = pubs["The Swan"]
    assert swan.domain == "theswan.pub" and swan.has_website is True
    role = next(o for o in swan.observations if o["signal_code"] == "ROLE_MAILBOX")
    assert role["value_text"] == "info@theswan.pub" and role["evidence_url"] == "https://www.theswan.pub"
    assert next(o["value_num"] for o in swan.observations if o["signal_code"] == "INDEPENDENT_VENUE") == 1.0
    bakes = {b.name: b for b in await provider.fetch(None, {}, DiscoveryQuery(term="bakery", location="SA1"))}["Bay Bakes"]
    assert bakes.domain == "" and bakes.website == "https://baybakes.wixsite.com/home" and bakes.has_website is True
    thai = {b.name: b for b in await provider.fetch(None, {}, DiscoveryQuery(term="restaurant", location="SA1"))}
    assert "Gower Thai" in thai


@pytest.mark.asyncio
async def test_run_discovery_end_to_end_keeps_chains_distinct_and_is_idempotent(state, monkeypatch):
    install_signals()
    await seed_signal_catalog(state)
    for code in ("VENUE_CATEGORY", "ROLE_MAILBOX", "INDEPENDENT_VENUE", "NO_WEBSITE"):
        await state.set_signal_status(code, "confirmed")
    provider, _ = await _provider(state)
    monkeypatch.setitem(D.PROVIDERS, provider.key, provider)

    class Cfg:  # build_queries is bypassed; run_discovery only needs something truthy
        pass
    queries = [DiscoveryQuery(term=t, location="SA1") for t in ("pub", "cafe", "restaurant", "bakery")]
    report = await D.run_discovery(state, Cfg(), provider_key=provider.key, queries=queries, max_spend=0.0)
    assert report.errors == [] and not report.stopped
    assert report.estimated_cost == 0.0 and report.actual_cost == 0.0
    assert report.new_companies == 8          # every SA1 venue matching the four terms; Not Swansea excluded
    names = {(await state.get_company_by_external_id(f"overture:{i}")).name for i in (4, 5, 6)}
    assert {"Costa Uplands", "Costa Quadrant", "Costa Marina"} <= names   # three companies, not one
    obs = await state.get_observations(limit=1000)
    by_code = {}
    for o in obs:
        by_code.setdefault(o["signal_code"], []).append(o)
    assert len(by_code["VENUE_CATEGORY"]) == 8
    assert sorted(o["value_text"] for o in by_code["ROLE_MAILBOX"]) == [
        "bookings@gowerthai.co.uk", "hello@baybakes.co.uk", "info@oldcustomer.co.uk", "info@theswan.pub"]
    assert sum(1 for o in by_code["INDEPENDENT_VENUE"] if o["value_num"] == 0.0) == 3
    assert len(by_code.get("NO_WEBSITE", [])) == 1   # the Facebook-only cafe

    again = await D.run_discovery(state, Cfg(), provider_key=provider.key, queries=queries, max_spend=0.0)
    assert again.new_companies == 0 and again.known_companies == 8


@pytest.mark.asyncio
async def test_craven_scout_turns_role_mailboxes_into_nameless_prospects(state, settings, monkeypatch):
    install_signals()
    await seed_signal_catalog(state)
    for code in ("VENUE_CATEGORY", "ROLE_MAILBOX", "INDEPENDENT_VENUE"):
        await state.set_signal_status(code, "confirmed")
    provider, _ = await _provider(state, suppressed=("info@oldcustomer.co.uk",))
    monkeypatch.setitem(D.PROVIDERS, provider.key, provider)
    queries = [DiscoveryQuery(term=t, location="SA1") for t in ("pub", "cafe", "restaurant")]
    await D.run_discovery(state, object(), provider_key=provider.key, queries=queries, max_spend=0.0)
    # simulate a customer that slipped in before suppression knew it
    from harvey.models.company import Company
    cid = await state.add_company(Company(name="Late Customer", domain="late.co.uk", industry="pub"))
    run_id = await state.start_run("discover", provider="test")
    await state.add_observations([{"company_id": cid, "signal_code": "ROLE_MAILBOX", "collector": "discover",
                                   "value_num": None, "value_text": "info@late.co.uk", "confidence": 1.0,
                                   "evidence_url": "", "run_id": run_id}], run_id=run_id)
    await SuppressionStore(state, "craven", None)._put_local(
        [{"kind": "email", "value": "info@late.co.uk", "reason": "existing_customer"}], source="test")

    from harvey.craven.scout import CravenScout
    from tests.test_outbox_native import Cfg, Env, StubBrain
    scout = CravenScout(brain=StubBrain(), state=state, config=Cfg(), env=Env(), settings=settings)

    async def fake_verify(email, env, role_parts):
        return EmailResult(email=email, status="verified" if email.startswith("info@") else "guess")
    scout._verify = fake_verify

    await scout.run()
    prospects = {p.email: p for p in await state.get_prospects_by_status("new")}
    assert set(prospects) == {"info@theswan.pub", "bookings@gowerthai.co.uk"}
    swan = prospects["info@theswan.pub"]
    assert swan.first_name == "" and swan.title == "General enquiries mailbox"
    assert swan.email_status == "verified" and swan.email_verified is True
    assert prospects["bookings@gowerthai.co.uk"].email_status == "guess"   # honest: no verifier verdict
    assert swan.company == "The Swan" and swan.source == "overture_role_mailbox"
    assert "info@late.co.uk" not in prospects

    await scout.run()   # idempotent: nothing to add, nothing duplicated
    assert len(await state.get_prospects_by_status("new")) == 2
