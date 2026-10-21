"""A small PostgREST client for the self-hosted Supabase that is the system
of record (POIs, suppression, lawful basis). Service key stays server-side;
responses never carry it. Pages with Range headers (PostgREST caps a page
at 1,000 rows by default).
"""

import logging
import os

import httpx

logger = logging.getLogger("harvey.craven.supabase")

PAGE = 1000
TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


class SupabaseRest:
    def __init__(self, base_url: str, service_key: str, transport=None):
        self.base_url = base_url.rstrip("/")
        self.key = service_key
        self._transport = transport  # tests inject an httpx.MockTransport

    @classmethod
    def from_env(cls, env=None) -> "SupabaseRest | None":
        env = env if env is not None else os.environ
        url = (env.get("SUPABASE_URL") or env.get("OE_SUPABASE_URL") or "").strip()
        key = (env.get("SUPABASE_SERVICE_KEY") or env.get("OE_SUPABASE_KEY") or "").strip()
        if not url or not key:
            return None
        return cls(url, key)

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"apikey": self.key, "Authorization": f"Bearer {self.key}",
             "Content-Type": "application/json"}
        if extra:
            h.update(extra)
        return h

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT, transport=self._transport)

    async def select(self, table: str, params: dict, page: int = PAGE) -> list[dict]:
        """Every row matching params, fetched a page at a time."""
        rows: list[dict] = []
        start = 0
        async with self._client() as client:
            while True:
                headers = self._headers({"Range-Unit": "items", "Range": f"{start}-{start + page - 1}"})
                r = await client.get(f"{self.base_url}/rest/v1/{table}", params=params, headers=headers)
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"supabase GET {table}: HTTP {r.status_code} {r.text[:140]}")
                batch = r.json()
                rows.extend(batch)
                if len(batch) < page:
                    return rows
                start += page

    async def count(self, table: str, params: dict) -> int:
        async with self._client() as client:
            headers = self._headers({"Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"})
            r = await client.get(f"{self.base_url}/rest/v1/{table}", params={**params, "select": "id"},
                                 headers=headers)
            if r.status_code not in (200, 206):
                raise RuntimeError(f"supabase COUNT {table}: HTTP {r.status_code} {r.text[:140]}")
            total = (r.headers.get("content-range") or "/0").split("/")[-1]
            return int(total) if total.isdigit() else 0

    async def upsert(self, table: str, rows: list[dict], on_conflict: str,
                     resolution: str = "ignore-duplicates") -> None:
        if not rows:
            return
        async with self._client() as client:
            for i in range(0, len(rows), PAGE):
                r = await client.post(
                    f"{self.base_url}/rest/v1/{table}", params={"on_conflict": on_conflict},
                    headers=self._headers({"Prefer": f"resolution={resolution}"}),
                    json=rows[i:i + PAGE],
                )
                if r.status_code not in (200, 201, 204, 409):
                    raise RuntimeError(f"supabase POST {table}: HTTP {r.status_code} {r.text[:140]}")
