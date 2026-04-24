"""Smart Money Alerts — user subscriptions for tracked filings / tickers.

Backend CRUD for the `public.user_alerts` table. The actual notification
delivery worker (EDGAR RSS poller + email/SMS dispatch) is a separate
scheduled job — this module just manages subscriptions.

Schema: see supabase_alerts_schema.sql
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_current_user, get_db, log_user, require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


AlertType = Literal[
    "fund", "ticker", "politician", "activist", "keyword",
    # CFTC positioning alerts (target = contract code, label holds threshold):
    "cftc_crowded_long", "cftc_crowded_short", "cftc_sign_flip", "cftc_new_extreme",
]
Channel = Literal["email", "sms", "push"]


class AlertCreate(BaseModel):
    alert_type: AlertType
    target: str
    label: str | None = None
    channels: list[Channel] = ["email"]


class AlertUpdate(BaseModel):
    active: bool | None = None
    label: str | None = None
    channels: list[Channel] | None = None


@router.get("/alerts")
async def list_alerts(
    user: str = Depends(get_current_user),
    db=Depends(get_db),
):
    """List the current user's alerts, newest first."""
    if user == "anonymous":
        raise HTTPException(401, "Sign in required")
    try:
        res = (
            db.table("user_alerts")
            .select("*")
            .eq("user_email", user)
            .order("created_at", desc=True)
            .execute()
        )
        return {"count": len(res.data or []), "data": res.data or []}
    except Exception as e:
        logger.warning(f"list_alerts failed for {log_user(user)}: {e}")
        # Table-missing is the most common cause before the schema is applied;
        # return empty so the UI can render its empty state instead of erroring.
        msg = str(e).lower()
        if "relation" in msg and ("user_alerts" in msg or "does not exist" in msg):
            return {"count": 0, "data": [], "setup_required": True}
        raise HTTPException(500, f"Alerts query failed: {e}")


@router.post("/alerts")
async def create_alert(
    body: AlertCreate,
    user: str = Depends(get_current_user),
    db=Depends(get_db),
):
    if user == "anonymous":
        raise HTTPException(401, "Sign in required")
    target = body.target.strip()
    if not target:
        raise HTTPException(400, "Target cannot be empty")
    try:
        res = (
            db.table("user_alerts")
            .insert({
                "user_email": user,
                "alert_type": body.alert_type,
                "target": target,
                "label": body.label,
                "channels": body.channels,
                "active": True,
            })
            .execute()
        )
        rec = (res.data or [None])[0]
        return {"ok": True, "alert": rec}
    except Exception as e:
        logger.warning(f"create_alert failed: {e}")
        raise HTTPException(500, f"Alert creation failed: {e}")


@router.patch("/alerts/{alert_id}")
async def update_alert(
    alert_id: str,
    body: AlertUpdate,
    user: str = Depends(get_current_user),
    db=Depends(get_db),
):
    if user == "anonymous":
        raise HTTPException(401, "Sign in required")
    patch = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if not patch:
        return {"ok": True, "changed": 0}
    try:
        res = (
            db.table("user_alerts")
            .update(patch)
            .eq("id", alert_id)
            .eq("user_email", user)
            .execute()
        )
        return {"ok": True, "changed": len(res.data or [])}
    except Exception as e:
        logger.warning(f"update_alert failed: {e}")
        raise HTTPException(500, f"Alert update failed: {e}")


@router.get("/alerts/firings")
async def list_firings(
    limit: int = 20,
    user: str = Depends(get_current_user),
    db=Depends(get_db),
):
    """Recent alert firings for the current user. Lets the UI show 'you have N
    new alerts' and a list of what triggered."""
    if user == "anonymous":
        raise HTTPException(401, "Sign in required")
    try:
        # user_alert_firings.user_id is a UUID; look up the auth user's id
        # from user_alerts to avoid having to pass the UUID through the API.
        uid_rows = (
            db.table("user_alerts")
            .select("user_id")
            .eq("user_email", user)
            .limit(1)
            .execute()
        )
        if not uid_rows.data:
            return {"count": 0, "firings": []}
        user_id = uid_rows.data[0]["user_id"]
        res = (
            db.table("user_alert_firings")
            .select("*")
            .eq("user_id", user_id)
            .order("fired_at", desc=True)
            .limit(max(1, min(limit, 100)))
            .execute()
        )
        return {"count": len(res.data or []), "firings": res.data or []}
    except Exception as e:
        logger.warning(f"list_firings failed: {e}")
        raise HTTPException(500, f"Firings fetch failed: {e}")


@router.post("/alerts/run-worker")
async def run_worker(user: str = Depends(require_admin)):
    """Evaluate all active CFTC alerts against the latest data and persist
    firings. Admin-only (and eventually called by a Cloud Scheduler cron).
    Safe to re-run — idempotent per (alert, report_date)."""
    from src.alert_worker import evaluate_cftc_alerts, send_pending_notifications
    eval_result = evaluate_cftc_alerts()
    notif_result = send_pending_notifications()
    return {"evaluation": eval_result, "notifications": notif_result}


@router.delete("/alerts/{alert_id}")
async def delete_alert(
    alert_id: str,
    user: str = Depends(get_current_user),
    db=Depends(get_db),
):
    if user == "anonymous":
        raise HTTPException(401, "Sign in required")
    try:
        res = (
            db.table("user_alerts")
            .delete()
            .eq("id", alert_id)
            .eq("user_email", user)
            .execute()
        )
        return {"ok": True, "deleted": len(res.data or [])}
    except Exception as e:
        logger.warning(f"delete_alert failed: {e}")
        raise HTTPException(500, f"Alert delete failed: {e}")
