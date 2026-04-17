"""FastAPI dependency injection — auth, database, user context."""

import os
from fastapi import Depends, HTTPException, Header
from src.api_keys import get_secret
from src.db import get_client, set_user_id


def get_db():
    """Dependency: get Supabase client."""
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


def _is_local_dev() -> bool:
    # Env-only on purpose — don't fall back to secrets.toml, which can leak into
    # deployed images and silently disable auth in production.
    return os.environ.get("LOCAL_DEV", "").lower() == "true"


def _admin_emails() -> set[str]:
    raw = get_secret("ADMIN_EMAILS") or ""
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _decode_jwt(token: str) -> dict | None:
    """Verify a Supabase HS256 JWT and return its claims, or None on failure."""
    secret = get_secret("SUPABASE_JWT_SECRET")
    if not secret:
        return None
    try:
        import jwt
        return jwt.decode(token, secret, algorithms=["HS256"], audience="authenticated")
    except Exception:
        return None


async def get_current_user(authorization: str = Header(None)) -> str:
    """Extract the caller's email from a verified Supabase JWT.

    Returns "anonymous" when no valid token is provided. Endpoints that expose
    personal data must NOT treat "anonymous" as authorized — use require_admin.
    """
    if not authorization:
        return "anonymous"

    token = authorization.replace("Bearer ", "").strip()
    if not token:
        return "anonymous"

    claims = _decode_jwt(token)
    if not claims:
        return "anonymous"

    user_id = claims.get("email") or claims.get("sub") or "anonymous"
    if user_id != "anonymous":
        set_user_id(user_id)
    return user_id


async def require_admin(user: str = Depends(get_current_user)) -> str:
    """Only allow admin users. Raises 403 for everyone else.

    Admins are defined by the ADMIN_EMAILS secret (comma-separated emails).
    Local-dev bypass: LOCAL_DEV=true in the process env treats every caller as
    admin — intended for running against localhost only.
    """
    if _is_local_dev():
        return user

    admins = _admin_emails()
    if not admins:
        # Fail closed when no admins are configured.
        raise HTTPException(status_code=503, detail="Admin access not configured")

    if user.lower() not in admins:
        raise HTTPException(status_code=403, detail="Admin access required")

    return user


async def require_admin_or_scheduler(
    authorization: str = Header(None),
    x_capture_key: str = Header(None),
) -> str:
    """Allow either an admin JWT OR a matching Cloud Scheduler capture key.

    Used for background-job endpoints invoked by Cloud Scheduler. The key
    lives in the ``OI_CAPTURE_KEY`` secret and must be sent as the
    ``X-Capture-Key`` request header.
    """
    # Path 1: capture-key header (scheduler)
    expected = get_secret("OI_CAPTURE_KEY")
    if expected and x_capture_key and x_capture_key == expected:
        return "scheduler"

    # Path 2: admin JWT (manual invocation)
    user = await get_current_user(authorization=authorization)
    return await require_admin(user=user)
