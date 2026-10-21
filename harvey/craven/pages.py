"""Polite fetching of a venue's own pages (homepage, contact, menu), shared by
the mailbox scrape and the dossier job. Plain HTTP, no browser, no model."""

import asyncio
import random
import re

import httpx

from harvey.collectors.profile import _text

CONTACT_PATHS = ("/contact", "/contact-us", "/contact-us/", "/contactus", "/find-us", "/about", "/about-us", "/book")
MENU_PATHS = ("/menu", "/menus", "/food", "/drinks", "/drink", "/our-menu", "/food-menu", "/bar", "/cakes", "/takeaway")
UA = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 craven-outreach"
TIMEOUT = httpx.Timeout(connect=8.0, read=12.0, write=8.0, pool=8.0)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
MAILTO_RE = re.compile(r'href=["\']mailto:([^"\'?]+)', re.I)


def host_of(website: str) -> str:
    h = re.sub(r"^https?://", "", (website or "").strip().lower()).split("/")[0]
    return h


class VenuePages:
    def __init__(self, transport=None, delay=(0.4, 1.2)):
        self.transport = transport
        self.delay = delay

    async def _get(self, client: httpx.AsyncClient, url: str) -> str:
        try:
            r = await client.get(url)
        except (httpx.HTTPError, ValueError):
            return ""
        if r.status_code != 200 or "html" not in r.headers.get("content-type", "") and "text" not in r.headers.get("content-type", ""):
            return ""
        return r.text

    async def fetch(self, website: str, paths=()) -> dict[str, str]:
        """{url: html} for the homepage plus whichever of ``paths`` exist."""
        host = host_of(website)
        if not host:
            return {}
        out: dict[str, str] = {}
        async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True, transport=self.transport,
                                     headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"}) as client:
            home = ""
            base = ""
            for candidate in (f"https://{host}", f"http://{host}"):
                home = await self._get(client, candidate)
                if home:
                    base = candidate
                    break
            if not home:
                return {}
            out[base] = home
            for path in paths:
                await asyncio.sleep(random.uniform(*self.delay))
                html = await self._get(client, base + path)
                if html:
                    out[base + path] = html
        return out


def emails_in(html: str) -> list[str]:
    found = [m.lower() for m in MAILTO_RE.findall(html or "")]
    found += [m.lower() for m in EMAIL_RE.findall(_text(html or ""))]
    out = []
    for e in found:
        e = e.strip().rstrip(".")
        if e and e not in out and not e.endswith((".png", ".jpg", ".svg", ".gif", ".webp")):
            out.append(e)
    return out


def page_text(pages: dict[str, str], limit: int = 6000) -> str:
    chunks = []
    for url, html in pages.items():
        t = re.sub(r"\s+", " ", _text(html)).strip()
        chunks.append(f"[{url}]\n{t[:limit]}")
    return "\n\n".join(chunks)
