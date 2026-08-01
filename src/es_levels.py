"""Intraday reference levels for the E-mini S&P (ES).

The levels an intraday ES trader actually reacts to, computed from 5-minute
bars: prior RTH high/low/close, the overnight (Globex) range, today's
developing range and VWAP, and a volume profile (POC / value area).

WHY THESE: they are the levels where resting liquidity and prior acceptance
sit, so they are where ES tends to react regardless of anyone's opinion. They
are self-fulfilling to a degree — which is the point, not a caveat.

SESSION MODEL: ES trades nearly 24h. RTH is 09:30–16:00 ET (the cash session,
which is what "prior day high/low" conventionally means); Globex is everything
from 18:00 ET the previous evening to the 09:30 open. Splitting them matters:
an overnight high made on thin Globex volume behaves differently from an RTH
high made on size, and collapsing the two hides that.

Times are handled in US/Eastern because the session boundaries are defined in
exchange local time and shift with US daylight saving.
"""

from __future__ import annotations

import logging
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SYMBOL = "ES=F"
_TZ = "America/New_York"
_RTH_OPEN = (9, 30)
_RTH_CLOSE = (16, 0)
_VALUE_AREA = 0.70   # conventional 70% of volume around the POC

# A developing profile built from the first few bars of a session is noise, and
# the card leads with location relative to value — so until the session has
# real shape, the prior completed session's value area is the honest reference.
# That is also what traders actually watch into and just after the open.
_PROFILE_MIN_BARS = 24        # 2 hours of 5-minute bars


def _fetch_bars(days: int = 5, interval: str = "5m") -> pd.DataFrame:
    """5-minute ES bars in exchange local time. yf.Ticker().history(), never
    yf.download() — the latter is not thread-safe and this runs in a pool."""
    try:
        import yfinance as yf
        df = yf.Ticker(SYMBOL).history(period=f"{days}d", interval=interval, auto_adjust=False)
        if df.empty:
            return df
        idx = pd.to_datetime(df.index)
        df.index = idx.tz_convert(_TZ) if idx.tz is not None else idx.tz_localize("UTC").tz_convert(_TZ)
        return df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    except Exception as e:
        logger.warning(f"ES bar fetch failed: {e}")
        return pd.DataFrame()


def _is_rth(ts: pd.Timestamp) -> bool:
    t = (ts.hour, ts.minute)
    return _RTH_OPEN <= t < _RTH_CLOSE


def _vwap(df: pd.DataFrame) -> float | None:
    """Volume-weighted average price over the given bars.

    Typical price (H+L+C)/3 rather than close, which is the convention intraday
    platforms use — matching it matters more than any theoretical preference,
    because the level only works if everyone is looking at the same one.
    """
    if df.empty or df["Volume"].sum() <= 0:
        return None
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    return float((tp * df["Volume"]).sum() / df["Volume"].sum())


def _volume_profile(df: pd.DataFrame, bins: int = 60) -> dict | None:
    """Point of control and value area from a volume-by-price histogram.

    Each bar's volume is dropped into the price bin of its typical price. This
    is the standard approximation — a true profile distributes volume across
    each bar's whole range, which needs tick data we don't have. The POC lands
    in the same place; value-area edges can differ by a bin.
    """
    if df.empty or df["Volume"].sum() <= 0:
        return None
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    lo, hi = float(df["Low"].min()), float(df["High"].max())
    if hi <= lo:
        return None

    edges = np.linspace(lo, hi, bins + 1)
    idx = np.clip(np.digitize(tp.values, edges) - 1, 0, bins - 1)
    vol = np.zeros(bins)
    np.add.at(vol, idx, df["Volume"].values)
    if vol.sum() <= 0:
        return None

    centers = (edges[:-1] + edges[1:]) / 2
    poc_i = int(vol.argmax())

    # Grow outward from the POC, always taking the heavier neighbour, until the
    # value area holds 70% of volume. That's the standard construction.
    target = vol.sum() * _VALUE_AREA
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < target and (lo_i > 0 or hi_i < bins - 1):
        below = vol[lo_i - 1] if lo_i > 0 else -1
        above = vol[hi_i + 1] if hi_i < bins - 1 else -1
        if above >= below:
            hi_i += 1
            acc += vol[hi_i]
        else:
            lo_i -= 1
            acc += vol[lo_i]

    return {
        "poc": round(float(centers[poc_i]), 2),
        "vah": round(float(centers[hi_i]), 2),
        "val": round(float(centers[lo_i]), 2),
    }


def _quarterly_expiry(year: int, month: int) -> pd.Timestamp:
    """Third Friday of a quarterly month — ES expiration."""
    d = pd.Timestamp(year=year, month=month, day=1)
    fridays = [d + pd.Timedelta(days=i) for i in range(31)
               if (d + pd.Timedelta(days=i)).month == month
               and (d + pd.Timedelta(days=i)).weekday() == 4]
    return fridays[2]


def _contract_roll_risk(day: pd.Timestamp) -> bool:
    """Whether `day` sits in a quarterly roll window.

    `ES=F` is a CONTINUOUS front-month series: when volume rolls to the next
    quarterly contract the series steps by the roll spread — tens of handles —
    without anything in the data marking it. Prior-session levels can then be
    from the expiring contract while the last price is the new one, and every
    distance measured against them is wrong by that spread.

    We can't repair that without contract-specific data, so we flag it. Volume
    migrates around the Thursday eight days before expiry, so the window opens
    ~10 days out and closes at expiration.
    """
    # Compare on naive calendar dates — `day` arrives tz-aware from the session
    # model, the expiry is constructed naive.
    ref = pd.Timestamp(day).tz_localize(None).normalize() if pd.Timestamp(day).tzinfo \
        else pd.Timestamp(day).normalize()
    for month in (3, 6, 9, 12):
        for year in (ref.year - 1, ref.year, ref.year + 1):
            try:
                exp = _quarterly_expiry(year, month)
            except (IndexError, ValueError):
                continue
            if pd.Timedelta(days=0) <= (exp - ref) <= pd.Timedelta(days=10):
                return True
    return False


def _globex_open_at_or_before(ts: pd.Timestamp) -> pd.Timestamp:
    """The 18:00 ET Globex open that started the trading session containing ts."""
    base = ts.normalize() + pd.Timedelta(hours=18)
    return base - pd.Timedelta(days=1) if ts < base else base


def es_levels(profile_sessions: int = 1, now: pd.Timestamp | None = None) -> dict:
    """Reference levels for the current ES session.

    ANCHORING IS THE WHOLE PROBLEM HERE. ES trades nearly 24h, so "the current
    session" depends on when you ask, and the naive answer — the most recent
    calendar date that has RTH bars — is wrong exactly when this card is most
    used: overnight and pre-open.

    Anchoring that way, a check at 08:00 reported the PREVIOUS night's Globex
    range as "overnight" and the PREVIOUS day's RTH as "today", while `last`
    was the live price. Price then printed above every level on the ladder and
    read as a clean breakout when it was actually mid-range inside the real
    developing overnight. So the anchor is derived from the clock instead:

      live RTH        — RTH bars exist for the session day; today's levels develop.
      premarket       — a Globex session is running but RTH hasn't opened. The
                        overnight range is the DEVELOPING one, and no "today"
                        RTH levels are emitted, because there is no session yet.
      last_session    — market closed with no developing Globex (weekend, or the
                        Friday-evening weekly close). Describes the last
                        completed session, which is what a weekend review wants.
    """
    now = now or pd.Timestamp.now(tz=_TZ)
    if now.tzinfo is None:
        now = now.tz_localize(_TZ)

    # 7 days rather than 5 so a long weekend or a holiday still leaves a prior
    # RTH session in the window.
    bars = _fetch_bars(days=7)
    if bars.empty:
        return {"available": False, "reason": "no intraday ES data"}

    bars = bars.copy()
    bars["session"] = bars.index.normalize()
    bars["rth"] = [_is_rth(ts) for ts in bars.index]
    # Never let a bar from the future leak in (yfinance occasionally returns a
    # forming bar stamped ahead of the clock).
    bars = bars[bars.index <= now]
    if bars.empty:
        return {"available": False, "reason": "no ES bars at or before now"}

    rth = bars[bars["rth"]]
    if rth.empty:
        return {"available": False, "reason": "no RTH bars in window"}

    rth_dates = sorted(rth["session"].unique())

    # Shared with the schedule so levels and scheduled risk can never describe
    # two different sessions.
    from src.es_session import trading_session_day
    session_day = trading_session_day(now)

    rth_open_ts = session_day + pd.Timedelta(hours=_RTH_OPEN[0], minutes=_RTH_OPEN[1])
    globex_start = _globex_open_at_or_before(now)

    cur_rth = rth[rth["session"] == session_day]
    overnight = bars[(~bars["rth"]) & (bars.index >= globex_start) & (bars.index < rth_open_ts)]

    if not cur_rth.empty:
        mode = "rth"
        anchor = session_day
    elif not overnight.empty:
        mode = "premarket"
        anchor = session_day
    else:
        # Closed with nothing developing — describe the last completed session.
        mode = "last_session"
        anchor = rth_dates[-1]
        cur_rth = rth[rth["session"] == anchor]
        prior_close_ts = rth[rth["session"] < anchor].index.max() if len(rth_dates) > 1 else None
        overnight = bars[(~bars["rth"]) & (bars.index < cur_rth.index.min())]
        if prior_close_ts is not None:
            overnight = overnight[overnight.index > prior_close_ts]

    prior_dates = [d for d in rth_dates if d < anchor]
    prior = prior_dates[-1] if prior_dates else None

    last = float(bars["Close"].iloc[-1])
    last_bar_ts = bars.index[-1]
    bar_age_min = int((now - last_bar_ts).total_seconds() // 60)

    levels: list[dict] = []

    def add(key: str, label: str, value: float | None, group: str, note: str) -> None:
        if value is None or not np.isfinite(value):
            return
        levels.append({
            "key": key, "label": label, "group": group, "note": note,
            "value": round(float(value), 2),
            "distance": round(float(last - value), 2),
            "distance_pct": round(float((last - value) / value * 100), 3),
            "side": "above" if last >= value else "below",
        })

    # Prior RTH session — the conventional "prior day" levels.
    if prior is not None:
        p = rth[rth["session"] == prior]
        add("py_high", "Prior day high", float(p["High"].max()), "Prior session",
            "Prior RTH high — where the cash session topped out.")
        add("py_low", "Prior day low", float(p["Low"].min()), "Prior session",
            "Prior RTH low.")
        add("py_close", "Prior day close", float(p["Close"].iloc[-1]), "Prior session",
            "Settlement reference; gaps from here frame the day's bias.")

    # Overnight — the Globex range of the CURRENT trading session while one is
    # running, not the previous night's.
    on_developing = mode == "premarket"
    if not overnight.empty:
        on_note = ("Globex high so far — still developing, and made on thinner volume, "
                   "so it breaks more easily than an RTH level."
                   if on_developing else
                   "Globex high — made on thinner volume, so it breaks more easily than an RTH level.")
        add("on_high", "Overnight high" + (" (so far)" if on_developing else ""),
            float(overnight["High"].max()), "Overnight", on_note)
        add("on_low", "Overnight low" + (" (so far)" if on_developing else ""),
            float(overnight["Low"].min()), "Overnight",
            "Globex low, same caveat on volume.")

    # The RTH session itself. Skipped entirely in premarket — there is no
    # session yet, and labelling yesterday's values "today" is how the old
    # anchoring misled.
    rth_complete = False
    if not cur_rth.empty:
        rth_complete = bool(cur_rth.index.max() >= anchor + pd.Timedelta(hours=15, minutes=55))
        dev = "" if rth_complete else "Developing "
        add("today_open", "Session open", float(cur_rth["Open"].iloc[0]), "Today",
            "RTH open — the reference for the day's directional read.")
        add("today_high", "Session high", float(cur_rth["High"].max()), "Today", f"{dev or 'Final '}RTH high.")
        add("today_low", "Session low", float(cur_rth["Low"].min()), "Today", f"{dev or 'Final '}RTH low.")
        add("vwap", "Session VWAP", _vwap(cur_rth), "Today",
            "The session's volume-weighted average. Acceptance above or below it is the "
            "most-watched intraday bias line.")

    # Volume profile. The developing profile is only used once the session has
    # enough bars to mean something; before that (and pre-open) it resolves to
    # the last completed session, which is the value area traders actually
    # reference into the open.
    use_developing = (not cur_rth.empty) and len(cur_rth) >= _PROFILE_MIN_BARS
    prof_dates = [anchor] if use_developing else prior_dates[-1:]
    if profile_sessions > 1:
        ceiling = anchor if use_developing else (prior or anchor)
        prof_dates = [d for d in rth_dates if d <= ceiling][-profile_sessions:]
    prof_bars = rth[rth["session"].isin(prof_dates)]
    profile = _volume_profile(prof_bars)
    if profile:
        # Name them for the session they came from. "Value area high" meaning
        # yesterday's on one refresh and today's on the next, with no visible
        # difference, is the kind of ambiguity that gets a level traded wrong.
        pre = "" if use_developing else "Prior "
        whose = ("the developing session" if use_developing
                 else "the prior completed session")
        add("poc", f"{pre}POC" if pre else "Point of control", profile["poc"], "Volume profile",
            f"Most-traded price of {whose} — its fairest price, and a magnet.")
        add("vah", f"{pre}value area high" if pre else "Value area high", profile["vah"], "Volume profile",
            f"Upper edge of the 70% value area from {whose}; outside it price is in discovery.")
        add("val", f"{pre}value area low" if pre else "Value area low", profile["val"], "Volume profile",
            f"Lower edge of the 70% value area from {whose}.")

    levels.sort(key=lambda x: -x["value"])
    nearest = min(levels, key=lambda x: abs(x["distance"])) if levels else None

    # A quote the trader might size off has to declare its own age. Bars are
    # only "stale" against a session that is actually trading — a three-hour-old
    # bar on a Saturday is simply the last print, not a data problem.
    market_live = mode in ("rth", "premarket")
    return {
        "available": True,
        "symbol": SYMBOL,
        "last": round(last, 2),
        "asof": last_bar_ts.isoformat(),
        "bar_age_min": bar_age_min,
        "stale": bool(market_live and bar_age_min > 15),
        "mode": mode,
        "session_date": str(pd.Timestamp(anchor).date()),
        "prior_session_date": str(pd.Timestamp(prior).date()) if prior is not None else None,
        "rth_open_bars": int(len(cur_rth)),
        "rth_complete": rth_complete,
        "overnight_developing": on_developing,
        "overnight_bars": int(len(overnight)),
        "contract_roll_risk": _contract_roll_risk(session_day),
        "profile_is_prior_session": not use_developing,
        "profile_sessions": profile_sessions,
        "profile_session_date": str(pd.Timestamp(prof_dates[-1]).date()) if len(prof_dates) else None,
        "nearest": nearest,
        "levels": levels,
    }
