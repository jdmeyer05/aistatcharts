"""Candles as context: what the last bar says about TOMORROW'S RANGE.

THE REFRAME. The pattern study asked "does a hammer mean up" and answered no —
nothing in 185 pattern/size/direction cells survived correction. That was the
wrong question twice over. It threw away everything about a bar except a
folklore label, and it asked about the one thing markets are most efficient
about.

Ask instead what a candle is: six numbers, each measured against ATR — body,
upper wick, lower wick, range, where the close sat inside the range, and the
gap. The 61 TA-Lib patterns are coarse quantizations of exactly those. Measured
directly on 434,624 bars across 192 names and 2,491 sessions, using daily
cross-sectional rank IC t-tested across sessions (Fama-MacBeth, so the
cross-sectional correlation that inflated the pattern study cannot do it here):

    range -> tomorrow's RANGE     IC +0.158   t = 75.0
    volume -> tomorrow's RANGE    IC +0.148   t = 76.6
    close location -> DIRECTION   IC -0.016   t = -5.5
    range -> DIRECTION            IC +0.005   t =  1.9   (nothing)

Volatility clusters; direction does not. Candle geometry forecasts tomorrow's
RANGE with overwhelming confidence and forecasts its SIGN barely at all. Range
is also the more useful of the two, because it is what sets stop distance,
position size and what an option is worth.

TWO PIECES OF FOLKLORE COME OUT BACKWARDS, and both are monotonic across all
five buckets, which is the check that they are real:

  A strong close predicts WEAKNESS. Bars closing in the bottom fifth of their
  range are up 53.2% the next day (median +0.13%); bars closing in the top fifth
  are up 50.7% (median +0.03%). Short-term reversal, and the exact opposite of
  what "closed on the highs, momentum is with us" asserts.

  A narrow bar does NOT precede an explosion. The narrowest quintile is followed
  by a 0.77 ATR range, the widest by 0.91 ATR. Both revert toward 1 ATR, but the
  ORDERING is preserved — quiet begets quiet. The NR7-breakout story has it
  backwards.

HONEST SIZING. The direction effect is an IC of 0.016 and roughly 10bp of median
return across the whole close-location range. It is real, monotonic, and far too
small to trade on its own after costs. It belongs on a card as context that
tempers a read, never as a signal. The range forecast is the part strong enough
to act on.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

_TABLES = os.path.join(os.path.dirname(__file__), "candle_context_tables.json")


@lru_cache(maxsize=1)
def _tables() -> dict:
    try:
        with open(_TABLES, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"candle context tables unavailable: {e}")
        return {}


def _bin(value: float, edges: list[float]) -> int:
    """Fixed cut points, not quantiles — a live bar has to be placed on its own,
    without the rest of the cross-section to rank it against."""
    for i in range(len(edges) - 1):
        if edges[i] <= value < edges[i + 1]:
            return i
    return len(edges) - 2


_CLV_WORDS = ["in the bottom fifth of its range", "in the lower-middle of its range",
              "mid-range", "in the upper-middle of its range", "in the top fifth of its range"]
_RANGE_WORDS = ["very narrow", "narrow", "average", "wide", "very wide"]


def range_divergence(empirical_p50: float | None, implied_range: float | None,
                     atr: float | None = None) -> dict | None:
    """Options-implied range against what the tape has actually been doing.

    These are two estimates of the SAME quantity — tomorrow's high-low — built
    from unrelated inputs. The implied one comes from what people are paying for
    optionality; the empirical one from how bars conditioned like today's have
    actually resolved. When they disagree, that is the IV-vs-RV question asked
    one day ahead and bar-conditioned, rather than over a trailing 20-day window.

    Deliberately NOT expressed as a trade. A rich implied range is a reason to
    prefer selling premium only if the empirical estimate is the better forecast,
    and this module has no claim to that.
    """
    if not empirical_p50 or not implied_range or empirical_p50 <= 0:
        return None
    # Cast out of numpy: these arrive from pandas-backed callers and a np.float64
    # is not JSON-serialisable by the API's encoder.
    empirical_p50 = float(empirical_p50)
    implied_range = float(implied_range)
    atr = float(atr) if atr else None
    ratio = implied_range / empirical_p50
    if ratio >= 1.25:
        label, note = "implied rich", (
            "Options are pricing a wider session than bars conditioned like today's have "
            "typically delivered. Premium is expensive against recent behaviour.")
    elif ratio <= 0.8:
        label, note = "implied cheap", (
            "Options are pricing a narrower session than bars conditioned like today's have "
            "typically delivered. Premium is cheap against recent behaviour.")
    else:
        label, note = "in line", (
            "Options and recent bar behaviour agree on how much room tomorrow has.")
    return {
        "implied_range": round(implied_range, 2),
        "empirical_p50": round(empirical_p50, 2),
        "ratio": round(ratio, 2),
        "gap": round(implied_range - empirical_p50, 2),
        "gap_atr": round((implied_range - empirical_p50) / atr, 2) if atr else None,
        "label": label,
        "note": note,
        "caveat": ("Two different estimators of the same quantity, not a spread you can "
                   "trade directly. The implied figure is forward-looking and the empirical "
                   "one is conditioned on today's bar; neither is the truth."),
    }


def candle_context(symbol: str, bars=None) -> dict:
    """The last daily bar described continuously, plus tomorrow's range distribution."""
    T = _tables()
    if not T:
        return {"available": False, "reason": "context tables unavailable"}
    try:
        import numpy as np
        import talib
    except Exception as e:
        return {"available": False, "reason": f"talib unavailable: {e}"}

    df = bars
    if df is None:
        try:
            import yfinance as yf
            # Ticker().history, never yf.download — not thread-safe.
            df = yf.Ticker(symbol).history(period="6mo", interval="1d", auto_adjust=False)
            df = df.rename(columns=str.lower)
        except Exception as e:
            return {"available": False, "reason": f"history unavailable: {e}"}
    if df is None or len(df) < 40:
        return {"available": False, "reason": "not enough daily history"}

    cols = {c.lower(): c for c in df.columns}
    try:
        o, h, l, c = (df[cols[k]].values.astype(float) for k in ("open", "high", "low", "close"))
        v = df[cols["volume"]].values.astype(float) if "volume" in cols else None
    except KeyError:
        return {"available": False, "reason": "frame is missing OHLC columns"}

    atr = talib.ATR(h, l, c, 14)
    if not np.isfinite(atr[-1]) or atr[-1] <= 0:
        return {"available": False, "reason": "ATR unavailable"}

    a = float(atr[-1])
    rng = float(h[-1] - l[-1])
    body = float(c[-1] - o[-1])
    upper = float(h[-1] - max(o[-1], c[-1]))
    lower = float(min(o[-1], c[-1]) - l[-1])
    clv = float((c[-1] - l[-1]) / rng) if rng > 0 else 0.5
    vol_rel = None
    if v is not None and len(v) >= 21:
        avg = float(np.nanmean(v[-21:-1]))
        vol_rel = float(v[-1] / avg) if avg > 0 else None

    meta = T["meta"]
    rb = _bin(rng / a, meta["range_edges"])
    vb = _bin(vol_rel if vol_rel is not None else 1.0, meta["vol_edges"])
    cb = _bin(clv, meta["clv_edges"])

    rt = T["range_table"].get(f"{rb}|{vb}") or T["range_table"].get(f"{rb}|1")
    ct = T["clv_table"].get(str(cb))

    forecast = None
    if rt:
        forecast = {
            "n": rt["n"],
            "atr": round(a, 2),
            # Both units: ATR multiples are how the study measured it, price is
            # what a stop is actually placed in.
            "p25_atr": rt["p25"], "p50_atr": rt["p50"], "p75_atr": rt["p75"], "p90_atr": rt["p90"],
            "p25": round(rt["p25"] * a, 2), "p50": round(rt["p50"] * a, 2),
            "p75": round(rt["p75"] * a, 2), "p90": round(rt["p90"] * a, 2),
            "prob_exceeds_1_atr": rt["gt1atr"],
            "note": (f"Conditioned on today's range and participation, {rt['n']:,} comparable "
                     f"sessions put tomorrow's range at a median {rt['p50']:.2f} ATR "
                     f"({rt['p50']*a:.2f}), with a quarter of them beyond {rt['p75']:.2f} ATR "
                     f"({rt['p75']*a:.2f}). A stop inside {rt['p25']:.2f} ATR ({rt['p25']*a:.2f}) "
                     f"is inside the daily noise three times out of four."),
        }

    # The whole five-bucket curve, not just today's cell. The card draws the
    # strip to show the effect is MONOTONIC — that is the only reason to believe
    # a 10bp edge — and hardcoding these numbers in the frontend would let them
    # drift silently the first time the study is regenerated.
    curve = []
    for i in range(len(meta["clv_edges"]) - 1):
        row = T["clv_table"].get(str(i))
        if not row:
            continue
        curve.append({"bucket": i, "n": row["n"], "next_up_pct": row["up"],
                      "median_next_ret_pct": row["med_ret"], "is_today": i == cb})

    direction = None
    if ct:
        ics = T.get("ics", {}).get("clv->fwd_ret", {})
        direction = {
            "n": ct["n"], "next_up_pct": ct["up"], "median_next_ret_pct": ct["med_ret"],
            "t": ct["t"], "p": ct["p"],
            "ic": ics.get("ic"), "ic_t": ics.get("t"),
            "note": (f"Bars closing {_CLV_WORDS[cb]} have been up the next session "
                     f"{ct['up']:.1f}% of {ct['n']:,} times, median {ct['med_ret']:+.3f}%. "
                     f"The effect is monotonic across all five buckets — a strong close "
                     f"predicts WEAKNESS, not follow-through — but it spans only about 10bp "
                     f"of median return end to end, which is context, not a trade."),
        }

    return {
        "available": True,
        "symbol": symbol,
        "asof": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else None,
        "close": round(float(c[-1]), 2),
        "bar": {
            "range": round(rng, 2), "range_atr": round(rng / a, 2),
            "range_label": _RANGE_WORDS[min(rb, 4)],
            "body_atr": round(body / a, 2), "upper_wick_atr": round(upper / a, 2),
            "lower_wick_atr": round(lower / a, 2),
            "close_location": round(clv, 3), "close_location_label": _CLV_WORDS[cb],
            "volume_vs_20d": round(vol_rel, 2) if vol_rel is not None else None,
        },
        "tomorrow_range": forecast,
        "direction_tilt": direction,
        "close_location_curve": curve,
        "study": {
            "bars": meta.get("bars"), "sessions": meta.get("sessions"),
            "names": meta.get("names"), "from": meta.get("sample_from"),
            "to": meta.get("sample_to"), "method": meta.get("method"),
            "ics": T.get("ics"),
        },
        "disclaimer": ("Candle geometry forecasts tomorrow's RANGE strongly (rank IC 0.16, "
                       "t=75) and its DIRECTION barely (IC -0.016, t=-5.5). Size off the range; "
                       "treat the directional tilt as a tiebreaker, never as a reason to take "
                       "a trade."),
    }
