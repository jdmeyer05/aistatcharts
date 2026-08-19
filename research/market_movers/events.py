"""The event universe: every dated thing that could plausibly move the tape.

WHERE THE DATES COME FROM, AND WHY THE SOURCE CHOICE IS THE WHOLE JOB. A study
that measures "the market on CPI day" against dates that are not CPI days
measures nothing, and it looks exactly like a study that works. Four sources, in
descending order of trust:

1. FRED ``/fred/series/vintagedates`` — the dates a headline series was actually
   updated, which is the date its number printed. This is the primary source and
   it is used in preference to the release calendar for a specific reason: the
   RELEASE calendar bundles products. Release 27 fires 24 times a year because
   New Residential Construction publishes twice a month; release 95 mixes the
   advance durable goods report with the full M3 survey; release 46 shows both
   2024-02-14 and 2024-02-16 with no way to tell which one was PPI. Vintages are
   per-series, so HOUST gives twelve dates a year and PPIACO puts the February
   print on the 16th, which is where it was.

2. FRED ``/fred/release/dates`` — for the regional surveys, which have no clean
   headline series but publish on a single, unambiguous date.

3. federalreserve.gov — FOMC statement dates, parsed from the Fed's own calendar
   pages. No FRED release gives meeting dates: release 101 ("FOMC Press
   Release") fires ~313 days a year because it covers every press release the
   committee issues, not the eight decisions.

4. Rule-derived — ISM (licensed, not on FRED), FOMC minutes (three weeks after
   the decision, by long-standing policy), opex, triple witching, month and
   quarter end. Flagged ``derived`` so a reader can discount them.

THE RESIDUAL AMBIGUITY, AND HOW IT IS RESOLVED. Even vintage dates carry the
occasional extra: CPI shows both 2024-02-09 (annual seasonal-factor revision)
and 2024-02-13 (the January print), and retail sales adds an April benchmark
revision. Where a month has more than one vintage, the one nearest the series'
own median day-of-month is kept — CPI's median is the 12th, so the 13th wins
over the 9th, and retail sales' is the 16th, so the 15th wins over the 23rd.
That is a rule about the release's own habit, not a guess about which number
mattered.

NOT INVENTED FROM MEMORY. Every date here was fetched. Typing out FOMC dates
from recall is exactly the kind of quietly-wrong input that makes a study look
rigorous and be worthless.
"""

from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from datetime import date, timedelta

import requests

logger = logging.getLogger(__name__)

_VINTAGES = "https://api.stlouisfed.org/fred/series/vintagedates"
_REL_DATES = "https://api.stlouisfed.org/fred/release/dates"
_FED_CAL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
_FED_HIST = "https://www.federalreserve.gov/monetarypolicy/fomchistorical{year}.htm"
_UA = {"User-Agent": "Mozilla/5.0 (aistatcharts research)"}

# series_id -> (label, ET time it lands, family, cadence)
# Times are constants and always have been — BLS at 08:30, Census at 08:30 or
# 10:00 — and are recorded for interpretation only. The measurement runs on
# daily bars and never uses them.
VINTAGE_SERIES: dict[str, tuple[str, str, str, str]] = {
    "CPIAUCSL": ("CPI", "08:30", "inflation", "monthly"),
    "PAYEMS": ("Nonfarm payrolls", "08:30", "labour", "monthly"),
    "PCEPI": ("PCE price index", "08:30", "inflation", "monthly"),
    "PPIACO": ("PPI", "08:30", "inflation", "monthly"),
    "GDPC1": ("GDP", "08:30", "growth", "monthly"),
    "ICSA": ("Initial jobless claims", "08:30", "labour", "weekly"),
    "RSAFS": ("Retail sales", "08:30", "growth", "monthly"),
    "JTSJOL": ("JOLTS job openings", "10:00", "labour", "monthly"),
    "INDPRO": ("Industrial production", "09:15", "growth", "monthly"),
    "HOUST": ("Housing starts", "08:30", "growth", "monthly"),
    "BOPGSTB": ("Trade balance", "08:30", "growth", "monthly"),
    "HSN1F": ("New home sales", "10:00", "growth", "monthly"),
    "UMCSENT": ("U. Michigan sentiment (final)", "10:00", "survey", "monthly"),
}

# Regional surveys: no clean headline series, but a single unambiguous release.
RELEASE_IDS: dict[int, tuple[str, str, str]] = {
    321: ("Empire State manufacturing", "08:30", "survey"),
    351: ("Philly Fed manufacturing", "08:30", "survey"),
}


def _fred_key() -> str:
    key = os.environ.get("FRED_API_KEY")
    if key:
        return key
    try:
        import toml
        return toml.load(".streamlit/secrets.toml").get("FRED_API_KEY", "")
    except Exception:
        return ""


def _get(url: str, params: dict) -> dict:
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def vintage_dates(series_id: str, start: str, end: str) -> list[date]:
    j = _get(_VINTAGES, {"api_key": _fred_key(), "file_type": "json",
                         "series_id": series_id, "realtime_start": start,
                         "realtime_end": end, "limit": 10000})
    out = []
    for s in j.get("vintage_dates", []):
        try:
            out.append(date.fromisoformat(s))
        except Exception:
            continue
    return sorted(set(out))


def release_dates(release_id: int, start: str, end: str) -> list[date]:
    j = _get(_REL_DATES, {"api_key": _fred_key(), "file_type": "json",
                          "release_id": release_id, "realtime_start": start,
                          "realtime_end": end, "limit": 10000,
                          "include_release_dates_with_no_data": "false",
                          "sort_order": "asc"})
    out = []
    for row in j.get("release_dates", []):
        try:
            out.append(date.fromisoformat(row["date"]))
        except Exception:
            continue
    return sorted(set(out))


def dedupe_monthly(dates: list[date]) -> list[date]:
    """One date per month: the one nearest this release's own median day-of-month.

    Annual revisions and benchmark updates share a month with the real print.
    The print keeps a stable slot — CPI near the 12th, retail sales near the
    16th — so distance from the median day-of-month separates them without any
    hand-labelling of which date was which.
    """
    if len(dates) < 6:
        return dates
    doms = sorted(d.day for d in dates)
    median_dom = doms[len(doms) // 2]

    by_month: dict[tuple[int, int], list[date]] = defaultdict(list)
    for d in dates:
        by_month[(d.year, d.month)].append(d)

    kept = []
    for _, group in by_month.items():
        kept.append(min(group, key=lambda d: (abs(d.day - median_dom), d)))
    return sorted(kept)


def fomc_decision_dates(start_year: int, end_year: int) -> list[date]:
    """Statement dates, from the Fed's own calendar pages.

    The current page carries roughly the last five years plus the year ahead;
    older years live on per-year historical pages. Both list the statement as
    ``monetaryYYYYMMDDa.htm`` — the decision date, the second day of a two-day
    meeting, and the day the tape reacts.
    """
    found: set[str] = set()
    try:
        r = requests.get(_FED_CAL, headers=_UA, timeout=45)
        r.raise_for_status()
        found |= set(re.findall(r"monetary(\d{8})a\.htm", r.text))
    except Exception as e:
        logger.warning(f"FOMC current calendar fetch failed: {e}")

    for yr in range(start_year, end_year + 1):
        try:
            r = requests.get(_FED_HIST.format(year=yr), headers=_UA, timeout=45)
            if r.status_code != 200:
                continue
            found |= set(re.findall(r"monetary(\d{8})a\.htm", r.text))
        except Exception as e:
            logger.debug(f"FOMC {yr} historical page failed: {e}")

    out = []
    for s in found:
        try:
            d = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            continue
        if start_year <= d.year <= end_year:
            out.append(d)
    return sorted(set(out))


def _nth_weekday(y: int, m: int, weekday: int, n: int) -> date | None:
    d = date(y, m, 1)
    hits = []
    while d.month == m:
        if d.weekday() == weekday:
            hits.append(d)
        d += timedelta(days=1)
    return hits[n - 1] if len(hits) >= n else None


def _nth_business_day(y: int, m: int, n: int) -> date | None:
    d, count = date(y, m, 1), 0
    while d.month == m:
        if d.weekday() < 5:
            count += 1
            if count == n:
                return d
        d += timedelta(days=1)
    return None


def derived_dates(start: date, end: date, fomc: list[date]) -> dict[str, list[date]]:
    """Calendar arithmetic, for things with no publisher to ask.

    ISM is here because it is licensed and not on FRED — the manufacturing PMI
    lands on the first business day of the month and services on the third, and
    those rules have held for years. FOMC minutes are three weeks after the
    decision by standing policy. Everything here is approximate in a way the
    fetched dates are not, which is why the caller sees ``source="rule"``.
    """
    ism_mfg, ism_svc, opex, witch, month_end, quarter_end = [], [], [], [], [], []
    y, m = start.year, start.month
    while date(y, m, 1) <= end:
        for target, fn, n in ((ism_mfg, _nth_business_day, 1), (ism_svc, _nth_business_day, 3)):
            d = fn(y, m, n)
            if d and start <= d <= end:
                target.append(d)
        tf = _nth_weekday(y, m, 4, 3)
        if tf and start <= tf <= end:
            opex.append(tf)
            if m in (3, 6, 9, 12):
                witch.append(tf)
        nxt = date(y + (m == 12), 1 if m == 12 else m + 1, 1)
        last = nxt - timedelta(days=1)
        if start <= last <= end:
            month_end.append(last)
            if m in (3, 6, 9, 12):
                quarter_end.append(last)
        y, m = nxt.year, nxt.month

    minutes = [d + timedelta(days=21) for d in fomc if start <= d + timedelta(days=21) <= end]

    return {
        "ISM manufacturing": ism_mfg,
        "ISM services": ism_svc,
        "FOMC minutes": minutes,
        "Monthly opex": opex,
        "Triple witching": witch,
        "Month end": month_end,
        "Quarter end": quarter_end,
    }


def build_universe(start: str = "2012-01-01", end: str = "2026-12-31") -> dict[str, dict]:
    """Every event type with its real historical dates.

    Returns ``{label: {"dates": [...], "family": ..., "source": ..., ...}}``.
    """
    s, e = date.fromisoformat(start), date.fromisoformat(end)
    universe: dict[str, dict] = {}

    for sid, (label, when, family, cadence) in VINTAGE_SERIES.items():
        try:
            dates = vintage_dates(sid, start, end)
        except Exception as ex:
            logger.warning(f"{label}: vintage fetch failed ({ex})")
            continue
        raw_n = len(dates)
        if cadence == "monthly":
            dates = dedupe_monthly(dates)
        if len(dates) < 12:
            logger.warning(f"{label}: only {len(dates)} dates, skipping")
            continue
        universe[label] = {"dates": dates, "family": family,
                           "source": f"fred-vintage:{sid}", "release_time_et": when,
                           "raw_dates": raw_n, "cadence": cadence}

    for rid, (label, when, family) in RELEASE_IDS.items():
        try:
            dates = dedupe_monthly(release_dates(rid, start, end))
        except Exception as ex:
            logger.warning(f"{label}: release fetch failed ({ex})")
            continue
        if len(dates) < 12:
            continue
        universe[label] = {"dates": dates, "family": family,
                           "source": f"fred-release:{rid}", "release_time_et": when,
                           "raw_dates": len(dates), "cadence": "monthly"}

    fomc = fomc_decision_dates(s.year, e.year)
    if len(fomc) >= 12:
        universe["FOMC decision"] = {"dates": fomc, "family": "policy",
                                     "source": "federalreserve.gov",
                                     "release_time_et": "14:00",
                                     "raw_dates": len(fomc), "cadence": "8x/yr"}
    else:
        logger.warning(f"FOMC: only {len(fomc)} dates found, skipping")

    for label, dates in derived_dates(s, e, fomc).items():
        if len(dates) < 12:
            continue
        family = ("policy" if "FOMC" in label else
                  "survey" if "ISM" in label else "structural")
        universe[label] = {"dates": dates, "family": family, "source": "rule",
                           "release_time_et": "10:00" if "ISM" in label else "16:00",
                           "raw_dates": len(dates), "cadence": "monthly"}

    return universe
