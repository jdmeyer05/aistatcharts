"""State of the 12-month time-series momentum book.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT. Every other card on the home
page describes the market. This one describes a POSITION BOOK that is already
committed to — the monthly-rebalanced diversified TSMOM system specified in
`Trend_Following_System_Guide.docx` and implemented in
`spy5m_research/tsmom_live.py`. It issues no signal and makes no forecast. It
answers three bookkeeping questions a trader running the system has to answer
anyway:

    what am I supposed to be holding right now?      (`held` — set last month-end)
    what would the rule say if I rebalanced today?   (`live`)
    when do those two get reconciled?                (`next_rebalance`)

That distinction is the entire point. The rule rebalances MONTHLY and holds
weights constant in between; a card that only showed today's raw target would
invite trading the drift, which is not the system and backtests worse (daily
0.62 vs monthly 0.72-0.74).

THE RULE, PORTED EXACTLY. Any change here is a change to a backtested system,
so the constants are copied verbatim and the reasoning for each lives in
`tsmom_live.py`:

  * 32 ETFs, not a curated subset. A hand-picked "core 10" scored 1.01, which
    is the 94th percentile of 200 random 10-market subsets — selection luck.
  * sign of the trailing 252-day return; per-market weight scaled to a 10%
    vol budget off a 60-day EWMA; equal risk across markets.
  * shorts only in the 27 easy-to-borrow financial ETFs; a down-trend in a
    commodity ETF means flat, not short.
  * portfolio scaled to 10% annualised vol on a 126-day window, capped at 6x.
  * monthly rebalance, on the last trading day of the month.

IT FETCHES ITS OWN ADJUSTED PRICES, ON PURPOSE. `ohlcv_cache` mixes adjustment
conventions — yfinance rows are split+dividend adjusted, Polygon rows appended
on top are split-only — and this book is 40% bonds and credit. TLT, IEF, LQD,
HYG and TIP distribute monthly, so a total-return signal taken off that cache
would be wrong by the distribution stream. Same reason `market_drivers.py`
fetches its own. Do not "optimise" this onto the shared cache.

WHAT TO EXPECT FROM IT, so the card can say it rather than imply it: raw
backtest Sharpe 0.72; the Bayesian posterior across the 32-cell parameter grid
is 0.51 with a 95% credible interval of [0.29, 0.73]. Size for 0.5, not 0.7.
Known weakness: fast crashes. Covid was -22.5% — a 12-month signal cannot
reposition in three weeks, and no parameter choice fixes that.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── the production rule's constants, verbatim from tsmom_live.py ──
EPS = 1e-12
LOOKBACK = 252            # trading days ≈ 12 months
VOL_SPAN = 60             # EWMA span for per-market volatility
MKT_VOL_TARGET = 0.10     # per-market risk budget before portfolio scaling
PORT_VOL_TARGET = 0.10    # annualised target for the whole book
PORT_VOL_WIN = 126
MAX_SCALE = 6.0
MIN_LIVE_MARKETS = 8

UNIVERSE: dict[str, str] = {
    "SPY": "equity US", "QQQ": "equity US", "IWM": "equity US",
    "EFA": "equity intl", "EEM": "equity EM", "EWJ": "equity Japan",
    "TLT": "rates long", "IEF": "rates mid", "SHY": "rates short",
    "LQD": "credit IG", "HYG": "credit HY", "TIP": "rates infl",
    "GLD": "metals", "SLV": "metals", "DBC": "commodity broad",
    "USO": "energy", "UNG": "energy", "DBA": "agriculture",
    "UUP": "fx USD", "FXE": "fx EUR", "FXY": "fx JPY",
    "VNQ": "property", "IYR": "property",
    "XLE": "sector", "XLF": "sector", "XLK": "sector", "XLV": "sector",
    "XLU": "sector", "XLP": "sector", "XLI": "sector", "XLB": "sector",
    "XLY": "sector",
}

# Shorts only where borrow is reliable and cheap. Dropping commodity shorts was
# measured to cost nothing (Sharpe 0.72 either way) and removes the UNG/USO/DBA
# borrow problem.
SHORTABLE = {"SPY", "QQQ", "IWM", "EFA", "EEM", "EWJ", "TLT", "IEF", "SHY",
             "LQD", "HYG", "TIP", "GLD", "VNQ", "IYR", "UUP", "FXE", "FXY",
             "XLE", "XLF", "XLK", "XLV", "XLU", "XLP", "XLI", "XLB", "XLY"}

# Research figures, quoted with their source rather than recomputed live. The
# card renders these as "what the backtest said", never as live performance.
RESEARCH = {
    "sharpe_backtest": 0.72,
    "sharpe_posterior": 0.51,
    "sharpe_posterior_ci95": [0.29, 0.73],
    "ann_return_pct": 10.79,
    "ann_vol_pct": 10.9,
    "max_drawdown_pct": -23.2,
    "spy_sharpe": 0.22,
    "spy_max_drawdown_pct": -55.2,
    "turnover_per_year": 12,
    "capital_floor_usd": 25_000,
    "worst_episode": "covid 2020, −22.5% — a 12-month signal cannot reposition in three weeks",
    "source": "spy5m_research/tsmom_live.py + Trend_Following_System_Guide.docx",
    "eras_positive": "all 5 (0.27 / 0.68 / 1.19 / 0.29 / 0.64)",
}

_CACHE_KEY = "tsmom_book:v1"
_TTL_S = 12 * 3600
_MEM: dict = {}


def _fetch_prices(years: int = 6):
    """Adjusted daily closes for the universe.

    `yf.Ticker(t).history()` per ticker, never `yf.download()` — the latter is
    not thread-safe and this runs inside a pool.
    """
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor

    import yfinance as yf

    def one(t: str):
        try:
            h = yf.Ticker(t).history(period=f"{years}y", interval="1d", auto_adjust=True)
            if h is None or len(h) < 300:
                return t, None
            s = h["Close"]
            s.index = s.index.tz_localize(None).normalize()
            return t, s
        except Exception as e:
            logger.debug(f"tsmom price fetch failed for {t}: {e}")
            return t, None

    with ThreadPoolExecutor(max_workers=8) as pool:
        got = list(pool.map(one, list(UNIVERSE)))

    cols = {t: s for t, s in got if s is not None}
    if not cols:
        return None
    P = pd.DataFrame(cols).sort_index()
    # Drop any market that is mostly missing over the window rather than
    # forward-filling it into the risk budget.
    return P.dropna(axis=1, thresh=int(len(P) * 0.5))


def _target_weights(P):
    """Raw per-market weights before portfolio vol scaling. Causal — every
    input at row t is known at t. Ported verbatim from `tsmom_live.py`."""
    import numpy as np
    import pandas as pd

    r = P.pct_change(fill_method=None)
    vol = r.ewm(span=VOL_SPAN, min_periods=40).std() * np.sqrt(252)
    sig = np.sign(P / P.shift(LOOKBACK) - 1.0)
    block = pd.DataFrame({c: (c not in SHORTABLE) for c in P.columns}, index=P.index)
    sig = sig.mask((sig < 0) & block, 0.0)
    w = sig * (MKT_VOL_TARGET / (vol + EPS)).clip(0, 5)
    n = w.notna().sum(axis=1)
    return w.div(n.where(n > 0), axis=0), vol, r


def _portfolio_scale(P, w, r):
    """The k multiplier that targets 10% portfolio vol, capped at 6x.

    Reproduces the backtest's scaling path rather than approximating it: the
    weights are held between month-ends first, exactly as the rule trades, and
    the realised vol is measured on THAT series. Scaling off a daily-rebalanced
    series would measure a book nobody holds.
    """
    import numpy as np
    import pandas as pd

    me = P.resample("ME").last().index
    # A bare boolean ARRAY here raises "conditional must be same shape as self"
    # against a DataFrame — it has to be a row-indexed Series so pandas
    # broadcasts it across the columns.
    reb = pd.Series(P.index.isin(me), index=P.index)
    held = w.shift(1).where(reb).ffill()
    gross = (held * r).sum(axis=1)
    live = held.notna().sum(axis=1) >= MIN_LIVE_MARKETS
    x = gross[live]
    rv = x.rolling(PORT_VOL_WIN, min_periods=60).std() * np.sqrt(252)
    k = (PORT_VOL_TARGET / (rv + EPS)).clip(0, MAX_SCALE).shift(1)
    # The rebalance dates themselves, so the held book can be sized with the
    # scale that prevailed WHEN IT WAS SET rather than with today's. Those
    # differ by the portfolio-vol drift over the month, and quoting today's
    # scale on last month's weights would describe a book nobody holds.
    reb_dates = P.index[reb.values]
    return k.dropna(), held, reb_dates


def _book_rows(w_scaled, trend, vol, strength_median):
    rows = []
    for c in sorted(w_scaled.index):
        wt = float(w_scaled.get(c, 0.0) or 0.0)
        tr = trend.get(c)
        vl = vol.get(c)
        if tr is None or (isinstance(tr, float) and tr != tr):
            continue
        strength = abs(float(tr)) / float(vl) if vl and vl == vl and vl > 0 else None
        rows.append({
            "ticker": c,
            "asset_class": UNIVERSE.get(c, ""),
            "return_12m_pct": round(float(tr) * 100, 1),
            "ann_vol_pct": round(float(vl) * 100, 1) if vl == vl else None,
            "side": "long" if wt > 1e-6 else "short" if wt < -1e-6 else "flat",
            "weight_pct": round(wt * 100, 1),
            "trend_strength": round(strength, 2) if strength is not None else None,
            # The one improvement that beat a de Prado meta-labelling classifier
            # built for the same job: keep only markets whose |12m return|/vol is
            # above the cross-sectional median. Sharpe 0.62 -> 0.68, maxDD
            # -23.6% -> -18.2%. Flagged, not applied — the shipped production
            # rule does not include it, and this card reports the rule as run.
            "above_strength_median": (
                None if strength is None or strength_median is None
                else bool(strength >= strength_median)
            ),
        })
    return rows


def _next_rebalance(P):
    """Last trading day of the current month, from the price index itself.

    Derived from observed trading days rather than a calendar rule, so
    Thanksgiving-week and holiday month-ends are right without a holiday table.
    The final day of the CURRENT month is only known once it has traded, so for
    the tail of the index this is the best estimate available: the last session
    the index already contains for this month, or today's month-end if later
    sessions have not happened yet.
    """
    import pandas as pd

    last = P.index[-1]
    month_end = (pd.Timestamp(last).to_period("M").end_time).normalize()
    # Trading days already observed in this month.
    this_month = P.index[(P.index.year == last.year) & (P.index.month == last.month)]
    # Business days remaining to the calendar month end, exclusive of today.
    remaining = pd.bdate_range(last + pd.Timedelta(days=1), month_end)
    return {
        "estimated_date": str((remaining[-1] if len(remaining) else last).date()),
        "sessions_away": int(len(remaining)),
        "sessions_this_month": int(len(this_month)),
        "note": (
            "Business days to the calendar month end, holidays not deducted — "
            "so this can read one session long in a month containing a holiday."
        ),
    }


def _compute() -> dict:
    import numpy as np
    import pandas as pd

    P = _fetch_prices()
    if P is None or P.shape[1] < MIN_LIVE_MARKETS or len(P) < LOOKBACK + 60:
        return {
            "available": False,
            "reason": (
                f"needs {LOOKBACK + 60} sessions across at least {MIN_LIVE_MARKETS} "
                f"markets; got {0 if P is None else len(P)} x "
                f"{0 if P is None else P.shape[1]}"
            ),
        }

    w_raw, vol, r = _target_weights(P)
    k, held_raw, reb_dates = _portfolio_scale(P, w_raw, r)
    if k.empty:
        return {"available": False, "reason": "portfolio vol window not yet filled"}

    k_now = float(k.iloc[-1])
    last = P.index[-1]

    # The scale in force when the book was last set. Falls back to today's only
    # if no rebalance date sits inside the window with a defined k, which would
    # mean the book has never been set — worth being explicit about rather than
    # silently substituting.
    past_reb = [d for d in reb_dates if d <= last and d in k.index]
    last_reb = past_reb[-1] if past_reb else None
    k_held = float(k.loc[last_reb]) if last_reb is not None else k_now

    # `live` = the rule applied to today's prices. `held` = the weights the last
    # month-end actually set, which is what should be in the account now.
    live_w = (w_raw.iloc[-1] * k_now).dropna()
    held_w = (held_raw.iloc[-1] * k_held).dropna()

    trend = (P.iloc[-1] / P.shift(LOOKBACK).iloc[-1] - 1.0)
    vol_now = vol.iloc[-1]

    strengths = [abs(float(trend[c])) / float(vol_now[c])
                 for c in trend.index
                 if vol_now.get(c, 0) and vol_now[c] == vol_now[c] and vol_now[c] > 0
                 and trend[c] == trend[c]]
    strength_median = float(np.median(strengths)) if strengths else None

    live_rows = _book_rows(live_w, trend, vol_now, strength_median)
    held_rows = _book_rows(held_w, trend, vol_now, strength_median)
    held_side = {r_["ticker"]: r_["side"] for r_ in held_rows}

    # What changed since the book was last set. A sign flip is the event that
    # matters at the next rebalance; a weight drift is not.
    flips = [
        {
            "ticker": r_["ticker"],
            "from": held_side.get(r_["ticker"], "flat"),
            "to": r_["side"],
            "return_12m_pct": r_["return_12m_pct"],
            # Carried because a flip on a 12-month return of +0.1% is a coin
            # landing on its edge, not a trend change, and the two look
            # identical once reduced to "TLT: short → long".
            "trend_strength": r_["trend_strength"],
        }
        for r_ in live_rows
        if held_side.get(r_["ticker"], "flat") != r_["side"]
    ]

    def _exposure(rows):
        gl = sum(x["weight_pct"] for x in rows if x["weight_pct"] > 0)
        gs = -sum(x["weight_pct"] for x in rows if x["weight_pct"] < 0)
        return {
            "gross_long_pct": round(gl, 1),
            "gross_short_pct": round(gs, 1),
            "net_pct": round(gl - gs, 1),
            "total_gross_pct": round(gl + gs, 1),
            "n_long": sum(1 for x in rows if x["side"] == "long"),
            "n_short": sum(1 for x in rows if x["side"] == "short"),
            "n_flat": sum(1 for x in rows if x["side"] == "flat"),
        }

    return {
        "available": True,
        "asof": str(pd.Timestamp(last).date()),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "n_markets": int(P.shape[1]),
        "portfolio_scale": round(k_now, 2),
        "portfolio_scale_held": round(k_held, 2),
        "portfolio_scale_capped": bool(k_now >= MAX_SCALE - 1e-9),
        "last_rebalance": str(last_reb.date()) if last_reb is not None else None,
        "held": {"rows": held_rows, "exposure": _exposure(held_rows)},
        "live": {"rows": live_rows, "exposure": _exposure(live_rows)},
        "flips_since_rebalance": flips,
        "next_rebalance": _next_rebalance(P),
        "trend_strength_median": round(strength_median, 2) if strength_median else None,
        "research": RESEARCH,
        "rule": {
            "lookback_days": LOOKBACK,
            "vol_span": VOL_SPAN,
            "market_vol_target": MKT_VOL_TARGET,
            "portfolio_vol_target": PORT_VOL_TARGET,
            "max_scale": MAX_SCALE,
            "rebalance": "monthly, last trading day",
            "shortable": sorted(SHORTABLE),
        },
        # Said out loud on the card. Gross runs ~190% of NAV because equal-risk
        # weighting puts large notional in low-vol bond ETFs, and that needs a
        # margin account — a cash account halves the vol target for the same
        # Sharpe.
        "caveats": [
            "Gross exposure runs near 190% of NAV — equal-risk weighting puts large "
            "notional in low-volatility bond ETFs. Margin account assumed; a cash "
            "account runs the same rule at half the vol target for the same Sharpe.",
            "Whole-share rounding is the practical floor on account size: the error is "
            "4.5% of NAV at $25k and 1.3% at $100k.",
            "ETF implementation is short-term taxed. The futures version gets Section "
            "1256 60/40 treatment and is what managed futures actually uses.",
            "Weights are set at the month-end and held. The live column is what the rule "
            "would say today, not a position to take today.",
        ],
    }


def book(force: bool = False) -> dict:
    """Cached book state. 12h TTL in memory and in Supabase.

    The rule moves once a month; the prices behind it move once a day. Nothing
    here justifies a shorter TTL, and the fetch is 32 yfinance calls.
    """
    from time import time as _now

    hit = _MEM.get("v")
    if hit and not force and (_now() - hit[0]) < _TTL_S:
        return hit[1]

    if not force:
        try:
            from src._cache_util import _supabase_get
            got = _supabase_get(_CACHE_KEY)
            if got:
                updated, value = got
                age = (datetime.utcnow() - updated).total_seconds()
                if age < _TTL_S and isinstance(value, dict) and value.get("available"):
                    _MEM["v"] = (_now(), value)
                    return value
        except Exception as e:
            logger.debug(f"tsmom book cache read failed: {e}")

    try:
        out = _compute()
    except Exception as e:
        logger.warning(f"tsmom book compute failed: {e}")
        return {"available": False, "reason": str(e)}

    # Never cache an unavailable result — a transient yfinance failure would
    # otherwise pin the card to its error state for twelve hours.
    if out.get("available"):
        _MEM["v"] = (_now(), out)
        try:
            from src._cache_util import _supabase_put
            _supabase_put(_CACHE_KEY, out)
        except Exception as e:
            logger.debug(f"tsmom book cache write failed: {e}")
    return out


def prewarm() -> None:
    """Called from the API's startup warm-up. 32 yfinance fetches cold."""
    try:
        b = book()
        if b.get("available"):
            logger.info(f"TSMOM book pre-warmed ({b['n_markets']} markets, {b['asof']})")
        else:
            logger.warning(f"TSMOM book pre-warm unavailable: {b.get('reason')}")
    except Exception as e:
        logger.warning(f"TSMOM book pre-warm failed: {e}")
