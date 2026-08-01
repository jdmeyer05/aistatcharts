"""Daily candlestick patterns — what fired, and what it is actually worth.

WHAT THIS IS FOR. "Was yesterday a bullish engulfing?" is a real question with a
real answer, and TA-Lib answers it exactly. "What happens next?" is a different
question, and the honest answer — measured, not asserted — is nothing much. This
module does both, and refuses to let the first imply the second.

WHY THE EVIDENCE IS BAKED IN. The study behind `candle_evidence.json` is 729,221
path simulations against 650,339 matched baseline trades across 197 names and
twelve years. It cannot run inside a request. It is regenerated offline and the
provenance travels with it.

HOW THE STUDY WAS RUN, because the design is the entire result:

  Entry at the NEXT OPEN. You cannot trade the close you are still looking at.
  Stop at the pattern's low (both bars, since most of these are 2-bar shapes).
  Target 2R, time stop 20 sessions. When a single daily bar spans both stop and
  target, the STOP is assumed to have come first — daily bars cannot resolve the
  order, and assuming otherwise flatters every result.

  The baseline is the same mechanics run on EVERY bar, matched to each signal on
  size tranche, direction, and R/ATR decile. That last one matters more than
  anything: the stop sits at the bar's low, so a big-bodied candle mechanically
  produces a large R, which changes how often trades resolve at all. Patterns
  that merely select for wide bars inherit the market's drift without predicting
  anything — and before this control, they looked like the strongest signals in
  the study.

FOUR CORRECTIONS, EACH OF WHICH KILLED FINDINGS THAT LOOKED REAL:

  1. Comparing win rate to 50%. The S&P is up 60.7% of 5-day windows. A "56%
     bullish" pattern is BELOW the base rate, not above it.
  2. Matching on direction alone. Adding the R/ATR decile control took the
     survivors from 40 to 3.
  3. Testing win rate instead of expectancy. At a 2R target a pattern can win
     the same fraction and still make money by paying more when right — and a
     binomial test on win rate cannot see that at all.
  4. Treating same-day signals as independent. Five thousand trades firing
     across forty names on a thousand distinct sessions is a thousand
     observations, not five thousand. Clustering by date cut the t-statistics by
     roughly the square root of that ratio.

THE RESULT. Of 185 pattern x tranche x direction cells, 10 reach nominal p<0.05
against 9.2 expected by chance alone, and NONE survive Benjamini-Hochberg. The
cleanest summary is that 48.6% of cells keep the sign of their edge between the
first and second half of the sample — a coin flip. There is no daily
candlestick edge here to sell, and this module says so on every card.

That is not a reason to hide the patterns. Knowing a hammer printed is useful
context for a discretionary read. Knowing that the hammer carries no measurable
forward edge is what stops it becoming a reason to size up.
"""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)

_EVIDENCE_PATH = os.path.join(os.path.dirname(__file__), "candle_evidence.json")

# Human names for the TA-Lib CDL functions worth surfacing. TA-Lib ships 61;
# the long tail fires so rarely that a card listing them adds noise, not signal.
PATTERN_LABELS: dict[str, str] = {
    "ENGULFING": "Engulfing",
    "HARAMI": "Harami",
    "HARAMICROSS": "Harami cross",
    "HAMMER": "Hammer",
    "INVERTEDHAMMER": "Inverted hammer",
    "HANGINGMAN": "Hanging man",
    "SHOOTINGSTAR": "Shooting star",
    "DOJI": "Doji",
    "DOJISTAR": "Doji star",
    "DRAGONFLYDOJI": "Dragonfly doji",
    "GRAVESTONEDOJI": "Gravestone doji",
    "LONGLEGGEDDOJI": "Long-legged doji",
    "TAKURI": "Takuri line",
    "MORNINGSTAR": "Morning star",
    "EVENINGSTAR": "Evening star",
    "MORNINGDOJISTAR": "Morning doji star",
    "EVENINGDOJISTAR": "Evening doji star",
    "3WHITESOLDIERS": "Three white soldiers",
    "3BLACKCROWS": "Three black crows",
    "3INSIDE": "Three inside up/down",
    "3OUTSIDE": "Three outside up/down",
    "PIERCING": "Piercing line",
    "DARKCLOUDCOVER": "Dark cloud cover",
    "MARUBOZU": "Marubozu",
    "CLOSINGMARUBOZU": "Closing marubozu",
    "BELTHOLD": "Belt hold",
    "SPINNINGTOP": "Spinning top",
    "HIGHWAVE": "High wave",
    "LONGLINE": "Long line",
    "SHORTLINE": "Short line",
    "HIKKAKE": "Hikkake",
    "MATCHINGLOW": "Matching low",
    "TASUKIGAP": "Tasuki gap",
    "SEPARATINGLINES": "Separating lines",
    "RISEFALL3METHODS": "Rising/falling three methods",
    "ABANDONEDBABY": "Abandoned baby",
    "KICKING": "Kicking",
    "COUNTERATTACK": "Counterattack",
    "HOMINGPIGEON": "Homing pigeon",
    "STICKSANDWICH": "Stick sandwich",
    "THRUSTING": "Thrusting",
    "INNECK": "In-neck",
    "ONNECK": "On-neck",
}

# Patterns whose name asserts a direction the data does not support. Called out
# explicitly because the folklore is the thing a trader has to unlearn.
_FOLKLORE_NOTE = {
    "INVERTEDHAMMER": ("Named a bullish reversal. Measured as a long it is the most "
                       "consistently NEGATIVE cell in the study — every size tranche "
                       "outside mega caps, in both halves of the sample."),
    "ENGULFING": ("The most cited reversal pattern there is. Measured across 14,554 "
                  "instances it does not separate from a geometry-matched baseline."),
}


@lru_cache(maxsize=1)
def _evidence() -> dict:
    try:
        with open(_EVIDENCE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"candle evidence unavailable: {e}")
        return {"meta": {}, "cells": {}}


def evidence_meta() -> dict:
    """Provenance for the study — sample size, controls, and the headline result."""
    return dict(_evidence().get("meta") or {})


def lookup_evidence(pattern: str, tranche: str | None, direction: int) -> dict | None:
    """Measured outcome for this pattern in this size tranche, if it was tested.

    Returns None when the cell was never measured — which is a real answer, not a
    zero. Rare patterns never reached the 300-signal floor and nothing honest can
    be said about them.
    """
    cells = _evidence().get("cells") or {}
    if tranche:
        hit = cells.get(f"{pattern}|{tranche}|{direction}")
        if hit:
            return {**hit, "tranche": tranche}
    # No tranche known (an index, an ETF): fall back to the widest-sample cell so
    # the reader still gets a measured number, clearly labelled as not their
    # size cohort.
    best = None
    for key, v in cells.items():
        p, t, d = key.split("|")
        if p == pattern and int(d) == direction and (best is None or v["n"] > best[1]["n"]):
            best = (t, v)
    return {**best[1], "tranche": best[0], "tranche_is_proxy": True} if best else None


def _verdict(ev: dict | None) -> dict:
    """What the measurement licenses you to say. Deliberately conservative."""
    if not ev:
        return {"label": "unmeasured",
                "note": "Too few instances in the study to say anything. Absence of a "
                        "number here is not a weak signal — it is no signal."}
    q = ev.get("q")
    edge = ev.get("edge_r", 0.0)
    stable = (ev.get("h1_r", 0) * ev.get("h2_r", 0)) > 0
    if q is not None and q < 0.05 and stable:
        return {"label": "edge survives", "note": "Survives multiple-testing correction and "
                                                  "keeps its sign out of sample."}
    if q is not None and q < 0.05:
        return {"label": "unstable", "note": "Significant overall but flips sign between the "
                                             "two halves of the sample — treat as noise."}
    direction_word = "better" if edge > 0 else "worse"
    return {
        "label": "no measurable edge",
        "note": (f"Ran {edge:+.3f}R {direction_word} than a matched baseline over {ev['n']:,} "
                 f"instances, which does not survive correction across the {evidence_meta().get('cells', 0)} "
                 f"cells tested (q={q:.2f}). Treat as descriptive, not predictive."),
    }


def detect(symbol: str, tranche: str | None = None, sessions: int = 3,
           bars=None) -> dict:
    """Which daily candlestick patterns printed on the most recent sessions.

    `bars` accepts a ready OHLC frame (columns open/high/low/close, ascending);
    otherwise daily history is fetched. `tranche` selects the size cohort the
    evidence is quoted from.
    """
    try:
        import numpy as np
        import talib
    except Exception as e:
        return {"available": False, "reason": f"talib unavailable: {e}"}

    df = bars
    if df is None:
        try:
            import yfinance as yf
            # Ticker().history, never yf.download — the latter is not thread-safe
            # and this is called from a pooled request handler.
            df = yf.Ticker(symbol).history(period="6mo", interval="1d", auto_adjust=False)
            df = df.rename(columns=str.lower)
        except Exception as e:
            return {"available": False, "reason": f"history unavailable: {e}"}
    if df is None or len(df) < 60:
        return {"available": False, "reason": "not enough daily history"}

    cols = {c.lower(): c for c in df.columns}
    try:
        o, h, l, c = (df[cols[k]].values.astype(float) for k in ("open", "high", "low", "close"))
    except KeyError:
        return {"available": False, "reason": "frame is missing OHLC columns"}

    out = []
    for key, label in PATTERN_LABELS.items():
        fn = getattr(talib, f"CDL{key}", None)
        if fn is None:
            continue
        try:
            sig = fn(o, h, l, c)
        except Exception:
            continue
        for i in range(max(0, len(sig) - sessions), len(sig)):
            if not sig[i]:
                continue
            direction = 1 if sig[i] > 0 else -1
            ev = lookup_evidence(key, tranche, direction)
            out.append({
                "key": key,
                "label": label,
                "date": str(df.index[i].date()) if hasattr(df.index[i], "date") else str(df.index[i]),
                "sessions_ago": int(len(sig) - 1 - i),
                "direction": "bullish" if direction > 0 else "bearish",
                "open": round(float(o[i]), 2), "high": round(float(h[i]), 2),
                "low": round(float(l[i]), 2), "close": round(float(c[i]), 2),
                "body_pct": round(float((c[i] - o[i]) / o[i] * 100), 2) if o[i] else None,
                "evidence": ev,
                "verdict": _verdict(ev),
                "folklore": _FOLKLORE_NOTE.get(key),
            })

    out.sort(key=lambda r: (r["sessions_ago"], r["label"]))
    meta = evidence_meta()
    return {
        "available": True,
        "symbol": symbol,
        "tranche": tranche,
        "asof": str(df.index[-1].date()) if hasattr(df.index[-1], "date") else None,
        "last_close": round(float(c[-1]), 2),
        "sessions_scanned": sessions,
        "patterns": out,
        "study": meta,
        "headline": (
            f"Across {meta.get('cells', 0)} pattern/size/direction cells measured on "
            f"{meta.get('signals', 0):,} simulated trades, {meta.get('nominal_p05', 0)} reach "
            f"nominal significance against {meta.get('expected_by_chance', 0)} expected by chance, "
            f"and none survive multiple-testing correction. "
            f"{meta.get('sign_stable_pct', 0)}% of cells keep the sign of their edge between the "
            f"two halves of the sample — a coin flip."
        ) if meta else None,
        "disclaimer": ("Patterns are reported because what printed is a fact worth knowing. The "
                       "measured evidence beside each one is what stops it becoming a reason to "
                       "size up."),
    }
