"""AI endpoints: quizzes, coding challenges, hints and grading. Replaces the Apps Script web app.

The question and challenge banks are shared by everyone; what each person has answered or solved lives
in question_state / challenge_state (row-level security keeps those private). Serving from the bank costs
no AI call; only an empty bank triggers one generation call, which counts against the daily quota.
"""
import json
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import challengegen, quizgen
from .auth import User, allowlisted_user
from .config import Settings, get_settings
from .db import Supabase
from .gemini import Gemini
from .quota import spend_ai_call

router = APIRouter()
TRACK = "^(py|dsa|sf|sql)$"
RETRY_MSG = "couldn't write a question I could verify — tap again"


def get_gemini(cfg: Settings = Depends(get_settings)) -> Gemini:
    return Gemini(cfg)


# ---- quizzes ----

class QuizReq(BaseModel):
    track: str = Field(pattern=TRACK)
    topic_id: str = Field(min_length=1, max_length=80)      # "w3" for a Python plan week, else the topic id
    theme: str = Field("", max_length=200)                  # py: the week's theme
    recent: str = Field("", max_length=1500)                # py: lessons just completed
    week: int | None = Field(None, ge=1, le=99)
    topic: str = Field("", max_length=200)
    section: str = Field("", max_length=200)
    source: str = Field("", max_length=4000)                # topics: the text questions must come from


def servable(bank: list[dict], states: dict[int, dict], today: str) -> list[dict]:
    """Not yet seen, or answered wrong (retry), or shown on an earlier day without an answer. New ones first."""
    ready = []
    for r in bank:
        s = states.get(r["id"])
        if s is None:
            ready.append((0, r["id"], r))
        elif s["status"] == "retry" or (s["status"] == "served" and s["served_on"] != today):
            ready.append((1, r["id"], r))
    return [r for _, _, r in sorted(ready, key=lambda t: t[:2])]


@router.post("/quiz/next")
async def quiz_next(req: QuizReq, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep),
                    ai: Gemini = Depends(get_gemini), cfg: Settings = Depends(get_settings)):
    if req.track == "py" and not req.theme:
        raise HTTPException(422, "Python quizzes need the week's theme")
    today = date.today().isoformat()
    bank = await db.admin_get("question_bank", {"track": f"eq.{req.track}", "topic_id": f"eq.{req.topic_id}",
                                                "select": "*", "order": "id", "limit": "300"})
    states = {s["question_id"]: s for s in await db.get(
        "question_state", user.token, {"select": "question_id,status,served_on", "limit": "5000"})}
    ready = servable(bank, states, today)
    if not ready:       # bank dry: ONE generation call (plus its verification call), then serve from the result
        await spend_ai_call(user, db, cfg.daily_ai_limit)
        avoid = [r["question"] for r in bank]
        try:
            if req.track == "py":
                made = await quizgen.week_batch(ai.ask, week=req.week or 0, theme=req.theme, recent=req.recent, avoid=avoid)
            else:
                made = await quizgen.topic_batch(ai.ask, track=req.track, topic=req.topic, section=req.section,
                                                 source=req.source, avoid=avoid)
        except (ValueError, KeyError, TypeError) as e:     # unreadable model output or a missing source
            return {"topic_id": req.topic_id, "error": str(e)[:120] or RETRY_MSG}
        made = [m for m in made if m["question"] not in set(avoid)]
        if made:
            rows = [{"track": req.track, "topic_id": req.topic_id, "question": m["question"], "options": m["options"],
                     "correct": m["correct"], "explanation": m.get("explanation") or "",
                     "evidence": m.get("evidence") or ""} for m in made]
            saved = await db.admin_upsert("question_bank", rows, "track,topic_id,question")
            ready = servable(saved, states, today)
        if not ready:
            return {"topic_id": req.topic_id, "error": RETRY_MSG}
    q = ready[0]
    await db.upsert("question_state", user.token,
                    [{"question_id": q["id"], "status": "served", "served_on": today}], "user_id,question_id")
    return {"topic_id": req.topic_id, "id": q["id"], "question": q["question"], "options": q["options"],
            "correct": q["correct"], "explanation": q.get("explanation") or "", "evidence": q.get("evidence") or ""}


class QuizResult(BaseModel):
    question_id: int
    ok: bool


@router.post("/quiz/result")
async def quiz_result(r: QuizResult, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep)):
    """Right: never again. Wrong: comes back until answered right."""
    await db.upsert("question_state", user.token,
                    [{"question_id": r.question_id, "status": "right" if r.ok else "retry",
                      "served_on": date.today().isoformat()}], "user_id,question_id")
    return {"ok": True}


# ---- coding challenges ----

class ChallengeReq(BaseModel):
    track: str = Field(pattern=TRACK)
    key: str = Field(min_length=1, max_length=80)           # "w3" for a Python week, else the topic id
    avoid: list[str] = Field(default_factory=list, max_length=60)   # titles the app already shows
    theme: str = Field("", max_length=200)
    week: int | None = Field(None, ge=1, le=99)
    recent: str = Field("", max_length=1500)
    source: str = Field("", max_length=2000)


def first_unsolved(bank: list[dict], solved: set[int], avoid: list[str]) -> dict | None:
    return next((c for c in bank if c["id"] not in solved and c["title"] not in avoid), None)


def challenge_json(c: dict, key: str) -> dict:
    return {"key": key, "id": c["id"], "title": c["title"], "brief": c["brief"], "scaffold": c["scaffold"],
            "test": c["test"], "solution": c["solution"]}


@router.post("/challenge/next")
async def challenge_next(req: ChallengeReq, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep),
                         ai: Gemini = Depends(get_gemini), cfg: Settings = Depends(get_settings)):
    bank = await db.admin_get("challenge_bank", {"track": f"eq.{req.track}", "key": f"eq.{req.key}", "bad": "eq.false",
                                                 "select": "*", "order": "id", "limit": "300"})
    solved = {s["challenge_id"] for s in await db.get("challenge_state", user.token,
                                                      {"select": "challenge_id", "limit": "5000"})}
    hit = first_unsolved(bank, solved, req.avoid)
    if not hit:
        if not req.theme:
            raise HTTPException(422, "A new challenge needs the theme or topic name")
        await spend_ai_call(user, db, cfg.daily_ai_limit)
        try:
            made = await challengegen.challenge_batch(
                ai.ask, topic_mode=req.track != "py", theme=req.theme, week=req.week, recent=req.recent,
                source=req.source, avoid=[c["title"] for c in bank] + req.avoid)
        except (ValueError, KeyError, TypeError):
            return {"key": req.key, "error": "bad generation — tap again"}
        if made:
            saved = await db.admin_upsert("challenge_bank", [{"track": req.track, "key": req.key, **m} for m in made],
                                          "track,key,title")
            hit = first_unsolved(saved, solved, req.avoid)
        if not hit:
            return {"key": req.key, "error": "bad generation — tap again"}
    return challenge_json(hit, req.key)


class ChallengeSolved(BaseModel):
    challenge_id: int
    code: str = Field("", max_length=2000)


@router.post("/challenge/solved")
async def challenge_solved(r: ChallengeSolved, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep)):
    await db.upsert("challenge_state", user.token,
                    [{"challenge_id": r.challenge_id, "code": r.code, "solved_on": date.today().isoformat()}],
                    "user_id,challenge_id")
    return {"ok": True}


class ChallengeBad(BaseModel):
    challenge_id: int


@router.post("/challenge/bad")
async def challenge_bad(r: ChallengeBad, _: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep)):
    """The app ran the reference solution against its own tests in the browser and it failed: retire it for everyone."""
    await db.admin_patch("challenge_bank", {"id": f"eq.{r.challenge_id}"}, {"bad": True})
    return {"ok": True}


# ---- hints and grading ----

class HintReq(BaseModel):
    task: str = Field(max_length=2000)
    code: str = Field(max_length=4000)


class GradeReq(BaseModel):
    task: str = Field(max_length=2000)
    answer: str = Field(max_length=4000)


async def soft_ask(ai: Gemini, prompt: str, **opts) -> str:
    """Hints and grading degrade to a friendly message when the model is busy; any other failure still raises."""
    try:
        return (await ai.ask(prompt, **opts)).strip()
    except HTTPException as e:
        if e.status_code == 503:
            return ""
        raise


@router.post("/ai/hint")
async def ai_hint(r: HintReq, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep),
                  ai: Gemini = Depends(get_gemini), cfg: Settings = Depends(get_settings)):
    await spend_ai_call(user, db, cfg.daily_ai_limit)
    prompt = ("You are a patient Python tutor inside a study app. The student is stuck on this exercise:\n\n"
              f"Exercise: {r.task}\n\nTheir current code/answer:\n{r.code}\n\n"
              "Give ONE short hint (max 2 sentences) that nudges them toward the solution. Point at the concept or "
              "the mistake. NEVER write the full solution or complete code. If you are not sure what is wrong, "
              "say what to check instead of guessing. Plain text only.")
    hint = await soft_ask(ai, prompt, temperature=0.3, thinking_budget=0)
    return {"hint": hint or "Hint service is busy (rate limit) — try again in a minute."}


@router.post("/ai/grade")
async def ai_grade(r: GradeReq, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep),
                   ai: Gemini = Depends(get_gemini), cfg: Settings = Depends(get_settings)):
    await spend_ai_call(user, db, cfg.daily_ai_limit)
    prompt = ("You are grading a short free-text exercise in a study app for a Salesforce support engineer learning "
              "AI engineering. Be encouraging but strict.\n\nHARD RULES — pass MUST be false if ANY of these apply:\n"
              "- the answer still contains placeholder dots (...) or template text\n"
              "- the answer is essentially the unmodified starter/scaffold\n"
              "- the answer is under 15 words (unless the task asks for something shorter)\n"
              f"- the answer doesn't attempt the specific task\n\nTask: {r.task}\n\nStudent answer:\n{r.answer}\n\n"
              'Reply ONLY with JSON, no markdown: {"pass": true or false, "feedback": "1-2 sentences: what is good, what to improve"}')
    raw = await soft_ask(ai, prompt, temperature=0, json=True)
    if not raw:
        return {"ok": False, "feedback": "Grading service is busy (rate limit) — try again in a minute."}
    try:
        j = json.loads(raw.replace("```json", "").replace("```", "").strip())
        return {"ok": j.get("pass") is True, "feedback": str(j.get("feedback") or "")[:400]}
    except (ValueError, AttributeError):
        return {"ok": False, "feedback": raw[:400]}
