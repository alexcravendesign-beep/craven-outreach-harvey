import json

import pytest

from harvey.craven.writer import CravenWriter, draft_one, fact_lines
from harvey.models.company import Company
from harvey.models.prospect import COMPANY_INBOX_SOURCE, Prospect
from tests.test_outbox_native import Cfg, Env


def obs(code, num=None, text="", conf=1.0, at="2026-10-21T10:00:00"):
    return {"signal_code": code, "value_num": num, "value_text": text, "confidence": conf, "observed_at": at}


def test_fact_lines_follow_the_playbook_order_and_hide_forbidden_codes():
    lines, lead = fact_lines([
        obs("VENUE_CATEGORY", text="cafe"),
        obs("MENU_EQUIPMENT_CLASS", text="display_chiller", conf=0.95),
        obs("FHRS_AWAITING_INSPECTION", num=1.0),
        obs("FHRS_HYGIENE_SCORE", num=15.0),      # never in copy
        obs("ROLE_MAILBOX", text="info@x.co"),     # never in copy
        obs("ENERGY_CLAIM", num=1.0),
        obs("FHRS_RATING", num=5.0),
    ])
    assert lead == "FHRS_AWAITING_INSPECTION"
    assert lines[0].startswith("- LEAD:") and "first food hygiene inspection" in lines[0]
    assert any("cake or patisserie display chiller" in l for l in lines)
    assert any("a cafe" in l for l in lines)
    assert any("energy or sustainability" in l for l in lines)
    joined = "\n".join(lines)
    assert "info@x.co" not in joined and "15" not in joined and "HYGIENE" not in joined


def test_weak_signals_do_not_lead_or_appear():
    lines, lead = fact_lines([
        obs("MENU_EQUIPMENT_CLASS", text="cellar_cooling", conf=0.65),   # below the floor
        obs("FHRS_STRUCTURAL_SCORE", num=5.0),                            # fine condition: no angle
        obs("FHRS_INSPECTION_AGE_DAYS", num=200.0),                       # recent: no angle
        obs("FHRS_AWAITING_INSPECTION", num=0.0),
        obs("VENUE_CATEGORY", text="pub"),
    ])
    assert lead == "VENUE_CATEGORY" and len(lines) == 1 and "a pub" in lines[0]


def test_no_signals_means_no_facts():
    assert fact_lines([obs("ROLE_MAILBOX", text="info@x.co")]) == ([], "")


class RecordingBrain:
    def __init__(self, reply=None):
        self.prompts = []
        self.reply = reply or {"subject": "your cake counter", "body": "Saw the cake counter is doing the selling at The Swan. How is the display chiller holding up on a warm afternoon?\n\nAlex Craven, Craven Cooling, Swansea"}
        self.paused_reason = ""
        self.last_reply_empty = False

    def load_skills_for_agent(self, *_):
        return "SKILLS HERE"

    def load_prompt(self, *_a, **_k):
        return "PROMPT HERE"

    async def think_json(self, prompt, session_id=None, agent="", task=""):
        self.prompts.append(prompt)
        return self.reply

    async def think(self, prompt, session_id=None, agent="", task=""):
        return json.dumps(self.reply)


@pytest.mark.asyncio
async def test_craven_writer_grounds_in_observations_and_restores_notes(state):
    cid = await state.add_company(Company(name="The Swan", domain="theswan.pub", industry="cafe"))
    run_id = await state.start_run("fhrs", provider="fhrs")
    await state.upsert_signal_code("FHRS_AWAITING_INSPECTION", label="x", category="profile", value_type="bool")
    await state.upsert_signal_code("MENU_EQUIPMENT_CLASS", label="x", category="profile", value_type="text")
    await state.add_observations([
        {"company_id": cid, "signal_code": "FHRS_AWAITING_INSPECTION", "collector": "fhrs", "value_num": 1.0, "value_text": "", "confidence": 0.9, "evidence_url": "https://ratings.food.gov.uk/business/en-GB/1", "run_id": run_id},
        {"company_id": cid, "signal_code": "MENU_EQUIPMENT_CLASS", "collector": "profile", "value_num": None, "value_text": "display_chiller", "confidence": 0.95, "evidence_url": "https://theswan.pub/menu", "run_id": run_id},
    ], run_id=run_id)
    pid = await state.add_prospect(Prospect(first_name="", last_name="", title="General enquiries mailbox", email="info@theswan.pub",
                                            email_status="guess", company="The Swan", company_id=cid, source=COMPANY_INBOX_SOURCE,
                                            personalization_notes="original notes"))
    brain = RecordingBrain()
    writer = CravenWriter(brain, state, Cfg(), Env())
    writer.skills = ""
    prospect = await state.get_prospect(pid)
    draft = await writer._write_personal_email(prospect)
    assert draft["subject"] == "your cake counter" and draft["lead"] == "FHRS_AWAITING_INSPECTION"
    prompt = brain.prompts[-1]
    assert "VENUE FACTS" in prompt and "LEAD:" in prompt and "first food hygiene inspection" in prompt
    assert "display chiller" in prompt and "info@theswan.pub" in prompt   # upstream names the inbox address itself
    assert prospect.personalization_notes == "original notes"
    assert "original notes" not in prompt.split("VENUE FACTS")[1]


@pytest.mark.asyncio
async def test_draft_one_stages_a_pending_review_row_even_for_a_guess_address(state):
    cid = await state.add_company(Company(name="The Swan", domain="theswan.pub", industry="cafe"))
    await state.upsert_signal_code("VENUE_CATEGORY", label="x", category="discovery", value_type="text")
    run_id = await state.start_run("discover", provider="test")
    await state.add_observations([{"company_id": cid, "signal_code": "VENUE_CATEGORY", "collector": "discover", "value_num": None,
                                   "value_text": "cafe", "confidence": 1.0, "evidence_url": "", "run_id": run_id}], run_id=run_id)
    await state.add_prospect(Prospect(first_name="", last_name="", title="General enquiries mailbox", email="info@theswan.pub",
                                      email_status="guess", company="The Swan", company_id=cid, source=COMPANY_INBOX_SOURCE))
    out = await draft_one(state, RecordingBrain(), Cfg(), Env(), cid)
    assert out and out["to"] == "info@theswan.pub" and out["email_status"] == "guess" and out["lead"] == "VENUE_CATEGORY"
    item = await state.get_outbox_item(out["outbox_id"])
    assert item["status"] == "pending_review" and item["kind"] == "sequence" and item["to_email"] == "info@theswan.pub"
    assert await draft_one(state, RecordingBrain(), Cfg(), Env(), "nope") is None
