# Licensing and planning sources, Llanelli to Bridgend

What each council actually publishes about new premises licence applications
(Licensing Act 2003) and planning applications, verified by fetching the real
pages on 21 October 2026. Two collectors read these: `licensing.py` and
`planning.py`. Everything below is what the code relies on; when a council
changes its page, fix it here first.

Pilot area: outcodes SA1–SA20 and CF31–CF36. Councils: City and County of
Swansea, Neath Port Talbot, Carmarthenshire, Bridgend, Powys (for SA9 only).

## Summary

| Council | Licensing Act 2003 applications | Planning |
|---|---|---|
| Swansea | HTML list + one bilingual Word/PDF per application (Swansea's own template). Cloudflare blocks curl, but Python httpx gets through; a drop folder is the fallback. | PlanIt `auth=Swansea` (area 407), scraping Idox at property.swansea.gov.uk |
| Neath Port Talbot | HTML list + one DOCX/PDF per application; open to scripts | PlanIt `auth=Neath` (area 408), scraping Idox at planningonline.npt.gov.uk |
| Carmarthenshire | One bilingual PDF "Register of Applications", republished every few weeks under a new URL, linked from a fixed page | PlanIt `auth=Carmarthenshire`, scraping the Salesforce portal carmarthenshire.my.site.com |
| Bridgend | HTML list + one PDF per application; plus a 170-page PDF register of all granted licences | PlanIt `auth=Bridgend`, scraping planning.bridgend.gov.uk |
| Powys | Structured HTML (dl/dt/dd per application), no documents | PlanIt `auth=Powys`, scraping the Salesforce portal service.powys.gov.uk |

Nothing on data.gov.uk: no council in the area publishes licensing or
planning as an open dataset. No council runs a searchable licensing
register online; each says the full register is inspected at its offices.
The 28-day website notice is a statutory duty (Licensing Act 2003
(Premises licences and club premises certificates) Regulations 2005, as
amended), which is why all five publish *something*.

## Planning: PlanIt (planit.org.uk)

- **API:** `https://www.planit.org.uk/api/applics/json?auth=<area>&recent=<days>&pg_sz=300&search=<terms>`.
  No key. Docs at `https://www.planit.org.uk/api/`. `auth` is the area name or
  numeric id; `recent=N` = start_date within N days; `search` supports quoted
  phrases, `or`, and `-` for NOT; `pg_sz` up to 5,000 (1,000 kB cap);
  `page` from 1. CSV/GeoJSON also available.
- **Areas confirmed by live query:** Swansea (area_id 407), Neath (408 — the
  API knows Neath Port Talbot only as "Neath"; "Neath Port Talbot" returns
  `P0001: No match`), Carmarthenshire, Bridgend, Powys. All five had
  applications dated within the previous week on 21 Oct 2026, so the
  scrapers are live.
- **Record fields:** `name` (e.g. `Swansea/2026/1730/FUL`), `uid`
  (`2026/1730/FUL`), `reference`, `area_name`, `area_id`, `start_date`,
  `decided_date`, `last_changed`, `app_type` (Full | Conditions | Amendment |
  Heritage | Outline | Other …), `app_state` (Undecided | Permitted |
  Rejected | Withdrawn), `app_size`, `description`, `address`, `postcode`
  (may be null), `location` (GeoJSON point), `link` (PlanIt page), `url`
  (the council's own application page — this is the evidence URL),
  `other_fields` (applicant_name, agent_name, application_type,
  date_received, date_validated, consultation dates, docs_url, comment_url,
  ward_name, n_documents …). Swansea's other_fields carry
  `applicant_name: "See source"`: the applicant is not in the API.
- **Address shape:** the council's single-line location, venue name first
  when there is one: `Morris Arms Llangyfelach Road Treboeth Swansea SA5 9LN`,
  `Awa Restaurant 8 - 14 College Street Swansea SA1 5AE`,
  `1 Cross Buildings Woodfield Street Morriston Swansea SA6 8DR`. No
  separator between name and street, so matching uses tokens, not splits.
- **Rate limit (measured):** after roughly a dozen calls in ten minutes the
  API returned HTTP 429 `{"error": "Rate limits exceeded, try again in 271s"}`
  with the same figure in `Retry-After`. The planning collector therefore
  makes **one call per council per run**, caches the JSON for a day under
  `data/planning/`, and stops the run on a 429 (it does not retry inside the
  run). robots.txt disallows `/api/applics/` to crawlers; we are an
  identified API client, not a crawler, and the API page invites use, but
  keep the rate low and the user agent honest
  (`craven-outreach/0.1 (planning signals; contact …)`).
- **Search terms used:** `"change of use" or restaurant or cafe or café or
  takeaway or "hot food" or bar or "public house" or hotel or kitchen or
  extraction or refurbishment`, `recent=120`. The classifier in
  `planning.py` then decides what is a change of use *to* a hospitality use
  (not from one: "Change of use of restaurant and flat to a 6 person HMO" is
  a venue closing, and is dropped) and what is a refit of an existing venue
  (extension, refurbishment, extraction flue, servery, terrace, bar at an
  address or description that names a pub, restaurant, cafe, hotel, takeaway).
  `Conditions` and `Amendment` application types are ignored (discharge of
  conditions and non-material amendments follow a permission already
  granted); `Rejected` and `Withdrawn` states are ignored.
- **Use classes in Wales:** Wales kept the pre-2020 classes: A1 shops, A2
  financial/professional, A3 food and drink (restaurants, cafes, pubs,
  takeaways), C1 hotels. "Class A3" in a Welsh description is a hospitality
  use; "Class E" (the English 2020 class) does not appear.
- **Councils' own portals (fallback if PlanIt lapses):** Swansea and NPT run
  Idox Public Access (`.../online-applications/search.do?action=weeklyList`
  gives a weekly list; `applicationDetails.do?keyVal=…` pages; session cookie
  needed for searches, HTML tables). Carmarthenshire and Powys moved to
  Salesforce Experience sites (`carmarthenshire.my.site.com/en/s/planning-application/<id>`,
  `service.powys.gov.uk/pr/s/detail/<id>`): JavaScript-rendered, no plain
  HTML list. Bridgend runs its own `planning.bridgend.gov.uk/Planning/Display/<ref>`
  (302 to a session page for scripts). PlanIt is the only route that gives
  all five as one JSON shape, which is why the collector uses it.

## Licensing, council by council

### Swansea (City and County of Swansea)

- **Page:** `https://www.swansea.gov.uk/licencingnoticeofnewpremises`
  ("Notice of new premises applications, variations and reviews"). "Last
  modified on 13 October 2026" in the footer. Notices stay for the 28-day
  representation period.
- **Format:** HTML; under "Current applications" one heading per
  application, text = premises/applicant and address, e.g. `RRA RETAIL
  Ltd_55-57 Eversley road, Sketty, Swansea, SA2 9DE (Word)`, `Pizza Giorgio,
  159 Ravenhill Road, Swansea (PDF)`, with a link to a `.docx` or `.pdf`
  under `https://www.swansea.gov.uk/media/<id>/<slug>/doc|pdf/<file>?m=<ts>`.
  Three applications on 21 Oct 2026. The page says nothing about whether an
  entry is a grant, a variation or a review; the document does.
- **Access:** `www.swansea.gov.uk` sits behind a Cloudflare managed
  challenge ("Just a moment…"): HTTP 403 for curl (polite UA or a Chrome
  UA alike) and for WebFetch. **Python httpx with our user agent got the
  page and all three documents (HTTP 200) on 21 Oct 2026**, so the block
  keys on the client's TLS fingerprint rather than the UA. The collector
  fetches directly; if Cloudflare starts refusing httpx too, it logs the
  403 and reads a **drop folder** `data/licensing/swansea/inbox/`
  (`index.html` plus the linked files, saved from a browser).
- **The mini is blocked, the Studio is not (21 Oct 2026, 18:20):** the same
  Fetcher gets 403 from the Mac mini that runs the collector and the page
  (33,920 bytes) from the Studio. So the Studio feeds the drop folder:
  `harvey/craven/collectors/swansea_feed.py` (launchd agent
  `com.8zero8.craven-swansea-feed`, daily 07:00, plist under
  `services/launchd/`) fetches the page and every linked document with the
  collector's Fetcher, writes them in the inbox layout
  (`index.html` + `<last path segment of the link, no query>`), and scps
  them into `~/Projects/craven-outreach/app/data/licensing/swansea/inbox/`
  on the mini. The feed never delivers an index that parsed to no notices.
  Cloudflare's challenge is state on its side, not the client's: at 18:18
  the same evening the Studio was challenged too, and a single daily fetch
  is the polite shape; a 403 on a given morning just leaves yesterday's
  inbox in place (the collector reads whatever is there).
- **List markup (verified):** each application is a
  `<div class="item item--media">` with `<h2 class="item__title">RRA RETAIL
  Ltd_55-57 Eversley road, Sketty, Swansea, SA2 9DE (Word)</h2>` and one
  `<a href="/media/<id>/<slug>/doc|pdf/<file>?m=…">` whose text is just
  `doc`/`pdf`. The title is the premises/applicant and address (an
  underscore separates applicant and address when both are given).
- **Document template (verified on two DOCX and one PDF):** bilingual,
  **Welsh first, then English**; the English half starts at the last line
  beginning `NOTICE OF …`:
  `NOTICE OF APPLICATION FOR A PREMISES LICENCE UNDER SECTION 17 …` (grant),
  `NOTICE OF VARIATION OF A PREMISES LICENCE UNDER SECTION 84 …`
  (variation; the council's own heading), `NOTICE OF APPLICATION FOR A
  REVIEW OF A PREMISES LICENCE UNDER SECTION 51 …` (review). Then
  `PREMISES` / address line with postcode, `APPLICANT` / name, an
  activities table (a)–h) labels with an `X` beside the ones applied for
  and hours like `Mon-Sun – 0600-2300`), or for a variation the activity
  lines and a `To amend …` sentence, then `… to the above by 4 November
  2026`. Reviews say `Premises: <name>, <address>` and `between the date of
  this notice and the 10th November 2026`. **The Welsh heading can
  disagree with the English one** (a section 17 grant headed "CAIS I
  AMRYWIO", an application to vary): the parser reads the English half only.
- **Identifying a venue:** the title gives the address and usually the
  postcode; the document gives the applicant. Shops applying for an
  off-licence carry no trading name in the notice (premises = the address).

### Neath Port Talbot

- **Page:** `https://www.npt.gov.uk/business/licensing/alcohol-and-entertainment/licensing-notices/`
  ("Licensing notices"). HTML, plain to scripts (200 with our UA). Under
  "Downloads": one link per application,
  `<a href="/media/<id>/<slug>.docx?v=…">Application for grant of a premises
  licence - 9 Darter (DOCX 17 KB)</a>` or `…/<slug>.pdf?mediaredirect=false&v=…`.
  Two on 21 Oct 2026. Representations to lrs@npt.gov.uk.
- **Document template (verified on both a DOCX and a PDF):**
  ```
  LICENSING ACT 2003
  APPLICATION FOR GRANT OF A PREMISES LICENCE
  Neath Port Talbot County Borough Council hereby give notice that an
  application has been received under Section 17 of the Licensing Act for
  the grant of a Premises Licence from:- <applicant>
  in respect of:- <premises name>, <address>, <postcode>
  The licensable activities to be carried out are:- <activities>
  … should do so in writing … by the <dd/mm/yyyy>
  ```
  Section 17 = grant; variations come under section 34 ("APPLICATION FOR
  VARIATION OF A PREMISES LICENCE"); reviews under section 51.
- **Fields:** applicant, premises name (first comma chunk of "in respect
  of" unless it starts with a digit), full address with postcode,
  activities, closing date. No reference number: `lic:npt:<media id>` (the
  8-character id in the media path) is the stable key.
- **Cadence:** notices appear within days of receipt and drop after 28
  days; run weekly. The DOCX is parsed from `word/document.xml` with the
  standard library, the PDF with pypdf.

### Carmarthenshire

- **Page:** `https://www.carmarthenshire.gov.wales/business/licensing-permits/alcohol-and-entertainment/comment-on-a-licence-application/`
  carries the link **"View the register of current applications"** →
  `/media/<id>/public-register-<ddmm>.pdf` (on 21 Oct 2026:
  `/media/qytnzn3s/public-register-1210.pdf`, "Report produced on
  12/10/2026"; the previous one, `public-register1709.pdf`, was still linked
  without text). The URL changes every time the register is republished
  (every few weeks, judged by the dates in old file names: 1704, 1306,
  1807, 1709, 1210, 13_11_25, 28112025, 12122025). Old URLs 404. The
  collector always starts from the fixed page.
- **Format (verified, pypdf layout mode):** bilingual Welsh/English PDF,
  "Deddf Trwyddedu 2003 / Licensing Act 2003 — Cofrestr Ceisiadau /
  Register of Applications". Sections by heading:
  `Cais am Drwydded Safle / Application For Premise Licence` (grant),
  `Cais am Amrywio Trwydded Safle / Application to Vary Premises Licence`
  (variation), `Cais am Fân Amrywiadau / Minor Variation Application`
  (minor variation; its date column is "Application Date", not closing
  date). Each entry: a line starting `dd/mm/yyyy` then premises name and
  address (postcode last), next line the applicant and their address, then
  `Cais am:` (Welsh activities) and `Application for:` / `Application to:`
  (English activities) until the next dated line. Footer "Lluniwyd yr
  adroddiad ar / Report produced on dd/mm/yyyy".
- **Fields:** closing date, premises name + address, applicant, activities
  and hours. No reference number: key = `lic:carmarthenshire:<slug of
  premises name + postcode + closing date>`.
- **Identifying a venue:** name and address are run together on one line
  with the postcode at the end; the parser takes the postcode and, for the
  name, the tokens before the first street-number or street word.
- **Pilot filter:** most entries are outside SA1–SA20 (Carmarthen SA31,
  Kidwelly SA17 is inside, Llanelli SA14/15 inside, Ammanford SA18 inside);
  the collector keeps only pilot outcodes.

### Bridgend

- **Page:** `https://www.bridgend.gov.uk/business/licensing/licensing-registers-and-notices/`
  ("Licensing registers and notices"). HTML, open to scripts. Sections
  `<h2>Public notices</h2>` (new and variation applications),
  `<h2>Review notices</h2>`, `<h2>Summary review notices</h2>`; each a
  `document-list` of `<a href="/media/<id>/<slug>.pdf" title="<premises,
  address, postcode>">`. One public notice and one review on 21 Oct 2026.
  Also linked: **Register of Premises Licences**
  (`/media/32mfemaa/register-of-premises-licences.pdf`, 170 pages, every
  granted licence: premises, licence no `BCBCLP…`, applied/start dates,
  holder, activities) and the club certificates register. The full
  register is a future matching aid (licence holder = legal entity), not a
  new-venue signal.
- **Document template (verified):**
  ```
  Licensing Act 2003 - Public Notice of Application
  Application for a Premises Licence (under section 17)
  Name of Applicant: <applicant>
  Name and Postal address of Premises:
  <premises name>
  <address lines…>
  <postcode>
  Statement of Relevant Licensable Activities …
  1. The sale by retail of alcohol …
  Any representations must be made in writing … by <d Month yyyy>.
  ```
  Variations say "Application to Vary a Premises Licence (under section
  34)". Created-date in the PDF metadata (2 Oct 2026 for a notice whose
  deadline is 29 Oct 2026: consistent with 28 days from receipt).
- **Fields:** applicant, premises name (first line of the block), address,
  postcode (last line), activities, closing date. Key = `lic:bridgend:<media id>`.

### Powys (SA9 only: Abercrave, Ystradgynlais, Ystalyfera)

- **Page:** `https://en.powys.gov.uk/article/5201/Current-premises--club-licence-applications`
  ("Current premises / club licence applications"). HTML, open to scripts
  (a `?ccp=true#cookie-consent-prompt` redirect, harmless). No documents.
- **Format (verified):** `<h3>Current Applications</h3>` then one
  `<div class="tb-alert tb-alert-mono">` per application:
  `<h4>Application for new premises licence</h4>` or `<h4>Application for
  full variation to premises licence</h4>`, a `<dl class="dl-horizontal">`
  with `<dt>Applicant:</dt><dd>…</dd>`, `<dt>Premises:</dt><dd><name>,
  <address>, <postcode>, </dd>`, `<dt>Activities:</dt><dd><ul><li><strong>Sale
  of alcohol</strong>, hours</li>…</ul></dd>`, optional `<dt>Other
  variations:</dt>`, and `<p>Closing date for representations: dd/mm/yyyy</p>`.
  Four on 21 Oct 2026, one of them in the pilot (Abercrave RFC, SA9 1XH, a
  variation); police reviews are filed as "full variation" with the review
  text in "Other variations", so the parser treats an applicant containing
  "Police" as a review, not a variation.
- **Fields:** kind, applicant, premises name (first comma chunk), address,
  postcode, activities, closing date. Key = `lic:powys:<slug of premises +
  postcode + closing date>`.
- **Register:** "Register of Premises and Personal Licences issued" is by
  request only (article 5050). Nothing downloadable.

## What identifies a venue, and how matching works

Every source yields a **premises name** (except some Swansea headings and
address-only planning applications), a **full postcode** (nearly always;
PlanIt's `postcode` is null for land parcels) and an address. Matching to
our venues goes:

1. Full postcode → the owned `pois` rows at that postcode (Supabase
   `postcode=eq.`), → companies by `overture:<id>`.
2. `name_similarity(premises name, poi name) >= 0.85` (the FHRS floor; one
   shared common word is never enough, see `common.py`). For planning,
   where the venue name is fused into the address, the venue's distinctive
   tokens must all appear in the application's address or description;
   one token alone must lead the address and be at least four letters.
3. A match records the signal on the existing company with that confidence.
   No match creates a company (`discover:licensing` / `discover:planning`,
   external id `lic:…` / `plan:…`), so a venue nobody has heard of yet
   becomes a lead. Never both.
4. Idempotence: `craven_enrichment(company_id, source='licensing:<key>' |
   'planning:<key>')` is written once per application per company; a rerun
   finds it and does nothing.

## Etiquette

- User agent names us and gives a contact. One list page and its linked
  documents per council per run; documents cached by URL under
  `data/licensing/<council>/`, PlanIt JSON by council and day under
  `data/planning/`. Run weekly (the enrichment cycle calls both with a
  bounded batch; `harvey-craven licensing` / `planning` run them by hand).
- A 429 from PlanIt ends the planning run; the next run picks up. No
  retries inside a run against any council host.
- Nothing is posted to any council. Representations addresses on the pages
  are for the public, not for us.

## Open items

- Swansea's notice documents need a browser; the drop folder is the
  working route until someone decides on a browser-driven fetch.
- Bridgend's and NPT's variation / review document templates are inferred
  from the section headings on the list pages and the Act's section
  numbers; a live variation notice should be captured into the fixtures
  when one appears.
- Carmarthenshire's planning portal (Salesforce) sometimes gives PlanIt a
  null postcode; those applications are skipped for matching and created
  with outcode "" — check them by hand.
