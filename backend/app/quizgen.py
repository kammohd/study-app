"""Write multiple-choice questions with Gemini and keep only the ones that survive checking.

Ported from the Apps Script v10 backend. A question reaches the bank only if it:
  1. has a sane shape (4 distinct options, an answer index),
  2. quotes real source text as evidence (topic quizzes from a source),
  3. is answered the same way by a second, blind call that says it is sure.
`ask` is any async function (prompt, **options) -> str, so tests can pass a fake instead of Gemini.
"""
import json
import random
import re
from typing import Awaitable, Callable

Ask = Callable[..., Awaitable[str]]

WEEK_BATCH = 10
TOPIC_BATCH = 5


def parse_json(raw: str):
    t = re.sub(r"```json|```", "", raw).strip()
    m = re.search(r"\[[\s\S]*\]", t)
    return json.loads(m.group(0) if m else t)


def ok_q(q) -> bool:
    """A real question, exactly 4 distinct options, an integer answer index 0-3."""
    if not isinstance(q, dict) or not isinstance(q.get("question"), str) or len(q["question"]) < 15:
        return False
    opts = q.get("options")
    if not isinstance(opts, list) or len(opts) != 4:
        return False
    seen = set()
    for o in opts:
        o = str(o).strip().lower()
        if not o or o in seen:
            return False
        seen.add(o)
    c = q.get("correct")
    return isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 3


def shuffle_q(q: dict, rng: random.Random | None = None) -> dict:
    """Models love putting the right answer first. Shuffle the options and track where it went."""
    rng = rng or random
    opts = [(str(o), i == q["correct"]) for i, o in enumerate(q["options"])]
    rng.shuffle(opts)
    return {**q, "options": [o for o, _ in opts], "correct": next(i for i, (_, r) in enumerate(opts) if r)}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", str(s).lower())).strip()


def quote_in_source(quote: str, src: str) -> bool:
    """The evidence must really come from the source: 80% of its word-triples appear in it."""
    w = _norm(quote).split()
    if len(w) < 4:
        return False
    s = f" {_norm(src)} "
    triples = [f" {w[i]} {w[i + 1]} {w[i + 2]} " for i in range(len(w) - 2)]
    return sum(t in s for t in triples) / len(triples) >= 0.8


async def verify(ask: Ask, items: list[dict], src: str, obj_mode: bool) -> list[dict]:
    """Blind check: answer every question WITHOUT seeing the claimed answer; keep the ones that agree.

    obj_mode: the source only lists exam objectives, so general knowledge is allowed;
    otherwise the source text itself must settle the answer."""
    if not items:
        return []
    qs = [{"i": i, "question": q["question"], "options": q["options"]} for i, q in enumerate(items)]
    rule = ("Use your own accurate knowledge; the text below only says what the exam covers.\n" if obj_mode
            else "Use ONLY the source text. If the source does not settle the answer, set sure to false.\n")
    prompt = ("You are an exam checker. Answer each multiple-choice question yourself, independently.\n"
              f"{rule}SOURCE:\n{src}\n\nQUESTIONS (JSON):\n{json.dumps(qs)}\n\n"
              'Reply with ONLY a JSON array: [{"i":0,"answer":<option index 0-3>,"sure":true or false}, ...]. '
              "sure is true only if exactly one option is clearly correct and you are certain.")
    try:
        answers = {a["i"]: a for a in parse_json(await ask(prompt, temperature=0, json=True, thinking_budget=0))}
    except (ValueError, KeyError, TypeError):
        return []                       # an unreadable check means nothing is trusted
    return [q for i, q in enumerate(items) if (a := answers.get(i)) and a.get("sure") is True
            and a.get("answer") == q["correct"]]


async def week_batch(ask: Ask, *, week: int, theme: str, recent: str, avoid: list[str]) -> list[dict]:
    """Python-plan revision questions for one plan week, from the lessons the learner just finished."""
    recent = recent or f"the theme: {theme}"
    prompt = (f"Create {WEEK_BATCH} DIFFERENT beginner multiple-choice questions to help a learner revise. "
              f"They are on week {week} (theme: {theme}) of an AI-engineering program. "
              f"Recently completed lessons: {recent}. "
              + (f"NEVER repeat or rephrase these questions: {' | '.join(avoid[-60:])}. " if avoid else "")
              + "Ask about concepts in those lessons. Vary topic and difficulty.\n"
              "RULES: exactly 4 options; exactly one correct; only well-established facts you are certain of "
              "(no version numbers, limits or library details you are unsure of); distractors plausible but "
              "clearly wrong; put the correct answer in a random position; add a one-sentence explanation.\n"
              "Reply with ONLY a JSON array: "
              '[{"question":"...","options":["...","...","...","..."],"correct":0,"explanation":"..."}, ...] '
              "where correct is the index of the right option.")
    items = [q for q in parse_json(await ask(prompt, temperature=0.4, json=True, thinking_budget=0)) if ok_q(q)]
    return [shuffle_q(q) for q in await verify(ask, items, f"Lessons: {recent}", True)]


async def topic_batch(ask: Ask, *, track: str, topic: str, section: str, source: str,
                      avoid: list[str]) -> list[dict]:
    """Questions for one topic, written from the source text the app sends (never from memory alone)."""
    src = source[:4000]
    if len(src) < 40:
        raise ValueError("no source text for this topic")
    obj_mode = track == "sf"           # Salesforce sends exam objectives, DSA sends a fact summary
    lines = [ln for ln in src.split("\n") if ln]
    numbered = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(lines))
    dont = f"Do NOT repeat these: {' | '.join(avoid[-30:])}\n" if avoid else ""
    if obj_mode:
        prompt = ("You write practice questions for the Salesforce Certified Platform Administrator exam.\n"
                  f"Topic: {topic} ({section})\nOFFICIAL OBJECTIVES for this section (numbered):\n{numbered}\n\n"
                  f"Write {TOPIC_BATCH} DIFFERENT scenario-style questions, each testing ONE objective above.\n"
                  "RULES: use only well-established, documented Salesforce behaviour you are CERTAIN of. "
                  "Never state numbers, limits, prices, release names or UI paths unless certain. "
                  "If unsure of a fact, pick a different concept. Exactly 4 options, exactly one correct, "
                  "distractors plausible but wrong, correct answer in a random position, one-sentence "
                  'explanation, and "objective": the number of the objective tested.\n' + dont
                  + "Reply with ONLY a JSON array: "
                  '[{"question":"...","options":["...","...","...","..."],"correct":0,"explanation":"...","objective":1}, ...]')
    else:
        prompt = ("You write exam-prep multiple-choice questions for a learner.\n"
                  f"Topic: {topic}\nSOURCE (the ONLY facts you may use):\n{src}\n\n"
                  f"Write {TOPIC_BATCH} DIFFERENT questions that can be answered from SOURCE alone.\n"
                  "RULES: use no fact that is not in SOURCE. Exactly 4 options, exactly one correct, distractors "
                  "plausible but wrong, correct answer in a random position, a one-sentence explanation, and "
                  '"evidence": an exact quote copied from SOURCE (6-30 words) that proves the answer.\n' + dont
                  + "Reply with ONLY a JSON array: "
                  '[{"question":"...","options":["...","...","...","..."],"correct":0,"explanation":"...","evidence":"..."}, ...]')
    items = [q for q in parse_json(await ask(prompt, temperature=0.3, json=True, thinking_budget=0)) if ok_q(q)]
    if obj_mode:
        items = [q for q in items if isinstance(q.get("objective"), int) and 1 <= q["objective"] <= len(lines)]
        for q in items:
            q["evidence"] = lines[q["objective"] - 1]
    else:
        items = [q for q in items if isinstance(q.get("evidence"), str) and quote_in_source(q["evidence"], src)]
    return [shuffle_q(q) for q in await verify(ask, items, numbered if obj_mode else src, obj_mode)]
