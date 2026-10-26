"""Study app API. Run locally:  uvicorn app.main:app --reload"""
import re
from datetime import date

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .auth import User, admin_user, allowlisted_user, current_user
from .config import Settings, get_settings
from .db import Supabase

settings = get_settings()
app = FastAPI(title="Study app API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["authorization", "content-type"],
)

TRACKS = ("py", "dsa", "sf", "sql")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/me")
async def me(user: User = Depends(current_user), db: Supabase = Depends(Supabase.dep)):
    """Who am I, and may I use AI? The front end uses this to show or hide AI buttons."""
    row = None
    if user.email:
        rows = await db.admin_get("allowed_emails", {"email": f"eq.{user.email}", "select": "email,is_admin"})
        row = rows[0] if rows else None
    return {"id": user.id, "email": user.email, "ai_allowed": row is not None,
            "is_admin": bool(row and row.get("is_admin"))}


# ---- ticks: which lessons / videos / topics are done, and when ----

class Tick(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    done_on: date | None = None      # null = untick


@app.get("/ticks")
async def get_ticks(user: User = Depends(current_user), db: Supabase = Depends(Supabase.dep)):
    rows = await db.get("ticks", user.token, {"select": "id,done_on"})
    return {r["id"]: r["done_on"] for r in rows}


@app.post("/ticks")
async def save_ticks(ticks: list[Tick], user: User = Depends(current_user), db: Supabase = Depends(Supabase.dep)):
    """Accepts a batch, the same shape the app already sends: tick or untick many ids at once."""
    if len(ticks) > 200:
        raise HTTPException(422, "At most 200 ticks per request")
    done = [{"id": t.id, "done_on": t.done_on.isoformat()} for t in ticks if t.done_on]
    undone = [t.id for t in ticks if not t.done_on]
    if done:
        await db.upsert("ticks", user.token, done, "user_id,id")
    if undone:
        quoted = ",".join('"' + i.replace('"', "") + '"' for i in undone)
        await db.delete("ticks", user.token, {"id": f"in.({quoted})"})
    return {"saved": len(done), "removed": len(undone)}


# ---- weekly goals ----

@app.get("/goals")
async def get_goals(user: User = Depends(current_user), db: Supabase = Depends(Supabase.dep)):
    rows = await db.get("goals", user.token, {"select": "track,per_week"})
    return {r["track"]: r["per_week"] for r in rows}


@app.put("/goals")
async def put_goals(goals: dict[str, int], user: User = Depends(current_user), db: Supabase = Depends(Supabase.dep)):
    bad = [t for t in goals if t not in TRACKS]
    if bad or any(not 0 <= n <= 50 for n in goals.values()):
        raise HTTPException(422, f"Tracks must be one of {TRACKS} and goals 0-50")
    await db.upsert("goals", user.token, [{"track": t, "per_week": n} for t, n in goals.items()], "user_id,track")
    return goals


# ---- attempts ----

class Attempt(BaseModel):
    track: str
    kind: str = Field(pattern="^(quiz|challenge)$")
    ref: str = Field(max_length=120)
    ok: bool


@app.post("/attempts")
async def add_attempt(a: Attempt, user: User = Depends(current_user), db: Supabase = Depends(Supabase.dep)):
    await db.insert("attempts", user.token, [a.model_dump()])
    return {"ok": True}


# ---- AI quota (the Gemini topic builder in phase 3 will call spend_ai_call) ----

async def _calls_today(user: User, db: Supabase) -> int:
    rows = await db.admin_get("ai_usage", {"user_id": f"eq.{user.id}", "day": f"eq.{date.today().isoformat()}",
                                           "select": "calls"})
    return rows[0]["calls"] if rows else 0


async def spend_ai_call(user: User, db: Supabase, limit: int) -> int:
    """Count one AI call for today; refuse when the daily limit is reached. Returns calls left."""
    used = await _calls_today(user, db)
    if used >= limit:
        raise HTTPException(429, f"Daily AI limit of {limit} reached; try again tomorrow")
    await db.admin_upsert("ai_usage", [{"user_id": user.id, "day": date.today().isoformat(), "calls": used + 1}],
                          "user_id,day")
    return limit - used - 1


@app.get("/ai/quota")
async def ai_quota(user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep),
                   cfg: Settings = Depends(get_settings)):
    used = await _calls_today(user, db)
    return {"limit": cfg.daily_ai_limit, "used": used, "left": cfg.daily_ai_limit - used}


# ---- admin: who is invited ----

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class Invite(BaseModel):
    email: str = Field(max_length=200)


@app.get("/admin/emails")
async def list_emails(_: User = Depends(admin_user), db: Supabase = Depends(Supabase.dep)):
    return await db.admin_get("allowed_emails", {"select": "email,is_admin,added_at", "order": "added_at"})


@app.post("/admin/emails")
async def add_email(inv: Invite, _: User = Depends(admin_user), db: Supabase = Depends(Supabase.dep)):
    email = inv.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "That does not look like an email address")
    existing = await db.admin_get("allowed_emails", {"email": f"eq.{email}", "select": "email"})
    if not existing:   # never overwrite an existing row, so this cannot demote an admin
        await db.admin_insert("allowed_emails", [{"email": email}])
    return {"email": email}


@app.delete("/admin/emails/{email}")
async def remove_email(email: str, me: User = Depends(admin_user), db: Supabase = Depends(Supabase.dep)):
    email = email.strip().lower()
    if email == me.email:
        raise HTTPException(422, "You cannot remove yourself")
    target = await db.admin_get("allowed_emails", {"email": f"eq.{email}", "select": "is_admin"})
    if target and target[0].get("is_admin"):
        raise HTTPException(422, "Remove admin rights in the database first")
    await db.admin_delete("allowed_emails", {"email": f"eq.{email}"})
    return {"removed": email}
