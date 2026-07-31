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
from datetime import datetime

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SYMBOL = "ES=F"
_TZ = "America/New_York"
_RTH_OPEN = (9, 30)
_RTH_CLOSE = (16, 0)
_VALUE_AREA = 0.70   # conventional 70% of volume around the POC


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


def es_levels(profile_sessions: int = 1) -> dict:
    """Reference levels for the current/most recent ES session."""
    bars = _fetch_bars(days=5)
    if bars.empty:
        return {"available": False, "reason": "no intraday ES data"}

    bars = bars.copy()
    bars["session"] = bars.index.normalize()
    bars["rth"] = [_is_rth(ts) for ts in bars.index]

    rth = bars[bars["rth"]]
    if rth.empty:
        return {"available": False, "reason": "no RTH bars in window"}

    sessions = sorted(rth["session"].unique())
    today = sessions[-1]
    prior = sessions[-2] if len(sessions) > 1 else None

    cur_rth = rth[rth["session"] == today]
    last = float(bars["Close"].iloc[-1])

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

    # Overnight: everything between the prior RTH close and today's RTH open.
    on = bars[(bars["session"] == today) & (~bars["rth"]) & (bars.index < cur_rth.index.min())] \
        if not cur_rth.empty else bars[(bars["session"] == today) & (~bars["rth"])]
    if prior is not None:
        p_close_ts = rth[rth["session"] == prior].index.max()
        on = bars[(bars.index > p_close_ts) & (~bars["rth"])]
        if not cur_rth.empty:
            on = on[on.index < cur_rth.index.min()]
    if not on.empty:
        add("on_high", "Overnight high", float(on["High"].max()), "Overnight",
            "Globex high — made on thinner volume, so it breaks more easily than an RTH level.")
        add("on_low", "Overnight low", float(on["Low"].min()), "Overnight",
            "Globex low, same caveat on volume.")

    # Today's developing session.
    if not cur_rth.empty:
        add("today_open", "Today's open", float(cur_rth["Open"].iloc[0]), "Today",
            "RTH open — the reference for the day's directional read.")
        add("today_high", "Session high", float(cur_rth["High"].max()), "Today", "Developing RTH high.")
        add("today_low", "Session low", float(cur_rth["Low"].min()), "Today", "Developing RTH low.")
        add("vwap", "Session VWAP", _vwap(cur_rth), "Today",
            "The day's volume-weighted average. Acceptance above or below it is the "
            "most-watched intraday bias line.")

    # Volume profile over the requested number of completed RTH sessions.
    prof_sessions = sessions[-profile_sessions:] if profile_sessions > 0 else []
    prof_bars = rth[rth["session"].isin(prof_sessions)]
    profile = _volume_profile(prof_bars)
    if profile:
        add("poc", "Point of control", profile["poc"], "Volume profile",
            "Price with the most traded volume — the session's fairest price and a magnet.")
        add("vah", "Value area high", profile["vah"], "Volume profile",
            "Upper edge of the 70% value area; outside it price is in discovery.")
        add("val", "Value area low", profile["val"], "Volume profile",
            "Lower edge of the 70% value area.")

    levels.sort(key=lambda x: -x["value"])
    nearest = min(levels, key=lambda x: abs(x["distance"])) if levels else None

    return {
        "available": True,
        "symbol": SYMBOL,
        "last": round(last, 2),
        "asof": bars.index[-1].isoformat(),
        "session_date": str(pd.Timestamp(today).date()),
        "rth_open_bars": int(len(cur_rth)),
        "profile_sessions": profile_sessions,
        "nearest": nearest,
        "levels": levels,
    }
