"""Centralized economic event calendar — single source of truth.

FOMC meetings, CPI release dates, and other key macro events.
Update this file once per year (typically in January when the Fed publishes its schedule).
All pages import from here instead of maintaining their own hardcoded lists.

Last updated: 2026-03-28
Sources:
- FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
"""

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


def get_upcoming_fomc(n: int = 3) -> list[str]:
    """Return the next N upcoming FOMC meeting dates."""
    import pandas as pd
    now = pd.Timestamp.now()
    upcoming = [d for d in FOMC_DATES if pd.to_datetime(d) > now]
    return upcoming[:n]


def get_next_fomc() -> str | None:
    """Return the single next FOMC date, or None if list exhausted."""
    upcoming = get_upcoming_fomc(1)
    return upcoming[0] if upcoming else None


def is_fomc_week(date_str: str = None) -> bool:
    """Check if a date falls within 5 days of an FOMC meeting."""
    import pandas as pd
    dt = pd.to_datetime(date_str) if date_str else pd.Timestamp.now()
    for fd in FOMC_DATES:
        fdt = pd.to_datetime(fd)
        if abs((dt - fdt).days) <= 5:
            return True
    return False


def find_events_near_date(date_str: str, window_days: int = 5) -> list[dict]:
    """Find all known macro events within window_days of a given date.

    Returns list of dicts with: name, date, days_away.
    """
    import pandas as pd
    dt = pd.to_datetime(date_str)
    events = []

    # FOMC
    for fd in FOMC_DATES:
        fdt = pd.to_datetime(fd)
        days = (fdt - dt).days
        if -window_days <= days <= window_days:
            is_sep = fd in FOMC_SEP_DATES
            events.append({
                "name": f"FOMC{' + SEP/Dot Plot' if is_sep else ''}",
                "date": fd,
                "days_away": days,
            })

    # CPI: typically 12th-15th of each month
    if 7 <= dt.day <= 20:
        events.append({"name": "CPI release window", "date": date_str, "days_away": 0})

    # NFP: first Friday of month
    _first_fri = dt.replace(day=1)
    while _first_fri.weekday() != 4:
        _first_fri += pd.Timedelta(days=1)
    nfp_days = abs((dt - _first_fri).days)
    if nfp_days <= window_days:
        events.append({
            "name": "NFP/Jobs Report",
            "date": _first_fri.strftime("%Y-%m-%d"),
            "days_away": (dt - _first_fri).days,
        })

    # Quad witching: 3rd Friday of Mar/Jun/Sep/Dec
    if dt.month in (3, 6, 9, 12) and 15 <= dt.day <= 21 and dt.weekday() == 4:
        events.append({"name": "Quad Witching (OpEx)", "date": date_str, "days_away": 0})

    return events
