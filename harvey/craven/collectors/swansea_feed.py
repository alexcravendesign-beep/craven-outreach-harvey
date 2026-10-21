"""Swansea licensing feed: fetch the council's notice page and every notice
document from a host Cloudflare lets through, and deliver them into the
collector's drop folder on the host that runs the collector.

Why: www.swansea.gov.uk answers 403 (Cloudflare) to the Mac mini that runs
``harvey-craven licensing`` but serves the Studio. ``collect_council`` already
reads Swansea from ``data/licensing/swansea/inbox/`` when that folder holds
``index.html``; the documents sit beside it under their own file names (the
last path segment of the link, query string dropped), which is exactly what
``stage_swansea`` writes. The parser is the collector's own: this module
only fetches with its ``Fetcher`` and copies.

Run on the Studio by launchd (``com.8zero8.craven-swansea-feed``, daily):

    python -m harvey.craven.collectors.swansea_feed --deliver nick@mini:~/Projects/craven-outreach/app

Nothing here records anything; the collector on the mini does that.
"""

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
from pathlib import Path

import httpx

from harvey.craven.collectors.licensing import CACHE_DIR, PAGES, Fetcher, parse_notice_list

logger = logging.getLogger("harvey.craven.swansea_feed")

INBOX_REL = Path("data") / "licensing" / "swansea" / "inbox"


def doc_name(url: str) -> str:
    """The file name ``collect_council`` looks for in the drop folder."""
    return url.rsplit("/", 1)[-1].split("?")[0]


async def stage_swansea(fetcher: Fetcher, stage: Path) -> dict:
    """Fetch the list page and each linked notice into ``stage`` in the drop
    folder's layout. A document that fails is logged and left out (the
    collector then falls back to the link text, as it does for a 403)."""
    page_url = PAGES["swansea"]
    html = (await fetcher.get("swansea", page_url, ".html")).decode("utf-8", "ignore")
    items = parse_notice_list(html, page_url)
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    (stage / "index.html").write_text(html, encoding="utf-8")
    fetched, failed = [], []
    for url, text in items:
        name = doc_name(url)
        try:
            data = await fetcher.get("swansea", url, "." + name.rsplit(".", 1)[-1].lower())
        except httpx.HTTPError as e:
            logger.warning(f"swansea feed: {name} not fetched ({e})")
            failed.append(name)
            continue
        (stage / name).write_bytes(data)
        fetched.append(name)
        logger.info(f"swansea feed: {name} ({len(data)} bytes) — {text}")
    return {"page_bytes": len(html), "notices": len(items), "documents": len(fetched), "failed": failed}


def deliver(stage: Path, target: str, ssh_opts: tuple[str, ...] = ("-o", "BatchMode=yes", "-o", "ConnectTimeout=20")) -> None:
    """scp the staged files into ``<target>/data/licensing/swansea/inbox/``
    where ``target`` is ``user@host:path`` of the collector's project root."""
    host, _, root = target.partition(":")
    inbox = f"{root.rstrip('/')}/{INBOX_REL.as_posix()}"
    # Unquoted on purpose: the path is our own config and may start with ~,
    # which the remote shell only expands outside quotes (quoted, it made a
    # literal "~" directory and the scp that followed failed).
    subprocess.run(["ssh", *ssh_opts, host, f"mkdir -p {inbox}"], check=True)
    files = sorted(p for p in stage.iterdir() if p.is_file())
    subprocess.run(["scp", *ssh_opts, "-q", *map(str, files), f"{host}:{inbox}/"], check=True)
    logger.info(f"swansea feed: delivered {len(files)} files to {host}:{inbox}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="fetch Swansea's licensing notices into the collector's drop folder")
    ap.add_argument("--stage", type=Path, default=CACHE_DIR / "swansea" / "feed",
                    help="where the inbox layout is assembled (default data/licensing/swansea/feed)")
    ap.add_argument("--deliver", metavar="USER@HOST:PROJECT_ROOT",
                    help="scp the staged files into PROJECT_ROOT/data/licensing/swansea/inbox/ on that host")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    try:
        out = asyncio.run(stage_swansea(Fetcher(cache_days=0), args.stage))
    except httpx.HTTPError as e:
        logger.error(f"swansea feed: list page not fetched ({e}); nothing delivered")
        return 1
    print(f"swansea feed: notices={out['notices']} documents={out['documents']} failed={len(out['failed'])} "
          f"page_bytes={out['page_bytes']} stage={args.stage}")
    if out["notices"] == 0:
        logger.error("swansea feed: the page parsed to no notices; not delivering an empty index")
        return 2
    if args.deliver:
        deliver(args.stage, args.deliver)
    return 0


if __name__ == "__main__":
    sys.exit(main())
