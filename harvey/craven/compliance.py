"""Role-address classification: the UK PECR corporate-subscriber route.

Cold email goes only to a business's shared mailbox (info@, sales@ ...),
never to a person's own address. Ported from v1 spine/compliance.mjs.
"""

DEFAULT_ROLE_LOCAL_PARTS = frozenset({
    "info", "sales", "enquiries", "enquiry", "contact", "hello", "office",
    "admin", "bookings", "booking", "reservations", "reservation", "manager",
    "marketing", "mail", "general", "team", "orders", "accounts", "support",
    "hq", "shop", "reception",
})


def local_part(email: str) -> str:
    return (email or "").strip().lower().split("@", 1)[0]


def classify_email(email: str, role_local_parts=DEFAULT_ROLE_LOCAL_PARTS) -> str:
    """'role' for a shared business mailbox, else 'personal'."""
    return "role" if local_part(email) in set(role_local_parts) else "personal"


def is_role_address(email: str, role_local_parts=DEFAULT_ROLE_LOCAL_PARTS) -> bool:
    return classify_email(email, role_local_parts) == "role"
