"""Relative Rotation Graph for the 11 SPDR sectors against the S&P 500.

An RRG plots relative STRENGTH against relative MOMENTUM, so a sector's
position and its direction of travel are visible at once. Read as quadrants,
rotating clockwise in the healthy case:

    Improving (weak but accelerating)  →  Leading (strong and accelerating)
    Lagging   (weak and decelerating)  ←  Weakening (strong but decelerating)

WHAT THIS CARD IS FOR — measured 2026-08-02, and it is narrower than it looks.

This is a REGIME descriptor, not a session signal. Tested directly: RRG state on
day T against the next session's direction, range and trend-efficiency, over
1,829 day-pairs. Direction is null at every quintile (54.5% baseline, no bucket
outside noise). Trend-efficiency is null (t = +0.07). Next-day range showed a
nominal t = +3.94 that survived vol controls but FAILED a split-half — first
half t = -0.84 with the opposite sign — so it is regime-specific, not a
relationship. Defensive tilt against next-day range died once the COVID and 2022
windows were removed (t = 2.55 -> 1.37). Nothing here forecasts the session, and
StockCharts says the same of the licensed original: "Relative Rotation Graphs
are not a trading system, and there are no predefined trading rules or signals."

What it DOES do is separate environments contemporaneously, which is the regime
question. Weekly, since 2018, by dominant quadrant: realised vol runs 12.8 when
Improving leads and 19.3 when Weakening leads, with SPX-vs-50dma falling
monotonically +3.1% -> +0.2% across the same ordering. Those are co-occurrences,
not forecasts, and part of the effect is definitional — defensive leadership IS
what a falling market looks like.

Average pairwise sector CORRELATION is carried here as a measure in its own
right, not as something the RRG proxies. Measured: rrg dispersion against
cross-sectional return dispersion is only +0.32, and against 20d realised vol
+0.20. Dispersion on an RRG is a quarterly RELATIVE-STRENGTH spread; correlation
is daily co-movement, and the two can point opposite ways — on 2026-07-31
dispersion sat in its bottom tercile while correlation was at 0.13, a multi-year
low. Correlation is the number an index trader can least infer from price, so it
is measured directly rather than inferred from the rotation picture.

WEEKLY, NOT DAILY. Measured on the same history: a sector holds a quadrant for a
median 2 days daily versus 2 weeks weekly, and 1.99 of 11 sectors change quadrant
per day against 0.90/day equivalent weekly — 2-5x more persistent in calendar
terms. A daily board relabels faster than the environment it claims to describe.

METHOD NOTE — the original RRG uses Julius de Kempenaer's JdK RS-Ratio and
RS-Momentum, which are proprietary and unpublished; both StockCharts and
relativerotationgraphs.com decline to publish the formula. This is the standard
open reconstruction: relative strength against the benchmark, normalised to a
rolling window and centred on 100. Quadrant assignment and rotational behaviour
match; the absolute numbers will not tie out against a licensed RRG terminal.
"""

from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SECTORS: list[tuple[str, str]] = [
    ("XLK", "Technology"),
    ("XLF", "Financials"),
    ("XLE", "Energy"),
    ("XLV", "Health Care"),
    ("XLI", "Industrials"),
    ("XLU", "Utilities"),
    ("XLP", "Staples"),
    ("XLY", "Discretionary"),
    ("XLC", "Communications"),
    ("XLB", "Materials"),
    ("XLRE", "Real Estate"),
]

# Defensive vs cyclical split for the leadership tilt. Conventional GICS
# grouping — utilities, staples, health care and real estate are the low-beta
# side. Real estate sits with the defensives on rate-sensitivity grounds; it is
# the one debatable member and is called out here so the choice is visible.
DEFENSIVE = {"XLU", "XLP", "XLV", "XLRE"}

# The universe ships the index itself (^GSPC) rather than SPY. Same relative
# strength read, and it avoids introducing a second source for one series.
BENCHMARK = "SPX"

# Windows in WEEKS. These are the calendar equivalents of the 63/21/252 trading
# days the daily build used, so the switch changed the sampling frequency and
# nothing else about the horizon being measured.
_RS_WINDOW = 13
_MOM_WINDOW = 4
_NORM_WINDOW = 52

# Canonical RRG scaling. The community reconstruction of JdK is
# `100 + 10 * z`; this module previously used `100 + z`, which compressed the
# whole board into roughly 98.7-102.3. That is not cosmetic: with unit variance
# the quadrant boundaries sit inside the noise, and a sector reading 100.01 on
# momentum was labelled a full quadrant away from one reading 99.99.
_SCALE = 10.0

# A z-score needs most of its window to mean anything. The old code accepted a
# third (84 of 252) and presented the result identically to a full one.
_MIN_FRAC = 0.75


def _min_periods(window: int) -> int:
    return max(2, int(window * _MIN_FRAC))


def _zscore(s: pd.Series, window: int) -> pd.Series:
    """Rolling z-score. NaN where it cannot be computed — never a placeholder.

    The previous implementation ended with `.fillna(0)`, which returns exactly
    100 after centring. A sector with a degenerate or too-short series therefore
    landed precisely on the quadrant origin, and `_quadrant(100, 100)` returns
    "leading" — the strongest label on the board was the fallback for "no data".
    Verified against the shipped code before this change: a zero-variance series
    and a 10-point series both produced 100.0.
    """
    mp = _min_periods(window)
    mean = s.rolling(window, min_periods=mp).mean()
    std = s.rolling(window, min_periods=mp).std()
    return (s - mean) / std.replace(0, np.nan)


def _normalise(s: pd.Series, window: int) -> pd.Series:
    """Centre a series on 100 by its own rolling distribution.

    Centring on 100 is what makes the quadrant boundaries meaningful: 100 is
    'in line with the benchmark', so the sign of (value - 100) is the read.
    """
    return 100 + _SCALE * _zscore(s, window)


def _quadrant(ratio: float, mom: float) -> str:
    if ratio >= 100 and mom >= 100:
        return "leading"
    if ratio >= 100 and mom < 100:
        return "weakening"
    if ratio < 100 and mom < 100:
        return "lagging"
    return "improving"


def _to_weekly(panel: pd.DataFrame) -> pd.DataFrame:
    """Friday closes. The final row may be a partial week, which is intended —
    it is the latest observation of a price ratio, not a weekly aggregate, so it
    is valid mid-week. `week_complete` in the payload says which it is."""
    return panel.resample("W-FRI").last().dropna(how="all")


def _pctile(hist: pd.Series, value: float) -> float | None:
    """Where `value` sits within its own history, 0-100."""
    h = hist.dropna()
    if len(h) < 20:
        return None
    return round(float((h < value).mean() * 100), 1)


def _pct_rank(hist: pd.Series) -> pd.Series:
    """`_pctile` applied to every observation, by exactly the same definition.

    Both must agree or the card contradicts itself: the headline band comes from
    _pctile on the latest value, while the accompanying context averages the
    history sharing that band. Deriving the two from different rules — a
    fraction-below here and a `quantile(1/3)` cut there — lets a value on the
    boundary be labelled one band and described by another.

    `method="min"` is what makes them identical: for a value with k observations
    strictly below it the rank is k+1, so (rank-1)/n == (h < value).mean().
    """
    n = len(hist)
    if n == 0:
        return pd.Series(dtype=float)
    # Rounded to match _pctile exactly. Without this a true percentile of 66.66
    # rounds to 66.7 there and clears the 200/3 band cut, while the unrounded
    # value here stays below it — the same observation in two bands.
    return hist.rank(method="min").sub(1).div(n).mul(100).round(1)


def _band(pct: float | None, low_label: str, mid_label: str, high_label: str) -> str | None:
    """Tercile band by percentile. Thresholds are on the DISTRIBUTION, not on
    the raw value — the scale of both measures depends on how dispersed the
    sectors happen to be, so a fixed cut would drift with the regime."""
    if pct is None:
        return None
    if pct >= 200 / 3:
        return high_label
    if pct <= 100 / 3:
        return low_label
    return mid_label


def _environment(daily: pd.DataFrame, weekly_index: pd.DatetimeIndex) -> pd.DataFrame:
    """Contemporaneous description of the environment at each weekly date.

    Deliberately NOT forward-looking: every column is computed from data up to
    and including that date. These are the co-occurring conditions that give a
    regime label its content, and they are measured rather than asserted.
    """
    syms = [s for s, _ in SECTORS if s in daily.columns]

    # Strip forward-filled rows before any statistic is taken.
    #
    # aligned_panel reindexes onto a CALENDAR business-day grid and ffills, so
    # every market holiday becomes a row where all 12 series are unchanged —
    # 51 of 1305 rows over 5Y. Those synthetic zero returns depress realised vol
    # (about one per 20-day window) and drag correlation. Measured effect on
    # correlation is small (0.134 vs 0.138) but it is free to remove, and a
    # genuine day on which every sector AND the index close exactly unchanged
    # does not occur.
    moved = daily.diff().abs().sum(axis=1) > 0
    moved.iloc[0] = True
    daily = daily[moved]

    bench = daily[BENCHMARK]
    ret = bench.pct_change()
    rv = ret.rolling(20).std() * np.sqrt(252) * 100
    trend = (bench / bench.rolling(50).mean() - 1) * 100
    sect_ret = daily[syms].pct_change()

    rows = []
    for ts in weekly_index:
        window = sect_ret.loc[:ts].tail(60)
        corr = np.nan
        if len(window) >= 40 and window.shape[1] >= 3:
            c = window.corr().to_numpy()
            iu = np.triu_indices_from(c, 1)
            vals = c[iu]
            vals = vals[np.isfinite(vals)]
            if vals.size:
                corr = float(vals.mean())
        rv_at = rv.loc[:ts]
        tr_at = trend.loc[:ts]
        rows.append({
            "date": ts,
            "realized_vol": float(rv_at.iloc[-1]) if len(rv_at) and np.isfinite(rv_at.iloc[-1]) else np.nan,
            "avg_sector_corr": corr,
            "trend_vs_50dma": float(tr_at.iloc[-1]) if len(tr_at) and np.isfinite(tr_at.iloc[-1]) else np.nan,
        })
    return pd.DataFrame(rows).set_index("date")


def _conditional(env: pd.DataFrame, series: pd.Series, current_band: str | None,
                 low_label: str, mid_label: str, high_label: str) -> dict | None:
    """Average environment over the history that shared the current band.

    This is the sentence the card is built on, so it is computed from the live
    history every time rather than hardcoded from a one-off study.
    """
    if current_band is None or series.dropna().empty:
        return None
    s = series.dropna()
    bands = _pct_rank(s).map(lambda p: _band(p, low_label, mid_label, high_label))
    match = bands[bands == current_band].index.intersection(env.index)
    sub = env.loc[match].dropna(how="all")
    if len(sub) < 20:
        return None
    out = {"n": int(len(sub))}
    for col in ("realized_vol", "avg_sector_corr", "trend_vs_50dma"):
        v = sub[col].mean()
        out[col] = round(float(v), 2) if np.isfinite(v) else None
    return out


def sector_rrg(tail_weeks: int = 8, lookback: str = "5Y") -> dict:
    """Weekly RS-Ratio / RS-Momentum for each sector, with a trailing path.

    Each tail point is one week. 5Y of daily bars gives ~260 weekly rows, of
    which ~175 survive the 52-week normalisation — enough history for the
    percentile context on the regime measures to mean something.

    `lookback` stays at 5Y because asking for more returns nothing more:
    measured 2026-08-02, a 10Y request widened the raw index to 2609 rows but
    the all-11-sectors-present intersection began on the same date either way
    (2021-08-02), because the daily source is hard-capped at 5 calendar years
    and truncates silently. 10Y costs the extra fetch and buys no history.
    """
    from src.causality import aligned_panel

    symbols = [s for s, _ in SECTORS]
    daily = aligned_panel(symbols + [BENCHMARK], lookback=lookback)
    if daily.empty or BENCHMARK not in daily.columns:
        return {"available": False, "reason": "benchmark or sector data unavailable"}
    if not isinstance(daily.index, pd.DatetimeIndex):
        daily.index = pd.to_datetime(daily.index)
    daily = daily.sort_index()

    weekly = _to_weekly(daily)
    if len(weekly) < _NORM_WINDOW:
        return {"available": False,
                "reason": f"only {len(weekly)} weeks of history, need {_NORM_WINDOW}"}

    bench = weekly[BENCHMARK]
    rows: list[dict] = []
    unavailable: list[str] = []
    ratio_hist: dict[str, pd.Series] = {}
    mom_hist: dict[str, pd.Series] = {}

    for sym, label in SECTORS:
        if sym not in weekly.columns:
            unavailable.append(sym)
            continue
        pair = pd.concat([weekly[sym], bench], axis=1).dropna()
        if len(pair) < _NORM_WINDOW:
            unavailable.append(sym)
            continue

        rs = pair.iloc[:, 0] / pair.iloc[:, 1]
        # Strength: relative performance against its own quarter.
        strength = rs / rs.rolling(_RS_WINDOW, min_periods=_min_periods(_RS_WINDOW)).mean()
        rs_ratio = _normalise(strength, _NORM_WINDOW)
        # Momentum: the rate of change OF that strength. Taken as a DIFFERENCE
        # from its own recent mean rather than a ratio — the old code divided
        # rs_ratio by its rolling mean, which only stayed stable because the
        # series was pinned near 100. Under any other centring that denominator
        # can approach zero. A difference has no such failure mode and is the
        # same quantity once it is z-scored.
        rs_mom = _normalise(
            rs_ratio - rs_ratio.rolling(_MOM_WINDOW, min_periods=_min_periods(_MOM_WINDOW)).mean(),
            _NORM_WINDOW)

        joined = pd.concat([rs_ratio.rename("ratio"), rs_mom.rename("mom")], axis=1).dropna()
        if joined.empty:
            unavailable.append(sym)
            continue

        ratio_hist[sym], mom_hist[sym] = joined["ratio"], joined["mom"]
        cur = joined.iloc[-1]
        # Each point is a week, so the tail needs no striding — that existed
        # only to thin daily samples down to something readable as a curve.
        tail = [
            {"date": idx.strftime("%Y-%m-%d"),
             "ratio": round(float(r.ratio), 2), "mom": round(float(r.mom), 2)}
            for idx, r in joined.iloc[-max(2, tail_weeks):].iterrows()
        ]
        prev = joined.iloc[-2] if len(joined) > 1 else cur
        # Heading in degrees (0 = due east, counter-clockwise positive) — lets
        # the UI show direction of travel without redrawing the tail. None when
        # the dot has not meaningfully moved: arctan2(0, 0) is 0.0, which would
        # report "due east" for a sector that went nowhere. The cut is 1% of a
        # standard deviation on this scale, which is no direction at all.
        dx, dy = float(cur.ratio - prev.ratio), float(cur.mom - prev.mom)
        heading = (round(float(np.degrees(np.arctan2(dy, dx))), 1)
                   if np.hypot(dx, dy) > 0.01 * _SCALE else None)
        rows.append({
            "symbol": sym,
            "label": label,
            "ratio": round(float(cur.ratio), 2),
            "mom": round(float(cur.mom), 2),
            "quadrant": _quadrant(float(cur.ratio), float(cur.mom)),
            "prev_quadrant": _quadrant(float(prev.ratio), float(prev.mom)),
            "heading": heading,
            "tail": tail,
        })

    if not rows:
        return {"available": False, "reason": "no sector could be computed"}

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["quadrant"]] = counts.get(r["quadrant"], 0) + 1

    R = pd.DataFrame(ratio_hist).dropna()
    M = pd.DataFrame(mom_hist).dropna()
    shared = R.index.intersection(M.index)
    R, M = R.loc[shared], M.loc[shared]

    # ── The two regime measures ──────────────────────────────────────────────
    # Both are CONTINUOUS. The old headline was "which quadrant holds the most
    # dots", which changed 29% of days and had a median spell of one period,
    # because a hard cut applied to values hugging 100 relabels on noise.
    defensive = [s for s in R.columns if s in DEFENSIVE]
    cyclical = [s for s in R.columns if s not in DEFENSIVE]
    tilt_hist = (R[defensive].mean(axis=1) - R[cyclical].mean(axis=1)
                 if defensive and cyclical else pd.Series(dtype=float))
    disp_hist = np.sqrt((R - 100) ** 2 + (M - 100) ** 2).mean(axis=1)

    env = _environment(daily, shared)

    regime: dict = {}
    if not tilt_hist.empty:
        val = float(tilt_hist.iloc[-1])
        pct = _pctile(tilt_hist, val)
        band = _band(pct, "cyclical led", "balanced", "defensive led")
        regime["tilt"] = {
            "value": round(val, 2), "pctile": pct, "band": band,
            "n_history": int(len(tilt_hist)),
            "context": _conditional(env, tilt_hist, band,
                                    "cyclical led", "balanced", "defensive led"),
        }
    val = float(disp_hist.iloc[-1])
    pct = _pctile(disp_hist, val)
    band = _band(pct, "tight", "normal", "wide")
    regime["dispersion"] = {
        "value": round(val, 2), "pctile": pct, "band": band,
        "n_history": int(len(disp_hist)),
        "context": _conditional(env, disp_hist, band, "tight", "normal", "wide"),
    }

    # Correlation is a first-class measure, not a by-product of the rotation
    # picture — see the module docstring for why the RRG does not proxy it.
    corr_hist = env["avg_sector_corr"].dropna() if not env.empty else pd.Series(dtype=float)
    if not corr_hist.empty:
        val = float(corr_hist.iloc[-1])
        pct = _pctile(corr_hist, val)
        band = _band(pct, "dispersed", "normal", "correlated")
        regime["correlation"] = {
            "value": round(val, 3), "pctile": pct, "band": band,
            "n_history": int(len(corr_hist)),
            "context": _conditional(env, corr_hist, band,
                                    "dispersed", "normal", "correlated"),
        }

    if not env.empty:
        last = env.iloc[-1]
        regime["current"] = {
            k: (round(float(last[k]), 2) if np.isfinite(last[k]) else None)
            for k in ("realized_vol", "avg_sector_corr", "trend_vs_50dma")
        }

    last_daily = daily.index[-1]
    return {
        "available": True,
        "benchmark": BENCHMARK,
        "frequency": "weekly",
        "asof": datetime.utcnow().isoformat() + "Z",
        "data_asof": last_daily.strftime("%Y-%m-%d"),
        "week_ending": shared[-1].strftime("%Y-%m-%d"),
        # False means the newest point is a partial week — valid, since a price
        # ratio is observable any day, but it will move until Friday's close.
        "week_complete": bool(last_daily.weekday() >= 4),
        "windows": {"rs_weeks": _RS_WINDOW, "mom_weeks": _MOM_WINDOW,
                    "norm_weeks": _NORM_WINDOW, "scale": _SCALE},
        "tail_weeks": tail_weeks,
        "counts": counts,
        "regime": regime,
        "rows": sorted(rows, key=lambda r: (-r["ratio"], -r["mom"])),
        "unavailable": unavailable,
    }
