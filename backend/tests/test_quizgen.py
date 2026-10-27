"""Question-quality gates. No network: `ask` is a scripted fake standing in for Gemini."""
import asyncio
import json
import random

import pytest

from app import quizgen as qg

SRC = "A stack is a last in first out structure. Push adds an item to the top and pop removes the top item."


def q(**kw):
    base = {"question": "Which structure is last in first out?", "options": ["stack", "queue", "heap", "graph"],
            "correct": 0, "explanation": "LIFO."}
    return {**base, **kw}


def scripted(*replies):
    """An async `ask` that returns each reply in turn (dicts/lists are JSON-encoded)."""
    it = iter(replies)

    async def ask(prompt, **opts):
        r = next(it)
        return r if isinstance(r, str) else json.dumps(r)
    return ask


def run(coro):
    return asyncio.run(coro)


def test_ok_q_rejects_bad_shapes():
    assert qg.ok_q(q())
    assert not qg.ok_q(q(question="too short"))
    assert not qg.ok_q(q(options=["a", "b", "c"]))
    assert not qg.ok_q(q(options=["a", "A ", "c", "d"]))          # duplicate once trimmed and lowercased
    assert not qg.ok_q(q(correct=4))
    assert not qg.ok_q(q(correct=True))                            # a bool is not an index
    assert not qg.ok_q("nope")


def test_shuffle_keeps_the_right_answer_right():
    for seed in range(20):
        out = qg.shuffle_q(q(), random.Random(seed))
        assert out["options"][out["correct"]] == "stack"
        assert sorted(out["options"]) == ["graph", "heap", "queue", "stack"]


def test_quote_must_come_from_the_source():
    assert qg.quote_in_source("Push adds an item to the top", SRC)
    assert not qg.quote_in_source("Pop adds an item to the bottom of the queue", SRC)
    assert not qg.quote_in_source("too short", SRC)


def test_verify_keeps_only_agreeing_sure_answers():
    items = [q(), q(question="Which one is first in first out structure?", correct=1), q(question="A third question here?")]
    ask = scripted([{"i": 0, "answer": 0, "sure": True},
                    {"i": 1, "answer": 0, "sure": True},          # disagrees with correct=1
                    {"i": 2, "answer": 0, "sure": False}])        # not sure
    assert run(qg.verify(ask, items, SRC, False)) == [items[0]]


def test_verify_trusts_nothing_when_the_check_is_unreadable():
    assert run(qg.verify(scripted("not json at all"), [q()], SRC, False)) == []


def test_topic_batch_drops_fabricated_evidence_and_unverified_questions():
    good = q(evidence="Push adds an item to the top and pop removes the top item")
    fake = q(question="Which structure is first in first out?", correct=1, evidence="Queues remove the oldest element first always")
    ask = scripted([good, fake], [{"i": 0, "answer": good["correct"], "sure": True}])
    out = run(qg.topic_batch(ask, track="dsa", topic="Stacks", section="", source=SRC, avoid=[]))
    assert len(out) == 1 and out[0]["options"][out[0]["correct"]] == "stack"


def test_topic_batch_salesforce_uses_the_objective_line_as_evidence():
    src = "Describe the standard objects\nManage users and profiles"
    item = q(objective=2)
    ask = scripted([item], [{"i": 0, "answer": 0, "sure": True}])
    out = run(qg.topic_batch(ask, track="sf", topic="Users", section="Org", source=src, avoid=[]))
    assert out[0]["evidence"] == "Manage users and profiles"


def test_topic_batch_refuses_a_missing_source():
    with pytest.raises(ValueError):
        run(qg.topic_batch(scripted(), track="dsa", topic="Stacks", section="", source="short", avoid=[]))


def test_week_batch_returns_shuffled_verified_questions():
    ask = scripted([q()], [{"i": 0, "answer": 0, "sure": True}])
    out = run(qg.week_batch(ask, week=3, theme="APIs & requests", recent="Requests lesson", avoid=[]))
    assert len(out) == 1 and out[0]["options"][out[0]["correct"]] == "stack"
