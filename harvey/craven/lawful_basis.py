"""The lawful-basis record (the LIA) cold email is sent under.

A campaign may only send when the configured ``lia_ref`` names a record
that a human has SIGNED. The signature is recorded locally by
``harvey-craven lia sign`` and mirrored to Supabase ``oe_lawful_basis``
(v1's table: tenant, ref_id, basis, assessment).
"""

from datetime import datetime, timezone

from harvey.craven.supabase import SupabaseRest


class LawfulBasisStore:
    def __init__(self, state, tenant: str, supabase: SupabaseRest | None = None):
        self.state = state
        self.tenant = tenant
        self.supabase = supabase

    async def get(self, ref: str) -> dict | None:
        async with self.state._connect() as db:
            db.row_factory = None
            async with db.execute(
                "SELECT ref, basis, assessment, doc_url, signed_by, signed_at FROM craven_lawful_basis "
                "WHERE ref = ?", (ref,)) as cur:
                row = await cur.fetchone()
        if not row:
            return None
        keys = ("ref", "basis", "assessment", "doc_url", "signed_by", "signed_at")
        return dict(zip(keys, row))

    async def is_signed(self, ref: str) -> bool:
        rec = await self.get(ref) if ref else None
        return bool(rec and rec["signed_by"] and rec["signed_at"])

    async def sign(self, ref: str, signed_by: str, doc_url: str = "",
                   basis: str = "legitimate_interest", assessment: str = "") -> dict:
        if not ref or not signed_by.strip():
            raise ValueError("a lawful-basis signature needs a ref and a signer")
        now = datetime.now(timezone.utc).isoformat()
        async with self.state._connect() as db:
            await db.execute(
                "INSERT INTO craven_lawful_basis (ref, basis, assessment, doc_url, signed_by, signed_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(ref) DO UPDATE SET basis = excluded.basis, "
                "assessment = CASE WHEN excluded.assessment != '' THEN excluded.assessment ELSE assessment END, "
                "doc_url = CASE WHEN excluded.doc_url != '' THEN excluded.doc_url ELSE doc_url END, "
                "signed_by = excluded.signed_by, signed_at = excluded.signed_at",
                (ref, basis, assessment, doc_url, signed_by.strip(), now))
            await db.commit()
        if self.supabase is not None:
            await self.supabase.upsert(
                "oe_lawful_basis",
                [{"tenant": self.tenant, "ref_id": ref, "basis": basis,
                  "assessment": (assessment or f"signed by {signed_by.strip()} {now}; {doc_url}").strip()}],
                on_conflict="tenant,ref_id", resolution="merge-duplicates")
        return await self.get(ref)

    async def record_campaign(self, campaign_id: str, lia_ref: str) -> None:
        async with self.state._connect() as db:
            await db.execute(
                "INSERT INTO craven_campaign_basis (campaign_id, lia_ref) VALUES (?, ?) "
                "ON CONFLICT(campaign_id) DO UPDATE SET lia_ref = excluded.lia_ref", (campaign_id, lia_ref))
            await db.commit()
