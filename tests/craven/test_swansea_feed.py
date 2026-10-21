"""The Studio-side Swansea feed writes exactly the drop-folder layout that
collect_council reads (index.html + each notice under its link file name)."""

from pathlib import Path

import httpx
import pytest

from harvey.craven.collectors.licensing import Fetcher, collect_council
from harvey.craven.collectors.swansea_feed import deliver, doc_name, stage_swansea
from tests.craven.test_licensing import docx_bytes, fixture


def test_doc_name_is_the_last_path_segment_without_the_query():
    assert doc_name("https://www.swansea.gov.uk/media/28667/x/doc/ngRRA_RETAIL.docx?m=1783090075843") == "ngRRA_RETAIL.docx"


def feed_transport(calls: list[str], missing: set[str] = frozenset()):
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        if req.url.path == "/licencingnoticeofnewpremises":
            return httpx.Response(200, text=fixture("swansea_notices.html"))
        if any(m in req.url.path for m in missing):
            return httpx.Response(403)
        if req.url.path.endswith(".docx"):
            return httpx.Response(200, content=docx_bytes(fixture("swansea_notice_grant.txt").splitlines()))
        return httpx.Response(200, content=b"%PDF-1.4 not really")
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_stage_writes_the_inbox_layout_the_collector_reads(tmp_path):
    calls = []
    fetcher = Fetcher(cache_days=0, transport=feed_transport(calls, missing={"/media/29847/"}), cache_dir=tmp_path / "cache",
                      sleeper=lambda s: None)
    stage = tmp_path / "feed"
    out = await stage_swansea(fetcher, stage)
    assert out["notices"] == 3 and out["documents"] == 2 and out["failed"] == ["5n224_Neath_Road__Landore__Swansea.docx"]
    assert sorted(p.name for p in stage.iterdir()) == [
        "index.html", "ngRRA_RETAIL_Ltd_55-57_Eversley_road__Sketty__Abertawe__SA2_9DE.docx",
        "noPizza_Giorgio__159_Ravenhill_Road__Swansea.pdf"]
    # the collector, with the council blocked, reads the staged folder as its inbox and parses the dropped notice
    blocked = Fetcher(transport=httpx.MockTransport(lambda r: httpx.Response(403)), cache_dir=tmp_path / "c2", sleeper=lambda s: None)
    notices = await collect_council("swansea", blocked, inbox=stage)
    assert [(n.key, n.kind, n.postcode, n.closing_date) for n in notices] == [
        ("28667", "grant", "SA2 9DE", "2026-07-31"), ("29847", "grant", "", ""), ("29895", "grant", "", "")]
    assert notices[0].applicant == "RRA RETAIL LIMITED"
    # a rerun replaces the stage wholesale: nothing stale survives
    (stage / "old.docx").write_bytes(b"PK")
    await stage_swansea(fetcher, stage)
    assert not (stage / "old.docx").exists()


def test_deliver_scps_into_the_project_inbox(tmp_path, monkeypatch):
    import harvey.craven.collectors.swansea_feed as F
    stage = tmp_path / "feed"; stage.mkdir()
    (stage / "index.html").write_text("<html/>"); (stage / "a.docx").write_bytes(b"PK")
    ran = []
    monkeypatch.setattr(F.subprocess, "run", lambda cmd, check: ran.append(cmd))
    deliver(stage, "nick@mini:~/Projects/craven-outreach/app", ssh_opts=())
    assert ran[0] == ["ssh", "nick@mini", "mkdir -p ~/Projects/craven-outreach/app/data/licensing/swansea/inbox"]
    assert ran[1][:2] == ["scp", "-q"] and ran[1][-1] == "nick@mini:~/Projects/craven-outreach/app/data/licensing/swansea/inbox/"
    assert [Path(p).name for p in ran[1][2:-1]] == ["a.docx", "index.html"]
