"""Intraday structure for the E-mini — the shape of the session, not its levels.

`es_levels` answers "where are the reference prices". This answers "what kind
of session is this, and is anything actually behind the move":

  opening range / initial balance   the first hour frames the whole day
  day type                          trend vs normal vs neutral, in profile terms
  relative volume                   participation vs the same time on a normal day
  overnight inventory               is the book lopsided into the open
  naked POCs                        prior fair prices never revisited — magnets
  gaps                              unfilled RTH gaps
  cross-asset                       is NQ/RTY/rates/dollar confirming or diverging

Everything except the cross-asset block comes from the same 5-minute bars
`es_levels` already fetches, so it costs one extra request at most.

A NOTE ON WHAT THESE ARE. Initial balance, day types and naked POCs come from
market profile, which is a framework for describing auction behaviour, not a
predictive model. They organise what the session is doing; they do not say what
it will do next. Base rates for that live in `es_baserates`, measured rather
than assumed.
"""

from __future__ import annotations

import logging
from datetime import time as dtime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_RTH_OPEN = dtime(9, 30)
_RTH_CLOSE = dtime(16, 0)
_IB_MINUTES = 60          # initial balance — the first hour of the cash session


def _rth_mask(idx: pd.DatetimeIndex) -> np.ndarray:
    return np.array([_RTH_OPEN <= t.time() < _RTH_CLOSE for t in idx])


def _session_rth(bars: pd.DataFrame, day: pd.Timestamp) -> pd.DataFrame:
    """RTH bars for one session date."""
    d = pd.Timestamp(day).normalize()
    sel = bars[(bars.index.normalize() == d) & _rth_mask(bars.index)]
    return sel


# ── Opening range and initial balance ─────────────────────────────

def opening_range(rth: pd.DataFrame) -> dict:
    """Opening ranges and the initial balance, with extension.

    The IB is the first hour. Whether price extends beyond it, and by how much,
    is the classic read on whether the day is trending or rotating: a session
    that never leaves its IB is a balancing day, one that extends and holds is
    an initiative move.
    """
    if rth.empty:
        return {"available": False}

    start = rth.index.min()
    out: dict = {"available": True, "rth_start": start.isoformat()}

    for mins, key in ((5, "or5"), (15, "or15"), (30, "or30")):
        w = rth[rth.index < start + pd.Timedelta(minutes=mins)]
        if w.empty:
            continue
        out[key] = {
            "high": round(float(w["High"].max()), 2),
            "low": round(float(w["Low"].min()), 2),
            "range": round(float(w["High"].max() - w["Low"].min()), 2),
            "complete": bool(rth.index.max() >= start + pd.Timedelta(minutes=mins)),
        }

    ib = rth[rth.index < start + pd.Timedelta(minutes=_IB_MINUTES)]
    if ib.empty:
        return out

    ib_hi, ib_lo = float(ib["High"].max()), float(ib["Low"].min())
    ib_complete = bool(rth.index.max() >= start + pd.Timedelta(minutes=_IB_MINUTES))
    after = rth[rth.index >= start + pd.Timedelta(minutes=_IB_MINUTES)]

    ext_up = float(after["High"].max() - ib_hi) if not after.empty else 0.0
    ext_dn = float(ib_lo - after["Low"].min()) if not after.empty else 0.0
    out["ib"] = {
        "high": round(ib_hi, 2),
        "low": round(ib_lo, 2),
        "range": round(ib_hi - ib_lo, 2),
        "complete": ib_complete,
        # Only extension beyond the IB counts; negative means it never got there.
        "extension_up": round(max(0.0, ext_up), 2),
        "extension_down": round(max(0.0, ext_dn), 2),
        "extended": bool(max(ext_up, ext_dn) > 0),
    }
    return out


def day_type(rth: pd.DataFrame, ib: dict | None) -> dict:
    """Classify the session in market-profile terms.

    The distinction that matters intraday is whether the auction is SEEKING a
    new price (trend / initiative) or ROTATING around an accepted one
    (balance). Measured two ways that have to agree-ish: how far price ranged
    against its initial balance, and where it closed within the day's range —
    a true trend day closes near its extreme because it never rotated back.
    """
    if rth.empty or not ib:
        return {"available": False}

    hi, lo = float(rth["High"].max()), float(rth["Low"].min())
    rng = hi - lo
    if rng <= 0 or not ib.get("range"):
        return {"available": False}

    last = float(rth["Close"].iloc[-1])
    close_pos = (last - lo) / rng                       # 0 = at the low, 1 = at the high
    ib_mult = rng / ib["range"] if ib["range"] > 0 else 1.0
    ext = max(ib.get("extension_up", 0), ib.get("extension_down", 0))
    direction = "up" if ib.get("extension_up", 0) >= ib.get("extension_down", 0) else "down"

    if not ib.get("complete"):
        label, note = "forming", "Initial balance still building — too early to type the day."
    elif ib_mult >= 2.0 and (close_pos >= 0.75 or close_pos <= 0.25):
        label = "trend"
        note = ("Range is 2x the initial balance and price is holding near the extreme — "
                "an initiative move that hasn't rotated back. Fading these is what hurts.")
    elif ext > 0 and ib_mult >= 1.25:
        label = "normal variation"
        note = ("Price extended beyond the initial balance but is still rotating — "
                "the range is being explored, not abandoned.")
    elif ext <= 0:
        label = "balance"
        note = ("Contained inside the initial balance. Rotational, and the edges of the IB "
                "are the reference — a break of one is the first real signal.")
    else:
        label = "normal"
        note = "A typical session — some extension beyond the IB, still two-sided."

    return {
        "available": True,
        "label": label,
        "note": note,
        "ib_multiple": round(float(ib_mult), 2),
        "close_position": round(float(close_pos), 3),
        "extension_direction": direction if ext > 0 else None,
        "range": round(rng, 2),
    }


# ── Participation ─────────────────────────────────────────────────

def relative_volume(bars: pd.DataFrame, session_day: pd.Timestamp,
                    lookback_sessions: int = 10) -> dict:
    """Cumulative RTH volume vs the same point of a typical session.

    Volume is heavily shaped by time of day — comparing a 10:15 total against a
    full-day average says nothing. This compares like for like: cumulative
    volume this many minutes into the session, against the median of the same
    elapsed point across recent sessions.

    A move on sub-1.0 relative volume is drifting; the same move at 1.5x has
    participation behind it and is far more likely to hold.
    """
    rth_all = bars[_rth_mask(bars.index)]
    if rth_all.empty:
        return {"available": False}

    day = pd.Timestamp(session_day).normalize()
    cur = rth_all[rth_all.index.normalize() == day]
    if cur.empty:
        return {"available": False, "reason": "no RTH bars for the session"}

    elapsed = int((cur.index.max() - cur.index.min()).total_seconds() // 60) + 5
    cur_vol = float(cur["Volume"].sum())

    # Same elapsed window on each prior session.
    priors = sorted({d for d in rth_all.index.normalize().unique() if d < day})[-lookback_sessions:]
    comps: list[float] = []
    for d in priors:
        s = rth_all[rth_all.index.normalize() == d]
        if s.empty:
            continue
        w = s[s.index < s.index.min() + pd.Timedelta(minutes=elapsed)]
        v = float(w["Volume"].sum())
        if v > 0:
            comps.append(v)

    if len(comps) < 3:
        return {"available": False, "reason": "not enough comparable sessions"}

    typical = float(np.median(comps))
    ratio = cur_vol / typical if typical > 0 else None
    if ratio is None:
        return {"available": False}

    if ratio >= 1.5:
        verdict, note = "heavy", "Well above a normal pace — moves have real participation behind them."
    elif ratio >= 1.15:
        verdict, note = "above average", "Healthy participation."
    elif ratio >= 0.85:
        verdict, note = "normal", "Typical pace for this point in the session."
    elif ratio >= 0.6:
        verdict, note = "light", "Below-average participation — breaks are more prone to fail."
    else:
        verdict, note = "very light", "Barely anyone here. Drift and false breaks are the norm."

    return {
        "available": True,
        "ratio": round(ratio, 2),
        "verdict": verdict,
        "note": note,
        "elapsed_minutes": elapsed,
        "session_volume": int(cur_vol),
        "typical_volume": int(typical),
        "sessions_compared": len(comps),
    }


# ── Overnight inventory ───────────────────────────────────────────

def overnight_inventory(overnight: pd.DataFrame, prior_high: float | None,
                        prior_low: float | None, last: float) -> dict:
    """Whether the Globex session left the book lopsided into the cash open.

    If the overnight has run well beyond the prior day's range in one
    direction, positions built in thin liquidity are offside or over-extended,
    and the first hour of RTH frequently corrects some of it. That is an
    inventory imbalance, and it is one of the more reliable open-auction reads.
    """
    if overnight.empty:
        return {"available": False}

    on_hi = float(overnight["High"].max())
    on_lo = float(overnight["Low"].min())
    on_rng = on_hi - on_lo
    pos = (last - on_lo) / on_rng if on_rng > 0 else None

    took_high = bool(prior_high is not None and on_hi > prior_high)
    took_low = bool(prior_low is not None and on_lo < prior_low)

    if took_high and not took_low:
        skew = "long"
        note = ("Overnight took out the prior day's high on thin volume. Inventory is long "
                "and often gets corrected lower in the first hour.")
    elif took_low and not took_high:
        skew = "short"
        note = ("Overnight took out the prior day's low on thin volume. Inventory is short "
                "and often gets squeezed higher early.")
    elif took_high and took_low:
        skew = "two-sided"
        note = "Overnight ran both sides of the prior range — no clean inventory read, and a wide reference."
    else:
        skew = "balanced"
        note = "Overnight stayed inside the prior day's range. No inventory imbalance to correct."

    return {
        "available": True,
        "high": round(on_hi, 2),
        "low": round(on_lo, 2),
        "range": round(on_rng, 2),
        "position_in_range": round(float(pos), 3) if pos is not None else None,
        "took_prior_high": took_high,
        "took_prior_low": took_low,
        "skew": skew,
        "note": note,
        "bars": int(len(overnight)),
    }


# ── Naked POCs and gaps ───────────────────────────────────────────

def _session_poc(rth: pd.DataFrame, bins: int = 60) -> float | None:
    """POC of one session — same binning as es_levels for consistency."""
    if rth.empty or rth["Volume"].sum() <= 0:
        return None
    tp = (rth["High"] + rth["Low"] + rth["Close"]) / 3
    lo, hi = float(rth["Low"].min()), float(rth["High"].max())
    if hi <= lo:
        return None
    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(tp.values, edges) - 1, 0, bins - 1)
    vol = np.zeros(bins)
    np.add.at(vol, idx, rth["Volume"].values)
    if vol.sum() <= 0:
        return None
    centers = (edges[:-1] + edges[1:]) / 2
    return float(centers[int(vol.argmax())])


def naked_pocs(bars: pd.DataFrame, session_day: pd.Timestamp, last: float,
               max_back: int = 12) -> list[dict]:
    """Prior sessions' points of control that price has not traded back to.

    A POC is where an auction spent the most volume — its fairest price. One
    that later sessions never returned to tends to act as a magnet, because
    the auction left business unfinished there. Once price trades through it
    the level is "filled" and stops mattering, so only untouched ones are kept.
    """
    rth_all = bars[_rth_mask(bars.index)]
    if rth_all.empty:
        return []

    day = pd.Timestamp(session_day).normalize()
    days = sorted({d for d in rth_all.index.normalize().unique() if d < day})[-max_back:]

    out: list[dict] = []
    for i, d in enumerate(days):
        s = rth_all[rth_all.index.normalize() == d]
        poc = _session_poc(s)
        if poc is None:
            continue
        # Touched if ANY later bar (RTH or Globex) traded through it.
        later = bars[bars.index > s.index.max()]
        if later.empty:
            continue
        touched = bool(((later["Low"] <= poc) & (later["High"] >= poc)).any())
        if touched:
            continue
        out.append({
            "date": str(pd.Timestamp(d).date()),
            "value": round(poc, 2),
            "distance": round(last - poc, 2),
            "side": "above" if last >= poc else "below",
            "sessions_ago": len(days) - i,
        })

    out.sort(key=lambda x: abs(x["distance"]))
    return out


def unfilled_gaps(bars: pd.DataFrame, session_day: pd.Timestamp, last: float,
                  max_back: int = 12, min_handles: float = 2.0) -> list[dict]:
    """RTH open-vs-prior-close gaps that price has not traded back through.

    Measured on the cash session because that is the gap traders reference —
    ES itself trades through the night, so there is no literal price gap, but
    the distance between one RTH close and the next RTH open behaves like one.
    """
    rth_all = bars[_rth_mask(bars.index)]
    if rth_all.empty:
        return []

    day = pd.Timestamp(session_day).normalize()
    days = sorted({d for d in rth_all.index.normalize().unique() if d <= day})[-(max_back + 1):]

    out: list[dict] = []
    for prev, cur in zip(days, days[1:]):
        p = rth_all[rth_all.index.normalize() == prev]
        c = rth_all[rth_all.index.normalize() == cur]
        if p.empty or c.empty:
            continue
        p_close = float(p["Close"].iloc[-1])
        c_open = float(c["Open"].iloc[0])
        size = c_open - p_close
        if abs(size) < min_handles:
            continue
        # Filled once any subsequent RTH bar trades back to the prior close.
        after = rth_all[rth_all.index >= c.index.min()]
        filled = bool(((after["Low"] <= p_close) & (after["High"] >= p_close)).any())
        if filled:
            continue
        out.append({
            "date": str(pd.Timestamp(cur).date()),
            "from": round(p_close, 2),
            "to": round(c_open, 2),
            "size": round(size, 2),
            "direction": "up" if size > 0 else "down",
            "distance": round(last - p_close, 2),
        })

    out.sort(key=lambda x: abs(x["distance"]))
    return out


# ── Cross-asset confirmation ──────────────────────────────────────

_PEERS = [
    ("NQ=F", "Nasdaq (NQ)", "Tech leadership. NQ leading ES higher is risk-on confirmation; "
                            "NQ lagging badly while ES holds is a warning on breadth."),
    ("RTY=F", "Russell (RTY)", "Small caps. Confirms or denies the breadth behind an index move."),
    ("ZN=F", "10Y note (ZN)", "Bonds bid while equities rally is a defensive tell — the two "
                              "usually disagree in risk-on."),
    ("DX-Y.NYB", "Dollar (DXY)", "A rising dollar is a headwind for equities via financial conditions."),
]


def cross_asset(session_day: pd.Timestamp | None = None) -> dict:
    """Same-day percent moves in the markets that confirm or contradict ES.

    Divergence is the signal here, not the level: ES making a new high while
    NQ and RTY refuse to follow is a far weaker move than one they confirm.
    """
    def one(spec: tuple[str, str, str]) -> dict | None:
        sym, label, why = spec
        try:
            import yfinance as yf
            # Ticker().history, never yf.download — the latter is not thread-safe
            # and this runs in a pool.
            h = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=False)
            if len(h) < 2:
                return None
            last = float(h["Close"].iloc[-1])
            prev = float(h["Close"].iloc[-2])
            if prev <= 0:
                return None
            return {
                "symbol": sym, "label": label, "why": why,
                "last": round(last, 2),
                "change_pct": round((last / prev - 1) * 100, 2),
            }
        except Exception as e:
            logger.warning(f"cross-asset {sym} failed: {e}")
            return None

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(_PEERS)) as pool:
        rows = [r for r in pool.map(one, _PEERS) if r]

    return {"available": bool(rows), "rows": rows}


# ── Assembly ──────────────────────────────────────────────────────

def es_intraday(bars: pd.DataFrame, session_day: pd.Timestamp, last: float,
                overnight: pd.DataFrame | None = None,
                prior_high: float | None = None,
                prior_low: float | None = None,
                with_cross_asset: bool = True) -> dict:
    """Everything in this module, computed off bars the caller already has."""
    if bars is None or bars.empty:
        return {"available": False, "reason": "no bars"}

    rth = _session_rth(bars, session_day)
    orng = opening_range(rth)
    ib = orng.get("ib") if orng.get("available") else None

    return {
        "available": True,
        "session_date": str(pd.Timestamp(session_day).date()),
        "opening_range": orng,
        "day_type": day_type(rth, ib),
        "relative_volume": relative_volume(bars, session_day),
        "overnight_inventory": (overnight_inventory(overnight, prior_high, prior_low, last)
                                if overnight is not None else {"available": False}),
        "naked_pocs": naked_pocs(bars, session_day, last),
        "unfilled_gaps": unfilled_gaps(bars, session_day, last),
        "cross_asset": cross_asset(session_day) if with_cross_asset else {"available": False},
    }
