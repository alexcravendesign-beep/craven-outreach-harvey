"""The venue page and the map: one company as the reviewer sees it, every
placed company as a pin, both fed by whatever plugin registered itself in
harvey.dashboard_ext (none, here, unless a test sets it)."""

import json
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey import dashboard_ext as ext
from harvey.models.company import Company
from harvey.models.prospect import COMPANY_INBOX_SOURCE, Prospect
from harvey.state import StateManager


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "harvey.db"
        monkeypatch.setattr(dash, "DB_PATH", db)
        dash.token_store.configure(Path(tmp) / "data" / "dashboard-token")
        token = dash.token_store.get()
        ext.reset()
        with TestClient(dash.app, base_url="http://127.0.0.1:5555",
                        headers={"Authorization": f"Bearer {token}"}) as c:
            c.db_path = db
            yield c
        ext.reset()


async def _seed(db_path):
    state = StateManager(str(db_path))
    await state.init_db()
    for code, vt in (("VENUE_CATEGORY", "text"), ("FHRS_RATING", "number"), ("FHRS_HYGIENE_SCORE", "number"),
                     ("ENERGY_CLAIM", "bool"), ("VENUE_DOSSIER", "text")):
        await state.upsert_signal_code(code, label=code.replace("_", " ").title(), category="profile", value_type=vt)
    cid = await state.add_company(Company(name="The Swan", domain="theswan.pub", industry="pub", location="SA1",
                                          source="discover:overture_supabase", external_id="overture:a1"))
    other = await state.add_company(Company(name="Velvet", industry="cafe", source="discover:overture_supabase",
                                            external_id="overture:a2"))
    run_id = await state.start_run("profile", provider="test")
    obs = lambda code, num=None, text="", url="", at="2026-10-20T10:00:00": {  # noqa: E731
        "company_id": cid, "signal_code": code, "collector": "test", "value_num": num, "value_text": text,
        "confidence": 0.9, "evidence_url": url, "run_id": run_id, "observed_at": at}
    await state.add_observations([
        obs("VENUE_CATEGORY", text="cafe", url="https://overture.example/a1", at="2026-10-01T10:00:00"),
        obs("VENUE_CATEGORY", text="pub", url="https://overture.example/a1", at="2026-10-20T10:00:00"),
        obs("FHRS_RATING", num=5.0, url="https://ratings.food.gov.uk/business/1"),
        obs("FHRS_HYGIENE_SCORE", num=5.0, url="https://ratings.food.gov.uk/business/1"),
        obs("ENERGY_CLAIM", num=1.0, url="https://theswan.pub/about"),
        obs("VENUE_DOSSIER", text=json.dumps({"what_they_sell": "cask ale and Sunday roasts",
                                               "equipment": [{"class": "cellar_cooling", "evidence": "six cask lines"}],
                                               "hook": "the cellar"})),
    ], run_id=run_id)
    pid = await state.add_prospect(Prospect(first_name="", last_name="", title="General enquiries mailbox",
                                            email="info@theswan.pub", email_status="risky", company="The Swan",
                                            company_id=cid, source=COMPANY_INBOX_SOURCE))
    item = await state.add_outbox_item(prospect_id=pid, to_email="info@theswan.pub", subject="your cellar",
                                       body="Hello", send_at="2026-10-21T09:00:00", status="pending_review")
    return state, cid, other, pid, item


def test_unknown_company_is_404(client):
    assert client.get("/api/companies/nope").status_code == 404


def test_venue_page_shows_latest_signal_per_code_with_evidence(client):
    import asyncio
    state, cid, _, pid, item = asyncio.run(_seed(client.db_path))
    data = client.get(f"/api/companies/{cid}").json()
    assert data["company"]["name"] == "The Swan" and data["company"]["external_id"] == "overture:a1"
    by_code = {s["code"]: s for s in data["signals"]}
    assert by_code["VENUE_CATEGORY"]["value"] == "pub"          # latest wins
    assert by_code["VENUE_CATEGORY"]["evidence_url"] == "https://overture.example/a1"
    assert by_code["VENUE_CATEGORY"]["collector"] == "test" and by_code["VENUE_CATEGORY"]["confidence"] == 0.9
    assert by_code["FHRS_RATING"]["value"] == "5"
    assert by_code["ENERGY_CLAIM"]["value"] == "yes"
    # With no plugin, nothing is reviewer-only, nothing leads, and the
    # dossier is just another signal.
    assert not any(s["reviewer_only"] for s in data["signals"]) and data["lead"] == ""
    assert data["reviewer_only_note"] == "" and data["dossier"] is None
    assert "VENUE_DOSSIER" in by_code
    assert [p["id"] for p in data["prospects"]] == [pid]
    assert [d["id"] for d in data["drafts"]] == [item] and data["drafts"][0]["status"] == "pending_review"


def test_plugin_marks_reviewer_only_signals_the_lead_and_the_dossier(client):
    import asyncio
    _, cid, other, _, _ = asyncio.run(_seed(client.db_path))
    ext.REVIEWER_ONLY_CODES = frozenset({"FHRS_RATING", "FHRS_HYGIENE_SCORE"})
    ext.DOSSIER_CODE = "VENUE_DOSSIER"
    ext.LEAD_OF = lambda observations: "VENUE_CATEGORY" if any(
        o["signal_code"] == "VENUE_CATEGORY" for o in observations) else ""
    data = client.get(f"/api/companies/{cid}").json()
    by_code = {s["code"]: s for s in data["signals"]}
    assert "VENUE_DOSSIER" not in by_code
    assert data["dossier"]["what_they_sell"] == "cask ale and Sunday roasts"
    assert data["dossier"]["equipment"][0]["class"] == "cellar_cooling"
    assert by_code["FHRS_RATING"]["reviewer_only"] and by_code["FHRS_HYGIENE_SCORE"]["reviewer_only"]
    assert not by_code["ENERGY_CLAIM"]["reviewer_only"]
    assert "never go into an email" in data["reviewer_only_note"]
    assert data["lead"] == "VENUE_CATEGORY" and by_code["VENUE_CATEGORY"]["lead"]
    assert data["signals"][0]["code"] == "VENUE_CATEGORY"       # the lead comes first
    assert data["signals"][-1]["reviewer_only"]                 # reviewer-only rows last
    empty = client.get(f"/api/companies/{other}").json()
    assert empty["signals"] == [] and empty["lead"] == "" and empty["prospects"] == [] and empty["drafts"] == []


def test_outbox_context_carries_the_company_id_for_the_venue_link(client):
    import asyncio
    _, cid, _, _, item = asyncio.run(_seed(client.db_path))
    pending = client.get("/api/outbox").json()["pending"]
    assert pending[0]["id"] == item and pending[0]["context"]["company_id"] == cid


def test_reject_keeps_the_reason_as_feedback_on_the_draft(client):
    import asyncio
    _, _, _, _, item = asyncio.run(_seed(client.db_path))
    r = client.post(f"/api/outbox/{item}/reject", json={"reason": "too salesy, mentions the rating"})
    assert r.status_code == 200 and r.json()["success"] and r.json()["feedback_id"]
    rows = client.get(f"/api/feedback/outbox/{item}").json()
    assert [f["comment"] for f in rows] == ["too salesy, mentions the rating"]
    assert client.get("/api/outbox").json()["failed"][0]["status"] == "rejected"


def test_reject_without_a_reason_still_rejects_and_keeps_nothing(client):
    import asyncio
    _, _, _, _, item = asyncio.run(_seed(client.db_path))
    r = client.post(f"/api/outbox/{item}/reject")
    assert r.status_code == 200 and r.json() == {"success": True, "feedback_id": ""}
    assert client.get(f"/api/feedback/outbox/{item}").json() == []
    r = client.post(f"/api/outbox/{item}/reject", json={"reason": "   "})
    assert r.status_code == 409  # already rejected; nothing written either way
    assert client.get(f"/api/feedback/outbox/{item}").json() == []


# ── The map ──


def test_map_without_a_geo_plugin_says_so(client):
    data = client.get("/api/map").json()
    assert data["available"] is False and data["pins"] == [] and "nothing to map" in data["message"]


async def _place(db_path, table, rows):
    state = StateManager(str(db_path))
    async with state._connect() as db:
        await db.execute(f"CREATE TABLE IF NOT EXISTS {table} (company_id TEXT PRIMARY KEY, lat REAL, lng REAL, "
                         "postcode TEXT DEFAULT '', synced_at TEXT)")
        await db.executemany(f"INSERT INTO {table} VALUES (?, ?, ?, ?, 'now')", rows)
        await db.commit()


def test_map_pins_carry_reachability_lead_and_figures(client):
    import asyncio
    state, cid, other, _, _ = asyncio.run(_seed(client.db_path))
    asyncio.run(_place(client.db_path, "test_geo", [(cid, 51.62, -3.94, "SA1 1AA"), (other, 51.63, -3.95, "SA1 1AB"),
                                                     ("ghost", 0, 0, "")]))
    ext.GEO_TABLE = "test_geo"
    ext.LEAD_OF = lambda observations: "VENUE_CATEGORY" if observations else ""

    async def fig(state):
        return {"suppressed_customers": 5325}
    ext.FIGURES = fig
    data = client.get("/api/map").json()
    assert data["available"] is True and data["figures"] == {"suppressed_customers": 5325}
    pins = {p["id"]: p for p in data["pins"]}
    assert set(pins) == {cid, other}                          # a row with no company is not a pin
    assert pins[cid]["lat"] == 51.62 and pins[cid]["lng"] == -3.94 and pins[cid]["postcode"] == "SA1 1AA"
    assert pins[cid]["name"] == "The Swan" and pins[cid]["industry"] == "pub"
    assert pins[cid]["reachability"] == "emailable" and pins[cid]["lead"] == "VENUE_CATEGORY"
    assert pins[other]["reachability"] == "none" and pins[other]["lead"] == ""


def test_map_reachability_distinguishes_a_mere_mailbox_from_a_verified_one(client):
    import asyncio
    state, cid, other, _, _ = asyncio.run(_seed(client.db_path))

    async def guess():
        await state.add_prospect(Prospect(first_name="", last_name="", email="hello@velvet.cafe", email_status="guess",
                                          company="Velvet", company_id=other, source=COMPANY_INBOX_SOURCE))
    asyncio.run(guess())
    asyncio.run(_place(client.db_path, "test_geo", [(cid, 1, 1, ""), (other, 2, 2, "")]))
    ext.GEO_TABLE = "test_geo"
    pins = {p["id"]: p for p in client.get("/api/map").json()["pins"]}
    assert pins[cid]["reachability"] == "emailable" and pins[other]["reachability"] == "mailbox"


def test_map_refuses_a_table_name_that_is_not_an_identifier(client):
    ext.GEO_TABLE = "craven_geo; DROP TABLE companies"
    assert client.get("/api/map").json()["available"] is False
    assert client.get("/api/companies").status_code == 200


def test_the_craven_plugin_registers_itself_with_the_dashboard(client):
    """install() fills the seam from the writer's own rules."""
    from harvey import craven
    from harvey.craven.writer import fact_lines
    craven._install_dashboard()
    assert ext.GEO_TABLE == "craven_geo" and ext.DOSSIER_CODE == "VENUE_DOSSIER"
    assert {"FHRS_RATING", "FHRS_HYGIENE_SCORE", "FHRS_STRUCTURAL_SCORE", "FHRS_MANAGEMENT_SCORE"} <= ext.REVIEWER_ONLY_CODES
    assert "ROLE_MAILBOX" not in ext.REVIEWER_ONLY_CODES
    obs = [{"signal_code": "VENUE_CATEGORY", "value_text": "pub", "observed_at": "2026-10-20"}]
    assert ext.LEAD_OF(obs) == fact_lines(obs)[1] == "VENUE_CATEGORY"
    assert ext.DRAFT_ONE.__name__ == "draft_one" and ext.FIGURES.__name__ == "figures"
