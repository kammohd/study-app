"""Check the Supabase login token (a JWT) on every request.

Supabase signs tokens with a private key and publishes the matching public keys at a JWKS URL.
We download those once, then verify each token's signature, expiry and audience locally.
"""
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, HTTPException, Request

from .config import Settings, get_settings
from .db import Supabase


@dataclass
class User:
    id: str
    email: str | None
    token: str          # the raw JWT, forwarded to Supabase so row-level security applies


@lru_cache
def _jwks(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json", cache_keys=True)


def current_user(request: Request, settings: Settings = Depends(get_settings)) -> User:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = header[7:]
    try:
        key = _jwks(settings.supabase_url).get_signing_key_from_jwt(token).key
        claims = jwt.decode(
            token, key, algorithms=["ES256"], audience="authenticated",
            issuer=f"{settings.supabase_url}/auth/v1",
        )
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"Invalid token: {e}")
    return User(
        id=claims["sub"],
        email=(claims.get("email") or "").lower() or None,
        token=token,
    )


async def allowlisted_user(user: User = Depends(current_user), db: Supabase = Depends(Supabase.dep)) -> User:
    """For features that cost money (AI): the user's email must be in allowed_emails."""
    if not user.email:
        raise HTTPException(403, "Sign in with an approved email to use AI features")
    rows = await db.admin_get("allowed_emails", {"email": f"eq.{user.email}", "select": "email"})
    if not rows:
        raise HTTPException(403, "This email is not approved for AI features")
    return user


async def admin_user(user: User = Depends(current_user), db: Supabase = Depends(Supabase.dep)) -> User:
    """For the admin screen: the email must be on the list with is_admin set. Checked here, never in the browser."""
    rows = await db.admin_get("allowed_emails", {"email": f"eq.{user.email}", "select": "is_admin"}) if user.email else []
    if not rows or not rows[0].get("is_admin"):
        raise HTTPException(403, "Admins only")
    return user
