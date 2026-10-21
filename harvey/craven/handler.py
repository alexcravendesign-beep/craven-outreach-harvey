"""Craven's Handler: Harvey's Handler, plus the suppression list.

A bounce or an opt-out is permanent for this tenant, not just for one
Harvey database: both go to the Supabase system of record (and the local
mirror) so no future campaign, run or tool emails that address again.
"""

import logging

from harvey.agents.handler import Handler
from harvey.craven.supabase import SupabaseRest
from harvey.craven.suppression import SuppressionStore

logger = logging.getLogger("harvey.craven.handler")


class CravenHandler(Handler):
    def __init__(self, brain, state, config, env, *, settings=None, supabase=None):
        super().__init__(brain, state, config, env)
        from harvey import craven
        self.settings = settings or craven.SETTINGS
        if self.settings is None:
            raise RuntimeError("Craven plugin not installed: no settings")
        self.supabase = supabase if supabase is not None else SupabaseRest.from_env()
        self.suppression = SuppressionStore(state, self.settings.tenant, self.supabase)

    async def _suppress(self, email: str, reason: str) -> None:
        if not email or "@" not in email:
            return
        try:
            await self.suppression.add(email=email, reason=reason)
            logger.info(f"Suppressed ({reason}): {email}")
        except Exception as e:
            # The local mirror row is written before the remote push, so a
            # Supabase failure never loses the suppression for this engine.
            logger.error(f"Suppression push failed ({reason}) for {email}: {e}")

    async def on_bounce(self, prospect, msg) -> None:
        await self._suppress(prospect.email, "bounce")

    async def on_opt_out(self, prospect, lead_email: str) -> None:
        # The person who wrote (their own address) and the record we emailed.
        await self._suppress(lead_email, "unsubscribe")
        if (prospect.email or "").strip().lower() != (lead_email or "").strip().lower():
            await self._suppress(prospect.email, "unsubscribe")
