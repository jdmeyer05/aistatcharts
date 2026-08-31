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
efficiency so far and efficiency to come runs between -0.08 and +0.04 at all
eleven marks, signs flipping between the two halves of the sample — a flat null,
reproducing an earlier null found on 30-second SPX bars over a different window.
So the module ships that number rather than hiding it: a choppy morning is not
evidence of a choppy afternoon, and the card says so in the same breath it says
the morning was choppy.

FITTED ON A ROLLING WINDOW, NOT ALL OF HISTORY. The efficiency distribution
drifts. Measured on this sample, the 33rd percentile of final-session efficiency
fell from 0.084 in 2021-22 to 0.070 in 2024-26 — sessions genuinely got choppier
— and a class cut fitted on everything since 2021 therefore calls 36.4% of
recent sessions choppy where a stationary cut would call 33.3%. That is not a
rounding error: it showed up in the walk-forward scorecard as the choppy side
delivering 4 points MORE than it promised while the trendy side delivered 3
points less, a symmetry that is the signature of stale cuts rather than a bad
threshold.

So the fit uses the most recent 750 sessions. Walk-forward, that cuts weighted
calibration error from 2.23 to 1.43 points with accuracy and coverage unchanged,
and the optimum is broad — every window from 600 to 1000 beats an expanding one.
Stated honestly, the gain is concentrated in the recent half of the test window
(2.39 -> 0.65 points) and the window is marginally WORSE early on (2.26 -> 2.58),
which is what a drift correction should look like: there is less to correct
before the drift has accumulated, and a shorter window is meanwhile noisier.
Production always forecasts forward with the full 750 behind it, so it lives in
the regime where the correction pays.

CONFIDENCE IS MEASURED, NOT WORDED. "Likely" and "confident" are set by the
historical frequency with which a reading in today's percentile band, at today's
mark, belonged to a session that FINISHED in that class — computed from the
sample on every call, never a constant typed into this file. That frequency
grows through the session as the elapsed part comes to dominate the whole, and
it grows with how far the reading sits from the middle.

The two sides are NOT symmetric and the thresholds do not pretend otherwise. A
session that has trended hard by midday has banked a net move that is difficult
to undo, so the trendy side resolves early — top decile at 11:30 finished trendy
70% of the time out of sample against a 33% base. A quiet morning can still
break out, so the choppy side resolves late — bottom decile at 11:30 finished
choppy only 47% of the time.

Measured against the thresholds below, on the full sample this is fitted on:
"confident trendy" first becomes reachable at 11:00, "confident choppy" not
until 14:00, and before 10:30 no band on either side clears even the "likely"
floor. So the early session can report a lean and cannot report a conviction,
and that is the data verdict rather than a design choice.
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

# 30-minute marks. The session does not get one until 10:30, and that is
# measured rather than assumed: at 10:00 the best any percentile band manages is
# 41% choppy / 44% trendy, both under the 45% floor, so EVERY reading there
# resolves to "mixed". A block that can only ever print one word is worse than
# no block — it looks like a read and carries nothing — so the mark is gone and
# the card simply says nothing before 10:30.
_MARKS = ("10:30", "11:00", "11:30", "12:00", "12:30",
          "13:00", "13:30", "14:00", "14:30", "15:00")

_FULL_BARS = 78          # 09:30-16:00 inclusive on a 5-minute grid
_MIN_BARS = _FULL_BARS - 2
_MIN_CELL = 40           # below this a band is widened rather than quoted
_FIT_WINDOW = 750        # sessions; the cuts track the tape rather than average it
_TODAY_TTL_S = 60        # today's bars are the live half; history is not

# Above this the sign-flip null is sampled rather than enumerated. The bound is
# about MEMORY, not accuracy: enumeration allocates 2^(n-1) x (n-1) floats, which
# is 0.2 MB at n=12, 3.9 MB at n=16 and 80 MB at n=20. An hour holds 11 returns
# so the cap is never approached in practice — which is exactly why it needs to
# be set deliberately rather than left where nothing happens to reach it.
_EXACT_MAX_N = 16

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


# The hourly buckets the session-path table is drawn on. 15:30 is half the width
# of the others, which matters here more than anywhere else on the card: fewer
# bars raises the efficiency ratio mechanically, and its median duly measures
# 0.408 against ~0.27 everywhere else. It therefore gets its own distribution,
# as every bucket does, and its readings are never compared with the rest.
_HOUR_BUCKETS = ("09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30")
_BUCKET_BARS = {b: (12 if b != "15:30" else 6) for b in _HOUR_BUCKETS}
# An hour is scored only when EVERY bar of it has arrived. The old rule accepted
# four fifths, which quietly reintroduced the one bias this module exists to
# avoid: efficiency falls with bar count, so a 10-bar hour ranked against a
# population of 12-bar hours reads systematically more trending than it was. The
# 5-minute grid is fixed and SPY trades every bucket, so a short hour means the
# feed has not caught up yet, not that the hour was short.


def _bucket_of(ts) -> str | None:
    """Which hourly bucket a bar belongs to, or None outside the cash session."""
    m = ts.hour * 60 + ts.minute
    if m < 570 or m >= 960:
        return None
    return _HOUR_BUCKETS[min((m - 570) // 60, 6)]


def _bucket_idx(index: pd.DatetimeIndex) -> np.ndarray:
    """Bucket number per bar, -1 outside the cash session. Vectorised: the
    string form of this ran a comprehension per bucket per session and cost
    nearly two seconds over five years of bars, on the cold-start path."""
    m = index.hour.to_numpy() * 60 + index.minute.to_numpy()
    idx = np.full(len(m), -1, dtype=int)
    ok = (m >= 570) & (m < 960)
    idx[ok] = np.minimum((m[ok] - 570) // 60, 6)
    return idx


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


def _sign_flip_p(r: np.ndarray) -> tuple[float, float]:
    """Two-sided random-walk test for one hour. Returns (p_trend, p_chop).

    The null flips the SIGN of each return, keeping every move's magnitude and
    destroying only its direction. Permuting the returns instead would be
    useless: the efficiency ratio is invariant to it, since both the sum and the
    sum of absolute values survive a reordering unchanged.

    Enumerated exactly rather than sampled, for n small enough — 2^n sign
    vectors, halved by symmetry. Exact means the card cannot flicker between
    loads, which a Monte Carlo p-value would.
    """
    n = len(r)
    if n < 5 or not np.isfinite(r).all():
        return float("nan"), float("nan")
    a_ = float(np.abs(r).sum())
    if a_ <= 0:
        return float("nan"), float("nan")
    obs = abs(float(r.sum())) / a_
    if n <= _EXACT_MAX_N:
        # bit i of k gives the sign of return i; the first sign is fixed at +1
        # because flipping every sign leaves |sum| unchanged.
        k = np.arange(1 << (n - 1), dtype=np.int64)
        signs = 1.0 - 2.0 * ((k[:, None] >> np.arange(n - 1)) & 1).astype(float)
        sums = r[0] + signs @ r[1:]
    else:
        rng = np.random.default_rng(0)      # fixed: the reading must be stable
        signs = rng.choice([-1.0, 1.0], size=(20000, n))
        sums = (signs * r).sum(1)
    null = np.abs(sums) / a_
    return float((null >= obs).mean()), float((null <= obs).mean())


def _hour_panel(fine: pd.DataFrame) -> dict:
    """Per-bucket efficiency history — one distribution per hourly slot.

    Only the distribution survives. This used to carry class cuts and a
    per-class robustness score for a confidence word, all of which went when the
    hourly labels did: an hour cannot be shown to have trended, so nothing built
    to qualify that claim has anything left to qualify. What remains is the
    ranking the card still prints, and the buckets stay separate because the
    15:30 slot is half the width of the others.
    """
    out: dict = {}
    day = fine.index.normalize()
    per: dict = {b: [] for b in _HOUR_BUCKETS}
    for _, g in fine.groupby(day):
        if len(g) < _MIN_BARS:
            continue
        bi = _bucket_idx(g.index)
        c = g["Close"].to_numpy(dtype=float)
        for j, k in enumerate(_HOUR_BUCKETS):
            cc = c[bi == j]
            if len(cc) < _BUCKET_BARS[k]:
                continue
            e = _er(cc)
            if np.isfinite(e):
                per[k].append(e)

    for k, rows in per.items():
        if len(rows) < 200:
            continue
        er = np.array(rows, dtype=float)
        lo, hi = np.quantile(er, [1 / 3, 2 / 3])
        out[k] = {"er": er, "lo": float(lo), "hi": float(hi), "n": len(er)}
    return out


def _hourly_rows(sess: pd.DataFrame, hp: dict) -> list:
    """This session hour by hour — and whether any of it beat a coin flip.

    THE CORRECTION THIS FUNCTION EXISTS IN. The first version labelled every
    hour choppy / mixed / trendy from its percentile against the same hour in
    history, with a confidence word attached. That was wrong, and wrong in a way
    a percentile actively hides: the population it ranked against is ITSELF
    almost entirely random walks, so the 70th percentile of it is a random walk
    too, and the label read "likely trendy" over an hour that had done nothing.

    Measured over 8,708 hours against a sign-flip null, exactly 9.5% of hours
    clear p<0.10 on the trending side and 10.0% on the choppy side — chance is
    10%. There is no excess in either tail, so not one hour in the sample is
    distinguishable from a coin flip beyond the rate chance alone supplies. The
    same test at 1-MINUTE resolution, where an hour has 60 bars instead of 12,
    gives 9.5% and 10.0%: this is not a shortage of resolution, it is an absence
    of the thing being measured. Reversal rate fails identically — the sign-flip
    rate of 1-minute returns averages 0.505 against 0.500 for a random walk, and
    its cross-sectional spread is 1.07x what binomial noise alone would produce.

    So the labels are gone. What is left is what is true: how much of the hour's
    travel became net progress, where that ranks among the same hour in history,
    and whether it beat the null. On most hours the honest answer to the last is
    no, and printing that is the point rather than a failure of the module.
    """
    rows = []
    if sess is None or sess.empty or not hp:
        return rows
    bi = _bucket_idx(sess.index)
    c = sess["Close"].to_numpy(dtype=float)
    for j, k in enumerate(_HOUR_BUCKETS):
        h = hp.get(k)
        if not h:
            continue
        cc = c[bi == j]
        if len(cc) < _BUCKET_BARS[k]:
            rows.append({"bucket": k, "state": "pending" if len(cc) else "not_started",
                         "bars": int(len(cc)), "bars_expected": _BUCKET_BARS[k]})
            continue
        r = np.diff(cc)
        a_ = float(np.abs(r).sum())
        if not np.isfinite(a_) or a_ <= 0:
            rows.append({"bucket": k, "state": "flat", "bars": int(len(r)),
                         "bars_expected": _BUCKET_BARS[k]})
            continue
        e = abs(float(r.sum())) / a_
        p_trend, p_chop = _sign_flip_p(r)
        pct = float((h["er"] < e).mean() * 100)

        # A verdict, not a label. "Coin flip" is the answer roughly nine times in
        # ten and is stated plainly rather than dressed as "mixed", which reads
        # like a measurement of something in between.
        if not (np.isfinite(p_trend) and np.isfinite(p_chop)):
            # NOT a coin flip. "Coin flip" is a result — the null was run and the
            # hour did not beat it. When the test could not run at all, saying so
            # is a different statement, and collapsing the two prints an absence
            # as a measurement.
            verdict, p = "untested", float("nan")
        elif p_trend < 0.10:
            verdict, p = "trended", p_trend
        elif p_chop < 0.10:
            verdict, p = "chopped", p_chop
        else:
            verdict, p = "coin flip", min(p_trend, p_chop)

        rows.append({
            "bucket": k, "state": "complete",
            "verdict": verdict,
            "p": round(p, 3) if np.isfinite(p) else None,
            "p_trend": round(p_trend, 3) if np.isfinite(p_trend) else None,
            "p_chop": round(p_chop, 3) if np.isfinite(p_chop) else None,
            "net_progress_pct": round(e * 100, 1),
            "efficiency": round(e, 4),
            "pctile": round(pct, 1),
            "median_at_bucket": round(float(np.median(h["er"])), 4),
            "bars": int(len(cc)), "bars_expected": _BUCKET_BARS[k],
            "returns": int(len(r)),
            "n_history": h["n"],
        })
    return rows


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
                    "reason": "before 10:30 — no mark yet separates from its base rate"}
        mark = elapsed[-1]

        # The day is part of the key: the panel is built to EXCLUDE today, so a
        # container living across a session boundary would otherwise reuse a
        # panel that excludes the wrong date.
        key = ("panel", len(fine), str(fine.index[-1]), str(today.date()))
        hit = _CACHE.get(key)
        if hit and (_now_s() - hit[0]) < _TTL_S:
            panel = hit[1]
        else:
            panel = _panel(fine[fine.index.normalize() != today])
            if not panel.empty:
                _CACHE[key] = (_now_s(), panel)
        if panel.empty or len(panel) < 200:
            return {"available": False, "reason": "not enough history to calibrate"}
        # The cuts and band rates come from the recent window only. `panel` is
        # sorted by date, so this is the tail. Everything older still exists —
        # it is simply not allowed to define what "choppy" currently means.
        history_available = int(len(panel))
        panel = panel.iloc[-_FIT_WINDOW:] if len(panel) > _FIT_WINDOW else panel

        # The per-bucket distributions are a separate object from the cumulative
        # panel — an hour's efficiency and a session-to-date efficiency are not
        # the same measurement and share no cuts. Cached on the same key.
        hkey = ("hours", len(fine), str(fine.index[-1]), str(today.date()))
        hhit = _CACHE.get(hkey)
        if hhit and (_now_s() - hhit[0]) < _TTL_S:
            hp = hhit[1]
        else:
            # Same rolling treatment: an hourly tercile cut fitted on 2021
            # over-calls choppy hours in 2026 for exactly the reason the
            # session-level one does. The hourly labels are descriptive and so
            # carry no calibration score of their own, which is precisely why
            # the correction is applied by argument rather than waiting for a
            # scorecard to catch it.
            _hist = fine[fine.index.normalize() != today]
            _days = _hist.index.normalize().unique()
            if len(_days) > _FIT_WINDOW:
                _hist = _hist[_hist.index.normalize() >= _days[-_FIT_WINDOW]]
            hp = _hour_panel(_hist)
            if hp:
                _CACHE[hkey] = (_now_s(), hp)

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

        # Measured on the sessions actually used, not assumed from the tercile
        # construction: the cuts come from the whole panel while these rates are
        # computed on the subset that has a bar at this mark, so the two need not
        # be exactly a third.
        base_chop = float((fin < lo_f).mean())
        base_trend = float((fin >= hi_f).mean())

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

        # THE SAME TEST THE HOURLY ROWS GET. An hour cannot beat a coin flip; a
        # session sometimes can, because it has 78 bars rather than 12 and
        # because whole sessions do deviate from a random walk where single
        # hours do not — 12.9% of sessions clear the trending tail at p<0.10 and
        # 8.0% the choppy one, against a 10% chance rate. So the percentile below
        # is worth reporting, and this is what says whether it means anything.
        # ALL bars, not truncated to the mark. The percentile above must be
        # clock-matched, because efficiency falls with bar count and 11:30 and
        # 15:00 are different populations. This test needs no such matching — it
        # compares the session only against sign-flipped copies of ITSELF — so
        # cutting it at the last completed mark would discard live bars for
        # nothing. It cost a real reading: truncated at 15:00 this session scored
        # p=0.177 and read "coin flip", while the same session through 15:30 is
        # p=0.015, the 1st percentile of the sample.
        _sess_r = sess["Close"].to_numpy(dtype=float)
        p_trend_sf, p_chop_sf = _sign_flip_p(np.diff(_sess_r))
        rw = None
        if np.isfinite(p_trend_sf) and np.isfinite(p_chop_sf):
            rw = {
                "p_trend": round(p_trend_sf, 4),
                "p_chop": round(p_chop_sf, 4),
                "through": sess.index[-1].strftime("%H:%M"),
                "bars": int(len(_sess_r)),
                "verdict": ("trended" if p_trend_sf < 0.10
                            else "chopped" if p_chop_sf < 0.10 else "coin flip"),
                "note": (
                    "Sign-flip test on the session so far: every move keeps its size "
                    "and loses its direction, and this is how often chance alone "
                    "produces at least this much net progress. "
                    + (f"Only {p_chop_sf * 100:.1f}% of those worlds chop this hard."
                       if p_chop_sf < 0.10 else
                       f"Only {p_trend_sf * 100:.1f}% of those worlds trend this far."
                       if p_trend_sf < 0.10 else
                       "This session is not distinguishable from a random walk.")
                ),
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
            f"(n={n_band}, against a base rate of "
            f"{(base_trend if side == 'trendy' else base_chop) * 100:.0f}%)."
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
            "base_choppy_pct": round(base_chop * 100, 1),
            "base_trendy_pct": round(base_trend * 100, 1),
            "band": f"p{b_lo * 100:.0f}-{b_hi * 100:.0f}",
            "band_widened": widened,
            "n_band": n_band,
            "sessions": int(len(col)),
            "fit_window": _FIT_WINDOW,
            "history_available": history_available,
            "instrument": "SPY 5-minute closes, cash session",
            "bars_stale": stale,
            "last_bar": sess.index[-1].strftime("%H:%M"),
            "forward": fwd,
            # Whether the percentile above describes anything a coin flip
            # would not have produced. The hourly rows almost never pass
            # this; a session sometimes does.
            "random_walk": rw,
            # The day's rhythm, hour by hour. A separate measurement from
            # everything above: those are cumulative from the open, these are
            # each hour on its own, and the two can disagree — a session whose
            # every hour trended in an opposite direction reads choppy overall.
            "hourly": _hourly_rows(sess, hp),
            "hourly_note": (
                "An hour of this tape is statistically a coin flip. Against a "
                "sign-flip null, 9.5% of 8,708 historical hours clear p<0.10 on the "
                "trending side and 10.0% on the choppy side, where chance is 10% — "
                "no excess in either tail, and the same at 1-minute resolution, so "
                "it is not a shortage of bars. These rows therefore rank the "
                "session's hours and report whether any beat the null; they do not "
                "claim an hour trended. Nor do they forecast: out of sample, nothing "
                "knowable at the top of an hour predicts the next one (R2 -0.001, "
                "classification 34.5% against a 34.5% baseline)."
            ),
            "hourly_forecast": {
                "verdict": "null",
                "oos_r2": -0.0013,
                "accuracy_pct": 34.5,
                "baseline_pct": 34.5,
                "note": ("Prior hour efficiency, reversal rate, volatility, range and "
                         "the session's cumulative reading were fitted on 60% of "
                         "sessions and scored on the rest. The only variable that "
                         "looked predictive was time of day, and it was the "
                         "half-width 15:30 bucket: excluding it, that correlation "
                         "falls from +0.127 to -0.006."),
            },
            "note": note,
            "method": (
                "Kaufman efficiency ratio — net move divided by total travel — on "
                "5-minute closes from the cash open to this mark. Efficiency falls "
                "mechanically with bar count, so the reading is scored only against "
                f"the {len(col):,} most recent historical sessions AT THE SAME MARK, "
                "never across clock times. The window is rolling rather than the full "
                f"history ({history_available:,} sessions available) because the "
                "efficiency distribution drifts, and cuts fitted on 2021 over-call "
                "choppy today. Class cuts are the terciles of final-session efficiency; "
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
