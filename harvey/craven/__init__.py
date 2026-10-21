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
    from harvey import main
    from harvey.collectors import discover
    from harvey.craven.discover_overture import OvertureSupabase
    from harvey.craven.handler import CravenHandler
    from harvey.craven.scout import CravenScout
    from harvey.craven.sender import CravenSender
    from harvey.craven.signals import install_signals
    main.AGENT_OVERRIDES["sender"] = CravenSender
    main.AGENT_OVERRIDES["handler"] = CravenHandler
    main.AGENT_OVERRIDES["scout"] = CravenScout
    install_signals()
    provider = OvertureSupabase(
        tenant=SETTINGS.tenant,
        chain_domain_threshold=SETTINGS.discovery.chain_domain_threshold,
        area_outcodes=tuple(getattr(config.icp, "geography", []) or []),
    )
    discover.PROVIDERS[provider.key] = provider
    discover.DEFAULT_PROVIDER = provider.key  # harvest from what we own first
    logger.info(
        "Craven plugin installed (tenant=%s, corporate_subscribers_only=%s)",
        SETTINGS.tenant, SETTINGS.compliance.corporate_subscribers_only,
    )
