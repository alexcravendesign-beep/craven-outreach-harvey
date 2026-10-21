"""CravenHandler: a bounce or an opt-out also lands on the suppression list
(local mirror + Supabase); Harvey's own bounce handling and kill switch are
unchanged."""

from datetime import datetime, timezone

import pytest

from harvey.craven.handler import CravenHandler
from harvey.integrations.mail_provider import InboundMessage
from harvey.models.prospect import Prospect
from tests.craven.conftest import FakeSupabase
from tests.test_outbox_native import Cfg, Env, FakeProvider, StubBrain


def _now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def make_craven_handler(state, provider, settings, supabase, intent="question"):
    h = CravenHandler(brain=StubBrain(intent=intent), state=state, config=Cfg(), env=Env(),
                      settings=settings, supabase=supabase)
    h.provider = provider
    return h


async def seed_role_prospect(state, email="info@swan.pub", status="contacted"):
    return await state.add_prospect(Prospect(
        first_name="", last_name="", title="General enquiries", company="The Swan",
        email=email, email_status="verified", email_verified=True, status=status))


async def sent_item(state, pid, to_email, message_id, campaign="c1"):
    item_id = await state.add_outbox_item(
        prospect_id=pid, to_email=to_email, subject="s", body="b", send_at=_now_iso(),
        status="approved", campaign_id=campaign, step=1)
    await state.update_outbox_item(item_id, status="sent", message_id=message_id, sent_at=_now_iso())
    return item_id


@pytest.mark.asyncio
async def test_a_bounce_suppresses_the_address_and_keeps_harveys_behaviour(state, settings):
    pid = await seed_role_prospect(state)
    await sent_item(state, pid, "info@swan.pub", "<m1@x>")
    for i in range(10):
        await sent_item(state, pid, "info@swan.pub", f"<m{i+2}@x>", campaign=f"c{i+2}")
    sb = FakeSupabase()
    provider = FakeProvider()
    provider.inbound = [InboundMessage(
        provider_id="b1", from_email="mailer-daemon@googlemail.com",
        subject="Delivery Status Notification (Failure)", body="couldn't be delivered",
        in_reply_to="<m1@x>", is_bounce=True)]
    handler = make_craven_handler(state, provider, settings, sb.client())

    await handler._run_native()

    assert (await state.get_prospect(pid)).email_status == "invalid"       # upstream, unchanged
    assert await state.get_setting("sending_paused") != ""                  # kill switch, unchanged
    assert await handler.suppression.is_suppressed("info@swan.pub")          # ours: local mirror
    table, rows, _ = sb.posts[-1]
    assert table == "oe_suppression" and rows[0]["email"] == "info@swan.pub" and rows[0]["reason"] == "bounce"


@pytest.mark.asyncio
async def test_an_opt_out_reply_suppresses_the_writer_and_the_record(state, settings):
    pid = await seed_role_prospect(state)
    await sent_item(state, pid, "info@swan.pub", "<m1@x>")
    sb = FakeSupabase()
    provider = FakeProvider()
    provider.inbound = [InboundMessage(
        provider_id="r1", from_email="jo.bloggs@swan.pub", subject="Re: s",
        body="Please unsubscribe us, not interested.", in_reply_to="<m1@x>")]
    handler = make_craven_handler(state, provider, settings, sb.client())

    await handler._run_native()

    assert (await state.get_prospect(pid)).status == "opted_out"            # upstream, unchanged
    assert await handler.suppression.is_suppressed("jo.bloggs@swan.pub")     # the person who wrote
    assert await handler.suppression.is_suppressed("info@swan.pub")          # the mailbox we emailed
    reasons = {(rows[0]["email"], rows[0]["reason"]) for table, rows, _h in sb.posts if table == "oe_suppression"}
    assert reasons == {("jo.bloggs@swan.pub", "unsubscribe"), ("info@swan.pub", "unsubscribe")}


@pytest.mark.asyncio
async def test_a_supabase_outage_keeps_the_local_suppression(state, settings):
    import httpx
    from harvey.craven.supabase import SupabaseRest
    pid = await seed_role_prospect(state)
    await sent_item(state, pid, "info@swan.pub", "<m1@x>")
    broken = SupabaseRest("https://sb.test", "k", transport=httpx.MockTransport(
        lambda req: httpx.Response(503, text="down")))
    provider = FakeProvider()
    provider.inbound = [InboundMessage(
        provider_id="b1", from_email="postmaster@x", subject="Undeliverable", body="bounce",
        in_reply_to="<m1@x>", is_bounce=True)]
    handler = make_craven_handler(state, provider, settings, broken)

    assert await handler._run_native() is True
    assert await handler.suppression.is_suppressed("info@swan.pub")
