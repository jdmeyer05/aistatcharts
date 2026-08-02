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
from datetime import date as _date, time as _dtime, timedelta as _timedelta

import numpy as np
import pandas as pd

from src._cache_util import result_cached as _result_cached

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_RTH_OPEN = (9, 30)
_RTH_CLOSE = (16, 0)
_ON_OPEN_HOUR = 18

# ES is quarterly: H=Mar M=Jun U=Sep Z=Dec, suffixed with the year's last digit.
#
# DERIVED, never hardcoded. A fixed list ages out silently: once the front month
# moves past its last entry the study quietly loses the most recent quarter —
# the part that matters most — while every listed contract still loads, so
# `contracts_missing` stays empty and the payload reports itself complete. That
# is the same "looks whole, isn't" failure as the 1000-row cache read.
_QUARTER_CODES = {3: "H", 6: "M", 9: "U", 12: "Z"}


def _contracts_for(as_of: _date | None = None, years: int = 2) -> list[str]:
    """Quarterly ES contracts whose front-month window covers the lookback.

    One quarter ahead of `as_of` is included: near an expiry the next contract
    already carries the volume, and the per-session volume test downstream picks
    whichever was genuinely trading.
    """
    today = as_of or _date.today()
    start = today - _timedelta(days=365 * years)
    end = today + _timedelta(days=120)          # one quarter ahead
    out: list[str] = []
    for year in range(start.year, end.year + 1):
        for month, code in sorted(_QUARTER_CODES.items()):
            # Expiry is the third Friday; mid-month is close enough to decide
            # whether a contract's window overlaps the lookback at all.
            expiry = _date(year, month, 15)
            if start <= expiry <= end:
                out.append(f"ES{code}{year % 10}")
    return out

# A session needs most of its bars to be measurable at all. Half-days make every
# range statistic small in the same direction, which is worse than dropping them.
_MIN_RTH_BARS = 70        # of 78 in a full cash session
_MIN_ON_BARS = 100        # of ~198 overnight
_MIN_BUCKET = 25          # below this a conditional rate is noise; omit the row
_SESSION_MINUTES = 390    # 09:30-16:00


def _panel() -> pd.DataFrame:
    """Per-session overnight/RTH features across the available contracts."""
    from src.futures_data import fetch_bars

    frames, loaded, missed = [], [], []
    for c in _contracts_for():
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
                   for x in ((t.hour, t.minute) for t in allb.index)]

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
        bh, bl = rh > onh, rl < onl

        # Which side went FIRST, and when. The first break is the one a trader
        # is actually present for; "the session broke both" is an outcome, not a
        # decision they ever faced.
        hi_t = rth.index[rth["High"] > onh][0] if bh else None
        lo_t = rth.index[rth["Low"] < onl][0] if bl else None
        if hi_t is not None and (lo_t is None or hi_t <= lo_t):
            first, first_t = "high", hi_t
        elif lo_t is not None:
            first, first_t = "low", lo_t
        else:
            first, first_t = None, None

        rows.append({
            "session": sess, "onh": onh, "onl": onl, "on_range": onr,
            "open": o, "rth_high": rh, "rth_low": rl, "rth_close": rc,
            "rth_range": rh - rl, "rth_ret": rc - o,
            "open_pct_in_on": (o - onl) / onr,
            "broke_onh": bh, "broke_onl": bl,
            "first_break": first,
            "first_break_min": None if first_t is None
            else (first_t.hour * 60 + first_t.minute) - (_RTH_OPEN[0] * 60 + _RTH_OPEN[1]),
            "ext_high": (rh - onh) if bh else np.nan,
            "ext_low": (onl - rl) if bl else np.nan,
            "held_high": (rc > onh) if bh else np.nan,
            "held_low": (rc < onl) if bl else np.nan,
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
    # Keep the real bin edges. Deriving them from each bucket's observed min/max
    # leaves a sliver between buckets — 0.655 to 0.657 — where a live session
    # matches nothing and silently loses its range expectation. The outer edges
    # are opened up so a range beyond anything in the sample still lands.
    _SIZE_LABELS = ["tight", "below avg", "above avg", "wide"]
    s["onq"], _edges = pd.qcut(s["on_range_pct"], 4, labels=_SIZE_LABELS, retbins=True)
    # A finite sentinel, not inf: this payload is JSON-serialised into Supabase
    # and `Infinity` is not valid JSON — it would fail the cache write, or worse
    # round-trip into something unparseable. No overnight range is 999% of price.
    _edges = list(_edges)
    _edges[0], _edges[-1] = 0.0, 999.0

    # 1. Does the overnight range survive the cash session?
    both = s["broke_onh"] & s["broke_onl"]
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

    # 3. How big is the cash session, given the overnight range. Carried
    #    alongside: how much of the FULL 23-hour range was already made before
    #    the bell. A trader watching only 09:30-16:00 is watching the minority
    #    of the day's movement, and that share climbs sharply when the overnight
    #    was wide — which is the difference between a session with room and one
    #    that has already spent itself.
    s["full_range"] = (np.maximum(s["onh"], s["rth_high"])
                       - np.minimum(s["onl"], s["rth_low"]))
    s["on_share"] = s["on_range"] / s["full_range"] * 100

    by_size = []
    for lab, sub in s.groupby("onq", observed=True):
        if len(sub) < _MIN_BUCKET:
            continue
        by_size.append({
            "band": str(lab),
            "n": int(len(sub)),
            # Buckets are formed on range as a PERCENT of price, so the edges
            # have to be carried in percent too. Matching a live session on
            # points against these medians drifts as the index level moves —
            # ES ran 6500->7500 across this sample alone.
            "on_range_pct_lo": round(_edges[_SIZE_LABELS.index(str(lab))], 3),
            "on_range_pct_hi": round(_edges[_SIZE_LABELS.index(str(lab)) + 1], 3),
            "median_on_range": round(float(sub["on_range"].median()), 1),
            "rth_p25": round(float(sub["rth_range"].quantile(0.25)), 1),
            "rth_median": round(float(sub["rth_range"].median()), 1),
            "rth_p75": round(float(sub["rth_range"].quantile(0.75)), 1),
            "rth_over_on": round(float(sub["ratio"].median()), 2),
            "one_sided_pct": round(float((sub["broke_onh"] ^ sub["broke_onl"]).mean() * 100), 1),
            "overnight_share_of_full_range_pct": round(float(sub["on_share"].median()), 1),
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

    # 5. Break anatomy. Extension is measured to the session extreme, so it is
    #    bounded by how much session is LEFT — which turns out to be the whole
    #    story. Per hour remaining it is flat (3.9/3.6/3.7/4.5 across timing
    #    buckets, Spearman +0.05), so "late breaks are weaker" is runway, not
    #    behaviour. Stated as a rate, that flatness becomes the useful part.
    b = s.dropna(subset=["first_break_min"]).copy()
    b["ext"] = np.where(b["first_break"] == "high", b["ext_high"], b["ext_low"])
    b["held"] = np.where(b["first_break"] == "high", b["held_high"], b["held_low"]).astype(float)
    b["other_side"] = np.where(b["first_break"] == "high",
                               b["broke_onl"], b["broke_onh"]).astype(float)
    b["remaining_hr"] = (_SESSION_MINUTES - b["first_break_min"]).clip(lower=15) / 60
    b["ext_per_hr"] = b["ext"] / b["remaining_hr"]

    anatomy = {}
    if len(b) >= 100:
        early = float((b["first_break_min"] <= 30).mean() * 100)
        anatomy = {
            "n": int(len(b)),
            "median_first_break_min": int(b["first_break_min"].median()),
            "breaks_within_30min_pct": round(early, 1),
            "extension_pts_per_hour_remaining": round(float(b["ext_per_hr"].median()), 2),
            "reversal_pct": round(float(b["other_side"].mean() * 100), 1),
            "closes_beyond_pct": round(float(b["held"].mean() * 100), 1),
            "up_extension_x_range": round(float((s.loc[s["broke_onh"], "ext_high"]
                                                 / s.loc[s["broke_onh"], "on_range"]).median()), 2),
            "down_extension_x_range": round(float((s.loc[s["broke_onl"], "ext_low"]
                                                   / s.loc[s["broke_onl"], "on_range"]).median()), 2),
            # Deliberately no hardcoded figures here — the numbers live in the
            # fields above, and prose that restates them goes stale silently the
            # first time the sample changes.
            "note": ("Two thirds of first breaks land inside the opening half hour — a break "
                     "you are waiting for usually arrives fast or not at all. Extension scales "
                     "with the session time still to run, and that rate is flat whenever the "
                     "break happens, so a late break is not weaker, it simply has less room."),
            "direction_note": ("Downside breaks extend further than upside ones, but not "
                               "dependably: the gap ran 0.40 vs 0.59 in 2024 and 0.45 vs 0.69 "
                               "in 2025, then nearly closed in 2026 at 0.41 vs 0.44. Across a "
                               "sample that rose 35%, upside breaks were also more frequent "
                               "(317 vs 270) — frequent shallow rallies, rarer sharper breaks."),
            "caveat": ("How far a break extends predicts whether it closes beyond almost "
                       "perfectly (11% shallow vs 85% deep) — but those are nearly the same "
                       "event, so it is anatomy, not a signal. Depth is not knowable at the "
                       "moment you have to act."),
        }

    # 6. Does any of this constitute an EDGE? Computed, not asserted, because a
    #    page full of break statistics reads as a trading signal unless it says
    #    otherwise in numbers. Enter at the level on the first break, hold to the
    #    cash close, no stop and no costs — the friendliest possible test.
    tradeability = {}
    fb = s.dropna(subset=["first_break"])
    if len(fb) >= 100:
        lvl = np.where(fb["first_break"] == "high", fb["onh"], fb["onl"])
        sgn = np.where(fb["first_break"] == "high", 1.0, -1.0)
        pnl = pd.Series(sgn * (fb["rth_close"].values - lvl), index=fb.index)
        by_year = {int(y): round(float(p.mean()), 2)
                   for y, p in pnl.groupby(pnl.index.year) if len(p) >= 40}
        tradeability = {
            "test": "enter at the level on the first break, hold to the cash close",
            "n": int(len(pnl)),
            "mean_pts": round(float(pnl.mean()), 2),
            "median_pts": round(float(pnl.median()), 2),
            "win_rate_pct": round(float((pnl > 0).mean() * 100), 1),
            "mean_pts_by_year": by_year,
            "verdict": ("No edge. Costs and slippage are not even included and it is already "
                        "negative. These base rates describe what a session DOES — useful for "
                        "sizing a target, placing a stop, and knowing when to expect the move "
                        "— but the break itself is not a trade."),
            "waiting_does_not_fix_it": (
                "Requiring the break to travel 0.25x the overnight range first lifts "
                "closes-beyond from 50% to 66%, which looks like an improvement and is not. "
                "Confirmed breaks only EXIST on days that already ran, so the filter samples "
                "winners. Compared on the same 326 sessions, entering at the level returned "
                "+12.6 pts against +1.2 for waiting — the 9-point give-up costs more than the "
                "better hit rate returns."),
        }

    missing = s.attrs.get("contracts_missing") or []
    return {
        "break_anatomy": anatomy,
        "tradeability": tradeability,
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
        "overnight_share_of_full_range_pct": round(float(s["on_share"].median()), 1),
        "notes": [
            "Every session studied opened INSIDE its overnight range — ES trades "
            "continuously into 09:30, so it cannot gap away from it. Cash-close-to-"
            "cash-open gap statistics describe a move that already traded overnight.",
            "Read at 09:30: the overnight range is known and the cash session is not.",
            "Most of the day's range is already made before the bell — the overnight "
            "session accounts for the median share reported above. Watching only "
            "09:30-16:00 means watching the minority of the movement.",
        ],
    }


# Cache key carries a SCHEMA version. The payload shape has changed twice while
# the key stayed fixed, and a stale entry missing a newly-added field is not a
# stale number — it is a different shape that downstream `.get()` calls paper
# over. Bump this whenever a field is added or its meaning changes.
@_result_cached("es_overnight_base_v5")
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


def _extension_now(base: dict, session_day: pd.Timestamp, last_ts: pd.Timestamp) -> dict | None:
    """How far a break starting now would typically run, from time remaining.

    The one target rule the data supports cleanly: extension per hour of session
    left is flat regardless of when the break happens, so the runway IS the
    forecast. Outside the cash session there is no runway to price, so this
    returns nothing rather than a number that reads as a target.
    """
    a = base.get("break_anatomy") or {}
    rate = a.get("extension_pts_per_hour_remaining")
    if not rate:
        return None
    open_t = session_day.replace(hour=_RTH_OPEN[0], minute=_RTH_OPEN[1])
    close_t = session_day.replace(hour=_RTH_CLOSE[0], minute=_RTH_CLOSE[1])
    if not (open_t <= last_ts < close_t):
        return None
    hrs = (close_t - last_ts).total_seconds() / 3600
    return {
        "minutes_left": int(hrs * 60),
        "typical_extension_pts": round(rate * hrs, 1),
        "rate_pts_per_hour": rate,
        "basis": "median; extension scales with time left, not with when it breaks",
    }


def _CONTRACT_TICKER() -> str | None:
    """Which ES contract the shared bar fetch resolved to, for labelling."""
    try:
        from src.es_levels import _CONTRACT
        return _CONTRACT.get("ticker")
    except Exception:
        return None


def overnight_read(base: dict | None = None, frames: dict | None = None) -> dict:
    """Today's overnight range, and what the base rates say to expect from it.

    `frames` is the cockpit's shared session split; pass it to avoid a second
    bar fetch. Degrades to the historical tables alone if the live session can't
    be read — a missing live read must not blank the study, which stands alone.
    """
    from src.es_levels import session_frames

    base = base or overnight_base_rates()
    if not base.get("available"):
        return base

    # Reuse the cockpit's session model rather than rebuilding one. `session_frames`
    # exists so levels, structure, expected move and this all see ONE split from
    # ONE bar fetch; a second hand-rolled model here would be free to drift from
    # the levels card sitting next to it, and would spend an API call doing it.
    if frames is None:
        frames = session_frames()
    if not frames:
        return {**base, "live": None}

    on, rth = frames["overnight"], frames["cur_rth"]
    session_day = frames["anchor"]
    bars = frames["bars"]
    ticker = _CONTRACT_TICKER()
    if on is None or len(on) < 20 or bars.empty:
        return {**base, "live": None}

    onh, onl = float(on["High"].max()), float(on["Low"].min())
    onr = onh - onl
    last = float(bars["Close"].iloc[-1])
    if onr <= 0:
        return {**base, "live": None}

    # The base rates are conditioned on where the session OPENS, so the live
    # read has to feed them the open — not the last price. Using `last` mid-
    # session silently asks a different question, and once price has left the
    # overnight range it is worse than that: it would report a 90.8% chance of
    # breaking a level that has already broken.
    if rth is None or rth.empty:
        phase, anchor, anchor_is_proxy = "premarket", last, True
    else:
        anchor = float(rth["Open"].iloc[0])
        anchor_is_proxy = False
        phase = {"rth": "rth", "premarket": "premarket"}.get(frames.get("mode"), "complete")

    pos = (anchor - onl) / onr
    band = _pos_band(pos)
    match = next((b for b in base["by_open_position"] if b["band"] == band), None)

    # What has already happened is a fact, not a forecast. Report it separately
    # so a resolved break is never dressed up as a probability.
    broke_high = bool(not rth.empty and float(rth["High"].max()) > onh)
    broke_low = bool(not rth.empty and float(rth["Low"].min()) < onl)

    expected = None
    if match:
        expected = {"n": match["n"]}
        # Omit the side that has already resolved rather than restating its
        # prior — the trader's question about that side is now "does it hold",
        # which is a different table.
        if not broke_high:
            expected["breaks_on_high_pct"] = match["breaks_on_high_pct"]
        if not broke_low:
            expected["breaks_on_low_pct"] = match["breaks_on_low_pct"]

    # Bucket edges must be PRESENT to match. The permissive version of this —
    # `.get(lo, -1) <= x <= .get(hi, 1e9)` — quietly matched every bucket when a
    # cached payload predated the edges, so it returned the first one and
    # reported a wide overnight as tight. A missing edge is a reason to say
    # nothing, not to widen the bucket until something fits.
    on_range_pct = onr / anchor * 100
    size = next((b for b in base.get("by_overnight_size", [])
                 if b.get("on_range_pct_lo") is not None
                 and b.get("on_range_pct_hi") is not None
                 and b["on_range_pct_lo"] <= on_range_pct <= b["on_range_pct_hi"]), None)

    return {
        **base,
        "live": {
            "contract": ticker,
            "session_date": str(session_day.date()),
            "phase": phase,
            "overnight_high": round(onh, 2),
            "overnight_low": round(onl, 2),
            "overnight_range": round(onr, 2),
            "overnight_range_pct": round(on_range_pct, 3),
            "last": round(last, 2),
            "open": None if anchor_is_proxy else round(anchor, 2),
            "open_is_estimated": anchor_is_proxy,
            "position_in_range_pct": round(float(pos * 100), 1),
            "band": band,
            "to_on_high": round(onh - last, 2),
            "to_on_low": round(last - onl, 2),
            "broke_on_high": broke_high,
            "broke_on_low": broke_low,
            "expected": expected,
            "extension_if_it_breaks_now": _extension_now(
                base, pd.Timestamp(session_day), last_ts=bars.index[-1]),
            "rth_range_expectation": ({
                "p25": size["rth_p25"], "median": size["rth_median"], "p75": size["rth_p75"],
                "n": size["n"],
            } if size else None),
        },
    }
