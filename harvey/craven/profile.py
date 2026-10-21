"""Craven's profiler: Harvey's free HTTP profiler plus what a refrigeration
seller reads on a venue's site — the menu.

MENU_EQUIPMENT_CLASS names the kit the menu implies (the main copy angle);
ENERGY_CLAIM notes an energy/sustainability claim. Upstream's own detectors
still run for whatever upstream signals are confirmed.
"""

import logging
import re

from harvey.collectors.profile import ProfileCollector, _text

logger = logging.getLogger("harvey.craven.profile")

MENU_PATHS = ("/menu", "/menus", "/food", "/drinks", "/drink", "/our-menu", "/food-menu", "/bar", "/cakes")

# equipment class -> (menu terms, weight). The class with the most hits wins;
# confidence rises with the number of distinct terms seen.
EQUIPMENT_TERMS: dict[str, tuple[str, ...]] = {
    "display_freezer": ("gelato", "ice cream", "ice-cream", "sorbet", "frozen yoghurt", "frozen yogurt"),
    "bottle_cooler": ("bottle bar", "craft beer", "craft cans", "by the bottle", "wine by the glass", "wine list",
                      "cocktail", "cocktails", "prosecco", "bottled beers"),
    "serve_over": ("deli counter", "deli", "delicatessen", "fresh fish", "fishmonger", "cured meats", "charcuterie",
                   "cheese counter", "butcher", "counter service"),
    "display_chiller": ("cakes", "cake", "patisserie", "pastries", "bakes", "cheesecake", "brownies", "afternoon tea",
                        "cream tea", "scones"),
    "cellar_cooling": ("real ale", "cask ale", "cask", "camra", "hand pull", "hand-pull", "on tap", "ales"),
    "prep_counter": ("takeaway", "take away", "fast food", "kebab", "pizza", "burgers", "fish and chips", "fish & chips",
                     "delivery", "collection", "order online"),
    "multideck": ("farm shop", "greengrocer", "local produce", "ready meals", "chilled", "convenience"),
}
ENERGY_TERMS = ("energy", "sustainab", "carbon", "eco-friendly", "eco friendly", "net zero", "renewable", "green energy")


def equipment_class(text: str) -> tuple[str, float, list[str]] | None:
    t = (text or "").lower()
    best, best_hits = None, []
    for klass, terms in EQUIPMENT_TERMS.items():
        hits = [term for term in terms if term in t]
        if len(hits) > len(best_hits):
            best, best_hits = klass, hits
    if not best or not best_hits:
        return None
    conf = min(1.0, 0.5 + 0.15 * len(best_hits))
    return best, round(conf, 2), best_hits


def energy_claim(text: str) -> bool:
    t = (text or "").lower()
    return any(term in t for term in ENERGY_TERMS)


class CravenProfileCollector(ProfileCollector):
    def __init__(self, state, confirmed=None, timeout: float = 12.0):
        super().__init__(state, confirmed, timeout)
        self._home_cache: dict[str, tuple[str, str]] = {}

    async def _improve_name(self, company: dict, domain: str):
        # Upstream renames a company after its site's title when the domain
        # doesn't back the name up. Our names come from the owned venue data
        # and a venue's site is often a booking or ordering platform, so the
        # rename is wrong here ("The White Lion" became "Sessions Serve").
        return None

    async def _resolve_site(self, domain: str) -> tuple[str, str]:
        # Memoised so our extra detectors reuse the homepage upstream fetched.
        if domain not in self._home_cache:
            self._home_cache[domain] = await super()._resolve_site(domain)
        return self._home_cache[domain]

    async def profile_company(self, company_id: str, domain: str) -> list[dict]:
        obs = await super().profile_company(company_id, domain)
        domain = domain.lower().replace("www.", "").strip("/")
        site, home = self._home_cache.get(domain) or ("", "")
        if not home:
            return obs
        pages = [(site, home)]
        if self.wants("MENU_EQUIPMENT_CLASS"):
            murl, mhtml = await self._first_present(site, MENU_PATHS)
            if mhtml:
                pages.append((murl, mhtml))
        text = "\n".join(_text(h) for _, h in pages)
        if self.wants("MENU_EQUIPMENT_CLASS"):
            found = equipment_class(text)
            if found:
                klass, conf, hits = found
                obs.append({"signal_code": "MENU_EQUIPMENT_CLASS", "company_id": company_id, "collector": "profile",
                            "value_num": None, "value_text": klass, "confidence": conf,
                            "evidence_url": pages[-1][0]})
                logger.info(f"profile: {domain} menu → {klass} ({', '.join(hits[:3])})")
        if self.wants("ENERGY_CLAIM"):
            obs.append({"signal_code": "ENERGY_CLAIM", "company_id": company_id, "collector": "profile",
                        "value_num": 1.0 if energy_claim(text) else 0.0, "value_text": "", "confidence": 1.0,
                        "evidence_url": site})
        self._home_cache.pop(domain, None)
        return obs
