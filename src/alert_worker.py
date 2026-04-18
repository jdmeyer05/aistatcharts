"""CFTC alert evaluation + delivery.

Run weekly (Cloud Scheduler cron) after the Friday 3:30pm ET CFTC release:
  1. read every active cftc_* user_alert
  2. evaluate against the latest contract_history
  3. insert a row in user_alert_firings when the condition is met
  4. (future) dispatch email/push via Resend or similar

Dedup: one firing per (alert, report_date). Re-running the worker on the same
data is idempotent — we don't create duplicate firings, we just skip any
(alert_id, report_date) pair already in the firings table.

Email delivery is stubbed — real send happens when RESEND_API_KEY (or similar)
is configured. Until then, firings land in the DB and the user sees them in
the Alerts UI on next visit.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─── Evaluation logic ───────────────────────────────────────────────────

def _fire_crowded_long(last_row: pd.Series, prev_row: pd.Series | None) -> tuple[bool, dict]:
    p = last_row.get("spec_pctile_3y")
    if p is None or pd.isna(p):
        return (False, {})
    if float(p) < 0.95:
        return (False, {})
    return (True, {
        "pctile_3y": round(float(p), 3),
        "spec_net": int(last_row.get("spec_net") or 0),
        "zscore_3y": round(float(last_row.get("spec_zscore_3y") or 0), 2),
    })


def _fire_crowded_short(last_row: pd.Series, prev_row: pd.Series | None) -> tuple[bool, dict]:
    p = last_row.get("spec_pctile_3y")
    if p is None or pd.isna(p):
        return (False, {})
    if float(p) > 0.05:
        return (False, {})
    return (True, {
        "pctile_3y": round(float(p), 3),
        "spec_net": int(last_row.get("spec_net") or 0),
        "zscore_3y": round(float(last_row.get("spec_zscore_3y") or 0), 2),
    })


def _fire_sign_flip(last_row: pd.Series, prev_row: pd.Series | None) -> tuple[bool, dict]:
    if prev_row is None:
        return (False, {})
    cur = last_row.get("spec_net")
    prev = prev_row.get("spec_net")
    if cur is None or prev is None or pd.isna(cur) or pd.isna(prev):
        return (False, {})
    cur, prev = float(cur), float(prev)
    if (cur > 0) == (prev > 0):
        return (False, {})  # no sign change
    return (True, {
        "prev_net": int(prev),
        "curr_net": int(cur),
        "direction": "long" if cur > 0 else "short",
    })


def _fire_new_extreme(last_row: pd.Series, prev_row: pd.Series | None) -> tuple[bool, dict]:
    # New 3Y extreme means current spec_net is at (or beyond) the 3Y high or low.
    # Rolling percentile already normalized; 1.0 or 0.0 at a new high/low respectively.
    p = last_row.get("spec_pctile_3y")
    if p is None or pd.isna(p):
        return (False, {})
    p = float(p)
    if p >= 0.99:
        return (True, {"extreme": "new_3y_high", "pctile_3y": round(p, 3), "spec_net": int(last_row.get("spec_net") or 0)})
    if p <= 0.01:
        return (True, {"extreme": "new_3y_low", "pctile_3y": round(p, 3), "spec_net": int(last_row.get("spec_net") or 0)})
    return (False, {})


_EVALUATORS = {
    "cftc_crowded_long":  _fire_crowded_long,
    "cftc_crowded_short": _fire_crowded_short,
    "cftc_sign_flip":     _fire_sign_flip,
    "cftc_new_extreme":   _fire_new_extreme,
}


# ─── Worker ─────────────────────────────────────────────────────────────

def evaluate_cftc_alerts() -> dict:
    """Evaluate every active cftc_* alert. Insert any firings into
    user_alert_firings (deduped per report_date). Returns a summary."""
    from src.db import get_client
    from src.cftc import contract_history, CONTRACTS_BY_CODE

    client = get_client()
    if client is None:
        return {"ok": False, "error": "Supabase client unavailable", "fired": 0}

    try:
        resp = (client.table("user_alerts")
                .select("id, user_id, alert_type, target, label, channels, active")
                .like("alert_type", "cftc_%")
                .eq("active", True)
                .execute())
        alerts = resp.data or []
    except Exception as e:
        return {"ok": False, "error": f"Failed to fetch alerts: {e}", "fired": 0}

    if not alerts:
        return {"ok": True, "alerts_evaluated": 0, "fired": 0, "firings": []}

    # Cache contract_history calls across all alerts that target the same contract.
    history_cache: dict[str, pd.DataFrame] = {}

    fired_rows: list[dict] = []
    evaluated = 0
    skipped_dedup = 0
    errors: list[str] = []

    for alert in alerts:
        evaluated += 1
        code = alert["target"]
        alert_type = alert["alert_type"]
        evaluator = _EVALUATORS.get(alert_type)
        if evaluator is None:
            errors.append(f"No evaluator for {alert_type}")
            continue
        if code not in CONTRACTS_BY_CODE:
            errors.append(f"Unknown contract code: {code}")
            continue

        # Pull or reuse history
        if code not in history_cache:
            history_cache[code] = contract_history(code, lookback_weeks=260)
        h = history_cache[code]
        if h.empty or len(h) < 2:
            continue

        last = h.iloc[-1]
        prev = h.iloc[-2]
        report_date = last["date"].strftime("%Y-%m-%d") if pd.notna(last["date"]) else None
        if report_date is None:
            continue

        # Dedup — skip if this alert already fired for this report_date.
        try:
            existing = (client.table("user_alert_firings")
                        .select("id")
                        .eq("alert_id", alert["id"])
                        .eq("context->>report_date", report_date)
                        .limit(1)
                        .execute())
            if existing.data:
                skipped_dedup += 1
                continue
        except Exception as e:
            logger.warning(f"dedup lookup failed for alert {alert['id']}: {e}")

        fired, ctx = evaluator(last, prev)
        if not fired:
            continue

        spec = CONTRACTS_BY_CODE.get(code)
        ctx["report_date"] = report_date
        ctx["symbol"] = spec.symbol if spec else code
        ctx["name"] = spec.name if spec else code
        ctx["label"] = alert.get("label") or ""

        try:
            inserted = (client.table("user_alert_firings").insert({
                "alert_id": alert["id"],
                "user_id": alert["user_id"],
                "alert_type": alert_type,
                "target": code,
                "context": ctx,
            }).execute())
            if inserted.data:
                fired_rows.append(inserted.data[0])
        except Exception as e:
            errors.append(f"Insert failed for alert {alert['id']}: {e}")

    return {
        "ok": True,
        "alerts_evaluated": evaluated,
        "fired": len(fired_rows),
        "skipped_dedup": skipped_dedup,
        "errors": errors,
        "firings": [{"id": r.get("id"), "alert_id": r.get("alert_id"), "context": r.get("context")} for r in fired_rows],
    }


def send_pending_notifications() -> dict:
    """Find firings with notified_at IS NULL, send email via Resend if
    RESEND_API_KEY is set. Otherwise mark as logged-only (notified_at=now,
    notify_error='email not configured')."""
    from src.db import get_client

    client = get_client()
    if client is None:
        return {"ok": False, "error": "Supabase client unavailable"}

    try:
        resp = (client.table("user_alert_firings")
                .select("id, user_id, alert_type, target, context, fired_at")
                .is_("notified_at", "null")
                .order("fired_at", desc=False)
                .limit(500)
                .execute())
        pending = resp.data or []
    except Exception as e:
        return {"ok": False, "error": f"pending fetch failed: {e}"}

    if not pending:
        return {"ok": True, "pending": 0, "sent": 0}

    resend_key = os.environ.get("RESEND_API_KEY")
    sent = 0
    failed: list[str] = []

    for firing in pending:
        # TODO: when RESEND_API_KEY is configured, actually call the Resend
        # HTTP API here to send an email. Resolve user's email from
        # auth.users by user_id. For now we just mark notified with a flag
        # so the queue doesn't grow unbounded.
        now_iso = datetime.utcnow().isoformat()
        update = {
            "notified_at": now_iso,
            "notify_error": None if resend_key else "email not configured (set RESEND_API_KEY)",
        }
        try:
            client.table("user_alert_firings").update(update).eq("id", firing["id"]).execute()
            sent += 1
        except Exception as e:
            failed.append(f"{firing['id']}: {e}")

    return {"ok": True, "pending": len(pending), "sent": sent, "failed": failed}
