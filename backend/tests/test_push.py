"""Push, nudge and quote tests. No network: the database, Gemini and the push services are fakes."""
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.update(SUPABASE_URL="https://test.supabase.co", SUPABASE_PUBLISHABLE_KEY="pub",
                  SUPABASE_SECRET_KEY="secret")

from app import ai_routes, auth, main, nudge, push, push_routes  # noqa: E402
from app.db import Supabase  # noqa: E402
from test_ai_routes import FakeAi, FakeDb as BaseDb  # noqa: E402
from test_api import FakeJwks, token  # noqa: E402

MON_8PM_DUBAI = datetime(2026, 10, 26, 16, 0, tzinfo=timezone.utc)      # a Monday
SUN_8PM_DUBAI = datetime(2026, 11, 1, 16, 0, tzinfo=timezone.utc)
GOOD = "https://fcm.googleapis.com/fcm/send/abc"
SUB = {"endpoint": GOOD, "keys": {"p256dh": "k", "auth": "a"}}


class FakeDb(BaseDb):
    USER_TABLES = BaseDb.USER_TABLES | {"push_subs", "nudge_prefs"}

    def __init__(self):
        super().__init__()
        self.t.update(push_subs=[], nudge_prefs=[], ticks=[])

    async def admin_delete(self, table, params):
        gone = self._match(self.t[table], params)
        self.t[table] = [r for r in self.t[table] if r not in gone]

    async def delete(self, table, token, params):
        await self.admin_delete(table, params)


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(auth, "_jwks", lambda url: FakeJwks())
    monkeypatch.setenv("CRON_TOKEN", "cron-secret")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "pem")
    push_routes._quotes.clear()
    db, ai = FakeDb(), FakeAi()
    main.app.dependency_overrides[Supabase.dep] = lambda: db
    main.app.dependency_overrides[ai_routes.get_gemini] = lambda: ai
    sent = []
    monkeypatch.setattr(push, "send_one", lambda sub, payload, cfg: sent.append((sub["endpoint"], payload)) or "ok")
    yield TestClient(main.app), db, ai, sent, monkeypatch
    main.app.dependency_overrides.clear()


def device(db, uid="u1", endpoint=GOOD):
    db.t["push_subs"].append({"user_id": uid, "endpoint": endpoint, "sub": {"endpoint": endpoint, "keys": {}}})


def prefs(db, uid="u1", tz="Asia/Dubai", next_task="Week 3, day 2: Requests", enabled=True, last=""):
    db.t["nudge_prefs"].append({"user_id": uid, "tz": tz, "next_task": next_task, "enabled": enabled, "last_nudge": last})


def at(monkeypatch, when):
    monkeypatch.setattr(push_routes, "utcnow", lambda: when)


CRON = {"x-cron-token": "cron-secret"}


def test_only_real_push_services_are_accepted():
    for ok in (GOOD, "https://updates.push.services.mozilla.com/wpush/v2/x", "https://web.push.apple.com/x",
               "https://wns2-par02p.notify.windows.com/x"):
        assert push.endpoint_ok(ok), ok
    for bad in ("http://fcm.googleapis.com/x", "https://169.254.169.254/computeMetadata/v1/",
                "https://fcm.googleapis.com.evil.com/x", "https://fcm.googleapis.com@evil.com/x",
                "https://localhost/x", "ftp://fcm.googleapis.com/x", ""):
        assert not push.endpoint_ok(bad), bad


def test_stage_follows_local_time_and_sundays_rest():
    dubai, london = nudge.zone("Asia/Dubai"), nudge.zone("Europe/London")
    assert nudge.stage_for(MON_8PM_DUBAI.astimezone(dubai)) == "1"
    assert nudge.stage_for(MON_8PM_DUBAI.astimezone(london)) is None            # 17:00 there
    assert nudge.stage_for(datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc).astimezone(dubai)) == "2"
    assert nudge.stage_for(SUN_8PM_DUBAI.astimezone(dubai)) is None
    assert nudge.zone("Nowhere/Land").key == nudge.DEFAULT_TZ


def test_subscribe_rejects_internal_addresses_and_caps_devices(api):
    c, db, *_ = api
    h = token()
    assert c.post("/push/subscribe", headers=h, json={"subscription": {**SUB, "endpoint": "https://169.254.169.254/x"}}).status_code == 422
    assert c.post("/push/subscribe", headers=h, json={"subscription": SUB}).json() == {"ok": True}
    assert [r["endpoint"] for r in db.t["push_subs"]] == [GOOD]
    for i in range(9):
        c.post("/push/subscribe", headers=h, json={"subscription": {**SUB, "endpoint": f"https://fcm.googleapis.com/{i}"}})
    assert c.post("/push/subscribe", headers=h, json={"subscription": {**SUB, "endpoint": "https://fcm.googleapis.com/new"}}).status_code == 422
    assert c.post("/push/subscribe", headers=h, json={"subscription": SUB}).status_code == 200      # existing device still updates
    assert c.post("/push/subscribe", json={"subscription": SUB}).status_code == 401
    assert c.post("/push/subscribe", headers=token(email="other@x.com"), json={"subscription": SUB}).status_code == 403


def test_prefs_validate_timezone(api):
    c, db, *_ = api
    assert c.put("/me/nudge", headers=token(), json={"tz": "Mars/Base", "next_task": "x"}).status_code == 422
    assert c.put("/me/nudge", headers=token(), json={"tz": "Asia/Dubai", "next_task": "Week 1, day 1"}).status_code == 200
    assert db.t["nudge_prefs"][0]["next_task"] == "Week 1, day 1"


def test_cron_needs_the_token(api):
    c, *_ = api
    assert c.post("/internal/nudge").status_code == 401
    assert c.post("/internal/nudge", headers={"x-cron-token": "wrong"}).status_code == 401
    assert c.post("/internal/nudge", headers=token()).status_code == 401           # a user's login is not the cron token


def test_cron_closed_when_no_token_is_configured(api):
    c, db, ai, sent, mp = api
    mp.setenv("CRON_TOKEN", "")
    assert c.post("/internal/nudge", headers={"x-cron-token": ""}).status_code == 401


def test_nudge_sends_once_at_the_users_8pm(api):
    c, db, ai, sent, mp = api
    device(db); prefs(db)
    ai.replies = ["Tonight is Requests. Try the Colab version on your phone."]
    at(mp, MON_8PM_DUBAI)
    r = c.post("/internal/nudge", headers=CRON).json()
    assert r["results"] == [{"user": "u1", "sent": 1, "devices": 1, "removed": 0}]
    assert "Requests" in sent[0][1] and "Study time" in sent[0][1]
    assert db.t["nudge_prefs"][0]["last_nudge"] == "2026-10-26:1"
    r2 = c.post("/internal/nudge", headers=CRON).json()                              # a retried job must not double-send
    assert r2["results"] == [{"user": "u1", "skipped": "already sent"}] and len(sent) == 1


def test_nobody_is_nudged_off_hours_on_sundays_or_when_switched_off(api):
    c, db, ai, sent, mp = api
    device(db); prefs(db)
    at(mp, datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc))                         # 2pm in Dubai
    assert c.post("/internal/nudge", headers=CRON).json()["results"] == []
    at(mp, SUN_8PM_DUBAI)
    assert c.post("/internal/nudge", headers=CRON).json()["results"] == []
    db.t["nudge_prefs"][0]["enabled"] = False
    at(mp, MON_8PM_DUBAI)
    assert c.post("/internal/nudge", headers=CRON).json()["results"] == [] and not sent


def test_each_person_gets_their_own_local_time(api):
    c, db, ai, sent, mp = api
    device(db, "u1"); prefs(db, "u1", tz="Asia/Dubai")
    device(db, "u2", "https://fcm.googleapis.com/two"); prefs(db, "u2", tz="Europe/London")
    ai.replies = ["Nudge for the one whose evening it is."]
    at(mp, MON_8PM_DUBAI)
    r = c.post("/internal/nudge", headers=CRON).json()
    assert [x["user"] for x in r["results"]] == ["u1"] and [s[0] for s in sent] == [GOOD]


def test_follow_up_is_skipped_if_they_already_studied_or_nothing_is_planned(api):
    c, db, ai, sent, mp = api
    device(db); prefs(db)
    db.t["ticks"] = [{"user_id": "u1", "id": "w3t1", "done_on": "2026-10-26"}]
    at(mp, datetime(2026, 10, 26, 17, 0, tzinfo=timezone.utc))                         # 9pm, stage 2
    assert c.post("/internal/nudge", headers=CRON).json()["results"] == [{"user": "u1", "skipped": "already studied"}]
    db.t["nudge_prefs"][0]["next_task"] = ""
    at(mp, MON_8PM_DUBAI)
    assert c.post("/internal/nudge", headers=CRON).json()["results"] == [{"user": "u1", "skipped": "nothing planned"}]
    assert not sent


def test_dead_devices_are_removed_and_a_failed_send_is_retried_next_hour(api):
    c, db, ai, sent, mp = api
    device(db, endpoint="https://fcm.googleapis.com/dead"); device(db, endpoint="https://fcm.googleapis.com/flaky")
    prefs(db)
    ai.replies = ["x"]
    mp.setattr(push, "send_one", lambda sub, p, cfg: "gone" if sub["endpoint"].endswith("dead") else "error")
    at(mp, MON_8PM_DUBAI)
    r = c.post("/internal/nudge", headers=CRON).json()["results"][0]
    assert r["sent"] == 0 and r["removed"] == 1
    assert [d["endpoint"] for d in db.t["push_subs"]] == ["https://fcm.googleapis.com/flaky"]
    assert db.t["nudge_prefs"][0]["last_nudge"] == ""            # nothing delivered, so it may try again


def test_dry_run_and_force_for_testing(api):
    c, db, ai, sent, mp = api
    device(db); prefs(db, last="2026-10-26:1")
    ai.replies = ["preview", "sent"]
    at(mp, MON_8PM_DUBAI)
    dry = c.post("/internal/nudge", headers=CRON, json={"dry_run": True, "force": True}).json()["results"]
    assert dry == [{"user": "u1", "would_send": "preview"}] and not sent
    c.post("/internal/nudge", headers=CRON, json={"force": True})
    assert len(sent) == 1


def test_quote_is_cached_per_person_and_falls_back(api):
    c, db, ai, sent, mp = api
    prefs(db)
    ai.replies = ["Small steps, big shifts."]
    assert c.get("/quote", headers=token()).json() == {"quote": "Small steps, big shifts."}
    ai.replies = [AssertionError("cached")]
    assert c.get("/quote", headers=token()).json() == {"quote": "Small steps, big shifts."}
    assert c.get("/quote", headers=token(email="other@x.com")).status_code == 403
    push_routes._quotes.clear()
    from fastapi import HTTPException
    ai.replies = [HTTPException(503, "busy")]
    assert c.get("/quote", headers=token()).json() == {"quote": "Consistency beats intensity."}
