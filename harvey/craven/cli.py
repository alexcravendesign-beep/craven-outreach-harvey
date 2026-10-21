"""``harvey-craven``: the plugin's own commands (a separate console script, so
Harvey's CLI stays untouched).

  harvey-craven lia sign --ref lia-1 --by "Nick Lewis" [--doc URL] [--assessment TEXT]
  harvey-craven lia show --ref lia-1
  harvey-craven suppress --email info@x.co [--reason manual] | --domain x.co
  harvey-craven sync                      # pull the suppression list from Supabase
  harvey-craven check <email>             # is this address suppressed / a role mailbox?
"""

import argparse
import asyncio
import sys

from dotenv import load_dotenv


def _boot():
    load_dotenv()
    from harvey.config import load_config
    from harvey.state import StateManager
    from harvey import craven
    config = load_config()  # installs the plugin
    if craven.SETTINGS is None:
        sys.exit("The Craven plugin is not loaded: add `plugins: [harvey.craven]` to harvey.local.yaml.")
    state = StateManager()
    return config, state, craven.SETTINGS


async def _stores(state, settings):
    from harvey.craven.lawful_basis import LawfulBasisStore
    from harvey.craven.schema import ensure_schema
    from harvey.craven.supabase import SupabaseRest
    from harvey.craven.suppression import SuppressionStore
    await state.init_db()
    await ensure_schema(state)
    sb = SupabaseRest.from_env()
    return SuppressionStore(state, settings.tenant, sb), LawfulBasisStore(state, settings.tenant, sb), sb


def cmd_lia(args):
    _, state, settings = _boot()

    async def go():
        _, lia, _ = await _stores(state, settings)
        if args.action == "sign":
            rec = await lia.sign(args.ref, args.by, doc_url=args.doc or "", assessment=args.assessment or "")
            print(f"Signed lawful basis '{rec['ref']}' by {rec['signed_by']} at {rec['signed_at']}.")
            if settings.compliance.lia_ref != args.ref:
                print(f"Note: craven.compliance.lia_ref is '{settings.compliance.lia_ref or '(unset)'}'; "
                      f"set it to '{args.ref}' for the gate to use this record.")
        else:
            rec = await lia.get(args.ref)
            print(rec if rec else f"No lawful-basis record '{args.ref}'.")
    asyncio.run(go())


def cmd_suppress(args):
    _, state, settings = _boot()

    async def go():
        sup, _, _ = await _stores(state, settings)
        await sup.add(email=args.email or "", domain=args.domain or "", reason=args.reason)
        print(f"Suppressed {args.email or args.domain} ({args.reason}). Mirror: {await sup.count()} rows.")
    asyncio.run(go())


def cmd_sync(args):
    _, state, settings = _boot()

    async def go():
        sup, _, sb = await _stores(state, settings)
        if sb is None:
            sys.exit("SUPABASE_URL / SUPABASE_SERVICE_KEY not set.")
        n = await sup.sync()
        print(f"Merged {n} row(s). Mirror holds {await sup.count()}; last sync {await sup.synced_at()}.")
    asyncio.run(go())


def cmd_fhrs(args):
    _, state, settings = _boot()

    async def go():
        from harvey.craven.collectors.fhrs import enrich_fhrs
        from harvey.craven.schema import ensure_schema
        from harvey.craven.supabase import SupabaseRest
        await state.init_db(); await ensure_schema(state)
        print(await enrich_fhrs(state, settings, SupabaseRest.from_env(), limit=args.limit))
    asyncio.run(go())


def cmd_companies_house(args):
    _, state, settings = _boot()

    async def go():
        from harvey.craven.collectors.companies_house import enrich_companies_house
        from harvey.craven.schema import ensure_schema
        from harvey.craven.supabase import SupabaseRest
        await state.init_db(); await ensure_schema(state)
        print(await enrich_companies_house(state, settings, SupabaseRest.from_env(), limit=args.limit))
    asyncio.run(go())


def cmd_sweep(args):
    _, state, settings = _boot()

    async def go():
        from harvey.craven.collectors.companies_house import sweep_new_incorporations
        from harvey.craven.schema import ensure_schema
        await state.init_db(); await ensure_schema(state)
        print(await sweep_new_incorporations(state, settings))
    asyncio.run(go())


def cmd_draft(args):
    config, state, settings = _boot()

    async def go():
        from harvey.brain import Brain
        from harvey.config import load_env
        from harvey.craven.writer import draft_one
        await state.init_db()
        brain = Brain(state, config.brain)
        for cid in args.company:
            out = await draft_one(state, brain, config, load_env(), cid, settings=settings)
            if out is None:
                print(f"{cid}: no draft (no prospect, no usable signal, or the model gave nothing)")
                continue
            print(f"\n=== {out['company']} -> {out['to']} [{out['email_status']}] lead={out['lead']} outbox={out['outbox_id']}")
            print(f"Subject: {out['subject']}\n\n{out['body']}\n")
    asyncio.run(go())


def cmd_scrape_mailboxes(args):
    _, state, settings = _boot()

    async def go():
        from harvey.craven.mailbox_scrape import scrape_mailboxes
        from harvey.craven.schema import ensure_schema
        await state.init_db(); await ensure_schema(state)
        print(await scrape_mailboxes(state, settings, limit=args.limit))
    asyncio.run(go())


def cmd_dossiers(args):
    config, state, settings = _boot()

    async def go():
        from harvey.brain import Brain
        from harvey.craven.dossier import build_dossiers
        from harvey.craven.schema import ensure_schema
        await state.init_db(); await ensure_schema(state)
        print(await build_dossiers(state, Brain(state, config.brain), settings, limit=args.limit, max_percent=args.max_percent))
    asyncio.run(go())


def cmd_check(args):
    _, state, settings = _boot()
    from harvey.craven import compliance

    async def go():
        sup, _, _ = await _stores(state, settings)
        kind = compliance.classify_email(args.email, settings.compliance.role_local_parts)
        print(f"{args.email}: {kind} mailbox; suppressed={await sup.is_suppressed(args.email)}; "
              f"mirror stale={await sup.is_stale(settings.compliance.max_mirror_age_hours)}")
    asyncio.run(go())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="harvey-craven", description="Craven plugin commands")
    subs = parser.add_subparsers(dest="command", required=True)

    lia = subs.add_parser("lia", help="lawful basis (LIA) records")
    lia.add_argument("action", choices=["sign", "show"])
    lia.add_argument("--ref", required=True)
    lia.add_argument("--by", default="", help="who signs (required for sign)")
    lia.add_argument("--doc", default="", help="URL/path of the signed assessment")
    lia.add_argument("--assessment", default="")
    lia.set_defaults(func=cmd_lia)

    sup = subs.add_parser("suppress", help="add an address or domain to the suppression list")
    sup.add_argument("--email", default="")
    sup.add_argument("--domain", default="")
    sup.add_argument("--reason", default="manual")
    sup.set_defaults(func=cmd_suppress)

    subs.add_parser("sync", help="pull the suppression list from Supabase").set_defaults(func=cmd_sync)

    f = subs.add_parser("fhrs", help="match venues to the Food Hygiene Rating Scheme (open data)")
    f.add_argument("--limit", type=int, default=500)
    f.set_defaults(func=cmd_fhrs)

    ch = subs.add_parser("companies-house", help="match venues to Companies House (needs COMPANIES_HOUSE_API_KEY)")
    ch.add_argument("--limit", type=int, default=100)
    ch.set_defaults(func=cmd_companies_house)

    sw = subs.add_parser("sweep-new-companies", help="new hospitality incorporations in the area (Companies House)")
    sw.set_defaults(func=cmd_sweep)

    ms = subs.add_parser("scrape-mailboxes", help="find a venue's own mailbox on its website (free, no model)")
    ms.add_argument("--limit", type=int, default=300)
    ms.set_defaults(func=cmd_scrape_mailboxes)

    do = subs.add_parser("dossiers", help="model-read venue dossiers (runs on your subscription; GUI session only)")
    do.add_argument("--limit", type=int, default=100)
    do.add_argument("--max-percent", type=float, default=50.0)
    do.set_defaults(func=cmd_dossiers)

    dr = subs.add_parser("draft", help="write one grounded first email for a company into the Outbox (pending review; never sends)")
    dr.add_argument("--company", action="append", required=True, help="company id (repeatable)")
    dr.set_defaults(func=cmd_draft)

    chk = subs.add_parser("check", help="classify an address and check suppression")
    chk.add_argument("email")
    chk.set_defaults(func=cmd_check)

    args = parser.parse_args(argv)
    if args.command == "lia" and args.action == "sign" and not args.by:
        parser.error("lia sign needs --by")
    args.func(args)


if __name__ == "__main__":
    main()
