"""A stored history of the vol-scan summary, so its thresholds can be checked.

WHY THIS EXISTS. The scan classifies a regime off round numbers — 1.10 for
"steep skew", 1.20/0.85 for rich/cheap IV against HV — that were never checked
against the distribution they gate. Audited on the live universe 2026-08-02:

  - The 1.10 skew cut sits at the **50th percentile** of the 20-name cross
    section. "Broad Fear - Steep Skew" fires when more than half the universe
    is above roughly its own median, which is a coin flip wearing a number.
  - The 0.85 IV/HV cut sits at the **0th percentile**: not one single name is
    below it, so an AVERAGE of twenty names cannot realistically reach it. That
    branch is close to dead code. A cut calibrated on single-name intuition was
    applied to a mean, whose dispersion is far smaller.

Neither can be fixed by picking better round numbers. Both need a reference
distribution for the measure itself, and none was ever stored — every scan
overwrote the last. This records one observation per session day so that a
percentile becomes available, and reports `n_history` so a reader can see how
much it rests on rather than being handed a confident-looking number.

Until the history is deep enough, percentiles come back None. None means "not
yet knowable", which is the whole point — a 50 would be an invented middle.

Storage is the existing `cftc_cache` key/value table under a single key, as a
capped JSON array. That avoids a schema migration for what is a small append-
only log.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

logger = logging.getLogger(__name__)

_KEY = "vol_summary_history_v1"

# ~2 years of session days. The cap matters: this lives in one JSON value, and
# an unbounded array would grow the read cost of every scan.
_MAX_ROWS = 520

# A percentile from a handful of observations is theatre. 60 session days is
# about a quarter, which is the shortest window over which "unusual" means
# anything for a vol measure.
_MIN_HISTORY = 60

# The measures worth tracking. Each is a scalar the regime logic actually reads,
# or one it should read instead of a cross-sectional count.
TRACKED = ("avg_iv", "avg_ivhv", "avg_skew", "median_skew",
           "n_inverted", "n_steep_skew", "impl_corr")


def _load() -> list[dict]:
    try:
        from src._cache_util import _supabase_get
        hit = _supabase_get(_KEY)
        if not hit:
            return []
        rows = hit[1]
        return rows if isinstance(rows, list) else []
    except Exception as e:
        logger.debug(f"vol history load failed: {e}")
        return []


def _save(rows: list[dict]) -> None:
    try:
        from src._cache_util import _supabase_put
        _supabase_put(_KEY, rows)
    except Exception as e:
        logger.debug(f"vol history save failed: {e}")


def record(summary: dict, session_date: date | None = None,
           healthy: bool = True) -> list[dict]:
    """Append (or replace) today's observation and return the full history.

    One row per session day, latest write wins — the scan runs many times a day
    behind a cache and every run would otherwise land as a separate observation,
    which would weight busy days more heavily than quiet ones for no reason.

    `healthy` is the caller's judgement that the scan was not degraded. A
    partial universe would enter the record as a real reading and then quietly
    distort every percentile computed against it afterwards.
    """
    if not healthy or not summary:
        return _load()

    d = (session_date or datetime.now(timezone.utc).date()).isoformat()
    row = {"date": d}
    for k in TRACKED:
        v = summary.get(k)
        row[k] = float(v) if isinstance(v, (int, float)) else None

    rows = [r for r in _load() if r.get("date") != d]
    rows.append(row)
    rows.sort(key=lambda r: r.get("date") or "")
    rows = rows[-_MAX_ROWS:]
    _save(rows)
    return rows


def percentiles(rows: list[dict], summary: dict) -> dict:
    """Where each tracked measure sits in its own recorded history.

    Excludes today's own observation from the reference set, so the answer is
    "against what came before" rather than a value being partly compared to
    itself.
    """
    out: dict = {}
    today = (rows[-1].get("date") if rows else None)
    prior = [r for r in rows if r.get("date") != today]
    for k in TRACKED:
        cur = summary.get(k)
        vals = [r[k] for r in prior if isinstance(r.get(k), (int, float))]
        if not isinstance(cur, (int, float)) or len(vals) < _MIN_HISTORY:
            out[k] = {"pctile": None, "n_history": len(vals)}
            continue
        below = sum(1 for v in vals if v < cur)
        out[k] = {"pctile": round(below / len(vals) * 100, 1),
                  "n_history": len(vals)}
    return out


def threshold_report(mdf, cuts: dict[str, float]) -> dict:
    """Where each hardcoded cut sits in TODAY's cross section.

    This is not validation — a cut can sit anywhere in one day's cross section
    and still be meaningful against history. It is disclosure: a cut sitting at
    the median cannot separate a regime from its opposite, and that fact should
    be visible in the payload rather than discoverable only by auditing.
    """
    out: dict = {}
    for name, spec in cuts.items():
        col, cut = spec["column"], spec["cut"]
        if col not in getattr(mdf, "columns", []):
            out[name] = {"cut": cut, "column": col, "pctile_in_universe": None}
            continue
        s = mdf[col].dropna().astype(float)
        if s.empty:
            out[name] = {"cut": cut, "column": col, "pctile_in_universe": None}
            continue
        pct = float((s < cut).mean() * 100)
        out[name] = {
            "cut": cut,
            "column": col,
            "pctile_in_universe": round(pct, 1),
            "n": int(len(s)),
            # A cut inside this band splits the universe roughly in half, so any
            # label it drives is close to a coin flip on that day's data.
            "near_median": bool(35 <= pct <= 65),
            "validated": False,
        }
    return out
