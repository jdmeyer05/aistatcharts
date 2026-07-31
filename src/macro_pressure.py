"""Macro pressure scorecard — what the macro backdrop is doing to equities.

Answers one question per row: is this factor currently pushing equities up,
down, or neither. Every verdict is arithmetic on the series, not a judgement
call, so a reader can audit why any row says what it says:

    score = -adverse * z(change)

where `adverse` encodes the sign convention (does RISING hurt equities?) and
`z(change)` is how unusual the recent move is against that factor's own
history of moves. Positive score = equity-supportive. The composite is the
mean across factors.

Two deliberate choices:

- Scored on CHANGE, not level. A high level that has been high for a year is
  already discounted; what moves equities is the delta. Level percentile is
  reported alongside as context, not as the verdict.
- Normalised per factor. A 20bp move in HY OAS and a 20bp move in the 10Y are
  not comparable in raw units, so each factor is z-scored against its own
  distribution of changes before anything is averaged.

Series come from the shared causality universe via aligned_panel(), which
forward-fills weekly/monthly prints onto the business-day grid.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Factor:
    key: str
    label: str
    group: str          # display grouping
    kind: str           # "technical" | "fundamental"
    adverse: int        # +1 = RISING is a headwind for equities; -1 = FALLING is
    unit: str           # "pct" | "bp" | "index" | "ratio" | "usd_tn" | "thousands"
    change_mode: str    # "abs" (already in %/points) | "pct" (relative move)
    why: str            # the transmission mechanism, one line


# Ordering within a group is the order rows render.
FACTORS: list[Factor] = [
    # ── Rates & policy ────────────────────────────────────────────
    Factor("REAL10Y", "10Y real yield", "Rates & Policy", "fundamental", +1, "pct", "abs",
           "The discount rate on future earnings. Rising real yields compress multiples "
           "independently of growth."),
    Factor("SLOPE", "2s10s slope", "Rates & Policy", "technical", -1, "pct", "abs",
           "Flattening prices a slowing economy and squeezes bank net interest margins; "
           "steepening from a low base usually accompanies recovery."),
    Factor("FED_BS", "Fed balance sheet", "Rates & Policy", "fundamental", -1, "usd_tn", "pct",
           "Reserve growth is the base of system liquidity. Contraction drains the marginal "
           "buyer of risk assets."),

    # ── Credit ────────────────────────────────────────────────────
    Factor("HY_OAS", "High-yield OAS", "Credit", "technical", +1, "pct", "abs",
           "The cleanest read on risk appetite. Credit leads equities at turning points more "
           "reliably than equities lead credit."),
    Factor("IG_OAS", "Investment-grade OAS", "Credit", "technical", +1, "pct", "abs",
           "Investment-grade funding cost. Widening raises the corporate hurdle rate and "
           "chills buybacks and capex."),

    # ── Dollar & volatility ───────────────────────────────────────
    Factor("DXY", "Dollar (broad TWI)", "Dollar & Vol", "technical", +1, "index", "pct",
           "A stronger dollar cuts translated overseas earnings and tightens global funding "
           "for dollar borrowers."),
    Factor("VIX", "VIX", "Dollar & Vol", "technical", +1, "index", "abs",
           "Rising implied vol mechanically shrinks position sizes at vol-target funds, "
           "forcing supply regardless of view."),
    Factor("MOVE", "Rates vol (MOVE)", "Dollar & Vol", "technical", +1, "index", "abs",
           "Unstable rates raise the discount-rate error bar. Equity drawdowns usually start "
           "in the rates complex."),

    # ── Growth ────────────────────────────────────────────────────
    Factor("CLAIMS", "Initial jobless claims", "Growth", "fundamental", +1, "thousands", "pct",
           "The highest-frequency read on the labour market, and the first place a genuine "
           "earnings slowdown shows up."),
    Factor("CU_AU", "Copper/Gold ratio", "Growth", "technical", -1, "ratio", "pct",
           "Industrial demand against safe-haven demand — a market-priced growth signal that "
           "updates daily rather than monthly."),
    Factor("UMCSENT", "U-Mich sentiment", "Growth", "fundamental", -1, "index", "pct",
           "Consumer sentiment leads discretionary spending, which is roughly two-thirds of "
           "US GDP."),

    # ── Inflation ─────────────────────────────────────────────────
    Factor("CPI_YOY", "CPI year-over-year", "Inflation", "fundamental", +1, "pct", "abs",
           "Sets the policy reaction function. Re-accelerating inflation removes the option "
           "of cuts and caps valuations."),
]

GROUP_ORDER = ["Rates & Policy", "Credit", "Dollar & Vol", "Growth", "Inflation"]

# Panel inputs. SLOPE, CU_AU and CPI_YOY are derived, not fetched.
_PANEL_SYMBOLS = [
    "REAL10Y", "UST10Y", "UST2Y", "FED_BS",
    "HY_OAS", "IG_OAS",
    "DXY", "VIX", "MOVE",
    "CLAIMS", "COPPER", "GOLD", "UMCSENT",
    "CPI",
]

_CHANGE_WINDOW = 30      # business days — ~6 weeks, the "current pressure" horizon
_YOY_WINDOW = 252        # business days for the CPI year-over-year calculation


def _derive(panel: pd.DataFrame) -> pd.DataFrame:
    """Add the composed series the scorecard scores but the universe doesn't ship."""
    out = panel.copy()
    if "UST10Y" in out and "UST2Y" in out:
        out["SLOPE"] = out["UST10Y"] - out["UST2Y"]
    if "COPPER" in out and "GOLD" in out:
        # Scaled purely for readability; scoring is scale-invariant.
        out["CU_AU"] = out["COPPER"] / out["GOLD"] * 1000
    if "CPI" in out:
        # The universe ships the CPI index level; the tradeable signal is YoY.
        out["CPI_YOY"] = (out["CPI"] / out["CPI"].shift(_YOY_WINDOW) - 1) * 100
    return out


def _display(level: float, unit: str) -> tuple[float, str]:
    """Scale a raw series value into the unit a human reads it in.

    FRED ships the Fed balance sheet in millions and jobless claims in persons;
    printing 6738190 or 197000 raw is technically correct and useless.
    """
    if unit == "usd_tn":
        return round(level / 1_000_000, 2), "$T"
    if unit == "thousands":
        return round(level / 1_000, 0), "k"
    if unit == "pct":
        return round(level, 2), "%"
    if unit == "ratio":
        return round(level, 2), ""
    return round(level, 2), ""


def _last_print_date(s: pd.Series) -> tuple[str | None, int]:
    """When the underlying series last actually moved, and how stale that is.

    aligned_panel forward-fills, so a monthly series reads as a live value every
    business day. A factor whose last real print was two months ago contributes
    a zero change that looks like "no pressure" when it really means "no news" —
    the two deserve to be distinguished on screen.
    """
    s = s.dropna()
    if s.empty:
        return None, 0
    changed = s[s.diff().fillna(0) != 0]
    if changed.empty:
        return None, 0
    last = changed.index[-1]
    return last.strftime("%Y-%m-%d"), int((s.index[-1] - last).days)


def _series_stats(s: pd.Series, change_mode: str) -> dict | None:
    """Level, change over the window, percentile of level, and z-score of change."""
    s = s.dropna()
    if len(s) < _CHANGE_WINDOW + 20:
        return None

    level = float(s.iloc[-1])
    prior = float(s.iloc[-(_CHANGE_WINDOW + 1)])

    if change_mode == "pct":
        if prior == 0:
            return None
        change = (level / prior - 1) * 100
        change_hist = (s / s.shift(_CHANGE_WINDOW) - 1) * 100
    else:
        change = level - prior
        change_hist = s - s.shift(_CHANGE_WINDOW)

    change_hist = change_hist.dropna()
    if len(change_hist) < 30:
        return None

    sd = float(change_hist.std())
    # A flat series has no meaningful "unusual move" — score it as no pressure
    # rather than dividing by ~0 and manufacturing a huge z.
    z = float(change / sd) if sd > 1e-12 else 0.0
    z = float(np.clip(z, -4.0, 4.0))

    pctile = float((s <= level).mean())
    last_print, stale_days = _last_print_date(s)

    return {
        "level": round(level, 4),
        "change": round(change, 4),
        "change_z": round(z, 2),
        "pctile": round(pctile, 3),
        "last_print": last_print,
        "stale_days": stale_days,
    }


def _verdict(score: float) -> str:
    if score >= 0.5:
        return "supportive"
    if score <= -0.5:
        return "headwind"
    return "neutral"


def _net_label(mean_score: float) -> str:
    if mean_score >= 0.5:
        return "supportive"
    if mean_score >= 0.15:
        return "mildly supportive"
    if mean_score > -0.15:
        return "balanced"
    if mean_score > -0.5:
        return "mildly negative"
    return "negative"


def macro_pressure_board(lookback: str = "3Y") -> dict:
    """Score every factor's current pressure on equities.

    Returns rows in display order plus a composite. Factors that can't be
    computed are omitted from `rows` and named in `unavailable`, so a partial
    upstream outage degrades the board instead of failing it.
    """
    from src.causality import aligned_panel

    panel = aligned_panel(_PANEL_SYMBOLS, lookback=lookback)
    if panel.empty:
        return {"available": False, "reason": "no macro data"}

    panel = _derive(panel)

    rows: list[dict] = []
    unavailable: list[str] = []
    for f in FACTORS:
        if f.key not in panel.columns:
            unavailable.append(f.key)
            continue
        stats = _series_stats(panel[f.key], f.change_mode)
        if stats is None:
            unavailable.append(f.key)
            continue

        # The whole verdict, in one line: flip the change by the sign convention.
        score = float(np.clip(-f.adverse * stats["change_z"], -4.0, 4.0))
        disp_level, disp_unit = _display(stats["level"], f.unit)
        rows.append({
            "key": f.key,
            "label": f.label,
            "group": f.group,
            "kind": f.kind,
            "unit": f.unit,
            "change_mode": f.change_mode,
            "why": f.why,
            "score": round(score, 2),
            "verdict": _verdict(score),
            "display_level": disp_level,
            "display_unit": disp_unit,
            # A monthly print that hasn't updated inside the change window
            # contributes a structural zero, not a real "no pressure" reading.
            "stale": stats["stale_days"] > _CHANGE_WINDOW * 1.5,
            **stats,
        })

    if not rows:
        return {"available": False, "reason": "no factor could be computed"}

    mean_score = float(np.mean([r["score"] for r in rows]))
    counts = {v: sum(1 for r in rows if r["verdict"] == v)
              for v in ("supportive", "neutral", "headwind")}

    # Most-extreme factor on each side — the ones worth naming in a summary.
    ranked = sorted(rows, key=lambda r: r["score"])
    return {
        "available": True,
        "asof": datetime.utcnow().isoformat() + "Z",
        "data_asof": panel.index[-1].strftime("%Y-%m-%d"),
        "lookback": lookback,
        "change_window_days": _CHANGE_WINDOW,
        "net_score": round(mean_score, 2),
        "net_label": _net_label(mean_score),
        "counts": counts,
        "group_order": GROUP_ORDER,
        "biggest_headwind": ranked[0] if ranked[0]["score"] < 0 else None,
        "biggest_support": ranked[-1] if ranked[-1]["score"] > 0 else None,
        "rows": rows,
        "unavailable": unavailable,
    }
