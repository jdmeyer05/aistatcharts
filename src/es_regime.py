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

THREE INSTRUMENTS, DELIBERATELY UNEQUAL
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

   As a >=1.3x flag it runs 70.4% precision against a 29.3% base rate by the
   10:30 bucket, a 2.40x lift, stable across both halves.

   CORRECTED 2026-08-30. This block used to claim the static forecast lands
   within 25% on 0.0% of the widest decile — "it never gets a wide day right".
   That figure was CIRCULAR: the decile had been cut on the actual/static
   RATIO, which selects precisely the days static got most wrong. Cut the
   decile on the ACTUAL range and static scores 19.5%. Path-implied still wins,
   by less than was claimed. Two related corrections from the same re-run:
     - The +/-25% hit rate is NOT comparable across time of day. A session that
       stops expanding scores exactly 1/f - 1, which crosses 0.25 as the median
       fraction f passes 0.800 — between the 12:30 and 13:30 marks. 27.6% of
       sessions never expand after 13:30 and all flip to "hit" at once, which
       is why that column jumps 61.3% -> 84.7% while MAE moves smoothly. Read
       the MAE column, not the hit rate, when comparing marks.
     - NOTHING fitted on yesterday rescues the widest days. HAR lifts the wide
       decile only 19.5% -> 24.1% and still under-forecasts it by ~38%. Only
       the developing path repairs a wide day, which is the case for keeping
       path-implied primary.

2. HAR-RV — the pre-open prior, added 2026-08-30.
   Realised variance on its own daily/weekly/monthly averages (Corsi 2009).
   Answers the window path-implied cannot reach, every day rather than a dozen
   times a year. Measured on THIS module's own input: MAE 33.7% against the
   static 20-day median's 40.0%. See the block above `har_range_forecast`.

3. CROSS-ASSET DISPERSION — a pre-open FLAG, never a multiplier.
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
#
# ---------------------------------------------------------------------------
# HAR-RV — the pre-open forecast, and the only instrument here that speaks
# before the session has delivered anything.
# ---------------------------------------------------------------------------
#
# Path-implied cannot answer until a bucket has closed, which is why dispersion
# was bolted on to cover the first hour. But dispersion is a weak flag — roughly
# a dozen firings a year, and a lift whose size moves between sample halves. The
# hour was covered rather than answered.
#
# HAR (Corsi 2009) answers it every day: realised variance regressed on its own
# daily, weekly and monthly averages. It has been the standard volatility
# forecaster for fifteen years and this platform had never been scored against
# it — the incumbent benchmark was a static 20-day median, which is a weak
# comparator, and it was hiding real accuracy.
#
# MEASURED ON THE PRODUCTION INPUT, not on a research file. The research run
# used 6,027 sessions of 1-minute history; this module sees ~1,250 sessions of
# 5-minute Polygon SPY on a rolling five-year window, so it was re-run on that
# window before shipping. Out of sample, expanding fit, 743 sessions:
#
#       forecast              MAE      within +/-25%     QLIKE
#       static 20d median    40.0%         40.0%         0.718
#       HAR (calibrated)     33.7%         46.3%         0.461
#
# HAR wins in 6 of 6 burn-in/calibration settings tried, by 5.5-6.7pp of MAE,
# every one at |t| > 4 on the paired per-session loss differential (Newey-West,
# 9 lags). The pair below is NOT the best cell — it is the one leaving the most
# usable calibration history, and it scores second-worst of the six, so nothing
# here is tuned to the backtest.
_HAR_BURN = 250          # sessions before a fit is attempted
_HAR_MINOBS = 250        # sessions before the sigma->range constant is trusted
_HAR_CACHE: dict = {}
_HAR_TTL_S = 3600        # the panel moves once a day; an hour is generous


def _har_panel() -> pd.DataFrame:
    """One row per completed session: realised variance, and range as a
    FRACTION of price so the quantity is unit-free and transfers SPY -> ES.

    RV is built from 5-minute returns rather than squared daily returns — a
    chi-square with one degree of freedom is roughly ten times noisier, and the
    point of holding intraday bars is not to throw that away.
    """
    from src.es_baserates import _fine
    f = _fine()
    if f is None or f.empty:
        return pd.DataFrame()
    # TODAY MUST NOT BE IN HERE. `_fine` returns bars up to now, so from about
    # 15:20 a live session has enough bars to pass the completeness filter below
    # and would enter the panel as though it had closed — which both understates
    # its own RV and, worse, silently turns the output into a forecast of
    # TOMORROW while the card presents it as a prior for today. Cut on the
    # exchange-local date, never `date.today()`: this process runs on Cloud Run
    # in UTC, where after 20:00 ET "today" is already tomorrow.
    f = f[f.index < pd.Timestamp.now(tz=_TZ).normalize()]
    if f.empty:
        return pd.DataFrame()
    day = f.index.normalize()
    r = np.log(f["Close"]).groupby(day).diff()
    g = pd.DataFrame({"r": r.to_numpy(), "h": f["High"].to_numpy(),
                      "l": f["Low"].to_numpy(), "c": f["Close"].to_numpy()},
                     index=day)
    n = g.groupby(level=0).size()
    # A half-day makes RV and the range wrong in the same direction, so drop it
    # rather than patch it — the rule `_hourly` already applies to the path curve.
    g = g[g.index.isin(n[n >= 70].index)]
    if g.empty:
        return pd.DataFrame()
    grp = g.groupby(level=0)
    p = pd.DataFrame({
        "RV": grp["r"].apply(lambda s: float(np.nansum(np.asarray(s, float) ** 2))),
        "R": (grp["h"].max() - grp["l"].min()) / grp["c"].last(),
    })
    return p[(p["RV"] > 0) & (p["R"] > 0)].dropna()


def har_range_forecast(normal_range: float | None = None) -> dict:
    """Pre-open forecast of the session's range, as a multiple of a normal day.

    Unlike `path_implied_range` this needs nothing from the session it
    describes, so it is available at 09:29 — and for exactly the same reason it
    cannot see today. Once a bucket has closed the path estimate is better and
    this becomes context rather than the answer.
    """
    from time import time as _t
    # CACHE THE MODEL, NOT THE ANSWER. `implied_range` is the only field that
    # depends on the caller's `normal_range`, so caching the whole dict would
    # hand a second caller with a different instrument the first one's handles.
    hit = _HAR_CACHE.get("core")
    if hit and (_t() - hit[0]) < _HAR_TTL_S:
        return _har_dress(hit[1], normal_range)

    try:
        p = _har_panel()
    except Exception as e:                                   # pragma: no cover
        logger.warning(f"HAR panel failed: {e}")
        return {"available": False, "reason": "no intraday history"}

    need = _HAR_BURN + _HAR_MINOBS
    if len(p) < need:
        return {"available": False,
                "reason": f"needs {need} completed sessions, has {len(p)}"}

    # `shift(1)` steps to the previous ROW, and dropped half-days mean that is
    # occasionally not the previous trading day. That is tolerable HERE and was
    # fatal elsewhere, and the difference is worth stating: RV_{t-1} enters as a
    # LEVEL, so a gap only mis-weights a persistent series by one session. The
    # bug this resembles — an adjacency guard built on rows-present rather than
    # a trading calendar — was destructive because a stale prior close entered a
    # RETURN, manufacturing multi-day moves labelled as overnight gaps. Roughly
    # ten sessions in 1,250 are affected and none of them can do that here.
    rv = p["RV"]
    d = rv.shift(1)
    w = rv.rolling(5).mean().shift(1)
    m = rv.rolling(22).mean().shift(1)
    X = np.column_stack([np.ones(len(p)), np.log(d), np.log(w), np.log(m)])
    y = np.log(rv).to_numpy()
    ok = np.isfinite(X).all(1) & np.isfinite(y)
    if int(ok.sum()) < need:
        return {"available": False, "reason": "not enough finite HAR rows"}

    beta, *_ = np.linalg.lstsq(X[ok], y[ok], rcond=None)
    sigma = np.exp(0.5 * (X @ beta))              # median sigma under log-normal

    # Sigma is not a range. The Gaussian factor is sqrt(8/pi) = 1.5958, but the
    # tape is not Gaussian, so the constant is MEASURED on this window and the
    # theoretical value is carried alongside only as a check that they agree.
    # Mixing a sigma and a range without this factor has produced a real bug on
    # this platform before (a 113-handle day read as 196% of expected move).
    ratio = pd.Series(p["R"].to_numpy() / sigma, index=p.index)
    ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()
    if len(ratio) < _HAR_MINOBS:
        return {"available": False, "reason": "sigma->range constant unresolved"}
    C = float(ratio.median())

    # TODAY's forecast: yesterday's aggregates carried one step forward. The
    # panel holds only COMPLETED sessions, so this is causal by construction.
    a = rv.to_numpy()
    xn = np.array([1.0, np.log(a[-1]), np.log(a[-5:].mean()), np.log(a[-22:].mean())])
    if not np.isfinite(xn).all():
        return {"available": False, "reason": "trailing variance not finite"}
    frac = float(C * np.exp(0.5 * float(xn @ beta)))

    norm_frac = float(p["R"].tail(20).median())
    if not np.isfinite(norm_frac) or norm_frac <= 0:
        return {"available": False, "reason": "no trailing normal range"}
    mult = frac / norm_frac
    if not np.isfinite(mult) or mult <= 0:
        return {"available": False, "reason": "forecast not finite"}

    character = "wide" if mult >= 1.30 else ("compressed" if mult <= 0.75 else "normal")

    core = {
        "available": True,
        "multiplier": round(mult, 2),
        "character": character,
        "sessions": int(len(p)),
        "asof": str(p.index[-1].date()),
        "calibration": round(C, 4),
        "calibration_theory": round(float(np.sqrt(8 / np.pi)), 4),
        "persistence": round(float(beta[1:].sum()), 4),
        "oos_mae_pct": 33.7,
        "note": (f"Yesterday's volatility complex implies {mult:.2f}x a normal "
                 f"range for today, measured from realised variance rather than "
                 f"from what options cost."),
        "caveat": (
            "A pre-open prior and nothing more. It is built entirely from "
            "sessions that have already closed, so it cannot see today's "
            "catalyst — and it does NOT rescue the widest days, which stay "
            "under-forecast by every estimator fitted on yesterday. Once a "
            "bucket has closed, the path estimate is the better number."
        ),
        "method": (
            "Log realised variance regressed on its own 1-day, 5-day and 22-day "
            "averages (HAR, Corsi 2009), fitted on this window's completed "
            "sessions, then scaled to a range by the measured median ratio of "
            "range to sigma."
        ),
    }
    _HAR_CACHE["core"] = (_t(), core)
    return _har_dress(core, normal_range)


def _har_dress(core: dict, normal_range: float | None) -> dict:
    """Attach the only two fields that depend on the caller's instrument.

    Kept out of the cache so a caller asking in ES handles and one asking in SPY
    points cannot be served each other's numbers — the same one-fetch-one-answer
    rule `asset_gap` follows, applied to the half that is NOT shared.
    """
    out = dict(core)
    mult = out.get("multiplier")
    out["normal_range"] = round(float(normal_range), 2) if normal_range else None
    out["implied_range"] = (round(mult * float(normal_range), 2)
                            if (mult and normal_range) else None)
    return out


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
    """The three instruments together, with the primary one clearly primary."""
    # PRIME THE SHARED FETCH FIRST. Both the path curve and the HAR panel read
    # `_fine`, whose cache is checked-then-filled with no single-flight guard —
    # so submitting them together on a cold instance fires the ~20-page Polygon
    # pull twice in parallel. The ES brief is already the heaviest call on a
    # fresh revision and the SSR prefetch gives up at 20s, so this one line is
    # the difference between adding a second fetch and adding none.
    try:
        from src.es_baserates import _fine
        _fine()
    except Exception as e:                                   # pragma: no cover
        logger.warning(f"regime prefetch failed: {e}")

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_path = pool.submit(path_implied_range, range_so_far, normal_range, now)
        f_har = pool.submit(har_range_forecast, normal_range)
        f_disp = pool.submit(cross_asset_dispersion) if with_dispersion else None
        path = f_path.result()
        har = f_har.result()
        disp = f_disp.result() if f_disp else None

    # ORDER OF PRECEDENCE, and the reason for it.
    #
    # The path estimate wins whenever it exists: it is measured from the range
    # this session has actually delivered, and its error halves as the day runs.
    #
    # HAR is second and covers the window path cannot — from the pre-open to the
    # first bucket close. It is a real forecast every day (MAE 33.7% vs 40.0%
    # for the static median it replaces), which is a different class of object
    # from the dispersion flag.
    #
    # Dispersion is now third and no longer sets the headline number. It fires
    # roughly a dozen times a year and its lift moves between sample halves; it
    # earns its place as a note that last night was unusual, not as the estimate.
    if path.get("available"):
        headline, basis = path.get("character"), "path"
    elif har.get("available"):
        headline, basis = har.get("character"), "har"
    elif disp and disp.get("available") and disp["sum_z"] >= _DISPERSION_BANDS[1][0]:
        headline, basis = "possibly wide", "dispersion"
    else:
        headline, basis = "unknown", None

    # When both speak, say whether they agree. A pre-open prior that the session
    # has already overtaken is information — it is the shape of an unscheduled
    # catalyst, which is the gap this module was built for in the first place.
    divergence = None
    if path.get("available") and har.get("available"):
        pm, hm = path.get("multiplier"), har.get("multiplier")
        if pm and hm and hm > 0:
            ratio = pm / hm
            divergence = {
                "path_multiplier": pm,
                "har_multiplier": hm,
                "ratio": round(ratio, 2),
                "note": (
                    f"The session is running {ratio:.2f}x what last night's "
                    f"volatility implied."
                    + (" Wider than the pre-open prior expected." if ratio >= 1.25
                       else (" Narrower than the pre-open prior expected."
                             if ratio <= 0.8 else " In line with it."))
                ),
            }

    return {
        "available": bool(path.get("available") or har.get("available")
                          or (disp or {}).get("available")),
        "character": headline,
        "basis": basis,
        "path_implied": path,
        "har": har,
        "dispersion": disp,
        "divergence": divergence,
        "disclaimer": (
            "Describes how much room the session is delivering, never which way it "
            "goes. The path estimate is measured from this session's own range; the "
            "HAR prior from realised variance in sessions already closed; the "
            "dispersion flag from last night's cross-asset moves."
        ),
    }
