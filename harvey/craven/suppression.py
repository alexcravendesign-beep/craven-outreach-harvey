"""The suppression list: who must never be emailed.

Supabase ``oe_suppression`` is the system of record (shared with the v1
engine and the public unsubscribe service). Harvey keeps a local mirror in
``craven_suppressions`` so the gate never depends on the network at send
time, and FAILS CLOSED when the mirror is older than the configured age.
Rows are never deleted anywhere; sync is incremental on the ``at`` column.
"""

import logging
from datetime import datetime, timedelta, timezone

from harvey.craven.supabase import SupabaseRest

logger = logging.getLogger("harvey.craven.suppression")

SYNCED_AT_KEY = "craven_suppression_synced_at"
WATERMARK_KEY = "craven_suppression_watermark"   # latest remote ``at`` seen


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(email: str) -> str:
    return (email or "").strip().lower()


class SuppressionStore:
    def __init__(self, state, tenant: str, supabase: SupabaseRest | None):
        self.state = state
        self.tenant = tenant
        self.supabase = supabase

    # ── reads ──

    async def is_suppressed(self, email: str) -> bool:
        e = _norm(email)
        domain = e.split("@", 1)[1] if "@" in e else ""
        async with self.state._connect() as db:
            async with db.execute(
                "SELECT 1 FROM craven_suppressions WHERE (kind = 'email' AND value = ?) "
                "OR (kind = 'domain' AND value = ?) LIMIT 1", (e, domain)) as cur:
                return await cur.fetchone() is not None

    async def count(self) -> int:
        async with self.state._connect() as db:
            async with db.execute("SELECT COUNT(*) FROM craven_suppressions") as cur:
                return (await cur.fetchone())[0]

    async def synced_at(self) -> datetime | None:
        raw = await self.state.get_setting(SYNCED_AT_KEY, "")
        return datetime.fromisoformat(raw) if raw else None

    async def is_stale(self, max_age_hours: int) -> bool:
        """True when the mirror has never synced or is older than allowed."""
        when = await self.synced_at()
        if when is None:
            return True
        return _now() - when > timedelta(hours=max_age_hours)

    # ── writes ──

    async def _put_local(self, rows: list[dict], source: str) -> int:
        """Upsert mirror rows. rows: {kind, value, reason, added_at}."""
        if not rows:
            return 0
        stamp = _now().isoformat()
        async with self.state._connect() as db:
            await db.executemany(
                "INSERT INTO craven_suppressions (kind, value, reason, source, added_at, synced_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(kind, value) DO UPDATE SET synced_at = excluded.synced_at",
                [(r["kind"], r["value"], r.get("reason") or "manual", source, r.get("added_at"), stamp)
                 for r in rows])
            await db.commit()
        return len(rows)

    async def add(self, email: str = "", reason: str = "manual", domain: str = "") -> None:
        """Suppress an address or a whole domain, locally and in Supabase."""
        rows = []
        if email:
            rows.append({"kind": "email", "value": _norm(email), "reason": reason, "added_at": _now().isoformat()})
        if domain:
            rows.append({"kind": "domain", "value": domain.strip().lower(), "reason": reason,
                         "added_at": _now().isoformat()})
        await self._put_local(rows, source="harvey")
        if self.supabase is not None:
            await self.supabase.upsert(
                "oe_suppression",
                [{"tenant": self.tenant, "email": _norm(email) or None,
                  "domain": domain.strip().lower() or None, "reason": reason}],
                on_conflict="tenant,email,domain,reason")

    async def sync(self) -> int:
        """Pull rows newer than the watermark from Supabase. Returns rows merged.
        Raises when the system of record can't be read: the caller logs it and
        the mirror ages toward fail-closed; nothing is guessed."""
        if self.supabase is None:
            raise RuntimeError("suppression sync: SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
        watermark = await self.state.get_setting(WATERMARK_KEY, "")
        params = {"tenant": f"eq.{self.tenant}", "select": "email,domain,reason,at", "order": "at.asc"}
        if watermark:
            params["at"] = f"gt.{watermark}"
        remote = await self.supabase.select("oe_suppression", params)
        rows = []
        latest = watermark
        for r in remote:
            if r.get("email"):
                rows.append({"kind": "email", "value": _norm(r["email"]), "reason": r.get("reason"),
                             "added_at": r.get("at")})
            if r.get("domain"):
                rows.append({"kind": "domain", "value": r["domain"].strip().lower(), "reason": r.get("reason"),
                             "added_at": r.get("at")})
            if r.get("at") and (not latest or r["at"] > latest):
                latest = r["at"]
        merged = await self._put_local(rows, source="supabase")
        if latest and latest != watermark:
            await self.state.set_setting(WATERMARK_KEY, latest)
        await self.state.set_setting(SYNCED_AT_KEY, _now().isoformat())
        logger.info(f"Suppression sync: {merged} row(s) merged (mirror now {await self.count()}).")
        return merged
