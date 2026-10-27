"""Per-user daily AI call counter. Every Gemini call is paid for out of a shared free tier, so each approved user gets a cap."""
from datetime import date

from fastapi import HTTPException

from .auth import User
from .db import Supabase


async def calls_today(user: User, db: Supabase) -> int:
    rows = await db.admin_get("ai_usage", {"user_id": f"eq.{user.id}", "day": f"eq.{date.today().isoformat()}",
                                           "select": "calls"})
    return rows[0]["calls"] if rows else 0


async def spend_ai_call(user: User, db: Supabase, limit: int) -> int:
    """Count one AI call for today; refuse when the daily limit is reached. Returns calls left."""
    used = await calls_today(user, db)
    if used >= limit:
        raise HTTPException(429, f"Daily AI limit of {limit} reached; try again tomorrow")
    await db.admin_upsert("ai_usage", [{"user_id": user.id, "day": date.today().isoformat(), "calls": used + 1}],
                          "user_id,day")
    return limit - used - 1
