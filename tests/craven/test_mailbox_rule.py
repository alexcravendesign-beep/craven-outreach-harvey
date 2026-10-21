"""The widened business-mailbox rule (Nick, 21 Oct 2026), the contact-page
scrape and the model dossier."""

import json

import httpx
import pytest

from harvey.craven import compliance
from harvey.craven.dossier import build_dossiers, parse_dossier
from harvey.craven.mailbox_scrape import pick_business_mailbox, scrape_mailboxes
from harvey.craven.pages import VenuePages, emails_in
from harvey.craven.signals import install_signals
from harvey.craven.writer import fact_lines
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog


def test_business_mailbox_rule():
    ok = lambda e, n="", w="": compliance.is_business_mailbox(e, n, w)[0]
    assert ok("info@theswan.pub")                                              # role local part
    assert ok("landlord@theswan.pub", "The Swan", "https://www.theswan.pub")   # venue's own domain
    assert ok("theswanpub@gmail.com", "The Swan Pub")                          # named after the venue, free-mail
    assert ok("mexchickenperiperi@outlook.com", "Mex Chicken Peri Peri")
    assert ok("gintapas@hotmail.co.uk", "Gin & Tapas")
    assert not ok("jo.bloggs@gmail.com", "The Swan", "https://theswan.pub")   # a person
    assert not ok("dave1987@yahoo.com", "Mumbles Coffee")
    assert not ok("not-an-address", "X")
    assert compliance.registrable("https://www.theswan.pub/menu") == "theswan.pub"
    assert compliance.registrable("www.farmers.co.uk") == "farmers.co.uk"


def test_pick_prefers_role_addresses_then_venue_ones():
    email, why = pick_business_mailbox(["dave@gmail.com", "bookings@theswan.pub", "theswanpub@gmail.com"],
                                       "The Swan Pub", "https://theswan.pub", compliance.DEFAULT_ROLE_LOCAL_PARTS)
    assert email == "bookings@theswan.pub" and why == "role local part"
    email, why = pick_business_mailbox(["dave@gmail.com", "theswanpub@gmail.com"], "The Swan Pub", "", compliance.DEFAULT_ROLE_LOCAL_PARTS)
    assert email == "theswanpub@gmail.com" and why == "named after the venue"
    assert pick_business_mailbox(["dave@gmail.com"], "The Swan Pub", "", compliance.DEFAULT_ROLE_LOCAL_PARTS) == ("", "")


HOME = '<html><body><h1>The Swan</h1><a href="mailto:Bookings@TheSwan.pub?subject=x">book</a> Call us or email landlord@theswan.pub</body></html>'
CONTACT = '<html><body>Contact: info@theswan.pub, or Dave on dave@gmail.com. <img src="x@y.png"></body></html>'


def site_transport(hits):
    def handler(req: httpx.Request) -> httpx.Response:
        hits.append(str(req.url))
        if req.url.path in ("", "/"):
            return httpx.Response(200, text=HOME, headers={"content-type": "text/html"})
        if req.url.path.startswith("/contact"):
            return httpx.Response(200, text=CONTACT, headers={"content-type": "text/html"})
        return httpx.Response(404, text="")
    return httpx.MockTransport(handler)


def test_emails_in_reads_mailto_and_text_and_skips_images():
    assert emails_in(HOME) == ["bookings@theswan.pub", "landlord@theswan.pub"]
    assert "x@y.png" not in emails_in(CONTACT) and "dave@gmail.com" in emails_in(CONTACT)


@pytest.mark.asyncio
async def test_scrape_records_the_venues_mailbox_and_remembers_misses(state, settings):
    from harvey.craven.schema import ensure_schema
    await ensure_schema(state)
    install_signals(); await seed_signal_catalog(state)
    await state.set_signal_status("ROLE_MAILBOX", "confirmed")
    swan = await state.add_company(Company(name="The Swan", domain="theswan.pub", website="https://theswan.pub", industry="pub", source="discover:overture_supabase", external_id="overture:1"))
    dead = await state.add_company(Company(name="Nowhere", domain="nowhere.test", website="https://nowhere.test", industry="pub", source="discover:overture_supabase", external_id="overture:2"))
    hits = []

    def handler(req):
        if "nowhere" in str(req.url):
            return httpx.Response(500, text="")
        return site_transport(hits).handler(req)
    pages = VenuePages(transport=httpx.MockTransport(handler), delay=(0, 0))
    out = await scrape_mailboxes(state, settings, limit=10, pages=pages)
    assert out == {"companies": 2, "found": 1}
    obs = await state.get_observations(company_id=swan, signal_code="ROLE_MAILBOX")
    assert obs[0]["value_text"] == "info@theswan.pub" and obs[0]["collector"] == "mailbox_scrape"   # info@ ranks first among role addresses
    assert await state.get_observations(company_id=dead) == []
    assert (await scrape_mailboxes(state, settings, limit=10, pages=pages))["companies"] == 0   # both remembered


def test_parse_dossier_keeps_only_known_classes_and_real_addresses():
    d = parse_dossier({"what_they_sell": "Tapas and gin", "service_style": "bar-led",
                       "equipment": [{"class": "bottle_cooler", "evidence": "40 gins"}, {"class": "spaceship", "evidence": "x"}, {"class": "none"}],
                       "business_emails": ["hello@gintapas.com", "nope"], "hook": "Forty gins on the back bar."})
    assert d["equipment"] == [{"class": "bottle_cooler", "evidence": "40 gins"}]
    assert d["business_emails"] == ["hello@gintapas.com"] and d["hook"].startswith("Forty")
    assert parse_dossier("garbage") is None


class DossierBrain:
    def __init__(self):
        self.prompts = []

    async def is_within_budget(self, max_daily_calls=200, max_percent=None):
        return True

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        self.prompts.append(prompt)
        return {"what_they_sell": "Pub with real ale and Sunday roasts", "service_style": "table service",
                "equipment": [{"class": "cellar_cooling", "evidence": "six real ales on hand pull"}],
                "proud_of": "CAMRA pub of the year 2024", "hook": "Six real ales on hand pull is a cellar worth looking after.",
                "business_emails": ["landlord@theswan.pub", "dave@gmail.com"], "seasonal_note": "Christmas menu bookings open"}


@pytest.mark.asyncio
async def test_dossier_records_facts_class_and_the_venues_own_address(state, settings):
    from harvey.craven.schema import ensure_schema
    await ensure_schema(state)
    install_signals(); await seed_signal_catalog(state)
    for code in ("VENUE_DOSSIER", "MENU_EQUIPMENT_CLASS", "ROLE_MAILBOX"):
        await state.set_signal_status(code, "confirmed")
    swan = await state.add_company(Company(name="The Swan", domain="theswan.pub", website="https://theswan.pub", industry="pub", source="discover:overture_supabase", external_id="overture:1"))
    brain = DossierBrain()
    pages = VenuePages(transport=site_transport([]), delay=(0, 0))
    out = await build_dossiers(state, brain, settings, limit=10, pages=pages)
    assert out == {"companies": 1, "dossiers": 1, "mailboxes": 1}
    assert "The Swan" in brain.prompts[0] and "[https://theswan.pub]" in brain.prompts[0]
    obs = {o["signal_code"]: o for o in await state.get_observations(company_id=swan)}
    assert json.loads(obs["VENUE_DOSSIER"]["value_text"])["proud_of"].startswith("CAMRA")
    assert obs["MENU_EQUIPMENT_CLASS"]["value_text"] == "cellar_cooling" and obs["MENU_EQUIPMENT_CLASS"]["confidence"] == 0.9
    assert obs["ROLE_MAILBOX"]["value_text"] == "landlord@theswan.pub"       # on the venue's domain; dave@gmail is not
    # the writer turns the dossier into facts
    lines, lead = fact_lines(list(obs.values()) + [{"signal_code": "VENUE_CATEGORY", "value_text": "pub", "observed_at": "2026-10-21"}])
    joined = "\n".join(lines)
    assert "CAMRA" in joined and "six real ales" in joined and "Christmas menu" in joined and "landlord@" not in joined
    assert lead == "MENU_EQUIPMENT_CLASS"
