"""Measured base rates for the claims the briefing makes.

The rest of this cockpit describes the session. This module is the only part
that says how often a thing has actually happened — gap fills, prior-range
breaks, how wide a day usually gets, and how much bigger a CPI or payrolls
session runs than a normal one.

WHY THIS EXISTS. "Outside value tends to trend", "gaps fill", "the overnight
high gets tested" — all standard, all repeated everywhere, and none of it
carries a number. A base rate turns a slogan into a probability you can size
against, and it occasionally shows the slogan is wrong.

WHY THE CASH INDEX AND NOT ES. Gap statistics need the 09:30 open against the
prior 16:00 close. `ES=F` daily bars open at the 18:00 GLOBEX open — which is
minutes after the prior close, so the "gap" it implies is near zero and the
whole study would be meaningless. Verified on the same date: cash opened at
7462.13 where the ES daily bar opened 7479.50. So everything here is measured
on ^GSPC, where Open and Close ARE the cash session, then expressed as
percentages and converted to ES handles at the current price. ES tracks SPX
closely enough intraday that a same-day percentage carries across; the absolute
levels do not, and nothing here quotes one.

WHAT THESE ARE NOT. Unconditional frequencies over a fixed window. They are not
a forecast, they take no account of the regime you are in, and a 70% base rate
still loses three times in ten. They are a prior to update, not a signal.

FOMC IS ABSENT ON PURPOSE. Release dates come from FRED, which does not carry
FOMC meetings, and the hardcoded calendar only holds forward-looking dates —
so there is no honest history to measure. Better a gap than an invented one.
"""

from __future__ import annotations

import logging
from datetime import date as _date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_INDEX = "^GSPC"
_DEFAULT_YEARS = 10
_CACHE: dict = {}
_TTL_S = 12 * 3600

# Gap buckets in percent of the prior close. Small gaps behave differently from
# large ones, and lumping them together hides exactly the effect being measured.
_GAP_BUCKETS = [
    (0.0, 0.15, "flat (<0.15%)"),
    (0.15, 0.35, "small (0.15-0.35%)"),
    (0.35, 0.75, "moderate (0.35-0.75%)"),
    (0.75, 99.0, "large (>0.75%)"),
]

# FRED release ids with full history. FOMC deliberately excluded — see above.
_EVENT_RELEASES = [
    (10, "CPI"),
    (50, "Nonfarm payrolls"),
    (54, "PCE"),
    (46, "PPI"),
    (9, "Retail sales"),
]


def _daily(years: int) -> pd.DataFrame:
    """Cash-index daily bars. Cached — this moves once a day at most."""
    from time import time as _now
    hit = _CACHE.get(("daily", years))
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]
    try:
        import yfinance as yf
        h = yf.Ticker(_INDEX).history(period=f"{years + 1}y", interval="1d", auto_adjust=False)
        if h.empty:
            return pd.DataFrame()
        h = h[["Open", "High", "Low", "Close"]].dropna()
        h["prev_close"] = h["Close"].shift(1)
        h["prev_high"] = h["High"].shift(1)
        h["prev_low"] = h["Low"].shift(1)
        h = h.dropna()
        h["gap_pct"] = (h["Open"] - h["prev_close"]) / h["prev_close"] * 100
        h["range_pct"] = (h["High"] - h["Low"]) / h["prev_close"] * 100
        h["body_pct"] = (h["Close"] - h["Open"]) / h["prev_close"] * 100
        _CACHE[("daily", years)] = (_now(), h)
        return h
    except Exception as e:
        logger.warning(f"base-rate history failed: {e}")
        return pd.DataFrame()


def _bucket(gap_pct: float) -> str | None:
    a = abs(gap_pct)
    for lo, hi, label in _GAP_BUCKETS:
        if lo <= a < hi:
            return label
    return None


def gap_base_rates(h: pd.DataFrame, gap_pct: float | None = None) -> dict:
    """How often a gap gets filled, by gap size and direction.

    "Filled" means price traded back to the prior close at some point in the
    SAME cash session — the definition a day trader cares about, not an
    eventual fill weeks later.
    """
    if h.empty:
        return {"available": False}

    up = h["gap_pct"] > 0
    # A gap up is filled if the session's low reaches back to the prior close.
    filled = np.where(up, h["Low"] <= h["prev_close"], h["High"] >= h["prev_close"])
    h = h.assign(filled=filled, direction=np.where(up, "up", "down"))

    rows = []
    for lo, hi, label in _GAP_BUCKETS:
        sel = h[(h["gap_pct"].abs() >= lo) & (h["gap_pct"].abs() < hi)]
        if len(sel) < 20:
            continue
        rows.append({
            "bucket": label,
            "n": int(len(sel)),
            "fill_rate": round(float(sel["filled"].mean() * 100), 1),
            "up_fill_rate": round(float(sel[sel["direction"] == "up"]["filled"].mean() * 100), 1)
            if (sel["direction"] == "up").any() else None,
            "down_fill_rate": round(float(sel[sel["direction"] == "down"]["filled"].mean() * 100), 1)
            if (sel["direction"] == "down").any() else None,
            "close_above_open_rate": round(float((sel["body_pct"] > 0).mean() * 100), 1),
        })

    today = None
    if gap_pct is not None:
        b = _bucket(gap_pct)
        match = next((r for r in rows if r["bucket"] == b), None)
        if match:
            direction = "up" if gap_pct > 0 else "down"
            rate = match["up_fill_rate"] if direction == "up" else match["down_fill_rate"]
            today = {
                "gap_pct": round(float(gap_pct), 3),
                "direction": direction,
                "bucket": b,
                "fill_rate": rate if rate is not None else match["fill_rate"],
                "n": match["n"],
                "note": (f"{'An' if direction == 'up' else 'A'} {direction} gap this size has "
                         f"traded back to the prior close in the same session "
                         f"{rate if rate is not None else match['fill_rate']:.0f}% of "
                         f"{match['n']} occurrences."),
            }

    return {"available": bool(rows), "buckets": rows, "today": today}


def range_base_rates(h: pd.DataFrame, last: float | None = None) -> dict:
    """How wide a session usually gets, and how often it breaks the prior range."""
    if h.empty:
        return {"available": False}

    pcts = h["range_pct"].dropna()
    if len(pcts) < 50:
        return {"available": False}

    def q(p: float) -> float:
        return float(np.percentile(pcts, p))

    took_high = float((h["High"] > h["prev_high"]).mean() * 100)
    took_low = float((h["Low"] < h["prev_low"]).mean() * 100)
    took_both = float(((h["High"] > h["prev_high"]) & (h["Low"] < h["prev_low"])).mean() * 100)
    took_neither = float(((h["High"] <= h["prev_high"]) & (h["Low"] >= h["prev_low"])).mean() * 100)

    # A trend day: most of the range is directional body rather than rotation.
    trend = float((h["body_pct"].abs() / h["range_pct"].replace(0, np.nan) >= 0.75).mean() * 100)

    def handles(pct: float) -> float | None:
        return round(pct / 100 * last, 1) if last else None

    return {
        "available": True,
        "n": int(len(h)),
        "median_range_pct": round(q(50), 3),
        "median_range_handles": handles(q(50)),
        "p25_handles": handles(q(25)),
        "p75_handles": handles(q(75)),
        "p90_handles": handles(q(90)),
        "took_prior_high_pct": round(took_high, 1),
        "took_prior_low_pct": round(took_low, 1),
        "took_both_pct": round(took_both, 1),
        "took_neither_pct": round(took_neither, 1),
        "trend_day_pct": round(trend, 1),
    }


def _release_dates(release_id: int, start: str, end: str) -> list[str]:
    try:
        import requests
        from src.api_keys import get_secret
        key = get_secret("FRED_API_KEY")
        if not key:
            return []
        r = requests.get(
            "https://api.stlouisfed.org/fred/release/dates",
            params={"api_key": key, "file_type": "json", "release_id": release_id,
                    "realtime_start": start, "realtime_end": end,
                    "include_release_dates_with_no_data": "true",
                    "sort_order": "asc", "limit": 1000},
            timeout=20,
        )
        r.raise_for_status()
        return [d["date"] for d in r.json().get("release_dates", [])]
    except Exception as e:
        logger.warning(f"release dates {release_id} failed: {e}")
        return []


def event_base_rates(h: pd.DataFrame, years: int) -> dict:
    """How much wider a session runs on each major release, vs a normal day.

    The ratio is what matters for sizing: if CPI days run 1.6x a normal range,
    a stop placed for an ordinary session is roughly a third too tight.
    """
    if h.empty:
        return {"available": False}

    from time import time as _now
    hit = _CACHE.get(("events", years))
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]

    end = _date.today().isoformat()
    start = str((pd.Timestamp(_date.today()) - pd.Timedelta(days=365 * years)).date())

    baseline_range = float(h["range_pct"].median())
    baseline_body = float(h["body_pct"].abs().median())
    idx_dates = {d.date().isoformat() for d in h.index}

    rows = []
    for rid, name in _EVENT_RELEASES:
        dates = [d for d in _release_dates(rid, start, end) if d in idx_dates]
        if len(dates) < 12:
            continue
        sel = h[[d.date().isoformat() in set(dates) for d in h.index]]
        if sel.empty:
            continue
        med_range = float(sel["range_pct"].median())
        rows.append({
            "name": name,
            "n": int(len(sel)),
            "median_range_pct": round(med_range, 3),
            "range_vs_normal": round(med_range / baseline_range, 2) if baseline_range else None,
            "median_abs_move_pct": round(float(sel["body_pct"].abs().median()), 3),
            "move_vs_normal": round(float(sel["body_pct"].abs().median()) / baseline_body, 2)
            if baseline_body else None,
            "up_close_rate": round(float((sel["body_pct"] > 0).mean() * 100), 1),
        })

    rows.sort(key=lambda r: -(r["range_vs_normal"] or 0))
    out = {
        "available": bool(rows),
        "baseline_range_pct": round(baseline_range, 3),
        "events": rows,
        "note": ("FOMC is not included — release dates come from FRED, which does not carry "
                 "Fed meetings, and the local calendar only holds forward-looking dates."),
    }
    _CACHE[("events", years)] = (_now(), out)
    return out


def base_rates(last: float | None = None, gap_pct: float | None = None,
               years: int = _DEFAULT_YEARS) -> dict:
    """All measured base rates, optionally conditioned on today's gap."""
    h = _daily(years)
    if h.empty:
        return {"available": False, "reason": "no index history"}

    cutoff = pd.Timestamp.now(tz=h.index.tz) - pd.Timedelta(days=365 * years)
    h = h[h.index >= cutoff]
    if len(h) < 200:
        return {"available": False, "reason": "not enough history"}

    return {
        "available": True,
        "source": f"{_INDEX} cash session",
        "window_years": years,
        "sessions": int(len(h)),
        "from": str(h.index.min().date()),
        "to": str(h.index.max().date()),
        "gaps": gap_base_rates(h, gap_pct),
        "range": range_base_rates(h, last),
        "events": event_base_rates(h, years),
    }
