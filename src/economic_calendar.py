"""Centralized economic event calendar — single source of truth.

Every page that needs "what macro is scheduled" imports from here rather than
keeping its own list.

WHERE THE DATES COME FROM
-------------------------
Three tiers, in descending order of trust, and each event says which tier it
came from so the UI can be honest about it:

1. ``source="fred"`` — the release calendar published by the issuing agency,
   read through FRED's ``/fred/releases/dates`` endpoint. BLS, BEA and Census
   publish their release dates a year ahead and FRED mirrors them, so CPI on
   the 12th vs the 13th is a fact here, not a guess. One HTTP call covers every
   release; the response is cached for six hours.

2. ``source="fomc"`` — the Fed's own published meeting calendar, hardcoded
   below. Not on FRED as a release. Update annually (see FOMC_DATES).

3. ``source="rule"`` — derived from a scheduling rule, flagged ``derived=True``.
   Used only where the rule is genuinely stable (ISM on the first/third
   business day, EIA petroleum on Wednesday) and the publisher's calendar is
   not machine-readable for free. ISM and U-Mich were pulled from FRED over
   licensing, which is why they are here rather than in tier 1.

Release TIMES are constants, never fetched. BLS publishes at 08:30 ET, ISM at
10:00, the FOMC statement at 14:00 — these have not moved in decades, and
scraping them would add a fragile dependency for no accuracy gain.

SIGN CONVENTION: ``days_away`` is positive for the future, negative for the
past, everywhere. (It used to be inverted for the NFP branch, which made
"upcoming events" filters silently drop the jobs report.)

TWO DIFFERENT QUESTIONS, TWO DIFFERENT FIELDS
---------------------------------------------
``impact`` (high/medium/low) is assigned by judgement and answers a TIMING
question: is this a scheduled discontinuity you need to be at the screen for.
A 14:00 FOMC statement is high on that axis whatever the tape then does, which
is why the ES card's scheduled-risk block and ``high_impact_today`` key off it.

``measured`` is attached from ``src.event_impact`` and answers a SIZING
question: how much wider than a normal session has this release actually made
the tape, over 3,677 sessions. The two disagree sharply — CPI is ``high`` on
timing but 1.06x on sizing and ranked 12th of 23, while only Nonfarm payrolls
survives the multiple-comparison correction, at 1.39x.

Do not collapse them into one field. The home calendar previously read the
timing label as though it were the sizing answer, which is how CPI, PCE and NFP
came to be rendered identically.

Last reviewed: 2026-08-29
Sources:
- FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
- Releases: https://fred.stlouisfed.org/docs/api/fred/releases_dates.html
- Measured multipliers: research/market_movers/ (see src/event_impact.py)
"""

from __future__ import annotations

import logging
from datetime import date as _date, datetime, timedelta

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# FOMC MEETING DATES (decision day, typically Wednesday)
# ═══════════════════════════════════════════════
# Update annually from https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm

FOMC_DATES = [
    # 2026
    "2026-01-29", "2026-03-19", "2026-05-06", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-11-04", "2026-12-16",
    # 2027
    "2027-01-27", "2027-03-17", "2027-05-05", "2027-06-16",
    "2027-07-28", "2027-09-22", "2027-11-03", "2027-12-15",
]

# Meetings with Summary of Economic Projections (dot plot)
FOMC_SEP_DATES = [
    "2026-03-19", "2026-06-17", "2026-09-16", "2026-12-16",
    "2027-03-17", "2027-06-16", "2027-09-22", "2027-12-15",
]


# ═══════════════════════════════════════════════
# FRED-scheduled releases
# ═══════════════════════════════════════════════
# release_id -> (display name, ET hour, ET minute, impact, trader note)
#
# `impact` ranks typical ES range expansion on the print, not economic
# importance — GDP matters more to an economist than it does to the tape.
_FRED_RELEASES: dict[int, tuple[str, int, int, str, str]] = {
    10:  ("CPI", 8, 30, "high",
          "The most-watched inflation print, and the clearest gap between billing and "
          "measurement on this calendar. The violence is in the first half hour and often "
          "retraces; the `measured` block carries the whole-session figure."),
    50:  ("Nonfarm payrolls", 8, 30, "high",
          "Revisions matter as much as the headline. The one release on this calendar whose "
          "range expansion survives correction across the 23 events tested."),
    54:  ("PCE / personal income", 8, 30, "high",
          "The Fed's preferred inflation gauge. The intuition that it moves less than CPI "
          "because CPI already telegraphed it is not what the tape did: measured, PCE ranks "
          "ahead of CPI. Neither survives correction."),
    46:  ("PPI", 8, 30, "medium",
          "Feeds the PCE nowcast more than it moves ES directly."),
    53:  ("GDP", 8, 30, "medium",
          "Backward-looking. The advance estimate moves more than the revisions."),
    180: ("Initial jobless claims", 8, 30, "medium",
          "Weekly, every Thursday. Background noise unless the trend breaks."),
    9:   ("Retail sales", 8, 30, "medium",
          "Consumer demand; hits discretionary names hardest."),
    192: ("JOLTS job openings", 10, 0, "low",
          "Stale by the time it prints, but the Fed cites it on labour slack."),
    13:  ("Industrial production", 9, 15, "low",
          "09:15 — the one release that lands between the bell and the 09:30 open."),
    27:  ("Housing starts", 8, 30, "low",
          "Rate-sensitive complex; reaches ES mostly through homebuilders."),
    51:  ("Trade balance", 8, 30, "low",
          "Matters for the GDP nowcast and on tariff headlines."),
    321: ("Empire State manufacturing", 8, 30, "low",
          "First regional survey of the month — an early read on ISM."),
    351: ("Philly Fed manufacturing", 8, 30, "low",
          "Second regional survey; watch prices-paid for the inflation read."),
}


# ═══════════════════════════════════════════════
# FRED fetch
# ═══════════════════════════════════════════════

_CACHE: dict = {}
_TTL_S = 6 * 3600
_FAIL_TTL_S = 300          # don't hammer FRED while it's down
_BACK_DAYS = 15
_FWD_DAYS = 120


def _fred_window() -> list[dict]:
    """Every tracked FRED release date in a wide window around today.

    Fetched in ONE request covering all releases and filtered locally — the
    per-release endpoint would mean a dozen round-trips on a cold cache, inside
    request handlers that are already doing real work.
    """
    from time import time as _now

    hit = _CACHE.get("fred")
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]
    if _CACHE.get("fred_fail") and (_now() - _CACHE["fred_fail"]) < _FAIL_TTL_S:
        # Serve stale rather than nothing — a six-hour-old CPI date is still
        # the right CPI date.
        return hit[1] if hit else []

    try:
        import requests
        from src.api_keys import get_secret

        key = get_secret("FRED_API_KEY")
        if not key:
            _CACHE["fred_fail"] = _now()
            return hit[1] if hit else []

        today = _date.today()
        r = requests.get(
            "https://api.stlouisfed.org/fred/releases/dates",
            params={
                "api_key": key,
                "file_type": "json",
                "realtime_start": (today - timedelta(days=_BACK_DAYS)).isoformat(),
                "realtime_end": (today + timedelta(days=_FWD_DAYS)).isoformat(),
                # Future dates have no data attached yet; without this they are
                # omitted and the calendar only ever knows about the past.
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
                "limit": 1000,
            },
            timeout=15,
        )
        r.raise_for_status()

        out: list[dict] = []
        for row in r.json().get("release_dates", []):
            meta = _FRED_RELEASES.get(row.get("release_id"))
            if not meta:
                continue
            name, hh, mm, impact, note = meta
            out.append({
                "name": name, "date": row["date"], "time_et": f"{hh:02d}:{mm:02d}",
                "hour": hh, "minute": mm, "impact": impact, "note": note,
                "source": "fred", "derived": False,
            })

        _CACHE["fred"] = (_now(), out)
        _CACHE.pop("fred_fail", None)
        return out

    except Exception as e:
        logger.warning(f"FRED release calendar fetch failed: {e}")
        _CACHE["fred_fail"] = _now()
        return hit[1] if hit else []


# ═══════════════════════════════════════════════
# Rule-derived events
# ═══════════════════════════════════════════════

def _holidays(start: _date, end: _date) -> set[_date]:
    """US federal holidays — what ISM and EIA shift their schedules around."""
    try:
        import pandas as pd
        from pandas.tseries.holiday import USFederalHolidayCalendar
        cal = USFederalHolidayCalendar()
        return {d.date() for d in cal.holidays(start=pd.Timestamp(start), end=pd.Timestamp(end))}
    except Exception:
        return set()


def _nth_business_day(year: int, month: int, n: int, hols: set[_date]) -> _date | None:
    """Nth business day of a month, skipping weekends and federal holidays."""
    d = _date(year, month, 1)
    count = 0
    while d.month == month:
        if d.weekday() < 5 and d not in hols:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
    return None


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> _date | None:
    """Nth given weekday of a month (weekday: Mon=0). n=-1 for the last one."""
    days = []
    d = _date(year, month, 1)
    while d.month == month:
        if d.weekday() == weekday:
            days.append(d)
        d += timedelta(days=1)
    if not days:
        return None
    try:
        return days[n - 1] if n > 0 else days[n]
    except IndexError:
        return None


def _derived_events(start: _date, end: _date) -> list[dict]:
    """Events whose publisher calendar isn't machine-readable for free, built
    from scheduling rules that have held for years. Flagged ``derived=True`` so
    the UI can hedge the wording — a rule can slip a day, a published date can't.
    """
    hols = _holidays(start - timedelta(days=10), end + timedelta(days=10))
    out: list[dict] = []

    def add(d: _date | None, name: str, hh: int, mm: int, impact: str, note: str) -> None:
        if d and start <= d <= end:
            out.append({
                "name": name, "date": d.isoformat(), "time_et": f"{hh:02d}:{mm:02d}",
                "hour": hh, "minute": mm, "impact": impact, "note": note,
                "source": "rule", "derived": True,
            })

    # Walk every month the window touches.
    months: list[tuple[int, int]] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        months.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)

    for y, m in months:
        # ISM — pulled from FRED over licensing, so rule-derived. The first and
        # third business day of the month have been its slots for years.
        add(_nth_business_day(y, m, 1, hols), "ISM manufacturing PMI", 10, 0, "medium",
            "10:00 print — lands after the opening range is already set.")
        add(_nth_business_day(y, m, 3, hols), "ISM services PMI", 10, 0, "medium",
            "The larger share of the economy, and the bigger ES mover of the two.")

        # U-Mich sentiment: preliminary on the second Friday, final on the fourth.
        add(_nth_weekday(y, m, 4, 2), "U-Mich sentiment (prelim)", 10, 0, "low",
            "Watch the inflation-expectations sub-index, not the headline.")
        add(_nth_weekday(y, m, 4, 4), "U-Mich sentiment (final)", 10, 0, "low",
            "Rarely moves anything unless it revises hard.")

        # Conference Board consumer confidence: last Tuesday of the month.
        add(_nth_weekday(y, m, 1, -1), "Consumer confidence", 10, 0, "low",
            "Rarely a mover on its own.")

        # Quad witching: third Friday of Mar/Jun/Sep/Dec.
        if m in (3, 6, 9, 12):
            add(_nth_weekday(y, m, 4, 3), "Quad witching (OpEx)", 9, 30, "medium",
                "Index futures, index options, single-stock futures and options all "
                "expire — volume spikes and pinning distorts the close. Volume, not "
                "range: measured on dividend-adjusted closes it is a NARROWER than "
                "normal session. The reputation for wide witching days comes from "
                "SPY's quarterly ex-dividend landing on the same date.")

    # EIA petroleum status: Wednesday 10:30, shifting to Thursday when a
    # holiday lands earlier in the week.
    d = start
    while d <= end:
        if d.weekday() == 2:
            shifted = any((d - timedelta(days=k)) in hols for k in (1, 2))
            when = d + timedelta(days=1) if shifted else d
            if start <= when <= end:
                out.append({
                    "name": "EIA petroleum status", "date": when.isoformat(),
                    "time_et": "10:30", "hour": 10, "minute": 30, "impact": "low",
                    "note": "Energy complex; reaches ES only through XLE.",
                    "source": "rule", "derived": True,
                })
        d += timedelta(days=1)

    return out


def _fomc_events(start: _date, end: _date) -> list[dict]:
    out = []
    for fd in FOMC_DATES:
        try:
            d = datetime.strptime(fd, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= d <= end):
            continue
        is_sep = fd in FOMC_SEP_DATES
        out.append({
            "name": "FOMC decision + SEP/dot plot" if is_sep else "FOMC decision",
            "date": fd, "time_et": "14:00", "hour": 14, "minute": 0, "impact": "high",
            "note": ("Statement 14:00, press conference 14:30 — the conference usually "
                     "moves more than the statement."
                     + (" Dot plot lands with the statement." if is_sep else "")),
            "source": "fomc", "derived": False,
        })
    return out


# ═══════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════

_IMPACT_RANK = {"high": 0, "medium": 1, "low": 2}


def macro_events(start: str | _date, end: str | _date) -> list[dict]:
    """Every known macro event in [start, end], sorted by date then time.

    Each event: name, date, time_et, hour, minute, impact, note, source,
    derived, measured. FRED-published dates win over a rule-derived one for the
    same event on the same day.

    `measured` is the measured range-expansion block (or None where the event
    was never in the study's universe) — see the module docstring for why it is
    a separate axis from `impact` rather than a replacement for it.
    """
    s = start if isinstance(start, _date) else datetime.strptime(str(start)[:10], "%Y-%m-%d").date()
    e = end if isinstance(end, _date) else datetime.strptime(str(end)[:10], "%Y-%m-%d").date()
    if e < s:
        s, e = e, s

    events = [ev for ev in _fred_window() if s.isoformat() <= ev["date"] <= e.isoformat()]
    events += _fomc_events(s, e)
    events += _derived_events(s, e)

    # De-dupe on (date, name); a published date beats a derived one.
    best: dict[tuple[str, str], dict] = {}
    for ev in events:
        k = (ev["date"], ev["name"])
        if k not in best or (best[k].get("derived") and not ev.get("derived")):
            best[k] = ev

    ordered = sorted(best.values(),
                     key=lambda x: (x["date"], x["hour"], x["minute"],
                                    _IMPACT_RANK.get(x["impact"], 3)))

    # Attached last so every path out of this function carries it — the ES card,
    # the alert worker and the home calendar all read `macro_events`, and a
    # block that only some callers received would be worse than none.
    try:
        from src.event_impact import attach
        return attach(ordered)
    except Exception as e:  # pragma: no cover — the calendar must still work
        logger.warning(f"measured impact unavailable, calendar served without it: {e}")
        return [{**ev, "measured": None} for ev in ordered]


def find_events_near_date(date_str: str, window_days: int = 5) -> list[dict]:
    """All known macro events within window_days of a given date.

    Returns dicts with name, date and days_away (positive = future), plus
    time_et / impact / note / source / derived for callers that want them.
    """
    try:
        anchor = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        anchor = _date.today()

    w = max(0, int(window_days))
    out = []
    for ev in macro_events(anchor - timedelta(days=w), anchor + timedelta(days=w)):
        d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        out.append({**ev, "days_away": (d - anchor).days})
    return out


def todays_events(today: str | _date | None = None) -> list[dict]:
    """Just today's scheduled releases, in clock order."""
    d = today or _date.today()
    if not isinstance(d, _date):
        d = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    return macro_events(d, d)


def get_upcoming_fomc(n: int = 3) -> list[str]:
    """The next N upcoming FOMC meeting dates."""
    today = _date.today()
    return [d for d in FOMC_DATES
            if datetime.strptime(d, "%Y-%m-%d").date() > today][:n]


def get_next_fomc() -> str | None:
    """The single next FOMC date, or None if the list is exhausted."""
    upcoming = get_upcoming_fomc(1)
    return upcoming[0] if upcoming else None


def is_fomc_week(date_str: str | None = None) -> bool:
    """Whether a date falls within 5 days of an FOMC meeting."""
    if date_str:
        try:
            dt = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            dt = _date.today()
    else:
        dt = _date.today()
    return any(abs((dt - datetime.strptime(fd, "%Y-%m-%d").date()).days) <= 5
               for fd in FOMC_DATES)
