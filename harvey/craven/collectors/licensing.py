"""Council licensing notices — new premises licence applications and
variations under the Licensing Act 2003.

Every licensing authority must publish each application on its website for
the 28-day representation period. A new premises licence is applied for
weeks before a venue opens (or before a new operator starts): the earliest
"why today" we have. What each council publishes, and in what shape, is in
SOURCES-licensing-planning.md beside this file; the parsers here follow it
line by line.

Shape: one polite fetch of each council's list page per run, the linked
notice documents cached by URL under data/licensing/<council>/, parsed to
Notice records, filtered to the pilot outcodes, then matched to the venues
we own by full postcode + name (the FHRS rule) or created as new companies
(source discover:licensing, external_id lic:<council>:<key>). A notice is
recorded once per company (craven_enrichment source 'licensing:<key>').

Swansea's site sits behind a Cloudflare challenge that blocks curl and
similar clients; Python httpx with our user agent got through on 21 Oct
2026. If that stops, its list page and documents are read from a drop
folder (data/licensing/swansea/inbox/index.html plus the files).
"""

import hashlib
import html as html_lib
import io
import logging
import re
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import httpx

from harvey.craven.collectors.common import (best_poi_by_name, find_postcode, mark_enrichment, normalise_postcode,
                                             outcode, parse_uk_date, pois_by_postcodes, slugify)
from harvey.paths import PROJECT_ROOT

logger = logging.getLogger("harvey.craven.licensing")

SOURCE = "licensing"
CACHE_DIR = PROJECT_ROOT / "data" / "licensing"
USER_AGENT = "craven-outreach/0.1 (licensing notices; contact: alexcravendesign@gmail.com)"
MATCH_FLOOR = 0.85

PAGES = {
    "swansea": "https://www.swansea.gov.uk/licencingnoticeofnewpremises",
    "npt": "https://www.npt.gov.uk/business/licensing/alcohol-and-entertainment/licensing-notices/",
    "carmarthenshire": ("https://www.carmarthenshire.gov.wales/business/licensing-permits/alcohol-and-entertainment/"
                        "comment-on-a-licence-application/"),
    "bridgend": "https://www.bridgend.gov.uk/business/licensing/licensing-registers-and-notices/",
    "powys": "https://en.powys.gov.uk/article/5201/Current-premises--club-licence-applications",
}

KINDS = ("grant", "variation", "minor_variation", "review", "club")


@dataclass
class Notice:
    council: str
    key: str                     # stable within the council: media id or slug
    kind: str                    # one of KINDS
    premises: str = ""
    address: str = ""
    postcode: str = ""
    applicant: str = ""
    activities: list[str] = field(default_factory=list)
    closing_date: str = ""       # ISO
    url: str = ""                # the notice document or page: the evidence

    @property
    def external_id(self) -> str:
        return f"lic:{self.council}:{self.key}"


# ── Fetching (polite, cached) ──

class Fetcher:
    def __init__(self, cache_days: int = 1, transport=None, cache_dir: Path | None = None, sleeper=time.sleep):
        self.cache_days = cache_days
        self.transport = transport
        self.cache_dir = cache_dir or CACHE_DIR
        self.sleeper = sleeper
        self._calls = 0

    def _path(self, council: str, url: str, suffix: str) -> Path:
        d = self.cache_dir / council
        d.mkdir(parents=True, exist_ok=True)
        return d / (hashlib.sha1(url.encode()).hexdigest()[:16] + suffix)

    async def get(self, council: str, url: str, suffix: str = ".bin") -> bytes:
        path = self._path(council, url, suffix)
        if path.exists() and time.time() - path.stat().st_mtime < self.cache_days * 86400:
            return path.read_bytes()
        if self._calls:
            self.sleeper(1.0)               # one request a second, at most
        self._calls += 1
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), transport=self.transport,
                                     headers={"user-agent": USER_AGENT}, follow_redirects=True) as c:
            r = await c.get(url)
            r.raise_for_status()
            data = r.content
        path.write_bytes(data)
        return data


def extract_text(data: bytes, name: str) -> str:
    """Plain text from a notice document: DOCX (word/document.xml) or PDF (pypdf)."""
    lower = name.lower().split("?")[0]
    if lower.endswith(".docx") or data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                xml = z.read("word/document.xml").decode("utf-8", "ignore")
        except (zipfile.BadZipFile, KeyError):
            return ""
        paras = []
        for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
            t = "".join(re.findall(r"<w:t(?:\s[^>]*?)?>(.*?)</w:t>", p, re.S))   # <w:t>, never <w:top .../>
            if t.strip():
                paras.append(html_lib.unescape(t))
        return "\n".join(paras)
    if lower.endswith(".pdf") or data[:4] == b"%PDF":
        try:
            import pypdf
        except ImportError:
            logger.warning("pypdf is not installed; PDF notices are skipped (pip install pypdf)")
            return ""
        try:
            reader = pypdf.PdfReader(io.BytesIO(data))
            return "\n".join((pg.extract_text(extraction_mode="layout") or "") for pg in reader.pages)
        except Exception as e:                      # a broken upload must not stop the run
            logger.warning(f"could not read PDF {name}: {e}")
            return ""
    if lower.endswith(".doc"):
        logger.info(f"legacy .doc notice skipped (binary Word): {name}")
    return ""


def _links(html: str, base: str, pattern: str = r"\.(?:pdf|docx?)(?:\?[^\"']*)?$") -> list[tuple[str, str]]:
    """(absolute url, link text) for every anchor whose href matches."""
    out = []
    for m in re.finditer(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", html, re.S | re.I):
        href = html_lib.unescape(m.group(1))
        if not re.search(pattern, href, re.I):
            continue
        text = html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        title = re.search(r"title=\"([^\"]+)\"", m.group(0))
        if title and len(title.group(1)) > len(text):
            text = html_lib.unescape(title.group(1))
        out.append((urljoin(base, href), text))
    return out


def _media_id(url: str) -> str:
    m = re.search(r"/media/([A-Za-z0-9]+)/", url)
    return m.group(1) if m else slugify(url.rsplit("/", 1)[-1])


def _kind_from_title(text: str) -> str:
    t = (text or "").lower()
    if "review" in t:
        return "review"
    if "minor variation" in t:
        return "minor_variation"
    if "club premises" in t:
        return "club"
    if "vary" in t or "variation" in t:
        return "variation"
    return "grant"


def _activities(block: str) -> list[str]:
    acts = []
    for line in block.splitlines():
        line = line.strip(" -:–")
        if not line or line.lower().startswith(("a record of", "any ", "it is an offence", "if you wish")):
            if acts:
                break
            continue
        acts.append(re.sub(r"\s+", " ", line))
    return acts


# ── Swansea and Neath Port Talbot: list page + one document per application ──

def parse_notice_list(html: str, base: str) -> list[tuple[str, str]]:
    """The NPT 'Licensing notices' page: (document url, link text). Swansea's
    page has the same words but lists each document as an item whose title
    (an <h2 class="item__title">) names the premises; the anchor text there is
    just 'doc' or 'pdf', so the title is returned instead."""
    items = []
    for block in re.finditer(r"<div class=\"item item--media\">(.*?)</div>\s*</div>\s*</div>", html, re.S):
        title = re.search(r"class=\"item__title\">\s*(.*?)\s*</h2>", block.group(1), re.S)
        links = _links(block.group(1), base)
        if title and links:
            text = re.sub(r"\s*\((?:Word|PDF)\)\s*$", "", html_lib.unescape(re.sub(r"\s+", " ", title.group(1))), flags=re.I)
            items.append((links[0][0], text))
    return items or _links(html, base)


SWANSEA_ENGLISH = re.compile(r"^\s*NOTICE OF (?:APPLICATION FOR |VARIATION OF |A )", re.M)


def parse_swansea_notice(text: str) -> dict:
    """Swansea's bilingual notice (Welsh, then English). Only the English half
    is read: the Welsh heading has been seen to disagree with it. Grant =
    'NOTICE OF APPLICATION FOR A PREMISES LICENCE UNDER SECTION 17';
    variation = 'NOTICE OF VARIATION ... SECTION 34/84'; review = 'REVIEW ...
    SECTION 51'. Fields: PREMISES / APPLICANT lines (or 'Premises: ...' in a
    review), activity lines with hours, 'by <date>'."""
    t = re.sub(r"[ \t]+", " ", text or "")
    starts = [m.start() for m in SWANSEA_ENGLISH.finditer(t)]
    if not starts:
        return parse_npt_notice(t) if "in respect of" in t.lower() else {}
    eng = t[starts[-1]:]
    head = eng[:160].upper()
    kind = "review" if "REVIEW" in head else "variation" if "VARIATION" in head else "grant"
    m = re.search(r"^\s*PREMISES\s*:?\s*\n(.+)$", eng, re.M) or re.search(r"Premises:\s*(.+?)(?=\n\s*Licence reference|\n\s*APPLICANT|\n\s*The grounds)", eng, re.S)
    premises_line = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    m = re.search(r"^\s*APPLICANT\s*:?\s*\n(.+)$", eng, re.M)
    applicant = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    acts = []
    lines = [ln.strip() for ln in eng.splitlines()]
    for i, ln in enumerate(lines):
        if re.search(r"\d{4}\s*-\s*\d{4}|\d{1,2}[:.]\d{2}\s*-", ln):
            label = ""
            for back in range(i - 1, max(-1, i - 3), -1):
                prev = lines[back].strip(" :\u2013-")
                if prev and prev.upper() != "X" and not re.search(r"\d{4}", prev) and len(prev) > 2:
                    label = prev
                    break
            acts.append(f"{label}: {ln}" if label else ln)
    m = re.search(r"^\s*(?:To|For) (?:amend|add|remove|extend|vary|change)[^\n]*", eng, re.M | re.I)
    if m and kind != "grant":
        acts.append(m.group(0).strip())
    m = re.search(r"\b[Bb]y\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})", eng)
    if not m:
        m = re.search(r"and the\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})", eng)
    closing = parse_uk_date(m.group(1)) if m else ""
    postcode = find_postcode(premises_line)
    parts = [x.strip() for x in premises_line.split(",") if x.strip()]
    premises = parts[0] if parts and not parts[0][:1].isdigit() else ""
    address = ", ".join(parts[1:] if premises else parts)
    return {"kind": kind, "applicant": applicant, "premises": premises, "address": address,
            "postcode": postcode, "activities": acts, "closing_date": closing}


def parse_npt_notice(text: str) -> dict:
    """The NPT notice template (Swansea's is believed to be the same; unverified).
    'from:- <applicant>', 'in respect of:- <premises>, <address>, <postcode>',
    'activities ... are:- <lines>', 'by the <date>'."""
    t = re.sub(r"[ \t]+", " ", text or "")
    head = t[:200].upper()
    kind = _kind_from_title(head)
    m = re.search(r"from\s*:\s*-?\s*(.+?)(?:\n\s*\n|\n\s*in respect)", t, re.S | re.I)
    applicant = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    m = re.search(r"in respect of\s*:\s*-?\s*(.+?)(?:\n\s*\n|\n\s*The licensable)", t, re.S | re.I)
    premises_line = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
    m = re.search(r"activities[^\n]*?(?:are|be)\s*:\s*-?\s*\n?(.+?)(?:\n\s*A record of|\n\s*Any |\Z)", t, re.S | re.I)
    acts = _activities(m.group(1)) if m else []
    m = re.search(r"by the\s+(\d{1,2}/\d{1,2}/\d{4})", t)
    closing = parse_uk_date(m.group(1)) if m else ""
    postcode = find_postcode(premises_line)
    parts = [p.strip() for p in premises_line.split(",") if p.strip()]
    premises = parts[0] if parts and not parts[0][:1].isdigit() else ""
    address = ", ".join(parts[1:] if premises else parts)
    return {"kind": kind, "applicant": applicant, "premises": premises, "address": address,
            "postcode": postcode, "activities": acts, "closing_date": closing}


def notice_from_npt_doc(council: str, url: str, link_text: str, text: str) -> Notice:
    d = (parse_swansea_notice(text) if council == "swansea" else parse_npt_notice(text)) if text.strip() else {}
    # the link text names the premises when the document could not be read
    title = re.sub(r"\(.*?\)|\bApplication for .*? - ", "", link_text).strip(" -")
    title = re.sub(r"^[^,]*_", "", title)                      # Swansea: "RRA RETAIL Ltd_55-57 Eversley road, ..."
    premises = d.get("premises") or (title.split(",")[0].strip() if title and not title[:1].isdigit() else "")
    return Notice(council=council, key=_media_id(url), kind=d.get("kind") or _kind_from_title(link_text),
                  premises=premises, address=d.get("address") or ", ".join(title.split(",")[1:]).strip(),
                  postcode=d.get("postcode") or find_postcode(title), applicant=d.get("applicant", ""),
                  activities=d.get("activities", []), closing_date=d.get("closing_date", ""), url=url)


# ── Bridgend: list page with sections + one PDF per application ──

def parse_bridgend_list(html: str, base: str) -> list[tuple[str, str, str]]:
    """(url, title, section) for the Public notices / Review notices lists."""
    out = []
    for sec in re.finditer(r"<h2>([^<]+)</h2>(.*?)(?=<h[23]>|\Z)", html, re.S):   # the Documents <h3> is not a section
        section = sec.group(1).strip().lower()
        if "notice" not in section:
            continue
        for url, text in _links(sec.group(2), base, r"\.pdf(?:\?.*)?$"):
            out.append((url, text, section))
    return out


def parse_bridgend_notice(text: str) -> dict:
    t = re.sub(r"[ \t]+", " ", text or "")
    kind = _kind_from_title(t[:600])
    m = re.search(r"Name of Applicant:\s*(.+)", t)
    applicant = m.group(1).strip() if m else ""
    m = re.search(r"address of Premises:\s*\n(.+?)(?:\n\s*\n\s*Statement|\n\s*Statement)", t, re.S | re.I)
    lines = [ln.strip() for ln in (m.group(1) if m else "").splitlines() if ln.strip()]
    premises = lines[0] if lines and not lines[0][:1].isdigit() else ""
    rest = lines[1:] if premises else lines
    postcode = find_postcode(" ".join(lines))
    address = ", ".join(ln for ln in rest if normalise_postcode(ln) != postcode)
    m = re.search(r"relevant times:\s*\n(.+?)(?:\n\s*The Licensing Register|\n\s*Any representations)", t, re.S | re.I)
    acts = [re.sub(r"^\d+\.\s*", "", a) for a in _activities(m.group(1))] if m else []
    m = re.search(r"by\s+(\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+\s+\d{4})", t)
    closing = parse_uk_date(m.group(1)) if m else ""
    return {"kind": kind, "applicant": applicant, "premises": premises, "address": address,
            "postcode": postcode, "activities": acts, "closing_date": closing}


def notice_from_bridgend_doc(url: str, title: str, section: str, text: str) -> Notice:
    d = parse_bridgend_notice(text) if text.strip() else {}
    kind = "review" if "review" in section else (d.get("kind") or _kind_from_title(title))
    parts = [p.strip() for p in re.sub(r"^.*?–\s*", "", title).split(",") if p.strip()]
    return Notice(council="bridgend", key=_media_id(url), kind=kind,
                  premises=d.get("premises") or (parts[0] if parts and not parts[0][:1].isdigit() else ""),
                  address=d.get("address") or ", ".join(parts[1:]), postcode=d.get("postcode") or find_postcode(title),
                  applicant=d.get("applicant", ""), activities=d.get("activities", []),
                  closing_date=d.get("closing_date", ""), url=url)


# ── Carmarthenshire: one bilingual PDF register, linked from a fixed page ──

def parse_carmarthenshire_register_link(html: str, base: str) -> str:
    for url, text in _links(html, base, r"public-register[^\"']*\.pdf$"):
        if "register of current applications" in text.lower():
            return url
    links = _links(html, base, r"public-register[^\"']*\.pdf$")
    return links[0][0] if links else ""


CARMS_SECTION = re.compile(r"^\s*Cais (?:am|i) .*?/\s*(.+?)\s*$", re.M)
CARMS_ENTRY = re.compile(r"^\s*(\d{2}/\d{2}/\d{4})\s+(.+?)\s*$", re.M)
STREET_WORDS = ("road", "street", "lane", "avenue", "close", "crescent", "square", "place", "terrace",
                "way", "drive", "heol", "stryd", "ffordd", "rd", "st", "industrial", "estate")


def split_name_address(line: str) -> tuple[str, str]:
    """'King Kebab 34 Murray Street Llanelli ... SA15 1DJ' -> ('King Kebab', '34 Murray Street ...').
    The name ends before the first house number or before the word that
    precedes a street word."""
    words = line.split()
    for i, w in enumerate(words):
        if i and re.match(r"^\d", w):
            return " ".join(words[:i]), " ".join(words[i:])
    for i, w in enumerate(words):
        if i >= 2 and w.lower().strip(",") in STREET_WORDS:
            return " ".join(words[:i - 1]), " ".join(words[i - 1:])
    return line, ""


def parse_carmarthenshire_register(text: str) -> list[dict]:
    """Entries of the 'Register of Applications' PDF (pypdf layout text)."""
    entries = []
    section_kind = "grant"
    lines = (text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        sm = CARMS_SECTION.match(line)
        if sm:
            section_kind = _kind_from_title(sm.group(1))
            i += 1
            continue
        em = CARMS_ENTRY.match(line)
        if not em or "Report produced" in line:
            i += 1
            continue
        date_text, premises_line = em.groups()
        premises_line = re.sub(r"\s{2,}", " ", premises_line)
        j = i + 1
        applicant = ""
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j < len(lines) and not re.match(r"\s*(Cais|Application)", lines[j]) and not CARMS_ENTRY.match(lines[j]):
            applicant = re.sub(r"\s{2,}", " ", lines[j]).strip()
            j += 1
        block = []
        while j < len(lines) and not CARMS_ENTRY.match(lines[j]) and not CARMS_SECTION.match(lines[j]) \
                and "Report produced" not in lines[j]:
            block.append(lines[j])
            j += 1
        english = "\n".join(block)
        m = re.search(r"Application (?:for|to):\s*\n?(.+)", english, re.S)
        acts = [re.sub(r"\s+", " ", ln).strip() for ln in (m.group(1) if m else "").splitlines() if ln.strip()]
        postcode = find_postcode(premises_line)
        premises, address = split_name_address(re.sub(r"\s*" + re.escape(postcode) + r"\s*$", "", premises_line)
                                               if postcode else premises_line)
        entries.append({"kind": section_kind, "closing_date": parse_uk_date(date_text), "premises": premises.strip(),
                        "address": address.strip(), "postcode": postcode, "applicant": applicant, "activities": acts})
        i = j
    return entries


def notices_from_carmarthenshire(entries: list[dict], url: str) -> list[Notice]:
    out = []
    for e in entries:
        key = slugify(f"{e['premises']}-{e['postcode']}-{e['closing_date']}")
        out.append(Notice(council="carmarthenshire", key=key, kind=e["kind"], premises=e["premises"],
                          address=e["address"], postcode=e["postcode"], applicant=e["applicant"],
                          activities=e["activities"], closing_date=e["closing_date"], url=url))
    return out


# ── Powys: structured HTML ──

def parse_powys(html: str, url: str) -> list[Notice]:
    out = []
    for block in re.finditer(r"<h4[^>]*>(.*?)</h4>(.*?)Closing date for representations:\s*([\d/]+)", html, re.S | re.I):
        title = html_lib.unescape(re.sub(r"<[^>]+>", "", block.group(1))).strip()
        body = block.group(2)
        fields = {}
        for dt, dd in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", body, re.S):
            key = re.sub(r"<[^>]+>", "", dt).strip(": ").lower()
            if "<li>" in dd:
                val = [html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", li))).strip(", ")
                       for li in re.findall(r"<li>(.*?)</li>", dd, re.S)]
            else:
                val = html_lib.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", dd))).strip(" ,")
            fields[key] = val
        premises_line = fields.get("premises") or ""
        parts = [p.strip() for p in premises_line.split(",") if p.strip()]
        premises = parts[0] if parts and not parts[0][:1].isdigit() else ""
        applicant = fields.get("applicant") or ""
        kind = _kind_from_title(title)
        other = fields.get("other variations") or ""
        if kind == "variation" and ("police" in applicant.lower() or "review" in other.lower()):
            kind = "review"
        acts = list(fields.get("activities") or [])
        if other and kind != "review":
            acts.append(other)
        closing = parse_uk_date(block.group(3))
        out.append(Notice(council="powys", key=slugify(f"{premises}-{find_postcode(premises_line)}-{closing}"),
                          kind=kind, premises=premises, address=", ".join(parts[1:] if premises else parts),
                          postcode=find_postcode(premises_line), applicant=applicant, activities=acts,
                          closing_date=closing, url=url))
    return out


# ── Collecting per council ──

async def collect_council(council: str, fetcher: Fetcher, inbox: Path | None = None) -> list[Notice]:
    """Every current notice a council publishes. Fetch failures are logged
    and yield nothing; the run goes on to the next council."""
    page_url = PAGES[council]
    inbox = inbox if inbox is not None else fetcher.cache_dir / council / "inbox"

    async def page_and_docs(load_doc):
        html = ""
        if (inbox / "index.html").exists():
            html = (inbox / "index.html").read_text(encoding="utf-8", errors="ignore")
            logger.info(f"{council}: list page read from drop folder {inbox}")
        else:
            try:
                html = (await fetcher.get(council, page_url, ".html")).decode("utf-8", "ignore")
            except httpx.HTTPError as e:
                logger.warning(f"{council}: list page not fetched ({e}); nothing recorded"
                               + (f". Drop the page and its files in {inbox}" if council == "swansea" else ""))
                return []
        return await load_doc(html)

    async def doc_text(url: str) -> str:
        name = url.rsplit("/", 1)[-1].split("?")[0]
        local = inbox / name
        if local.exists():
            return extract_text(local.read_bytes(), name)
        try:
            return extract_text(await fetcher.get(council, url, "." + name.rsplit(".", 1)[-1].lower()), name)
        except httpx.HTTPError as e:
            logger.warning(f"{council}: notice {name} not fetched ({e}); using the link text only")
            return ""

    if council in ("swansea", "npt"):
        async def load(html):
            return [notice_from_npt_doc(council, url, text, await doc_text(url))
                    for url, text in parse_notice_list(html, page_url)]
        return await page_and_docs(load)
    if council == "bridgend":
        async def load(html):
            return [notice_from_bridgend_doc(url, title, section, await doc_text(url))
                    for url, title, section in parse_bridgend_list(html, page_url)]
        return await page_and_docs(load)
    if council == "carmarthenshire":
        async def load(html):
            url = parse_carmarthenshire_register_link(html, page_url)
            if not url:
                logger.warning("carmarthenshire: no 'register of current applications' link on the page")
                return []
            return notices_from_carmarthenshire(parse_carmarthenshire_register(await doc_text(url)), url)
        return await page_and_docs(load)
    if council == "powys":
        async def load(html):
            return parse_powys(html, page_url)
        return await page_and_docs(load)
    raise ValueError(f"unknown council {council!r}")


# ── Recording ──

def observation_for(n: Notice, company_id: str, confidence: float, run_id: str) -> dict | None:
    base = {"company_id": company_id, "collector": SOURCE, "confidence": confidence, "evidence_url": n.url,
            "run_id": run_id, "value_num": None, "value_text": ""}
    closing = f"closing date for representations {n.closing_date}" if n.closing_date else "current notice"
    acts = "; ".join(n.activities)[:160]
    if n.kind == "grant":
        return {**base, "signal_code": "NEW_PREMISES_LICENCE_APPLICATION", "value_num": 1.0,
                "value_text": f"new premises licence ({closing}" + (f"; {acts}" if acts else "") + ")"}
    if n.kind in ("variation", "minor_variation"):
        what = acts or "variation of the premises licence"
        return {**base, "signal_code": "LICENCE_VARIATION",
                "value_text": f"{what} ({'minor variation' if n.kind == 'minor_variation' else 'variation'}; {closing})"}
    return None                      # reviews and club certificates are not angles


async def record_notices(state, supabase, notices: list[Notice], outcodes: set[str], run_id: str,
                         confirmed: set[str]) -> dict:
    """Match each notice to a venue we own (full postcode + name) or create
    a company for it; record the signal once per company."""
    from harvey.models.company import Company
    kept = [n for n in notices if n.kind in ("grant", "variation", "minor_variation")
            and (not outcodes or outcode(n.postcode) in outcodes)]
    pois = await pois_by_postcodes(supabase, [n.postcode for n in kept if n.postcode])
    matched = created = skipped = obs_total = 0
    for n in kept:
        poi, conf = best_poi_by_name(n.premises, pois.get(normalise_postcode(n.postcode), []), MATCH_FLOOR)
        company_id = ""
        if poi is not None:
            company = await state.get_company_by_external_id(f"overture:{poi['id']}")
            if company is not None:
                company_id = company.id
        is_match = bool(company_id)
        if is_match:
            confidence = conf
        else:
            existing = await state.get_company_by_external_id(n.external_id)
            if existing is not None:
                company_id = existing.id
            else:
                company_id = await state.add_company(Company(
                    name=n.premises or n.address or n.external_id, industry="licence_applicant",
                    location=outcode(n.postcode), source="discover:licensing", source_url=n.url,
                    external_id=n.external_id, notes=f"{n.kind}; applicant {n.applicant}"[:200]))
                created += 1
            confidence = 1.0
        marker = f"licensing:{n.external_id}"
        async with state._connect() as db:
            async with db.execute("SELECT 1 FROM craven_enrichment WHERE company_id = ? AND source = ?",
                                  (company_id, marker)) as cur:
                if await cur.fetchone():
                    skipped += 1
                    continue
        row = observation_for(n, company_id, confidence, run_id)
        if row and row["signal_code"] in confirmed:
            written = await state.add_observations([row], run_id=run_id)
            obs_total += written
            if written:
                # the marker only once the observation exists: a notice seen before its
                # signal was confirmed is recorded on the first run after confirmation
                await mark_enrichment(state, company_id, marker, True, f"{n.kind} {n.closing_date} ({confidence:.2f})")
        if is_match:
            matched += 1
    return {"notices": len(notices), "in_area": len(kept), "matched": matched, "created": created,
            "already_recorded": skipped, "observations": obs_total}


async def enrich_licensing(state, settings, supabase, councils: list[str] | None = None,
                           fetcher: Fetcher | None = None, inboxes: dict[str, Path] | None = None) -> dict:
    """Read every council's current notices and record them. Returns counts per council."""
    cfg = settings.enrichment
    councils = councils or cfg.licensing_councils
    fetcher = fetcher or Fetcher(cfg.licensing_cache_days)
    confirmed = await state.confirmed_signal_codes()
    outcodes = {o.upper() for o in cfg.area_outcodes}
    run_id = await state.start_run("licensing", provider="council_notices", params={"councils": councils})
    out: dict = {}
    total = 0
    try:
        for council in councils:
            notices = await collect_council(council, fetcher, (inboxes or {}).get(council))
            out[council] = await record_notices(state, supabase, notices, outcodes, run_id, confirmed)
            total += out[council]["observations"]
        await state.finish_run(run_id, status="completed", records=total, cost_usd=0.0)
    except Exception as e:
        await state.finish_run(run_id, status="failed", records=total, cost_usd=0.0, error=str(e))
        raise
    logger.info(f"licensing: {out}")
    return out
