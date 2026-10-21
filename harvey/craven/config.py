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
    sender_legal_name: str = "Craven Cooling Services Ltd"
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


class CravenEnrichmentConfig(BaseModel):
    # FHRS (Food Hygiene Rating Scheme) local authorities covering the pilot
    # area; each is one API call of up to 5,000 establishments, cached.
    fhrs_authority_ids: list[int] = [351, 346, 341, 349]  # Swansea, Neath Port Talbot, Carmarthenshire, Powys
    fhrs_cache_days: int = 7
    fhrs_match_floor: float = 0.85
    # Companies House: hospitality SIC codes and the area for the new-company sweep.
    companies_house_sic: list[str] = ["56101", "56102", "56103", "56301", "55100", "47110"]
    companies_house_location: str = "Swansea"
    companies_house_sweep_days: int = 180
    recheck_days: int = 90
    # Council licensing notices (Licensing Act 2003) and planning applications
    # (via PlanIt). See collectors/SOURCES-licensing-planning.md for what each
    # council publishes. Records outside the pilot outcodes are ignored.
    area_outcodes: list[str] = [f"SA{i}" for i in range(1, 21)] + [f"CF{i}" for i in range(31, 37)]
    licensing_councils: list[str] = ["swansea", "npt", "carmarthenshire", "bridgend", "powys"]
    licensing_cache_days: int = 1
    planning_areas: list[str] = ["Swansea", "Neath", "Carmarthenshire", "Bridgend", "Powys"]
    planning_recent_days: int = 120
    planning_cache_days: int = 1
    planning_search: str = ('"change of use" or restaurant or cafe or café or takeaway or "hot food" or bar '
                            'or "public house" or hotel or kitchen or extraction or refurbishment')


class CravenConfig(BaseModel):
    tenant: str = "craven"
    compliance: CravenComplianceConfig = CravenComplianceConfig()
    deliverability: CravenDeliverabilityConfig = CravenDeliverabilityConfig()
    discovery: CravenDiscoveryConfig = CravenDiscoveryConfig()
    enrichment: CravenEnrichmentConfig = CravenEnrichmentConfig()


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
