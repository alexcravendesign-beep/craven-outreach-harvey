import pytest

from harvey.craven.profile import CravenProfileCollector, companies_with_dossier, energy_claim, equipment_class
from harvey.craven.signals import install_signals
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog

HOME = "<html><body><h1>The Swan</h1><p>Real ale on hand pull, cask from local breweries. Sunday roasts.</p></body></html>"
MENU = "<html><body><h2>Cakes</h2><p>Brownies, cheesecake and scones with our afternoon tea.</p><p>We are proud of our energy efficient kitchen.</p></body></html>"


def test_equipment_class_picks_the_class_with_most_evidence():
    klass, conf, hits = equipment_class("gelato and ice cream parlour, sorbet daily")
    assert klass == "display_freezer" and conf >= 0.9 and "gelato" in hits
    assert equipment_class("just a plain page about us") is None
    klass, conf, _ = equipment_class("Cakes, brownies, scones and a wine list")
    assert klass == "display_chiller" and 0.5 < conf <= 1.0


def test_energy_claim():
    assert energy_claim("We run a sustainable, low carbon kitchen")
    assert not energy_claim("Fish and chips since 1952")


@pytest.mark.asyncio
async def test_collector_adds_menu_class_and_energy_claim_without_refetching_home(state, monkeypatch):
    collector = CravenProfileCollector(state, confirmed={"MENU_EQUIPMENT_CLASS", "ENERGY_CLAIM"})
    fetched = []

    async def fake_resolve(domain):
        fetched.append("home"); return "https://theswan.pub", HOME

    async def fake_first_present(site, paths):
        fetched.append("menu"); return f"{site}/menu", MENU

    async def fake_get(url):
        return ""
    monkeypatch.setattr(CravenProfileCollector.__mro__[1], "_resolve_site", lambda self, d: fake_resolve(d))
    monkeypatch.setattr(collector, "_first_present", fake_first_present)
    monkeypatch.setattr(collector, "_get", fake_get)

    obs = {o["signal_code"]: o for o in await collector.profile_company("c1", "theswan.pub")}
    assert fetched.count("home") == 1                        # memoised, not fetched twice
    assert obs["MENU_EQUIPMENT_CLASS"]["value_text"] in ("display_chiller", "cellar_cooling")
    assert obs["MENU_EQUIPMENT_CLASS"]["evidence_url"] == "https://theswan.pub/menu"
    assert obs["ENERGY_CLAIM"]["value_num"] == 1.0
    assert all(o["collector"] == "profile" for o in obs.values())


@pytest.mark.asyncio
async def test_collector_never_renames_a_venue(state):
    collector = CravenProfileCollector(state, confirmed=set())
    assert await collector._improve_name({"id": "c1", "name": "The White Lion"}, "app.servedup.co") is None


def test_booking_platforms_are_not_a_venues_website():
    from harvey.craven.discover_overture import website_kind
    assert website_kind("https://app.servedup.co/whitelion") == "social"
    assert website_kind("https://www.resdiary.com/restaurant/x") == "social"
    assert website_kind("https://theswan.pub") == "own"


@pytest.mark.asyncio
async def test_run_skips_venues_that_already_have_a_dossier_and_rotates_them_out(state, monkeypatch, caplog):
    from harvey.craven.schema import ensure_schema
    await ensure_schema(state)
    install_signals(); await seed_signal_catalog(state)
    swan = await state.add_company(Company(name="The Swan", domain="theswan.pub", source="discover:overture_supabase", external_id="overture:1"))
    tapas = await state.add_company(Company(name="Gin & Tapas", domain="gintapas.com", source="discover:overture_supabase", external_id="overture:2"))
    nosite = await state.add_company(Company(name="No Site", source="discover:overture_supabase", external_id="overture:3"))
    await state.add_observations([{"company_id": swan, "signal_code": "VENUE_DOSSIER", "collector": "dossier",
                                   "value_text": "{}", "confidence": 0.9, "evidence_url": "https://theswan.pub"},
                                  {"company_id": swan, "signal_code": "MENU_EQUIPMENT_CLASS", "collector": "dossier",
                                   "value_text": "cellar_cooling", "confidence": 0.9, "evidence_url": "https://theswan.pub"}])
    assert await companies_with_dossier(state, [swan, tapas, nosite, ""]) == {swan}
    backlog = await state.companies_needing_profile(limit=10)
    assert {c["id"] for c in backlog} == {swan, tapas}          # the siteless venue is never in the backlog

    collector = CravenProfileCollector(state, confirmed={"MENU_EQUIPMENT_CLASS", "ENERGY_CLAIM"})
    profiled = []

    async def fake_profile(company_id, domain):
        profiled.append(domain)
        return [{"signal_code": "ENERGY_CLAIM", "company_id": company_id, "collector": "profile", "value_num": 0.0,
                 "value_text": "", "confidence": 1.0, "evidence_url": f"https://{domain}"}]
    monkeypatch.setattr(collector, "profile_company", fake_profile)
    monkeypatch.setattr(collector, "_improve_name", lambda company, domain: _nothing())
    monkeypatch.setattr("harvey.collectors.profile.asyncio.sleep", lambda s: _nothing())

    with caplog.at_level("INFO", logger="harvey.craven.profile"):
        n = await collector.run(backlog, run_id="r1")
    assert profiled == ["gintapas.com"] and n == 1 and collector.skipped_with_dossier == 1
    assert "1 of 2 already have a dossier" in caplog.text
    # the dossier venue's keyword guess was never written, and it has rotated out of the backlog with the other
    assert await state.get_observations(company_id=swan, signal_code="ENERGY_CLAIM") == []
    assert [o["collector"] for o in await state.get_observations(company_id=swan, signal_code="MENU_EQUIPMENT_CLASS")] == ["dossier"]
    assert await state.companies_needing_profile(limit=10) == []
    # a later pass has nothing to skip and nothing to do
    assert await collector.run(await state.companies_needing_profile(limit=10), run_id="r2") == 0
    assert collector.skipped_with_dossier == 1 and profiled == ["gintapas.com"]


async def _nothing():
    return None
