"""r/wallstreetbets ticker-mention scraper.

Pure-Python, no OAuth — hits Reddit's public `.json` endpoints with a
descriptive User-Agent. Extracts ticker symbols from post titles, self-text,
and top comments, and produces a ranked list of tickers with mention count,
bull/bear sentiment, and calls-vs-puts lean.

Reddit's public API rate-limits unauthenticated traffic at ~60 req/min per
IP. This scraper makes ≤ 10 requests per full scrape cycle; safe to run
hourly without rate-limit concern.

Usage:
    from src.wsb_scraper import scan_wsb
    result = scan_wsb()  # {'tickers': [...], 'as_of': '...'}
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable

import requests

logger = logging.getLogger(__name__)

# Subreddits to crawl. r/wallstreetbets is the flagship; r/options pulls in
# more sophisticated options talk. We weight WSB mentions more heavily in
# the aggregation since it's noisier but higher-volume.
SUBREDDIT_CONFIGS = [
    {"name": "wallstreetbets", "weight": 1.0, "listings": ["hot", "new"]},
    {"name": "options", "weight": 0.7, "listings": ["hot"]},
    {"name": "stocks", "weight": 0.5, "listings": ["hot"]},
]

# User-Agent is mandatory for Reddit's public endpoints; plain requests UA
# gets 429'd. Use a descriptive identifier per their API etiquette.
_UA = "aistatcharts.com/1.0 (financial research platform)"

# Filter uppercase tokens against these — otherwise common English words,
# abbreviations, and subreddit shorthand get counted as "tickers".
# This list is intentionally aggressive — false positives hurt signal quality
# more than false negatives hurt coverage, since missing a real ticker just
# means it needs a `$` prefix to register (see _TICKER_RE below).
_TICKER_BLACKLIST = {
    # Articles, prepositions, common short words
    "A", "I", "THE", "AND", "FOR", "BUT", "YOU", "ARE", "ALL", "NOT", "WAS",
    "YOUR", "OUR", "CAN", "WILL", "THIS", "WITH", "HAVE", "HAD", "HAS",
    "BEEN", "FROM", "THEY", "THEM", "THEIR", "BOTH", "EACH", "THESE", "THOSE",
    "WHEN", "WHAT", "LIKE", "JUST", "OUT", "NOW", "HERE", "GOT", "GET", "GIVE",
    "MY", "SO", "NO", "OR", "IF", "BE", "TO", "US", "AM", "PM", "AN", "ON",
    "AT", "IN", "IS", "IT", "OF", "BY", "AS", "DO", "GO", "UP", "HE", "SHE",
    "WE", "ME", "HI", "OH", "AH", "UM",
    # Common English nouns/verbs that get capitalized in titles
    "THANK", "THANKS", "SHARE", "SHARES", "PRICE", "PRICES", "TODAY", "YESTERDAY",
    "TOMORROW", "WEEK", "WEEKS", "MONTH", "MONTHS", "YEAR", "YEARS", "HOUR", "HOURS",
    "DAY", "DAYS", "TIME", "TIMES", "ONCE", "TWICE", "MONEY", "BACK", "MADE", "MAKE",
    "TAKE", "TOOK", "TAKEN", "KEEP", "HOLD", "HELD", "GIVE", "GAVE", "GIVEN",
    "SAY", "SAID", "SAYS", "NAME", "GAME", "GAMES", "LOTS", "LOT", "TEXT",
    "SIDE", "SIZE", "RIGHT", "LEFT", "HUGE", "BIG", "SMALL", "TINY",
    "EVEN", "ODD", "ALSO", "AGAIN", "AGO", "EVER", "NEVER", "ALWAYS",
    "SOMETIMES", "OFTEN", "RARELY", "SELDOM",
    "GOOD", "BAD", "HARD", "EASY", "LAST", "FIRST", "BEST", "WORST", "FREE",
    "FULL", "HALF", "REAL", "FAKE", "TRUE", "FALSE", "SURE", "WELL", "ONLY",
    "VERY", "QUITE", "LESS", "MORE", "MOST", "LEAST", "MANY", "FEW", "TOO",
    "NEW", "OLD", "HIGH", "LOWER", "HIGHER", "UPPER", "LOWER",
    "OVER", "UNDER", "AFTER", "BEFORE", "SINCE", "DURING", "WHILE", "WHERE",
    "HOW", "WHY", "WHO", "WHOM", "WHICH",
    "BEING", "DONE", "WENT", "COME", "CAME", "SEEN", "SEEM", "SEEMS", "SEEMED",
    "SAME", "SUCH", "ANY", "SOME", "EVERY", "MUCH", "EACH",
    "NEWS", "POST", "POSTS", "LOGO", "LINK", "LINKS", "HERE", "THERE",
    "SUPER", "UPPER", "REALLY", "PRETTY", "STILL", "EVER",
    # Finance-specific abbreviations + index tickers (usually noise in WSB context)
    "YOLO", "HODL", "DD", "FOMO", "YTD", "MOM", "QOQ", "FUD", "TLDR", "OP", "WSB",
    "ETF", "ETFS", "IPO", "EPS", "PE", "PEG", "ROE", "ROA", "ROI", "COGS",
    "CEO", "CFO", "COO", "CTO", "SEC", "FTC", "FDA", "DOJ", "IRS", "USD",
    "USA", "EST", "PT", "GMT", "UTC",
    "Q1", "Q2", "Q3", "Q4", "H1", "H2",
    "CALL", "CALLS", "PUT", "PUTS", "LONG", "SHORT", "BUY", "SELL",
    "ATH", "ATL", "ITM", "OTM", "ATM",
    "TOP", "FLOP", "DROP",
    "NYSE", "NASDAQ", "DOW", "SP", "SPX", "VIX", "RUT",
    "CHINA", "RUSSIA", "INDIA", "JAPAN", "GERMANY", "FRANCE", "UK",
    "FED", "CPI", "GDP", "PPI", "PCE", "FOMC", "NFP",
    "YES", "OK", "WTF", "LOL", "LMAO", "OMG", "OMFG", "FFS", "SMH",
    "IMO", "IMHO", "AFAIK", "IIRC", "BTW", "FYI", "TIL",
    # Common ambiguous 2-letter words or symbols that aren't tickers
    "AI", "HR", "PR", "OK",   # AI especially — always the tech concept, never the ticker
    "INTEL",  # the word "intelligence"; use "$INTC" or "INTC" (the actual ticker) to reference the company
    "AMEX",
    # News outlets, government acronyms, WSB slang, profanity
    "CNBC", "CNN", "BBC", "WSJ", "FT", "BLOOMBERG",
    "IEEPA", "NATO", "EU", "WTO", "IMF", "OPEC", "NAFTA", "USMCA",
    "LFG", "LFGGG", "KEKW", "BRUH", "DUMB", "RETARDS", "APES", "JACKED",
    "FUCK", "SHIT", "ASS", "DAMN", "HELL", "FUCKED", "FUCKING",
    "ZERO", "ONE", "TWO", "TEN",  # numbers as words
    "IRA", "ROTH", "HSA", "FSA",  # retirement accounts
    "LLC", "INC", "CORP", "LTD", "CO",
    "EOY", "EOD", "BOD", "YTD", "MTD", "WTD",
    "API", "SDK", "UI", "UX",
}

# Ticker regex — 1-5 uppercase letters, optionally prefixed with "$".
# 2-letter tokens need a `$` prefix to count — too many English false positives
# otherwise. 3+ letter tokens fall through to the blacklist filter.
_TICKER_RE = re.compile(r"\$?\b([A-Z]{2,5})\b")
_DOLLAR_PREFIXED_RE = re.compile(r"\$([A-Z]{1,5})\b")

# Sentiment keywords — weighted by intensity.
_BULL_WORDS = {
    "call": 1, "calls": 1, "long": 1, "buy": 1, "moon": 2, "rocket": 2,
    "squeeze": 2, "hodl": 1, "bull": 1, "bullish": 1, "pump": 1,
    "gamma": 1, "rip": 1, "rally": 1, "breakout": 1, "green": 1,
    "lambo": 2, "tendies": 2, "diamond": 1, "gain": 1, "gains": 1,
    "🚀": 3, "🌕": 2, "💎": 2, "🙌": 1, "📈": 2,
}
_BEAR_WORDS = {
    "put": 1, "puts": 1, "short": 1, "sell": 1, "crash": 2, "dump": 2,
    "tank": 2, "bear": 1, "bearish": 1, "drill": 2, "collapse": 2,
    "dead": 1, "bag": 1, "loss": 1, "losses": 1, "red": 1, "drop": 1,
    "bagholder": 2,
    "📉": 2, "💀": 2, "🩸": 2, "🗑": 1,
}

# Options-talk keywords — we count separately from pure bull/bear to
# produce the Options Talk tab.
_CALL_RE = re.compile(r"\b(calls?|long call)\b", re.IGNORECASE)
_PUT_RE = re.compile(r"\b(puts?|long put|short put)\b", re.IGNORECASE)


def _fetch_listing(subreddit: str, sort: str, limit: int = 50) -> list[dict]:
    """One Reddit `.json` request. Returns the list of child posts."""
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    try:
        r = requests.get(
            url,
            params={"limit": limit, "raw_json": 1},
            headers={"User-Agent": _UA},
            timeout=10,
        )
        if r.status_code == 429:
            logger.warning(f"reddit rate-limited on {url}")
            return []
        r.raise_for_status()
        return [c.get("data", {}) for c in r.json().get("data", {}).get("children", [])]
    except Exception as e:
        logger.debug(f"reddit fetch failed for {subreddit}/{sort}: {e}")
        return []


def _fetch_comments(subreddit: str, post_id: str, limit: int = 30) -> str:
    """Top-level comments for a single post, concatenated into one string."""
    url = f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    try:
        r = requests.get(
            url,
            params={"limit": limit, "depth": 1, "raw_json": 1},
            headers={"User-Agent": _UA},
            timeout=10,
        )
        if r.status_code != 200:
            return ""
        body = r.json()
        if not isinstance(body, list) or len(body) < 2:
            return ""
        comment_listing = body[1].get("data", {}).get("children", [])
        out: list[str] = []
        for c in comment_listing:
            data = c.get("data", {}) or {}
            text = data.get("body", "")
            if text and text != "[deleted]":
                out.append(text)
        return "\n".join(out)
    except Exception as e:
        logger.debug(f"reddit comments fetch failed for {post_id}: {e}")
        return ""


def _extract_tickers(text: str) -> set[str]:
    """Return the set of plausible tickers in a chunk of text.

    Rules:
      - `$TICKER` prefix always counts (explicit ticker marker on Reddit).
      - 3-5 letter uppercase tokens count unless blacklisted.
      - 2-letter uppercase tokens only count when `$`-prefixed — raw 2-letter
        uppercase words are overwhelmingly English (AI, HR, OK, etc.) not
        tickers. The tradeoff: miss F/T/GE mentions without `$`, avoid
        hundreds of "AI" false positives.
      - Guard against pathological input (>10KB of text per chunk) by
        capping — one runaway copy-paste post shouldn't dominate the scan.
    """
    if not text:
        return set()
    if len(text) > 10_000:
        text = text[:10_000]
    found: set[str] = set()
    # Pass 1: `$X...` explicit markers — always count (length 1-5)
    for match in _DOLLAR_PREFIXED_RE.finditer(text):
        tok = match.group(1).upper()
        if tok not in _TICKER_BLACKLIST:
            found.add(tok)
    # Pass 2: bare ALL-CAPS 3-5 letter tokens — blacklist-filtered
    for match in _TICKER_RE.finditer(text):
        tok = match.group(1).upper()
        if len(tok) < 3:
            continue  # 2-letter bare tokens require $ prefix (handled above)
        if tok in _TICKER_BLACKLIST:
            continue
        found.add(tok)
    return found


def _score_sentiment(text: str) -> tuple[int, int]:
    """Count bull + bear keyword hits in `text`. Returns (bull, bear)."""
    lower = text.lower()
    bull = sum(
        lower.count(word) * weight
        for word, weight in _BULL_WORDS.items()
    )
    bear = sum(
        lower.count(word) * weight
        for word, weight in _BEAR_WORDS.items()
    )
    return bull, bear


def _count_options_mentions(text: str) -> tuple[int, int]:
    """Return (calls_count, puts_count) in this text."""
    return len(_CALL_RE.findall(text)), len(_PUT_RE.findall(text))


def scan_wsb(
    include_comments: bool = True,
    comments_top_n: int = 10,
    min_mentions: int = 2,
) -> dict:
    """Scrape the configured subreddits and return ranked ticker mentions.

    Args:
        include_comments: Fetch top-level comments for the top N posts per
            listing. Adds 10 extra requests and meaningfully improves signal
            quality (titles alone miss context).
        comments_top_n: How many top posts per listing to pull comments for.
        min_mentions: Drop tickers with fewer than this many total mentions.

    Returns a dict:
        {
            "as_of_utc": ISO 8601,
            "subreddits_scanned": [...],
            "post_count": int,
            "tickers": [
                {
                    "ticker": "GME",
                    "mentions": 42,
                    "upvote_weighted": 1250,
                    "bull_score": 28, "bear_score": 5, "sentiment": 0.82,
                    "calls_mentions": 12, "puts_mentions": 2, "options_lean": "calls",
                    "dd_posts": 3,    # posts with DD flair mentioning this ticker
                    "top_post": { "title": "...", "url": "...", "ups": 1500 }
                },
                ...
            ]
        }
    """
    agg: dict[str, dict] = defaultdict(lambda: {
        "ticker": "",
        "mentions": 0,
        "upvote_weighted": 0,
        "bull_score": 0,
        "bear_score": 0,
        "calls_mentions": 0,
        "puts_mentions": 0,
        "dd_posts": 0,
        "top_post": None,
        "_top_ups": -1,
    })

    subreddits_scanned: list[str] = []
    total_posts = 0

    for cfg in SUBREDDIT_CONFIGS:
        sub = cfg["name"]
        weight = cfg["weight"]
        subreddits_scanned.append(sub)
        for sort in cfg["listings"]:
            posts = _fetch_listing(sub, sort, limit=50)
            time.sleep(1.2)  # polite gap between reddit hits
            if not posts:
                continue
            total_posts += len(posts)

            # Identify top-N posts per listing for comment fetches
            top_ids: Iterable[str] = ()
            if include_comments:
                ranked = sorted(posts, key=lambda p: p.get("ups", 0) or 0, reverse=True)
                top_ids = [p.get("id") for p in ranked[:comments_top_n] if p.get("id")]

            comment_bodies: dict[str, str] = {}
            for pid in top_ids:
                comment_bodies[pid] = _fetch_comments(sub, pid, limit=20)
                time.sleep(1.2)

            for post in posts:
                title = post.get("title", "") or ""
                selftext = post.get("selftext", "") or ""
                ups = post.get("ups", 0) or 0
                pid = post.get("id", "")
                url = post.get("url", "") or post.get("permalink", "")
                flair = (post.get("link_flair_text") or "").lower()
                is_dd = "dd" in flair or "due diligence" in flair

                comments = comment_bodies.get(pid, "")
                full_text = f"{title}\n{selftext}\n{comments}"

                tickers = _extract_tickers(title) | _extract_tickers(selftext)
                if comments:
                    tickers |= _extract_tickers(comments)
                if not tickers:
                    continue

                bull, bear = _score_sentiment(full_text)
                calls, puts = _count_options_mentions(full_text)

                for t in tickers:
                    row = agg[t]
                    row["ticker"] = t
                    row["mentions"] += 1
                    row["upvote_weighted"] += int(ups * weight)
                    row["bull_score"] += int(bull * weight)
                    row["bear_score"] += int(bear * weight)
                    row["calls_mentions"] += int(calls * weight)
                    row["puts_mentions"] += int(puts * weight)
                    if is_dd:
                        row["dd_posts"] += 1
                    if ups > row["_top_ups"]:
                        row["_top_ups"] = ups
                        row["top_post"] = {
                            "title": title[:140],
                            "url": f"https://reddit.com{post.get('permalink', '')}" if post.get("permalink") else url,
                            "ups": ups,
                            "subreddit": sub,
                            "flair": post.get("link_flair_text") or "",
                        }

    # Finalize: compute sentiment + options_lean + drop strings we don't emit
    out_rows = []
    for t, row in agg.items():
        if row["mentions"] < min_mentions:
            continue
        row.pop("_top_ups", None)
        bull, bear = row["bull_score"], row["bear_score"]
        total = bull + bear
        row["sentiment"] = round((bull - bear) / total, 2) if total else 0.0
        co, pu = row["calls_mentions"], row["puts_mentions"]
        if co + pu == 0:
            row["options_lean"] = "neutral"
        elif co > pu * 1.5:
            row["options_lean"] = "calls"
        elif pu > co * 1.5:
            row["options_lean"] = "puts"
        else:
            row["options_lean"] = "mixed"
        out_rows.append(row)

    # Rank by upvote-weighted mentions (richer than plain count)
    out_rows.sort(key=lambda r: (r["upvote_weighted"], r["mentions"]), reverse=True)

    return {
        "as_of_utc": datetime.now(timezone.utc).isoformat(),
        "subreddits_scanned": subreddits_scanned,
        "post_count": total_posts,
        "tickers": out_rows[:50],
    }
