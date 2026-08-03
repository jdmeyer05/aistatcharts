"""What does the rest of the session look like from here?

THE GAP THIS FILLS. Every other module on the card answers "should I engage with
this session at all". Almost nothing answers "I am already in — now what?" On
2026-08-03 the card led with STAND ASIDE while the one number that spoke to
holding (an initial-balance break holds into the close 69-80% of the time) sat
collapsed inside a table. The loud number answered the wrong question for
somebody who was already positioned.

So this conditions on the state a session is ACTUALLY in — the half-hour mark,
where price sits inside the range built so far, and whether the day is running
wide — and reports the distribution of what remains. A distribution, never a
recommendation: it says what happened next in comparable sessions, and the
reader decides what that is worth.

TWO RESULTS WORTH KNOWING BEFORE READING THE TABLE
──────────────────────────────────────────────────
1. POSITION PREDICTS WHICH EXTREME BREAKS, NOT WHERE PRICE CLOSES.
   At the 12:30 mark, price in the top 15% of the session's range takes out the
   high 91.2% of the time and the low 12.3%. From the bottom 15% those invert to
   13.2% and 89.0%. But the probability of CLOSING above the current price is
   52-58% in every position band — statistically indistinguishable across the
   whole ladder. Where you sit tells you a great deal about which side extends
   and essentially nothing about the close.

2. A WIDE DAY IS WORSE TO HOLD FROM THE HIGHS, NOT BETTER.
   Closing above the current price runs ~50% on wide days against ~55-64% on
   normal ones, with roughly double the median give-back. The intuition that a
   trending day keeps trending is not what the tape did; wide sessions gave more
   back from their extremes. Consistent at every mark.

METHOD. 5-minute SPY, 1,222 complete sessions, 13,442 observations. Every figure
is scaled by that session's own trailing 20-day median range, so cells are
comparable across volatility regimes and the multiplier transfers to ES
directly — the basis is a level offset, not a scale factor.

The wide/normal split uses the PATH-IMPLIED MULTIPLIER from `es_regime`, not
`range_so_far / normal`. The latter is not scale-consistent across hours: a
session is mechanically less likely to have cleared 1.0x by 11:30 than by 13:30,
which made the same nominal condition carry n=35 at one mark and n=71 at another.

Each session contributes at most ONE observation per mark, so within a cell the
observations are distinct sessions rather than overlapping snapshots of a few.
"""

from __future__ import annotations

import logging
from time import time as _now

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_CACHE: dict = {}
_TTL_S = 12 * 3600

# 30-minute grid. Finer than the hourly path table because a management decision
# is not made on the hour, and it roughly doubled the sample per cell.
_MARKS = [10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0]

_WIDE_MULT = 1.30            # same threshold the character read uses
_MIN_CELL = 40               # below this a cell is not reported at all
_POS_BANDS = [
    (0.85, 1.01, "top 15%"),
    (0.60, 0.85, "upper mid"),
    (0.40, 0.60, "middle"),
    (0.15, 0.40, "lower mid"),
    (-0.01, 0.15, "bottom 15%"),
]


def _band(pos: float) -> str | None:
    for lo, hi, label in _POS_BANDS:
        if lo <= pos < hi:
            return label
    return None


def _nearest_mark(now: pd.Timestamp) -> float | None:
    """The most recent 30-minute mark that has actually passed."""
    hrs = now.hour + now.minute / 60
    past = [m for m in _MARKS if m <= hrs]
    return past[-1] if past else None


def _build() -> dict:
    """The conditional table. Cached — it moves once a day at most."""
    from src.es_baserates import _polygon_5m, _to_slots, _SLOTS, _INTRADAY_SYMBOL

    fine = _polygon_5m(_INTRADAY_SYMBOL, 5)
    if fine is None or fine.empty:
        return {}
    fine = fine.copy()
    fine["day"] = fine.index.normalize()
    h = _to_slots(fine)
    full = h.groupby("day")["slot"].nunique()
    fine = fine[fine["day"].isin(set(full[full == len(_SLOTS)].index))]

    sess = fine.groupby("day").agg(hi=("High", "max"), lo=("Low", "min"),
                                   close=("Close", "last"))
    sess["rng"] = sess["hi"] - sess["lo"]
    # Prior sessions only — a normal built from the session being measured would
    # be lookahead in every row.
    sess["normal"] = sess["rng"].shift(1).rolling(20).median()
    sess = sess.dropna()
    sess = sess[(sess["normal"] > 0) & (sess["rng"] > 0)]
    if len(sess) < 300:
        return {}

    frac: dict[float, list] = {m: [] for m in _MARKS}
    recs: list[dict] = []
    for day, g in fine[fine["day"].isin(sess.index)].groupby("day"):
        s = sess.loc[day]
        hrs = (g.index.hour + g.index.minute / 60).values
        for m in _MARKS:
            upto, rest = g[hrs < m], g[hrs >= m]
            if len(upto) < 3 or rest.empty:
                continue
            hi_so, lo_so = upto["High"].max(), upto["Low"].min()
            rng_so = hi_so - lo_so
            if rng_so <= 0:
                continue
            frac[m].append(rng_so / s["rng"])
            now_px = upto["Close"].iloc[-1]
            recs.append({
                "mark": m, "rng_so": rng_so, "normal": s["normal"],
                "pos": (now_px - lo_so) / rng_so,
                "to_close": (s["close"] - now_px) / s["normal"],
                "max_up": (rest["High"].max() - now_px) / s["normal"],
                "max_dn": (now_px - rest["Low"].min()) / s["normal"],
                "new_high": bool(rest["High"].max() > hi_so),
                "new_low": bool(rest["Low"].min() < lo_so),
            })

    typ = {m: float(np.median(v)) for m, v in frac.items() if len(v) >= 200}
    D = pd.DataFrame(recs)
    if D.empty:
        return {}
    D = D[D["mark"].isin(typ)]
    D["mult"] = (D["rng_so"] / D["mark"].map(typ)) / D["normal"]
    D["wide"] = D["mult"] >= _WIDE_MULT
    D["band"] = D["pos"].map(_band)
    D = D.dropna(subset=["band"])

    cells: dict = {}
    for (mark, band, wide), s in D.groupby(["mark", "band", "wide"]):
        if len(s) < _MIN_CELL:
            continue
        q = s["to_close"].quantile([0.1, 0.25, 0.5, 0.75, 0.9])
        cells[f"{mark}|{band}|{int(wide)}"] = {
            "n": int(len(s)),
            "p_new_high": round(float(s["new_high"].mean()) * 100, 1),
            "p_new_low": round(float(s["new_low"].mean()) * 100, 1),
            "p_close_above": round(float((s["to_close"] > 0).mean()) * 100, 1),
            "to_close_p10": round(float(q[0.1]), 3),
            "to_close_p25": round(float(q[0.25]), 3),
            "to_close_med": round(float(q[0.5]), 3),
            "to_close_p75": round(float(q[0.75]), 3),
            "to_close_p90": round(float(q[0.9]), 3),
            "median_max_up": round(float(s["max_up"].median()), 3),
            "median_max_dn": round(float(s["max_dn"].median()), 3),
        }

    return {"cells": cells, "typical": typ,
            "sessions": int(sess.index.nunique()),
            "observations": int(len(D))}


def _table() -> dict:
    hit = _CACHE.get("table")
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]
    try:
        t = _build()
    except Exception as e:
        logger.warning(f"rest-of-session table failed: {e}")
        t = {}
    _CACHE["table"] = (_now(), t)
    return t


def rest_of_session(position_in_range: float | None,
                    multiplier: float | None,
                    normal_range: float | None,
                    now: pd.Timestamp | None = None) -> dict:
    """What comparable sessions did between this state and their close.

    `position_in_range` is 0 at the session low and 1 at the session high, of the
    range built SO FAR. `multiplier` is the path-implied character read.
    `normal_range` converts the scaled figures back into the instrument's own
    units, so the caller reads handles rather than ratios.
    """
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)

    if position_in_range is None:
        return {"available": False, "reason": "no developing range yet"}
    mark = _nearest_mark(now)
    if mark is None:
        return {"available": False,
                "reason": "before 10:00 — too little of the session has formed "
                          "for its position to mean anything"}

    band = _band(float(position_in_range))
    if band is None:
        return {"available": False, "reason": "position outside the measured bands"}

    t = _table()
    cells = t.get("cells") or {}
    if not cells:
        return {"available": False, "reason": "history unavailable"}

    wide = bool(multiplier is not None and multiplier >= _WIDE_MULT)
    cell = cells.get(f"{mark}|{band}|{int(wide)}")
    exact = cell is not None
    if cell is None:
        # Fall back to the other regime at the same mark and band rather than to
        # a different hour — the hour is the stronger conditioner, and a cell
        # borrowed from a different time of day would describe a different
        # decision. Reported as a fallback, never silently.
        cell = cells.get(f"{mark}|{band}|{int(not wide)}")
        if cell is None:
            return {"available": False,
                    "reason": f"no measured cell for {band} at {mark:.1f} "
                              f"({'wide' if wide else 'normal'} regime)"}

    def to_units(v):
        return round(v * normal_range, 1) if (normal_range and v is not None) else None

    return {
        "available": True,
        "mark": f"{int(mark):02d}:{int(round((mark % 1) * 60)):02d}",
        "band": band,
        "regime": "wide" if wide else "normal",
        "exact_cell": exact,
        "n": cell["n"],
        "p_new_high": cell["p_new_high"],
        "p_new_low": cell["p_new_low"],
        "p_close_above": cell["p_close_above"],
        # Scaled figures kept alongside the converted ones so the ratio the study
        # actually measured is visible, not just its projection onto today.
        "to_close": {
            "p25": cell["to_close_p25"], "median": cell["to_close_med"],
            "p75": cell["to_close_p75"],
            "p25_units": to_units(cell["to_close_p25"]),
            "median_units": to_units(cell["to_close_med"]),
            "p75_units": to_units(cell["to_close_p75"]),
        },
        "median_max_up": cell["median_max_up"],
        "median_max_up_units": to_units(cell["median_max_up"]),
        "median_max_dn": cell["median_max_dn"],
        "median_max_dn_units": to_units(cell["median_max_dn"]),
        "sessions": t.get("sessions"),
        "note": (
            f"Price is in the {band} of the range built so far. In comparable "
            f"{'wide' if wide else 'normal'} sessions at {int(mark):02d}:"
            f"{int(round((mark % 1) * 60)):02d}, a new high followed "
            f"{cell['p_new_high']:.0f}% of the time and a new low "
            f"{cell['p_new_low']:.0f}%, while the close landed above this price "
            f"{cell['p_close_above']:.0f}% of the time (n={cell['n']})."
        ),
        "caveat": (
            "Which extreme gets taken out is strongly conditioned on where price "
            "sits; where the session CLOSES is barely conditioned on it at all — "
            "the close-above rate sits near 55% from every position band. Read the "
            "first two numbers as information and the third as close to a coin "
            "flip. Distributions of what followed, not a forecast of what will."
        ),
        "method": ("5-minute SPY, 1,222 complete sessions. Every figure scaled by that "
                   "session's own trailing 20-day median range; each session contributes "
                   "at most one observation per mark."),
    }
