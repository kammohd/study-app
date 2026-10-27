"""AI route tests. No network: the database and Gemini are fakes."""
import json
import os

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

os.environ.update(SUPABASE_URL="https://test.supabase.co", SUPABASE_PUBLISHABLE_KEY="pub",
                  SUPABASE_SECRET_KEY="secret", DAILY_AI_LIMIT="5")

from app import ai_routes, auth, main  # noqa: E402
from app.db import Supabase  # noqa: E402
from test_api import FakeJwks, token  # noqa: E402


class FakeDb:
    """Just enough PostgREST: eq. filters, ids, upsert on conflict columns, patch. Rows are per user for state tables."""

    USER_TABLES = {"question_state", "challenge_state"}

    def __init__(self):
        self.t = {"allowed_emails": [{"email": "me@x.com", "is_admin": True}], "ai_usage": [], "question_bank": [],
                  "question_state": [], "challenge_bank": [], "challenge_state": [], "invite_requests": []}
        self.next_id = 1

    def _match(self, rows, params):
        for k, v in (params or {}).items():
            if not isinstance(v, str):
                continue
            if v.startswith("eq."):
                want = v[3:]
                rows = [r for r in rows if str(r.get(k)).lower() == want.lower() or str(r.get(k)) == want]
            elif v.startswith("gte."):
                rows = [r for r in rows if str(r.get(k, "")) >= v[4:]]
            elif v.startswith("lt."):
                rows = [r for r in rows if r.get(k) is not None and str(r.get(k)) < v[3:]]
            elif v.startswith("in.("):
                wanted = [x.strip('"') for x in v[4:-1].split(",")]
                rows = [r for r in rows if str(r.get(k)) in wanted]
        return rows

    async def admin_get(self, table, params=None):
        return self._match(self.t[table], params)

    async def get(self, table, token, params=None):
        return list(self.t[table])

    def _upsert(self, table, rows, on_conflict):
        keys = on_conflict.split(",")
        out = []
        for row in rows:
            row = {"user_id": "u1", **row} if table in self.USER_TABLES else dict(row)
            same = next((r for r in self.t[table] if all(r.get(k) == row.get(k) for k in keys)), None)
            if same:
                same.update(row)
                out.append(same)
            else:
                if table in ("question_bank", "challenge_bank"):
                    row = {"id": self.next_id, "bad": False, **row}
                    self.next_id += 1
                self.t[table].append(row)
                out.append(row)
        return out

    async def admin_upsert(self, table, rows, on_conflict):
        return self._upsert(table, rows, on_conflict)

    async def upsert(self, table, token, rows, on_conflict):
        return self._upsert(table, rows, on_conflict)

    async def admin_patch(self, table, params, values):
        rows = self._match(self.t[table], params)
        for r in rows:
            r.update(values)
        return rows


class FakeAi:
    """Scripted Gemini: each ask() returns the next reply; a reply that is an Exception is raised."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    async def ask(self, prompt, **opts):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


def question(text="Which structure is last in first out?", correct=0):
    return {"question": text, "options": ["stack", "queue", "heap", "graph"], "correct": correct, "explanation": "LIFO."}


def verdicts(n):
    return [{"i": i, "answer": 0, "sure": True} for i in range(n)]


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setattr(auth, "_jwks", lambda url: FakeJwks())
    db, ai = FakeDb(), FakeAi()
    main.app.dependency_overrides[Supabase.dep] = lambda: db
    main.app.dependency_overrides[ai_routes.get_gemini] = lambda: ai
    yield TestClient(main.app), db, ai
    main.app.dependency_overrides.clear()


PY = {"track": "py", "topic_id": "w3", "week": 3, "theme": "APIs & requests", "recent": "Requests lesson"}


def test_ai_routes_need_an_approved_email(api):
    c, _, _ = api
    assert c.post("/quiz/next", headers=token(email="other@x.com"), json=PY).status_code == 403
    assert c.post("/quiz/next", json=PY).status_code == 401


def test_python_quiz_needs_a_theme(api):
    c, _, _ = api
    assert c.post("/quiz/next", headers=token(), json={"track": "py", "topic_id": "w3"}).status_code == 422


def test_empty_bank_generates_banks_and_serves(api):
    c, db, ai = api
    ai.replies = [[question()], verdicts(1)]
    r = c.post("/quiz/next", headers=token(), json=PY).json()
    assert r["question"].startswith("Which structure") and r["options"][r["correct"]] == "stack"
    assert ai.calls == 2                                           # one write call, one blind-check call
    assert len(db.t["question_bank"]) == 1
    assert db.t["question_state"][0]["status"] == "served"
    assert db.t["ai_usage"][0]["calls"] == 1                       # one quota unit per generation


def test_serving_from_the_bank_costs_no_ai_call(api):
    c, db, ai = api
    ai.replies = [[question(), question("Which structure is first in first out?")], verdicts(2)]
    first = c.post("/quiz/next", headers=token(), json=PY).json()
    c.post("/quiz/result", headers=token(), json={"question_id": first["id"], "ok": True})
    ai.replies = [AssertionError("must not call the model")]
    second = c.post("/quiz/next", headers=token(), json=PY).json()
    assert second["id"] != first["id"] and ai.calls == 2


def test_wrong_answer_comes_back_until_right(api):
    c, db, ai = api
    ai.replies = [[question()], verdicts(1)]
    q = c.post("/quiz/next", headers=token(), json=PY).json()
    c.post("/quiz/result", headers=token(), json={"question_id": q["id"], "ok": False})
    assert db.t["question_state"][0]["status"] == "retry"
    assert c.post("/quiz/next", headers=token(), json=PY).json()["id"] == q["id"]
    c.post("/quiz/result", headers=token(), json={"question_id": q["id"], "ok": True})
    assert db.t["question_state"][0]["status"] == "right"


def test_unverifiable_batch_reports_an_error_not_a_crash(api):
    c, db, ai = api
    bad = [[question()], [{"i": 0, "answer": 1, "sure": True}]]            # blind check disagrees
    ai.replies = bad + bad                                                 # both attempts fail
    r = c.post("/quiz/next", headers=token(), json=PY).json()
    assert "error" in r and r["topic_id"] == "w3" and db.t["question_bank"] == []
    assert db.t["ai_usage"][0]["calls"] == 1                               # the retry is free


def test_one_bad_batch_is_retried_automatically(api):
    c, db, ai = api
    ai.replies = [[question()], [{"i": 0, "answer": 1, "sure": True}],     # first batch rejected
                  [question()], verdicts(1)]                               # second passes
    r = c.post("/quiz/next", headers=token(), json=PY).json()
    assert r["question"].startswith("Which structure") and ai.calls == 4
    assert db.t["ai_usage"][0]["calls"] == 1


def test_daily_limit_blocks_generation(api):
    c, db, ai = api
    db.t["ai_usage"] = [{"user_id": "u1", "day": __import__("datetime").date.today().isoformat(), "calls": 5}]
    assert c.post("/quiz/next", headers=token(), json=PY).status_code == 429


def challenge(title="Reverse words"):
    return {"title": title, "brief": "Reverse a string.", "scaffold": "def f(s):\n    return ...",
            "test": "assert f('ab') == 'ba'", "solution": "def f(s):\n    return s[::-1]"}


CH = {"track": "py", "key": "w3", "theme": "APIs & requests", "week": 3}


def test_challenge_generate_solve_and_retire(api):
    c, db, ai = api
    ai.replies = [[challenge("One"), challenge("Two"), {"title": "No asserts", "scaffold": "x", "test": "pass", "solution": "x"}]]
    first = c.post("/challenge/next", headers=token(), json=CH).json()
    assert first["title"] == "One" and len(db.t["challenge_bank"]) == 2          # the one without asserts is dropped
    c.post("/challenge/solved", headers=token(), json={"challenge_id": first["id"], "code": "ok"})
    ai.replies = [AssertionError("bank still has one")]
    second = c.post("/challenge/next", headers=token(), json=CH).json()
    assert second["title"] == "Two"
    c.post("/challenge/bad", headers=token(), json={"challenge_id": second["id"]})
    assert [r["bad"] for r in db.t["challenge_bank"]] == [False, True]


def test_hint_degrades_when_the_model_is_busy(api):
    c, _, ai = api
    ai.replies = [HTTPException(503, "busy")]
    assert "busy" in c.post("/ai/hint", headers=token(), json={"task": "t", "code": "c"}).json()["hint"]


def test_grade_parses_json_and_never_passes_garbage(api):
    c, _, ai = api
    ai.replies = ['```json\n{"pass": true, "feedback": "Good."}\n```']
    assert c.post("/ai/grade", headers=token(), json={"task": "t", "answer": "a"}).json() == {"ok": True, "feedback": "Good."}
    ai.replies = ["I cannot grade this"]
    assert c.post("/ai/grade", headers=token(), json={"task": "t", "answer": "a"}).json()["ok"] is False
