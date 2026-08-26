"""Single-name earnings big enough that an index trader has to care.

WHY THIS EXISTS
---------------
The ES card's scheduled-risk panel was macro-only: `economic_calendar` covers
CPI, PCE, payrolls, ISM, FOMC and nothing else. That is the right universe for
08:30 prints and the wrong one for the evening of a megacap report, when the
panel would render "Nothing on the macro calendar" over the most event-loaded
Globex session of the quarter. Absence of a macro print was being drawn as
absence of risk.

WHAT IS AND IS NOT AVAILABLE HERE
---------------------------------
There is no S&P 500 constituent feed on this stack — FMP's `sp500-constituent`
answers 402 on our key, and `cross_asset_vol` and `vol_es_read` both already
carry the same note. So a float-adjusted INDEX WEIGHT cannot be computed, and
nothing in this module claims one. Selection is by MARKET CAP, which is a size
proxy and is labelled as such everywhere it surfaces.

The number that actually answers "how much should I care" is not a weight at
all: it is what SPX options charge for spanning the event, which
`es_expected_move.event_premium` measures directly off the straddle term
structure. This module decides WHICH names get on the card; that one prices
what the card should say about them.

THE EFFECT WINDOW IS THE WHOLE POINT
------------------------------------
A macro print lands inside the session it belongs to. An earnings report does
not. A company reporting "after the close" on Tuesday moves Wednesday's gap,
and a card that files it under Tuesday tells the trader to brace for something
that cannot touch the session in front of them. So events are attached to the
session they AFFECT, not the date they carry:

    AMC on the prior trading day  -> this session's gap (already printed)
    BMO on the session day        -> this session, pre-open
    AMC on the session day        -> next session's gap (after this bell)

RELEASE TIMES ARE APPROXIMATE, and say so. Agencies publish at a constant
minute; companies do not. "After the close" means somewhere between 16:00 and
16:30 depending on the filer, so these carry `time_approx` and the UI hedges
them rather than counting down to a minute nobody promised.

Sources:
- Calendar: https://finnhub.io/docs/api/earnings-calendar (gives `hour` as
  amc/bmo/dmh, which is the field this whole module turns on)
- Market cap: https://site.financialmodelingprep.com/developer/docs (stable
  `/profile`; the v3 endpoints are retired and answer 403)
"""

from __future__ import annotations

import logging
from datetime import date as _date, datetime, timedelta

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════
# Size tiers
# ═══════════════════════════════════════════════
# Cutoffs are on market cap because index weight is not obtainable (see module
# docstring). They are deliberately coarse: the job is to separate "this can
# move the index on its own" from "this is a single-stock story", and no
# threshold in this neighbourhood changes that call. As of writing the top US
# cap is ~$5T and the next name reporting in the same week was $211B, so the
# gap the high tier is trying to catch is an order of magnitude, not a rounding.
_TIER_HIGH = 1_000e9
_TIER_MEDIUM = 250e9
_TIER_FLOOR = 100e9        # below this it is noise to someone trading ES

# Companies do not publish to the minute. These are the conventional windows.
_AMC_CLOCK = (16, 15)
_BMO_CLOCK = (7, 0)

# How many names to surface per effect window. The panel is a session read, not
# an earnings screener — three megacaps is already more than a trader will act
# on, and a longer list buries the macro rows underneath it.
_MAX_PER_WINDOW = 3

# How many of a window's candidates get a market-cap lookup at all. Bounds the
# cold-cache cost of the panel at 3 x this, which is what keeps a vendor's rate
# limit from turning the whole block off.
_SHORTLIST = 8

_CACHE: dict = {}
_CAL_TTL_S = 6 * 3600
_CAP_TTL_S = 24 * 3600
_FAIL_TTL_S = 300


# ═══════════════════════════════════════════════
# Feeds
# ═══════════════════════════════════════════════

def _finnhub_window(start: _date, end: _date) -> list[dict]:
    """Raw earnings calendar rows for a date window, cached.

    Fetched as one window rather than per-day: the ES card asks about three
    adjacent sessions on every render, and three round-trips to answer one
    question is three chances to be rate-limited mid-card.
    """
    from time import time as _now

    key = f"cal:{start.isoformat()}:{end.isoformat()}"
    hit = _CACHE.get(key)
    if hit and (_now() - hit[0]) < _CAL_TTL_S:
        return hit[1]
    if _CACHE.get("cal_fail") and (_now() - _CACHE["cal_fail"]) < _FAIL_TTL_S:
        return hit[1] if hit else []

    try:
        import requests
        from src.api_keys import get_secret

        api_key = get_secret("FINNHUB_API_KEY")
        if not api_key:
            _CACHE["cal_fail"] = _now()
            return hit[1] if hit else []

        r = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": start.isoformat(), "to": end.isoformat(), "token": api_key},
            timeout=15,
        )
        r.raise_for_status()
        rows = r.json().get("earningsCalendar") or []
        _CACHE[key] = (_now(), rows)
        _CACHE.pop("cal_fail", None)
        return rows
    except Exception as e:
        logger.warning(f"Finnhub earnings calendar fetch failed: {e}")
        _CACHE["cal_fail"] = _now()
        return hit[1] if hit else []


def _cap_from_massive(symbol: str) -> float | None:
    """Market cap from the reference endpoint on the options vendor's key.

    Primary because it is the SAME key the chain calls already use, so it sits
    behind the entitlement this stack actually pays for rather than a free tier
    that runs out mid-session.
    """
    import requests
    from src.api_keys import get_secret

    key = get_secret("POLYGON_API_KEY") or get_secret("MASSIVE_API_KEY")
    if not key:
        return None
    r = requests.get(f"https://api.polygon.io/v3/reference/tickers/{symbol}",
                     params={"apiKey": key}, timeout=12)
    r.raise_for_status()
    cap = (r.json().get("results") or {}).get("market_cap")
    return float(cap) if cap else None


def _cap_from_fmp(symbol: str) -> float | None:
    """Fallback. Free-tier quota is small enough to 429 mid-session, which is
    exactly why it is not the primary."""
    import requests
    from src.api_keys import get_secret

    key = get_secret("FMP_API_KEY")
    if not key:
        return None
    r = requests.get("https://financialmodelingprep.com/stable/profile",
                     params={"symbol": symbol, "apikey": key}, timeout=12)
    r.raise_for_status()
    body = r.json()
    cap = body[0].get("marketCap") if isinstance(body, list) and body else None
    return float(cap) if cap else None


def _market_cap(symbol: str) -> float | None:
    """Market cap for one symbol, cached for a day, across two vendors.

    Cached per SYMBOL rather than per request because the same handful of
    megacaps recur every quarter — after the first warm-up the tiering costs
    nothing, which is what makes it safe to run inside the ES bundle.

    A FAILURE IS CACHED TOO, briefly. Without that, a rate-limited vendor is
    re-asked for every symbol on every render, which is how one 429 turns into
    a sustained outage of the panel instead of a slow minute.
    """
    from time import time as _now

    key = f"cap:{symbol}"
    hit = _CACHE.get(key)
    if hit and (_now() - hit[0]) < (_CAP_TTL_S if hit[1] else _FAIL_TTL_S):
        return hit[1]

    for source in (_cap_from_massive, _cap_from_fmp):
        try:
            cap = source(symbol)
            if cap:
                _CACHE[key] = (_now(), cap)
                return cap
        except Exception as e:
            logger.debug(f"{source.__name__} for {symbol} failed: {e}")

    _CACHE[key] = (_now(), None)
    return None


# ═══════════════════════════════════════════════
# Shaping
# ═══════════════════════════════════════════════

def _tier(cap: float | None) -> str | None:
    """Impact tier from market cap, or None to drop the name entirely."""
    if not cap or cap < _TIER_FLOOR:
        return None
    if cap >= _TIER_HIGH:
        return "high"
    if cap >= _TIER_MEDIUM:
        return "medium"
    return "low"


def _fmt_cap(cap: float) -> str:
    return f"${cap / 1e12:.2f}T" if cap >= 1e12 else f"${cap / 1e9:,.0f}B"


def _nyse_holidays(start: _date, end: _date) -> set[_date]:
    """US equity market closures — NOT the federal calendar.

    The two differ in both directions and both directions are bugs here. The
    federal list has Columbus Day and Veterans Day, when the exchange is OPEN
    and companies do report; skipping those would step over a real report day.
    It lacks Good Friday, when the exchange is SHUT; treating that as a trading
    day makes the lookup land on a date that has no reports at all. So the rule
    set is spelled out rather than proxied.
    """
    try:
        import pandas as pd
        from pandas.tseries.holiday import (
            AbstractHolidayCalendar, Holiday, GoodFriday, USLaborDay,
            USMartinLutherKingJr, USMemorialDay, USPresidentsDay,
            USThanksgivingDay, nearest_workday,
        )

        class _NYSE(AbstractHolidayCalendar):
            rules = [
                Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
                USMartinLutherKingJr,
                USPresidentsDay,
                GoodFriday,
                USMemorialDay,
                Holiday("Juneteenth", month=6, day=19, start_date="2021-06-18",
                        observance=nearest_workday),
                Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
                USLaborDay,
                USThanksgivingDay,
                Holiday("Christmas", month=12, day=25, observance=nearest_workday),
            ]

        # Pad the window: an observance rule can shift a holiday across the edge.
        hols = _NYSE().holidays(start=pd.Timestamp(start) - pd.Timedelta(days=7),
                                end=pd.Timestamp(end) + pd.Timedelta(days=7))
        return {h.date() for h in hols}
    except Exception as e:
        # Degrade to weekends-only rather than failing the panel. The cost is the
        # bug this function exists to fix, so it is worth a line in the log.
        logger.warning(f"NYSE holiday calendar unavailable, using weekends only: {e}")
        return set()


def _prev_trading_day(d: _date) -> _date:
    """The last session before `d`.

    Weekends alone are not enough: on the Tuesday after Memorial Day the
    previous CALENDAR weekday is the holiday itself, so the AMC lookup lands on
    a closed market, finds nothing, and the panel renders empty on a morning
    that opens on Friday's reports.
    """
    hols = _nyse_holidays(d - timedelta(days=10), d)
    p = d - timedelta(days=1)
    while p.weekday() >= 5 or p in hols:
        p -= timedelta(days=1)
    return p


def _next_trading_day(d: _date) -> _date:
    hols = _nyse_holidays(d, d + timedelta(days=10))
    n = d + timedelta(days=1)
    while n.weekday() >= 5 or n in hols:
        n += timedelta(days=1)
    return n


# What each effect window means, in the words the card should use. Keeping the
# phrasing here rather than in the component means the AI payload and the panel
# describe the event the same way — they diverged once already on `derived`.
_WINDOWS = {
    "this_session_gap": (
        "already reported — this session opens on the reaction",
        "Reported after the previous close, so the gap is the reaction and the "
        "overnight range is not an ordinary one.",
    ),
    "this_session_open": (
        "before this session's open",
        "Lands pre-open, so it sets the tone for the cash open the way an 08:30 "
        "print does.",
    ),
    "next_session_gap": (
        "after this session's close — next session's gap",
        "Lands after the bell, so it cannot move this session's range. It is the "
        "next session's gap risk, and the reason to think about holding into the "
        "close rather than about today's levels.",
    ),
}


def _candidates(rows: list[dict], on: _date, want_hour: str) -> list[dict]:
    """Rows for one date and one reporting window, pre-filtered before any
    market-cap lookup.

    The pre-filter matters: a day's calendar runs to ~25 names and most are
    micro-caps with no analyst coverage at all. Requiring both a reporting
    window and an EPS estimate drops those without a single HTTP call, which is
    what keeps this affordable inside a request handler.
    """
    iso = on.isoformat()
    return [
        r for r in rows
        if str(r.get("date", ""))[:10] == iso
        and (r.get("hour") or "").lower() == want_hour
        and r.get("epsEstimate") is not None
        and r.get("symbol")
    ]


def _build(rows: list[dict], on: _date, want_hour: str, window: str) -> list[dict]:
    """Tier one date/window's candidates by market cap and shape the survivors."""
    from concurrent.futures import ThreadPoolExecutor

    cands = _candidates(rows, on, want_hour)
    if not cands:
        return []

    # Shortlist on the revenue estimate that came free in the calendar payload
    # before spending a single lookup. A day's window runs to ~25 covered names
    # and the card shows three, so pricing all of them is ~22 wasted calls into
    # a rate limit. Revenue is a WORSE size proxy than market cap — that is why
    # it only shortlists and never decides — but the two agree closely enough at
    # the top that a trillion-dollar name outside the revenue top few is not a
    # case that occurs in practice.
    ranked = sorted(cands, key=lambda r: -(r.get("revenueEstimate") or 0))
    shortlist = ranked[:_SHORTLIST]

    symbols = sorted({r["symbol"] for r in shortlist})
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        caps = dict(zip(symbols, pool.map(_market_cap, symbols)))

    # Sizing the names is what decides whether they belong on the card, so
    # failing to size them is not a quiet nothing — it renders as an empty panel
    # that looks exactly like a calm evening. Say so in the log.
    if symbols and not any(caps.values()):
        logger.warning(
            "earnings calendar: %d candidates on %s %s but no market cap resolved "
            "from any vendor — the panel will show nothing, which is NOT the same "
            "as nothing being scheduled", len(symbols), on.isoformat(), want_hour)

    cands = shortlist

    hh, mm = _AMC_CLOCK if want_hour == "amc" else _BMO_CLOCK
    label, why = _WINDOWS[window]

    out: list[dict] = []
    for r in cands:
        sym = r["symbol"]
        cap = caps.get(sym)
        tier = _tier(cap)
        if not tier:
            continue
        out.append({
            "name": f"{sym} earnings",
            "symbol": sym,
            "date": on.isoformat(),
            "time_et": f"{hh:02d}:{mm:02d}",
            "hour": hh,
            "minute": mm,
            "impact": tier,
            "note": (
                f"{_fmt_cap(cap)} market cap — {label}. {why} "
                "Size is market cap, not index weight: no constituent feed is "
                "available here, so this ranks the name rather than pricing its "
                "contribution to the index."
            ),
            "source": "finnhub",
            # The DATE is from a published calendar, so it is not rule-derived.
            # The TIME is a convention, which is a different kind of uncertainty
            # and gets its own flag rather than being smuggled into `derived`.
            "derived": False,
            "time_approx": True,
            "kind": "earnings",
            "affects": window,
            "affects_label": label,
            "market_cap": cap,
            "eps_estimate": r.get("epsEstimate"),
            "revenue_estimate": r.get("revenueEstimate"),
        })

    out.sort(key=lambda e: -(e["market_cap"] or 0))
    if len(out) > _MAX_PER_WINDOW:
        dropped = [e["symbol"] for e in out[_MAX_PER_WINDOW:]]
        logger.info("earnings calendar: %s %s showing top %d by size, also qualifying: %s",
                    on.isoformat(), want_hour, _MAX_PER_WINDOW, ", ".join(dropped))
        # Say it on the card too. A silently truncated list reads as a complete
        # one, and "three names report tonight" is a different session from
        # "six do" even when the other three are the smaller half.
        #
        # Careful with the wording: this is not "everything else reporting". Only
        # the revenue shortlist is ever priced, so a name outside it is invisible
        # to this list too. It is the qualifying names we KNOW about and chose
        # not to give a row — claiming more than that would trade one false
        # completeness for another.
        out[_MAX_PER_WINDOW - 1] = dict(
            out[_MAX_PER_WINDOW - 1],
            also_reporting=dropped,
            note=out[_MAX_PER_WINDOW - 1]["note"]
            + f" Also above the size floor in this window, without rows of their own: "
            + ", ".join(dropped) + ". Smaller names report too and are not tracked here.",
        )
    return out[:_MAX_PER_WINDOW]


def earnings_for_session(session_day: str | _date) -> list[dict]:
    """Earnings that bear on one RTH session, attached by effect window.

    Returns event dicts shaped like `economic_calendar`'s so the ES schedule can
    merge the two lists and sort them together, plus `kind`, `affects` and
    `market_cap`. Empty on any failure — a missing earnings feed must degrade to
    the macro-only card it replaced, never to an exception inside the bundle.
    """
    d = session_day
    if not isinstance(d, _date):
        d = datetime.strptime(str(d)[:10], "%Y-%m-%d").date()

    prev, nxt = _prev_trading_day(d), _next_trading_day(d)
    rows = _finnhub_window(prev, nxt)
    if not rows:
        return []

    events = (
        _build(rows, prev, "amc", "this_session_gap")
        + _build(rows, d, "bmo", "this_session_open")
        + _build(rows, d, "amc", "next_session_gap")
    )
    return sorted(events, key=lambda e: (e["date"], e["hour"], e["minute"]))
