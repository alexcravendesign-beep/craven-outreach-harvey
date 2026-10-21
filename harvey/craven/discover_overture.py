"""Discovery from the data we own: the Overture Maps pull of UK hospitality
venues (362,504 rows) in Supabase ``public.pois``.

Doctrine: harvest from what we own first; never re-pay an API for data we
hold; never Google Places (its terms forbid caching). A query is one ICP
term (mapped to Overture category patterns) in one outcode. Cost is zero.

What the provider does beyond returning venues:
- a social or delivery-platform "website" (Facebook, Just Eat...) is not a
  website: the venue stays, its domain and website are blanked;
- a page-builder host (wixsite.com, squarespace.com...) is a real site whose
  host is shared with thousands of others, so the venue keeps its website but
  gets no domain (identity by external id), and counts as independent;
- venues sharing a real domain with more than ``chain_domain_threshold``
  others in the pilot area are a chain: domain blanked so Harvey's unique
  domain key doesn't collapse them onto one company, INDEPENDENT_VENUE=0;
- an address on the suppression list (an existing customer) never becomes a
  company at all;
- a published role mailbox is attached as a ROLE_MAILBOX observation (a
  personal address is never recorded).
"""

import logging
import os
import re
from collections import Counter

from harvey.collectors.discover import Business, DiscoveryProvider, DiscoveryQuery, normalize_domain
from harvey.craven import compliance
from harvey.craven.supabase import SupabaseRest
from harvey.craven.suppression import SuppressionStore

logger = logging.getLogger("harvey.craven.discover")

POI_COLUMNS = "id,name,category,confidence,lat,lng,address,postcode,locality,phone,website,email"

# ICP term -> Overture category patterns (PostgREST `like` uses * as wildcard).
TERM_TO_CATEGORY: dict[str, list[str]] = {
    "pub": ["pub", "gastropub", "brewery", "irish_pub", "social_club", "inn"],
    "bar": ["bar", "cocktail_bar", "wine_bar", "beer_bar", "sports_bar", "tapas_bar", "lounge", "nightclub"],
    "cafe": ["cafe", "coffee_shop", "tea_room"],
    "restaurant": ["restaurant", "*_restaurant"],
    "hotel": ["hotel", "bed_and_breakfast", "guest_house", "hostel"],
    "fast_food": ["fast_food_restaurant", "fish_and_chips_restaurant", "takeout_restaurant", "chicken_restaurant"],
    "butcher": ["butcher_shop", "butcher"],
    "farm_shop": ["farm_shop", "farmers_market"],
    "convenience": ["convenience_store", "grocery_store"],
    "deli": ["deli", "delicatessen"],
    "bakery": ["bakery", "patisserie"],
}

SOCIAL_HOSTS = re.compile(
    r"(^|\.)(facebook\.com|fb\.com|instagram\.com|tripadvisor\.[a-z.]+|twitter\.com|x\.com|linkedin\.com|"
    r"yelp\.[a-z.]+|just-eat\.[a-z.]+|justeat\.[a-z.]+|deliveroo\.[a-z.]+|ubereats\.com|booking\.com|"
    r"google\.com|goo\.gl|business\.site|tiktok\.com|youtube\.com|opentable\.[a-z.]+|"
    r"servedup\.co|tablein\.com|resdiary\.com|designmynight\.com|thefork\.[a-z.]+|order\.online|"
    r"flipdish\.com|foodhub\.co\.uk|order-online\.[a-z.]+|hungrrr\.co\.uk|wix\.com/site)$")
PAGE_BUILDER_HOSTS = re.compile(
    r"(^|\.)(wixsite\.com|wix\.com|squarespace\.com|weebly\.com|godaddysites\.com|webador\.[a-z.]+|"
    r"site123\.me|strikingly\.com|carrd\.co|mystrikingly\.com|yolasite\.com|jimdosite\.com)$")


def website_kind(website: str) -> str:
    """'own' | 'social' | 'builder' | 'none'."""
    host = normalize_domain(website)
    if not host:
        return "none"
    if SOCIAL_HOSTS.search(host):
        return "social"
    if PAGE_BUILDER_HOSTS.search(host):
        return "builder"
    return "own"


def categories_for(term: str) -> list[str]:
    key = (term or "").strip().lower().replace(" ", "_")
    if key in TERM_TO_CATEGORY:
        return TERM_TO_CATEGORY[key]
    return [key] if key else []


def _outcode(postcode: str) -> str:
    return (postcode or "").strip().upper().split(" ")[0]


class OvertureSupabase(DiscoveryProvider):
    key = "overture_supabase"
    label = "Owned venue data (Overture Maps via Supabase)"
    kind = "owned"
    blurb = ("The 362,504 UK hospitality venues we already hold (Overture Maps "
             "2026-06-17): name, category, address, phone, website, published "
             "email. No API, no spend, no terms to trip over.")
    cost_note = "free (owned data)"
    free_tier = "unlimited"
    signup_url = "https://overturemaps.org"
    env_keys = ()
    caveat = ("Snapshot data: refresh by re-pulling the Overture release. "
              "Shops without a food/drink category are thin. Queries are by "
              "outcode (SA1, SA2...), not by town name.")

    def __init__(self, *, supabase=None, state_factory=None, tenant: str = "craven",
                 chain_domain_threshold: int = 2, area_outcodes: tuple[str, ...] = ()):
        self._supabase = supabase
        self._state_factory = state_factory
        self.tenant = tenant
        self.chain_domain_threshold = chain_domain_threshold
        self.area_outcodes = tuple(area_outcodes)
        self._area_rows: dict[str, list[dict]] = {}
        self._domain_counts: Counter | None = None
        self._suppression: SuppressionStore | None = None

    # ── wiring ──

    @property
    def supabase(self) -> SupabaseRest | None:
        if self._supabase is None:
            self._supabase = SupabaseRest.from_env()
        return self._supabase

    def configured(self, env: dict) -> bool:
        return self.supabase is not None

    def estimate(self, queries: list[DiscoveryQuery]) -> float:
        return 0.0

    async def _suppressed(self, email: str) -> bool:
        if not email:
            return False
        if self._suppression is None:
            if self._state_factory is None:
                from harvey.state import StateManager
                self._state_factory = StateManager
            self._suppression = SuppressionStore(self._state_factory(), self.tenant, None)
        try:
            return await self._suppression.is_suppressed(email)
        except Exception as e:  # no mirror table yet: nothing is suppressed, say so
            logger.debug(f"suppression check unavailable ({e}); treating as not suppressed")
            return False

    # ── data ──

    async def _rows_for_outcode(self, outcode: str) -> list[dict]:
        if outcode not in self._area_rows:
            rows = await self.supabase.select(
                "pois", {"select": POI_COLUMNS, "postcode": f"like.{outcode} *", "order": "id.asc"})
            self._area_rows[outcode] = rows
        return self._area_rows[outcode]

    async def _domain_count(self, domain: str, outcode: str) -> int:
        """How many venues across the pilot area share this real domain."""
        if self._domain_counts is None:
            counts: Counter = Counter()
            for oc in (self.area_outcodes or (outcode,)):
                for r in await self._rows_for_outcode(oc):
                    if website_kind(r.get("website") or "") == "own":
                        counts[normalize_domain(r["website"])] += 1
            self._domain_counts = counts
        return self._domain_counts.get(domain, 0)

    @staticmethod
    def _matches(category: str, patterns: list[str]) -> bool:
        c = (category or "").lower()
        for p in patterns:
            if p.startswith("*"):
                if c.endswith(p[1:]):
                    return True
            elif c == p:
                return True
        return False

    # ── the provider ──

    async def fetch(self, client, env, query: DiscoveryQuery) -> list[Business]:
        outcode = _outcode(query.location)
        patterns = categories_for(query.term)
        if not outcode or not patterns:
            return []
        rows = [r for r in await self._rows_for_outcode(outcode) if self._matches(r.get("category"), patterns)]
        out: list[Business] = []
        for r in rows[: query.limit or 100]:
            email = (r.get("email") or "").strip().lower()
            if email and await self._suppressed(email):
                continue  # an existing customer never becomes a company
            website = (r.get("website") or "").strip()
            kind = website_kind(website)
            domain = normalize_domain(website) if kind == "own" else ""
            independent = 1.0
            if kind == "social":
                website = ""
            if domain:
                shared = await self._domain_count(domain, outcode)
                if shared > self.chain_domain_threshold:
                    independent = 0.0  # a chain: bought centrally, excluded by cohort
                if shared > 1:
                    # Harvey keys companies by domain, so two venues on one site
                    # would collapse into one company. Keep every venue distinct
                    # (identity by external id) whether it is a chain or a
                    # two-site family business.
                    domain = ""
            address = ", ".join(x for x in (r.get("address"), r.get("locality"), r.get("postcode")) if x)
            observations = [
                {"signal_code": "VENUE_CATEGORY", "value_text": r.get("category") or ""},
                {"signal_code": "INDEPENDENT_VENUE", "value_num": independent},
            ]
            if email:
                ok, why = compliance.is_business_mailbox(email, r.get("name") or "", r.get("website") or "")
                if ok:
                    observations.append({"signal_code": "ROLE_MAILBOX", "value_text": email,
                                         "evidence_url": website or "", "confidence": 1.0 if why == "role local part" else 0.9})
            out.append(Business(
                name=(r.get("name") or "").strip(),
                domain=domain,
                website=website,
                phone=(r.get("phone") or "").strip(),
                address=address,
                location=outcode,
                category=r.get("category") or "",
                has_website=(kind in ("own", "builder")),
                external_id=f"overture:{r.get('id')}",
                source_url=website or "",
                provider=self.key,
                observations=observations,
            ))
        logger.info(f"overture: {query.term} in {outcode}: {len(out)} venue(s) ({len(rows)} matched, "
                    f"{len(rows) - len(out)} skipped as suppressed)")
        return out
