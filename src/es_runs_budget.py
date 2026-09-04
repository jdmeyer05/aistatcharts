"""How many tradeable legs does a day like today usually hand out — the trade
budget, printed before the open instead of remembered.

RE-BASED 2026-09-03 to the user's actual P&L threshold: theta = $1.00 SPY
= 10 ES/SPX points (was $1.50). The day-quality study (spy5m_research/
day_quality.py) showed the money frame is: pay legs of 10+ pts vs TRAPS —
5-9.9 pt swings that reverse before ever reaching 10. The trap-to-leg ratio
is the day-quality number, and the vol level is the one pre-bell lever that
moves it (2.28 traps per confirmed leg in the lowest VIX quintile vs 1.06 in
the highest), so it prints beside the budget.

The point of the budget is the CAP. The run-structure study found no serial
edge at all — a within-session permutation of each day's own bars reproduces
the run count exactly (at the $1.50 calibration: 4,652 observed vs 4,692
shuffled, p=0.27) — so once the expected count is banked, nothing measurable
says another is coming. The count is a volatility effect, and volatility is
the one thing knowable in advance.

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
(spy5m_research/calib_10pt.py, run 2026-09-03): 1,254 SPY sessions,
2021-08-16 -> 2026-08-13, 1-minute closes, theta = $1.00, traps at $0.50.
Budget tables count CONFIRMED legs only (same frame as the live counter);
a zero-confirmed day can still be a strong one-way drift whose terminal leg
never retraced 10 pts, which is why "P(zero)" is not quoted as "dead day" —
the scarcity stat shown is P(<= 1 confirmed). Continuation odds include the
terminal leg at bell value (dropping it manufactured fake money once) and the
hazard was re-verified FLAT in this frame (44-47% at every distance 10-50 pts).
Unconditional, for reference: median 6 legs/session, mean 7.81, P(<=1) 12.6%,
traps mean 10.9. NOTE: the TradingView Session_Context panel still embeds the
2026-09-02 theta=$1.50 tables (pine_calibration.py); regenerate it with
calib_10pt.py's numbers when the user wants the two surfaces re-synced.

The live inputs are measured to match the calibration's units exactly: sigma
is the std of 1-MINUTE percent changes 09:30-10:00, scaled by sqrt(390)*100
(day-scaled percent, instrument-free), and the counter runs on SPY 1-minute
closes — which is why this module fetches its own 1-minute day rather than
reusing the 5-minute frame (measured at the $1.50 study: 5-minute sampling
counts 2.91 runs/session where 1-minute counts 4.85; mixing resolutions
would misread every bucket).
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

THETA_USD = 1.00            # SPY dollars; 10 ES/SPX points at any index level
_CALIBRATED = "2026-09-03"
_N_SESSIONS = 1254

# VIX prior close -> FULL-session confirmed legs, traps, and the day-quality
# ratio (traps per confirmed leg). All from calib_10pt.py, 2026-09-03.
_VIX_CUTS = (14.9, 16.9, 19.1, 23.0)
_VIX_MED = (2, 5, 6, 7, 13)
_VIX_MEAN = (2.9, 5.5, 7.0, 8.4, 15.3)
_VIX_PZ = (17, 7, 5, 2, 0)
_VIX_PLE1 = (31, 18, 9, 6, 0)
_VIX_TRAPS = (6.6, 9.1, 10.8, 11.9, 16.3)
_VIX_RATIO = (2.28, 1.66, 1.54, 1.42, 1.06)

# first-30-min sigma (day-scaled %) -> legs AFTER 10:00 (re-anchored)
_SIG_CUTS = (0.55, 0.72, 0.92, 1.30)
_SIG_MED = (2, 3, 4, 7, 11)
_SIG_MEAN = (2.6, 3.8, 5.4, 8.1, 13.6)
_SIG_PZ = (21, 15, 4, 3, 0)
_SIG_PLE1 = (41, 27, 10, 5, 0)

_QUINTILE = ("lowest 20%", "2nd quintile", "middle", "4th quintile",
             "highest 20%")

# Leg continuation by VIX quintile (calib_10pt.py, 2026-09-03, 11,044 legs
# incl terminal): P(the leg adds >= 10 pts more | it reached run size) and
# E[additional pts]. The hazard is FLAT in distance traveled in this frame
# too (44-47% at every level 10 through 50 pts) -- legs are memoryless -- so
# no x-dependence is quoted; only the vol bucket moves the odds. Levels are
# lower than the old $1.50-frame numbers (49-57%) because a theta=10 leg dies
# on an easier retrace; the frames are not comparable side by side.
_CONT_P10 = (37, 42, 44, 43, 48)
_CONT_E = (9.9, 11.7, 12.4, 12.6, 14.0)


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
        "theta_note": "10+ ES-pt legs ($1.00 SPY)",
        "calibrated": _CALIBRATED,
        "n_sessions": _N_SESSIONS,
        # The reason the count is a CAP and not a signal, shipped with the
        # number for the same reason the chop card ships its null.
        "serial_null": ("leg arrivals carry no serial structure (permutation "
                        "null at the $1.50 calibration: 0.99x observed, "
                        "p=0.27); the count is a volatility effect, known "
                        "mostly before the open"),
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
                "p_le1_pct": _VIX_PLE1[b],
            },
            # The user-endorsed day-quality frame (2026-09-03): losses come
            # from 5-9.9 pt swings that reverse before reaching 10, so the
            # traps-per-leg ratio is quoted beside the budget. Low-vol days
            # do not lack movement; they lack movement relative to the noise
            # around it.
            "day_quality": {
                "traps_mean": _VIX_TRAPS[b],
                "trap_leg_ratio": _VIX_RATIO[b],
                "traps_note": "5-9.9 pt swings that reverse before reaching 10",
            },
        })

    # ---- live half: first-30-min sigma and the confirmed-run counter -------
    bars = _today_1m(day)
    if bars is None or bars.empty:
        return out

    closes = bars["Close"].to_numpy(float)
    count, leg_dir, leg_size = _run_legs(closes, THETA_USD)
    last_bar = bars.index[-1]
    leg = None
    if leg_dir:
        leg = {"direction": "up" if leg_dir > 0 else "down",
               "size_usd": round(leg_size, 2),
               "size_es_pts": round(leg_size * 10, 1)}
        # continuation odds only once the leg is itself run-sized -- below
        # theta it is not yet the object the survival table measured
        if vix is not None and leg_size >= THETA_USD:
            b = _bucket(vix, _VIX_CUTS)
            leg["continuation"] = {
                "p_add10_pct": _CONT_P10[b],
                "e_more_pts": _CONT_E[b],
                "note": "flat in distance traveled -- legs are memoryless",
            }
    out.update({
        "available": True,
        "runs_confirmed": count,
        "leg_in_flight": leg,
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
                    "p_le1_pct": _SIG_PLE1[sb],
                },
            })
    elif last_bar.time() < _time(10, 0):
        out["sig30_status"] = "forms at 10:00"
    else:
        out["sig30_status"] = "first 30 minutes incomplete in feed"

    return out
