"""What the overnight (Globex) session implies about the RTH session to come.

The question this answers is not "which way" but "how much, and which side" —
what an intraday ES trader can reasonably expect the session to DO, read at
09:30 when the overnight range is already known and the cash session hasn't
started.

WHY THIS IS NEW: it needs real CME futures data covering 18:00-09:30 ET. SPY's
extended session stops at 20:00 and the cash index doesn't trade overnight at
all, so until the futures feed landed, none of this was measurable here.

THE FINDING THAT REFRAMES EVERYTHING ELSE: ES cannot gap away from its overnight
range, because it trades continuously into the open. All 494 sessions studied
opened INSIDE the overnight range — not most, all. Conventional gap statistics
(prior cash close to cash open) describe a move that already traded, with real
volume, at prices you can see. So the useful question is not "did it gap" but
WHERE IN the overnight range the cash session opens — and that turns out to
predict which side breaks, monotonically and hard.

Sample: ~494 sessions, two years, front contract by volume per session. Every
statistic here is computed WITHIN a session (range ratios, position within a
range, which extreme broke), so contract rolls need no back-adjustment — a roll
gap between sessions cannot contaminate a within-session measure.
"""

from __future__ import annotations

import logging
from datetime import time as _dtime

import numpy as np
import pandas as pd

from src._cache_util import result_cached as _result_cached

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_RTH_OPEN = (9, 30)
_RTH_CLOSE = (16, 0)
_ON_OPEN_HOUR = 18

# Two years of quarterly contracts. ES is H/M/U/Z; the free tier carries 2y, so
# this list is the whole available history rather than a choice.
_CONTRACTS = ["ESU4", "ESZ4", "ESH5", "ESM5", "ESU5", "ESZ5", "ESH6", "ESM6", "ESU6"]

# A session needs most of its bars to be measurable at all. Half-days make every
# range statistic small in the same direction, which is worse than dropping them.
_MIN_RTH_BARS = 70        # of 78 in a full cash session
_MIN_ON_BARS = 100        # of ~198 overnight
_MIN_BUCKET = 25          # below this a conditional rate is noise; omit the row


def _panel() -> pd.DataFrame:
    """Per-session overnight/RTH features across the available contracts."""
    from src.futures_data import fetch_bars

    frames, loaded, missed = [], [], []
    for c in _CONTRACTS:
        df = fetch_bars(c, resolution="5min", limit=50000)
        if df is None or df.empty:
            missed.append(c)
            continue
        d = df.copy()
        d["contract"] = c
        frames.append(d)
        loaded.append(c)
    if not frames:
        return pd.DataFrame()
    if missed:
        # A dropped contract removes a whole quarter of sessions. The remaining
        # statistics still LOOK fine, which is exactly why this has to surface
        # rather than be inferred from a sample-size someone would have to know
        # to check.
        logger.warning(f"ES history incomplete — missing {missed}")

    allb = pd.concat(frames)
    allb["date"] = allb.index.normalize()

    # Which contract was actually trading on a given date is a volume question,
    # not a calendar one — the expiring contract keeps quoting long after the
    # volume has left it.
    vol = allb.groupby(["date", "contract"])["Volume"].sum().reset_index()
    front = vol.sort_values("Volume").groupby("date").tail(1).set_index("date")["contract"]
    allb = allb[allb["contract"].values == front.reindex(allb["date"]).values]

    hhmm = [(t.hour, t.minute) for t in allb.index]
    is_rth = [_RTH_OPEN <= x < _RTH_CLOSE for x in hhmm]
    rth_dates = pd.DatetimeIndex(sorted(allb.loc[is_rth, "date"].unique()))
    if len(rth_dates) == 0:
        return pd.DataFrame()
    rth_set = set(rth_dates)

    # An evening bar belongs to the NEXT cash session, which is what makes the
    # overnight range a leading indicator rather than a trailing one.
    def _sess(ts, d):
        if ts.hour >= _ON_OPEN_HOUR:
            pos = rth_dates.searchsorted(d, side="right")
            return rth_dates[pos] if pos < len(rth_dates) else pd.NaT
        return d if d in rth_set else pd.NaT

    allb["session"] = [_sess(t, d) for t, d in zip(allb.index, allb["date"])]
    allb = allb.dropna(subset=["session"])
    allb["seg"] = ["rth" if _RTH_OPEN <= x < _RTH_CLOSE
                   else ("on" if (x >= (_ON_OPEN_HOUR, 0) or x < _RTH_OPEN) else "post")
                   for x in hhmm[:0] or [(t.hour, t.minute) for t in allb.index]]

    rows = []
    for sess, g in allb.groupby("session"):
        on, rth = g[g["seg"] == "on"], g[g["seg"] == "rth"]
        if len(rth) < _MIN_RTH_BARS or len(on) < _MIN_ON_BARS:
            continue
        onh, onl = float(on["High"].max()), float(on["Low"].min())
        onr = onh - onl
        if onr <= 0:
            continue
        o = float(rth["Open"].iloc[0])
        rh, rl, rc = float(rth["High"].max()), float(rth["Low"].min()), float(rth["Close"].iloc[-1])
        rows.append({
            "session": sess, "onh": onh, "onl": onl, "on_range": onr,
            "open": o, "rth_high": rh, "rth_low": rl, "rth_close": rc,
            "rth_range": rh - rl, "rth_ret": rc - o,
            "open_pct_in_on": (o - onl) / onr,
            "broke_onh": rh > onh, "broke_onl": rl < onl,
        })
    if not rows:
        return pd.DataFrame()

    s = pd.DataFrame(rows).set_index("session").sort_index()
    s["on_range_pct"] = s["on_range"] / s["open"] * 100
    s["ratio"] = s["rth_range"] / s["on_range"]
    s["prior_rth_close"] = s["rth_close"].shift(1)
    s["true_gap"] = s["open"] - s["prior_rth_close"]
    s.attrs["contracts_loaded"] = loaded
    s.attrs["contracts_missing"] = missed
    return s


# Where the cash open sits inside the overnight range. The edges are where the
# asymmetry lives, so they get their own buckets rather than being averaged into
# a middle that behaves nothing like them.
_OPEN_BANDS = [(-9.0, 0.2, "bottom 20%"), (0.2, 0.4, "lower"), (0.4, 0.6, "middle"),
               (0.6, 0.8, "upper"), (0.8, 9.0, "top 20%")]


def _pos_band(x: float) -> str | None:
    for lo, hi, lab in _OPEN_BANDS:
        if lo <= x < hi:
            return lab
    return None


def _compute_base_rates() -> dict:
    """The historical study. Two minutes cold — nine contracts paced against the
    free tier's 5 calls/minute — so it is cached by `overnight_base_rates`."""
    s = _panel()
    if s.empty or len(s) < 100:
        return {"available": False, "reason": "insufficient ES session history"}

    n = len(s)
    s = s.copy()
    s["band"] = [_pos_band(x) for x in s["open_pct_in_on"]]
    s["onq"] = pd.qcut(s["on_range_pct"], 4, labels=["tight", "below avg", "above avg", "wide"])

    # 1. Does the overnight range survive the cash session?
    both = bool_pct = (s["broke_onh"] & s["broke_onl"])
    one_sided = s["broke_onh"] ^ s["broke_onl"]
    inside = (~s["broke_onh"]) & (~s["broke_onl"])

    # 2. Position in the overnight range -> which side gives way.
    by_pos = []
    for _, _, lab in _OPEN_BANDS:
        sub = s[s["band"] == lab]
        if len(sub) < _MIN_BUCKET:
            continue
        by_pos.append({
            "band": lab,
            "n": int(len(sub)),
            "breaks_on_high_pct": round(float(sub["broke_onh"].mean() * 100), 1),
            "breaks_on_low_pct": round(float(sub["broke_onl"].mean() * 100), 1),
            "both_pct": round(float((sub["broke_onh"] & sub["broke_onl"]).mean() * 100), 1),
            "median_rth_range": round(float(sub["rth_range"].median()), 1),
        })

    # 3. How big is the cash session, given the overnight range.
    by_size = []
    for lab, sub in s.groupby("onq", observed=True):
        if len(sub) < _MIN_BUCKET:
            continue
        by_size.append({
            "band": str(lab),
            "n": int(len(sub)),
            "median_on_range": round(float(sub["on_range"].median()), 1),
            "rth_p25": round(float(sub["rth_range"].quantile(0.25)), 1),
            "rth_median": round(float(sub["rth_range"].median()), 1),
            "rth_p75": round(float(sub["rth_range"].quantile(0.75)), 1),
            "rth_over_on": round(float(sub["ratio"].median()), 2),
            "one_sided_pct": round(float((sub["broke_onh"] ^ sub["broke_onl"]).mean() * 100), 1),
        })

    # 4. The real overnight move, and what the cash session does with it. Fill
    #    and continuation are DIFFERENT questions and the answers diverge
    #    sharply — reporting only "gaps fill" would be the misleading half.
    g = s.dropna(subset=["true_gap"]).copy()
    gaps = []
    if len(g) >= 4 * _MIN_BUCKET:
        g["gq"] = pd.qcut(g["true_gap"].abs(), 4, labels=["tiny", "small", "moderate", "large"])
        for lab, sub in g.groupby("gq", observed=True):
            if len(sub) < _MIN_BUCKET:
                continue
            filled = ((sub["rth_low"] <= sub["prior_rth_close"])
                      & (sub["rth_high"] >= sub["prior_rth_close"]))
            gaps.append({
                "band": str(lab),
                "n": int(len(sub)),
                "median_gap": round(float(sub["true_gap"].abs().median()), 2),
                "fills_prior_close_pct": round(float(filled.mean() * 100), 1),
                "continues_pct": round(float((np.sign(sub["rth_ret"])
                                              == np.sign(sub["true_gap"])).mean() * 100), 1),
            })

    missing = s.attrs.get("contracts_missing") or []
    return {
        "available": True,
        "sessions": int(n),
        "from": str(s.index.min().date()),
        "to": str(s.index.max().date()),
        # Named, not implied by the session count. A quarter of missing sessions
        # changes nothing about how these tables LOOK.
        "complete": not missing,
        "contracts_missing": missing,
        "range_survival": {
            "one_sided_pct": round(float(one_sided.mean() * 100), 1),
            "both_sides_pct": round(float(both.mean() * 100), 1),
            "held_inside_pct": round(float(inside.mean() * 100), 1),
            "note": ("The overnight range almost never survives the cash session — it holds "
                     "on about one day in twenty. The tradeable split is not whether it "
                     "breaks but whether ONE side breaks or both, and one side is roughly "
                     "three times as common as both."),
        },
        "by_open_position": by_pos,
        "by_overnight_size": by_size,
        "overnight_move": gaps,
        "median_on_range": round(float(s["on_range"].median()), 1),
        "median_rth_range": round(float(s["rth_range"].median()), 1),
        "notes": [
            "Every session studied opened INSIDE its overnight range — ES trades "
            "continuously into 09:30, so it cannot gap away from it. Cash-close-to-"
            "cash-open gap statistics describe a move that already traded overnight.",
            "Read at 09:30: the overnight range is known and the cash session is not.",
        ],
    }


@_result_cached("es_overnight_base")
def _cached_base_rates() -> dict:
    r = _compute_base_rates()
    # The shared cache layer only refuses to store empty dicts and ones carrying
    # `error`, so a study that is merely INCOMPLETE would otherwise persist for
    # the full 12h TTL looking exactly like a complete one. Tag it so a dropped
    # contract costs one slow rebuild rather than half a day of quiet wrongness.
    if not r.get("available") or not r.get("complete"):
        return {**r, "error": "incomplete history"}
    return r


def overnight_base_rates() -> dict:
    """Cached historical study — memory, then Supabase, then recompute."""
    return {k: v for k, v in _cached_base_rates().items() if k != "error"}


def overnight_read(base: dict | None = None) -> dict:
    """Today's overnight range, and what the base rates say to expect from it.

    Degrades to the historical tables alone if the live session can't be read —
    a missing live read must not blank the study, which is useful on its own.
    """
    from src.futures_data import fetch_front_bars

    base = base or overnight_base_rates()
    if not base.get("available"):
        return base

    bars, ticker = fetch_front_bars("ES", resolution="5min", limit=600)
    if bars is None or bars.empty:
        return {**base, "live": None}

    # The most recent overnight block: bars from the last 18:00 boundary forward
    # to either the cash open or the last bar, whichever comes first.
    hhmm = [(t.hour, t.minute) for t in bars.index]
    last_ts = bars.index[-1]
    session_day = (last_ts + pd.Timedelta(days=1)).normalize() if last_ts.hour >= _ON_OPEN_HOUR \
        else last_ts.normalize()
    start = (session_day - pd.Timedelta(days=1)).replace(hour=_ON_OPEN_HOUR, minute=0)
    on = bars[(bars.index >= start) & (bars.index < session_day.replace(hour=9, minute=30))]
    if len(on) < 20:
        return {**base, "live": None}

    onh, onl = float(on["High"].max()), float(on["Low"].min())
    onr = onh - onl
    last = float(bars["Close"].iloc[-1])
    if onr <= 0:
        return {**base, "live": None}

    pos = (last - onl) / onr
    band = _pos_band(pos)
    match = next((b for b in base["by_open_position"] if b["band"] == band), None)

    # Which size bucket today's overnight range falls into, by comparing against
    # the historical medians rather than re-deriving quantiles from one session.
    size = None
    for b in base.get("by_overnight_size", []):
        if onr <= b["median_on_range"] * 1.25:
            size = b
            break
    size = size or (base.get("by_overnight_size") or [None])[-1]

    return {
        **base,
        "live": {
            "contract": ticker,
            "session_date": str(session_day.date()),
            "overnight_high": round(onh, 2),
            "overnight_low": round(onl, 2),
            "overnight_range": round(onr, 2),
            "last": round(last, 2),
            "position_in_range_pct": round(float(pos * 100), 1),
            "band": band,
            "expected": ({
                "breaks_on_high_pct": match["breaks_on_high_pct"],
                "breaks_on_low_pct": match["breaks_on_low_pct"],
                "n": match["n"],
            } if match else None),
            "rth_range_expectation": ({
                "p25": size["rth_p25"], "median": size["rth_median"], "p75": size["rth_p75"],
                "n": size["n"],
            } if size else None),
        },
    }
