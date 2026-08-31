"""Is this session choppy or trending — the axis the rest of the card cannot see.

WHY THIS EXISTS. Every range estimator on this page answers HOW BIG: the path-
implied multiplier, HAR, the expected move, the candle study. None of them
answers HOW STRAIGHT. Those are close to independent axes — measured at
corr(range, efficiency) = +0.37 on this very sample — so a session can be wide
and rotational or narrow and one-way, and the card had no word for the
difference. A reader watching price cover ground without going anywhere was
getting a page full of numbers that all agreed it was a normal day.

THE MEASURE. Kaufman efficiency ratio on 5-minute closes:

    ER = |net move| / sum(|bar-to-bar move|)

0 is pure chop (all travel, no progress); 1 is a straight line. It is unit-free,
so it carries from SPY to ES unchanged.

WHY A READING IS NEVER QUOTED BARE. ER falls mechanically with the number of
bars — a random walk gives roughly 1/sqrt(n) — so 0.09 at 11:30 and 0.09 at
15:00 are entirely different statements. Every reading here is converted to a
percentile against the historical distribution AT THE SAME MARK, and nothing in
the payload is comparable across clock times except those percentiles.

WHAT IT DOES NOT DO — THE POINT WORTH READING TWICE. This says what the session
HAS BEEN. It does not forecast the rest of it. Measured on the disjoint
remainder (no shared bars, so no mechanical overlap), the correlation between
efficiency so far and efficiency to come runs between -0.07 and +0.04 at all
eleven marks, signs flipping between the two halves of the sample — a flat null,
reproducing an earlier null found on 30-second SPX bars over a different window.
So the module ships that number rather than hiding it: a choppy morning is not
evidence of a choppy afternoon, and the card says so in the same breath it says
the morning was choppy.

CONFIDENCE IS MEASURED, NOT WORDED. "Likely" and "confident" are set by the
historical frequency with which a reading in today's percentile band, at today's
mark, belonged to a session that FINISHED in that class — computed from the
sample on every call, never a constant typed into this file. That frequency
grows through the session as the elapsed part comes to dominate the whole, and
it grows with how far the reading sits from the middle.

The two sides are NOT symmetric and the thresholds do not pretend otherwise. A
session that has trended hard by midday has banked a net move that is difficult
to undo, so the trendy side resolves early — top decile at 11:30 finished trendy
70% of the time against a 33% base. A quiet morning can still break out, so the
choppy side resolves late — bottom decile at 11:30 finished choppy only 47% of
the time, reaching the same confidence only after about 13:30. "Confident
choppy" is therefore a label the early session simply cannot earn, and that is
the data verdict rather than a design choice.
"""

from __future__ import annotations

import logging
from datetime import time as _time
from time import time as _now_s

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_CACHE: dict = {}
_TTL_S = 6 * 3600

# 30-minute marks. 09:30-10:00 is deliberately absent: seven bars is too few for
# an efficiency ratio to carry information, and the 10:00 row measured the
# weakest separation of any mark tested.
_MARKS = ("10:00", "10:30", "11:00", "11:30", "12:00", "12:30",
          "13:00", "13:30", "14:00", "14:30", "15:00")

_FULL_BARS = 78          # 09:30-16:00 inclusive on a 5-minute grid
_MIN_BARS = _FULL_BARS - 2
_MIN_CELL = 40           # below this a band is widened rather than quoted
_TODAY_TTL_S = 60        # today's bars are the live half; history is not

# Percentile band edges. Fine in the tails, where the reading actually separates,
# and one wide band through the middle, where it does not.
_EDGES = (0.0, 0.10, 0.20, 1 / 3, 2 / 3, 0.80, 0.90, 1.0)

# A label is only offered when the measured hit rate clears these. Both sides use
# the same numbers — the asymmetry lives in the data, not in the thresholds.
_CONFIDENT = 0.65
_LIKELY = 0.45


def _today_bars(day: pd.Timestamp) -> pd.DataFrame | None:
    """Today's 5-minute bars, fetched fresh on a 60-second TTL.

    WHY THIS EXISTS RATHER THAN SLICING THE SHARED FRAME. `es_baserates._fine()`
    caches five years of bars for TWELVE HOURS, which is right for the thing it
    was built for — history does not change during a session. But this module
    reads the running session out of that same frame, and a container that first
    fetched at 09:45 would then serve a frame ending at 09:45 until the evening:
    the mark would sit at 09:30 all afternoon while the clock advanced past it,
    and the card would report a stale reading as a current one. The history half
    still comes from the 12-hour cache, which is free and correct; only the live
    half is refetched, and it is a single un-paged request for one day.

    Returns None on any failure, which the caller treats as "fall back to the
    shared frame" rather than as an empty session — those are different states.
    """
    from time import time as _t
    hit = _CACHE.get("today")
    if hit and hit[1] == day and (_t() - hit[0]) < _TODAY_TTL_S:
        return hit[2]
    try:
        from src.api_keys import get_secret
        import requests
        key = get_secret("MASSIVE_API_KEY")
        if not key:
            return None
        iso = day.date().isoformat()
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/SPY/range/5/minute/{iso}/{iso}",
            params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": key},
            timeout=20)
        if r.status_code != 200:
            return None
        res = r.json().get("results") or []
        if not res:
            return None
        b = pd.DataFrame(res)
        b.index = pd.to_datetime(b["t"], unit="ms", utc=True).dt.tz_convert(_TZ)
        b = b.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"})
        b = b[["Open", "High", "Low", "Close"]].sort_index()
        b = b[~b.index.duplicated(keep="first")].dropna()
        # RTH only, matching the frame this is standing in for. A pre-market bar
        # would shift every index into the session by one and silently move the
        # mark the reading is attributed to.
        b = b[[_time(9, 30) <= t.time() < _time(16, 0) for t in b.index]]
        if b.empty:
            return None
        _CACHE["today"] = (_t(), day, b)
        return b
    except Exception as e:
        logger.warning(f"session_chop: today fetch failed: {e}")
        return None


def _er(closes: np.ndarray) -> float:
    """Efficiency ratio over a run of closes. NaN below three points."""
    if closes is None or len(closes) < 3:
        return float("nan")
    travel = float(np.abs(np.diff(closes)).sum())
    if not np.isfinite(travel) or travel <= 0:
        return float("nan")
    return float(abs(closes[-1] - closes[0]) / travel)


def _panel(fine: pd.DataFrame) -> pd.DataFrame:
    """One row per COMPLETE historical session: efficiency at each mark, and final.

    Short sessions are dropped rather than padded. A half day has a different bar
    count at every mark, which is precisely the constant the per-mark
    distributions assume — including them would score a 1pm close against a
    population that traded until four.
    """
    rows = []
    day = fine.index.normalize()
    for d, g in fine.groupby(day):
        if len(g) < _MIN_BARS:
            continue
        c = g["Close"].to_numpy(dtype=float)
        t = g.index.strftime("%H:%M").to_numpy()
        rec = {"day": d, "final": _er(c)}
        for m in _MARKS:
            idx = np.where(t == m)[0]
            if not len(idx):
                rec[m] = np.nan
                rec["rest_" + m] = np.nan
                continue
            i = int(idx[0])
            rec[m] = _er(c[: i + 1])
            rec["rest_" + m] = _er(c[i:])       # disjoint from the above
        rows.append(rec)
    return pd.DataFrame(rows).set_index("day").sort_index() if rows else pd.DataFrame()


def _classes(panel: pd.DataFrame) -> tuple[float, float]:
    """Tercile cuts on FINAL efficiency — what "this was a choppy day" means."""
    q = panel["final"].dropna().quantile([1 / 3, 2 / 3])
    return float(q.iloc[0]), float(q.iloc[1])


def _band(value: float, edges: np.ndarray) -> tuple[float, float, int]:
    """Locate a reading in the fitted percentile bands. Returns (lo, hi, index)."""
    for i in range(len(edges) - 1):
        last = i == len(edges) - 2
        if edges[i] <= value < edges[i + 1] or (last and value >= edges[i]):
            return float(_EDGES[i]), float(_EDGES[i + 1]), i
    return float(_EDGES[0]), float(_EDGES[1]), 0


def session_chop(fine: pd.DataFrame | None = None,
                 now: pd.Timestamp | None = None) -> dict | None:
    """Today character, its measured confidence, and the forward null.

    `fine` is the shared 5-minute SPY frame the base-rate study already fetches;
    passing it in keeps this module free of its own network call.
    """
    try:
        if fine is None:
            from src.es_baserates import _fine
            fine = _fine()
        if fine is None or fine.empty:
            return {"available": False, "reason": "no intraday bars"}

        clock = now if now is not None else pd.Timestamp.now(tz=_TZ)
        clock = clock.tz_localize(_TZ) if clock.tzinfo is None else clock.tz_convert(_TZ)

        today = clock.normalize()
        # The live half is refetched; the shared frame stands in only if that
        # fails, and it is then explicitly stale rather than silently so.
        sess = _today_bars(today)
        stale = False
        if sess is None:
            sess = fine[fine.index.normalize() == today]
            stale = True
        if sess is None or sess.empty:
            return {"available": False, "reason": "no bars for this session yet"}

        # The latest mark that has fully elapsed AND has a bar. Using the wall
        # clock alone would claim a 15:00 reading on a half day that closed at
        # one, so the bar has to exist, not merely the minute.
        stamps = set(sess.index.strftime("%H:%M"))
        elapsed = [m for m in _MARKS
                   if m in stamps and clock.time() >= _time(*map(int, m.split(":")))]
        if not elapsed:
            return {"available": False,
                    "reason": "before 10:00 — too few bars for an efficiency read"}
        mark = elapsed[-1]

        key = ("panel", len(fine), str(fine.index[-1]))
        hit = _CACHE.get(key)
        if hit and (_now_s() - hit[0]) < _TTL_S:
            panel = hit[1]
        else:
            panel = _panel(fine[fine.index.normalize() != today])
            if not panel.empty:
                _CACHE[key] = (_now_s(), panel)
        if panel.empty or len(panel) < 200:
            return {"available": False, "reason": "not enough history to calibrate"}

        col = panel[[mark, "final"]].dropna()
        if len(col) < 200:
            return {"available": False, "reason": "not enough history at " + mark}

        t = sess.index.strftime("%H:%M").to_numpy()
        i = int(np.where(t == mark)[0][0])
        cur = _er(sess["Close"].to_numpy(dtype=float)[: i + 1])
        # NaN never fails a comparison, so it is tested for explicitly rather
        # than left to fall through a bounds check that would silently pass it.
        if not np.isfinite(cur):
            return {"available": False, "reason": "flat tape — no travel to divide by"}

        hist = col[mark].to_numpy(dtype=float)
        pctile = float((hist < cur).mean() * 100)

        lo_f, hi_f = _classes(panel)
        fin = col["final"].to_numpy(dtype=float)
        edges = np.quantile(hist, _EDGES)
        b_lo, b_hi, bi = _band(cur, edges)

        def _rates(mask: np.ndarray) -> tuple[float, float, int]:
            n = int(mask.sum())
            if n == 0:
                return float("nan"), float("nan"), 0
            f = fin[mask]
            return float((f < lo_f).mean()), float((f >= hi_f).mean()), n

        top = bi >= len(edges) - 2
        in_band = (hist >= edges[bi]) & (
            np.ones_like(hist, dtype=bool) if top else (hist < edges[bi + 1]))
        p_chop, p_trend, n_band = _rates(in_band)
        widened = False
        if n_band < _MIN_CELL:
            # A cell too thin to quote is widened to its side tercile rather than
            # reported at whatever precision the handful of sessions allows.
            widened = True
            side_mask = (hist < np.quantile(hist, 1 / 3)) if cur < float(np.median(hist)) \
                else (hist >= np.quantile(hist, 2 / 3))
            p_chop, p_trend, n_band = _rates(side_mask)

        # The label: whichever side the evidence favours, at the strength the
        # measured frequency supports and no higher.
        p_best, side = (p_trend, "trendy") if p_trend >= p_chop else (p_chop, "choppy")
        if not np.isfinite(p_best) or p_best < _LIKELY:
            label, conf = "mixed", "none"
        elif p_best >= _CONFIDENT:
            label, conf = "confident " + side, "confident"
        else:
            label, conf = "likely " + side, "likely"

        # The forward number, measured on the disjoint remainder.
        fwd = None
        rest_col = panel[[mark, "rest_" + mark]].dropna()
        if len(rest_col) >= 200:
            r_hist = rest_col[mark].to_numpy(dtype=float)
            r_rest = rest_col["rest_" + mark].to_numpy(dtype=float)
            r_cut = float(np.quantile(r_rest, 1 / 3))
            base = float((r_rest < r_cut).mean())
            r_edges = np.quantile(r_hist, _EDGES)
            r_top = bi >= len(r_edges) - 2
            m2 = (r_hist >= r_edges[bi]) & (
                np.ones_like(r_hist, dtype=bool) if r_top else (r_hist < r_edges[bi + 1]))
            if int(m2.sum()) >= _MIN_CELL and base > 0:
                cond = float((r_rest[m2] < r_cut).mean())
                r = float(np.corrcoef(r_hist, r_rest)[0, 1])
                fwd = {
                    "p_rest_choppy_pct": round(cond * 100, 1),
                    "base_pct": round(base * 100, 1),
                    "lift": round(cond / base, 2),
                    "corr": round(r, 3),
                    "n": int(m2.sum()),
                    "verdict": "null",
                    "note": ("Efficiency so far does not forecast efficiency to come. "
                             "Across the full sample the two are correlated "
                             f"{r:+.3f} on bars that do not overlap, and readings in "
                             "this band were followed by a choppy remainder "
                             f"{cond * 100:.0f}% of the time against a "
                             f"{base * 100:.0f}% base rate. This measures the session "
                             "behind you, not the one ahead."),
                }

        med = float(np.median(hist))
        pct_txt = (f"{pctile:.0f}th percentile of sessions at this mark"
                   if 1 <= pctile <= 99 else
                   f"{'below' if pctile < 50 else 'above'} all but "
                   f"{min(pctile, 100 - pctile):.0f}% of sessions at this mark")

        note = (
            f"Through {mark} the session has covered its ground at an efficiency of "
            f"{cur:.3f} — {pct_txt}, against a median of {med:.3f}. Sessions reading "
            f"here at {mark} finished {side} {p_best * 100:.0f}% of the time "
            f"(n={n_band}, base rate 33%)."
        ) if label != "mixed" else (
            f"Through {mark} the session has covered its ground at an efficiency of "
            f"{cur:.3f} — {pct_txt}, against a median of {med:.3f}. That is close "
            f"enough to an ordinary session that neither character is the better "
            f"description (n={n_band})."
        )

        return {
            "available": True,
            "mark": mark,
            "label": label,
            "side": side if label != "mixed" else "mixed",
            "confidence": conf,
            "efficiency": round(cur, 4),
            "pctile": round(pctile, 1),
            "median_at_mark": round(med, 4),
            "p_finish_choppy_pct": round(p_chop * 100, 1) if np.isfinite(p_chop) else None,
            "p_finish_trendy_pct": round(p_trend * 100, 1) if np.isfinite(p_trend) else None,
            "base_rate_pct": 33.3,
            "band": f"p{b_lo * 100:.0f}-{b_hi * 100:.0f}",
            "band_widened": widened,
            "n_band": n_band,
            "sessions": int(len(col)),
            "instrument": "SPY 5-minute closes, cash session",
            "bars_stale": stale,
            "last_bar": sess.index[-1].strftime("%H:%M"),
            "forward": fwd,
            "note": note,
            "method": (
                "Kaufman efficiency ratio — net move divided by total travel — on "
                "5-minute closes from the cash open to this mark. Efficiency falls "
                "mechanically with bar count, so the reading is scored only against "
                f"the {len(col):,} historical sessions AT THE SAME MARK, never across "
                "clock times. Class cuts are the terciles of final-session efficiency; "
                "the confidence is the measured frequency with which this band finished "
                "in that class, recomputed from the sample rather than stored."
            ),
            "caveat": (
                "Describes the session that has happened. The forward correlation is a "
                "measured null, so this is context for reading the tape you are in, "
                "not a statement about the hours ahead."
            ),
        }
    except Exception as e:
        logger.warning(f"session_chop failed: {e}")
        return {"available": False, "reason": "computation failed"}
