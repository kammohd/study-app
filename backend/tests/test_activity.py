"""Admin board tests. No network: the database is a fake."""
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient

os.environ.update(SUPABASE_URL="https://test.supabase.co", SUPABASE_PUBLISHABLE_KEY="pub", SUPABASE_SECRET_KEY="secret")

from app import activity, admin_routes, auth, main  # noqa: E402
from app.db import Supabase  # noqa: E402
from test_ai_routes import FakeDb as BaseDb  # noqa: E402
from test_api import FakeJwks, token  # noqa: E402

WED = date(2026, 10, 28)          # the week began Monday 26 Oct


def tick(i, d):
    return {"id": i, "done_on": d}


def test_week_starts_on_monday():
    assert activity.week_start(WED) == date(2026, 10, 26)
    assert activity.week_start(date(2026, 11, 1)) == date(2026, 10, 26)      # Sunday still belongs to that week
    assert activity.week_start(date(2026, 11, 2)) == date(2026, 11, 2)


def test_only_lessons_and_finished_topics_count_as_progress():
    assert activity.track_of("w3t2") == "py"
    assert activity.track_of("dsa.big-o.done") == "dsa"
    assert activity.track_of("sf.profiles.done") == "sf"
    assert activity.track_of("w1v2") is None and activity.track_of("dsa.big-o.v1") is None and activity.track_of("w1e0") is None


def test_streak_counts_back_from_today_or_yesterday():
    assert activity.streak({"2026-10-28", "2026-10-27", "2026-10-26", "2026-10-24"}, WED) == 3
    assert activity.streak({"2026-10-27", "2026-10-26"}, WED) == 2        # nothing yet today does not break it
    assert activity.streak({"2026-10-25"}, WED) == 0
    assert activity.streak(set(), WED) == 0


def test_summary_uses_this_week_goals_and_the_users_own_overrides():
    ticks = [tick("w1t0", "2026-10-26"), tick("w1t1", "2026-10-27"), tick("w1t2", "2026-10-25"),     # last one: previous week
             tick("dsa.big-o.done", "2026-10-28"), tick("dsa.big-o.v1", "2026-10-28")]
    attempts = [{"kind": "quiz", "ok": True}, {"kind": "quiz", "ok": False}, {"kind": "challenge", "ok": True}]
    s = activity.summarize(ticks=ticks, goals={"py": 2, "sql": 0}, attempts=attempts, ai_calls=4, today=WED)
    assert s["done"] == {"py": 2, "dsa": 1, "sf": 0, "sql": 0} and s["items_week"] == 3
    assert (s["goals_met"], s["goals_total"]) == (1, 3)           # py 2/2 met; dsa 1/3, sf 0/3 not; sql off
    assert s["streak"] == 4                                        # 28th, 27th, 26th and 25th are all study days
    assert (s["quiz_right"], s["quiz_total"], s["challenges_solved"], s["ai_today"]) == (1, 2, 1, 4)
    assert s["last_tick"] == "2026-10-28"


def test_ranking_prefers_more_done_then_answers_then_streak():
    row = lambda e, i, q=0, st=0: {"email": e, "items_week": i, "quiz_right": q, "streak": st}      # noqa: E731
    out = activity.rank([row("c", 1), row("b", 3, 1), row("a", 3, 5), row("d", 3, 5, 2)])
    assert [r["email"] for r in out] == ["d", "a", "b", "c"]


class FakeDb(BaseDb):
    def __init__(self):
        super().__init__()
        self.t.update(profiles=[], ticks=[], goals=[], attempts=[], allowed_emails=[
            {"email": "me@x.com", "is_admin": True}, {"email": "pal@x.com", "is_admin": False},
            {"email": "new@x.com", "is_admin": False}])

    def _match(self, rows, params):
        rows = super()._match(rows, params)
        for k, v in (params or {}).items():
            if isinstance(v, str) and v.startswith("gte."):
                rows = [r for r in rows if str(r.get(k, ""))[:10] >= v[4:]]
        return rows


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(auth, "_jwks", lambda url: FakeJwks())
    monkeypatch.setattr(admin_routes, "today", lambda: WED)
    db = FakeDb()
    main.app.dependency_overrides[Supabase.dep] = lambda: db
    yield TestClient(main.app), db
    main.app.dependency_overrides.clear()


def test_board_is_admin_only(api):
    c, _ = api
    assert c.get("/admin/activity").status_code == 401
    assert c.get("/admin/activity", headers=token(email="pal@x.com")).status_code == 403
    assert c.get("/admin/activity", headers=token(email="stranger@x.com")).status_code == 403
    assert c.get("/admin/activity", headers=token()).status_code == 200


def test_board_ranks_people_and_lists_the_ones_who_have_not_joined(api):
    c, db = api
    db.t["profiles"] = [{"user_id": "u1", "email": "me@x.com", "last_seen": "2026-10-28T08:00:00+00:00"},
                        {"user_id": "u2", "email": "pal@x.com", "last_seen": "2026-10-27T20:00:00+00:00"}]
    db.t["ticks"] = [{"user_id": "u1", "id": "w1t0", "done_on": "2026-10-28"},
                     {"user_id": "u2", "id": "w1t0", "done_on": "2026-10-27"}, {"user_id": "u2", "id": "w1t1", "done_on": "2026-10-28"},
                     {"user_id": "u2", "id": "dsa.big-o.done", "done_on": "2026-10-28"}]
    db.t["attempts"] = [{"user_id": "u2", "at": "2026-10-27T10:00:00+00:00", "kind": "quiz", "ok": True},
                        {"user_id": "u1", "at": "2026-10-27T11:00:00+00:00", "kind": "quiz", "ok": False},
                        {"user_id": "u2", "at": "2026-10-20T10:00:00+00:00", "kind": "quiz", "ok": True}]      # last week: ignored
    db.t["goals"] = [{"user_id": "u1", "track": "py", "per_week": 1}]
    db.t["ai_usage"] = [{"user_id": "u2", "day": "2026-10-28", "calls": 7}]
    r = c.get("/admin/activity", headers=token()).json()
    assert r["week_start"] == "2026-10-26"
    assert [u["email"] for u in r["users"]] == ["pal@x.com", "me@x.com", "new@x.com"]
    pal, me, new = r["users"]
    assert (pal["items_week"], pal["quiz_right"], pal["ai_today"], pal["joined"]) == (3, 1, 7, True)
    assert (me["goals"]["py"], me["goals_met"]) == (1, 1)                       # own override of 1 lesson/week
    assert new == {"email": "new@x.com", "is_admin": False, "joined": False}
    assert "user_id" not in str(r)                                              # internal ids are not exposed


def test_me_records_a_profile_for_invited_people_only(api):
    c, db = api
    c.get("/me", headers=token(email="stranger@x.com"))
    assert db.t["profiles"] == []
    c.get("/me", headers=token())
    assert [p["email"] for p in db.t["profiles"]] == ["me@x.com"]
