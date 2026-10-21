"""The per-cycle enrichment ride-along: FHRS matching and Companies House
lookups for a bounded batch, free and model-less, beside Harvey's profiler."""

import logging

from harvey.craven.collectors.companies_house import enrich_companies_house
from harvey.craven.collectors.fhrs import enrich_fhrs
from harvey.craven.schema import ensure_schema
from harvey.craven.supabase import SupabaseRest

logger = logging.getLogger("harvey.craven.enrich")


async def enrichment_cycle(state, config, *, settings=None, batch: int = 50) -> dict:
    from harvey import craven
    settings = settings or craven.SETTINGS
    await ensure_schema(state)
    sb = SupabaseRest.from_env()
    out = {}
    try:
        out["fhrs"] = await enrich_fhrs(state, settings, sb, limit=batch)
    except Exception as e:
        logger.error(f"FHRS enrichment failed this cycle: {e}")
    try:
        out["companies_house"] = await enrich_companies_house(state, settings, sb, limit=min(batch, 40))
    except Exception as e:
        logger.error(f"Companies House enrichment failed this cycle: {e}")
    return out


def cycle_hook(state, config):
    return enrichment_cycle(state, config)
