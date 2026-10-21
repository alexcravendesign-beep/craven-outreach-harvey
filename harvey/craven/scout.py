"""Craven's Scout: venues, not people.

Harvey's Scout hunts named decision-makers (team pages, web search, Hunter).
Craven writes to a venue's shared mailbox, so this Scout does one thing: for
every company that carries a ROLE_MAILBOX observation and has no prospect
yet, verify the mailbox and record it as a nameless prospect. Suppressed
addresses are skipped and the company marked checked, so it is never
retried every cycle.
"""

import logging

from harvey.agents.scout import Scout
from harvey.craven import compliance
from harvey.craven.suppression import SuppressionStore
from harvey.craven.verify import verify_role_email
from harvey.models.prospect import Prospect

logger = logging.getLogger("harvey.craven.scout")

SOURCE = "overture_role_mailbox"
TITLE = "General enquiries mailbox"


class CravenScout(Scout):
    def __init__(self, brain, state, config, env, *, settings=None, batch: int = 50):
        super().__init__(brain, state, config, env)
        from harvey import craven
        self.settings = settings or craven.SETTINGS
        if self.settings is None:
            raise RuntimeError("Craven plugin not installed: no settings")
        self.batch = batch
        self.suppression = SuppressionStore(state, self.settings.tenant, None)
        self._verify = verify_role_email  # swapped in tests

    async def companies_with_mailbox_and_no_prospect(self, limit: int) -> list[dict]:
        """Companies carrying a ROLE_MAILBOX observation and zero prospects,
        not checked recently. Unlike Harvey's backfill, a domain is not
        required: a venue known only by its Facebook page still has a mailbox."""
        async with self.state._connect() as db:
            async with db.execute(
                """SELECT c.id, c.name, c.industry, c.website,
                          (SELECT o.value_text FROM observations o
                           WHERE o.company_id = c.id AND o.signal_code = 'ROLE_MAILBOX'
                           ORDER BY o.observed_at DESC LIMIT 1) AS mailbox
                   FROM companies c
                   WHERE NOT EXISTS (SELECT 1 FROM prospects p WHERE p.company_id = c.id)
                     AND EXISTS (SELECT 1 FROM observations o WHERE o.company_id = c.id
                                 AND o.signal_code = 'ROLE_MAILBOX')
                     AND (c.prospects_checked_at IS NULL
                          OR c.prospects_checked_at < datetime('now', '-14 days'))
                   ORDER BY c.prospects_checked_at IS NOT NULL, c.prospects_checked_at, c.created_at
                   LIMIT ?""", (int(limit),)) as cur:
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in await cur.fetchall()]

    async def run(self):
        logger.info("Scout (Craven): venues with a published mailbox and no prospect yet...")
        found = skipped = 0
        role_parts = self.settings.compliance.role_local_parts
        for c in await self.companies_with_mailbox_and_no_prospect(self.batch):
            email = (c.get("mailbox") or "").strip().lower()
            try:
                if not email or not compliance.is_role_address(email, role_parts):
                    skipped += 1
                elif await self.suppression.is_suppressed(email):
                    skipped += 1
                elif await self.state.prospect_exists(email=email):
                    skipped += 1
                else:
                    result = await self._verify(email, self.env, role_parts)
                    if result.status == "invalid":
                        skipped += 1
                    else:
                        await self.state.add_prospect(Prospect(
                            first_name="", last_name="", title=TITLE,
                            email=email, email_status=result.status,
                            email_verified=(result.status == "verified"),
                            company=c.get("name") or "", company_id=c["id"],
                            industry=c.get("industry") or "", source=SOURCE,
                            source_url=c.get("website") or "",
                            personalization_notes="Venue mailbox; write to the business, not a person.",
                        ))
                        found += 1
                        logger.info(f"Scout (Craven): {c.get('name')} -> {email} [{result.status}]")
            except Exception as e:
                logger.warning(f"Scout (Craven): {c.get('name')}: {e}")
            finally:
                await self.state.mark_prospects_checked(c["id"])
        await self.state.log_action(action_type="prospect", agent="scout",
                                    details={"prospects_found": found, "skipped": skipped, "source": SOURCE})
        logger.info(f"Scout (Craven): {found} prospect(s) added, {skipped} skipped.")
