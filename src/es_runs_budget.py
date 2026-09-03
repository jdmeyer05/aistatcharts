"""How many tradeable runs does a day like today usually hand out — the trade
budget, printed before the open instead of remembered.

Answers one planning question: "if I engage today, how many legs of $1.50+
(SPY scale, ~15 ES handles) should I expect, and what are the odds of none?"
The point is the CAP. The run-structure study found no serial edge at all —
a within-session permutation of each day's own bars reproduces the run count
exactly (4,652 observed vs 4,692 shuffled, p=0.27) — so once the expected
count is banked, nothing measurable says another is coming. The count is a
volatility effect, and volatility is the one thing knowable in advance.

Two readings, in order of when they become known:
  pre-open   VIX prior close -> full-session run expectation (the decision
             point: the dead-day model scored AUC 0.811 pre-open vs 0.814 at
             10:00 — waiting for the tape buys nothing)
  at 10:00   first-30-min sigma -> runs over the REST of the day (the best
             single live predictor, rho +0.65; re-anchored at 10:00 so the
             predictor's window is not inside the target)

Plus a live counter of runs already confirmed today, from the same causal
ZigZag the study used: a leg is confirmed only after price retraces theta
from its extreme, which is the first moment anyone could know.

CALIBRATION — every constant below was computed, not assigned
(spy5m_research/pine_calibration.py, run 2026-09-02): 1,254 SPY sessions,
2021-08-16 -> 2026-08-13, 1-minute closes, theta = $1.50. The same table is
embedded in the user's TradingView indicator (Session_Context.pine), so the
two surfaces cannot disagree. Regenerate both from the script if recalibrated.
Unconditional, for reference: median 2 runs/session, mean 3.71 (dragged by a
violent minority), P(zero) 23%.

The live inputs are measured to match the calibration's units exactly: sigma
is the std of 1-MINUTE percent changes 09:30-10:00, scaled by sqrt(390)*100
(day-scaled percent, instrument-free), and the counter runs on SPY 1-minute
closes — which is why this module fetches its own 1-minute day rather than
reusing the 5-minute frame (5-minute sampling counts 2.91 runs/session where
1-minute counts 4.85; mixing resolutions would misread every bucket).
"""
from __future__ import annotations

import logging
from datetime import time as _time
from time import time as _now

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_CACHE: dict = {}
_TODAY_TTL_S = 60
_VIX_TTL_S = 12 * 3600

THETA_USD = 1.50            # SPY dollars; ~15 ES handles at current ratios
_CALIBRATED = "2026-09-02"
_N_SESSIONS = 1254

# VIX prior close -> FULL-session runs
_VIX_CUTS = (14.9, 16.9, 19.1, 23.0)
_VIX_MED = (0, 2, 2, 3, 6)
_VIX_MEAN = (1.0, 2.4, 3.2, 4.1, 7.9)
_VIX_PZ = (52, 27, 17, 16, 2)

# first-30-min sigma (day-scaled %) -> runs AFTER 10:00 (re-anchored)
_SIG_CUTS = (0.55, 0.72, 0.92, 1.30)
_SIG_MED = (0, 1, 2, 3, 5)
_SIG_MEAN = (1.0, 1.6, 2.2, 3.9, 7.0)
_SIG_PZ = (56, 42, 27, 12, 4)

_QUINTILE = ("lowest 20%", "2nd quintile", "middle", "4th quintile",
             "highest 20%")


def _bucket(v: float, cuts: tuple) -> int:
    i = 0
    for c in cuts:
        if v > c:
            i += 1
    return i


def _prior_vix(session_day: pd.Timestamp) -> float | None:
    """^VIX close of the last session strictly before `session_day`.

    Cached for 12h keyed by the session day — a prior close cannot change
    intraday, and yfinance is the flakiest fetch on this card.
    """
    key = ("vix", session_day.date())
    hit = _CACHE.get(key)
    if hit and (_now() - hit[0]) < _VIX_TTL_S:
        return hit[1]
    try:
        import yfinance as yf
        h = yf.Ticker("^VIX").history(period="10d", interval="1d",
                                      auto_adjust=False)
        if h is None or h.empty:
            return None
        h.index = h.index.tz_localize(None).normalize()
        prior = h.loc[h.index < pd.Timestamp(session_day.date()), "Close"]
        if prior.empty:
            return None
        v = float(prior.iloc[-1])
        if not np.isfinite(v) or v <= 0:
            return None
        _CACHE[key] = (_now(), v)
        return v
    except Exception as e:
        logger.warning(f"runs_budget: VIX fetch failed: {e}")
        return None


def _today_1m(day: pd.Timestamp) -> pd.DataFrame | None:
    """Today's SPY 1-minute RTH bars, 60-second TTL.

    Its own fetch for the same reason `es_chop._today_bars` has one — the
    shared frames cache history for hours — but at 1-minute because the
    calibration is 1-minute (see module docstring). One un-paged request.
    """
    hit = _CACHE.get("today1m")
    if hit and hit[1] == day.date() and (_now() - hit[0]) < _TODAY_TTL_S:
        return hit[2]
    try:
        from src.api_keys import get_secret
        import requests
        key = get_secret("MASSIVE_API_KEY")
        if not key:
            return None
        iso = day.date().isoformat()
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/minute/{iso}/{iso}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000,
                    "apiKey": key},
            timeout=20)
        if r.status_code != 200:
            return None
        res = r.json().get("results") or []
        if not res:
            return None
        b = pd.DataFrame(res)
        b.index = pd.to_datetime(b["t"], unit="ms", utc=True).dt.tz_convert(_TZ)
        b = b.rename(columns={"c": "Close"})[["Close"]].sort_index()
        b = b[~b.index.duplicated(keep="first")].dropna()
        b = b[[_time(9, 30) <= t.time() < _time(16, 0) for t in b.index]]
        if b.empty:
            return None
        _CACHE["today1m"] = (_now(), day.date(), b)
        return b
    except Exception as e:
        logger.warning(f"runs_budget: today 1m fetch failed: {e}")
        return None


def _run_legs(p: np.ndarray, theta: float) -> tuple[int, int, float]:
    """Confirmed-leg count + the leg in flight, causal ZigZag.

    Port of `run_legs` from the study (spy5m_research/runs_expectation.py),
    returning (confirmed count, direction of leg in flight, its size so far).
    Direction: +1 up, -1 down, 0 none confirmed yet.
    """
    n = len(p)
    if n < 2:
        return 0, 0, 0.0
    ap = p[0]
    hi = lo = p[0]
    d = 0
    count = 0
    for i in range(1, n):
        x = p[i]
        if x > hi:
            hi = x
        if x < lo:
            lo = x
        if d == 0:
            if hi - ap >= theta and hi - x >= theta:
                count += 1
                ap, d, lo = hi, -1, x
            elif ap - lo >= theta and x - lo >= theta:
                count += 1
                ap, d, hi = lo, 1, x
        elif d == 1:
            if hi - x >= theta:
                count += 1
                ap, d, lo = hi, -1, x
        else:
            if x - lo >= theta:
                count += 1
                ap, d, hi = lo, 1, x
    leg = hi - ap if d == 1 else (ap - lo if d == -1 else 0.0)
    return count, d, float(leg)


def runs_budget(now: pd.Timestamp | None = None,
                session_day: pd.Timestamp | None = None) -> dict:
    """The budget block. Meaningful pre-open — that is the point of it."""
    clock = now if now is not None else pd.Timestamp.now(tz=_TZ)
    day = session_day if session_day is not None else clock.normalize()

    out: dict = {
        "available": False,
        "theta_usd": THETA_USD,
        "theta_note": "$1.50 on SPY, about 15 ES handles",
        "calibrated": _CALIBRATED,
        "n_sessions": _N_SESSIONS,
        # The reason the count is a CAP and not a signal, shipped with the
        # number for the same reason the chop card ships its null.
        "serial_null": ("run arrivals carry no serial structure "
                        "(permutation null 0.99x observed, p=0.27); the count "
                        "is a volatility effect, known mostly before the open"),
    }

    vix = _prior_vix(day)
    if vix is not None:
        b = _bucket(vix, _VIX_CUTS)
        out.update({
            "available": True,
            "vix_prior_close": round(vix, 2),
            "vix_bucket": b + 1,
            "vix_bucket_label": _QUINTILE[b],
            "pre_open": {
                "median_runs": _VIX_MED[b],
                "mean_runs": _VIX_MEAN[b],
                "p_zero_pct": _VIX_PZ[b],
            },
        })

    # ---- live half: first-30-min sigma and the confirmed-run counter -------
    bars = _today_1m(day)
    if bars is None or bars.empty:
        return out

    closes = bars["Close"].to_numpy(float)
    count, leg_dir, leg_size = _run_legs(closes, THETA_USD)
    last_bar = bars.index[-1]
    out.update({
        "available": True,
        "runs_confirmed": count,
        "leg_in_flight": {"direction": ("up" if leg_dir > 0 else
                                        "down" if leg_dir < 0 else "none"),
                          "size_usd": round(leg_size, 2)} if leg_dir else None,
        "bars_through": last_bar.strftime("%H:%M"),
    })

    # sigma needs the full first half hour to have ARRIVED, not just the clock
    # to have passed 10:00 — the feed runs ~20 min behind the wall clock and a
    # sigma from 22 of 30 minutes is a different (smaller) statistic.
    first30 = bars[bars.index.time < _time(10, 0)]
    have_full_window = (last_bar.time() >= _time(10, 0)
                        and len(first30) >= 25)
    if have_full_window:
        c = first30["Close"].to_numpy(float)
        d = np.diff(c) / c[:-1]
        if len(d) >= 24 and np.all(np.isfinite(d)):
            sig30 = float(np.std(d) * np.sqrt(390) * 100)
            sb = _bucket(sig30, _SIG_CUTS)
            out.update({
                "sig30_pct": round(sig30, 2),
                "sig30_bucket": sb + 1,
                "sig30_bucket_label": _QUINTILE[sb],
                "after_1000": {
                    "median_runs": _SIG_MED[sb],
                    "mean_runs": _SIG_MEAN[sb],
                    "p_zero_pct": _SIG_PZ[sb],
                },
            })
    elif last_bar.time() < _time(10, 0):
        out["sig30_status"] = "forms at 10:00"
    else:
        out["sig30_status"] = "first 30 minutes incomplete in feed"

    return out
