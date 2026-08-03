"""What actually moved the tape today, and what was happening when it did.

BUILT THE OTHER WAY ROUND FROM THE OBVIOUS DESIGN. The intuitive version starts
with the news feed and asks "what did this headline do to price", which requires
deciding in advance which stories matter and then hunting for their effect —
that is how a page ends up narrating a 6.5% crude break as having "no matching
macro headline" while the story sits two modules away, and equally how it ends up
crediting a headline for a move that was already underway.

This starts from the TAPE. Rank the moments price actually moved — five-minute
bars against the session's own median bar — and only then ask what was happening
at that time. The ranking is measured. The annotation is temporal coincidence and
is labelled as such, never as causation.

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

import pandas as pd

logger = logging.getLogger(__name__)

_TZ = "America/New_York"

# A bar this many times the session's own median bar is a move worth explaining.
# Tuned to surface a handful per session rather than a running commentary: at 2.5
# a typical day produces roughly three to six.
_MOVE_MULT = 2.5

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
    """
    big = bars[(bars["High"] - bars["Low"]) >= _MOVE_MULT * median_bar]
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
        rng = float(seg["High"].max() - seg["Low"].min())
        moves.append({
            "start": r[0], "end": r[-1],
            "range": round(rng, 2),
            "net": round(float(seg["Close"].iloc[-1] - seg["Open"].iloc[0]), 2),
            "x_normal_bar": round(rng / median_bar, 1) if median_bar else None,
            "bars": len(r),
        })
    return sorted(moves, key=lambda m: -m["range"])


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

    # Measured impact of each release that has already printed. Compared with the
    # session's own median 30-minute window, which is a like-for-like unit but
    # NOT time-of-day adjusted — the opening hour is naturally the widest part of
    # the session, so a 10:00 release will flatter itself. Said on the card.
    win = pd.Timedelta(minutes=_IMPACT_WINDOW_MIN)
    thirty = cur["High"].rolling(6).max() - cur["Low"].rolling(6).min()
    median_30 = float(thirty.median()) if thirty.notna().any() else None
    impacts = []
    for e in ev:
        if e["when"] > now:
            continue
        seg = cur.loc[e["when"]:e["when"] + win]
        if seg.empty:
            continue
        r = float(seg["High"].max() - seg["Low"].min())
        impacts.append({
            "name": e["name"], "at": e["when"].strftime("%H:%M"),
            "impact": e["impact"],
            "range": round(r, 2),
            "net": round(float(seg["Close"].iloc[-1] - seg["Open"].iloc[0]), 2),
            "x_normal_window": round(r / median_30, 2) if median_30 else None,
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
        "headline": (
            None if not biggest else
            (f"The session's largest expansion ran {biggest['range']:.0f} handles "
             f"({biggest['x_normal_bar']:.1f}x a normal bar) at {biggest['start']}"
             + (f", on {biggest['event']['name']}." if biggest.get("event")
                else (f", alongside: {biggest['headlines'][0]['title'][:90]}."
                      if biggest.get("headlines")
                      else " with nothing in either feed to attach it to.")))
        ),
        "caveat": (
            "Moves are ranked by MEASURED range against this session's own median "
            "bar. What sits beside each one is what was published at that time — "
            "coincidence in the clock, not demonstrated causation. Scheduled "
            "releases have exact known times and are the stronger link; headlines "
            "carry a wire timestamp that lags the event and sometimes lags the "
            "price, so they are only consulted where no release covers the move. "
            "Post-release windows are not adjusted for time of day, and the "
            "opening hour is naturally the widest part of the session."
        ),
        "unattributed_note": (
            None if not unattributed else
            f"{len(unattributed)} of {len(moves)} moves had nothing in either feed "
            f"within the window. A session whose expansions carry no catalyst is a "
            f"different kind of day from one where they all land on releases."
        ),
    }
