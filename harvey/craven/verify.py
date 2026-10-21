"""Honest grading of a published role mailbox.

The harvest says an address is role-shaped (info@, bookings@...). That proves
nothing about deliverability, so it is verified like any other address with
Harvey's own helpers: MX lookup, then the best configured verifier. Without a
verifier the status stays "guess", which the gate never sends to. No name
patterns are ever guessed: we only use what the business publishes.
"""

from harvey.craven import compliance
from harvey.integrations.email_finder import EmailResult, verify_published_address


async def verify_role_email(email: str, env=None, role_local_parts=None) -> EmailResult:
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return EmailResult(email=email, status="invalid")
    if not compliance.is_role_address(email, role_local_parts or compliance.DEFAULT_ROLE_LOCAL_PARTS):
        return EmailResult(email=email, status="invalid")  # personal: never a cold-email target
    try:
        status = await verify_published_address(email, env=env)
    except Exception:
        status = "guess"
    return EmailResult(email=email, status=status or "guess")
