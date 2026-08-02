"""FOMC outcome probabilities reconstructed from 30-Day Fed Funds (ZQ) futures.

This is the CME FedWatch construction, not a licensed feed. It is regime
context: what the rates market has PRICED for the next few meetings, and how
that has moved. It is not a forecast and carries no session-horizon content.

THE CONTRACT. ZQ settles at 100 minus the ARITHMETIC AVERAGE OF DAILY EFFR OVER
EVERY CALENDAR DAY of the delivery month; weekends and holidays carry the prior
business day's rate. Two consequences are routinely missed:

  1. It prices a MONTHLY AVERAGE, not a point-in-time rate, so a mid-month
     meeting is only partially reflected in its own month's contract.
  2. It settles on EFFR, not on the target range. EFFR prints inside a range
     whose upper bound is higher, and mixing a target-range level into a formula
     whose other terms are EFFR is a silent 8-12bp error.

TWO ERRORS THAT BREAK RECONSTRUCTIONS. Both were measured on 1,248 observations
across 18 meetings (research 2026-08-02) before this module was written.

  ERROR 1 — anchoring every meeting on spot EFFR instead of CHAINING. The rate
  prevailing before meeting i is the rate solved AFTER meeting i-1. Anchoring
  October 2026 on spot EFFR rather than on September's solved rate reported
  +180.83bp; chained correctly it prices +3.69bp. The error attributes all of
  the previously-priced tightening to whichever meeting you are looking at.

  ERROR 2 — using the within-month solve for a LATE-MONTH meeting. `n_post`
  sits in the denominator, so the estimator multiplies settlement noise by
  N/n_post. Measured leverage ran 2.1x for a mid-month meeting and 30-31x for
  meetings on the 29th-30th. At 30x, ONE ZQ TICK (0.5bp) moves the answer by
  15bp — sixty probability points.

  The fix, which is what CME does: when the FOLLOWING month contains no FOMC
  meeting, that contract prices a whole month at the post-meeting rate, so
  r_post = 100 - P(m+1) exactly, with no day-weighting and no leverage. Every
  high-leverage meeting is followed by a meeting-free month, so all of them are
  recoverable this way. Correcting it took the spread against Polymarket from
  SD 48.95bp to SD 4.89bp — about 90% of an apparent cross-venue disagreement
  was this estimator's own noise.

`method` and `leverage` ship in the payload for every meeting so a reader can
see which estimator produced a number rather than having to trust it.
"""

from __future__ import annotations

import calendar
import logging
import math
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_MONTH_CODE = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
               7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}

# FOMC decision dates — day 2 of each meeting, when the statement lands at 14:00
# ET and the new target is effective the FOLLOWING business day. Encoded rather
# than scraped, on the same reasoning as the release times in es_session: the
# Fed publishes these years ahead and they do not move.
#
# THIS LIST GOES STALE. `calendar_exhausted` in the payload says so explicitly
# rather than letting the board quietly shorten as dates fall off the back.
FOMC_DATES: list[date] = [
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
]

_STEP_BP = 25.0
_DEFAULT_MEETINGS = 4


def zq_ticker(year: int, month: int) -> str:
    """ZQ code for a delivery month. Single-digit year — ZQU6 is Sep 2026."""
    return f"ZQ{_MONTH_CODE[month]}{year % 10}"


def _next_month(y: int, m: int) -> tuple[int, int]:
    return (y, m + 1) if m < 12 else (y + 1, 1)


def _month_is_known(y: int, m: int) -> bool:
    """Is this month inside the horizon FOMC_DATES actually covers?

    Past the last encoded meeting we do not know whether a month holds one, and
    `_has_meeting` would answer False for every month forever. That silently
    licenses the next-month estimator on a false premise: with the list ending
    2026-12-09, January 2027 looked meeting-free and the December meeting was
    priced off ZQF7 as though that were established. It is not — the Fed meets
    about eight times a year and late January is a usual slot.
    """
    last = FOMC_DATES[-1]
    return (y, m) <= (last.year, last.month)


def _has_meeting(y: int, m: int) -> bool:
    """True only when a meeting is KNOWN to fall in this month. Callers that
    care about the difference between "no meeting" and "we don't know" must
    check `_month_is_known` as well."""
    return any(d.year == y and d.month == m for d in FOMC_DATES)


def month_weights(meeting: date) -> tuple[int, int, int]:
    """(N, n_pre, n_post) for the meeting's own calendar month.

    A rate decided on day k takes effect on day k+1, so days 1..k inclusive
    carry the OLD rate — n_pre is the day of the month itself, not day-1.
    """
    n = calendar.monthrange(meeting.year, meeting.month)[1]
    n_pre = meeting.day
    return n, n_pre, n - n_pre


def implied_post_rate(settle: float, r_pre: float, meeting: date) -> float | None:
    """Post-meeting EFFR implied by the MEETING-MONTH contract.

        F      = 100 - settle                       (average EFFR that month)
        F      = (n_pre/N)*r_pre + (n_post/N)*r_post
        r_post = (N*F - n_pre*r_pre) / n_post

    None when the meeting falls on the last day of the month: the contract then
    carries no post-meeting days and cannot say anything about the new rate.
    """
    n, n_pre, n_post = month_weights(meeting)
    if n_post <= 0:
        return None
    return (n * (100.0 - settle) - n_pre * r_pre) / n_post


def outcome_probabilities(delta_bp: float, step_bp: float = _STEP_BP) -> dict[int, float]:
    """Split an implied rate CHANGE across adjacent 25bp outcomes.

    CME assumes moves are whole multiples of 25bp, so a delta between two
    buckets is apportioned to reproduce its expected value. This is an
    interpolation, not a distribution: it cannot express "50bp or nothing".
    """
    lower = math.floor(delta_bp / step_bp) * step_bp
    w_up = min(max((delta_bp - lower) / step_bp, 0.0), 1.0)
    out: dict[int, float] = {}
    if w_up > 0:
        out[int(lower + step_bp)] = w_up
    if w_up < 1:
        out[int(lower)] = 1.0 - w_up
    return dict(sorted(out.items()))


def _fetch_settles(months: list[tuple[int, int]]) -> dict[str, float]:
    """Latest settlement price per ZQ contract month.

    Uses the futures aggregates directly and never touches /contracts, which
    silently returns a stale reference slice unless a `date` parameter is
    passed. Tickers here are constructed from the calendar, so that call is
    not needed at all.
    """
    from src.futures_data import _get

    out: dict[str, float] = {}
    for y, m in months:
        tk = zq_ticker(y, m)
        j = _get(f"/futures/v1/aggs/{tk}", resolution="1day", limit=10)
        rows = (j or {}).get("results") or []
        if not rows:
            continue
        # `order` is IGNORED by this endpoint — asc and desc return identical
        # newest-first data — so the caller sorts rather than trusting it.
        rows.sort(key=lambda r: r.get("session_end_date") or "")
        last = rows[-1]
        px = last.get("settlement_price")
        if px is None:
            px = last.get("close")
        if px is not None:
            out[tk] = float(px)
    return out


def _spot_effr() -> float | None:
    try:
        from src.data_engine import _fred_history
        df = _fred_history("EFFR", days=60)
        if df is None or df.empty:
            return None
        return float(df["Close"].dropna().iloc[-1])
    except Exception as e:
        logger.warning(f"EFFR fetch failed: {e}")
        return None


def _prev_month(y: int, m: int) -> tuple[int, int]:
    return (y, m - 1) if m > 1 else (y - 1, 12)


def _anchor(first_meeting: date, settles: dict[str, float],
            spot: float | None) -> tuple[float | None, str]:
    """The rate prevailing BEFORE the first upcoming meeting.

    Walks BACKWARD from that meeting, not forward from today. Walking forward
    finds the next meeting-free month, which sits AFTER the meetings being
    priced and therefore already contains their outcomes — anchoring on it made
    September read -35.36bp and October +35.36bp, a perfectly offsetting pair
    that is the signature of this mistake.

    A meeting-free month is a clean read because the rate is CONSTANT across it,
    so 100 - settle is the prevailing rate no matter how much of the month has
    already elapsed. That is why using the current month here is fine, while
    using it as a forward estimate would not be.
    """
    py, pm = _prev_month(first_meeting.year, first_meeting.month)
    tk = zq_ticker(py, pm)
    if not _has_meeting(py, pm) and tk in settles:
        return 100.0 - settles[tk], f"{tk} (meeting-free month before the decision)"
    # The prior month held a meeting, so there is no constant-rate contract to
    # read. That meeting is already past, so its outcome is in realised EFFR.
    return spot, "spot EFFR"


def fed_probabilities(asof: date | None = None, n_meetings: int = _DEFAULT_MEETINGS) -> dict:
    """What ZQ prices for the next `n_meetings` FOMC decisions."""
    asof = asof or date.today()
    upcoming = [d for d in FOMC_DATES if d > asof][:n_meetings]
    if not upcoming:
        return {"available": False,
                "reason": "FOMC calendar exhausted — FOMC_DATES needs extending",
                "calendar_exhausted": True,
                "calendar_ends": FOMC_DATES[-1].isoformat()}

    # Every meeting month, plus each following month for the next-month
    # estimator, plus a couple ahead for the anchor.
    months: list[tuple[int, int]] = []
    for d in upcoming:
        months.append((d.year, d.month))
        months.append(_next_month(d.year, d.month))
    y, m = _next_month(asof.year, asof.month)
    for _ in range(3):
        months.append((y, m))
        y, m = _next_month(y, m)
    months = sorted(set(months))

    # The anchor reads the month BEFORE the first meeting, which may be the
    # current month and is not otherwise in the list.
    months.append(_prev_month(upcoming[0].year, upcoming[0].month))
    months = sorted(set(months))

    settles = _fetch_settles(months)
    if not settles:
        return {"available": False, "reason": "no ZQ settlements available"}

    spot = _spot_effr()
    r_pre, anchor_label = _anchor(upcoming[0], settles, spot)
    if r_pre is None:
        return {"available": False, "reason": "no anchor rate (ZQ and EFFR both unavailable)"}

    anchor_rate = r_pre
    rows: list[dict] = []
    for i, mt in enumerate(upcoming):
        tk = zq_ticker(mt.year, mt.month)
        settle = settles.get(tk)
        if settle is None:
            rows.append({"date": mt.isoformat(), "ticker": tk,
                         "error": f"no settlement for {tk}"})
            continue

        n, n_pre, n_post = month_weights(mt)
        nm_y, nm_m = _next_month(mt.year, mt.month)
        tk_next = zq_ticker(nm_y, nm_m)

        # PREFERRED: a meeting-free following month prices a whole month at the
        # post-meeting rate, so no day-weighting and no leverage. See the module
        # docstring for what the within-month solve does to a late-month meeting.
        r_post = None
        method = None
        if (_month_is_known(nm_y, nm_m) and not _has_meeting(nm_y, nm_m)
                and tk_next in settles):
            r_post, method = 100.0 - settles[tk_next], "next-month"
        if r_post is None:
            r_post = implied_post_rate(settle, r_pre, mt)
            method = "within-month"
        if r_post is None:
            rows.append({"date": mt.isoformat(), "ticker": tk,
                         "error": "meeting on the last day of its month; "
                                  "no post-meeting days in the contract"})
            continue

        delta_bp = (r_post - r_pre) * 100.0
        leverage = 1.0 if method == "next-month" else (n / n_post if n_post else None)
        probs = outcome_probabilities(delta_bp)
        rows.append({
            "date": mt.isoformat(),
            "days_away": (mt - asof).days,
            "ticker": tk,
            "settle": round(settle, 4),
            "implied_month_avg": round(100.0 - settle, 4),
            "r_pre": round(r_pre, 4),
            "r_post": round(r_post, 4),
            "delta_bp": round(delta_bp, 2),
            "anchor": anchor_label if i == 0 else "chained from the prior meeting",
            "method": method,
            # How many bp the answer moves per 1bp of settlement error. 1.0 on
            # the next-month route; the within-month route is published so a
            # double-digit reading is visible rather than buried.
            "leverage": round(leverage, 2) if leverage is not None else None,
            "n_days": n, "n_pre": n_pre, "n_post": n_post,
            "probabilities": {f"{k:+d}bp": round(v, 4) for k, v in probs.items()},
            "p_hike": round(sum(v for k, v in probs.items() if k > 0), 4),
            "p_cut": round(sum(v for k, v in probs.items() if k < 0), 4),
            "p_hold": round(sum(v for k, v in probs.items() if k == 0), 4),
        })
        r_pre = r_post          # <- the chain

    priced = [r for r in rows if "delta_bp" in r]
    return {
        "available": bool(priced),
        "asof": asof.isoformat(),
        "source": "CME 30-Day Fed Funds (ZQ) settlements",
        "reconstruction": True,
        "spot_effr": round(spot, 4) if spot is not None else None,
        "anchor_rate": round(anchor_rate, 4),
        "anchor": anchor_label,
        # Cumulative pricing across the whole strip shown, which is the regime
        # read: not "what happens in September" but where the market thinks the
        # rate lands by the last meeting on the board.
        "cumulative_bp": round(priced[-1]["r_post"] * 100 - anchor_rate * 100, 2) if priced else None,
        "meetings": rows,
        # The list is hardcoded; say when it is about to run out rather than
        # letting the board silently shorten.
        "calendar_ends": FOMC_DATES[-1].isoformat(),
        "calendar_exhausted": len(upcoming) < n_meetings,
    }
