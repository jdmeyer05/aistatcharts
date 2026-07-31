"""Relative Rotation Graph for the 11 SPDR sectors against the S&P 500.

An RRG plots relative STRENGTH against relative MOMENTUM, so a sector's
position and its direction of travel are visible at once. Read as quadrants,
rotating clockwise in the healthy case:

    Improving (weak but accelerating)  →  Leading (strong and accelerating)
    Lagging   (weak and decelerating)  ←  Weakening (strong but decelerating)

The value is the trajectory, not the dot: a name deep in Lagging but curling
toward Improving is an earlier signal than one already sitting in Leading.

METHOD NOTE — the original RRG uses Julius de Kempenaer's JdK RS-Ratio and
RS-Momentum, which are proprietary and unpublished. This is the standard open
reconstruction: relative strength against the benchmark, normalised to a
rolling window and centred on 100, with momentum as the normalised rate of
change of that ratio. Quadrant assignment and rotational behaviour match; the
absolute numbers will not tie out against a licensed RRG terminal, so they are
presented as unitless and centred on 100 rather than as a quoted index.
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

# The universe ships the index itself (^GSPC) rather than SPY. Same relative
# strength read, and it avoids introducing a second source for one series.
BENCHMARK = "SPX"

# Window lengths in trading days. 63d (a quarter) for the strength baseline is
# the conventional daily-RRG setting; momentum reads a shorter 21d so it leads
# the ratio rather than echoing it.
_RS_WINDOW = 63
_MOM_WINDOW = 21
_NORM_WINDOW = 252   # normalisation baseline — one year of context


def _normalise(s: pd.Series, window: int) -> pd.Series:
    """Centre a series on 100 by its own rolling distribution.

    Centring on 100 is what makes the quadrant boundaries meaningful: 100 is
    'in line with the benchmark', so the sign of (value - 100) is the read.
    """
    mean = s.rolling(window, min_periods=window // 3).mean()
    std = s.rolling(window, min_periods=window // 3).std()
    z = (s - mean) / std.replace(0, np.nan)
    return 100 + z.fillna(0)


def _quadrant(ratio: float, mom: float) -> str:
    if ratio >= 100 and mom >= 100:
        return "leading"
    if ratio >= 100 and mom < 100:
        return "weakening"
    if ratio < 100 and mom < 100:
        return "lagging"
    return "improving"


def sector_rrg(tail_weeks: int = 8, lookback: str = "3Y") -> dict:
    """RS-Ratio / RS-Momentum for each sector, with a weekly tail.

    The tail is sampled weekly rather than daily — a daily tail on a 3-month
    baseline is mostly noise and renders as a scribble.
    """
    from src.causality import aligned_panel

    symbols = [s for s, _ in SECTORS]
    panel = aligned_panel(symbols + [BENCHMARK], lookback=lookback)
    if panel.empty or BENCHMARK not in panel.columns:
        return {"available": False, "reason": "benchmark or sector data unavailable"}

    bench = panel[BENCHMARK]
    rows: list[dict] = []
    unavailable: list[str] = []

    for sym, label in SECTORS:
        if sym not in panel.columns:
            unavailable.append(sym)
            continue
        px = panel[sym]
        pair = pd.concat([px, bench], axis=1).dropna()
        if len(pair) < _NORM_WINDOW // 2:
            unavailable.append(sym)
            continue

        rs = (pair.iloc[:, 0] / pair.iloc[:, 1]) * 100
        # Strength: where relative performance sits against its own quarter.
        rs_ratio = _normalise(rs / rs.rolling(_RS_WINDOW, min_periods=_RS_WINDOW // 3).mean() * 100,
                              _NORM_WINDOW)
        # Momentum: the rate of change OF that strength, normalised the same way,
        # so both axes are in comparable units and the quadrants are square.
        rs_mom = _normalise(rs_ratio / rs_ratio.rolling(_MOM_WINDOW, min_periods=_MOM_WINDOW // 3).mean() * 100,
                            _NORM_WINDOW)

        joined = pd.concat([rs_ratio.rename("ratio"), rs_mom.rename("mom")], axis=1).dropna()
        if joined.empty:
            unavailable.append(sym)
            continue

        # Weekly tail, oldest first, so the front-end can draw a path.
        tail_df = joined.iloc[-(tail_weeks * 5):]
        tail = [
            {"date": idx.strftime("%Y-%m-%d"), "ratio": round(float(r.ratio), 2), "mom": round(float(r.mom), 2)}
            for idx, r in tail_df.iloc[::5].iterrows()
        ]
        cur = joined.iloc[-1]
        # Ensure the live point terminates the tail even when the weekly
        # sampling stride skips it.
        if not tail or tail[-1]["date"] != joined.index[-1].strftime("%Y-%m-%d"):
            tail.append({"date": joined.index[-1].strftime("%Y-%m-%d"),
                         "ratio": round(float(cur.ratio), 2), "mom": round(float(cur.mom), 2)})

        prev = joined.iloc[-6] if len(joined) > 6 else joined.iloc[0]
        rows.append({
            "symbol": sym,
            "label": label,
            "ratio": round(float(cur.ratio), 2),
            "mom": round(float(cur.mom), 2),
            "quadrant": _quadrant(float(cur.ratio), float(cur.mom)),
            "prev_quadrant": _quadrant(float(prev.ratio), float(prev.mom)),
            # Heading in degrees (0 = due east, counter-clockwise positive) —
            # lets the UI show direction of travel without redrawing the tail.
            "heading": round(float(np.degrees(np.arctan2(cur.mom - prev.mom, cur.ratio - prev.ratio))), 1),
            "tail": tail,
        })

    if not rows:
        return {"available": False, "reason": "no sector could be computed"}

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["quadrant"]] = counts.get(r["quadrant"], 0) + 1

    return {
        "available": True,
        "benchmark": BENCHMARK,
        "asof": datetime.utcnow().isoformat() + "Z",
        "data_asof": panel.index[-1].strftime("%Y-%m-%d"),
        "tail_weeks": tail_weeks,
        "counts": counts,
        "rows": sorted(rows, key=lambda r: (-r["ratio"], -r["mom"])),
        "unavailable": unavailable,
    }
