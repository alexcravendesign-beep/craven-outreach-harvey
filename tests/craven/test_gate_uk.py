"""The UK gate end to end through Harvey's Sender: every refusal path and the
one that passes, on a staged, approved outbox email."""

from datetime import datetime, timezone

import pytest

from harvey.craven.sender import CravenSender
from harvey.craven.unsubscribe import mint_token
from harvey.models.campaign import Campaign, EmailStep
from harvey.models.prospect import Prospect
from tests.craven.conftest import SECRET, FakeSupabase, customer_rows
from tests.test_outbox_native import Cfg, Env, FakeProvider


def _now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def make_craven_sender(state, provider, settings, supabase):
    cfg = Cfg()
    cfg.channels.email.require_approval = True
    sender = CravenSender(brain=None, state=state, config=cfg, env=Env(),
                         settings=settings, supabase=supabase, secret=SECRET)
    sender.provider = provider
    sender.send_pacing = False
    sender.cold_send_window = lambda: True   # the window has its own test
    return sender


async def stage(state, email, body="Short note.", kind="sequence", campaign_id=None):
    pid = await state.add_prospect(Prospect(
        first_name="", last_name="", title="General enquiries", company="The Swan",
        email=email, email_status="verified", email_verified=True, status="new"))
    if campaign_id is None:
        campaign = Campaign(id="", name="c", channel="email", prospect_ids=[pid], status="draft",
                            sequence=[EmailStep(step=1, subject="hi", body=body, delay_days=0)])
        campaign_id = await state.add_campaign(campaign)
    item_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=campaign_id, kind=kind, step=1, to_email=email,
        subject="hi", body=body, send_at=_now_iso(), status="approved", provider="fake")
    return pid, item_id, campaign_id


async def ready(state, settings, supabase):
    """A signed LIA, so tests exercise one refusal at a time."""
    from harvey.craven.lawful_basis import LawfulBasisStore
    await LawfulBasisStore(state, settings.tenant, None).sign("lia-1", "Nick Lewis")


@pytest.mark.asyncio
async def test_a_known_customer_is_refused_as_suppressed(state, settings):
    sb = FakeSupabase(customer_rows(5))
    provider = FakeProvider()
    sender = make_craven_sender(state, provider, settings, sb.client())
    await ready(state, settings, sb)
    _, item_id, _ = await stage(state, "customer3@example.co.uk")
    # customer3@ is not a role mailbox either; make sure 'suppressed' is what we assert on
    await sender._drain_due()
    assert provider.sent == []
    item = await state.get_outbox_item(item_id)
    assert item["status"] == "failed" and "suppressed" in item["error"]


@pytest.mark.asyncio
async def test_a_personal_address_is_refused(state, settings):
    sb = FakeSupabase(customer_rows(1))
    provider = FakeProvider()
    sender = make_craven_sender(state, provider, settings, sb.client())
    await ready(state, settings, sb)
    _, item_id, _ = await stage(state, "jo.bloggs@swan.pub")
    await sender._drain_due()
    assert provider.sent == []
    assert "personal address blocked" in (await state.get_outbox_item(item_id))["error"]


@pytest.mark.asyncio
async def test_an_unsigned_lawful_basis_refuses_cold_email(state, settings):
    sb = FakeSupabase(customer_rows(1))
    provider = FakeProvider()
    sender = make_craven_sender(state, provider, settings, sb.client())
    _, item_id, _ = await stage(state, "info@swan.pub")
    await sender._drain_due()
    assert provider.sent == []
    assert "not signed" in (await state.get_outbox_item(item_id))["error"]


@pytest.mark.asyncio
async def test_a_stale_mirror_fails_closed(state, settings):
    provider = FakeProvider()
    sender = make_craven_sender(state, provider, settings, supabase=None)  # sync cannot run
    await ready(state, settings, None)
    _, item_id, _ = await stage(state, "info@swan.pub")
    await sender._drain_due()
    assert provider.sent == []
    assert "stale" in (await state.get_outbox_item(item_id))["error"]


@pytest.mark.asyncio
async def test_missing_unsubscribe_config_refuses_rather_than_sending_bare(state):
    from harvey.craven.config import CravenConfig
    bare = CravenConfig(**{"compliance": {"lia_ref": "lia-1", "unsubscribe_base_url": ""}})
    sb = FakeSupabase(customer_rows(1))
    provider = FakeProvider()
    sender = make_craven_sender(state, provider, bare, sb.client())
    await ready(state, bare, sb)
    _, item_id, _ = await stage(state, "info@swan.pub")
    await sender._drain_due()
    assert provider.sent == []
    assert "unsubscribe link missing" in (await state.get_outbox_item(item_id))["error"]


@pytest.mark.asyncio
async def test_a_role_mailbox_under_a_signed_lia_sends_with_its_own_unsubscribe_link(state, settings):
    sb = FakeSupabase(customer_rows(3))
    provider = FakeProvider()
    sender = make_craven_sender(state, provider, settings, sb.client())
    await ready(state, settings, sb)
    _, item_id, campaign_id = await stage(state, "info@swan.pub",
                                          body="Saw the new cake counter. One link: https://cravencooling.co.uk/x")
    await sender._drain_due()
    assert len(provider.sent) == 1
    body = provider.sent[0]["body"]
    tok = mint_token(SECRET, "craven", "info@swan.pub", campaign_id)
    assert body.endswith(f"Stop these emails: https://u.test/unsubscribe?t={tok}")
    # two URLs in the body, but the unsubscribe one is exempt from the 1-link budget
    assert body.count("https://") == 2
    assert (await state.get_outbox_item(item_id))["status"] == "sent"
    assert (await state.get_outbox_item(item_id))["body"] == body


@pytest.mark.asyncio
async def test_a_reply_to_a_personal_address_is_allowed_unless_suppressed(state, settings):
    sb = FakeSupabase([{"email": "blocked@swan.pub", "domain": None, "reason": "unsubscribe",
                        "at": "2026-01-01T00:00:00+00:00"}])
    provider = FakeProvider()
    sender = make_craven_sender(state, provider, settings, sb.client())
    # No signed LIA on purpose: replies don't need one.
    _, ok_id, _ = await stage(state, "jo.bloggs@swan.pub", body="Thanks Jo, Tuesday works.", kind="reply")
    _, blocked_id, _ = await stage(state, "blocked@swan.pub", body="Thanks.", kind="reply")
    await sender._drain_due()
    assert [s["to"] for s in provider.sent] == ["jo.bloggs@swan.pub"]
    assert "Stop these emails" not in provider.sent[0]["body"]
    assert "suppressed" in (await state.get_outbox_item(blocked_id))["error"]
