"""API tests. No network: tokens are signed with a throwaway key and the database is a fake."""
import os
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

os.environ.update(SUPABASE_URL="https://test.supabase.co", SUPABASE_PUBLISHABLE_KEY="pub",
                  SUPABASE_SECRET_KEY="secret", DAILY_AI_LIMIT="2")

from app import auth, main  # noqa: E402
from app.db import Supabase  # noqa: E402

KEY = ec.generate_private_key(ec.SECP256R1())


class FakeJwks:
    def get_signing_key_from_jwt(self, token):
        class K:
            key = KEY.public_key()
        return K()


class FakeDb:
    def __init__(self):
        self.tables = {"allowed_emails": [{"email": "me@x.com", "is_admin": True}, {"email": "pal@x.com", "is_admin": False}], "ai_usage": [], "ticks": [], "goals": []}
        self.deleted = None

    async def admin_get(self, table, params=None):
        rows = self.tables[table]
        if table == "allowed_emails" and "email" in params:
            return [r for r in rows if r["email"] == params["email"].removeprefix("eq.")]
        return rows

    async def admin_insert(self, table, rows):
        self.tables[table] += rows
        return rows

    async def admin_delete(self, table, params):
        gone = params["email"].removeprefix("eq.")
        self.tables[table] = [r for r in self.tables[table] if r["email"] != gone]

    async def admin_upsert(self, table, rows, on_conflict):
        self.tables[table] = list(rows)
        return rows

    async def get(self, table, token, params=None):
        return self.tables[table]

    async def upsert(self, table, token, rows, on_conflict):
        self.tables[table] = rows
        return rows

    async def delete(self, table, token, params):
        self.deleted = params


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth, "_jwks", lambda url: FakeJwks())
    db = FakeDb()
    main.app.dependency_overrides[Supabase.dep] = lambda: db
    yield TestClient(main.app), db
    main.app.dependency_overrides.clear()


def token(email="me@x.com", aud="authenticated", exp=3600):
    claims = {"sub": "u1", "aud": aud, "iss": "https://test.supabase.co/auth/v1",
              "exp": int(time.time()) + exp}
    if email:
        claims["email"] = email
    return {"authorization": "Bearer " + jwt.encode(claims, KEY, algorithm="ES256")}


def test_no_token_rejected(client):
    assert client[0].get("/ticks").status_code == 401


def test_expired_and_wrong_audience_rejected(client):
    c, _ = client
    assert c.get("/ticks", headers=token(exp=-10)).status_code == 401
    assert c.get("/ticks", headers=token(aud="other")).status_code == 401


def test_forged_signature_rejected(client):
    c, _ = client
    forged = jwt.encode({"sub": "u1", "aud": "authenticated", "iss": "https://test.supabase.co/auth/v1",
                         "exp": int(time.time()) + 99}, ec.generate_private_key(ec.SECP256R1()), algorithm="ES256")
    assert c.get("/ticks", headers={"authorization": "Bearer " + forged}).status_code == 401


def test_me_reports_allowlist(client):
    c, _ = client
    assert c.get("/me", headers=token()).json()["ai_allowed"] is True
    assert c.get("/me", headers=token(email="other@x.com")).json()["ai_allowed"] is False


def test_ticks_tick_and_untick(client):
    c, db = client
    r = c.post("/ticks", headers=token(), json=[{"id": "w1t1", "done_on": "2026-10-26"}, {"id": "w1t2"}])
    assert r.json() == {"saved": 1, "removed": 1}
    assert db.deleted == {"id": 'in.("w1t2")'}


def test_goals_validation(client):
    c, _ = client
    assert c.put("/goals", headers=token(), json={"py": 6}).status_code == 200
    assert c.put("/goals", headers=token(), json={"cooking": 6}).status_code == 422
    assert c.put("/goals", headers=token(), json={"py": 99}).status_code == 422


def test_ai_blocked_without_email_and_unlisted(client):
    c, _ = client
    assert c.get("/ai/quota", headers=token(email=None)).status_code == 403
    assert c.get("/ai/quota", headers=token(email="other@x.com")).status_code == 403
    assert c.get("/ai/quota", headers=token()).json() == {"limit": 2, "used": 0, "left": 2}


@pytest.mark.anyio
async def test_quota_stops_at_limit():
    db = FakeDb()
    u = auth.User(id="u1", email="me@x.com", token="t")
    assert await main.spend_ai_call(u, db, 2) == 1
    assert await main.spend_ai_call(u, db, 2) == 0
    with pytest.raises(main.HTTPException) as e:
        await main.spend_ai_call(u, db, 2)
    assert e.value.status_code == 429


def test_admin_screen_is_admin_only(client):
    c, _ = client
    assert c.get("/me", headers=token()).json()["is_admin"] is True
    assert c.get("/me", headers=token(email="pal@x.com")).json()["is_admin"] is False
    assert c.get("/admin/emails", headers=token(email="pal@x.com")).status_code == 403
    assert c.get("/admin/emails", headers=token(email="stranger@x.com")).status_code == 403
    assert len(c.get("/admin/emails", headers=token()).json()) == 2


def test_admin_add_and_remove(client):
    c, db = client
    assert c.post("/admin/emails", headers=token(), json={"email": " New@X.com "}).json() == {"email": "new@x.com"}
    assert c.post("/admin/emails", headers=token(), json={"email": "not-an-email"}).status_code == 422
    c.post("/admin/emails", headers=token(), json={"email": "me@x.com"})   # existing admin: untouched
    assert [r for r in db.tables["allowed_emails"] if r["email"] == "me@x.com"] == [{"email": "me@x.com", "is_admin": True}]
    assert c.delete("/admin/emails/new@x.com", headers=token()).json() == {"removed": "new@x.com"}
    assert c.delete("/admin/emails/me@x.com", headers=token()).status_code == 422
    assert c.post("/admin/emails", headers=token(email="pal@x.com"), json={"email": "a@b.co"}).status_code == 403
