"""Dossier throughput: pages for the next venues are fetched while the one
model call runs, without changing what gets written."""

import asyncio
import json
import re
import time

import httpx
import pytest

from harvey.craven.dossier import Prefetcher, build_dossiers, companies_needing_dossier
from harvey.craven.pages import VenuePages
from harvey.craven.signals import install_signals
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog


def page_for(host: str) -> str:
    return f'<html><body><h1>{host}</h1> email <a href="mailto:info@{host}">x</a></body></html>'


class Sites:
    """An async MockTransport: every page takes ``delay`` seconds, and the
    transport records who was in flight when."""

    def __init__(self, delay=0.01, broken=()):
        self.delay, self.broken = delay, set(broken)
        self.requests: list[tuple[str, str, float]] = []     # host, path, start time
        self.inflight: dict[str, int] = {}
        self.max_inflight_per_host: dict[str, int] = {}
        self.max_inflight = 0

    async def handler(self, req: httpx.Request) -> httpx.Response:
        host = req.url.host
        self.requests.append((host, req.url.path, time.monotonic()))
        self.inflight[host] = self.inflight.get(host, 0) + 1
        self.max_inflight_per_host[host] = max(self.max_inflight_per_host.get(host, 0), self.inflight[host])
        self.max_inflight = max(self.max_inflight, sum(self.inflight.values()))
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.inflight[host] -= 1
        if host in self.broken:
            return httpx.Response(500, text="")
        if req.url.path in ("", "/"):
            return httpx.Response(200, text=page_for(host), headers={"content-type": "text/html"})
        if req.url.path == "/contact":
            return httpx.Response(200, text=f"<p>Contact {host}</p>", headers={"content-type": "text/html"})
        return httpx.Response(404, text="")

    def first_request(self, host: str) -> float:
        return min(t for h, _, t in self.requests if h == host)

    def hosts(self) -> set[str]:
        return {h for h, _, _ in self.requests}


class SlowBrain:
    """One model call at a time, ``delay`` seconds each; the dossier names
    the venue it was asked about so results can be checked per company."""

    def __init__(self, delay=0.05, gate: asyncio.Event | None = None):
        self.delay, self.gate = delay, gate
        self.prompts: list[str] = []
        self.calls: list[tuple[str, float, float]] = []      # venue, start, end
        self.active = 0
        self.max_active = 0

    async def is_within_budget(self, max_daily_calls=200, max_percent=None):
        return True

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        venue = re.search(r"Venue name: (.*)", prompt).group(1).strip()
        self.prompts.append(prompt)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        start = time.monotonic()
        if self.gate is not None and len(self.calls) == 0:
            await self.gate.wait()
        await asyncio.sleep(self.delay)
        self.active -= 1
        self.calls.append((venue, start, time.monotonic()))
        host = re.search(r"\[https?://([^\]/]+)", prompt).group(1)
        return {"what_they_sell": f"Dossier for {venue}", "service_style": "mixed",
                "equipment": [{"class": "bottle_cooler", "evidence": "wine list"}],
                "proud_of": None, "hook": None, "business_emails": [f"info@{host}"], "seasonal_note": None}


async def seed(state, n: int, shared_site: str = "") -> list[str]:
    from harvey.craven.schema import ensure_schema
    await ensure_schema(state)
    install_signals(); await seed_signal_catalog(state)
    for code in ("VENUE_DOSSIER", "MENU_EQUIPMENT_CLASS", "ROLE_MAILBOX"):
        await state.set_signal_status(code, "confirmed")
    ids = []
    for i in range(1, n + 1):
        site = shared_site or f"https://venue{i}.test"
        ids.append(await state.add_company(Company(name=f"Venue {i}", domain=f"venue{i}.test", website=site, industry="pub",
                                                   source="discover:overture_supabase", external_id=f"overture:{i}")))
    return ids


async def last_run(state) -> dict:
    async with state._connect() as db:
        async with db.execute("SELECT status, records, error FROM runs WHERE stage='dossier' ORDER BY rowid DESC LIMIT 1") as cur:
            r = await cur.fetchone()
            return {"status": r[0], "records": r[1], "error": r[2]}


async def enrichment_rows(state) -> dict[str, tuple[int, str]]:
    async with state._connect() as db:
        async with db.execute("SELECT company_id, matched, detail FROM craven_enrichment WHERE source='dossier'") as cur:
            return {r[0]: (r[1], r[2]) for r in await cur.fetchall()}


@pytest.mark.asyncio
async def test_fetches_overlap_the_model_call_and_each_other_but_rows_are_unchanged(state, settings):
    ids = await seed(state, 5)
    order = [c["name"] for c in await companies_needing_dossier(state, 90, 10)]
    sites, brain = Sites(delay=0.01), SlowBrain(delay=0.05)
    pages = VenuePages(transport=httpx.MockTransport(sites.handler), delay=(0, 0))

    t0 = time.monotonic()
    out = await build_dossiers(state, brain, settings, limit=10, pages=pages)
    wall = time.monotonic() - t0
    assert out == {"companies": 5, "dossiers": 5, "mailboxes": 5}
    # one model call at a time, in company order
    assert brain.max_active == 1 and [v for v, _, _ in brain.calls] == order
    # the second venue's pages were being fetched while the first model call ran
    first_call_end = brain.calls[0][2]
    second_host = f"venue{order[1].split()[1]}.test"
    assert sites.first_request(second_host) < first_call_end
    # fetch workers overlap each other across hosts, never within one host
    assert sites.max_inflight >= 2 and max(sites.max_inflight_per_host.values()) == 1
    # 5 venues x (1 home + 18 paths) pages at 10 ms would be ~1 s sequentially; the model is the long pole
    assert wall < 5 * (0.05 + 19 * 0.01)
    # every row the sequential loop wrote: dossier, class at 0.9, the venue's own mailbox, and the detail
    rows = await enrichment_rows(state)
    for cid, i in zip(ids, range(1, 6)):
        obs = {o["signal_code"]: o for o in await state.get_observations(company_id=cid)}
        assert json.loads(obs["VENUE_DOSSIER"]["value_text"])["what_they_sell"] == f"Dossier for Venue {i}"
        assert obs["VENUE_DOSSIER"]["evidence_url"] == f"https://venue{i}.test"
        assert obs["MENU_EQUIPMENT_CLASS"]["value_text"] == "bottle_cooler" and obs["MENU_EQUIPMENT_CLASS"]["confidence"] == 0.9
        assert obs["ROLE_MAILBOX"]["value_text"] == f"info@venue{i}.test"
        assert rows[cid] == (1, f"Dossier for Venue {i} | ['bottle_cooler'] | info@venue{i}.test")
    assert await last_run(state) == {"status": "completed", "records": 5, "error": ""}
    assert (await build_dossiers(state, brain, settings, limit=10, pages=pages))["companies"] == 0


@pytest.mark.asyncio
async def test_prefetch_runs_at_most_three_venues_ahead_of_the_model(state, settings):
    await seed(state, 7)
    order = [c["name"] for c in await companies_needing_dossier(state, 90, 10)]
    gate = asyncio.Event()
    sites, brain = Sites(delay=0.0), SlowBrain(delay=0.0, gate=gate)
    pages = VenuePages(transport=httpx.MockTransport(sites.handler), delay=(0, 0))
    task = asyncio.create_task(build_dossiers(state, brain, settings, limit=10, pages=pages, prefetch=3))
    await asyncio.sleep(0.2)
    # the model is stuck on venue 1; venues 2-4 are fetched and waiting, 5-7 untouched
    hosts = lambda names: {f"venue{n.split()[1]}.test" for n in names}
    assert len(brain.prompts) == 1 and sites.hosts() == hosts(order[:4])
    gate.set()
    out = await task
    assert out["dossiers"] == 7 and sites.hosts() == hosts(order) and [v for v, _, _ in brain.calls] == order


@pytest.mark.asyncio
async def test_two_companies_on_one_site_are_fetched_one_at_a_time(state, settings):
    await seed(state, 3, shared_site="https://shared.test")
    sites, brain = Sites(delay=0.01), SlowBrain(delay=0.0)
    pages = VenuePages(transport=httpx.MockTransport(sites.handler), delay=(0, 0))
    out = await build_dossiers(state, brain, settings, limit=10, pages=pages, fetch_workers=3)
    assert out["dossiers"] == 3 and sites.max_inflight_per_host["shared.test"] == 1


@pytest.mark.asyncio
async def test_unreachable_sites_are_remembered_and_a_fetch_error_fails_the_run_in_order(state, settings):
    ids = await seed(state, 4)
    order = [c["id"] for c in await companies_needing_dossier(state, 90, 10)]
    sites, brain = Sites(delay=0.0, broken={"venue2.test"}), SlowBrain(delay=0.0)
    pages = VenuePages(transport=httpx.MockTransport(sites.handler), delay=(0, 0))
    out = await build_dossiers(state, brain, settings, limit=10, pages=pages)
    assert out == {"companies": 4, "dossiers": 3, "mailboxes": 3}
    rows = await enrichment_rows(state)
    assert rows[ids[1]] == (0, "site unreachable") and all(rows[i][0] == 1 for i in (ids[0], ids[2], ids[3]))

    # a fetch that blows up surfaces on its own venue, after the earlier ones were written
    class ExplodingPages(VenuePages):
        async def fetch(self, website, paths=()):
            if "venue3" in website:
                raise RuntimeError("dns on fire")
            return await super().fetch(website, paths)
    async with state._connect() as db:
        await db.execute("DELETE FROM craven_enrichment WHERE source='dossier'"); await db.commit()
    pages = ExplodingPages(transport=httpx.MockTransport(Sites(delay=0.0).handler), delay=(0, 0))
    with pytest.raises(RuntimeError, match="dns on fire"):
        await build_dossiers(state, brain, settings, limit=10, pages=pages)
    rows = await enrichment_rows(state)
    done_before = [cid for cid in order[:order.index(ids[2])]]
    assert all(rows[cid][0] == 1 for cid in done_before) and ids[2] not in rows
    run = await last_run(state)
    assert run["status"] == "failed" and "dns on fire" in run["error"] and run["records"] == len(done_before)


@pytest.mark.asyncio
async def test_prefetcher_cancels_its_workers_on_exit():
    class Pages:
        def __init__(self): self.calls = []
        async def fetch(self, website, paths=()):
            self.calls.append(website); await asyncio.sleep(10); return {website: "x"}
    pages = Pages()
    async with Prefetcher(pages, [f"https://v{i}.test" for i in range(5)], (), ahead=2, workers=2) as f:
        await asyncio.sleep(0.02)
        assert f.started == [0, 1] and pages.calls == ["https://v0.test", "https://v1.test"]
    assert all(t.cancelled() or t.done() for t in f._tasks)
