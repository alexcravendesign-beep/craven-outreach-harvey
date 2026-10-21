"""The plugin's own SQLite tables, created idempotently.

Never appended to Harvey's state.MIGRATIONS (upstream appends there too and
``user_version`` numbers would collide on every rebase). Versioned instead by
the ``craven_schema_version`` key in Harvey's settings table.
"""

import logging

logger = logging.getLogger("harvey.craven.schema")

SCHEMA_VERSION = 1

STATEMENTS = [
    # One row per suppressed address or domain. value is lower-cased.
    """CREATE TABLE IF NOT EXISTS craven_suppressions (
        kind TEXT NOT NULL CHECK (kind IN ('email', 'domain')),
        value TEXT NOT NULL,
        reason TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'supabase',
        added_at TEXT,
        synced_at TEXT,
        PRIMARY KEY (kind, value)
    )""",
    # The written lawful-basis record (LIA) a tenant sends under. signed_by
    # empty = draft; the gate accepts only signed records.
    """CREATE TABLE IF NOT EXISTS craven_lawful_basis (
        ref TEXT PRIMARY KEY,
        basis TEXT NOT NULL DEFAULT 'legitimate_interest',
        assessment TEXT NOT NULL DEFAULT '',
        doc_url TEXT NOT NULL DEFAULT '',
        signed_by TEXT NOT NULL DEFAULT '',
        signed_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
    # Which LIA a campaign was sent under (campaigns has no spare column and
    # its update path whitelists columns).
    """CREATE TABLE IF NOT EXISTS craven_campaign_basis (
        campaign_id TEXT PRIMARY KEY,
        lia_ref TEXT NOT NULL,
        recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""",
]


async def ensure_schema(state) -> int:
    """Create the tables if missing; record the version. Safe on every start."""
    async with state._connect() as db:
        for stmt in STATEMENTS:
            await db.execute(stmt)
        await db.commit()
    current = await state.get_setting("craven_schema_version", "")
    if current != str(SCHEMA_VERSION):
        await state.set_setting("craven_schema_version", str(SCHEMA_VERSION))
        logger.info(f"Craven schema at version {SCHEMA_VERSION}")
    return SCHEMA_VERSION
