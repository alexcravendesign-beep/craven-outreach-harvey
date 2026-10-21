"""The ``craven:`` section of harvey.local.yaml, typed.

Harvey's HarveyConfig ignores unknown top-level keys, so this section is
parsed by the plugin from the raw YAML mapping that load_plugins passes in.
"""

from pydantic import BaseModel, ValidationError


class CravenComplianceConfig(BaseModel):
    # The signed LIA this tenant sends under (recorded by `harvey craven lia sign`).
    lia_ref: str = ""
    # Only corporate role mailboxes are emailed cold (UK PECR corporate-subscriber route).
    role_local_parts: list[str] = [
        "info", "sales", "enquiries", "enquiry", "contact", "hello", "office",
        "admin", "bookings", "booking", "reservations", "reservation", "manager",
        "marketing", "mail", "general", "team", "orders", "accounts", "support",
        "hq", "shop", "reception",
    ]
    corporate_subscribers_only: bool = True
    # Public base URL the per-recipient unsubscribe link points at (no trailing slash).
    unsubscribe_base_url: str = ""
    # How stale the local suppression mirror may be before cold sends fail closed.
    max_mirror_age_hours: int = 24
    sender_legal_name: str = "Craven Cooling Sales Ltd"
    company_number: str = ""


class CravenDeliverabilityConfig(BaseModel):
    sending_domain: str = ""
    # Cold email leaves only inside this window (Europe/London), Mon-Fri.
    send_days: list[int] = [0, 1, 2, 3, 4]      # Monday=0
    send_hours: tuple[str, str] = ("09:30", "16:30")
    timezone: str = "Europe/London"


class CravenDiscoveryConfig(BaseModel):
    source: str = "supabase"        # supabase | parquet
    parquet_path: str = ""
    # Venues sharing a website with more than this many others are a chain.
    chain_domain_threshold: int = 2


class CravenConfig(BaseModel):
    tenant: str = "craven"
    compliance: CravenComplianceConfig = CravenComplianceConfig()
    deliverability: CravenDeliverabilityConfig = CravenDeliverabilityConfig()
    discovery: CravenDiscoveryConfig = CravenDiscoveryConfig()


def parse_craven_config(section: dict) -> CravenConfig:
    # Imported here so the unsubscribe service can use this package without
    # Harvey's full configuration module (and its dependencies).
    from harvey.config import ConfigError
    if not isinstance(section, dict):
        raise ConfigError("'craven' in harvey.local.yaml must be a mapping.")
    try:
        return CravenConfig(**section)
    except ValidationError as e:
        raise ConfigError(f"Invalid craven settings:\n{e}")
