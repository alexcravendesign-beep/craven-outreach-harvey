"""The UK gate and the unsubscribe footer: PECR/GDPR safeguards as code.

Runs after Harvey's own deterministic gate, through ``Sender.extra_gates``.
For a cold email (kind == "sequence") it refuses when:

- the recipient is not a shared role mailbox (corporate-subscriber route);
- the recipient's address or domain is on the suppression list;
- the suppression mirror is older than allowed (FAIL CLOSED: an unreadable
  list is not an empty list);
- the campaign's lawful basis (the configured LIA ref) has not been signed
  by a human;
- the finished body carries no valid unsubscribe token, or one minted for a
  different recipient.

For a reply (kind == "reply") only the suppression check applies: answering
someone who wrote to us is not unsolicited marketing, and they may well have
written from a personal address.
"""

import logging

from harvey.craven import compliance
from harvey.craven.unsubscribe import mint_token, token_in_body, unsubscribe_url, verify_token
from harvey.gate import GateResult

logger = logging.getLogger("harvey.craven.gate")

FOOTER_PREFIX = "Stop these emails: "


class UKGate:
    def __init__(self, *, settings, suppression, lawful_basis, secret: str):
        self.settings = settings
        self.suppression = suppression
        self.lawful_basis = lawful_basis
        self.secret = secret

    @property
    def tenant(self) -> str:
        return self.settings.tenant

    def footer(self, to_email: str, campaign_id: str) -> str | None:
        """The unsubscribe line for this recipient, or None when it can't be minted."""
        base = (self.settings.compliance.unsubscribe_base_url or "").strip()
        if not base or not self.secret:
            return None
        return FOOTER_PREFIX + unsubscribe_url(base, mint_token(self.secret, self.tenant, to_email, campaign_id))

    def finalize_body(self, body: str, item: dict) -> str:
        """Sender.body_finalizers hook: append the unsubscribe line to cold email."""
        if item.get("kind") != "sequence":
            return body
        line = self.footer(item.get("to_email", ""), item.get("campaign_id", ""))
        if not line or line in body:
            return body
        return body.rstrip() + "\n" + line

    async def __call__(self, item: dict, prospect, body: str) -> GateResult | None:
        """Sender.extra_gates hook."""
        reasons: list[str] = []
        to_email = (item.get("to_email") or "").strip().lower()
        c = self.settings.compliance

        if await self.suppression.is_stale(c.max_mirror_age_hours):
            reasons.append(
                f"suppression mirror is stale (older than {c.max_mirror_age_hours}h or never synced); "
                "refusing rather than guessing")
        elif await self.suppression.is_suppressed(to_email):
            reasons.append("suppressed")

        if item.get("kind") != "sequence":
            return GateResult(ok=not reasons, reasons=reasons)

        if c.corporate_subscribers_only and not compliance.is_role_address(to_email, c.role_local_parts):
            reasons.append(f"personal address blocked (not a role mailbox): {to_email}")

        if not c.lia_ref:
            reasons.append("no lawful basis configured (craven.compliance.lia_ref)")
        elif not await self.lawful_basis.is_signed(c.lia_ref):
            reasons.append(f"lawful basis '{c.lia_ref}' is not signed")

        token = token_in_body(body)
        parsed = verify_token(self.secret, token) if token else None
        if not parsed:
            reasons.append("unsubscribe link missing or invalid")
        elif parsed["email"] != to_email or parsed["tenant"] != self.tenant:
            reasons.append("unsubscribe link is for a different recipient")

        return GateResult(ok=not reasons, reasons=reasons)
