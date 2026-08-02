"""ES session briefing — what to expect in the trading session, right now.

Built for someone loading the page repeatedly through the day, so everything
here is phrased relative to the current moment: which session we're in, what's
already happened, and what's still ahead on the clock.

TWO DESIGN CHOICES WORTH KNOWING:

1. Release TIMES are encoded, not scraped. US macro releases run on fixed
   clock times set by the issuing agency — BLS at 08:30 ET, ISM at 10:00,
   FOMC statements at 14:00, EIA petroleum at 10:30 Wednesday. Scraping a
   calendar site for something that hasn't moved in decades adds a fragile
   dependency for no accuracy gain. Dates still come from the calendar module;
   only the time-of-day is a constant here.

2. News is filtered for MACRO relevance, not volume. A feed of every headline
   is noise to someone trading the index. What moves ES is policy, inflation,
   labour, and large-cap earnings — so the fetch is scoped to sources that
   publish those and the rest is dropped.
"""

from __future__ import annotations

import logging
import html
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_TZ = "America/New_York"
_UA = "Mozilla/5.0 (compatible; aistatcharts/1.0)"


# ── Session phases ────────────────────────────────────────────────
# ES itself trades nearly 24h, but the phases that matter to a trader are
# defined by the cash session and the liquidity around it.
_PHASES = [
    ("overnight",   dtime(18, 0), dtime(23, 59, 59), "Globex reopen — thin, prone to drift and false breaks"),
    ("overnight",   dtime(0, 0),  dtime(3, 0),       "Asia hours — thinnest liquidity of the 24h cycle"),
    ("europe",      dtime(3, 0),  dtime(8, 0),       "Europe open — first real volume of the day"),
    ("premarket",   dtime(8, 0),  dtime(9, 30),      "US pre-market — macro prints land here, 08:30 especially"),
    ("rth_open",    dtime(9, 30), dtime(10, 30),     "Opening hour — highest volume and widest ranges of the session"),
    ("rth_midday",  dtime(10, 30), dtime(14, 0),     "Midday — volume fades, ranges compress, chop risk highest"),
    ("rth_close",   dtime(14, 0), dtime(16, 0),      "Closing drive — MOC imbalances build into 15:50"),
    ("post",        dtime(16, 0), dtime(18, 0),      "Post-settlement — earnings land here; ES stays open but thin"),
]


@dataclass(frozen=True)
class Release:
    """A scheduled macro release. `impact` ranks typical ES range expansion."""
    name: str
    hour: int
    minute: int
    impact: str          # "high" | "medium" | "low"
    note: str


# Time-of-day is a constant per agency. Matching on a substring of the event
# name keeps this loosely coupled to whatever the calendar module labels things.
_RELEASE_CLOCK: list[tuple[str, Release]] = [
    ("cpi",            Release("CPI", 8, 30, "high",
                               "Most-watched inflation print, but measured at only ~1.0x a normal "
                               "full-session range — the move is concentrated in the first half hour.")),
    ("ppi",            Release("PPI", 8, 30, "medium", "Feeds the PCE nowcast more than it moves ES directly.")),
    ("payroll",        Release("Nonfarm payrolls", 8, 30, "high",
                               "The other top-tier print. Revisions matter as much as the headline.")),
    ("nonfarm",        Release("Nonfarm payrolls", 8, 30, "high", "Top-tier labour print.")),
    ("jobless",        Release("Initial jobless claims", 8, 30, "medium",
                               "Weekly, every Thursday. Usually background unless the trend breaks.")),
    ("claims",         Release("Initial jobless claims", 8, 30, "medium", "Weekly Thursday labour read.")),
    ("retail sales",   Release("Retail sales", 8, 30, "medium", "Consumer demand; moves discretionary hardest.")),
    ("gdp",            Release("GDP", 8, 30, "medium", "Backward-looking, so it moves ES less than its billing.")),
    ("pce",            Release("PCE", 8, 30, "high", "The Fed's preferred inflation gauge.")),
    ("ism",            Release("ISM", 10, 0, "medium", "10:00 prints land after the opening range is set.")),
    ("consumer confidence", Release("Consumer confidence", 10, 0, "low", "Rarely a mover on its own.")),
    ("michigan",       Release("U-Mich sentiment", 10, 0, "low", "Watch the inflation-expectations sub-index.")),
    ("fomc",           Release("FOMC decision", 14, 0, "high",
                               "Statement 14:00, press conference 14:30 — the conference usually moves more.")),
    ("minutes",        Release("FOMC minutes", 14, 0, "medium", "Three weeks stale, but can re-price the path.")),
    ("eia",            Release("EIA petroleum", 10, 30, "low", "Energy complex; reaches ES only through XLE.")),
]

_DEFAULT_RELEASE = Release("", 8, 30, "low", "")


def _match_release(name: str) -> Release | None:
    low = (name or "").lower()
    for token, rel in _RELEASE_CLOCK:
        if token in low:
            return rel
    return None


def _holiday_caution(now: pd.Timestamp) -> str | None:
    """Warn when the phase table's hours probably don't apply.

    CME runs shortened sessions around US holidays — a 13:00 close the day
    after Thanksgiving and on Christmas Eve, and full closures on the major
    holidays. The phase table encodes a normal day, so rather than assert
    hours that may be wrong, flag the day and let the trader check. Federal
    holidays are a close-enough proxy; CME's calendar differs at the edges
    (Good Friday shut, Columbus Day open), which is why this is a caution and
    not a claim that the market is closed.
    """
    try:
        from pandas.tseries.holiday import USFederalHolidayCalendar
        d = now.normalize().tz_localize(None)
        hols = USFederalHolidayCalendar().holidays(
            start=d - pd.Timedelta(days=2), end=d + pd.Timedelta(days=2))
        hset = {h.normalize() for h in hols}
        if d in hset:
            return "US holiday — CME runs a closed or abbreviated session; the hours above may not apply."
        # Day after Thanksgiving (4th Thursday of November) and Christmas Eve
        # are both 13:00 ET closes.
        if (d.month == 11 and d.weekday() == 4 and (d - pd.Timedelta(days=1)) in hset) or \
           (d.month == 12 and d.day == 24):
            return "Early close — CME settles at 13:00 ET today."
        return None
    except Exception:
        return None


def current_phase(now: pd.Timestamp | None = None) -> dict:
    """Which session we're in, and what that implies for behaviour."""
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)
    weekday = now.weekday()          # 0=Mon .. 6=Sun
    t = now.time()

    # CME hours for ES: the week opens Sunday 18:00 ET and runs continuously to
    # Friday 17:00 ET, broken only by a one-hour maintenance halt at 17:00 each
    # weekday evening. The weekly close matters: ES does NOT reopen on Friday
    # evening, so treating 18:00 as a reopen every day of the week reported a
    # live Globex session all Friday night when the market was shut.
    if weekday == 5:                                    # Saturday
        reason = "weekend"
    elif weekday == 6 and t < dtime(18, 0):             # Sunday before the reopen
        reason = "weekend"
    elif weekday == 4 and t >= dtime(17, 0):            # Friday after the weekly close
        reason = "weekend"
    elif dtime(17, 0) <= t < dtime(18, 0):              # nightly maintenance halt
        reason = "halt"
    else:
        reason = None

    if reason:
        return {
            "phase": "closed",
            "label": "Market closed",
            "note": ("ES is closed for the weekend. Reopens Sunday 18:00 ET."
                     if reason == "weekend"
                     else "Daily maintenance break, 17:00–18:00 ET. Reopens at 18:00."),
            "is_rth": False,
            "now": now.isoformat(),
        }

    holiday = _holiday_caution(now)
    for phase, start, end, note in _PHASES:
        if start <= t < end:
            return {
                "phase": phase,
                "label": phase.replace("_", " ").title(),
                "note": f"{note} {holiday}".strip() if holiday else note,
                "holiday": holiday,
                "is_rth": phase.startswith("rth"),
                "now": now.isoformat(),
            }
    return {"phase": "overnight", "label": "Overnight", "note": "Globex session.",
            "is_rth": False, "now": now.isoformat()}


def trading_session_day(now: pd.Timestamp | None = None) -> pd.Timestamp:
    """The RTH date the current Globex session leads into.

    After 18:00 ET the session that just opened belongs to the NEXT weekday, so
    at 20:00 on a Monday the relevant schedule is Tuesday's, not the prints that
    already came and went. `es_levels` anchors its levels the same way — if the
    two disagree, the card shows one session's levels beside another session's
    scheduled risk, which is worse than either alone.
    """
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)
    day = now.normalize()
    if now.time() >= dtime(18, 0):
        day += pd.Timedelta(days=1)
    while day.weekday() >= 5:                # no Saturday/Sunday session
        day += pd.Timedelta(days=1)
    return day


def todays_schedule(now: pd.Timestamp | None = None) -> list[dict]:
    """Today's scheduled releases with clock times, marked done or upcoming.

    The calendar owns the event metadata — name, clock time, impact and note
    all come back on the event now, so this only has to do the arithmetic that
    depends on the current moment. `_match_release` stays as a fallback for any
    event that arrives without a time attached.
    """
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)

    session_day = trading_session_day(now)
    day = session_day.date()
    try:
        from src.economic_calendar import todays_events
        events = todays_events(day) or []
    except Exception as e:
        logger.warning(f"calendar fetch failed: {e}")
        events = []

    out: list[dict] = []
    for ev in events:
        name = ev.get("name") or ""
        hh, mm = ev.get("hour"), ev.get("minute")
        if hh is None or mm is None:
            rel = _match_release(name)
            hh, mm = (rel.hour, rel.minute) if rel else (8, 30)

        when = pd.Timestamp.combine(pd.Timestamp(day), dtime(hh, mm)).tz_localize(_TZ)
        mins = int((when - now).total_seconds() // 60)
        out.append({
            "name": name,
            # Absolute instant, so the client counts down from a real timestamp
            # instead of reconstructing one from a wall clock — which silently
            # breaks the moment an event is on the next calendar day.
            "when": when.isoformat(),
            "time_et": ev.get("time_et") or f"{hh:02d}:{mm:02d}",
            "impact": ev.get("impact") or "low",
            "note": ev.get("note") or "",
            # A rule-derived date can slip a day; a published one can't. The UI
            # hedges the wording on these.
            "derived": bool(ev.get("derived")),
            "minutes_away": mins,
            "status": "upcoming" if mins > 0 else "released",
            # Pre-open prints set the tone for the whole session; ones that land
            # mid-session interrupt an already-established range.
            "before_open": (hh, mm) < (9, 30),
        })

    out.sort(key=lambda e: e["time_et"])
    return out


# ── Macro news ────────────────────────────────────────────────────
# Free, no API key. Chosen for macro relevance to the index rather than breadth
# — a firehose of single-name headlines is noise to someone trading ES.
_FEEDS: list[tuple[str, str]] = [
    ("Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("CNBC Economy", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("CNBC Markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135"),
    # Both MarketWatch feeds were dropped after measuring them. mw_topstories
    # carries personal finance ("can she claim her late husband's Social
    # Security"), so the relevance filter discarded 100% of it. mw_marketpulse
    # is macro but ABANDONED — it still answers 200 with items dated 2024-10 to
    # 2025-07, over a year stale. A feed can fail by returning old news as
    # easily as by returning an error, and only one of those looks like failure.
    ("Investing.com", "https://www.investing.com/rss/news_14.rss"),
]

# Headlines that actually move the index, rather than everything published.
#
# The geopolitical and FX terms were added after the dark-feed warning fired:
# Investing.com was live and three minutes fresh while contributing NOTHING,
# because "Iran threatens regional energy fields" and "Japan to announce joint
# yen intervention with US" matched none of the original words — on a morning
# when another feed was carrying "Exxon and Chevron profits surge on rising oil
# prices due to Iran war". Widening is safe now in a way it was not before,
# because the tiers rank what gets through instead of the filter deciding alone.
_RELEVANT = re.compile(
    r"\b(fed|fomc|powell|rate cut|rate hike|inflation|cpi|pce|payroll|jobs|unemployment|"
    r"jobless|gdp|recession|tariff|treasury|yield|earnings|guidance|s&p|nasdaq|stocks|"
    r"selloff|rally|dollar|oil|hawkish|dovish|"
    r"war|military|strike|sanction|invasion|attack|iran|russia|ukraine|china|opec|crude|"
    r"energy|yen|euro|ecb|boj|intervention|currency|shutdown|debt ceiling|downgrade)\b", re.I)

# Tier 1 moves the whole index; tier 2 is market-wide colour. RANKED rather than
# filtered, because "key news" is an ordering problem — dropping a story to make
# room for a fresher one is how an FOMC statement ends up under a stock tip.
_TIER1 = re.compile(
    r"\b(fed|fomc|powell|rate cut|rate hike|hawkish|dovish|inflation|cpi|pce|payroll|"
    r"jobless|unemployment|gdp|recession|tariff|treasury|jobs report|"
    # Policy shocks and geopolitics move the index the way a data print does —
    # a currency intervention or a threat to energy supply is not colour.
    r"war|invasion|sanction|opec|intervention|ecb|boj|shutdown|debt ceiling|downgrade)\b",
    re.I)
_TIER2 = re.compile(
    r"\b(s&p|nasdaq|dow|stocks|selloff|rally|dollar|oil|vix|volatility|yields?|bonds?|"
    r"crude|energy|yen|euro|currency|china|iran|russia|ukraine|military|strike)\b", re.I)

# Single-name equity stories. The note above says a firehose of these is noise to
# someone trading ES, but "earnings" and "guidance" in _RELEVANT let them through
# anyway — "Linde's post-earnings slide is a buying opportunity" is not an index
# headline. Demoted rather than dropped: sometimes a mega-cap IS the index story.
_SINGLE_NAME = re.compile(
    r"\b(shares?|stock)\s+(jump|slid|slide|fall|fell|rise|rose|drop|surg|plung|gain|sink|"
    r"soar|tumbl|climb|slump)|post-earnings|buying opportunity|here'?s why|takeaways", re.I)

_MAX_AGE_HOURS = 120       # five days — older than that is not news before a bell


def _headline_tier(title: str) -> int:
    """1 moves the index, 2 is market-wide colour, 3 is everything else.

    A named function rather than three lines inside the merge loop so the rule
    can be tested directly. Tiering was wrong once already — a blanket demotion
    put "Fed decision sends bank shares soaring" under a stock tip — and a test
    that reimplements the rule instead of calling it would not have caught it.
    """
    tier = 1 if _TIER1.search(title) else (2 if _TIER2.search(title) else 3)
    # Demote single-name stories, but never past a policy headline: a Fed story
    # that happens to mention shares is still the Fed story.
    if tier > 1 and _SINGLE_NAME.search(title):
        tier = 3
    return tier


def _last_cash_close(now: pd.Timestamp) -> pd.Timestamp:
    """The most recent 16:00 ET that has already passed on a weekday.

    "What happened since I stopped watching" is a SESSION question, not a clock
    one. A fixed hours-ago bucket calls Friday afternoon's news 'earlier' when
    read on Monday morning — 65 hours old, and also the single most recent thing
    that happened. Anchoring on the prior cash close gets the weekend right.

    Holidays shift this by a day, which mislabels rather than misinforms: the
    hours-ago figure travels alongside and is exact either way.
    """
    t = now.tz_convert(_TZ) if now.tzinfo else now.tz_localize(_TZ)
    day = t.normalize()
    if t < day + pd.Timedelta(hours=16):
        day -= pd.Timedelta(days=1)
    while day.weekday() >= 5:
        day -= pd.Timedelta(days=1)
    return day + pd.Timedelta(hours=16)


def _parse_feed(name_url: tuple[str, str], limit: int) -> list[dict]:
    source, url = name_url
    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=12)
        if r.status_code != 200:
            return []
        items = re.findall(r"<item[^>]*>(.*?)</item>", r.text, re.S | re.I)[: limit * 3]
        out = []
        for raw in items:
            t = re.search(r"<title[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", raw, re.S | re.I)
            # CDATA-aware, like the title above. The Federal Reserve wraps its
            # dates — <pubDate><![CDATA[Fri, 31 Jul 2026 14:00:00 GMT]]></pubDate>
            # — so the bare pattern captured the wrapper, the parse failed, and
            # EVERY Fed headline came through undated. Undated then sorted to
            # last, which put the most index-relevant source on the page at the
            # bottom of it, with no way to tell an FOMC statement from today
            # apart from one three months old.
            d = re.search(r"<pubDate[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</pubDate>",
                          raw, re.S | re.I)
            l = re.search(r"<link[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</link>", raw, re.S | re.I)
            if not t:
                continue
            title = html.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip()
            if not title or not _RELEVANT.search(title):
                continue
            published = None
            if d:
                try:
                    published = pd.to_datetime(d.group(1), errors="coerce")
                except Exception:
                    published = None
            out.append({
                "source": source,
                "title": title,
                "url": (l.group(1).strip() if l else None),
                "published": published.isoformat() if published is not None and not pd.isna(published) else None,
            })
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        logger.warning(f"feed {source} failed: {e}")
        return []


def macro_news(limit_per_feed: int = 6, now: pd.Timestamp | None = None) -> list[dict]:
    """Macro-relevant headlines, ranked by how much they move the index.

    Read before the bell, so the ordering question is "what matters, of what has
    happened since I last looked" — not "what was published most recently". A
    market-colour piece from an hour ago should not outrank an FOMC statement
    from yesterday afternoon, which is what a pure recency sort does.
    """
    now = now or pd.Timestamp.now(tz=_TZ)
    last_close = _last_cash_close(now)
    with ThreadPoolExecutor(max_workers=len(_FEEDS)) as pool:
        batches = list(pool.map(lambda f: _parse_feed(f, limit_per_feed), _FEEDS))

    seen: set[str] = set()
    merged: list[dict] = []
    for b in batches:
        for item in b:
            # Dedupe on a normalised title — the same wire story runs across
            # several of these feeds with slightly different punctuation.
            k = re.sub(r"[^a-z0-9]+", "", item["title"].lower())[:70]
            if k in seen:
                continue
            seen.add(k)

            tier = _headline_tier(item["title"])

            hours, stamped = None, None
            if item.get("published"):
                try:
                    ts = pd.to_datetime(item["published"])
                    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
                    stamped = ts.tz_convert(_TZ)
                    hours = (now - stamped).total_seconds() / 3600
                except Exception:
                    hours, stamped = None, None
            # Stale is not news. An undated item is KEPT — it is a parsing
            # failure, not an old story, and silently dropping it is how a feed
            # goes dark without anyone noticing.
            if hours is not None and hours > _MAX_AGE_HOURS:
                continue

            item["tier"] = tier
            item["hours_ago"] = round(hours, 1) if hours is not None else None
            # Measured against the prior cash close, so on a Monday the whole
            # weekend reads as "since the last close" rather than as three
            # separate degrees of old.
            item["age"] = (None if stamped is None else
                           "since last close" if stamped >= last_close else "earlier")
            merged.append(item)

    # A feed that contributes nothing is worth a line in the log. Both
    # MarketWatch feeds failed this way — one filtered to zero, the other went a
    # year stale behind an HTTP 200 — and neither announced itself.
    contributing = {x["source"] for x in merged}
    for source, _ in _FEEDS:
        if source not in contributing:
            logger.warning(f"news feed '{source}' contributed nothing — "
                           "check whether it is stale, filtered out, or down")

    # Importance first, then recency within it. An undated item sorts as if it
    # were a day old — middling, not last — so a broken date parser can never
    # again bury a Fed release at the bottom of the page.
    merged.sort(key=lambda x: (x["tier"],
                               x["hours_ago"] if x["hours_ago"] is not None else 24.0))
    return merged[:20]


def es_session_brief() -> dict:
    """Everything the top-of-page briefing needs, in one call."""
    now = pd.Timestamp.now(tz=_TZ)
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_news = pool.submit(macro_news)
        f_sched = pool.submit(todays_schedule, now)
        news = f_news.result()
        schedule = f_sched.result()

    upcoming = [e for e in schedule if e["status"] == "upcoming"]
    next_event = min(upcoming, key=lambda e: e["minutes_away"]) if upcoming else None

    session_day = trading_session_day(now)
    return {
        "available": True,
        "asof": now.isoformat(),
        "session": current_phase(now),
        # Which session the schedule describes. In the evening this is
        # tomorrow's, matching where the levels are anchored.
        "session_day": str(session_day.date()),
        "schedule_is_today": session_day.date() == now.date(),
        "schedule": schedule,
        "next_event": next_event,
        "high_impact_today": [e for e in schedule if e["impact"] == "high"],
        "news": news,
    }
