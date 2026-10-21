"""Craven Cooling's plugin for Harvey (UK commercial refrigeration → hospitality).

Loaded through ``plugins: [harvey.craven]`` in harvey.local.yaml. Everything
Craven-specific lives in this package and attaches to Harvey's generic seams:

- ``main.AGENT_OVERRIDES`` — subclasses of Scout/Writer/Sender/Handler
- ``discover.PROVIDERS`` — the owned Overture/Supabase discovery provider
- ``signals.SIGNAL_CATALOG`` — the Craven signal catalogue
- ``profile.COLLECTOR_CLASS`` — UK venue profiling
- ``Sender.extra_gates`` / ``body_finalizers`` / ``cold_send_window`` — the
  PECR compliance spine (role-address-only, suppression, signed unsubscribe,
  lawful-basis reference) and the Mon-Fri send window

Milestone 0 ships the seam and this package's configuration only; each later
milestone adds its module here and registers it from ``install()``.
"""

import logging

from harvey.craven.config import CravenConfig, parse_craven_config

logger = logging.getLogger("harvey.craven")

# The parsed ``craven:`` section, set by install(). Modules in this package
# read it from here rather than threading it through Harvey's own config.
SETTINGS: CravenConfig | None = None


def install(config, raw: dict) -> None:
    """Harvey's plugin entry point. Idempotent: load_plugins calls it once."""
    global SETTINGS
    SETTINGS = parse_craven_config(raw.get("craven") or {})
    logger.info(
        "Craven plugin installed (tenant=%s, corporate_subscribers_only=%s)",
        SETTINGS.tenant, SETTINGS.compliance.corporate_subscribers_only,
    )
