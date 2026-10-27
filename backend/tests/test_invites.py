"""Invite request tests. No network: the database and the push services are fakes."""
import os
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.update(SUPABASE_URL="https://test.supabase.co", SUPABASE_PUBLISHABLE_KEY="pub", SUPABASE_SECRET_KEY="secret")

from app import auth, invites, main, push  # noqa: E402
from app.db import Supabase  # noqa: E402
from test_ai_routes import FakeDb as BaseDb  # noqa: E402
from test_api import FakeJwks, token  # noqa: E402


class FakeDb(BaseDb):
    def __init__(self):
        super().__init__()
        self.t.update(profiles=[], push_subs=[], ticks=[], goals=[])
        self.t["allowed_emails"] = [{"email": "me@x.com", "is_admin": True}, {"email": "pal@x.com", "is_admin": False}]

    async def admin_insert(self, table, rows):
        out = []
        for r in rows:
            r = {"id": self.next_id, "status": "pending", "created_at": datetime.now(timezone.utc).isoformat(), **r}
            self.next_id += 1
            self.t[table].append(r)
            out.append(r)
        return out

    async def admin_delete(self, table, params):
        gone = self._match(self.t[table], params)
        self.t[table] = [r for r in self.t[table] if r not in gone]


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(auth, "_jwks", lambda url: FakeJwks())
    db = FakeDb()
    sent = []
    monkeypatch.setattr(push, "send_one", lambda sub, payload, cfg: sent.append(payload) or "ok")
    main.app.dependency_overrides[Supabase.dep] = lambda: db
    yield TestClient(main.app), db, sent, monkeypatch
    main.app.dependency_overrides.clear()


def ask(c, email="new@x.com", ip="9.9.9.9", **extra):
    return c.post("/invite-requests", json={"email": email, **extra}, headers={"x-forwarded-for": ip})


def test_anyone_can_ask_without_logging_in(api):
    c, db, _, _ = api
    r = ask(c, " New@X.com ", name=" Sam ", note=" I study SQL ")
    assert r.status_code == 200 and r.json() == {"ok": True}
    row = db.t["invite_requests"][0]
    assert (row["email"], row["name"], row["note"], row["status"]) == ("new@x.com", "Sam", "I study SQL", "pending")


def test_the_network_address_is_stored_only_as_a_keyed_hash(api):
    c, db, _, _ = api
    ask(c, ip="203.0.113.7")
    stored = db.t["invite_requests"][0]["ip_hash"]
    assert stored and "203.0.113.7" not in stored and len(stored) == 24
    assert stored == invites.hashed("203.0.113.7", "secret") and stored != invites.hashed("203.0.113.7", "other-key")


def test_bad_email_is_refused_and_the_honeypot_stores_nothing(api):
    c, db, _, _ = api
    assert ask(c, "not-an-email").status_code == 422
    assert ask(c, "bot@x.com", website="http://spam.example").json() == {"ok": True}
    assert db.t["invite_requests"] == []


def test_the_answer_never_reveals_who_is_invited_or_has_asked(api):
    c, db, _, _ = api
    first, again, invited = ask(c, "new@x.com"), ask(c, "new@x.com"), ask(c, "pal@x.com")
    assert first.json() == again.json() == invited.json() == {"ok": True}
    assert [r["email"] for r in db.t["invite_requests"]] == ["new@x.com"]          # no duplicate, no row for an invited email


def test_one_network_gets_five_requests_a_day_and_a_forged_header_does_not_help(api):
    c, db, _, _ = api
    for i in range(5):
        assert ask(c, f"u{i}@x.com", ip=f"{i}.0.0.1, 9.9.9.9").status_code == 200     # leftmost entries are forged; the last is real
    assert ask(c, "u5@x.com", ip="7.7.7.7, 9.9.9.9").status_code == 429
    assert ask(c, "u6@x.com", ip="8.8.8.8").status_code == 200                           # a different network is fine
    assert len(db.t["invite_requests"]) == 6


def test_the_pending_queue_is_capped(api):
    c, db, _, mp = api
    mp.setattr(invites, "PENDING_CAP", 2)
    for i in range(3):
        db.t["invite_requests"].append({"id": i + 100, "email": f"q{i}@x.com", "status": "pending", "ip_hash": "x"})
    assert ask(c, "late@x.com").status_code == 503


def test_only_an_admin_sees_and_decides_requests(api):
    c, db, _, _ = api
    ask(c, "new@x.com")
    rid = db.t["invite_requests"][0]["id"]
    assert c.get("/admin/requests").status_code == 401
    assert c.get("/admin/requests", headers=token(email="pal@x.com")).status_code == 403
    assert c.post(f"/admin/requests/{rid}/approve", headers=token(email="pal@x.com")).status_code == 403
    assert [r["email"] for r in c.get("/admin/requests", headers=token()).json()] == ["new@x.com"]
    assert c.get("/admin/requests?status=bogus", headers=token()).status_code == 422


def test_approving_adds_the_email_once_and_rejecting_does_not(api):
    c, db, _, _ = api
    ask(c, "yes@x.com", ip="1.1.1.1"); ask(c, "no@x.com", ip="2.2.2.2")
    yes, no = (r["id"] for r in db.t["invite_requests"])
    assert c.post(f"/admin/requests/{yes}/approve", headers=token()).json() == {"email": "yes@x.com", "status": "approved"}
    c.post(f"/admin/requests/{yes}/approve", headers=token())                                   # twice: still one row
    c.post(f"/admin/requests/{no}/reject", headers=token())
    assert [e["email"] for e in db.t["allowed_emails"]].count("yes@x.com") == 1
    assert "no@x.com" not in [e["email"] for e in db.t["allowed_emails"]]
    done = {r["email"]: r for r in db.t["invite_requests"]}
    assert done["yes@x.com"]["status"] == "approved" and done["yes@x.com"]["decided_by"] == "me@x.com"
    assert done["no@x.com"]["status"] == "rejected" and done["no@x.com"]["decided_at"]
    assert c.post("/admin/requests/999/approve", headers=token()).status_code == 404


def test_admins_get_a_push_when_someone_asks_and_a_failure_never_blocks_the_request(api):
    c, db, sent, mp = api
    db.t["profiles"] = [{"user_id": "u1", "email": "me@x.com"}]
    db.t["push_subs"] = [{"user_id": "u1", "endpoint": "https://fcm.googleapis.com/x", "sub": {"endpoint": "https://fcm.googleapis.com/x"}}]
    assert ask(c, "new@x.com").status_code == 200
    assert len(sent) == 1 and "new@x.com" in sent[0] and "New invite request" in sent[0]
    mp.setattr(push, "send_one", lambda *a: (_ for _ in ()).throw(RuntimeError("push service down")))
    assert ask(c, "other@x.com", ip="5.5.5.5").status_code == 200
    assert len(db.t["invite_requests"]) == 2


def test_the_admin_sees_how_many_are_waiting(api):
    c, db, _, _ = api
    ask(c, "a@x.com", ip="1.1.1.1"); ask(c, "b@x.com", ip="2.2.2.2")
    assert c.get("/me", headers=token()).json()["pending_requests"] == 2
    assert "pending_requests" not in c.get("/me", headers=token(email="pal@x.com")).json()


def test_a_signed_in_person_who_is_not_invited_is_locked_out_of_their_data(api):
    c, db, _, _ = api
    stranger = token(email="stranger@x.com")
    assert c.get("/me", headers=stranger).json()["ai_allowed"] is False                  # /me still answers, so the app can show the gate
    for method, path in (("get", "/ticks"), ("get", "/goals"), ("put", "/goals"), ("post", "/ticks"), ("post", "/attempts")):
        r = getattr(c, method)(path, headers=stranger, **({"json": {}} if method != "get" else {}))
        assert r.status_code == 403, path


def test_decided_requests_are_deleted_after_ninety_days(api):
    import asyncio
    c, db, _, mp = api
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    mp.setattr(invites, "now", lambda: now)
    old, recent = (now - timedelta(days=91)).isoformat(), (now - timedelta(days=5)).isoformat()
    db.t["invite_requests"] = [
        {"id": 1, "email": "old@x.com", "status": "rejected", "decided_at": old},
        {"id": 2, "email": "new@x.com", "status": "approved", "decided_at": recent},
        {"id": 3, "email": "wait@x.com", "status": "pending", "decided_at": None},
    ]
    asyncio.run(invites.purge_old(db))
    assert [r["email"] for r in db.t["invite_requests"]] == ["new@x.com", "wait@x.com"]
