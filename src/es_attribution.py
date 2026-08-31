"""What actually moved the tape today, and what was happening when it did.

BUILT THE OTHER WAY ROUND FROM THE OBVIOUS DESIGN. The intuitive version starts
with the news feed and asks "what did this headline do to price", which requires
deciding in advance which stories matter and then hunting for their effect —
that is how a page ends up narrating a 6.5% crude break as having "no matching
macro headline" while the story sits two modules away, and equally how it ends up
crediting a headline for a move that was already underway.

This starts from the TAPE. Rank the moments price actually moved — five-minute
bars against a normal bar FOR THEIR OWN TIME OF DAY — and only then ask what was
happening at that time. The ranking is measured. The annotation is temporal
coincidence and is labelled as such, never as causation.

The time-of-day part was added 2026-08-30 and is not cosmetic. Ranking against
one median for the whole session compares a 09:30 bar, which runs 1.65x the
scale of a midday one, against the same yardstick as a 12:45 bar — so the module
reliably reported the open and went quiet through the middle of the day. See
`_PERIODICITY`. Note the ordering consequence: the first entry in `moves` is now
the most UNUSUAL expansion for its hour, which is not always the largest in
handles.

A LARGE MOVE WITH NO NEARBY CATALYST IS REPORTED AS EXACTLY THAT. It is one of
the more useful things on here: a session whose biggest expansion has nothing
attached to it is behaving differently from one whose expansions all land on
releases, and a page that only ever prints attributions cannot tell you which
kind of day you are in.

TWO CLASSES OF ATTRIBUTION, AND THEY ARE NOT EQUALLY GOOD
─────────────────────────────────────────────────────────
SCHEDULED RELEASES have an exact, known publication time. ISM at 10:00 is 10:00,
so a range expansion in the following minutes is a strong temporal link and the
window around it can be measured directly.

HEADLINES carry an RSS `pubDate`, which is when the WIRE published, not when the
event happened or when the market learned of it. It routinely lags by minutes and
sometimes by hours, and a story can be filed long after price has moved on it. So
headlines get a wider window, weaker language, and are never used to explain a
move that a scheduled release already covers.

A TIMEZONE TRAP THAT WOULD SILENTLY RUIN THIS. `macro_news` returns `published`
as a NAIVE timestamp in UTC, while the bars are tz-aware Eastern. Comparing them
without converting puts every headline four or five hours away from the move it
belongs to — near enough to look like a real window, far enough to attach the
wrong story to everything. Converted explicitly below rather than assumed.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"

# A bar this many times a normal bar FOR ITS TIME OF DAY is a move worth
# explaining. Tuned to surface a handful per session rather than a running
# commentary.
#
# WAS 2.5 AGAINST A FLAT SESSION MEDIAN. It is 2.20 now, and that is NOT a
# re-tune — it is the reparameterisation that holds the flag RATE fixed when the
# yardstick changes. Deseasonalising compresses the distribution (most of what
# the flat rule caught were opening bars, which are large for a mechanical
# reason), so keeping 2.5 would have cut total flags 48.7% and left a third of
# sessions silent. Solved on 495 sessions of the production 5-minute frame:
# 2.20 reproduces the old total to +1.8%, so the change is a pure
# REDISTRIBUTION of a fixed budget of attention:
#
#       first 30 min   443 -> 100 flags   (-77%)
#       11:30-14:00    185 -> 646 flags  (+249%)
#
# Those two figures independently reproduce the research run (-74% and +229%)
# on a different window and a different data source, which is the reason to
# believe them.
_MOVE_MULT = 2.20

# ---------------------------------------------------------------------------
# INTRADAY PERIODICITY — added 2026-08-30. Bars are compared against their own
# TIME OF DAY, not against a single whole-session constant.
# ---------------------------------------------------------------------------
#
# This module used to rank every bar against one median for the whole session.
# But the session's volatility runs on a fixed clock: measured over 6,027
# sessions of 5-minute SPY, the 09:30-09:35 bar is 1.65x the typical bar's SCALE
# (2.71x its variance) while 12:25-12:30 is 0.74x. Holding both to one threshold
# means a move at 10:15 and a move at 12:45 are being judged by standards that
# differ by more than a factor of two, and the module systematically
# over-attributes to the open and misses the midday.
#
# Boudt, Croux & Laurent (J. Empirical Finance 2011) is the reference: intraday
# periodicity causes spurious detection at periodically busy times and hides
# genuine moves in the quiet ones. They also show the classical estimator is
# biased by the very jumps it is meant to find, so this curve is estimated
# ROBUSTLY (weighted standard deviation on bipower-scaled returns). The
# difference is not cosmetic — the non-robust curve tracks each slot's jump rate
# at r = +0.900, and is 11.0% too high at 14:00-14:05, the FOMC slot.
#
# MEASURED CONSEQUENCE on 2,141 held-out sessions: 34.7% of all flags change.
# 74.1% of the moves this module currently flags in the opening half hour are
# ordinary for that time of day, and 408 midday moves it currently misses become
# visible (11:30-14:00 flags rise 229%).
#
# TWO PROPERTIES WORTH KNOWING:
#   - The factor is a RATIO of scales, so it is unit-free and transfers from the
#     SPY sample it was fitted on to the ES bars this module reads, the same
#     argument the range multiplier uses.
#   - It is DIVIDED OUT of the bars before the median is taken, rather than
#     multiplied into the threshold. That keeps the comparison valid mid-session:
#     a median over deseasonalised bars means the same thing at 10:00, when only
#     busy slots have printed, as it does at 15:55.
#
# The curve DRIFTS — the opening 5 minutes and closing 10 minutes have roughly
# doubled their share of session variance since 2002 while 14:00-15:30 halved.
# These are the 24-year values. Re-estimate rather than assume they are constants.
_PERIODICITY = (
    1.6474, 1.5056, 1.4522, 1.4191, 1.3760, 1.2192,   # 09:30
    1.5919, 1.3308, 1.2882, 1.2288, 1.1826, 1.1641,   # 10:00
    1.2282, 1.1308, 1.1425, 1.0719, 1.0655, 1.0173,   # 10:30
    1.0848, 1.0090, 0.9618, 0.9445, 0.9268, 0.9371,   # 11:00
    0.9698, 0.9081, 0.8715, 0.8451, 0.8317, 0.8335,   # 11:30
    0.8955, 0.8159, 0.7831, 0.8088, 0.8068, 0.7375,   # 12:00
    0.7893, 0.7613, 0.7476, 0.7583, 0.7580, 0.7392,   # 12:30
    0.8399, 0.7838, 0.7665, 0.7808, 0.7655, 0.7671,   # 13:00
    0.8020, 0.7889, 0.7949, 0.7779, 0.7806, 0.7746,   # 13:30
    0.9563, 0.8749, 0.8466, 0.8764, 0.8602, 0.8576,   # 14:00
    0.9165, 0.8815, 0.8845, 0.8775, 0.8725, 0.8787,   # 14:30
    1.0368, 0.9391, 0.9455, 0.9167, 0.9607, 0.9392,   # 15:00
    1.0775, 1.0169, 1.1040, 1.0998, 1.2299, 1.3175,   # 15:30
)


def _slot_factors(idx: pd.DatetimeIndex) -> np.ndarray:
    """The periodicity factor for each bar, by minutes since the 09:30 open.

    Anything outside RTH — or a bar the curve has no slot for — gets 1.0, which
    reduces to the old flat behaviour rather than dropping the bar. A half-day
    keeps its real clock position; the curve is indexed on time of day, not on
    position within the session, so a short session is not stretched to fit.
    """
    # The slot is a WALL-CLOCK position, so the index has to be in exchange time
    # before `.hour` is read. A UTC-indexed frame would land every bar four or
    # five slots away and be wrong in complete silence — the same timezone-join
    # failure that has produced all-NaN merges on this platform before.
    try:
        idx = idx.tz_convert(_TZ) if idx.tz is not None else idx.tz_localize(_TZ)
    except (TypeError, AttributeError):                      # pragma: no cover
        pass
    mins = (idx.hour * 60 + idx.minute) - (9 * 60 + 30)
    slot = np.floor_divide(np.asarray(mins, dtype=float), 5.0)
    out = np.ones(len(idx), dtype=float)
    ok = np.isfinite(slot) & (slot >= 0) & (slot < len(_PERIODICITY))
    if ok.any():
        out[ok] = np.asarray(_PERIODICITY, dtype=float)[slot[ok].astype(int)]
    return out


def _span_factor(idx: pd.DatetimeIndex) -> float:
    """The periodicity factor for a RANGE measured across several bars.

    Not the arithmetic mean of the bars' factors. A range over n steps scales
    with the square root of the summed variances, so the correct multi-bar
    factor is the ROOT MEAN SQUARE of the per-bar scale factors. RMS >= mean,
    and the gap is widest exactly where the factors move fastest — across the
    open, where 1.65 falls to 1.22 inside half an hour. For a single bar the two
    coincide, which is why this only shows up on merged runs and on the
    30-minute release windows.
    """
    f = _slot_factors(idx)
    if not len(f):
        return 1.0
    v = float(np.sqrt(np.mean(np.square(f))))
    return v if np.isfinite(v) and v > 0 else 1.0

# SCHEDULED RELEASES ARE MATCHED CAUSALLY, NOT BY PROXIMITY. A release at 10:00
# can only explain price action that starts at or after 10:00. The first cut
# windowed symmetrically around the whole move and duly credited ISM at 10:00
# with the 09:30-09:55 opening drive — a move that had finished five minutes
# before the number existed. Proximity in the clock is not the test; ORDER is.
#
# The small lead below is for clock skew and for a print landing a hair early,
# not for anticipation. Positioning ahead of a release is real but it is not
# something this module can distinguish from coincidence, so it is not claimed.
_EVENT_LEAD_MIN = 2
_EVENT_FOLLOW_MIN = 15

# The cash open is the largest scheduled event of the day and appears in no
# calendar. Without this the opening auction is either mis-attributed to
# whatever release happens to sit near it or reported as an unexplained
# expansion — and it is the single most predictable expansion there is.
_OPEN_WINDOW_MIN = 10
# Headlines: wide, because an RSS timestamp is when the wire filed, not when the
# market learned. Asymmetric — a story published BEFORE a move can explain it,
# one published well after is more likely reporting it.
_NEWS_BEFORE_MIN = 25
_NEWS_AFTER_MIN = 10
# Window measured after a scheduled release.
_IMPACT_WINDOW_MIN = 30


def _to_et(ts) -> pd.Timestamp | None:
    """Headline timestamps arrive naive-UTC; bars are tz-aware Eastern."""
    if not ts:
        return None
    try:
        t = pd.Timestamp(ts)
    except Exception:
        return None
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return t.tz_convert(_TZ)


def _moves(bars: pd.DataFrame, median_bar: float) -> list[dict]:
    """Contiguous runs of unusually large bars, merged into single moves.

    Merged because a fast repricing is several consecutive wide bars, and listing
    each one separately would report one event five times and bury everything
    else beneath it.

    "Unusually large" is judged AGAINST THE BAR'S OWN TIME OF DAY — see the
    `_PERIODICITY` block. `median_bar` is retained for the raw figures the card
    already shows, but selection and ranking both run on the deseasonalised
    scale, so a 12:45 move no longer has to clear an opening bar's height.
    """
    rng = (bars["High"] - bars["Low"]).astype(float)
    adj = rng / _slot_factors(bars.index)
    adj_median = float(np.nanmedian(adj.to_numpy()))
    flat = not np.isfinite(adj_median) or adj_median <= 0
    if flat:
        adj, adj_median = rng, median_bar        # degenerate: fall back to flat
    if not np.isfinite(adj_median) or adj_median <= 0:
        # No usable yardstick at all. Ranking against zero makes EVERY bar
        # "unusual" and returns the whole session as one move — a wrong answer
        # dressed as a confident one. Report nothing instead.
        return []

    big = bars[adj.to_numpy() >= _MOVE_MULT * adj_median]
    if big.empty:
        return []

    out: list[dict] = []
    run: list = []
    prev = None
    for ts in big.index:
        if prev is not None and (ts - prev) > pd.Timedelta(minutes=10):
            out.append(run)
            run = []
        run.append(ts)
        prev = ts
    if run:
        out.append(run)

    moves = []
    for r in out:
        seg = bars.loc[r[0]:r[-1]]
        seg_rng = float(seg["High"].max() - seg["Low"].min())
        # Score the run against what a normal bar IN ITS OWN SLOTS would deliver,
        # so a run at the open is not credited for the open's own scale. Same
        # convention as before — a multi-bar move measured in normal BARS — only
        # the yardstick is now time-of-day aware.
        # In the degenerate branch the yardstick is already flat, so the slot
        # factor must not be reapplied on top of it — otherwise the fallback
        # silently reports a number that is neither deseasonalised nor flat.
        expected = 1.0 if flat else _span_factor(seg.index)
        x_tod = (seg_rng / (adj_median * expected)
                 if adj_median > 0 and expected > 0 else None)
        moves.append({
            "start": r[0], "end": r[-1],
            "range": round(seg_rng, 2),
            "net": round(float(seg["Close"].iloc[-1] - seg["Open"].iloc[0]), 2),
            # x a normal bar FOR THIS TIME OF DAY. The flat figure is kept
            # alongside it because the card has quoted that number for months
            # and a silently redefined field is worse than two labelled ones.
            "x_normal_bar": round(x_tod, 1) if x_tod else None,
            "x_normal_bar_flat": round(seg_rng / median_bar, 1) if median_bar else None,
            "tod_factor": round(expected, 2),
            "bars": len(r),
        })
    return sorted(moves, key=lambda m: -(m["x_normal_bar"] or 0))


def price_attribution(frames: dict | None = None,
                      schedule: list[dict] | None = None,
                      news: list[dict] | None = None,
                      now: pd.Timestamp | None = None) -> dict:
    """The session's largest moves, with whatever was happening at the time."""
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)

    if frames is None:
        from src.es_levels import session_frames
        frames = session_frames(now=now)
    if not frames:
        return {"available": False, "reason": "no intraday ES data"}

    cur = frames.get("cur_rth")
    if cur is None or cur.empty or len(cur) < 6:
        return {"available": False,
                "reason": "the cash session has not built enough bars to rank"}

    bar_rng = (cur["High"] - cur["Low"]).astype(float)
    median_bar = float(bar_rng.median())
    if median_bar <= 0:
        return {"available": False, "reason": "degenerate bar ranges"}

    if schedule is None:
        try:
            from src.es_session import todays_schedule
            schedule = todays_schedule(now=now) or []
        except Exception as e:
            logger.warning(f"attribution schedule failed: {e}")
            schedule = []
    if news is None:
        try:
            from src.es_session import macro_news
            news = macro_news() or []
        except Exception as e:
            logger.warning(f"attribution news failed: {e}")
            news = []

    ev = [{"name": e.get("name"), "when": _to_et(e.get("when")),
           "impact": e.get("impact")} for e in schedule if e.get("when")]
    ev = [e for e in ev if e["when"] is not None]
    hd = [{"title": h.get("title"), "source": h.get("source"),
           "when": _to_et(h.get("published"))} for h in news if h.get("published")]
    hd = [h for h in hd if h["when"] is not None]

    session_open = cur.index.min()
    moves = _moves(cur, median_bar)
    for m in moves:
        # The move must START at or after the release, within the follow window.
        # Matching on the move's END, or windowing symmetrically, lets an event
        # explain price action that preceded it.
        lo = m["start"] - pd.Timedelta(minutes=_EVENT_LEAD_MIN)
        hi = m["start"] + pd.Timedelta(minutes=_EVENT_FOLLOW_MIN)
        hits = [e for e in ev if lo <= e["when"] <= hi]
        hits.sort(key=lambda e: abs((e["when"] - m["start"]).total_seconds()))
        m["event"] = ({"name": hits[0]["name"], "impact": hits[0]["impact"],
                       "at": hits[0]["when"].strftime("%H:%M")} if hits else None)

        # The opening auction, which no calendar carries.
        m["is_open"] = bool(
            m["start"] <= session_open + pd.Timedelta(minutes=_OPEN_WINDOW_MIN))
        if m["is_open"] and not m["event"]:
            m["event"] = {"name": "Cash open", "impact": "structural",
                          "at": session_open.strftime("%H:%M")}

        # Headlines are only consulted when nothing structural covers the move.
        # A known release time beats an RSS timestamp every time, and offering
        # both invites reading the weaker one as corroboration.
        if m["event"]:
            m["headlines"] = []
        else:
            nlo = m["start"] - pd.Timedelta(minutes=_NEWS_BEFORE_MIN)
            nhi = m["end"] + pd.Timedelta(minutes=_NEWS_AFTER_MIN)
            near = [h for h in hd if nlo <= h["when"] <= nhi]
            near.sort(key=lambda h: abs((h["when"] - m["start"]).total_seconds()))
            m["headlines"] = [{"title": h["title"], "source": h["source"],
                               "at": h["when"].strftime("%H:%M")} for h in near[:2]]

        m["attributed"] = bool(m["event"] or m["headlines"])
        m["start"] = m["start"].strftime("%H:%M")
        m["end"] = m["end"].strftime("%H:%M")

    # Measured impact of each release that has already printed, against the
    # session's own median 30-minute window — now TIME-OF-DAY ADJUSTED, which it
    # was not until 2026-08-30.
    #
    # This half of the module used to carry the defect its own caveat described:
    # "the opening hour is naturally the widest part of the session, so a 10:00
    # release will flatter itself." That is not a small effect here, because
    # releases are not spread evenly across the clock — they cluster at 08:30,
    # 10:00 and 14:00, and 10:00-10:30 is the second-richest slot of the session
    # at 1.57x pooled variance. A 10:00 print was being credited for range that
    # is simply what 10:00 does.
    #
    # Both the yardstick and the measured window are divided by their own span
    # factor, so the comparison is like-for-like at any hour and does not drift
    # as the session fills with slots of differing scale.
    win = pd.Timedelta(minutes=_IMPACT_WINDOW_MIN)
    thirty = cur["High"].rolling(6).max() - cur["Low"].rolling(6).min()
    median_30 = float(thirty.median()) if thirty.notna().any() else None
    # RMS factor of the trailing six bars, matched to `thirty`'s own alignment
    # (a rolling window labelled at its LAST bar covers the five before it).
    _f2 = pd.Series(_slot_factors(cur.index) ** 2, index=cur.index)
    _rms30 = np.sqrt(_f2.rolling(6).mean())
    thirty_adj = thirty / _rms30.replace(0.0, np.nan)
    median_30_adj = (float(thirty_adj.median())
                     if thirty_adj.notna().any() else None)
    impacts = []
    for e in ev:
        if e["when"] > now:
            continue
        # A release BEFORE the cash open has no comparable window: slicing from
        # 09:15 yields 09:30-09:45, fifteen minutes measured against a
        # thirty-minute median, which understates it by construction and reads
        # as the release having done nothing. The 08:30 prints are the common
        # case and they are exactly the ones worth not lying about.
        if e["when"] < session_open:
            continue
        seg = cur.loc[e["when"]:e["when"] + win]
        # Equally, a release inside the last half hour has a partial window.
        if len(seg) < 4:
            continue
        r = float(seg["High"].max() - seg["Low"].min())
        sf = _span_factor(seg.index)
        impacts.append({
            "name": e["name"], "at": e["when"].strftime("%H:%M"),
            "impact": e["impact"],
            "range": round(r, 2),
            "net": round(float(seg["Close"].iloc[-1] - seg["Open"].iloc[0]), 2),
            # x a normal 30-minute window AT THIS HOUR. The flat figure is kept
            # beside it because the card has quoted that number for months and a
            # silently redefined field is worse than two labelled ones.
            "x_normal_window": (round((r / sf) / median_30_adj, 2)
                                if median_30_adj and sf > 0 else None),
            "x_normal_window_flat": round(r / median_30, 2) if median_30 else None,
            "tod_factor": round(sf, 2),
        })

    unattributed = [m for m in moves if not m["attributed"]]
    biggest = moves[0] if moves else None

    return {
        "available": True,
        "moves": moves[:6],
        "n_moves": len(moves),
        "n_unattributed": len(unattributed),
        "event_impacts": impacts,
        "median_bar": round(median_bar, 2),
        "median_30min": round(median_30, 2) if median_30 else None,
        "threshold_x": _MOVE_MULT,
        "threshold_basis": "normal bar for that time of day",
        # `moves` is now ordered by how unusual a run was FOR ITS SLOT, not by
        # raw handles, so `moves[0]` is no longer necessarily the biggest move
        # of the day and must not be described as one. This sentence feeds the
        # page interpreter; a provenance claim that does not match the sort is
        # exactly the kind of stripped qualifier that has manufactured auditor
        # contradictions before.
        "headline": (
            None if not biggest or biggest.get("x_normal_bar") is None else
            (f"The session's most unusual expansion for its time of day ran "
             f"{biggest['range']:.0f} handles "
             f"({biggest['x_normal_bar']:.1f}x a normal bar at that hour) at {biggest['start']}"
             + (f", on {biggest['event']['name']}." if biggest.get("event")
                else (f", alongside: {biggest['headlines'][0]['title'][:90]}."
                      if biggest.get("headlines")
                      else " with nothing in either feed to attach it to.")))
        ),
        "caveat": (
            "Moves are ranked by MEASURED range against a normal bar for their own "
            "TIME OF DAY, not against one median for the whole session — the "
            "09:30 bar runs 1.65x the scale of a midday one, so a flat yardstick "
            "over-reported the open and hid the middle of the day. "
            "What sits beside each one is what was published at that time — "
            "coincidence in the clock, not demonstrated causation. Scheduled "
            "releases have exact known times and are the stronger link; headlines "
            "carry a wire timestamp that lags the event and sometimes lags the "
            "price, so they are only consulted where no release covers the move. "
            "The post-release impact windows are deseasonalised on the same "
            "curve, which matters more there than anywhere: releases cluster at "
            "08:30, 10:00 and 14:00, and 10:00-10:30 is the second-richest slot "
            "of the session, so a 10:00 print used to be credited for range that "
            "is simply what 10:00 does."
        ),
        "unattributed_note": (
            None if not unattributed else
            f"{len(unattributed)} of {len(moves)} moves had nothing in either feed "
            f"within the window. A session whose expansions carry no catalyst is a "
            f"different kind of day from one where they all land on releases."
        ),
    }
