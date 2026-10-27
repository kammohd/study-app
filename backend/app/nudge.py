"""When to nudge someone, and what to say. Pure logic, no database.

Ported from the Apps Script makeNudge, but per user and timezone-aware: the 8pm kickoff and the 9pm
follow-up (only if nothing was ticked yet) happen at 8pm and 9pm where that person lives; Sundays rest.
"""
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

from .gemini import Gemini

DEFAULT_TZ = "Asia/Dubai"
LESSON = re.compile(r"^w\d+t\d+$")
FAST_ONLY = ("gemini-flash-lite-latest",)     # a nudge must be quick; the lite model is enough


def zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return ZoneInfo(DEFAULT_TZ)


def valid_tz(tz: str) -> bool:
    try:
        ZoneInfo(tz)
        return True
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False


def stage_for(local: datetime) -> str | None:
    """"1" at 8pm, "2" at 9pm local time, nothing otherwise and nothing on Sundays."""
    if local.weekday() == 6:
        return None
    return {20: "1", 21: "2"}.get(local.hour)


def lessons_done(ticks: list[dict]) -> int:
    return sum(1 for t in ticks if LESSON.match(t["id"]))


def ticked_on(ticks: list[dict], day: str) -> bool:
    return any(str(t["done_on"])[:10] == day for t in ticks)


def skip_reason(stage: str, ticked_today: bool, next_task: str) -> str | None:
    if not next_task:
        return "nothing planned"
    if stage == "2" and ticked_today:
        return "already studied"
    return None


async def write(ai: Gemini, *, stage: str, done: int, next_task: str) -> tuple[str, str]:
    """Title and body for the push. Falls back to a plain line if the model is busy."""
    follow_up = stage == "2"
    prompt = (f"Write a push notification (under 45 words) for a learner working through an AI-engineering study plan. "
              f"{done} plan lessons done so far. Tonight's 35-minute task: {next_task}. "
              + ("This is a gentle 9pm follow-up: they have not started yet tonight. Encourage starting now, even a "
                 "small version. Never guilt-trip."
                 if follow_up else "This is the 8pm kickoff nudge. Be concrete and motivating.")
              + " If the task clearly needs a PC and they might be out, suggest a phone alternative (Colab, or a quiz "
                "in the app). No emoji, no greetings.")
    title = "Still time tonight" if follow_up else "Study time"
    try:
        body = (await ai.ask(prompt, temperature=0.8, thinking_budget=0, models=FAST_ONLY)).strip()
    except HTTPException:
        body = ""
    return title, body or f"Tonight: {next_task} (35 min). Small steps count."


async def quote(ai: Gemini, *, done: int, ticked_today: bool, next_task: str) -> str:
    prompt = ("Write ONE motivational line (max 18 words) for a learner working through an AI-engineering study plan. "
              f"They have completed {done} plan lessons and have {'already' if ticked_today else 'not yet'} studied today. "
              f"Next lesson: {next_task or 'none left'}. Vary style: sometimes progress, sometimes identity, "
              "sometimes momentum. No emoji, no quotation marks, no preamble.")
    try:
        return (await ai.ask(prompt, temperature=0.9, thinking_budget=0, models=FAST_ONLY)).strip().strip('"')
    except HTTPException:
        return "Consistency beats intensity."
