import pytest

from harvey import signals as harvey_signals
from harvey.craven import signals as craven_signals
from harvey.craven.signals import CRAVEN_CODES, CRAVEN_SIGNAL_CATALOG, install_signals, upstream_codes_to_reject
from harvey.signals import seed_signal_catalog

DASHBOARD_CATEGORIES = {"discovery", "profile", "people", "verification"}


def test_catalogue_entries_are_well_formed_for_the_dashboard_and_the_free_sweep():
    assert len(CRAVEN_SIGNAL_CATALOG) >= 12
    for sig in CRAVEN_SIGNAL_CATALOG:
        assert sig["code"].isupper() and " " not in sig["code"]
        assert sig["category"] in DASHBOARD_CATEGORIES
        assert sig["value_type"] in {"num", "text", "bool"}
        assert sig["collector"] in {"discover", "fhrs", "companies_house", "profile", "dossier"}
        assert sig["cost_note"].lower().startswith("free"), sig["code"]
        assert len(sig["description"]) > 40
    assert "FHRS_AWAITING_INSPECTION" in CRAVEN_CODES and "MENU_EQUIPMENT_CLASS" in CRAVEN_CODES
    assert next(s for s in CRAVEN_SIGNAL_CATALOG if s["code"] == "CH_INCORPORATED")["confidence_floor"] == 0.8


def test_install_extends_harveys_catalogue_once():
    before = len(harvey_signals.SIGNAL_CATALOG)
    first = install_signals()
    second = install_signals()
    codes = [s["code"] for s in harvey_signals.SIGNAL_CATALOG]
    assert second == 0
    assert set(CRAVEN_CODES) <= set(codes)
    assert len(codes) == len(set(codes))
    assert len(harvey_signals.SIGNAL_CATALOG) == before + first


def test_the_us_catalogue_is_what_gets_rejected_not_ours():
    install_signals()
    rejects = set(upstream_codes_to_reject())
    assert "INCUMBENT_AGENCY" in rejects and "RUNNING_GOOGLE_ADS" in rejects
    assert not (rejects & set(CRAVEN_CODES))
    assert not (rejects & set(craven_signals.KEEP_UPSTREAM))


@pytest.mark.asyncio
async def test_craven_codes_seed_confirm_record_and_cohort(state):
    install_signals()
    await seed_signal_catalog(state)
    rows = {r["code"]: r for r in await state.get_signal_codes()}
    assert rows["FHRS_AWAITING_INSPECTION"]["status"] == "proposed"
    assert rows["FHRS_AWAITING_INSPECTION"]["category"] == "profile"
    # the free sweep the CLI does
    free = [c["code"] for c in await state.get_signal_codes()
            if (c["cost_note"] or "").lower().startswith(("free", "included"))]
    assert set(CRAVEN_CODES) <= set(free)
    for code in CRAVEN_CODES:
        assert await state.set_signal_status(code, "confirmed")
    # an observation with a Craven code passes the governed-vocabulary trigger
    from harvey.models.company import Company
    cid = await state.add_company(Company(name="The Swan", domain="swan.pub", industry="pub"))
    run_id = await state.start_run("fhrs", provider="fhrs")
    n = await state.add_observations([
        {"company_id": cid, "signal_code": "FHRS_AWAITING_INSPECTION", "collector": "fhrs",
         "value_num": 1.0, "value_text": "", "confidence": 1.0, "evidence_url": "https://ratings.food.gov.uk/", "run_id": run_id},
        {"company_id": cid, "signal_code": "MENU_EQUIPMENT_CLASS", "collector": "profile",
         "value_num": None, "value_text": "cake display", "confidence": 0.9, "evidence_url": "https://swan.pub", "run_id": run_id},
    ], run_id=run_id)
    assert n == 2
    assert await state.cohort(require=["FHRS_AWAITING_INSPECTION", "MENU_EQUIPMENT_CLASS"]) == [cid]
    assert await state.cohort(require=["FHRS_AWAITING_INSPECTION"], exclude=["MENU_EQUIPMENT_CLASS"]) == []


@pytest.mark.asyncio
async def test_unknown_code_is_still_refused_by_the_trigger(state):
    install_signals()
    await seed_signal_catalog(state)
    from harvey.models.company import Company
    cid = await state.add_company(Company(name="X", domain="x.co", industry="pub"))
    with pytest.raises(Exception):
        await state.add_observations([{"company_id": cid, "signal_code": "NOT_A_SIGNAL", "collector": "x",
                                       "value_num": 1.0, "value_text": "", "confidence": 1.0, "evidence_url": "", "run_id": ""}])
