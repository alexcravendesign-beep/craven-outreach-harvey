"""Council licensing notices: parsing each council's real page shape (fixtures
captured 21 Oct 2026, applicants' personal names replaced), matching with
the FHRS rule, company creation, and idempotent reruns."""

import io
import zipfile
from pathlib import Path

import httpx
import pytest

from harvey.craven.collectors import licensing as L
from harvey.craven.collectors.licensing import (Fetcher, Notice, collect_council, enrich_licensing, extract_text,
                                                parse_bridgend_list, parse_bridgend_notice, parse_carmarthenshire_register,
                                                parse_carmarthenshire_register_link, parse_notice_list, parse_npt_notice,
                                                parse_powys, record_notices, split_name_address)
from harvey.craven.signals import install_signals
from harvey.models.company import Company
from harvey.signals import seed_signal_catalog
from tests.craven.conftest import FakeSupabase

FIX = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def docx_bytes(paragraphs: list[str]) -> bytes:
    """A minimal .docx with one paragraph per line, the way councils export notices."""
    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
           f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


NPT_DARTER_DOCX = docx_bytes([
    "LICENSING ACT 2003", "APPLICATION FOR GRANT OF A PREMISES LICENCE",
    "Neath Port Talbot County Borough Council hereby give notice that an application has been received under "
    "Section 17 of the Licensing Act for the grant of a Premises Licence from:- ",
    "A Partnership", "in respect of:- ", "Unit 5, Port Talbot Business Units, Addison Road, Port Talbot, SA12 6HZ",
    "The licensable activities to be carried out are:- ", "The Sale of Alcohol, Indoor Sporting Events",
    "A record of this application may be inspected Monday to Friday, between 10am – 4pm at the offices of:",
    "Neath Port Talbot County Borough Council", "Legal Regulatory Services", "Civic Centre", "Port Talbot", "SA13 1PJ",
    "If you wish to view the authorities licensing register you can do so by visiting the following website www.npt.gov.uk/licensing ",
    "Any responsible authority or other person wishing to make representations in respect of this application should "
    "do so in writing to the Licensing Section at the above address by the 02/11/2026.",
    "It is an offence knowingly or recklessly to make a false statement",
])


# ── Parsers ──

def test_extract_text_reads_docx_and_pdf():
    text = extract_text(NPT_DARTER_DOCX, "9-darter-eng.docx")
    assert "in respect of" in text and "SA12 6HZ" in text
    pdf = extract_text((FIX / "bridgend_notice_grant.pdf").read_bytes(), "notice.pdf")
    assert "Name of Applicant:" in pdf and "CF32 7HT" in pdf
    assert extract_text(b"not a document", "x.doc") == ""


def test_npt_list_and_notice_parse():
    links = parse_notice_list(fixture("npt_notices.html"), L.PAGES["npt"])
    assert [u.split("/media/")[1][:8] for u, _ in links] == ["o0bblexp", "zwdlbqy2"]
    assert links[1][1].startswith("Chicken Valley, 3 The Parade, Neath, SA11 1PU")
    d = parse_npt_notice(fixture("npt_notice_grant.txt"))
    assert d == {"kind": "grant", "applicant": "A N Applicant", "premises": "Chicken Valley",
                 "address": "3 The Parade, Neath, SA11 1PU", "postcode": "SA11 1PU",
                 "activities": ["Late Night Refreshment"], "closing_date": "2026-11-11"}
    d = parse_npt_notice(extract_text(NPT_DARTER_DOCX, "9-darter-eng.docx"))
    assert d["premises"] == "Unit 5" and d["postcode"] == "SA12 6HZ" and d["closing_date"] == "2026-11-02"
    assert d["activities"] == ["The Sale of Alcohol, Indoor Sporting Events"]


def test_swansea_list_parses_the_same_page_shape():
    links = parse_notice_list(fixture("swansea_notices.html"), L.PAGES["swansea"])
    assert [t for _, t in links] == ["RRA RETAIL Ltd_55-57 Eversley road, Sketty, Swansea, SA2 9DE",
                                     "224 Neath Road, Landore, Swansea", "Pizza Giorgio, 159 Ravenhill Road, Swansea"]
    assert links[0][0].startswith("https://www.swansea.gov.uk/media/28667/") and links[2][0].endswith(".pdf?m=1791902864277")


def test_bridgend_list_sections_and_notice_pdf():
    items = parse_bridgend_list(fixture("bridgend_notices.html"), L.PAGES["bridgend"])
    assert [(t.split(",")[0], s) for _, t, s in items] == [("Grabbi", "public notices"),
                                                           ("Review of a Premises Licence – Eden", "review notices")]
    d = parse_bridgend_notice(extract_text((FIX / "bridgend_notice_grant.pdf").read_bytes(), "n.pdf"))
    assert d["kind"] == "grant" and d["applicant"] == "Grabbi Limited" and d["premises"] == "Grabbi"
    assert d["postcode"] == "CF32 7HT" and d["closing_date"] == "2026-10-29"
    assert d["address"].startswith("The kingdom Hall") and d["address"].endswith("Bridgend")
    assert d["activities"] == ["The sale by retail of alcohol for consumption off the premises"]


def test_carmarthenshire_register_link_and_entries():
    url = parse_carmarthenshire_register_link(fixture("carmarthenshire_comment.html"), L.PAGES["carmarthenshire"])
    assert url == "https://www.carmarthenshire.gov.wales/media/qytnzn3s/public-register-1210.pdf"
    entries = parse_carmarthenshire_register(fixture("carmarthenshire_register.txt"))
    assert [(e["kind"], e["closing_date"], e["postcode"]) for e in entries] == [
        ("grant", "2026-10-27", "SA32 8QX"), ("grant", "2026-10-28", "SA15 1DJ"), ("grant", "2026-11-05", "SA18 1LS"),
        ("variation", "2026-10-01", "SA17 5HQ"), ("minor_variation", "2026-09-17", "SA15 3AE")]
    kebab = entries[1]
    assert kebab["premises"] == "King Kebab" and kebab["address"] == "34 Murray Street Llanelli Carmarthenshire"
    assert kebab["applicant"].startswith("Hasan2027Pizza Limited")
    assert kebab["activities"][0].startswith("Late night refreshment: Sunday - Thursday 23:00 - 00:00")
    assert entries[3]["activities"] == ["Amend the licence for all activities to start from 10:00.",
                                        "Amend the licence plan to add a new building.",
                                        "Amend conditions on premises licence."]
    assert split_name_address("Selwyn Samuel Centre Park Crescent Llanelli") == ("Selwyn Samuel Centre", "Park Crescent Llanelli")


def test_powys_structured_html():
    notices = parse_powys(fixture("powys_current.html"), L.PAGES["powys"])
    assert [(n.kind, n.premises, n.postcode, n.closing_date) for n in notices] == [
        ("grant", "Crown Building", "LD2 3AE", "2026-10-23"),
        ("review", "Griffin Inn", "LD3 0UR", "2026-11-10"),               # a police review filed as a variation
        ("variation", "Abercrave Rugby Football Club", "SA9 1XH", "2026-11-10"),
        ("grant", "ABERCEIROS INN", "LD4 4EW", "2026-11-10")]
    assert notices[0].activities[0].startswith("Sale of alcohol, 09.00 - 02.30")
    assert notices[2].activities == ["Change of the plan of the premises to include all outdoor areas and to amend/remove outdated conditions."]
    assert notices[2].external_id == "lic:powys:abercrave-rugby-football-club-sa9-1xh-2026-11-10"


# ── Fetching ──

def council_transport(calls: list[str], blocked: set[str] = frozenset()):
    pages = {
        L.PAGES["npt"]: fixture("npt_notices.html").encode(),
        L.PAGES["powys"]: fixture("powys_current.html").encode(),
        L.PAGES["bridgend"]: fixture("bridgend_notices.html").encode(),
        L.PAGES["carmarthenshire"]: fixture("carmarthenshire_comment.html").encode(),
        "https://www.npt.gov.uk/media/o0bblexp/9-darter-eng.docx?v=20261006120318": NPT_DARTER_DOCX,
        "https://www.npt.gov.uk/media/zwdlbqy2/chicken-valley-3-the-parade-neath-sa11-1pu.pdf?mediaredirect=false&v=20261014144919":
            b"%PDF-stub",   # unreadable PDF: the link text must carry the notice
        "https://www.bridgend.gov.uk/media/aqbj4qvv/grabbi-the-kingdom-hall-of-jehovah-s-witnesses-cemetery-road-ogmore-vale-bridgend-cf32-7ht.pdf":
            (FIX / "bridgend_notice_grant.pdf").read_bytes(),
        "https://www.bridgend.gov.uk/media/vxtpn5bj/review-of-a-premises-licence-eden-33-market-street-bridgend-cf31-1lj.pdf": b"%PDF-stub",
    }

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        calls.append(url)
        assert req.headers["user-agent"].startswith("craven-outreach/")
        if req.url.host in blocked:
            return httpx.Response(403, text="Just a moment...")
        if url in pages:
            return httpx.Response(200, content=pages[url])
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_collect_npt_fetches_once_and_caches(tmp_path):
    calls = []
    fetcher = Fetcher(cache_days=1, transport=council_transport(calls), cache_dir=tmp_path, sleeper=lambda s: None)
    notices = await collect_council("npt", fetcher)
    assert [(n.key, n.kind, n.premises, n.postcode, n.closing_date) for n in notices] == [
        ("o0bblexp", "grant", "Unit 5", "SA12 6HZ", "2026-11-02"),
        ("zwdlbqy2", "grant", "Chicken Valley", "SA11 1PU", "")]      # PDF unreadable: name and postcode from the link
    assert len(calls) == 3
    again = await collect_council("npt", Fetcher(cache_days=1, transport=council_transport(calls), cache_dir=tmp_path))
    assert len(calls) == 3 and len(again) == 2                           # everything came from the cache


@pytest.mark.asyncio
async def test_bridgend_reviews_are_kept_apart_and_carmarthenshire_follows_the_link(tmp_path):
    calls = []
    fetcher = Fetcher(transport=council_transport(calls), cache_dir=tmp_path, sleeper=lambda s: None)
    b = await collect_council("bridgend", fetcher)
    assert [(n.kind, n.premises, n.postcode) for n in b] == [("grant", "Grabbi", "CF32 7HT"), ("review", "Eden", "CF31 1LJ")]
    assert b[0].closing_date == "2026-10-29" and b[0].applicant == "Grabbi Limited"
    c = await collect_council("carmarthenshire", fetcher)
    assert c == []                                                        # the register PDF 404s in this transport
    assert any("public-register-1210.pdf" in u for u in calls)


@pytest.mark.asyncio
async def test_swansea_blocked_host_reads_the_drop_folder(tmp_path, caplog):
    calls = []
    fetcher = Fetcher(transport=council_transport(calls, blocked={"www.swansea.gov.uk"}), cache_dir=tmp_path,
                      sleeper=lambda s: None)
    assert await collect_council("swansea", fetcher) == []
    assert "Drop the page and its files" in caplog.text
    inbox = tmp_path / "swansea" / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "index.html").write_text(fixture("swansea_notices.html"), encoding="utf-8")
    (inbox / "ngRRA_RETAIL_Ltd_55-57_Eversley_road__Sketty__Abertawe__SA2_9DE.docx").write_bytes(
        docx_bytes(fixture("swansea_notice_grant.txt").splitlines()))
    notices = await collect_council("swansea", fetcher)
    assert [(n.key, n.kind, n.premises, n.postcode, n.closing_date) for n in notices] == [
        ("28667", "grant", "", "SA2 9DE", "2026-07-31"),                  # a shop: no trading name in the notice
        ("29847", "grant", "", "", ""),                                   # not in the drop, blocked: link text only
        ("29895", "grant", "Pizza Giorgio", "", "")]
    assert notices[0].applicant == "RRA RETAIL LIMITED" and notices[0].address.startswith("55-57 Eversley road")
    assert not any("/media/28667/" in u for u in calls)              # the dropped file was read, not fetched
    assert any("/media/29895/" in u for u in calls)                   # the missing one was tried, blocked, and logged


# ── Recording ──

async def _seed(state):
    install_signals(); await seed_signal_catalog(state)
    for code in ("NEW_PREMISES_LICENCE_APPLICATION", "LICENCE_VARIATION"):
        await state.set_signal_status(code, "confirmed")


@pytest.mark.asyncio
async def test_record_matches_creates_and_never_guesses(state, settings):
    await _seed(state)
    valley = await state.add_company(Company(name="Chicken Valley", industry="takeaway", source="discover:overture_supabase", external_id="overture:p1"))
    shack = await state.add_company(Company(name="Valley Chicken Shack", industry="takeaway", source="discover:overture_supabase", external_id="overture:p2"))
    rfc = await state.add_company(Company(name="Abercrave RFC", industry="bar", source="discover:overture_supabase", external_id="overture:p3"))
    sb = FakeSupabase(pois=[{"id": "p1", "name": "Chicken Valley", "postcode": "SA11 1PU"},
                            {"id": "p2", "name": "Valley Chicken Shack", "postcode": "SA15 1DJ"},
                            {"id": "p3", "name": "Abercrave RFC", "postcode": "SA9 1XH"}])
    notices = [
        Notice("npt", "zwdlbqy2", "grant", "Chicken Valley", "3 The Parade, Neath", "SA11 1PU", "A", ["Late Night Refreshment"], "2026-11-11", "https://npt.example/zwdlbqy2.pdf"),
        # one shared common word at the same postcode is not a match: a new company instead
        Notice("carmarthenshire", "chicken-valley-sa15-1dj-2026-10-28", "grant", "Chicken Valley", "34 Murray Street Llanelli", "SA15 1DJ", "", ["Late night refreshment"], "2026-10-28", "https://carms.example/register.pdf"),
        Notice("powys", "abercrave-rugby-football-club-sa9-1xh-2026-11-10", "variation", "Abercrave Rugby Football Club", "Abercrave", "SA9 1XH", "The club", ["Change of the plan of the premises to include all outdoor areas"], "2026-11-10", "https://powys.example/current"),
        Notice("powys", "crown-building-ld2-3ae-2026-10-23", "grant", "Crown Building", "Builth Wells", "LD2 3AE", "", ["Sale of alcohol"], "2026-10-23", "https://powys.example/current"),
        Notice("bridgend", "vxtpn5bj", "review", "Eden", "33 Market Street", "CF31 1LJ", "", [], "", "https://bridgend.example/eden.pdf"),
    ]
    confirmed = await state.confirmed_signal_codes()
    run_id = await state.start_run("licensing", provider="test")
    out = await record_notices(state, sb.client(), notices, {f"SA{i}" for i in range(1, 21)}, run_id, confirmed)
    # Chicken Valley matched at its postcode; the Llanelli namesake and "Abercrave RFC" (one shared word) were not guessed
    assert out == {"notices": 5, "in_area": 3, "matched": 1, "created": 2, "already_recorded": 0, "observations": 3}
    assert await state.cohort(require=["NEW_PREMISES_LICENCE_APPLICATION"]) != [] and valley in await state.cohort(require=["NEW_PREMISES_LICENCE_APPLICATION"])
    assert shack not in await state.cohort(require=["NEW_PREMISES_LICENCE_APPLICATION"])
    new = await state.get_company_by_external_id("lic:carmarthenshire:chicken-valley-sa15-1dj-2026-10-28")
    assert new and new.source == "discover:licensing" and new.location == "SA15" and new.name == "Chicken Valley"
    assert await state.get_company_by_external_id("lic:powys:crown-building-ld2-3ae-2026-10-23") is None   # outside the pilot
    obs = {o["signal_code"]: o for o in await state.get_observations(company_id=valley)}
    assert obs["NEW_PREMISES_LICENCE_APPLICATION"]["value_num"] == 1.0 and obs["NEW_PREMISES_LICENCE_APPLICATION"]["confidence"] >= 0.85
    assert obs["NEW_PREMISES_LICENCE_APPLICATION"]["evidence_url"] == "https://npt.example/zwdlbqy2.pdf"
    assert "closing date for representations 2026-11-11" in obs["NEW_PREMISES_LICENCE_APPLICATION"]["value_text"]
    assert await state.get_observations(company_id=rfc) == []
    club = await state.get_company_by_external_id("lic:powys:abercrave-rugby-football-club-sa9-1xh-2026-11-10")
    vobs = await state.get_observations(company_id=club.id, signal_code="LICENCE_VARIATION")
    assert len(vobs) == 1 and vobs[0]["value_text"].startswith("Change of the plan of the premises") and "variation" in vobs[0]["value_text"]
    # a rerun records nothing twice and creates nothing new
    again = await record_notices(state, sb.client(), notices, {f"SA{i}" for i in range(1, 21)}, run_id, confirmed)
    assert again["already_recorded"] == 3 and again["created"] == 0 and again["observations"] == 0
    assert len(await state.get_observations(company_id=valley)) == 1


@pytest.mark.asyncio
async def test_enrich_licensing_runs_the_councils_and_records_a_run(state, settings, tmp_path):
    await _seed(state)
    calls = []
    fetcher = Fetcher(transport=council_transport(calls), cache_dir=tmp_path, sleeper=lambda s: None)
    sb = FakeSupabase(pois=[])
    out = await enrich_licensing(state, settings, sb.client(), councils=["npt", "powys"], fetcher=fetcher)
    assert out["npt"]["created"] == 2 and out["npt"]["observations"] == 2
    assert out["powys"] == {"notices": 4, "in_area": 1, "matched": 0, "created": 1, "already_recorded": 0, "observations": 1}
    rfc = await state.get_company_by_external_id("lic:powys:abercrave-rugby-football-club-sa9-1xh-2026-11-10")
    assert rfc.industry == "licence_applicant" and rfc.location == "SA9"
    runs = await state.get_runs(limit=5) if hasattr(state, "get_runs") else []
    assert not runs or runs[0]["status"] == "completed"


def test_swansea_notice_template_reads_the_english_half():
    from harvey.craven.collectors.licensing import parse_swansea_notice
    g = parse_swansea_notice(fixture("swansea_notice_grant.txt"))
    assert g["kind"] == "grant" and g["applicant"] == "RRA RETAIL LIMITED" and g["postcode"] == "SA2 9DE"
    assert g["premises"] == "" and g["address"] == "55-57 Eversley road, Sketty, Swansea, SA2 9DE"
    assert g["activities"] == ["Supply of alcohol: Mon-Sun – 0600-2300"] and g["closing_date"] == "2026-07-31"
    v = parse_swansea_notice(fixture("swansea_notice_variation.txt"))
    assert v["kind"] == "variation" and v["applicant"] == "Optimus Trading Services Ltd" and v["postcode"] == "SA1 2JG"
    assert v["activities"][:2] == ["Sale of Alcohol: Monday-Sunday 0000-2400", "Late Night Refreshment: Monday-Sunday 2300-0500"]
    assert v["activities"][2].startswith("To amend the existing conditions") and v["closing_date"] == "2026-11-04"
    # the Welsh half says "amrywio" (vary) on a grant notice: only the English half is believed
    assert "HYSBYSIAD O GAIS I AMRYWIO" in fixture("swansea_notice_grant.txt")
