"""What a plugin tells the dashboard, so its routes stay generic.

The dashboard's venue page, map and draft-batch routes are core, but what
they show comes from a plugin that keeps coordinates, knows which signal
leads an email, and writes the drafts. A plugin's ``install()`` fills these
in; with none installed the routes answer honestly that nothing is
registered. Nothing here imports FastAPI, so the heartbeat process pays
nothing for it.
"""

from __future__ import annotations

from typing import Awaitable, Callable

# A table of (company_id, lat, lng, postcode) the map reads. Whoever fills
# it creates it; the dashboard only ever SELECTs from it.
GEO_TABLE: str = ""

# Which of a company's observations leads an email, if any:
# ``(observations: list[dict]) -> signal code or ""``. The map colours and
# the venue page name it as the lead signal.
LEAD_OF: Callable[[list[dict]], str] | None = None

# The signal whose value_text is a JSON document about the venue, rendered
# as plain facts on the venue page.
DOSSIER_CODE: str = ""

# Signals a reviewer may see but that never go into an email; the venue
# page says so next to them.
REVIEWER_ONLY_CODES: frozenset[str] = frozenset()

# Figures the map shows beside the pins (counts, never rows):
# ``async (state) -> dict[str, int]``.
FIGURES: Callable[..., Awaitable[dict]] | None = None

# Writes one grounded first email for a company into the Outbox as
# pending_review: ``async (state, brain, config, env, company_id) -> dict | None``.
DRAFT_ONE: Callable[..., Awaitable[dict | None]] | None = None


def reset() -> None:
    """Back to "no plugin" (tests)."""
    global GEO_TABLE, LEAD_OF, DOSSIER_CODE, REVIEWER_ONLY_CODES, FIGURES, DRAFT_ONE
    GEO_TABLE = ""
    LEAD_OF = None
    DOSSIER_CODE = ""
    REVIEWER_ONLY_CODES = frozenset()
    FIGURES = None
    DRAFT_ONE = None
