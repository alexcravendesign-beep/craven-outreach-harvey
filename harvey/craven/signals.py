"""The Craven signal catalogue: what makes a UK hospitality venue worth a
refrigeration conversation, and the honest email angle each fact enables.

Same dict format as harvey.signals.SIGNAL_CATALOG. ``category`` must be
one Harvey's dashboard groups (discovery | profile | people | verification)
and ``cost_note`` starts with "free" so ``harvey signals --confirm free``
sweeps them. Observations are rows; a code must be seeded before a
collector records it (the signal_codes trigger refuses unknown codes).
"""

from harvey import signals as harvey_signals

CRAVEN_SIGNAL_CATALOG: list[dict] = [
    # ── Discovery: the owned Overture layer ──
    {
        "code": "VENUE_CATEGORY",
        "label": "Venue type",
        "description": (
            "What the venue is (pub, cafe, restaurant, hotel, takeaway, butcher, "
            "farm shop, convenience, deli, bakery). Sets the equipment class: a pub "
            "runs bottle coolers and undercounters, a cafe a cake display, a farm "
            "shop a serve-over. The angle is the kit that venue type lives on."
        ),
        "category": "discovery", "value_type": "text", "collector": "discover",
        "cost_note": "free (owned Overture data)", "confidence_floor": 0.0,
    },
    {
        "code": "ROLE_MAILBOX",
        "label": "Shared business mailbox",
        "description": (
            "A published role address (info@, bookings@, enquiries@). The contactable "
            "fact that keeps cold email on the corporate-subscriber route under UK "
            "PECR. Not an angle; a precondition."
        ),
        "category": "verification", "value_type": "text", "collector": "discover",
        "cost_note": "free (owned Overture data)", "confidence_floor": 0.0,
    },
    {
        "code": "INDEPENDENT_VENUE",
        "label": "Independent, not a chain",
        "description": (
            "The website is not shared with more than two other venues. The "
            "decision-maker is on site; chains buy centrally and are excluded by cohort."
        ),
        "category": "discovery", "value_type": "bool", "collector": "discover",
        "cost_note": "free (owned Overture data)", "confidence_floor": 0.0,
    },

    # ── Profile: the Food Hygiene Rating Scheme (open data, no key) ──
    {
        "code": "FHRS_RATING",
        "label": "Food hygiene rating",
        "description": (
            "Their FHRS rating, 0-5. A 5 is a venue that keeps standards, worth "
            "keeping that way; a 3 or below usually has an equipment story behind it."
        ),
        "category": "profile", "value_type": "num", "collector": "fhrs",
        "cost_note": "free (FHRS open data API)", "confidence_floor": 0.0,
    },
    {
        "code": "FHRS_HYGIENE_SCORE",
        "label": "FHRS hygiene sub-score",
        "description": "The inspector's hygienic food handling score (0 best, 25 worst). Temperature control sits here.",
        "category": "profile", "value_type": "num", "collector": "fhrs",
        "cost_note": "free (FHRS open data API)", "confidence_floor": 0.0,
    },
    {
        "code": "FHRS_STRUCTURAL_SCORE",
        "label": "FHRS structural sub-score",
        "description": (
            "Cleanliness and condition of the building and equipment (0 best, 25 worst). "
            "A poor structural score is most often chilled storage in poor condition: "
            "the plainest refrigeration angle there is."
        ),
        "category": "profile", "value_type": "num", "collector": "fhrs",
        "cost_note": "free (FHRS open data API)", "confidence_floor": 0.0,
    },
    {
        "code": "FHRS_MANAGEMENT_SCORE",
        "label": "FHRS confidence-in-management sub-score",
        "description": "How the inspector rated their systems and records (0 best, 30 worst). Temperature logging is part of it.",
        "category": "profile", "value_type": "num", "collector": "fhrs",
        "cost_note": "free (FHRS open data API)", "confidence_floor": 0.0,
    },
    {
        "code": "FHRS_BUSINESS_TYPE",
        "label": "FHRS business type",
        "description": "The Food Standards Agency's own class (restaurant/cafe/canteen, pub/bar, takeaway, hotel, retailer, mobile caterer). Finer than the map category.",
        "category": "profile", "value_type": "text", "collector": "fhrs",
        "cost_note": "free (FHRS open data API)", "confidence_floor": 0.0,
    },
    {
        "code": "FHRS_INSPECTION_AGE_DAYS",
        "label": "Days since last inspection",
        "description": (
            "How long since the rating was given. Past about 18 months an inspection "
            "is likely due: a natural moment to talk about temperature records and "
            "equipment condition."
        ),
        "category": "profile", "value_type": "num", "collector": "fhrs",
        "cost_note": "free (FHRS open data API)", "confidence_floor": 0.0,
    },
    {
        "code": "FHRS_AWAITING_INSPECTION",
        "label": "Awaiting first inspection (off: ambiguous)",
        "description": (
            "Registered with the council but not yet rated. Means a NEW REGISTRATION, which "
            "is a new venue OR new hands at an old one: too ambiguous to write from, so it "
            "is rejected by default (Nick, 21 Oct 2026) and never appears in copy."
        ),
        "category": "profile", "value_type": "bool", "collector": "fhrs",
        "cost_note": "free (FHRS open data API)", "confidence_floor": 0.0,
    },

    # ── Verification + profile: Companies House (free API key) ──
    {
        "code": "CH_INCORPORATED",
        "label": "Registered company",
        "description": (
            "Matched to a company at Companies House (value = company number). Under "
            "UK PECR a limited company is a corporate subscriber; a sole trader is not. "
            "Used by the gate when corporate_subscribers_only is on. Name+postcode "
            "matching is fuzzy, so a confidence floor applies."
        ),
        "category": "verification", "value_type": "text", "collector": "companies_house",
        "cost_note": "free (Companies House API key, 600 requests per 5 minutes)", "confidence_floor": 0.8,
    },
    {
        "code": "CH_NEW_INCORPORATION_DAYS",
        "label": "Days since incorporation",
        "description": (
            "A hospitality company (SIC 56101/56102/56103/56301/55100/47110) incorporated "
            "recently in the area: opening soon, buying kit now."
        ),
        "category": "profile", "value_type": "num", "collector": "companies_house",
        "cost_note": "free (Companies House API key)", "confidence_floor": 0.0,
    },
    {
        "code": "CH_OFFICER_CHANGE_DAYS",
        "label": "Days since an officer change",
        "description": "A new director or secretary: often a new owner or manager, and a refit or refresh follows.",
        "category": "profile", "value_type": "num", "collector": "companies_house",
        "cost_note": "free (Companies House API key)", "confidence_floor": 0.0,
    },

    # ── Profile: their own website ──
    {
        "code": "MENU_EQUIPMENT_CLASS",
        "label": "Equipment class from the menu",
        "description": (
            "Words on the site that name the kit: gelato or ice cream (display freezer), "
            "bottle bar (bottle coolers), deli counter or fresh fish (serve-over), cakes "
            "and patisserie (display chiller), real ale (cellar cooling), takeaway "
            "(undercounter and prep). The main copy angle: it names a Tefcold family "
            "and lets the email quote a price inline."
        ),
        "category": "profile", "value_type": "text", "collector": "profile",
        "cost_note": "free (homepage and menu HTML)", "confidence_floor": 0.0,
    },
    {
        "code": "ENERGY_CLAIM",
        "label": "Talks about energy or sustainability",
        "description": "The site makes an energy or green claim. R290 running-cost numbers land with these venues.",
        "category": "profile", "value_type": "bool", "collector": "profile",
        "cost_note": "free (homepage HTML)", "confidence_floor": 0.0,
    },
]

# The model-read dossier (harvey.craven.dossier) is a signal too.
from harvey.craven.dossier import DOSSIER_SIGNAL  # noqa: E402
CRAVEN_SIGNAL_CATALOG.append(DOSSIER_SIGNAL)

# Upstream signals Craven keeps (everything else in the US catalogue is
# rejected once in the database; a rejection persists across upgrades).
KEEP_UPSTREAM = ("NO_WEBSITE", "EMAIL_STATUS", "FOUND_IN_SERP")

CRAVEN_CODES = tuple(s["code"] for s in CRAVEN_SIGNAL_CATALOG)


def install_signals() -> int:
    """Extend Harvey's catalogue with Craven's (idempotent). Returns how many
    codes were added this call."""
    present = {s["code"] for s in harvey_signals.SIGNAL_CATALOG}
    added = 0
    for sig in CRAVEN_SIGNAL_CATALOG:
        if sig["code"] not in present:
            harvey_signals.SIGNAL_CATALOG.append(sig)
            added += 1
    return added


def upstream_codes_to_reject() -> list[str]:
    return [s["code"] for s in harvey_signals.SIGNAL_CATALOG
            if s["code"] not in CRAVEN_CODES and s["code"] not in KEEP_UPSTREAM]
