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
                               "Largest single-print ES range expansion of a typical month.")),
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


def current_phase(now: pd.Timestamp | None = None) -> dict:
    """Which session we're in, and what that implies for behaviour."""
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)
    weekday = now.weekday()          # 0=Mon .. 6=Sun
    t = now.time()

    # ES is closed 17:00-18:00 ET daily and all weekend until Sunday 18:00.
    closed = (weekday == 5) or (weekday == 6 and t < dtime(18, 0)) or (dtime(17, 0) <= t < dtime(18, 0))
    if closed:
        return {"phase": "closed", "label": "Market closed",
                "note": "ES is closed. Reopens Sunday 18:00 ET." if weekday >= 5
                        else "Daily maintenance break, 17:00–18:00 ET.",
                "is_rth": False, "now": now.isoformat()}

    for phase, start, end, note in _PHASES:
        if start <= t < end:
            return {
                "phase": phase,
                "label": phase.replace("_", " ").title(),
                "note": note,
                "is_rth": phase.startswith("rth"),
                "now": now.isoformat(),
            }
    return {"phase": "overnight", "label": "Overnight", "note": "Globex session.",
            "is_rth": False, "now": now.isoformat()}


def todays_schedule(now: pd.Timestamp | None = None) -> list[dict]:
    """Today's scheduled releases with clock times, marked done or upcoming."""
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)

    try:
        # find_events_near_date is the calendar's actual entry point; it returns
        # {name, date, ...} within a window. Window of 1 so we only get today.
        from src.economic_calendar import find_events_near_date
        events = find_events_near_date(now.strftime("%Y-%m-%d"), window_days=1) or []
    except Exception as e:
        logger.warning(f"calendar fetch failed: {e}")
        events = []

    out: list[dict] = []
    today = now.date()
    for ev in events:
        name = ev.get("name") or ev.get("event") or ""
        raw_date = ev.get("date")
        if not raw_date:
            continue
        try:
            d = pd.to_datetime(raw_date).date()
        except Exception:
            continue
        if d != today:
            continue

        rel = _match_release(name)
        hh, mm = (rel.hour, rel.minute) if rel else (8, 30)
        when = pd.Timestamp.combine(pd.Timestamp(d), dtime(hh, mm)).tz_localize(_TZ)
        mins = int((when - now).total_seconds() // 60)
        out.append({
            "name": rel.name if rel and rel.name else name,
            "time_et": f"{hh:02d}:{mm:02d}",
            "impact": rel.impact if rel else "low",
            "note": rel.note if rel else "",
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
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
]

# Headlines that actually move the index, rather than everything published.
_RELEVANT = re.compile(
    r"\b(fed|fomc|powell|rate cut|rate hike|inflation|cpi|pce|payroll|jobs|unemployment|"
    r"jobless|gdp|recession|tariff|treasury|yield|earnings|guidance|s&p|nasdaq|stocks|"
    r"selloff|rally|dollar|oil|hawkish|dovish)\b", re.I)


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
            d = re.search(r"<pubDate[^>]*>(.*?)</pubDate>", raw, re.S | re.I)
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


def macro_news(limit_per_feed: int = 6) -> list[dict]:
    """Macro-relevant headlines across the free feeds, newest first."""
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
            merged.append(item)

    merged.sort(key=lambda x: x["published"] or "", reverse=True)
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

    return {
        "available": True,
        "asof": now.isoformat(),
        "session": current_phase(now),
        "schedule": schedule,
        "next_event": next_event,
        "high_impact_today": [e for e in schedule if e["impact"] == "high"],
        "news": news,
    }
