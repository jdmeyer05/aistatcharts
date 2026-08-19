"""Freeze what the model saw and what it said, on the request path, for free.

THIS IS THE WHOLE FOUNDATION. Grading, replay, A/B and every accuracy number
downstream exist because this module writes `payload` next to `output`. Without
the payload a past output can only be re-read, never re-run; with it, a new
prompt can be scored on two hundred historical situations whose outcomes are
already known.

RULES OF THE REQUEST PATH. Recording happens on a daemon thread and swallows
every exception. A snapshot that fails to write is a lost row; a snapshot that
raises is a broken home page. Only real model calls are recorded — cache hits
are skipped, which also caps volume without any sampling logic, because the
production caches already gate how often a generation actually happens.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Payload and output are stored whole; these caps exist so one pathological
# object cannot bloat the table. Both sit far above what the prompts actually
# send (12k for market-driver, 20k for interpret).
_MAX_PAYLOAD_CHARS = 80_000
_MAX_OUTPUT_CHARS = 40_000

# Share of live snapshots reserved for out-of-sample scoring. The critic reads
# discovery rows and never sees holdout rows, so a challenger cannot be tuned
# on the sample that decides whether it is promoted.
_HOLDOUT_SHARE = 0.4


def _db():
    try:
        from src.db import get_client
        return get_client()
    except Exception:
        return None


def _truncate(obj, limit: int):
    """Keep it JSON, keep it small, and say so when something was dropped."""
    try:
        s = json.dumps(obj, default=str)
    except Exception:
        return {"_unserializable": str(type(obj))}
    if len(s) <= limit:
        try:
            return json.loads(s)
        except Exception:
            return {"_unparseable": s[:1000]}
    return {"_truncated": True, "_original_chars": len(s), "_head": s[:limit]}


def _split_for(seed: str) -> str:
    """Deterministic assignment from a hash of the payload.

    Deliberately not random: the same payload always lands in the same split,
    so re-seeding or re-importing a row cannot quietly move a holdout case into
    the discovery set the critic is allowed to read.
    """
    h = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    return "holdout" if (h % 1000) < int(_HOLDOUT_SHARE * 1000) else "discovery"


def session_phase() -> str:
    """Coarse market clock, so scores can be read per phase rather than pooled.

    A narrative written at 03:00 on a Sunday and one written at 10:05 on a CPI
    morning are not the same task, and averaging them hides both.
    """
    try:
        import pandas as pd
        now = pd.Timestamp.now(tz="America/New_York")
    except Exception:
        return "unknown"
    if now.weekday() >= 5:
        return "weekend"
    t = now.time()
    from datetime import time as _t
    if _t(9, 30) <= t < _t(11, 0):
        return "rth_open"
    if _t(11, 0) <= t < _t(15, 0):
        return "rth_midday"
    if _t(15, 0) <= t < _t(16, 0):
        return "rth_close"
    if _t(4, 0) <= t < _t(9, 30):
        return "premarket"
    if _t(16, 0) <= t < _t(20, 0):
        return "postmarket"
    return "overnight"


def record(surface: str, payload, output, *, prompt_version: int = 0,
           model: str = "", meta: dict | None = None, blocking: bool = False,
           is_replay: bool = False, replay_of: int | None = None,
           split: str | None = None, claims: list | None = None) -> int | None:
    """Persist one generation. Returns the snapshot id when blocking.

    `claims` are stored inside the same writer, because they need the snapshot
    id that only exists after the insert. Replays never store claims — a claim
    made by a challenger about a week that has already happened is not a
    forecast, and letting those into `ai_claims` would quietly poison the one
    table in this system that is measured against reality.
    """
    args = (surface, payload, output, prompt_version, model, meta or {},
            is_replay, replay_of, split, claims or [])
    if blocking:
        return _write(*args)

    threading.Thread(target=_write, args=args, daemon=True).start()
    return None


def _write(surface, payload, output, prompt_version, model, meta,
           is_replay, replay_of, split, claims) -> int | None:
    db = _db()
    if db is None:
        return None
    try:
        pay = _truncate(payload, _MAX_PAYLOAD_CHARS)
        out = _truncate(output, _MAX_OUTPUT_CHARS)
        seed = json.dumps(pay, sort_keys=True, default=str)[:4000]
        row = {
            "surface": surface,
            "prompt_version": int(prompt_version or 0),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "session_phase": session_phase(),
            "model": model or "",
            "payload": pay,
            "output": out,
            "meta": meta or {},
            "is_replay": bool(is_replay),
            "replay_of": replay_of,
            # A replay inherits the split of the row it replays — assigning it a
            # fresh one would let a challenger's own output vote on which sample
            # it gets judged in.
            "split": split or _split_for(seed),
        }
        res = db.table("ai_snapshots").insert(row).execute()
        rows = res.data or []
        snap_id = int(rows[0]["id"]) if rows else None
    except Exception as e:
        logger.debug(f"prompt_snapshots: write failed for {surface}: {e}")
        return None

    if snap_id and claims and not is_replay:
        try:
            from src import prompt_claims
            kept = prompt_claims.extract({"calls": claims}, payload)
            if kept:
                prompt_claims.store(snap_id, surface, kept)
        except Exception as e:
            logger.debug(f"prompt_snapshots: claim store failed: {e}")

    return snap_id


def fetch(surface: str, *, split: str | None = None, limit: int = 200,
          days: int = 45, include_replays: bool = False,
          prompt_version: int | None = None) -> list[dict]:
    db = _db()
    if db is None:
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        q = db.table("ai_snapshots").select("*").eq("surface", surface).gte("created_at", since)
        if not include_replays:
            q = q.eq("is_replay", False)
        if split:
            q = q.eq("split", split)
        if prompt_version is not None:
            q = q.eq("prompt_version", prompt_version)
        return q.order("created_at", desc=True).limit(limit).execute().data or []
    except Exception as e:
        logger.warning(f"prompt_snapshots: fetch failed for {surface}: {e}")
        return []


def get(snapshot_id: int) -> dict | None:
    db = _db()
    if db is None:
        return None
    try:
        rows = db.table("ai_snapshots").select("*").eq("id", snapshot_id).limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception:
        return None


def prune(days: int = 120) -> int:
    """Drop snapshots past the retention window. Grades and claims cascade."""
    db = _db()
    if db is None:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        res = db.table("ai_snapshots").delete().lt("created_at", cutoff).execute()
        return len(res.data or [])
    except Exception as e:
        logger.warning(f"prompt_snapshots: prune failed: {e}")
        return 0
