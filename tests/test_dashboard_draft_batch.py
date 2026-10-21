"""Draft N: the cohort screen's one action that writes. Every draft lands in
the Outbox as pending_review through the plugin's writer; the route refuses,
saying why, whenever the writer or the model can't be used."""

import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import harvey.config as config_mod
import harvey.dashboard as dash
from harvey import claude_runtime
from harvey import dashboard_ext as ext
from harvey.config import EnvConfig, HarveyConfig
from harvey.models.company import Company
from harvey.models.prospect import COMPANY_INBOX_SOURCE, Prospect
from harvey.paths import PROJECT_ROOT
from harvey.state import StateManager

COHORT = {"require": ["VENUE_CATEGORY"], "limit": 20}


@pytest.fixture
def client(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "harvey.db"
        monkeypatch.setattr(dash, "DB_PATH", db)
        dash.token_store.configure(Path(tmp) / "data" / "dashboard-token")
        token = dash.token_store.get()
        ext.reset()
        cfg = HarveyConfig(**yaml.safe_load((PROJECT_ROOT / "harvey.yaml").read_text(encoding="utf-8")))
        monkeypatch.setattr(config_mod, "load_config", lambda config_path=None: cfg)
        monkeypatch.setattr(config_mod, "load_env", lambda: EnvConfig())
        with TestClient(dash.app, base_url="http://127.0.0.1:5555",
                        headers={"Authorization": f"Bearer {token}"}) as c:
            c.db_path = db
            c.cfg = cfg
            yield c
        ext.reset()


async def _seed(db_path, n=3, with_mailbox=True):
    state = StateManager(str(db_path))
    await state.init_db()
    await state.upsert_signal_code("VENUE_CATEGORY", label="Venue type", category="discovery", value_type="text")
    await state.upsert_signal_code("ROLE_MAILBOX", label="Mailbox", category="people", value_type="text")
    run_id = await state.start_run("discover", provider="test")
    ids = []
    for i in range(n):
        cid = await state.add_company(Company(name=f"Venue {i}", domain=f"v{i}.pub", industry="pub"))
        await state.add_observations([{"company_id": cid, "signal_code": "VENUE_CATEGORY", "collector": "t",
                                       "value_num": None, "value_text": "pub", "confidence": 1.0,
                                       "evidence_url": "", "run_id": run_id}], run_id=run_id)
        if with_mailbox:
            await state.add_prospect(Prospect(first_name="", last_name="", email=f"info@v{i}.pub", email_status="guess",
                                              company=f"Venue {i}", company_id=cid, source=COMPANY_INBOX_SOURCE))
        ids.append(cid)
    return state, ids


def _stub_writer(calls, fail_for=()):
    async def draft_one(state, brain, config, env, company_id):
        calls.append(company_id)
        if company_id in fail_for:
            return None
        prospect = (await state.get_contacts_for_company(company_id))[0]
        item = await state.add_outbox_item(prospect_id=prospect.id, to_email=prospect.email, subject=f"s-{company_id}",
                                           body="b", send_at="2026-10-21T09:00:00", status="pending_review")
        company = await state.get_company(company_id)
        return {"outbox_id": item, "company": company.name, "to": prospect.email, "lead": "VENUE_CATEGORY",
                "subject": f"s-{company_id}", "body": "b"}
    return draft_one


def test_refuses_without_a_plugin_writer(client):
    r = client.post("/api/craven/draft-batch", json=COHORT)
    assert r.status_code == 409 and "No plugin" in r.json()["message"]


def test_refuses_when_the_model_cannot_be_used(client, monkeypatch):
    ext.DRAFT_ONE = _stub_writer([])
    monkeypatch.setattr(claude_runtime, "find_claude", lambda env=None: None)
    claude_runtime.clear_cache()
    r = client.post("/api/craven/draft-batch", json=COHORT)
    assert r.status_code == 409
    assert "Claude CLI not found" in r.json()["message"] and "desktop" in r.json()["message"]
    assert client.get("/api/outbox").json()["pending"] == []


def test_refuses_when_the_config_cannot_be_loaded(client, monkeypatch):
    ext.DRAFT_ONE = _stub_writer([])

    def boom(config_path=None):
        raise config_mod.ConfigError("harvey.yaml is broken")
    monkeypatch.setattr(config_mod, "load_config", boom)
    r = client.post("/api/craven/draft-batch", json=COHORT)
    assert r.status_code == 409 and "harvey.yaml is broken" in r.json()["message"]


@pytest.mark.parametrize("body, status", [
    ({}, 400), ({"require": []}, 400), ({"require": "VENUE_CATEGORY", "limit": "x"}, 400),
    ([1, 2], 400), ({"require": ["x" * 200]}, 400),
])
def test_bad_requests_are_refused(client, body, status):
    ext.DRAFT_ONE = _stub_writer([])
    assert client.post("/api/craven/draft-batch", json=body).status_code == status


def test_stages_one_draft_per_cohort_company_with_a_mailbox(client):
    import asyncio
    state, ids = asyncio.run(_seed(client.db_path, n=3))
    calls = []
    ext.DRAFT_ONE = _stub_writer(calls)
    r = client.post("/api/craven/draft-batch", json={"require": ["VENUE_CATEGORY"], "limit": 2})
    data = r.json()
    assert r.status_code == 200 and data["success"]
    assert data["cohort"] == 3 and data["limit"] == 2 and len(data["drafted"]) == 2 and data["failed"] == []
    assert len(calls) == 2
    pending = client.get("/api/outbox").json()["pending"]
    assert len(pending) == 2 and all(p["status"] == "pending_review" for p in pending)
    assert {d["outbox_id"] for d in data["drafted"]} == {p["id"] for p in pending}
    assert data["drafted"][0]["lead"] == "VENUE_CATEGORY" and data["drafted"][0]["to"].startswith("info@")

    # Running again picks up the one left, skipping the two already drafted.
    r = client.post("/api/craven/draft-batch", json={"require": ["VENUE_CATEGORY"], "limit": 20})
    data = r.json()
    assert len(data["drafted"]) == 1 and data["skipped_already_drafted"] == 2
    assert len(client.get("/api/outbox").json()["pending"]) == 3
    r = client.post("/api/craven/draft-batch", json={"require": ["VENUE_CATEGORY"], "limit": 20})
    assert r.json()["drafted"] == [] and r.json()["skipped_already_drafted"] == 3


def test_companies_without_a_mailbox_are_skipped_and_failures_reported(client):
    import asyncio
    state, ids = asyncio.run(_seed(client.db_path, n=2, with_mailbox=False))

    async def one_mailbox():
        await state.add_prospect(Prospect(first_name="", last_name="", email="info@v0.pub", email_status="verified",
                                          company="Venue 0", company_id=ids[0], source=COMPANY_INBOX_SOURCE))
    asyncio.run(one_mailbox())
    calls = []
    ext.DRAFT_ONE = _stub_writer(calls, fail_for={ids[0]})
    data = client.post("/api/craven/draft-batch", json=COHORT).json()
    assert data["drafted"] == [] and data["skipped_no_prospect"] == 1
    assert [f["company_id"] for f in data["failed"]] == [ids[0]]
    assert calls == [ids[0]]


def test_a_writer_that_raises_counts_as_failed_not_a_500(client):
    import asyncio
    asyncio.run(_seed(client.db_path, n=1))

    async def boom(state, brain, config, env, company_id):
        raise RuntimeError("model gave nothing")
    ext.DRAFT_ONE = boom
    r = client.post("/api/craven/draft-batch", json=COHORT)
    assert r.status_code == 200 and len(r.json()["failed"]) == 1


def test_limit_is_capped_and_exclusions_apply(client):
    import asyncio
    state, ids = asyncio.run(_seed(client.db_path, n=2))

    async def exclude_first():
        run_id = await state.start_run("x", provider="t")
        await state.add_observations([{"company_id": ids[0], "signal_code": "ROLE_MAILBOX", "collector": "t",
                                       "value_num": None, "value_text": "info@v0.pub", "confidence": 1.0,
                                       "evidence_url": "", "run_id": run_id}], run_id=run_id)
    asyncio.run(exclude_first())
    calls = []
    ext.DRAFT_ONE = _stub_writer(calls)
    data = client.post("/api/craven/draft-batch",
                       json={"require": ["VENUE_CATEGORY"], "exclude": ["ROLE_MAILBOX"], "limit": 999}).json()
    assert data["limit"] == dash.DRAFT_BATCH_MAX and data["cohort"] == 1 and calls == [ids[1]]
