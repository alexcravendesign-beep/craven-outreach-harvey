"""Basemap tiles for the Map tab, fetched by the dashboard and cached on disk.

The browser never talks to the tile server. It asks the dashboard for
``/tiles/{z}/{x}/{y}.png``; the dashboard fetches the tile once from
OpenStreetMap with a User-Agent that identifies this app (their tile usage
policy: https://operations.osmfoundation.org/policies/tiles/), keeps it under
``data/tiles/`` for a week, and serves it as its own image. That keeps the
page's CSP at ``img-src 'self'``, makes the map work the same on every
device and browser, and means the tile server sees one polite, named client
rather than whatever each reviewer's browser sends (which is what got the
plain ``<img>`` route a 403 "Access blocked" tile).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

logger = logging.getLogger("harvey.tiles")

UPSTREAM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
USER_AGENT = "harvey-dashboard/0.3 (+https://github.com/ethanplusai/harvey; self-hosted, internal use)"
MAX_ZOOM = 19
CACHE_SECONDS = 7 * 24 * 3600
_MAX_IN_FLIGHT = 4          # polite to a volunteer-run server; a map view is ~12 tiles

_semaphore: asyncio.Semaphore | None = None


def valid(z: int, x: int, y: int) -> bool:
    return 0 <= z <= MAX_ZOOM and 0 <= x < (1 << z) and 0 <= y < (1 << z)


def cache_path(root: Path, z: int, x: int, y: int) -> Path:
    return root / str(z) / str(x) / f"{y}.png"


def cached(root: Path, z: int, x: int, y: int, now: float | None = None) -> bytes | None:
    p = cache_path(root, z, x, y)
    try:
        st = p.stat()
    except FileNotFoundError:
        return None
    if (now or time.time()) - st.st_mtime > CACHE_SECONDS:
        return None
    return p.read_bytes()


async def fetch_upstream(z: int, x: int, y: int) -> bytes:
    """One tile from the tile server, identified as this app. Raises on anything but a PNG."""
    import httpx
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(_MAX_IN_FLIGHT)
    async with _semaphore:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                     headers={"User-Agent": USER_AGENT, "Accept": "image/png"}) as client:
            r = await client.get(UPSTREAM.format(z=z, x=x, y=y))
    if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
        raise RuntimeError(f"tile server answered {r.status_code} {r.headers.get('content-type', '')}")
    return r.content


async def get_tile(root: Path, z: int, x: int, y: int, fetch=fetch_upstream) -> bytes:
    """The tile bytes: from the disk cache when fresh, else fetched and cached."""
    hit = cached(root, z, x, y)
    if hit is not None:
        return hit
    data = await fetch(z, x, y)
    p = cache_path(root, z, x, y)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(p)
    return data
