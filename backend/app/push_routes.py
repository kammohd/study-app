"""Notifications and the daily quote, per user. Replaces the Apps Script nudge, subs and quote routes.

Cloud Scheduler calls POST /internal/nudge every hour. For each person with a device registered, the
server works out their local time and sends the 8pm kickoff or the 9pm follow-up when it is due.
"""
import asyncio
import hmac
import json
import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from . import nudge, push
from .ai_routes import get_gemini
from .auth import User, allowlisted_user
from .config import Settings, get_settings
from .db import Supabase
from .gemini import Gemini

router = APIRouter()
MAX_DEVICES = 10
QUOTE_TTL = 6 * 3600
_quotes: dict[str, tuple[float, str]] = {}      # per instance; losing it on a restart only costs one model call


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---- devices ----

class Keys(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class Subscription(BaseModel):
    endpoint: str = Field(max_length=1000)
    keys: Keys


class SubscribeReq(BaseModel):
    subscription: Subscription


@router.post("/push/subscribe")
async def subscribe(req: SubscribeReq, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep)):
    sub = req.subscription
    if not push.endpoint_ok(sub.endpoint):
        raise HTTPException(422, "That is not a push service address")
    mine = await db.get("push_subs", user.token, {"select": "endpoint", "limit": "50"})
    if len(mine) >= MAX_DEVICES and sub.endpoint not in {m["endpoint"] for m in mine}:
        raise HTTPException(422, f"At most {MAX_DEVICES} devices per account")
    await db.upsert("push_subs", user.token, [{"endpoint": sub.endpoint, "sub": sub.model_dump(),
                                               "updated_at": utcnow().isoformat()}], "user_id,endpoint")
    return {"ok": True}


class Unsubscribe(BaseModel):
    endpoint: str = Field(max_length=1000)


@router.post("/push/unsubscribe")
async def unsubscribe(req: Unsubscribe, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep)):
    await db.delete("push_subs", user.token, {"endpoint": f"eq.{req.endpoint}"})
    return {"ok": True}


# ---- settings the app keeps fresh ----

class Prefs(BaseModel):
    tz: str = Field(max_length=64)
    next_task: str = Field("", max_length=300)      # the first lesson not yet ticked; "" when the plan is finished
    enabled: bool = True


@router.put("/me/nudge")
async def put_prefs(p: Prefs, user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep)):
    if not nudge.valid_tz(p.tz):
        raise HTTPException(422, "Unknown timezone")
    await db.upsert("nudge_prefs", user.token, [{"tz": p.tz, "next_task": p.next_task, "enabled": p.enabled,
                                                 "updated_at": utcnow().isoformat()}], "user_id")
    return {"ok": True}


# ---- daily quote ----

@router.get("/quote")
async def get_quote(user: User = Depends(allowlisted_user), db: Supabase = Depends(Supabase.dep),
                    ai: Gemini = Depends(get_gemini)):
    """One line per person, cached for hours. Uses the fast model and does not touch the daily AI quota."""
    hit = _quotes.get(user.id)
    if hit and time.time() - hit[0] < QUOTE_TTL:
        return {"quote": hit[1]}
    ticks = await db.admin_get("ticks", {"user_id": f"eq.{user.id}", "select": "id,done_on", "limit": "2000"})
    prefs = await db.admin_get("nudge_prefs", {"user_id": f"eq.{user.id}", "select": "tz,next_task"})
    p = prefs[0] if prefs else {"tz": nudge.DEFAULT_TZ, "next_task": ""}
    today = utcnow().astimezone(nudge.zone(p["tz"])).date().isoformat()
    text = await nudge.quote(ai, done=nudge.lessons_done(ticks), ticked_today=nudge.ticked_on(ticks, today),
                             next_task=p["next_task"])
    _quotes[user.id] = (time.time(), text)
    return {"quote": text}


# ---- the hourly job ----

class NudgeRun(BaseModel):
    stage: str | None = Field(None, pattern="^[12]$")    # force a stage (testing); default: whatever is due now
    user_id: str | None = None
    dry_run: bool = False
    force: bool = False                                   # ignore "already sent today"


@router.post("/internal/nudge")
async def run_nudges(req: NudgeRun | None = None, x_cron_token: str = Header(""), db: Supabase = Depends(Supabase.dep),
                     ai: Gemini = Depends(get_gemini), cfg: Settings = Depends(get_settings)):
    if not cfg.cron_token or not hmac.compare_digest(x_cron_token.encode(), cfg.cron_token.encode()):
        raise HTTPException(401, "Bad token")
    req = req or NudgeRun()
    devices = defaultdict(list)
    for d in await db.admin_get("push_subs", {"select": "user_id,endpoint,sub", "limit": "5000"}):
        devices[d["user_id"]].append(d)
    prefs = {p["user_id"]: p for p in await db.admin_get("nudge_prefs", {"select": "*", "limit": "5000"})}
    report = []
    for uid, devs in devices.items():
        p = prefs.get(uid)
        if (req.user_id and uid != req.user_id) or not p or not p["enabled"]:
            continue
        local = utcnow().astimezone(nudge.zone(p["tz"]))
        stage = req.stage or nudge.stage_for(local)
        if not stage:
            continue
        day = local.date().isoformat()
        if p["last_nudge"] == f"{day}:{stage}" and not req.force:
            report.append({"user": uid[:8], "skipped": "already sent"})
            continue
        ticks = await db.admin_get("ticks", {"user_id": f"eq.{uid}", "select": "id,done_on", "limit": "2000"})
        why = nudge.skip_reason(stage, nudge.ticked_on(ticks, day), p["next_task"])
        if why:
            report.append({"user": uid[:8], "skipped": why})
            continue
        title, body = await nudge.write(ai, stage=stage, done=nudge.lessons_done(ticks), next_task=p["next_task"])
        if req.dry_run:
            report.append({"user": uid[:8], "would_send": body})
            continue
        payload = json.dumps({"title": title, "body": body, "url": "https://kammohd.github.io/study-app/"})
        results = [await asyncio.to_thread(push.send_one, d["sub"], payload, cfg) for d in devs]
        gone = [d["endpoint"] for d, r in zip(devs, results) if r == "gone"]
        for endpoint in gone:       # the device unsubscribed or the app was removed: stop trying
            await db.admin_delete("push_subs", {"user_id": f"eq.{uid}", "endpoint": f"eq.{endpoint}"})
        if "ok" in results:
            await db.admin_patch("nudge_prefs", {"user_id": f"eq.{uid}"}, {"last_nudge": f"{day}:{stage}"})
        report.append({"user": uid[:8], "sent": results.count("ok"), "devices": len(devs), "removed": len(gone)})
    return {"people": len(devices), "results": report}
