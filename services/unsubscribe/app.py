"""The public unsubscribe service: the one thing a recipient can reach.

A cold email carries ``<base>/unsubscribe?t=<token>`` where the token is a
per-recipient HMAC (harvey.craven.unsubscribe, v1's exact format). This
service verifies it and writes the suppression to the system of record
(Supabase ``oe_suppression`` + ``oe_unsubscribes``); Harvey mirrors that on
its next heartbeat and its gate refuses the address from then on.

Mail clients and security gateways prefetch links, so a plain GET only
shows a confirmation page; the write happens on POST (the page's button, or
an RFC 8058 one-click body) or on GET with ``confirm=1`` for a one-tap link.
No recipient address is ever logged; only token validity and the outcome.
It depends on nothing of Harvey's state, so it runs anywhere.
"""

import html
import logging
import os
import time
from collections import defaultdict

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from harvey.craven.supabase import SupabaseRest
from harvey.craven.unsubscribe import verify_token

logger = logging.getLogger("unsubscribe")

TENANT = os.environ.get("CRAVEN_TENANT", "craven")
SECRET_ENV = ("UNSUB_HMAC_SECRET", "OE_SECRET")
RATE_WINDOW_SECONDS = 60
RATE_MAX_PER_WINDOW = 30
_hits: dict[str, list[float]] = defaultdict(list)


def secret() -> str:
    for name in SECRET_ENV:
        v = os.environ.get(name, "").strip()
        if v:
            return v
    return ""


def supabase() -> SupabaseRest | None:
    return SupabaseRest.from_env()


def _page(title: str, body: str, button_token: str | None = None) -> str:
    button = ""
    if button_token:
        button = (f'<form method="post" action="/unsubscribe"><input type="hidden" name="t" value="{html.escape(button_token)}">'
                  f'<button type="submit">Stop these emails</button></form>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{html.escape(title)}</title>
<style>body{{margin:0;min-height:90vh;display:grid;place-items:center;font-family:system-ui,sans-serif;background:#0a0e13;color:#e8f1f5}}
main{{max-width:420px;padding:32px;text-align:center}}h1{{font-size:20px;margin:0 0 10px}}p{{color:#8ba0ad;font-size:14px;line-height:1.6}}
button{{margin-top:16px;padding:12px 22px;font-size:16px;border:0;border-radius:8px;background:#00aeef;color:#001018;cursor:pointer}}</style></head>
<body><main><h1>{html.escape(title)}</h1><p>{body}</p>{button}</main></body></html>"""


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _hits[ip] if now - t < RATE_WINDOW_SECONDS]
    hits.append(now)
    _hits[ip] = hits
    return len(hits) > RATE_MAX_PER_WINDOW


async def record_unsubscribe(parsed: dict, sb: SupabaseRest) -> None:
    email = parsed["email"]
    await sb.upsert("oe_suppression",
                    [{"tenant": TENANT, "email": email, "domain": None, "reason": "unsubscribe"}],
                    on_conflict="tenant,email,domain,reason")
    await sb.upsert("oe_unsubscribes",
                    [{"tenant": TENANT, "email": email, "campaign_id": parsed.get("campaign_id") or None}],
                    on_conflict="id", resolution="merge-duplicates")


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def _headers(request: Request, call_next):
    response: Response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex"
    return response


@app.get("/healthz")
async def healthz():
    return PlainTextResponse("ok")


def _parsed_or_none(token: str) -> dict | None:
    parsed = verify_token(secret(), token)
    if not parsed or parsed.get("tenant") != TENANT or "@" not in (parsed.get("email") or ""):
        return None
    return parsed


INVALID = _page("This link isn't valid",
                "This unsubscribe link is invalid or has been altered. Nothing has changed. "
                "If you got here from an email, reply to it with the word STOP instead.")
DONE = _page("Done", "You won't hear from us again. This is permanent and takes effect immediately.")


@app.get("/unsubscribe")
async def unsubscribe_get(request: Request, t: str = "", confirm: str = ""):
    ip = request.client.host if request.client else "?"
    if _rate_limited(ip):
        return HTMLResponse(_page("Slow down", "Too many requests. Try again in a minute."), status_code=429)
    parsed = _parsed_or_none(t)
    if not parsed:
        logger.info("unsubscribe GET: invalid token")
        return HTMLResponse(INVALID, status_code=400)
    if confirm == "1":
        sb = supabase()
        if sb is None:
            return HTMLResponse(_page("Not available", "Please try again shortly."), status_code=503)
        await record_unsubscribe(parsed, sb)
        logger.info("unsubscribe GET confirm=1: recorded")
        return HTMLResponse(DONE)
    return HTMLResponse(_page("Stop these emails?",
                              "Press the button and you won't receive marketing email from us again.",
                              button_token=t))


@app.post("/unsubscribe")
async def unsubscribe_post(request: Request, t: str = ""):
    ip = request.client.host if request.client else "?"
    if _rate_limited(ip):
        return HTMLResponse(_page("Slow down", "Too many requests. Try again in a minute."), status_code=429)
    # The token comes from the query (one-click POST to the link itself) or
    # from the page's form field.
    token = t
    body_fields = {}
    try:
        raw = (await request.body()).decode("utf-8", "replace")
    except Exception:
        raw = ""
    if raw:
        from urllib.parse import parse_qs
        body_fields = {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}
    token = body_fields.get("t") or token
    one_click = body_fields.get("List-Unsubscribe") == "One-Click"
    parsed = _parsed_or_none(token)
    if not parsed:
        logger.info("unsubscribe POST: invalid token")
        return HTMLResponse(INVALID, status_code=400)
    sb = supabase()
    if sb is None:
        return HTMLResponse(_page("Not available", "Please try again shortly."), status_code=503)
    await record_unsubscribe(parsed, sb)
    logger.info("unsubscribe POST%s: recorded", " (one-click)" if one_click else "")
    if one_click:
        return PlainTextResponse("ok")
    return HTMLResponse(DONE)
