"""What each person has done this week, for the admin board. Pure functions, no database.

The rules deliberately mirror the app: the same default goals, a week that starts on Monday, the same
definition of an item done (a Python lesson tick, or a finished DSA / Salesforce / SQL topic) and the same streak.
"""
import re
from datetime import date, timedelta

DEFAULT_GOALS = {"py": 6, "dsa": 3, "sf": 3, "sql": 2}      # GOAL_DEFAULT in index.html
TRACKS = tuple(DEFAULT_GOALS)
LESSON = re.compile(r"^w\d+t\d+$")


def week_start(today: date) -> date:
    return today - timedelta(days=today.weekday())


def track_of(tick_id: str) -> str | None:
    """Which track a tick counts toward, or None for things that are not progress (videos, exercises)."""
    if LESSON.match(tick_id):
        return "py"
    return next((t for t in TRACKS[1:] if tick_id.startswith(t + ".") and tick_id.endswith(".done")), None)


def streak(days: set[str], today: date) -> int:
    """Consecutive study days ending today, or yesterday if today has not started yet."""
    d = today if today.isoformat() in days else today - timedelta(days=1)
    n = 0
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n


def summarize(*, ticks: list[dict], goals: dict[str, int], attempts: list[dict], ai_calls: int, today: date) -> dict:
    """ticks: recent {id, done_on}; attempts: this week's {kind, ok}; goals: the person's own overrides."""
    since = week_start(today).isoformat()
    done = dict.fromkeys(TRACKS, 0)
    for t in ticks:
        track = track_of(t["id"])
        if track and str(t["done_on"])[:10] >= since:
            done[track] += 1
    want = {t: goals.get(t, DEFAULT_GOALS[t]) for t in TRACKS}
    active = [t for t in TRACKS if want[t] > 0]
    quizzes = [a for a in attempts if a["kind"] == "quiz"]
    days = {str(t["done_on"])[:10] for t in ticks}
    return {
        "items_week": sum(done.values()), "done": done, "goals": want,
        "goals_met": sum(done[t] >= want[t] for t in active), "goals_total": len(active),
        "streak": streak(days, today), "last_tick": max(days, default=None),
        "quiz_right": sum(bool(a["ok"]) for a in quizzes), "quiz_total": len(quizzes),
        "challenges_solved": sum(a["kind"] == "challenge" and bool(a["ok"]) for a in attempts),
        "ai_today": ai_calls,
    }


def rank(rows: list[dict]) -> list[dict]:
    """Most done this week first; ties go to more correct answers, then a longer streak, then name."""
    return sorted(rows, key=lambda r: (-r["items_week"], -r["quiz_right"], -r["streak"], r["email"]))
