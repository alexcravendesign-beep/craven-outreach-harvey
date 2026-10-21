"""Craven's Sender: Harvey's Sender with the compliance spine attached.

Each drain first refreshes the suppression mirror from the system of record
(incremental; a failure is logged and the mirror ages toward fail-closed),
then runs Harvey's drain with the UK gate, the unsubscribe footer and the
weekday send window in place.
"""

import logging
import os

from harvey.agents.sender import Sender
from harvey.craven import unsubscribe
from harvey.craven.gate_uk import UKGate
from harvey.craven.lawful_basis import LawfulBasisStore
from harvey.craven.schema import ensure_schema
from harvey.craven.supabase import SupabaseRest
from harvey.craven.suppression import SuppressionStore
from harvey.craven.window import make_cold_send_window

logger = logging.getLogger("harvey.craven.sender")

SECRET_ENV = ("UNSUB_HMAC_SECRET", "OE_SECRET")


def unsubscribe_secret(env=None) -> str:
    env = env if env is not None else os.environ
    for name in SECRET_ENV:
        if env.get(name, "").strip():
            return env[name].strip()
    return ""


class CravenSender(Sender):
    def __init__(self, brain, state, config, env, *, settings=None, supabase=None, secret=None):
        super().__init__(brain, state, config, env)
        from harvey import craven
        self.settings = settings or craven.SETTINGS
        if self.settings is None:
            raise RuntimeError("Craven plugin not installed: no settings")
        self.supabase = supabase if supabase is not None else SupabaseRest.from_env()
        self.suppression = SuppressionStore(state, self.settings.tenant, self.supabase)
        self.lawful_basis = LawfulBasisStore(state, self.settings.tenant, self.supabase)
        self.uk_gate = UKGate(settings=self.settings, suppression=self.suppression,
                              lawful_basis=self.lawful_basis, state=state,
                              secret=secret if secret is not None else unsubscribe_secret())
        self.extra_gates.append(self.uk_gate)
        self.body_finalizers.append(self.uk_gate.finalize_body)
        self.cold_send_window = make_cold_send_window(self.settings.deliverability)
        base = (self.settings.compliance.unsubscribe_base_url or "").strip()
        if base:
            self.exempt_link_prefixes = (unsubscribe.unsubscribe_url(base, ""),)
        self._schema_ready = False

    async def _prepare(self):
        if not self._schema_ready:
            await ensure_schema(self.state)
            self._schema_ready = True
        try:
            await self.suppression.sync()
        except Exception as e:
            logger.error(f"Suppression sync failed: {e}. The mirror will fail closed when stale.")

    async def _drain_due(self):
        await self._prepare()
        await super()._drain_due()
