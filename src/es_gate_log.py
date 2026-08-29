"""Forward log of the conditions gate, so it can eventually be scored.

WHY THIS CANNOT BE REPLAYED, measured rather than assumed. The character read got
a track record for free because it is a pure function of price. The gate is not:

    factor                              replayable?
    session phase                       yes  (clock)
    participation / relative volume     yes  (bars)
    scheduled risk                      yes  (encoded release times)
    day type                            yes  (bars)
    breadth                             yes  — Polygon's grouped-daily endpoint
                                             IS entitled, 12,348 rows for a past
                                             date, so advance/decline can be
                                             reconstructed
    expected move (narrow / wide)       NO   — `I:VIX1D` returns 403; this plan
                                             carries no index entitlement
    range spent                         NO   — derived from the expected move
    dealer gamma                        NO   — needs the option chain with open
                                             interest as it stood that morning,
                                             which is not retained anywhere

Four of eight are replayable. That is not enough, and the shortfall is not
incidental: expected move, range spent and gamma contributed -5 of the -7 the
gate issued on 2026-08-03. Replaying the other four would produce a track record
for a DIFFERENT, weaker instrument and label it as the gate's. So the honest
option is to start the clock and wait.

STORED IN `cftc_cache`, WHICH IS NOT WHAT IT IS NAMED FOR. That table is already
a generic key/value store (`key`, jsonb `value`, `updated_at`) and reusing it
avoids a schema migration. That is a deliberate trade: a new table would be
tidier, and a migration on this project has to be run by hand — the Trump decoder
has been blocked on exactly that for months. A logger that only starts once
somebody remembers to run some SQL is a logger that never starts.

WRITES MUST NEVER AFFECT THE CARD. This is bookkeeping. Every failure path is
swallowed at debug level, and the caller fires it without waiting.

WHO CALLS THIS, AND WHY IT DECIDES WHAT THE RECORD MEANS. Originally only the
`/es-brief` route handler did — so a mark was logged only if somebody had the
page open at that moment, and the eventual record would have been a sample of
WATCHED sessions rather than of sessions. `api.main._gate_log_ticker` now fires
it once per mark on a schedule. The route call is kept because it costs nothing
and fills a mark marginally earlier when the page is open anyway; both write the
same upsert key, so they cannot double-count.
"""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_PREFIX = "es_gate_log"
# One snapshot per 30-minute mark. Finer would log the same verdict repeatedly;
# coarser would miss the gate changing its mind through the session, which is
# itself something worth being able to score.
_MARKS = [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0]


def _mark(now: pd.Timestamp) -> float | None:
    hrs = now.hour + now.minute / 60
    past = [m for m in _MARKS if m <= hrs]
    return past[-1] if past else None


def snapshot_key(session_day: str, mark: float) -> str:
    return f"{_PREFIX}:{session_day}:{mark:04.1f}"


def log_gate(brief: dict, now: pd.Timestamp | None = None) -> str | None:
    """Record what the card said, keyed so a repeat within the mark overwrites.

    Returns the key written, or None when there was nothing to record. Upsert
    rather than insert: the brief rebuilds every 90 seconds and an append-only
    log would carry twenty identical rows per mark.
    """
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)

    session = (brief or {}).get("session") or {}
    if not str(session.get("phase", "")).startswith("rth"):
        return None                      # only the cash session is scoreable
    mark = _mark(now)
    if mark is None:
        return None

    day = (brief or {}).get("session_day") or str(now.date())
    cond = (brief or {}).get("conditions") or {}
    regime = (brief or {}).get("regime") or {}
    path = regime.get("path_implied") or {}
    em = (brief or {}).get("expected_move") or {}
    lv = (brief or {}).get("levels") or {}

    payload = {
        "session_day": str(day)[:10],
        "mark": mark,
        "asof": now.isoformat(),
        # What the gate said, and — the part that makes it scoreable later —
        # the factors it said it from. A verdict without its reasons cannot be
        # diagnosed after the fact, only tallied.
        "verdict": cond.get("verdict"),
        "score": cond.get("score"),
        "factors_scored": cond.get("factors_scored"),
        "reasons": [{"factor": r.get("factor"), "effect": r.get("effect")}
                    for r in cond.get("reasons", [])],
        "character": regime.get("character"),
        "multiplier": path.get("multiplier"),
        "expected_range": em.get("expected_range"),
        "consumed_pct": (em.get("consumed") or {}).get("pct"),
        # The state at the time, so the outcome can be joined without needing
        # the bars re-fetched and re-aligned later.
        "last": lv.get("last"),
        "session_high": next((l.get("value") for l in lv.get("levels", [])
                              if l.get("key") == "today_high"), None),
        "session_low": next((l.get("value") for l in lv.get("levels", [])
                             if l.get("key") == "today_low"), None),
    }

    key = snapshot_key(payload["session_day"], mark)
    try:
        from src.db import get_client
        client = get_client()
        if client is None:
            return None
        client.table("cftc_cache").upsert({
            "key": key,
            "value": payload,
            "updated_at": datetime.utcnow().isoformat(),
        }).execute()
        return key
    except Exception as e:
        logger.debug(f"gate log write failed for {key}: {e}")
        return None


def read_snapshots(limit: int = 2000) -> list[dict]:
    """Every logged snapshot, newest first."""
    try:
        from src.db import get_client
        client = get_client()
        if client is None:
            return []
        resp = (client.table("cftc_cache")
                .select("key, value, updated_at")
                .like("key", f"{_PREFIX}:%")
                .order("key", desc=True)
                .limit(limit).execute())
        return [r["value"] for r in (resp.data or []) if isinstance(r.get("value"), dict)]
    except Exception as e:
        logger.debug(f"gate log read failed: {e}")
        return []


def gate_track_record(min_sessions: int = 30) -> dict:
    """Score the logged verdicts against what the sessions actually did.

    Deliberately refuses to report below `min_sessions`. A track record computed
    on five days would be quoted as though it meant something, and the whole
    reason this module exists is that unscored modules get trusted by default —
    replacing that with a badly-scored one is not an improvement.
    """
    snaps = read_snapshots()
    if not snaps:
        return {"available": False, "reason": "no snapshots logged yet",
                "logging_since": None, "sessions": 0}

    days = sorted({s.get("session_day") for s in snaps if s.get("session_day")})
    # Only sessions that have completed can be scored.
    today = str(pd.Timestamp.now(tz=_TZ).date())
    done = [d for d in days if d < today]

    if len(done) < min_sessions:
        return {
            "available": False,
            "reason": (f"logging since {days[0]} — {len(done)} completed sessions of "
                       f"{min_sessions} needed before this is worth quoting"),
            "logging_since": days[0],
            "sessions": len(done),
            "needed": min_sessions,
            "snapshots": len(snaps),
        }

    from src.es_baserates import _polygon_5m, _INTRADAY_SYMBOL
    fine = _polygon_5m(_INTRADAY_SYMBOL, 5)
    if fine is None or fine.empty:
        return {"available": False, "reason": "no bar history to score against"}
    fine = fine.copy()
    fine["day"] = fine.index.normalize()
    ses = fine.groupby("day").agg(hi=("High", "max"), lo=("Low", "min"),
                                  open=("Open", "first"), close=("Close", "last"))
    ses["rng"] = ses["hi"] - ses["lo"]
    ses["normal"] = ses["rng"].shift(1).rolling(20).median()
    by_day = {str(d.date()): r for d, r in ses.iterrows()}

    rows = []
    for s in snaps:
        d = s.get("session_day")
        if d not in done or d not in by_day:
            continue
        o = by_day[d]
        norm = o["normal"]
        # `not norm` does NOT catch NaN — `not float('nan')` is False and
        # `nan <= 0` is also False, so a session whose trailing-20 median had
        # not filled yet fell straight through and produced actual_x = NaN.
        # The median then silently skipped it while `(x >= 1.3).mean()` counted
        # it as a non-wide day, understating every wide_pct on the board.
        if norm is None or norm != norm or norm <= 0:
            continue
        rows.append({"session_day": d, "verdict": s.get("verdict"), "score": s.get("score"),
                     "actual_x": o["rng"] / o["normal"],
                     "closed_up": bool(o["close"] > o["open"])})
    if not rows:
        return {"available": False, "reason": "snapshots did not join to any completed session"}

    df = pd.DataFrame(rows)
    buckets = []
    for v in ("favourable", "workable", "poor", "stand aside"):
        b = df[df["verdict"] == v]
        # ONE SESSION IS ONE OBSERVATION, not one snapshot. A session is logged
        # at up to eleven marks, and every mark carries the SAME outcome — the
        # day's range and close are properties of the day. Counting marks made
        # `n` look like a sample size when it was a measure of how long the
        # verdict stood, and eleven copies of one session cannot tell you
        # anything eleven different sessions could. So the outcome columns are
        # collapsed to one row per session before anything is computed, and the
        # gate on sufficiency is on distinct sessions.
        if b.empty:
            continue
        per_session = b.drop_duplicates(subset="session_day")
        if len(per_session) < 10:
            continue
        buckets.append({
            "verdict": v,
            "n_sessions": int(len(per_session)),
            # Kept and labelled rather than dropped: how many marks a verdict
            # stood for says how persistent it was, which is a real question —
            # it is just not a sample size.
            "n_marks": int(len(b)),
            "median_range_x": round(float(per_session["actual_x"].median()), 2),
            "wide_pct": round(float((per_session["actual_x"] >= 1.3).mean()) * 100, 1),
            "closed_up_pct": round(float(per_session["closed_up"].mean()) * 100, 1),
        })

    per_session_all = df.drop_duplicates(subset="session_day")

    return {
        "available": True,
        "logging_since": days[0],
        "sessions": len(done),
        "snapshots": len(snaps),
        "buckets": buckets,
        # Base rates over DISTINCT SESSIONS, for the same reason the buckets
        # are. Computed over marks these were weighted by how many times each
        # session happened to be logged.
        "base_wide_pct": round(float((per_session_all["actual_x"] >= 1.3).mean()) * 100, 1),
        "base_up_pct": round(float(per_session_all["closed_up"].mean()) * 100, 1),
        "scored_sessions": int(len(per_session_all)),
        "caveat": (
            "Scored from snapshots taken at the time, not reconstructed — the gate "
            "reads dealer gamma and an options-implied range, neither of which is "
            "retained historically, so it cannot be replayed the way the character "
            "read can. The verdict is about CONDITIONS, so the range column is the "
            "one that speaks to it; the direction column is there to confirm it "
            "carries none. One session counts once per bucket however many marks it "
            "was logged at — a verdict that stood all day is one observation, not "
            "eleven."
        ),
    }
