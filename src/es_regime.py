"""Is this an ordinary session or an unusual one, and by how much?

Every other range estimator on this platform is NEWS-BLIND, and each is blind in
a different way. VIX1D is a price — it says what options cost, so on a day whose
catalyst landed overnight it measures anticipation that has already been
overtaken. The bar-conditioned study is pure geometry — it conditions on
yesterday's candle and cannot tell that shape on a quiet tape from the same
shape with two live catalysts running. And `consumed` divides by a number fixed
at the open and never revisited.

Observed 2026-08-03: coordinated Japan-US yen intervention and a Middle East
de-escalation drove ES ~79 handles against a VIX1D-implied 54 and a
bar-conditioned 71. Both static estimators were wrong in the same direction, and
nothing on the page could say so while the session was still running.

The gap is architectural rather than a tuning problem. Release-day multipliers
already exist (PPI 1.15x, NFP 1.10x) — but only the CALENDAR can populate them,
so an unscheduled event has no slot to occupy.

TWO INSTRUMENTS, DELIBERATELY UNEQUAL
─────────────────────────────────────
1. PATH-IMPLIED RANGE — primary, and the only one that earns a number.
   Inverts the session-path table the card already publishes: if a typical
   session has covered 68.8% of its eventual range by the end of the 10:30
   bucket, then `range_so_far / 0.688` is a forecast of the final range that
   updates every bar and is calibrated on realised paths rather than on options.
   Measured over 1,222 sessions of 5-minute SPY, out of sample (fractions fitted
   2021-2024, tested 2024-2026):

       forecast              MAE      within +/-25%
       static 20d median    39.2%         40.8%
       path-implied 09:30   30.8%         48.0%
       path-implied 10:30   25.0%         55.2%
       path-implied 11:30   20.7%         59.6%
       path-implied 12:30   16.7%         85.9%

   On the top decile of WIDE days the static forecast lands within 25% on
   0.0% of them — it never gets a wide day right, which is the whole failure.
   As a >=1.3x flag it runs 70.4% precision against a 29.3% base rate by the
   10:30 bucket, a 2.40x lift, stable across both halves.

2. CROSS-ASSET DISPERSION — a pre-open FLAG, never a multiplier.
   Path-implied says nothing until range has developed, so this fills the hour
   before it. A macro or geopolitical shock shows up first in the assets nearest
   the catalyst, not in the index. Measured on the OVERNIGHT GAP only — prior
   close to today's open — because anything using the session's own returns to
   predict the session's own range is lookahead.

   It survives, but WEAKLY: ~1.8x lift on P(wide day) at the top thresholds,
   n≈57-60 over five years (~12 firings a year), and the magnitude moves between
   sample halves even though the direction holds. A continuous score did not beat
   the simple count. So it is reported with its own base rate attached and is
   never allowed to set an expected range. The prior-session variant was tested
   and REJECTED — non-monotonic (2 outliers 44.9%, 3+ 34.7%), which is noise
   wearing a label.

WHY THE NEWS FEED DOES NOT SET THE NUMBER
─────────────────────────────────────────
The intuitive design is to hand the macro headlines to a model and let it assign
a range multiplier. There is no sample to calibrate "geopolitical de-escalation
day" on, so that multiplier would be invented — the fabricated-precision failure
the rest of this platform exists to avoid. The tape says HOW MUCH WIDER; the
headline layer says WHY. They stay in separate columns.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"

# Fitted on 2021-2024 and confirmed on 2024-2026, where they drifted 0.6-5.5pp.
# Recomputed live from the same hourly frame the path card uses; these are the
# fallback when that frame is unavailable, not the primary source.
_FALLBACK_FRACTIONS = {
    "09:30": 0.527, "10:30": 0.688, "11:30": 0.792,
    "12:30": 0.873, "13:30": 0.955, "14:30": 1.0, "15:30": 1.0,
}

# Below this the forecast is dividing a few minutes of range by ~0.53 and will
# swing wildly bar to bar. The first bucket does not close until 10:30 ET.
_MIN_SLOT = "09:30"

# The assets a macro shock reaches before the index. Deliberately NOT equity
# sectors — those co-move with the thing being predicted and leak the answer in.
_BASKET = {
    "USO": "crude", "GLD": "gold", "TLT": "long bonds", "UUP": "dollar",
    "FXY": "yen", "HYG": "credit", "SLV": "silver", "EEM": "EM equity",
}
_Z_LOOKBACK = 60
_Z_THRESHOLD = 2.0

# Measured on the overnight gap, 1,193 sessions. Base rate P(range >= 1.3x) is
# 27.4%, so these are the lifts the flag is entitled to claim and nothing more.
#
# BANDED ON SUM OF |z|, NOT ON A COUNT OF OUTLIERS. A count discards magnitude:
# on 2026-08-03 the yen gapped +6.42 sigma on the intervention and crude -2.39,
# and a count scored that identically to two ordinary 2.1-sigma moves — landing
# it in a 1.04x band on a day that delivered 1.44x. The continuous score puts the
# same session in the top 5%. Both variants backtest to a similar lift (1.76x vs
# 1.79x), so this is chosen for behaviour on the tails rather than on headline
# accuracy. Thresholds are in-sample quantiles; the top decile held out of sample
# at 40.0% train -> 43.1% test.
_DISPERSION_BANDS = [
    (12.76, "top 5%",  1.27, 48.3, 60),
    (10.21, "top 10%", 1.20, 40.8, 120),
    (7.36,  "top 25%", 1.11, 35.1, 299),
    (5.32,  "typical", 1.09, 32.2, 597),
    (0.0,   "quiet",   0.95, 24.9, 596),
]
_DISPERSION_BASE_RATE = 27.4


def _typical_fractions() -> tuple[dict, str]:
    """Median share of a session's final range in the books by each slot."""
    try:
        from src.es_baserates import _hourly, _SLOTS
        h = _hourly()
        if h.empty:
            return dict(_FALLBACK_FRACTIONS), "fallback"
        frac: dict[str, list] = {s: [] for s in _SLOTS}
        for _, g in h.groupby("day"):
            g = g.set_index("slot").reindex(_SLOTS)
            cov = (g["High"].cummax() - g["Low"].cummin()).values
            if not np.isfinite(cov[-1]) or cov[-1] <= 0:
                continue
            for i, s in enumerate(_SLOTS):
                if np.isfinite(cov[i]):
                    frac[s].append(cov[i] / cov[-1])
        out = {s: float(np.median(v)) for s, v in frac.items() if len(v) >= 200}
        if len(out) < len(_SLOTS):
            return dict(_FALLBACK_FRACTIONS), "fallback"
        return out, f"measured on {len(frac[_SLOTS[0]]):,} sessions"
    except Exception as e:
        logger.warning(f"typical fractions failed: {e}")
        return dict(_FALLBACK_FRACTIONS), "fallback"


def _slot_for(now: pd.Timestamp) -> str | None:
    """The last COMPLETED hourly bucket. A bucket still filling has covered only
    part of its range, and dividing by its full fraction reads low."""
    from src.es_baserates import _SLOTS
    mins = (now.hour * 60 + now.minute) - (9 * 60 + 30)
    if mins < 60:
        return None                      # first bucket has not closed
    idx = min(mins // 60 - 1, len(_SLOTS) - 1)
    return _SLOTS[int(idx)]


def path_implied_range(range_so_far: float | None,
                       normal_range: float | None,
                       now: pd.Timestamp | None = None) -> dict:
    """Forecast of the session's FINAL range from how much of it is already in.

    `normal_range` is the instrument's own trailing median session range, so the
    multiplier this returns is unit-free and transfers between SPY and ES
    directly — a 1.4x day is 1.4x on both. That is the same reasoning the candle
    read uses: the basis is a level offset, never a scale factor.
    """
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)
    if not range_so_far or range_so_far <= 0:
        return {"available": False, "reason": "no developing range yet"}

    slot = _slot_for(now)
    if slot is None:
        return {"available": False,
                "reason": "first hour has not closed — the estimate needs a "
                          "completed bucket to divide by"}

    fractions, source = _typical_fractions()
    f = fractions.get(slot)
    if not f or f <= 0:
        return {"available": False, "reason": "no path fraction for this slot"}

    implied = float(range_so_far) / f
    mult = (implied / normal_range) if normal_range else None

    # Confidence tracks the measured error, which halves across the session.
    # Stated as the out-of-sample figure rather than a label, so the reader can
    # weigh it rather than trust it.
    mae = {"09:30": 30.8, "10:30": 25.0, "11:30": 20.7,
           "12:30": 16.7, "13:30": 11.2}.get(slot, 11.2)

    if mult is None:
        character, note = "unknown", "No trailing range to compare against."
    elif mult >= 1.30:
        character = "wide"
        note = (f"The session is tracking {mult:.2f}x a normal day. Options and "
                f"bar-conditioned estimates are set before the open and do not "
                f"move; this one is measured from the range actually delivered.")
    elif mult <= 0.75:
        character = "compressed"
        note = f"The session is tracking {mult:.2f}x a normal day — narrower than usual."
    else:
        character = "normal"
        note = f"The session is tracking {mult:.2f}x a normal day."

    return {
        "available": True,
        "slot": slot,
        "implied_range": round(implied, 2),
        "range_so_far": round(float(range_so_far), 2),
        "normal_range": round(float(normal_range), 2) if normal_range else None,
        "multiplier": round(mult, 2) if mult is not None else None,
        "character": character,
        "note": note,
        "typical_pct_covered": round(f * 100, 1),
        "oos_mae_pct": mae,
        "source": source,
        "method": (f"Range so far divided by the {f*100:.1f}% of its final range a "
                   f"typical session has covered by the end of the {slot} bucket."),
    }


# Shared across every cross-asset consumer. `es_macro_setup` needs the same
# gaps for the same symbols, and fetching them twice cost seven redundant daily
# history calls on an already-heavy cold path — and, worse, let two blocks on
# one card quote different sigmas for the same asset if a cache refreshed
# between them. One fetch, one answer.
_GAP_CACHE: dict = {}
_GAP_TTL_S = 300


def asset_gap(symbol: str) -> dict | None:
    """Overnight gap in sigmas (prior close -> today's open, known at 09:30),
    plus the move so far today. Cached briefly and shared."""
    from time import time as _t
    hit = _GAP_CACHE.get(symbol)
    if hit and (_t() - hit[0]) < _GAP_TTL_S:
        return hit[1]
    out = None
    try:
        from src.data_engine import polygon_history
        d = polygon_history(symbol, 200)
        if d is not None and not d.empty and len(d) >= _Z_LOOKBACK + 2:
            move = d["Open"] / d["Close"].shift(1) - 1
            sd = move.shift(1).rolling(_Z_LOOKBACK).std()
            m, s = move.iloc[-1], sd.iloc[-1]
            if np.isfinite(s) and s > 0 and np.isfinite(m):
                out = {
                    "symbol": symbol,
                    "z": float(m / s),
                    "gap_pct": float(m * 100),
                    # Prior close to last, so it includes the gap. The chain
                    # check asks whether an asset moved TODAY, which is the
                    # whole day, not the session alone.
                    "day_pct": float(d["Close"].iloc[-1] / d["Close"].iloc[-2] - 1) * 100,
                }
    except Exception as e:
        logger.debug(f"gap {symbol}: {e}")
    _GAP_CACHE[symbol] = (_t(), out)
    return out


def _gap_z(symbol: str) -> tuple[float | None, float | None]:
    r = asset_gap(symbol)
    return (r["z"], r["gap_pct"]) if r else (None, None)


def cross_asset_dispersion() -> dict:
    """How unusual last night was, across the assets a shock reaches first.

    A FLAG, not a forecast. The lift is real but modest and the sample is small,
    so the measured base rate travels with the reading and no expected range is
    derived from it.
    """
    with ThreadPoolExecutor(max_workers=len(_BASKET)) as pool:
        res = dict(zip(_BASKET, pool.map(_gap_z, _BASKET)))

    rows = [{"symbol": s, "label": _BASKET[s], "z": round(z, 2), "pct": round(p, 2)}
            for s, (z, p) in res.items() if z is not None]
    if len(rows) < 4:
        return {"available": False, "reason": "too few basket assets priced"}

    outliers = sorted([r for r in rows if abs(r["z"]) >= _Z_THRESHOLD],
                      key=lambda r: -abs(r["z"]))
    n = len(outliers)
    # Scaled to the full basket when an asset failed to price, so a missing
    # symbol lowers confidence rather than silently lowering the score.
    sum_z = sum(abs(r["z"]) for r in rows) * len(_BASKET) / len(rows)
    band = next(b for b in _DISPERSION_BANDS if sum_z >= b[0])
    _, label, med_mult, p_wide, sample = band

    return {
        "available": True,
        "count": n,
        "sum_z": round(sum_z, 2),
        "assets_priced": len(rows),
        "band": label,
        "assets": sorted(rows, key=lambda r: -abs(r["z"])),
        "outliers": outliers,
        "median_multiplier": med_mult,
        "p_wide_pct": p_wide,
        "base_rate_pct": _DISPERSION_BASE_RATE,
        "lift": round(p_wide / _DISPERSION_BASE_RATE, 2),
        "sample": sample,
        "note": (
            f"Overnight cross-asset movement is in the {label} of sessions"
            + (f", led by {', '.join(o['label'] for o in outliers[:3])}." if outliers
               else " with nothing beyond 2 sigma.")
            + f" Sessions in this band ran a median {med_mult:.2f}x a normal range and "
              f"were wide {p_wide:.0f}% of the time against a {_DISPERSION_BASE_RATE:.0f}% "
              f"base rate (n={sample})."
        ),
        "caveat": (
            "A flag, not a forecast. The lift is modest and the sample is small — "
            "roughly a dozen firings a year — and the size of the effect moves "
            "between sample halves even though its direction holds. It says a "
            "session is more likely to be unusual, never how much room it has. "
            "Measured on the overnight gap only, so it carries no information "
            "from the session it describes."
        ),
        "method": ("Prior close to today's open for each asset, in standard deviations "
                   "of its own trailing 60-day gap. Equity sectors are excluded — they "
                   "co-move with the index being predicted."),
    }


def session_character(range_so_far: float | None = None,
                      normal_range: float | None = None,
                      now: pd.Timestamp | None = None,
                      with_dispersion: bool = True) -> dict:
    """The two instruments together, with the primary one clearly primary."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_path = pool.submit(path_implied_range, range_so_far, normal_range, now)
        f_disp = pool.submit(cross_asset_dispersion) if with_dispersion else None
        path = f_path.result()
        disp = f_disp.result() if f_disp else None

    # Headline prefers the measured path. Dispersion only speaks while the first
    # bucket is still open, which is the one window path-implied cannot cover.
    if path.get("available"):
        headline, basis = path.get("character"), "path"
    elif disp and disp.get("available") and disp["sum_z"] >= _DISPERSION_BANDS[1][0]:
        headline, basis = "possibly wide", "dispersion"
    else:
        headline, basis = "unknown", None

    return {
        "available": bool(path.get("available") or (disp or {}).get("available")),
        "character": headline,
        "basis": basis,
        "path_implied": path,
        "dispersion": disp,
        "disclaimer": (
            "Describes how much room the session is delivering, never which way it "
            "goes. The path estimate is measured from this session's own range; the "
            "dispersion flag is measured from last night's cross-asset moves."
        ),
    }
