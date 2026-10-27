"""Invite requests: anyone may ask, only an admin decides.

The public endpoint is the one place a stranger can write to our database, so it is defensive: it always
answers the same way (it never reveals who is already invited), keeps a hashed network address instead of
the address, caps requests per network per day and in total, and ignores a form field only a bot would fill.
"""
import asyncio
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import push
from .auth import User, admin_user
from .config import Settings, get_settings
from .db import Supabase

router = APIRouter()
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PER_NETWORK_PER_DAY = 5
PENDING_CAP = 300
KEEP_DECIDED_DAYS = 90
APP_URL = "https://kammohd.github.io/study-app/"


def now() -> datetime:
    return datetime.now(timezone.utc)


def network_of(request: Request) -> str:
    """The caller's address. Behind Cloud Run the last X-Forwarded-For entry is the one Google's proxy appended;
    earlier entries can be forged by the caller."""
    parts = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    return parts[-1] if parts else (request.client.host if request.client else "")


def hashed(address: str, key: str) -> str:
    return hmac.new(key.encode(), address.encode(), hashlib.sha256).hexdigest()[:24]


class InviteRequest(BaseModel):
    email: str = Field(max_length=200)
    name: str = Field("", max_length=80)
    note: str = Field("", max_length=300)
    website: str = Field("", max_length=200)      # honeypot: hidden on the page, so a person never fills it


@router.post("/invite-requests")
async def request_invite(req: InviteRequest, request: Request, db: Supabase = Depends(Supabase.dep),
                         cfg: Settings = Depends(get_settings)):
    email = req.email.strip().lower()
    if not EMAIL.match(email):
        raise HTTPException(422, "That does not look like an email address")
    if req.website.strip():
        return {"ok": True}                       # a bot: pretend it worked, store nothing
    mine = hashed(network_of(request), cfg.supabase_secret_key)
    since = (now() - timedelta(days=1)).isoformat()
    recent = await db.admin_get("invite_requests", {"ip_hash": f"eq.{mine}", "created_at": f"gte.{since}",
                                                    "select": "id", "limit": str(PER_NETWORK_PER_DAY + 1)})
    if len(recent) >= PER_NETWORK_PER_DAY:
        raise HTTPException(429, "Too many requests from this network today. Try again tomorrow.")
    pending = await db.admin_get("invite_requests", {"status": "eq.pending", "select": "id", "limit": str(PENDING_CAP + 1)})
    if len(pending) > PENDING_CAP:
        raise HTTPException(503, "Invite requests are closed for now.")
    known = await db.admin_get("invite_requests", {"email": f"eq.{email}", "select": "id"})
    invited = await db.admin_get("allowed_emails", {"email": f"eq.{email}", "select": "email"})
    if known or invited:
        return {"ok": True}                       # same answer either way: do not reveal who is invited or asked
    await db.admin_insert("invite_requests", [{"email": email, "name": req.name.strip(), "note": req.note.strip(), "ip_hash": mine}])
    try:
        await notify_admins(db, cfg, f"{email} asked for an invite")
    except Exception:                             # a failed notification must never fail the request
        pass
    return {"ok": True}


async def notify_admins(db: Supabase, cfg: Settings, text: str) -> int:
    """Web push to every device an admin has registered. Returns how many were reached."""
    admins = await db.admin_get("allowed_emails", {"is_admin": "eq.true", "select": "email"})
    if not admins:
        return 0
    quoted = ",".join('"' + a["email"] + '"' for a in admins)
    profiles = await db.admin_get("profiles", {"email": f"in.({quoted})", "select": "user_id"})
    if not profiles:
        return 0
    ids = ",".join(p["user_id"] for p in profiles)
    devices = await db.admin_get("push_subs", {"user_id": f"in.({ids})", "select": "user_id,endpoint,sub"})
    payload = json.dumps({"title": "New invite request", "body": text, "url": APP_URL})
    sent = 0
    for d in devices:
        result = await asyncio.to_thread(push.send_one, d["sub"], payload, cfg)
        if result == "ok":
            sent += 1
        elif result == "gone":
            await db.admin_delete("push_subs", {"user_id": f"eq.{d['user_id']}", "endpoint": f"eq.{d['endpoint']}"})
    return sent


async def purge_old(db: Supabase) -> None:
    """Decided requests are kept for a while (so a rejected address cannot nag again at once), then deleted."""
    cutoff = (now() - timedelta(days=KEEP_DECIDED_DAYS)).isoformat()
    await db.admin_delete("invite_requests", {"decided_at": f"lt.{cutoff}"})


# ---- admin ----

@router.get("/admin/requests")
async def list_requests(status: str = "pending", _: User = Depends(admin_user), db: Supabase = Depends(Supabase.dep)):
    if status not in ("pending", "approved", "rejected"):
        raise HTTPException(422, "status must be pending, approved or rejected")
    return await db.admin_get("invite_requests", {"status": f"eq.{status}", "select": "id,email,name,note,status,created_at",
                                                  "order": "created_at", "limit": "200"})


async def decide(rid: int, status: str, who: User, db: Supabase) -> dict:
    rows = await db.admin_get("invite_requests", {"id": f"eq.{rid}", "select": "id,email,status"})
    if not rows:
        raise HTTPException(404, "No such request")
    email = rows[0]["email"]
    if status == "approved" and not await db.admin_get("allowed_emails", {"email": f"eq.{email}", "select": "email"}):
        await db.admin_insert("allowed_emails", [{"email": email}])      # a plain row: never overwrites an admin
    await db.admin_patch("invite_requests", {"id": f"eq.{rid}"},
                         {"status": status, "decided_at": now().isoformat(), "decided_by": who.email or ""})
    return {"email": email, "status": status}


@router.post("/admin/requests/{rid}/approve")
async def approve(rid: int, me: User = Depends(admin_user), db: Supabase = Depends(Supabase.dep)):
    return await decide(rid, "approved", me, db)


@router.post("/admin/requests/{rid}/reject")
async def reject(rid: int, me: User = Depends(admin_user), db: Supabase = Depends(Supabase.dep)):
    return await decide(rid, "rejected", me, db)
