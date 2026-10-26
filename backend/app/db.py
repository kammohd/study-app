"""Thin client for Supabase's REST API (PostgREST).

Two ways to call it:
  * as the user  - forwards their JWT, so row-level security limits them to their own rows
  * as admin     - uses the secret key, which bypasses RLS; for allowlist, quota and question bank
"""
import httpx
from fastapi import Depends, HTTPException

from .config import Settings, get_settings


class Supabase:
    def __init__(self, settings: Settings):
        self.s = settings
        self.base = f"{settings.supabase_url}/rest/v1"

    @classmethod
    def dep(cls, settings: Settings = Depends(get_settings)) -> "Supabase":
        return cls(settings)

    def _user_headers(self, token: str) -> dict:
        return {"apikey": self.s.supabase_publishable_key, "Authorization": f"Bearer {token}"}

    def _admin_headers(self) -> dict:
        if not self.s.supabase_secret_key:
            raise HTTPException(500, "Server is missing SUPABASE_SECRET_KEY")
        return {"apikey": self.s.supabase_secret_key}

    async def _send(self, method: str, table: str, headers: dict, params=None, json=None, prefer=None):
        if prefer:
            headers = {**headers, "Prefer": prefer}
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.request(method, f"{self.base}/{table}", headers=headers, params=params, json=json)
        if r.status_code >= 400:
            raise HTTPException(502, f"Database error {r.status_code}: {r.text[:200]}")
        return r.json() if r.content else None

    # as the user (RLS applies)
    async def get(self, table, token, params=None):
        return await self._send("GET", table, self._user_headers(token), params)

    async def upsert(self, table, token, rows, on_conflict):
        return await self._send("POST", table, self._user_headers(token), {"on_conflict": on_conflict}, rows,
                                "resolution=merge-duplicates,return=representation")

    async def insert(self, table, token, rows):
        return await self._send("POST", table, self._user_headers(token), None, rows, "return=representation")

    async def delete(self, table, token, params):
        return await self._send("DELETE", table, self._user_headers(token), params)

    # as the backend (RLS bypassed)
    async def admin_get(self, table, params=None):
        return await self._send("GET", table, self._admin_headers(), params)

    async def admin_insert(self, table, rows):
        return await self._send("POST", table, self._admin_headers(), None, rows, "return=representation")

    async def admin_delete(self, table, params):
        return await self._send("DELETE", table, self._admin_headers(), params)

    async def admin_upsert(self, table, rows, on_conflict):
        return await self._send("POST", table, self._admin_headers(), {"on_conflict": on_conflict}, rows,
                                "resolution=merge-duplicates,return=representation")
