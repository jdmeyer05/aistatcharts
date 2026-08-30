"""Market breadth and internals for the ES cockpit.

WHAT THIS ANSWERS. The index tells you where the average dollar went. Breadth
tells you how many stocks went with it, and those two disagree often enough to
matter. A session where ES grinds up while most stocks fall is a different
trade from one where everything rallies together: the first is a handful of
mega-caps carrying a tape that is quietly weakening underneath, and it is the
one that reverses. Measured on 2026-07-31, SPX closed +0.70% with net
advancers at -11% and equal-weight -0.17% against cap-weight +0.72% — the
index made a new high on a majority of stocks going down.

TICK IS NOT HERE, AND CANNOT BE. The NYSE TICK is a count of stocks on an
uptick minus those on a downtick at an instant. Reconstructing it needs the
tick-by-tick trade stream for every NYSE listing with each trade classified
against the prior print. Neither Yahoo (which serves no internals at all — no
TICK, no TRIN, no advance/decline) nor the Polygon plan wired here exposes
that. A "TICK" built from 5-minute bars would be a different quantity wearing
the name of the one traders actually use, so it is absent rather than faked.
The Arms index below IS computable, because TRIN is defined from advance and
decline counts and volumes, which are all reconstructible.

THE UNIVERSE IS A CHOICE, AND IT MOVES THE NUMBER. Counting all 12,000-odd US
tickers dilutes the reading with names that barely trade. Applying a liquidity
filter changes net advancers on the same session from -3% to -11%. The filtered
universes agree with each other closely, so the filter is doing real work
rather than being tuned — but the number is not "the NYSE advance-decline
line", it is this universe's, and it is labelled that way everywhere.

THE LIQUIDITY FILTER USES YESTERDAY. Intraday, today's volume accumulates from
zero, so a fixed dollar-volume threshold would admit almost nothing at 09:35
and grow the universe all session — breadth would drift for a purely mechanical
reason and the open would read as a false extreme. The filter is therefore
applied to the PRIOR session's dollar volume, which is fixed for the whole day,
and a name only has to have traded at all today to be counted.
"""

from __future__ import annotations

import logging
from datetime import date as _date, timedelta

import pandas as pd

logger = logging.getLogger(__name__)

_TZ_NY = "America/New_York"
_CACHE: dict = {}
_LIVE_TTL_S = 60          # breadth moves continuously; a minute is honest
_DAILY_TTL_S = 6 * 3600

# Universe filter, applied to the PRIOR session so it is stable intraday.
_MIN_PRICE = 5.0
_MIN_DOLLAR_VOL = 10e6

# TRIN reference bands. These are the long-published Arms index conventions,
# not thresholds fitted here — a 1.0 is balance by construction, since it is the
# ratio of two ratios that are equal when advancing and declining stocks carry
# volume in proportion to their numbers.
_TRIN_BANDS = [
    (0.00, 0.50, "buying climax", "Advancing stocks are absorbing far more volume than their "
                                  "count implies — strong, but this is where upside exhausts."),
    (0.50, 0.85, "buying pressure", "Volume is concentrated in advancing stocks."),
    (0.85, 1.15, "balanced", "Volume is distributed roughly in line with the advance-decline split."),
    (1.15, 2.00, "selling pressure", "Volume is concentrated in declining stocks."),
    (2.00, 99.0, "washout", "Declining stocks are absorbing far more volume than their count "
                            "implies — capitulation, and where downside tends to exhaust."),
]


def _key() -> str | None:
    from src.api_keys import get_secret
    return get_secret("POLYGON_API_KEY") or get_secret("MASSIVE_API_KEY")


def _snapshot() -> pd.DataFrame:
    """Full-market snapshot: one call, every US ticker, ~3MB in under a second."""
    from time import time as _now
    hit = _CACHE.get("snap")
    if hit and (_now() - hit[0]) < _LIVE_TTL_S:
        return hit[1]
    key = _key()
    if not key:
        return pd.DataFrame()
    try:
        import requests
        r = requests.get(
            "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
            params={"apiKey": key}, timeout=45)
        r.raise_for_status()
        rows = r.json().get("tickers", [])
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame([{
            "ticker": t.get("ticker"),
            "chg_pct": t.get("todaysChangePerc"),
            "vol": (t.get("day") or {}).get("v") or 0.0,
            "close": (t.get("day") or {}).get("c") or 0.0,
            "prev_close": (t.get("prevDay") or {}).get("c") or 0.0,
            "prev_vol": (t.get("prevDay") or {}).get("v") or 0.0,
        } for t in rows]).set_index("ticker")
        _CACHE["snap"] = (_now(), df)
        return df
    except Exception as e:
        logger.warning(f"breadth snapshot failed: {e}")
        return pd.DataFrame()


def _grouped(day: str, cache: bool = True) -> pd.DataFrame:
    """Every US ticker's daily bar for one date. Empty on weekends and holidays.

    `cache=False` for deep-history walks. This module's cache is unbounded and
    each frame is ~12,000 rows, which is free when the caller wants the last two
    sessions repeatedly — the live breadth path — and is not free at all when a
    caller wants two hundred. `breadth_trend` walks 200 days once every twelve
    hours and folds each frame into running sums immediately; caching those
    would pin ~2.4M rows for the six-hour TTL to serve a read that will not come
    again. Measured: 209 entries, 2,412,389 rows retained after one such walk.
    """
    from time import time as _now
    hit = _CACHE.get(("grouped", day))
    if hit and (_now() - hit[0]) < _DAILY_TTL_S:
        return hit[1]
    key = _key()
    if not key:
        return pd.DataFrame()
    try:
        import requests
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{day}",
            params={"adjusted": "true", "apiKey": key}, timeout=30)
        r.raise_for_status()
        res = r.json().get("results", [])
        df = (pd.DataFrame(res).rename(columns={"T": "ticker", "c": "close", "v": "vol"})
              .set_index("ticker")[["close", "vol"]]) if res else pd.DataFrame()
        if cache:
            _CACHE[("grouped", day)] = (_now(), df)
        return df
    except Exception as e:
        logger.warning(f"grouped daily {day} failed: {e}")
        return pd.DataFrame()


def _last_two_sessions(anchor: _date) -> tuple[pd.DataFrame, pd.DataFrame, str, str] | None:
    """The two most recent dates that actually traded, walking back from `anchor`.

    Holidays return an empty payload rather than an error, so the only reliable
    test for "did this date trade" is whether the call came back with rows.
    """
    found: list[tuple[str, pd.DataFrame]] = []
    d = anchor
    for _ in range(10):
        if d.weekday() < 5:
            g = _grouped(d.isoformat())
            if not g.empty:
                found.append((d.isoformat(), g))
                if len(found) == 2:
                    (d1, g1), (d0, g0) = found
                    return g1, g0, d1, d0
        d -= timedelta(days=1)
    return None


def _classify(df: pd.DataFrame) -> dict:
    """Counts, volumes and the ratios built from them, on an already-filtered frame."""
    adv_m, dec_m = df["chg_pct"] > 0, df["chg_pct"] < 0
    adv, dec = int(adv_m.sum()), int(dec_m.sum())
    unch = int(len(df) - adv - dec)
    up_vol = float(df.loc[adv_m, "vol"].sum())
    dn_vol = float(df.loc[dec_m, "vol"].sum())
    n = len(df)

    ad_ratio = adv / dec if dec else None
    vol_ratio = up_vol / dn_vol if dn_vol else None
    # TRIN is the ratio of those two ratios. It is only defined when both
    # denominators exist; a session with no decliners is not a TRIN of infinity,
    # it is a session the statistic does not describe.
    trin = (ad_ratio / vol_ratio) if (ad_ratio and vol_ratio) else None

    band = None
    if trin is not None:
        for lo, hi, label, why in _TRIN_BANDS:
            if lo <= trin < hi:
                band = {"label": label, "why": why}
                break

    return {
        "universe_n": n,
        "advancers": adv,
        "decliners": dec,
        "unchanged": unch,
        "net_advancers": adv - dec,
        "net_advancers_pct": round((adv - dec) / n * 100, 1) if n else None,
        "ad_ratio": round(ad_ratio, 2) if ad_ratio else None,
        "up_volume": up_vol,
        "down_volume": dn_vol,
        "up_volume_pct": round(up_vol / (up_vol + dn_vol) * 100, 1) if (up_vol + dn_vol) else None,
        "volume_ratio": round(vol_ratio, 2) if vol_ratio else None,
        "trin": round(trin, 2) if trin else None,
        "trin_band": band,
    }


def _equal_vs_cap(snap: pd.DataFrame | None = None) -> dict:
    """Equal-weight against cap-weight — the cheapest honest breadth cross-check.

    METHODOLOGICALLY independent of the counts above: two ETF prices against a
    universe tally, so it can contradict the reconstruction and that
    contradiction is informative. It is NOT source-independent, and the docstring
    used to claim it was. Both names are already sitting in the snapshot the
    counts were built from, so reading them from anywhere else meant two extra
    round-trips AND a second prior-close convention that could disagree with
    every other number on the card — the exact failure the accuracy audit was
    about. Same source, same arithmetic, consistent answer.

    yfinance stays as the fallback for the case where the snapshot is missing or
    the names are absent from it.
    """
    try:
        out = {}
        if snap is not None and not snap.empty:
            for sym, key in (("RSP", "equal_weight"), ("SPY", "cap_weight")):
                if sym in snap.index:
                    row = snap.loc[sym]
                    chg = row.get("chg_pct")
                    # `chg_pct` is zeroed when the market is shut, so fall back to
                    # the completed session rather than publish a flat 0.00%.
                    if chg and float(row.get("vol") or 0) > 0:
                        out[key] = round(float(chg), 2)
            if len(out) == 2:
                spread = round(out["equal_weight"] - out["cap_weight"], 2)
                return {"available": True, **out, "spread_pct": spread,
                        "source": "polygon snapshot",
                        "label": ("broad" if spread > 0.15 else
                                  "narrow" if spread < -0.15 else "even"),
                        "note": ("Equal-weight minus cap-weight. Negative means the index is "
                                 "being carried by its largest members while the average "
                                 "stock lags.")}
            out = {}

        import yfinance as yf
        for sym, key in (("RSP", "equal_weight"), ("SPY", "cap_weight")):
            # yf.download is not thread-safe; Ticker().history is.
            h = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=False)
            if len(h) < 2:
                return {"available": False}
            c = h["Close"]
            out[key] = round(float(c.iloc[-1] / c.iloc[-2] - 1) * 100, 2)
        spread = round(out["equal_weight"] - out["cap_weight"], 2)
        return {
            "available": True,
            **out,
            "spread_pct": spread,
            "source": "yfinance fallback",
            "label": ("broad" if spread > 0.15 else
                      "narrow" if spread < -0.15 else "even"),
            "note": ("Equal-weight minus cap-weight. Negative means the index is being "
                     "carried by its largest members while the average stock lags."),
        }
    except Exception as e:
        logger.warning(f"equal-vs-cap failed: {e}")
        return {"available": False}


def _trend_or_none() -> dict | None:
    """Trend breadth, or nothing. Never raises into the live breadth path — a
    200-session walk failing must not cost the reader today's advance/decline."""
    try:
        from src.breadth_trend import trend_breadth
        # CACHE ONLY. A miss here would put a ~157s walk on the request path,
        # against the ES brief's 20s server-side timeout — the card would not
        # render slowly, it would not render. The scheduled refresh fills it.
        t = trend_breadth(cached_only=True)
        return t if t.get("available") else None
    except Exception as e:
        logger.debug(f"trend breadth unavailable: {e}")
        return None


def market_breadth(now: pd.Timestamp | None = None,
                   index_change_pct: float | None = None) -> dict:
    """Advance/decline, up/down volume and TRIN, live if the market is trading.

    `index_change_pct` is the index move to compare breadth against — pass it and
    the divergence read below becomes available. Without it the counts still
    stand on their own.

    `trend` carries the share of the universe above its 50- and 200-day average,
    from `breadth_trend`. It answers a DIFFERENT question from everything else
    here — those all ask whether today was broad, this asks whether the market
    is broadly in an uptrend — and the two diverge precisely when an index is
    being carried by a handful of names. Behind a 12h cache and a startup warm,
    so it never runs on the request path; a failure leaves it absent rather than
    degrading the counts.
    """
    snap = _snapshot()
    live = False
    frame = pd.DataFrame()
    asof_note = ""
    session_label = None

    if not snap.empty:
        traded = snap[(snap["vol"] > 0) & (snap["prev_close"] > 0)]
        live = len(traded) > 500
    eligible_n = None
    if live:
        # Liquidity measured on the PRIOR session so the universe is fixed all day.
        eligible = snap[(snap["prev_close"] >= _MIN_PRICE)
                        & (snap["prev_close"] * snap["prev_vol"] >= _MIN_DOLLAR_VOL)]
        eligible_n = int(len(eligible))
        u = eligible[eligible["vol"] > 0]
        frame = u[["chg_pct", "vol"]].dropna()
        # How many of the eligible names have actually traded matters: before the
        # bell only a fraction of them have, and a count drawn from that fraction
        # is a thin sample wearing a full session's label. Reported rather than
        # suppressed, because premarket breadth is still worth seeing — it just
        # must not be read as the same measurement.
        share = len(frame) / eligible_n if eligible_n else 0.0
        asof_note = (
            "Live, accumulating through the session."
            if share >= 0.8 else
            f"Live, but only {len(frame):,} of {eligible_n:,} eligible names have traded "
            f"({share*100:.0f}%) — thin participation, typical before the cash open. "
            "Treat as indicative, not a session reading."
        )
    else:
        # Exchange-local, never `date.today()`: this runs on Cloud Run in UTC,
        # where after 20:00 ET "today" is already tomorrow and the walk back to
        # the last traded session would start a day too far out.
        anchor = (now.tz_convert(_TZ_NY) if (now is not None and now.tzinfo)
                  else now if now is not None
                  else pd.Timestamp.now(tz=_TZ_NY))
        two = _last_two_sessions(anchor.date())
        if not two:
            return {"available": False, "reason": "no session data"}
        g1, g0, d1, d0 = two
        j = g1.join(g0.rename(columns={"close": "prev_close", "vol": "prev_vol"}), how="inner")
        j = j[(j["prev_close"] > 0) & (j["vol"] > 0)]
        j["chg_pct"] = (j["close"] - j["prev_close"]) / j["prev_close"] * 100
        # Filtered on the PRIOR session's dollar volume, exactly as the live path
        # is. Using this session's own volume here would be defensible on its own
        # — it is final — but it would mean the two paths select different
        # universes, so a number would shift at the close for no market reason.
        u = j[(j["prev_close"] >= _MIN_PRICE)
              & (j["prev_close"] * j["prev_vol"] >= _MIN_DOLLAR_VOL)]
        frame = u[["chg_pct", "vol"]].dropna()
        session_label = d1
        asof_note = f"Last completed session ({d1}); the market is not trading now."

    if frame.empty or len(frame) < 200:
        return {"available": False, "reason": "universe too small to be meaningful"}

    stats = _classify(frame)
    # Reuse the snapshot already in hand rather than re-fetching two names.
    eq = _equal_vs_cap(snap if live else None)

    # The index move breadth is compared against has to describe the SAME window
    # the counts do. Passing ES's change works while the market is trading, but
    # on a closed day the counts describe the last completed session and ES's
    # current change does not — comparing them would manufacture a divergence.
    # SPY's latest daily bar is the developing session when live and the
    # completed one when not, so it tracks the counts in both states.
    if index_change_pct is None and eq.get("available"):
        index_change_pct = eq.get("cap_weight")

    # Divergence: does breadth agree with the index? This is the whole point of
    # the module, so it is stated rather than left to be inferred from two rows.
    divergence = None
    net = stats["net_advancers_pct"]
    if index_change_pct is not None and net is not None:
        idx_up, breadth_up = index_change_pct > 0, net > 0
        if idx_up != breadth_up and abs(index_change_pct) > 0.1 and abs(net) > 5:
            divergence = {
                "label": "divergent",
                "note": (f"The index is {'up' if idx_up else 'down'} "
                         f"{abs(index_change_pct):.2f}% while net advancers are "
                         f"{net:+.0f}% — the move is not confirmed by the majority of "
                         f"stocks. Narrow moves are the ones that retrace."),
            }
        else:
            divergence = {
                "label": "confirmed",
                "note": (f"The index and breadth agree: {'up' if idx_up else 'down'} "
                         f"{abs(index_change_pct):.2f}% with net advancers {net:+.0f}%."),
            }

    return {
        "available": True,
        "live": live,
        "session": session_label,
        "asof_note": asof_note,
        "universe": {
            "n": stats["universe_n"],
            "eligible_n": eligible_n,
            "definition": (f"US tickers with a prior close above ${_MIN_PRICE:.0f} and prior-session "
                           f"dollar volume above ${_MIN_DOLLAR_VOL/1e6:.0f}M"),
            "note": ("Liquidity is measured on the PRIOR session so the universe does not "
                     "grow through the day, which would drift breadth for a mechanical reason. "
                     "`n` counts those that have actually traded; `eligible_n` is how many "
                     "passed the filter, so the gap between them is participation."),
        },
        **{k: v for k, v in stats.items() if k != "universe_n"},
        "equal_vs_cap": eq,
        # A DIFFERENT QUESTION FROM EVERYTHING ABOVE. These counts ask whether
        # today was broad; this asks whether the market is broadly in an
        # uptrend, and an index can be green on the day with most of its names
        # below their own 200-day. Cached 12h and pre-warmed, so it is never on
        # the request path; absent rather than degrading if it fails.
        "trend": _trend_or_none(),
        "divergence": divergence,
        "tick": {
            "available": False,
            "reason": ("NYSE TICK needs a classified tick-by-tick trade stream, which no "
                       "data source wired here provides. It is omitted rather than "
                       "approximated from bars, which would be a different statistic."),
        },
        "reconstruction": ("Advance/decline and TRIN are computed on the universe above, not "
                           "on NYSE-listed issues, so they will not tie out against a terminal's "
                           "NYSE figures. The direction and the extremes carry; the absolute "
                           "counts are this universe's."),
    }
