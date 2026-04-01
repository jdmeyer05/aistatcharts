"""FastAPI dependency injection — auth, database, user context."""

from fastapi import Depends, HTTPException, Header
from src.db import get_client, set_user_id


def get_db():
    """Dependency: get Supabase client."""
    db = get_client()
    if db is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return db


async def get_current_user(authorization: str = Header(None)) -> str:
    """Dependency: extract user_id from Supabase JWT.

    For now, accepts a simple bearer token that is the user's email.
    In production, verify the JWT signature against Supabase's JWKS.
    """
    if not authorization:
        return "anonymous"

    try:
        # Strip "Bearer " prefix
        token = authorization.replace("Bearer ", "").strip()
        if not token:
            return "anonymous"

        # TODO: In production, verify JWT signature against Supabase JWKS
        # For now, decode the JWT payload without verification (dev only)
        import json, base64
        parts = token.split(".")
        if len(parts) == 3:
            # Decode JWT payload (part 2)
            payload = parts[1] + "=" * (4 - len(parts[1]) % 4)  # pad base64
            decoded = json.loads(base64.b64decode(payload))
            user_id = decoded.get("sub") or decoded.get("email") or "anonymous"
            set_user_id(user_id)
            return user_id
    except Exception:
        pass

    return "anonymous"
