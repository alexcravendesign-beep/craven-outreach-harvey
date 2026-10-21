import pytest

from harvey.craven.profile import CravenProfileCollector, energy_claim, equipment_class

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
