"""How much of the market is above its own 50- and 200-day average.

WHAT THIS ADDS THAT `es_breadth` DOES NOT. Every measure in that module —
advance/decline, up/down volume, TRIN, equal-vs-cap — answers one question: is
TODAY broad. None of them answers whether the market is broadly in an uptrend.
Those diverge in exactly the situation that module's own docstring describes: an
index carried by a handful of megacaps. Net advancers catches that within a
session; the share of names above their 200-day catches it as a regime, and a
market can be green on the day with a majority of its names below trend.

It is also the one measure on Finviz's front page worth taking. Everything else
there is single-name discovery — gainers, unusual volume, insider buys, pattern
scans — which is noise for someone trading one index future.

WHY IT STREAMS RATHER THAN BUILDING A PANEL. A 200-session moving average over
~12,000 tickers is ~2.4M (ticker, close) pairs, which is a real amount of memory
on a Cloud Run instance sized for request handling. Only two numbers per ticker
are actually needed — the running sum of the last 50 closes and of the last 200
— so the days are walked newest-first and folded into two dicts, and each frame
is dropped as soon as it is folded. Peak memory is one day's frame plus two
float maps, regardless of lookback.

That last part is only true because the walk passes `cache=False`. It was not
true when this was written: `es_breadth`'s grouped cache is unbounded, so the
first version left 209 frames and 2,412,389 rows resident for the six-hour TTL
while claiming in this docstring that it did not.

THE UNIVERSE IS THE SAME CHOICE `es_breadth` MAKES, and for the same reason:
counting all 12,000-odd US tickers dilutes the reading with names that barely
trade. The prior session's dollar volume is the filter, so it is fixed for the
whole day and the universe cannot drift intraday. The number is not "the NYSE
percentage above its 200-day"; it is this universe's, and it says so.

NO PERCENTILE ON DAY ONE. 52% above the 200-day is a level, and a level with no
reference set is a fact about nothing. Rather than backfill — which would cost
another 200 grouped fetches per historical point — this records one observation
per session day and reports `pctile: None` until 60 have accumulated, using the
same routine as the vol history so there is one implementation of the rule.
"""

from __future__ import annotations

import logging
from datetime import date as _date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_KEY = "breadth_trend_history_v1"
_MIN_HISTORY = 60
TRACKED = ("pct_above_50dma", "pct_above_200dma")

#: Windows in trading days. 50 and 200 are the conventional cuts and the ones
#: every other desk quotes, which is the point — a non-standard window would not
#: be comparable to anything a reader has seen elsewhere.
_WINDOWS = (50, 200)

#: Calendar days to walk back to collect 200 trading days. Measured: 200
#: sessions spanned 293 calendar days from 2026-08-30. 300 left seven days of
#: margin, which one extra market holiday would eat — and running short does not
#: degrade gracefully, it returns `available: False`. 340 is still bounded and
#: costs nothing extra, because the walk stops on the session count.
_CALENDAR_SPAN = 340

_CACHE: dict = {}
_TTL_S = 12 * 3600


def _accumulate(anchor: _date, max_window: int):
    """Fold grouped-daily frames newest-first into per-ticker running sums.

    Returns (latest_close, latest_vol, sums, counts, sessions_used, first_day).
    `sums[w]` maps ticker -> sum of its last `w` closes; `counts[w]` maps ticker
    -> how many of those closes actually existed, so a name that listed 80 days
    ago is excluded from the 200-day figure rather than averaged over 80.
    """
    from src.es_breadth import _grouped

    sums = {w: {} for w in _WINDOWS}
    counts = {w: {} for w in _WINDOWS}
    latest_close = latest_vol = None
    sessions = 0
    first_day = None
    d = anchor

    while sessions < max_window and (anchor - d).days < _CALENDAR_SPAN:
        if d.weekday() < 5:                      # cheap skip; holidays return empty
            # cache=False: see `_grouped`. Caching 200 frames would pin ~2.4M
            # rows for six hours to serve a read that does not come again,
            # which is exactly the memory this streaming design avoids.
            df = _grouped(d.isoformat(), cache=False)
            if not df.empty:
                if latest_close is None:
                    latest_close = df["close"]
                    latest_vol = df["vol"]
                sessions += 1
                first_day = d.isoformat()
                closes = df["close"]
                for w in _WINDOWS:
                    if sessions <= w:
                        s, c = sums[w], counts[w]
                        for tk, px in closes.items():
                            s[tk] = s.get(tk, 0.0) + float(px)
                            c[tk] = c.get(tk, 0) + 1
        d -= timedelta(days=1)

    return latest_close, latest_vol, sums, counts, sessions, first_day


def trend_breadth(anchor: _date | None = None, force: bool = False) -> dict:
    """Share of the liquid universe trading above its 50- and 200-day average.

    Cached 12h: the inputs are daily bars and the answer changes once a session.
    """
    from time import time as _now

    # Keyed on the anchor. Without this an explicit `anchor=` would be handed
    # the cached default-anchor answer — a caller asking about a specific
    # session getting a different one, silently.
    ck = (anchor or _last_completed_session()).isoformat()
    hit = _CACHE.get(ck)
    if hit and not force and (_now() - hit[0]) < _TTL_S:
        return hit[1]

    if not force:
        try:
            from src._cache_util import _supabase_get
            got = _supabase_get(f"breadth_trend:v1:{ck}")
            if got:
                updated, value = got
                if ((datetime.utcnow() - updated).total_seconds() < _TTL_S
                        and isinstance(value, dict) and value.get("available")):
                    _CACHE[ck] = (_now(), value)
                    return value
        except Exception as e:
            logger.debug(f"trend breadth cache read failed: {e}")

    try:
        out = _compute(anchor)
    except Exception as e:
        logger.warning(f"trend breadth failed: {e}")
        return {"available": False, "reason": str(e)}

    if out.get("available"):
        _CACHE[ck] = (_now(), out)
        try:
            from src._cache_util import _supabase_put
            _supabase_put(f"breadth_trend:v1:{ck}", out)
        except Exception as e:
            logger.debug(f"trend breadth cache write failed: {e}")
    return out


def _last_completed_session(now=None) -> _date:
    """The most recent date whose cash session has finished.

    THE WALK MUST NOT START ON A SESSION IN PROGRESS. Polygon's grouped-daily
    endpoint serves the current day's aggregate while the market trades, so
    anchoring on today during RTH would take a PARTIAL close and a PARTIAL
    volume as the newest bar. The volume is the liquidity filter, so the
    universe would shrink at the open and grow through the day, and the
    percentage above the 200-day would drift for a purely mechanical reason —
    which is the exact failure `es_breadth` documents and guards against for its
    own filter. This measure is defined on completed closes and is recorded once
    per session day, so waiting for the close costs nothing it needs.

    16:15 ET rather than 16:00: the consolidated tape prints late, and a bar
    read at 16:00:30 is not reliably final.
    """
    import pandas as pd
    now = now or pd.Timestamp.now(tz="America/New_York")
    d = now.date()
    if now.hour * 60 + now.minute < 16 * 60 + 15:
        d -= timedelta(days=1)
    return d


def _compute(anchor: _date | None = None) -> dict:
    from src.es_breadth import _MIN_DOLLAR_VOL

    anchor = anchor or _last_completed_session()
    close, vol, sums, counts, sessions, first_day = _accumulate(anchor, max(_WINDOWS))

    if close is None or sessions < min(_WINDOWS):
        return {"available": False,
                "reason": f"only {sessions} sessions of daily bars available; "
                          f"{min(_WINDOWS)} needed for the shortest average"}

    # Same liquidity filter as the rest of the breadth module, on the latest
    # session's dollar volume. A universe of everything is a different number.
    liquid = {tk for tk, px in close.items()
              if float(px) * float(vol.get(tk, 0) or 0) >= _MIN_DOLLAR_VOL}

    out: dict = {
        "available": True,
        "asof": anchor.isoformat(),
        "sessions_used": sessions,
        "from": first_day,
        "universe": {
            "n": len(liquid),
            "note": (f"US tickers whose latest session traded at least "
                     f"${_MIN_DOLLAR_VOL/1e6:.0f}M. Not an exchange's official "
                     f"advance-decline universe — this one's."),
        },
        "windows": {},
    }

    for w in _WINDOWS:
        s, c = sums[w], counts[w]
        above = below = 0
        for tk in liquid:
            # A name must have the FULL window of history. Averaging a 200-day
            # over 80 days would put newly-listed names on a different measure
            # and quietly shift the percentage.
            if c.get(tk, 0) < w:
                continue
            ma = s[tk] / w
            px = float(close[tk])
            if px > ma:
                above += 1
            elif px < ma:
                below += 1
        n = above + below
        # STRING KEY, deliberately. A JSON round-trip through the Supabase cache
        # turns int keys into strings, so a fresh compute and a cached read would
        # otherwise hand callers dicts keyed differently depending on cache
        # state — the kind of difference that surfaces weeks later as a
        # KeyError on one code path and not the other.
        out["windows"][str(w)] = {
            "above": above,
            "below": below,
            "n": n,
            "pct_above": round(above / n * 100, 1) if n else None,
            # Names dropped for insufficient history — stated, because a
            # shrinking denominator is how a percentage moves for a reason that
            # has nothing to do with the market.
            "excluded_short_history": len(liquid) - n,
        }

    out["pct_above_50dma"] = out["windows"]["50"]["pct_above"]
    out["pct_above_200dma"] = out["windows"]["200"]["pct_above"]

    # Record forward and place today against what came before. Shares the vol
    # history routine rather than copying it — see that module.
    try:
        from src.vol_history import percentiles, record
        rows = record(out, session_date=anchor, healthy=True,
                      key=_KEY, tracked=TRACKED)
        out["history"] = percentiles(rows, out, session_date=anchor,
                                     tracked=TRACKED, min_history=_MIN_HISTORY)
    except Exception as e:
        logger.debug(f"trend breadth history failed: {e}")
        out["history"] = {}

    return out


def prewarm() -> None:
    """Called from the API's startup warm-up: ~200 grouped-daily fetches cold."""
    try:
        b = trend_breadth()
        if b.get("available"):
            logger.info(f"Trend breadth pre-warmed ({b['universe']['n']} names, "
                        f"{b['sessions_used']} sessions)")
        else:
            logger.warning(f"Trend breadth pre-warm unavailable: {b.get('reason')}")
    except Exception as e:
        logger.warning(f"Trend breadth pre-warm failed: {e}")
