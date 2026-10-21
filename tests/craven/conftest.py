"""Shared pieces for the Craven plugin tests: a state, Craven settings, and a
fake Supabase (PostgREST) that pages like the real one."""

import json
import os
import tempfile

import httpx
import pytest
import pytest_asyncio

from harvey.craven.config import CravenConfig
from harvey.craven.schema import ensure_schema
from harvey.craven.supabase import SupabaseRest
from harvey.state import StateManager

SECRET = "test-secret-123"
# Minted by the v1 Node engine (spine/compliance.mjs) with SECRET, tenant
# "craven", email "Info@Swan.pub", campaign "camp-42" on 2026-10-21.
NODE_TOKEN = ("eyJ0IjoiY3JhdmVuIiwiZSI6ImluZm9Ac3dhbi5wdWIiLCJjIjoiY2FtcC00MiJ9"
              ".wJGphpxw9gIwRJUqlnC8WuJFXH05KmQwu4DUgmm5wJQ")


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(os.path.join(tmpdir, "test.db"))
        await sm.init_db()
        await ensure_schema(sm)
        yield sm


@pytest.fixture
def settings():
    return CravenConfig(**{
        "tenant": "craven",
        "compliance": {"lia_ref": "lia-1", "unsubscribe_base_url": "https://u.test",
                       "max_mirror_age_hours": 24},
    })


class FakeSupabase:
    """Enough of PostgREST for the suppression and lawful-basis stores:
    GET with Range paging and a tenant/at filter, POST upserts recorded."""

    def __init__(self, suppression_rows=None, page=1000, pois=None):
        self.suppression = list(suppression_rows or [])
        self.pois = list(pois or [])
        self.posts = []
        self.gets = 0
        self.page = page

    def handler(self, request: httpx.Request) -> httpx.Response:
        table = request.url.path.rsplit("/", 1)[-1]
        if request.method == "POST":
            self.posts.append((table, json.loads(request.content), dict(request.headers)))
            return httpx.Response(201, json=[])
        self.gets += 1
        rows = {"oe_suppression": self.suppression, "pois": self.pois}.get(table, [])
        q = dict(request.url.params)
        at = q.get("at", "")
        if at.startswith("gt."):
            rows = [r for r in rows if (r.get("at") or "") > at[3:]]
        pc = q.get("postcode", "")
        if pc.startswith("like."):
            prefix = pc[5:].rstrip("*")
            rows = [r for r in rows if (r.get("postcode") or "").startswith(prefix)]
        elif pc.startswith("in.("):
            wanted_pcs = {x.strip('"') for x in pc[4:-1].split(",")}
            rows = [r for r in rows if (r.get("postcode") or "") in wanted_pcs]
        ids = q.get("id", "")
        if ids.startswith("in.("):
            wanted = set(ids[4:-1].split(","))
            rows = [r for r in rows if str(r.get("id")) in wanted]
        rng = request.headers.get("Range", "0-999")
        start, end = (int(x) for x in rng.split("-"))
        chunk = rows[start:end + 1]
        return httpx.Response(206 if chunk else 200, json=chunk,
                              headers={"Content-Range": f"{start}-{start + len(chunk) - 1}/{len(rows)}"})

    def client(self) -> SupabaseRest:
        return SupabaseRest("https://sb.test", "service-key", transport=httpx.MockTransport(self.handler))


def customer_rows(n: int, start_at="2026-07-14T21:45:32+00:00"):
    return [{"email": f"customer{i}@example.co.uk", "domain": None, "reason": "existing_customer",
             "at": start_at} for i in range(n)]
