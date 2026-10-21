"""The Map tab's basemap tiles come through the dashboard: public like an
image must be, strictly bounded, fetched once with an identifying
User-Agent, cached on disk, and never a 500 when the tile server says no."""

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import harvey.dashboard as dash
from harvey import dashboard_auth as auth
from harvey import tiles

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture
def anon(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp:
        dash.token_store.configure(Path(tmp) / "data" / "dashboard-token")
        dash.token_store.get()
        monkeypatch.setattr(dash, "TILE_CACHE_DIR", Path(tmp) / "tiles")
        with TestClient(dash.app, base_url="http://127.0.0.1:5555") as c:
            c.cache = Path(tmp) / "tiles"
            yield c


def test_tile_paths_are_public_and_nothing_near_them_is():
    assert auth.is_public_path("GET", "/tiles/12/2010/1348.png")
    assert not auth.is_public_path("POST", "/tiles/12/2010/1348.png")
    for bad in ("/tiles/12/2010/1348.jpg", "/tiles/../x.png", "/tiles/12/2010/1348.png/", "/tiles/a/b/c.png",
                "/tiles/12/2010/1348.png?x=1", "/tiles/12/2010/1348.pngx", "/tilesx/1/1/1.png"):
        assert not auth.is_public_path("GET", bad), bad


def test_bounds():
    assert tiles.valid(0, 0, 0) and tiles.valid(19, (1 << 19) - 1, 0)
    assert not tiles.valid(20, 0, 0) and not tiles.valid(3, 8, 0) and not tiles.valid(3, 0, -1)


def test_tile_is_fetched_once_with_our_user_agent_then_served_from_disk(anon, monkeypatch):
    calls = []

    async def fake_fetch(z, x, y):
        calls.append((z, x, y))
        return PNG
    monkeypatch.setattr(tiles, "fetch_upstream", fake_fetch)
    monkeypatch.setattr(tiles.get_tile, "__defaults__", (fake_fetch,))
    r = anon.get("/tiles/12/2010/1348.png")           # no Authorization header at all
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    # the security middleware sets no-store on every response (static assets
    # too); the disk cache is what keeps the tile server quiet
    assert (anon.cache / "12" / "2010" / "1348.png").read_bytes() == PNG
    assert anon.get("/tiles/12/2010/1348.png").content == PNG
    assert calls == [(12, 2010, 1348)]
    assert "harvey-dashboard" in tiles.USER_AGENT and "github.com" in tiles.USER_AGENT
    # the security headers still ride on the image
    assert r.headers["cross-origin-resource-policy"] == "same-origin"


def test_out_of_range_tile_is_404_and_never_fetched(anon, monkeypatch):
    async def boom(z, x, y):
        raise AssertionError("fetched")
    monkeypatch.setattr(tiles.get_tile, "__defaults__", (boom,))
    assert anon.get("/tiles/20/0/0.png").status_code == 404
    assert anon.get("/tiles/3/8/0.png").status_code == 404
    assert anon.get("/tiles/x/0/0.png").status_code == 401     # not a tile path, so not public


def test_tile_server_refusal_is_a_502_not_a_crash(anon, monkeypatch):
    async def refused(z, x, y):
        raise RuntimeError("tile server answered 403 image/png")
    monkeypatch.setattr(tiles.get_tile, "__defaults__", (refused,))
    r = anon.get("/tiles/12/2010/1348.png")
    assert r.status_code == 502
    assert not (anon.cache / "12").exists()


def test_stale_cache_is_refetched(tmp_path, monkeypatch):
    import asyncio
    import os
    import time
    p = tiles.cache_path(tmp_path, 1, 0, 0)
    p.parent.mkdir(parents=True)
    p.write_bytes(b"old")
    old = time.time() - tiles.CACHE_SECONDS - 10
    os.utime(p, (old, old))

    async def fresh(z, x, y):
        return b"new"
    assert asyncio.run(tiles.get_tile(tmp_path, 1, 0, 0, fetch=fresh)) == b"new"
    assert p.read_bytes() == b"new"
