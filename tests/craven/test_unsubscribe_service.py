"""The public unsubscribe service: a GET only shows the page (links get
prefetched), a POST or confirm=1 writes the suppression, a bad token
changes nothing."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from harvey.craven.supabase import SupabaseRest
from harvey.craven.unsubscribe import mint_token
from services.unsubscribe import app as service
from tests.craven.conftest import SECRET


class Recorder:
    def __init__(self):
        self.posts = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        self.posts.append((request.url.path.rsplit("/", 1)[-1], json.loads(request.content)))
        return httpx.Response(201, json=[])


@pytest.fixture
def client(monkeypatch):
    rec = Recorder()
    monkeypatch.setenv("UNSUB_HMAC_SECRET", SECRET)
    monkeypatch.setattr(service, "supabase",
                        lambda: SupabaseRest("https://sb.test", "k", transport=httpx.MockTransport(rec.handler)))
    service._hits.clear()
    return TestClient(service.app), rec


def _token(email="Info@Swan.pub", campaign="camp-42"):
    return mint_token(SECRET, "craven", email, campaign)


def test_healthz(client):
    c, _ = client
    assert c.get("/healthz").status_code == 200


def test_get_shows_the_confirmation_and_writes_nothing(client):
    c, rec = client
    r = c.get("/unsubscribe", params={"t": _token()})
    assert r.status_code == 200
    assert "Stop these emails" in r.text and 'name="t"' in r.text
    assert rec.posts == []
    assert r.headers["cache-control"] == "no-store"


def test_post_from_the_page_writes_both_rows_lowercased(client):
    c, rec = client
    r = c.post("/unsubscribe", data={"t": _token()})
    assert r.status_code == 200 and "Done" in r.text
    tables = dict(rec.posts)
    assert tables["oe_suppression"] == [{"tenant": "craven", "email": "info@swan.pub", "domain": None,
                                         "reason": "unsubscribe"}]
    assert tables["oe_unsubscribes"] == [{"tenant": "craven", "email": "info@swan.pub", "campaign_id": "camp-42"}]


def test_get_with_confirm_writes(client):
    c, rec = client
    r = c.get("/unsubscribe", params={"t": _token(), "confirm": "1"})
    assert r.status_code == 200 and "Done" in r.text
    assert [t for t, _ in rec.posts] == ["oe_suppression", "oe_unsubscribes"]


def test_one_click_post_to_the_link_itself(client):
    c, rec = client
    r = c.post(f"/unsubscribe?t={_token()}", content="List-Unsubscribe=One-Click",
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200 and r.text == "ok"
    assert len(rec.posts) == 2


def test_tampered_token_is_refused_and_nothing_is_written(client):
    c, rec = client
    bad = _token()[:-3] + "xyz"
    assert c.get("/unsubscribe", params={"t": bad}).status_code == 400
    assert c.post("/unsubscribe", data={"t": bad}).status_code == 400
    assert c.get("/unsubscribe", params={"t": bad, "confirm": "1"}).status_code == 400
    assert c.post("/unsubscribe", data={}).status_code == 400
    assert rec.posts == []


def test_a_token_for_another_tenant_is_refused(client):
    c, rec = client
    other = mint_token(SECRET, "someone-else", "info@swan.pub", "c")
    assert c.post("/unsubscribe", data={"t": other}).status_code == 400
    assert rec.posts == []


def test_no_address_is_logged(client, caplog):
    c, _ = client
    with caplog.at_level("INFO", logger="unsubscribe"):
        c.post("/unsubscribe", data={"t": _token()})
        c.get("/unsubscribe", params={"t": "garbage"})
    assert "swan.pub" not in caplog.text
    assert "recorded" in caplog.text and "invalid token" in caplog.text


def test_rate_limit_kicks_in(client):
    c, _ = client
    for _ in range(service.RATE_MAX_PER_WINDOW):
        assert c.get("/unsubscribe", params={"t": "x"}).status_code == 400
    assert c.get("/unsubscribe", params={"t": "x"}).status_code == 429
