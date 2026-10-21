"""Per-recipient, HMAC-signed unsubscribe tokens.

Byte-for-byte the v1 format (spine/compliance.mjs mintUnsubscribeToken):

    payload = base64url( JSON {"t": tenant, "e": email.lower(), "c": campaign_id} )  # no padding
    sig     = base64url( HMAC-SHA256(secret, payload) )
    token   = payload + "." + sig

so the secret already in use (v1's OE_SECRET) and the live v1 /unsubscribe
route keep verifying tokens minted here, and vice versa. The link format is
v1's too: <base_url>/unsubscribe?t=<token>.
"""

import base64
import hashlib
import hmac
import json
import re

TOKEN_RE = re.compile(r"/unsubscribe\?t=([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint_token(secret: str, tenant: str, email: str, campaign_id: str) -> str:
    if not secret:
        raise ValueError("unsubscribe secret is not set")
    # JSON.stringify: no spaces, insertion order, non-ASCII left as-is.
    payload_json = json.dumps(
        {"t": tenant, "e": (email or "").lower(), "c": campaign_id or ""},
        separators=(",", ":"), ensure_ascii=False,
    )
    payload = _b64url(payload_json.encode("utf-8"))
    sig = _b64url(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(secret: str, token: str) -> dict | None:
    """{"tenant", "email", "campaign_id"} for a genuine token, else None."""
    if not secret or not token or token.count(".") != 1:
        return None
    payload, sig = token.split(".", 1)
    if not payload or not sig:
        return None
    expect = _b64url(hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(sig.encode("ascii"), expect.encode("ascii")):
        return None
    try:
        data = json.loads(_unb64url(payload).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {"tenant": data.get("t", ""), "email": data.get("e", ""), "campaign_id": data.get("c", "")}


def unsubscribe_url(base_url: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/unsubscribe?t={token}"


def token_in_body(body: str) -> str | None:
    """The first unsubscribe token found in an email body, or None."""
    m = TOKEN_RE.search(body or "")
    return m.group(1) if m else None
