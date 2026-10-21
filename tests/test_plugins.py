"""The generic plugin seam: ``plugins:`` in the config loads extension
modules once, and those modules can substitute agents, attach discovery
facts, add send-time gates and finish the outgoing body.

A fork keeps its additions in such a module; Harvey's core stays shared.
"""

import os
import sys
import tempfile
import textwrap
import types
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from harvey import config as config_mod
from harvey.collectors.discover import Business, _observations
from harvey.gate import GateResult
from harvey.models.campaign import Campaign, EmailStep
from harvey.models.prospect import Prospect
from harvey.state import StateManager
from tests.test_outbox_native import Cfg, Env, FakeProvider, make_sender


# ── loading ──


def _write_config(tmp_path, plugins, extra=""):
    path = tmp_path / "harvey.yaml"
    path.write_text(textwrap.dedent(f"""
        persona: {{name: H, company: X, role: BD, email: h@x.co, linkedin: "", tone: direct}}
        product: {{name: P, description: d, pricing: "$", key_benefits: [b], objection_responses: {{}}}}
        icp: {{industries: [a], company_size: "1-10", titles: [Owner], geography: [UK]}}
        plugins: {plugins}
        {extra}
    """), encoding="utf-8")
    return str(path)


def _fake_module(name, install=None):
    mod = types.ModuleType(name)
    if install is not None:
        mod.install = install
    sys.modules[name] = mod
    return mod


@pytest.fixture(autouse=True)
def _fresh_plugin_registry(monkeypatch):
    monkeypatch.setattr(config_mod, "_INSTALLED_PLUGINS", set())
    yield
    for name in list(sys.modules):
        if name.startswith("fake_plugin_"):
            del sys.modules[name]


def test_plugins_default_to_none(tmp_path):
    cfg = config_mod.load_config(_write_config(tmp_path, "[]"))
    assert cfg.plugins == []


def test_plugin_install_sees_config_and_its_own_raw_section(tmp_path):
    seen = {}
    _fake_module("fake_plugin_a", install=lambda cfg, raw: seen.update(cfg=cfg, raw=raw))
    cfg = config_mod.load_config(_write_config(
        tmp_path, "[fake_plugin_a]", extra="fake_plugin_a: {tenant: craven}"))
    assert seen["cfg"] is cfg
    assert seen["raw"]["fake_plugin_a"] == {"tenant": "craven"}
    # HarveyConfig itself ignores the plugin's section (no extra= forbid).
    assert not hasattr(cfg, "fake_plugin_a")


def test_plugin_installs_once_per_process(tmp_path):
    calls = []
    _fake_module("fake_plugin_b", install=lambda cfg, raw: calls.append(1))
    path = _write_config(tmp_path, "[fake_plugin_b]")
    config_mod.load_config(path)
    config_mod.load_config(path)
    assert calls == [1]


def test_plugin_without_install_is_a_config_error(tmp_path):
    _fake_module("fake_plugin_c")
    with pytest.raises(config_mod.ConfigError, match="no install"):
        config_mod.load_config(_write_config(tmp_path, "[fake_plugin_c]"))


def test_missing_plugin_module_is_a_config_error(tmp_path):
    with pytest.raises(config_mod.ConfigError, match="failed to load"):
        config_mod.load_config(_write_config(tmp_path, "[fake_plugin_does_not_exist]"))


def test_failing_install_is_a_config_error_not_a_silent_skip(tmp_path):
    def boom(cfg, raw):
        raise RuntimeError("nope")
    _fake_module("fake_plugin_d", install=boom)
    with pytest.raises(config_mod.ConfigError, match="RuntimeError: nope"):
        config_mod.load_config(_write_config(tmp_path, "[fake_plugin_d]"))
    assert "fake_plugin_d" not in config_mod._INSTALLED_PLUGINS


# ── seams ──


def test_agent_overrides_registry_exists_and_is_empty_by_default():
    from harvey import main
    assert main.AGENT_OVERRIDES == {}


def test_profile_collector_class_is_the_default_collector():
    from harvey.collectors import profile
    assert profile.COLLECTOR_CLASS is profile.ProfileCollector


def test_provider_attached_observations_are_recorded_only_when_confirmed():
    biz = Business(name="The Swan", external_id="overture:1", source_url="https://swan.pub",
                   observations=[
                       {"signal_code": "ROLE_MAILBOX", "value_text": "info@swan.pub"},
                       {"signal_code": "VENUE_CATEGORY", "value_text": "pub", "confidence": 0.9},
                       {"signal_code": "NOT_CONFIRMED", "value_text": "x"},
                   ])
    rows = _observations(biz, "c1", confirmed={"ROLE_MAILBOX", "VENUE_CATEGORY"}, run_id="r1")
    by_code = {r["signal_code"]: r for r in rows}
    assert set(by_code) == {"ROLE_MAILBOX", "VENUE_CATEGORY"}
    assert by_code["ROLE_MAILBOX"]["value_text"] == "info@swan.pub"
    assert by_code["ROLE_MAILBOX"]["company_id"] == "c1"
    assert by_code["ROLE_MAILBOX"]["run_id"] == "r1"
    assert by_code["ROLE_MAILBOX"]["collector"] == "discover"
    assert by_code["ROLE_MAILBOX"]["evidence_url"] == "https://swan.pub"
    assert by_code["ROLE_MAILBOX"]["confidence"] == 1.0
    assert by_code["VENUE_CATEGORY"]["confidence"] == 0.9


# ── sender hooks ──


def _now_iso():
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


@pytest_asyncio.fixture
async def state():
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(os.path.join(tmpdir, "test.db"))
        await sm.init_db()
        yield sm


async def _approved_cold_email(state, email="info@swan.pub"):
    pid = await state.add_prospect(Prospect(
        first_name="", last_name="", title="General enquiries", company="The Swan",
        email=email, email_status="verified", email_verified=True, status="new"))
    campaign = Campaign(id="", name="c", channel="email", prospect_ids=[pid], status="draft",
                        sequence=[EmailStep(step=1, subject="hi", body="Short note.", delay_days=0)])
    campaign.id = await state.add_campaign(campaign)
    item_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id=campaign.id, kind="sequence", step=1,
        to_email=email, subject="hi", body="Short note.", send_at=_now_iso(),
        status="approved", provider="fake")
    return pid, item_id


@pytest.mark.asyncio
async def test_extra_gate_refusal_blocks_the_send_with_its_reason(state):
    provider = FakeProvider()
    sender = make_sender(state, provider)
    _, item_id = await _approved_cold_email(state)

    async def role_only(item, prospect, body):
        return GateResult(ok=False, reasons=["personal address blocked"])
    sender.extra_gates.append(role_only)

    await sender._drain_due()
    assert provider.sent == []
    item = await state.get_outbox_item(item_id)
    assert item["status"] == "failed"
    assert "personal address blocked" in item["error"]


@pytest.mark.asyncio
async def test_extra_gate_sees_the_finished_body_and_may_pass(state):
    provider = FakeProvider()
    sender = make_sender(state, provider)
    await _approved_cold_email(state)
    seen = {}

    async def record(item, prospect, body):
        seen["body"] = body
        return None  # no opinion
    sender.body_finalizers.append(lambda body, item: body + "\n\nUnsubscribe: https://u.example/t/abc")
    sender.extra_gates.append(record)

    await sender._drain_due()
    assert len(provider.sent) == 1
    assert provider.sent[0]["body"].endswith("Unsubscribe: https://u.example/t/abc")
    assert seen["body"] == provider.sent[0]["body"]
    # The sent row holds what actually left.
    sent = await state.get_outbox(status="sent")
    assert sent[0]["body"] == provider.sent[0]["body"]


@pytest.mark.asyncio
async def test_closed_cold_window_holds_cold_email_but_not_replies(state):
    provider = FakeProvider()
    sender = make_sender(state, provider)
    pid, cold_id = await _approved_cold_email(state)
    reply_id = await state.add_outbox_item(
        prospect_id=pid, campaign_id="", kind="reply", to_email="jo@swan.pub",
        subject="Re: hi", body="Thanks Jo, Tuesday works.", send_at=_now_iso(),
        status="approved", provider="fake")
    sender.cold_send_window = lambda: False

    await sender._drain_due()
    assert [s["to"] for s in provider.sent] == ["jo@swan.pub"]
    assert (await state.get_outbox_item(cold_id))["status"] == "approved"
    assert (await state.get_outbox_item(reply_id))["status"] == "sent"


@pytest.mark.asyncio
async def test_reply_may_go_to_the_person_who_wrote_not_the_shared_mailbox(state):
    from harvey.gate import pre_send_check
    prospect = Prospect(first_name="", last_name="", title="", company="The Swan",
                        email="info@swan.pub", email_status="verified", email_verified=True)
    cold = pre_send_check("jo@swan.pub", "hi", "note", prospect=prospect, kind="sequence")
    reply = pre_send_check("jo@swan.pub", "Re: hi", "note", prospect=prospect, kind="reply")
    assert not cold and "does not match" in " ".join(cold.reasons)
    assert reply
