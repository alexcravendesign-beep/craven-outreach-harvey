"""Which addresses cold email may go to: the business's mailbox, never a person's.

Three ways an address counts as the venue's own (Nick, 21 Oct 2026):
1. a role local part (info@, bookings@ ...);
2. the address is on the venue's own domain (the one its website uses);
3. the local part is the venue's name (thepubname@gmail.com is the pub).
Anything else (jo.bloggs@gmail.com) is treated as a person and refused.
"""

import re

DEFAULT_ROLE_LOCAL_PARTS = frozenset({
    "info", "sales", "enquiries", "enquiry", "contact", "hello", "office",
    "admin", "bookings", "booking", "reservations", "reservation", "manager",
    "marketing", "mail", "general", "team", "orders", "accounts", "support",
    "hq", "shop", "reception",
})
# Words that don't identify a venue on their own.
_GENERIC = {"the", "and", "of", "at", "bar", "cafe", "café", "restaurant", "hotel", "inn", "pub", "kitchen",
            "takeaway", "coffee", "shop", "lounge", "house", "arms", "grill", "club", "ltd", "limited", "co",
            "uk", "wales", "swansea", "mumbles", "gower", "llanelli", "neath", "bridgend"}


def local_part(email: str) -> str:
    return (email or "").strip().lower().split("@", 1)[0]


def email_domain(email: str) -> str:
    return (email or "").strip().lower().split("@", 1)[1] if "@" in (email or "") else ""


def registrable(host: str) -> str:
    """'www.theswan.pub/x' -> 'theswan.pub' (a cheap registrable-domain guess)."""
    h = (host or "").strip().lower()
    h = re.sub(r"^https?://", "", h).split("/")[0].split("@")[-1]
    h = h[4:] if h.startswith("www.") else h
    parts = h.split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "org", "gov", "ac", "ltd", "plc", "me") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else h


def _name_keys(venue_name: str) -> list[str]:
    words = [w for w in re.sub(r"[^a-z0-9 ]+", " ", (venue_name or "").lower()).split() if w not in _GENERIC]
    keys = []
    if words:
        keys.append("".join(words))              # "theswan" / "mexchickenperiperi"
        keys.extend(w for w in words if len(w) >= 5)
    return keys


def classify_email(email: str, role_local_parts=DEFAULT_ROLE_LOCAL_PARTS) -> str:
    """'role' for a shared business mailbox by local part, else 'personal'."""
    return "role" if local_part(email) in set(role_local_parts) else "personal"


def is_role_address(email: str, role_local_parts=DEFAULT_ROLE_LOCAL_PARTS) -> bool:
    return classify_email(email, role_local_parts) == "role"


def is_business_mailbox(email: str, venue_name: str = "", venue_website: str = "",
                        role_local_parts=DEFAULT_ROLE_LOCAL_PARTS) -> tuple[bool, str]:
    """(True, reason) when the address is the venue's own mailbox."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return False, "not an address"
    if is_role_address(email, role_local_parts):
        return True, "role local part"
    dom = email_domain(email)
    site = registrable(venue_website) if venue_website else ""
    if site and registrable(dom) == site:
        return True, "venue's own domain"
    local = re.sub(r"[^a-z0-9]", "", local_part(email))
    keys = _name_keys(venue_name)
    for i, key in enumerate(keys):
        k = re.sub(r"[^a-z0-9]", "", key)
        # the joined name ("theswan") may be short; single words must be 5+ letters
        floor = 4 if i == 0 else 5
        if len(k) >= floor and (k in local or (local in k and len(local) >= floor)):
            return True, "named after the venue"
    return False, "looks like a person's address"
