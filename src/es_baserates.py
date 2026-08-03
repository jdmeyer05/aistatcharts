"""Measured base rates for the claims the briefing makes.

The rest of this cockpit describes the session. This module is the only part
that says how often a thing has actually happened — gap fills, prior-range
breaks, how wide a day usually gets, and how much bigger a CPI or payrolls
session runs than a normal one.

WHY THIS EXISTS. "Outside value tends to trend", "gaps fill", "the overnight
high gets tested" — all standard, all repeated everywhere, and none of it
carries a number. A base rate turns a slogan into a probability you can size
against, and it occasionally shows the slogan is wrong.

WHY THE CASH INDEX AND NOT ES. Gap statistics need the 09:30 open against the
prior 16:00 close. `ES=F` daily bars open at the 18:00 GLOBEX open — which is
minutes after the prior close, so the "gap" it implies is near zero and the
whole study would be meaningless. Verified on the same date: cash opened at
7462.13 where the ES daily bar opened 7479.50. So everything here is measured
on ^GSPC, where Open and Close ARE the cash session, then expressed as
percentages and converted to ES handles at the current price. ES tracks SPX
closely enough intraday that a same-day percentage carries across; the absolute
levels do not, and nothing here quotes one.

WHAT THESE ARE NOT. Unconditional frequencies over a fixed window. They are not
a forecast, they take no account of the regime you are in, and a 70% base rate
still loses three times in ten. They are a prior to update, not a signal.

FOMC IS ABSENT ON PURPOSE. Release dates come from FRED, which does not carry
FOMC meetings, and the hardcoded calendar only holds forward-looking dates —
so there is no honest history to measure. Better a gap than an invented one.

THE PATH SECTION RUNS ON A SHORTER WINDOW. Everything above is measured on ten
years of daily bars. The intraday path statistics need hourly bars, and Yahoo
serves at most 730 days of them — about 721 complete sessions. That is a sound
sample, but it is a different and much shorter window than the daily study, and
it covers one broad regime rather than several. Both windows are reported.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from datetime import time as _time

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_INDEX = "^GSPC"
_DEFAULT_YEARS = 10
_CACHE: dict = {}
_TTL_S = 12 * 3600

# Hourly RTH buckets. Yahoo aligns these to the cash open, so the 09:30 bar IS
# the initial balance as `es_intraday._IB_MINUTES = 60` defines it — the live
# card and these base rates are therefore measuring the same object. If that
# constant ever changes, this study stops describing what the card shows.
_TZ_NY = "America/New_York"
_OPEN_T = _time(9, 30)
_CLOSE_T = _time(16, 0)
_SLOTS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
_INTRADAY_DAYS = 730          # Yahoo's hard cap for hourly history — fallback only
_INTRADAY_SYMBOL = "SPY"      # Polygon carries no index entitlement; see _hourly
_INTRADAY_YEARS = 5           # Polygon's history horizon on this plan
_INTRADAY_BAR_MIN = 5         # 09:30 sits on the 5-minute grid, so slots are exact
_MAX_PAGES = 60               # next_url pages; a 5y 5-minute pull takes ~20
_PAGE_TIMEOUT_S = 60          # ~12k bars a page; 30s was tight from Cloud Run
_PAGE_RETRIES = 3
_PATH_MIN_SESSIONS = 200

# How far past the IB edge price has to travel before it counts as a break,
# expressed as a fraction of the IB range. A break is not one event: at zero
# buffer it is a coin flip, and by half an IB range it is a different animal
# entirely. Reporting a single "IB break" number hides exactly that.
_BREAK_BUFFERS = [0.0, 0.10, 0.25, 0.50]

# Gap buckets in percent of the prior close. Small gaps behave differently from
# large ones, and lumping them together hides exactly the effect being measured.
_GAP_BUCKETS = [
    (0.0, 0.15, "flat (<0.15%)"),
    (0.15, 0.35, "small (0.15-0.35%)"),
    (0.35, 0.75, "moderate (0.35-0.75%)"),
    (0.75, 99.0, "large (>0.75%)"),
]

# FRED release ids with full history. FOMC deliberately excluded — see above.
_EVENT_RELEASES = [
    (10, "CPI"),
    (50, "Nonfarm payrolls"),
    (54, "PCE"),
    (46, "PPI"),
    (9, "Retail sales"),
]


def _daily(years: int) -> pd.DataFrame:
    """Cash-index daily bars. Cached — this moves once a day at most."""
    from time import time as _now
    hit = _CACHE.get(("daily", years))
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]
    try:
        import yfinance as yf
        h = yf.Ticker(_INDEX).history(period=f"{years + 1}y", interval="1d", auto_adjust=False)
        if h.empty:
            return pd.DataFrame()
        h = h[["Open", "High", "Low", "Close"]].dropna()
        h["prev_close"] = h["Close"].shift(1)
        h["prev_high"] = h["High"].shift(1)
        h["prev_low"] = h["Low"].shift(1)
        h = h.dropna()
        h["gap_pct"] = (h["Open"] - h["prev_close"]) / h["prev_close"] * 100
        h["range_pct"] = (h["High"] - h["Low"]) / h["prev_close"] * 100
        h["body_pct"] = (h["Close"] - h["Open"]) / h["prev_close"] * 100
        _CACHE[("daily", years)] = (_now(), h)
        return h
    except Exception as e:
        logger.warning(f"base-rate history failed: {e}")
        return pd.DataFrame()


def _bucket(gap_pct: float) -> str | None:
    a = abs(gap_pct)
    for lo, hi, label in _GAP_BUCKETS:
        if lo <= a < hi:
            return label
    return None


def gap_base_rates(h: pd.DataFrame, gap_pct: float | None = None) -> dict:
    """How often a gap gets filled, by gap size and direction.

    "Filled" means price traded back to the prior close at some point in the
    SAME cash session — the definition a day trader cares about, not an
    eventual fill weeks later.
    """
    if h.empty:
        return {"available": False}

    up = h["gap_pct"] > 0
    # A gap up is filled if the session's low reaches back to the prior close.
    filled = np.where(up, h["Low"] <= h["prev_close"], h["High"] >= h["prev_close"])
    h = h.assign(filled=filled, direction=np.where(up, "up", "down"))

    rows = []
    for lo, hi, label in _GAP_BUCKETS:
        sel = h[(h["gap_pct"].abs() >= lo) & (h["gap_pct"].abs() < hi)]
        if len(sel) < 20:
            continue
        rows.append({
            "bucket": label,
            "n": int(len(sel)),
            "fill_rate": round(float(sel["filled"].mean() * 100), 1),
            "up_fill_rate": round(float(sel[sel["direction"] == "up"]["filled"].mean() * 100), 1)
            if (sel["direction"] == "up").any() else None,
            "down_fill_rate": round(float(sel[sel["direction"] == "down"]["filled"].mean() * 100), 1)
            if (sel["direction"] == "down").any() else None,
            "close_above_open_rate": round(float((sel["body_pct"] > 0).mean() * 100), 1),
        })

    today = None
    if gap_pct is not None:
        b = _bucket(gap_pct)
        match = next((r for r in rows if r["bucket"] == b), None)
        if match:
            direction = "up" if gap_pct > 0 else "down"
            rate = match["up_fill_rate"] if direction == "up" else match["down_fill_rate"]
            today = {
                "gap_pct": round(float(gap_pct), 3),
                "direction": direction,
                "bucket": b,
                "fill_rate": rate if rate is not None else match["fill_rate"],
                "n": match["n"],
                "note": (f"{'An' if direction == 'up' else 'A'} {direction} gap this size has "
                         f"traded back to the prior close in the same session "
                         f"{rate if rate is not None else match['fill_rate']:.0f}% of "
                         f"{match['n']} occurrences."),
            }

    return {"available": bool(rows), "buckets": rows, "today": today}


def range_base_rates(h: pd.DataFrame, last: float | None = None) -> dict:
    """How wide a session usually gets, and how often it breaks the prior range."""
    if h.empty:
        return {"available": False}

    pcts = h["range_pct"].dropna()
    if len(pcts) < 50:
        return {"available": False}

    def q(p: float) -> float:
        return float(np.percentile(pcts, p))

    took_high = float((h["High"] > h["prev_high"]).mean() * 100)
    took_low = float((h["Low"] < h["prev_low"]).mean() * 100)
    took_both = float(((h["High"] > h["prev_high"]) & (h["Low"] < h["prev_low"])).mean() * 100)
    took_neither = float(((h["High"] <= h["prev_high"]) & (h["Low"] >= h["prev_low"])).mean() * 100)

    # A trend day: most of the range is directional body rather than rotation.
    trend = float((h["body_pct"].abs() / h["range_pct"].replace(0, np.nan) >= 0.75).mean() * 100)

    def handles(pct: float) -> float | None:
        return round(pct / 100 * last, 1) if last else None

    return {
        "available": True,
        "n": int(len(h)),
        "median_range_pct": round(q(50), 3),
        "median_range_handles": handles(q(50)),
        "p25_handles": handles(q(25)),
        "p75_handles": handles(q(75)),
        "p90_handles": handles(q(90)),
        "took_prior_high_pct": round(took_high, 1),
        "took_prior_low_pct": round(took_low, 1),
        "took_both_pct": round(took_both, 1),
        "took_neither_pct": round(took_neither, 1),
        "trend_day_pct": round(trend, 1),
    }


def _release_dates(release_id: int, start: str, end: str) -> list[str]:
    try:
        import requests
        from src.api_keys import get_secret
        key = get_secret("FRED_API_KEY")
        if not key:
            return []
        r = requests.get(
            "https://api.stlouisfed.org/fred/release/dates",
            params={"api_key": key, "file_type": "json", "release_id": release_id,
                    "realtime_start": start, "realtime_end": end,
                    "include_release_dates_with_no_data": "true",
                    "sort_order": "asc", "limit": 1000},
            timeout=20,
        )
        r.raise_for_status()
        return [d["date"] for d in r.json().get("release_dates", [])]
    except Exception as e:
        logger.warning(f"release dates {release_id} failed: {e}")
        return []


def event_base_rates(h: pd.DataFrame, years: int) -> dict:
    """How much wider a session runs on each major release, vs a normal day.

    The ratio is what matters for sizing: if CPI days run 1.6x a normal range,
    a stop placed for an ordinary session is roughly a third too tight.
    """
    if h.empty:
        return {"available": False}

    from time import time as _now
    hit = _CACHE.get(("events", years))
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]

    end = _date.today().isoformat()
    start = str((pd.Timestamp(_date.today()) - pd.Timedelta(days=365 * years)).date())

    baseline_range = float(h["range_pct"].median())
    baseline_body = float(h["body_pct"].abs().median())
    idx_dates = {d.date().isoformat() for d in h.index}

    rows = []
    for rid, name in _EVENT_RELEASES:
        dates = [d for d in _release_dates(rid, start, end) if d in idx_dates]
        if len(dates) < 12:
            continue
        sel = h[[d.date().isoformat() in set(dates) for d in h.index]]
        if sel.empty:
            continue
        med_range = float(sel["range_pct"].median())
        rows.append({
            "name": name,
            "n": int(len(sel)),
            "median_range_pct": round(med_range, 3),
            "range_vs_normal": round(med_range / baseline_range, 2) if baseline_range else None,
            "median_abs_move_pct": round(float(sel["body_pct"].abs().median()), 3),
            "move_vs_normal": round(float(sel["body_pct"].abs().median()) / baseline_body, 2)
            if baseline_body else None,
            "up_close_rate": round(float((sel["body_pct"] > 0).mean() * 100), 1),
        })

    rows.sort(key=lambda r: -(r["range_vs_normal"] or 0))
    out = {
        "available": bool(rows),
        "baseline_range_pct": round(baseline_range, 3),
        "events": rows,
        "note": ("FOMC is not included — release dates come from FRED, which does not carry "
                 "Fed meetings, and the local calendar only holds forward-looking dates."),
    }
    _CACHE[("events", years)] = (_now(), out)
    return out


# ── Intraday path ─────────────────────────────────────────────────

def _polygon_5m(symbol: str, years: int) -> pd.DataFrame:
    """5-minute bars from Polygon, ET, RTH only. Empty frame on any failure.

    Five-minute rather than hourly ON PURPOSE. Polygon aligns hourly bars to the
    clock, so its 09:00-10:00 bar is not the initial balance — adopting it would
    silently redefine the IB and decouple this study from what the live card
    shows. 09:30 sits exactly on the 5-minute grid, so bucketing the fine bars
    here makes the boundary exact regardless of vendor convention.

    Chunked because a single 5y request would exceed Polygon's 50k-row cap and
    be truncated without saying so.
    """
    from datetime import timedelta as _td
    try:
        from src.api_keys import get_secret
        import requests
        api_key = get_secret("MASSIVE_API_KEY")
        if not api_key:
            return pd.DataFrame()

        end = _date.today()

        def _window(i: int) -> list | None:
            """One year of bars, following Polygon's cursor to the end.

            Polygon pages this endpoint via next_url and caps a page around 12k
            bars NO MATTER WHAT `limit` says — the response reports queryCount
            50000 alongside resultsCount 11921. Reading only the first page
            silently returns about half the requested window, which looks like a
            complete history that merely has fewer sessions in it.
            """
            w_end = end - _td(days=365 * i)
            w_start = end - _td(days=365 * (i + 1)) + _td(days=1)
            url = (f"https://api.polygon.io/v2/aggs/ticker/{symbol}"
                   f"/range/{_INTRADAY_BAR_MIN}/minute/"
                   f"{w_start.isoformat()}/{w_end.isoformat()}")
            params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
            out, pages = [], 0
            while url and pages < _MAX_PAGES:
                # Retry the PAGE, not the window. A single read timeout used to
                # propagate out of the thread pool and discard all five years —
                # Cloud Run hit exactly that and silently served the shallower
                # yfinance fallback instead. These responses are ~12k bars each,
                # so an occasional slow one is expected, not exceptional.
                r = None
                for attempt in range(_PAGE_RETRIES):
                    try:
                        r = requests.get(url, params=params, timeout=_PAGE_TIMEOUT_S)
                        break
                    except requests.RequestException as e:
                        if attempt == _PAGE_RETRIES - 1:
                            logger.warning(f"Polygon {_INTRADAY_BAR_MIN}m {symbol} "
                                           f"{w_start}: {e}")
                            return None
                if r is None or r.status_code != 200:
                    logger.warning(f"Polygon {_INTRADAY_BAR_MIN}m {symbol} "
                                   f"{w_start}: HTTP {getattr(r, 'status_code', 'no response')}")
                    return None
                j = r.json()
                out.extend(j.get("results") or [])
                url = j.get("next_url")
                params = {"apiKey": api_key}   # next_url already carries the query
                pages += 1
            if url:
                # More pages remained. A truncated window is not a shorter
                # window: it ends mid-session and biases every path statistic.
                logger.warning(f"Polygon {_INTRADAY_BAR_MIN}m {symbol} {w_start}: still "
                               f"paging after {_MAX_PAGES} pages — refusing a partial window")
                return None
            return out

        # Windows are independent, so page them concurrently — the cursor within
        # a window is strictly sequential and 5y of 5-minute bars is ~20 pages
        # end to end, which is 15s of dead time on a cold instance that the ES
        # brief pre-warm would otherwise absorb.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=years) as pool:
            windows = list(pool.map(_window, range(years)))
        if any(w is None for w in windows):
            return pd.DataFrame()   # a hole would silently bias the study
        rows = [b for w in windows for b in w]
        if not rows:
            return pd.DataFrame()

        d = pd.DataFrame(rows)
        d.index = pd.to_datetime(d["t"], unit="ms", utc=True).dt.tz_convert(_TZ_NY)
        h = d.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close"})
        h = h[["Open", "High", "Low", "Close"]].sort_index()
        h = h[~h.index.duplicated(keep="first")].dropna()
        return h[[_OPEN_T <= t.time() < _CLOSE_T for t in h.index]]
    except Exception as e:
        logger.warning(f"Polygon intraday fetch failed for {symbol}: {e}")
        return pd.DataFrame()


def _to_slots(fine: pd.DataFrame) -> pd.DataFrame:
    """Collapse intra-hour bars into the 09:30-anchored hourly buckets.

    One row per (session, slot), indexed by the slot's opening timestamp, which
    is the shape `_sessions` expects and keeps its `idxmax` lookups unique.
    """
    mins = fine.index.hour * 60 + fine.index.minute - (_OPEN_T.hour * 60 + _OPEN_T.minute)
    fine = fine.assign(day=fine.index.normalize(),
                       slot=[_SLOTS[m // 60] for m in mins])
    g = fine.groupby(["day", "slot"], sort=True)
    h = g.agg(Open=("Open", "first"), High=("High", "max"),
              Low=("Low", "min"), Close=("Close", "last")).reset_index()
    # Build the slot timestamp from the calendar date, not from str() of the
    # tz-aware `day` — that carries a UTC offset into the string and parses back
    # as mixed-offset objects. RTH never straddles a DST transition (those land
    # at 02:00), so localizing is unambiguous.
    naive = pd.to_datetime(h["day"].dt.strftime("%Y-%m-%d") + " " + h["slot"],
                           format="%Y-%m-%d %H:%M")
    h.index = naive.dt.tz_localize(_TZ_NY)
    return h.sort_index()


def _hourly() -> pd.DataFrame:
    """One row per (session, hourly RTH bucket). Cached — this moves once a day.

    Only COMPLETE sessions survive. A half-day or a partial bar makes every path
    statistic wrong in the same direction — the range looks small and the
    extreme looks early — so a session missing any bucket is dropped rather than
    patched. Roughly nine of 730 go this way (holidays, early closes).

    Polygon 5-minute SPY is preferred over Yahoo hourly ^GSPC: five years of
    history instead of Yahoo's hard 730-day cap on hourly bars, and no rate
    limiting. SPY rather than the cash index because Polygon carries no index
    entitlement here — which costs nothing, since every statistic below is a
    ratio or a bucket share and so is invariant to the level of the series.
    """
    from time import time as _now
    hit = _CACHE.get("hourly")
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]

    h = pd.DataFrame()
    source = ""
    fine = _polygon_5m(_INTRADAY_SYMBOL, _INTRADAY_YEARS)
    if not fine.empty:
        h = _to_slots(fine)
        source = f"{_INTRADAY_SYMBOL} cash session, hourly buckets from 5-minute bars"

    if h.empty:
        # Yahoo hourly, the old path — shallower and rate-limitable, but it keeps
        # the card alive when Polygon is unreachable.
        try:
            import yfinance as yf
            y = yf.Ticker(_INDEX).history(period=f"{_INTRADAY_DAYS}d", interval="1h",
                                          auto_adjust=False)
            if y.empty:
                return pd.DataFrame()
            y.index = y.index.tz_convert(_TZ_NY)
            y = y[["Open", "High", "Low", "Close"]].dropna()
            y = y[[_OPEN_T <= t.time() < _CLOSE_T for t in y.index]]
            y["day"] = y.index.normalize()
            y["slot"] = [t.strftime("%H:%M") for t in y.index.time]
            h = y
            source = f"{_INDEX} cash session, hourly"
        except Exception as e:
            logger.warning(f"intraday path history failed: {e}")
            return pd.DataFrame()

    full = h.groupby("day")["slot"].nunique()
    h = h[h["day"].isin(full[full == len(_SLOTS)].index)]
    # Carried on the frame so the payload names the source it actually used
    # rather than the one it hoped for.
    h.attrs["source"] = source
    _CACHE["hourly"] = (_now(), h)
    return h


def _sessions(h: pd.DataFrame) -> pd.DataFrame:
    """Collapse the hourly frame to one row per session, with the IB and extremes."""
    # A duplicated bar for the same (day, slot) would silently produce a
    # duplicated index here and misalign every column assembled below, so it is
    # dropped rather than trusted — the completeness filter counts distinct
    # slots and would not catch it.
    h = h[~h.index.duplicated(keep="first")]
    ib = h[h["slot"] == _SLOTS[0]].set_index("day")
    ib = ib[~ib.index.duplicated(keep="first")]
    g = h.groupby("day")
    s = pd.DataFrame({
        "ib_hi": ib["High"], "ib_lo": ib["Low"],
        "hi": g["High"].max(), "lo": g["Low"].min(),
        "open": g["Open"].first(), "close": g["Close"].last(),
        "hi_slot": h.loc[g["High"].idxmax()].set_index("day")["slot"],
        "lo_slot": h.loc[g["Low"].idxmin()].set_index("day")["slot"],
    })
    s["rng"] = s["hi"] - s["lo"]
    s["ib_rng"] = s["ib_hi"] - s["ib_lo"]
    # A zero range is a data artifact, not a session; it would divide by zero in
    # every ratio below.
    return s[(s["rng"] > 0) & (s["ib_rng"] > 0)]


def _current_slot(now: pd.Timestamp | None) -> str | None:
    """Which hourly bucket the clock is in, or None outside the cash session.

    The weekday guard is defence in depth, not the real check. A time-of-day
    test alone said 10:22 on a Saturday was the 10:30 bucket, which would have
    put "30% of the range is still to come" on a card for a session that does
    not exist. Callers that know the session model should pass `now=None`
    unless it is genuinely RTH — `es_cockpit` does exactly that, and only that
    path knows about holidays and half-days.
    """
    if now is None:
        return None
    t = now.tz_convert(_TZ_NY) if now.tzinfo else now.tz_localize(_TZ_NY)
    if t.weekday() >= 5 or not (_OPEN_T <= t.time() < _CLOSE_T):
        return None
    mins = (t.hour * 60 + t.minute) - (9 * 60 + 30)
    return _SLOTS[min(mins // 60, len(_SLOTS) - 1)]


def path_base_rates(last: float | None = None,
                    now: pd.Timestamp | None = None) -> dict:
    """When the session's extremes print, and what an IB break is worth.

    The daily statistics above say how big a session gets. These say WHEN it
    gets there — which is the part an intraday trader actually trades against,
    because a level three hours away is a different proposition at 10:00 than at
    15:00, and most of the range is already spent by lunch.
    """
    h = _hourly()
    if h.empty:
        return {"available": False, "reason": "no intraday history"}
    s = _sessions(h)
    if len(s) < _PATH_MIN_SESSIONS:
        return {"available": False, "reason": "not enough complete sessions"}
    n = len(s)

    # Where the extremes print. The 15:30 bucket is HALF the width of the others
    # (15:30-16:00), so its share understates the closing drive per minute —
    # flagged in the payload rather than silently rescaled, because rescaling
    # would invent a number no session actually produced.
    extremes = [{
        "slot": sl,
        "minutes": 30 if sl == _SLOTS[-1] else 60,
        "high_pct": round(float((s["hi_slot"] == sl).mean() * 100), 1),
        "low_pct": round(float((s["lo_slot"] == sl).mean() * 100), 1),
    } for sl in _SLOTS]

    # Cumulative: how much is already decided by the end of each hour.
    progress = []
    for i, sl in enumerate(_SLOTS):
        done = set(_SLOTS[: i + 1])
        upto = h[h["slot"].isin(done)].groupby("day")
        frac = ((upto["High"].max() - upto["Low"].min()) / s["rng"]).dropna()
        progress.append({
            "slot": sl,
            "range_complete_pct": round(float(frac.median() * 100), 1),
            "range_complete_p25": round(float(frac.quantile(0.25) * 100), 1),
            "high_in_pct": round(float(s["hi_slot"].isin(done).mean() * 100), 1),
            "low_in_pct": round(float(s["lo_slot"].isin(done).mean() * 100), 1),
            "both_in_pct": round(float((s["hi_slot"].isin(done)
                                        & s["lo_slot"].isin(done)).mean() * 100), 1),
        })

    up0 = s["hi"] > s["ib_hi"]
    dn0 = s["lo"] < s["ib_lo"]
    ib_share = float((s["ib_rng"] / s["rng"]).median() * 100)

    # IB breaks, by how decisive they are. `held` is the trader's question: given
    # a break, did the session CLOSE beyond the IB edge, or fall back inside?
    breaks = []
    for buf in _BREAK_BUFFERS:
        up = s["hi"] > s["ib_hi"] + buf * s["ib_rng"]
        dn = s["lo"] < s["ib_lo"] - buf * s["ib_rng"]
        bu, bd, clean = s[up], s[dn], s[up & ~dn]
        if len(bu) < 40 or len(clean) < 40:
            continue
        breaks.append({
            "buffer_pct_of_ib": round(buf * 100),
            "up_n": int(len(bu)),
            "up_held_pct": round(float((bu["close"] > bu["ib_hi"]).mean() * 100), 1),
            "down_n": int(len(bd)),
            "down_held_pct": round(float((bd["close"] < bd["ib_lo"]).mean() * 100), 1),
            "both_sides_pct": round(float((up & dn).mean() * 100), 1),
            "clean_up_n": int(len(clean)),
            "clean_up_held_pct": round(float((clean["close"] > clean["ib_hi"]).mean() * 100), 1),
        })

    # Does a quiet first hour coil the day or just make a small target? The
    # answer turns out to be mostly the latter, which is worth knowing.
    # qcut raises when the tercile edges are not unique, which a run of identical
    # IB widths would produce. That is a reason to drop this one table, not to
    # lose the whole path study, so it degrades to empty.
    width = []
    try:
        terc = pd.qcut(s["ib_rng"] / s["open"] * 100, 3, labels=["narrow", "middle", "wide"])
    except (ValueError, IndexError) as e:
        logger.warning(f"IB width terciles unavailable: {e}")
        terc = pd.Series(index=s.index, dtype="object")
    for lab in ("narrow", "middle", "wide"):
        sel = s[terc == lab]
        if len(sel) < 40:
            continue
        u, d = sel["hi"] > sel["ib_hi"], sel["lo"] < sel["ib_lo"]
        width.append({
            "band": lab,
            "n": int(len(sel)),
            "one_sided_pct": round(float(((u | d) & ~(u & d)).mean() * 100), 1),
            "both_sides_pct": round(float((u & d).mean() * 100), 1),
            "day_range_x_ib": round(float((sel["rng"] / sel["ib_rng"]).median()), 2),
        })

    loc = (s["close"] - s["lo"]) / s["rng"]

    # Live pointer: what the clock says about how much is left.
    slot = _current_slot(now)
    live = None
    if slot:
        row = next(p for p in progress if p["slot"] == slot)
        i = _SLOTS.index(slot)
        live = {
            "slot": slot,
            "elapsed_label": f"through {slot}",
            "range_complete_pct": row["range_complete_pct"],
            "high_in_pct": row["high_in_pct"],
            "low_in_pct": row["low_in_pct"],
            "note": (f"By the end of this hour a typical session has covered "
                     f"{row['range_complete_pct']:.0f}% of its full range, with the high "
                     f"already in {row['high_in_pct']:.0f}% of the time and the low "
                     f"{row['low_in_pct']:.0f}%."
                     + ("" if i >= len(_SLOTS) - 1 else
                        f" {100 - row['range_complete_pct']:.0f}% of the range is typically "
                        f"still to come.")),
        }

    return {
        "available": True,
        "source": h.attrs.get("source") or f"{_INDEX} cash session, hourly",
        # STATED, NOT INFERRED. These path statistics sit inches from the
        # overnight study on the same card, and that one is ES futures over two
        # years while this is SPY over five. A reader glancing between "72.4%"
        # and "58.1%" will assume one instrument and one window unless both say
        # otherwise. The overnight module already names itself for exactly this
        # reason; this side was the half of the guard that was missing.
        "instrument": (h.attrs.get("instrument")
                       or f"{_INTRADAY_SYMBOL}, {_INTRADAY_BAR_MIN}-minute bars"),
        "instrument_note": (
            f"{_INTRADAY_SYMBOL} is the tradeable proxy for the cash index — Polygon "
            f"carries no index entitlement. Percentages describe the CASH session; "
            f"ES trades around the clock and its own overnight statistics are a "
            f"different study on a different instrument."),
        "sessions": n,
        "from": str(s.index.min().date()),
        "to": str(s.index.max().date()),
        "slots": _SLOTS,
        "extremes": extremes,
        "progress": progress,
        "initial_balance": {
            "definition": "first hour of the cash session (09:30-10:30 ET)",
            "share_of_day_range_pct": round(ib_share, 1),
            "one_sided_pct": round(float(((up0 | dn0) & ~(up0 & dn0)).mean() * 100), 1),
            "both_sides_pct": round(float((up0 & dn0).mean() * 100), 1),
            "inside_pct": round(float((~up0 & ~dn0).mean() * 100), 1),
            "held_high_of_day_pct": round(float((s["hi_slot"] == _SLOTS[0]).mean() * 100), 1),
            "held_low_of_day_pct": round(float((s["lo_slot"] == _SLOTS[0]).mean() * 100), 1),
            "note": ("Price leaves the first hour's range on all but a handful of "
                     "sessions, so 'the IB extended' on its own says almost nothing. "
                     "Whether it extends ONE side or both is the information."),
        },
        "ib_breaks": breaks,
        "ib_width": width,
        "close_location": {
            "upper_third_pct": round(float((loc >= 2 / 3).mean() * 100), 1),
            "middle_third_pct": round(float(((loc > 1 / 3) & (loc < 2 / 3)).mean() * 100), 1),
            "lower_third_pct": round(float((loc <= 1 / 3).mean() * 100), 1),
        },
        "live": live,
        "caveats": [
            "Hourly buckets, so 'the high printed in the 10:30 hour' means inside that "
            "hour, not at a timestamp.",
            "The 15:30 bucket covers 30 minutes, half the width of the others — its share "
            "of the extremes understates the closing drive minute for minute.",
            "Cash-index RTH only. The Globex path is not measured here, and an overnight "
            "extreme is invisible to this study.",
            f"{n} sessions over roughly two and a half years — a much shorter window than "
            "the daily statistics above, covering fewer regimes.",
        ],
    }


def _safe_path(last: float | None, now: pd.Timestamp | None) -> dict:
    try:
        return path_base_rates(last=last, now=now)
    except Exception as e:
        logger.warning(f"path base rates failed: {e}")
        return {"available": False, "reason": "path statistics unavailable"}


def base_rates(last: float | None = None, gap_pct: float | None = None,
               years: int = _DEFAULT_YEARS,
               now: pd.Timestamp | None = None,
               with_path: bool = True) -> dict:
    """All measured base rates, optionally conditioned on today's gap and clock."""
    h = _daily(years)
    if h.empty:
        return {"available": False, "reason": "no index history"}

    cutoff = pd.Timestamp.now(tz=h.index.tz) - pd.Timedelta(days=365 * years)
    h = h[h.index >= cutoff]
    if len(h) < 200:
        return {"available": False, "reason": "not enough history"}

    return {
        "available": True,
        "source": f"{_INDEX} cash session",
        # The DAILY study names itself too. Three sets of percentages sit on one
        # card — these daily rates, the SPY intraday path rates under `path`,
        # and the ES overnight study — on three different instruments over three
        # different windows. Any of them unlabelled and a reader merges them.
        "instrument": f"{_INDEX} daily bars, cash session",
        "window_years": years,
        "sessions": int(len(h)),
        "from": str(h.index.min().date()),
        "to": str(h.index.max().date()),
        "gaps": gap_base_rates(h, gap_pct),
        "range": range_base_rates(h, last),
        "events": event_base_rates(h, years),
        # The path study runs on its own, much shorter window, so it carries its
        # own `sessions`/`from`/`to` rather than inheriting the ones above.
        # It is also the newest and most fragile section, and it is contained
        # here on purpose: an exception raised through this return would be
        # caught upstream as "base_rates failed" and take the gap, range and
        # event rates down with it, for a fault in none of them.
        "path": _safe_path(last, now) if with_path else None,
    }
