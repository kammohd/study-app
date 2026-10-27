"""Admin-only: who is doing what this week. Admin is checked here on the server, never in the browser."""
import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from . import activity
from .auth import User, admin_user
from .db import Supabase

router = APIRouter()
HISTORY_DAYS = 60       # enough to measure a streak


def today() -> date:
    return datetime.now(timezone.utc).date()


@router.get("/admin/activity")
async def team_activity(_: User = Depends(admin_user), db: Supabase = Depends(Supabase.dep)):
    day = today()
    week = activity.week_start(day).isoformat()
    since = (day - timedelta(days=HISTORY_DAYS)).isoformat()
    invited, profiles, goal_rows, usage = await asyncio.gather(
        db.admin_get("allowed_emails", {"select": "email,is_admin", "order": "added_at"}),
        db.admin_get("profiles", {"select": "user_id,email,last_seen", "limit": "1000"}),
        db.admin_get("goals", {"select": "user_id,track,per_week", "limit": "1000"}),
        db.admin_get("ai_usage", {"day": f"eq.{day.isoformat()}", "select": "user_id,calls", "limit": "1000"}))
    by_email = {p["email"]: p for p in profiles}
    goals = defaultdict(dict)
    for g in goal_rows:
        goals[g["user_id"]][g["track"]] = g["per_week"]
    calls = {u["user_id"]: u["calls"] for u in usage}

    async def one(person: dict) -> dict:
        profile = by_email.get(person["email"])
        if not profile:     # invited but has never opened the app signed in
            return {"email": person["email"], "is_admin": person["is_admin"], "joined": False}
        uid = profile["user_id"]
        ticks, attempts = await asyncio.gather(
            db.admin_get("ticks", {"user_id": f"eq.{uid}", "done_on": f"gte.{since}", "select": "id,done_on", "limit": "1000"}),
            db.admin_get("attempts", {"user_id": f"eq.{uid}", "at": f"gte.{week}", "select": "kind,ok", "limit": "1000"}))
        stats = activity.summarize(ticks=ticks, goals=goals[uid], attempts=attempts, ai_calls=calls.get(uid, 0), today=day)
        return {"email": person["email"], "is_admin": person["is_admin"], "joined": True,
                "last_seen": profile["last_seen"], **stats}

    rows = await asyncio.gather(*(one(p) for p in invited))
    return {"week_start": week, "users": activity.rank([r for r in rows if r["joined"]]) + [r for r in rows if not r["joined"]]}
