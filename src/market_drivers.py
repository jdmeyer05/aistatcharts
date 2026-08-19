"""Which markets the tape has actually been moving with, and how that has shifted.

WHAT THIS IS FOR. The home page's regime read used to assert what was driving
markets from whatever the model made of a page of quotes. This measures it
instead: a rolling regression of SPY's daily returns on four macro markets, so
the narrative can say "the tape has moved with gold this quarter, and it moved
with the dollar a year ago" and have a number behind it.

WHAT IT IS NOT, AND THE PROMPT IS TOLD SO EXPLICITLY. Every number here is
SAME-DAY. It is co-movement, not causation and not prediction — the study behind
it (research/market_movers) measured the next-day correlations at essentially
zero for all four. A driver's share says how much of the day's variation moved
alongside it, not what will happen tomorrow and not which way.

WHY IT FETCHES ITS OWN PRICES INSTEAD OF USING THE OHLCV CACHE. The shared cache
mixes two adjustment conventions: yfinance history is split AND dividend
adjusted, while the Polygon bars appended on top of it are split-adjusted only.
For non-distributing GLD the two agree to 1.00000. For monthly distributors the
gap is real and measured — HYG's daily returns correlate 0.954 with the properly
adjusted series, with single-day errors up to 0.52% on six of the last 126
sessions. That is a phantom move on the exact days a dividend was paid, and it
lands directly in an R² whose entire job is to apportion variance between these
assets. So this module pays for its own adjusted fetch and hides the cost behind
a six-hour cache, which is generous for a window that moves one session a day.

CREDIT IS MEASURED AND THEN HELD OUT OF THE RANKING. High yield against duration
topped every single year of the fourteen-year study at +0.74 same-day, which is
not a discovery: HYG is itself a risk asset, and equities and high yield falling
together is closer to a definition than an explanation. It is the same reason VIX
is absent. Its incremental R² over the macro set is reported on its own, because
"how much does credit add once macro is accounted for" is a real question.
"""

from __future__ import annotations

import logging
from time import time as _now

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# label -> (long leg, short leg or None). Shared with the study in
# research/market_movers so the page and the research cannot drift apart.
MACRO_DRIVERS: dict[str, tuple[str, str | None]] = {
    "Rates (TLT)": ("TLT", None),
    "Dollar (DXY)": ("DX-Y.NYB", None),
    "Oil (USO)": ("USO", None),
    "Gold (GLD)": ("GLD", None),
}

RISK_COMOVEMENT: dict[str, tuple[str, str | None]] = {
    "Credit (HYG-IEF)": ("HYG", "IEF"),
}

COMPOSITION: dict[str, tuple[str, str | None]] = {
    "Breadth (RSP-SPY)": ("RSP", "SPY"),
    "Semis (SMH-SPY)": ("SMH", "SPY"),
    "Defensives (XLP-SPY)": ("XLP", "SPY"),
    "Small caps (IWM-SPY)": ("IWM", "SPY"),
}

WINDOW = 126                 # ~6 months of sessions
_LOOKBACK_SESSIONS = 252     # how far back the comparison window ends
_CACHE: dict = {}
_TTL_S = 6 * 3600


# ── price plumbing ────────────────────────────────────────────────

def load_returns(tickers: list[str], start: str = "2023-01-01") -> pd.DataFrame:
    """Daily returns from fully adjusted closes, one column per ticker.

    `auto_adjust=True` is load-bearing, not a default worth leaving to chance —
    see the module docstring. `Ticker().history()` in a loop, never
    `download()`, which is not thread-safe.
    """
    import yfinance as yf
    cols = {}
    for tk in tickers:
        try:
            df = yf.Ticker(tk).history(start=start, interval="1d", auto_adjust=True)
            if df is None or df.empty:
                logger.warning(f"market_drivers: no bars for {tk}")
                continue
            s = df["Close"].copy()
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            cols[tk] = s
        except Exception as e:
            logger.warning(f"market_drivers: {tk} failed ({e})")
    if not cols:
        raise RuntimeError("no driver data")
    return pd.DataFrame(cols).sort_index().pct_change(fill_method=None).dropna(how="all")


def build_factors(rets: pd.DataFrame, spec: dict[str, tuple[str, str | None]]) -> pd.DataFrame:
    out = {}
    for label, (long, short) in spec.items():
        if long not in rets.columns:
            continue
        if short is None:
            out[label] = rets[long]
        elif short in rets.columns:
            out[label] = rets[long] - rets[short]
    return pd.DataFrame(out)


def ols_r2(y: np.ndarray, X: np.ndarray) -> float:
    """R² of an OLS fit with an intercept. nan on a singular design."""
    if len(y) < X.shape[1] + 5:
        return float("nan")
    A = np.column_stack([np.ones(len(y)), X])
    try:
        beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    except np.linalg.LinAlgError:
        return float("nan")
    resid = y - A @ beta
    ss_res = float(resid @ resid)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else float("nan")


def window_attribution(y: np.ndarray, X: np.ndarray, names: list[str]) -> dict:
    """Total R² and each factor's incremental contribution over one window.

    A factor's share is the drop in R² when it alone is removed, so correlated
    factors split the credit instead of each claiming it. The increments do NOT
    sum to the total, and the gap is itself informative: a large one means the
    drivers were moving together.
    """
    full = ols_r2(y, X)
    if full != full:
        return {}
    out = {"r2_total": full}
    for i, nm in enumerate(names):
        reduced = ols_r2(y, np.delete(X, i, axis=1))
        out[nm] = (full - reduced) if reduced == reduced else float("nan")
    return out


def rolling_attribution(spy: pd.Series, factors: pd.DataFrame,
                        window: int = WINDOW) -> pd.DataFrame:
    """The same thing at every session. Used by the study, not by the page."""
    df = pd.concat([spy.rename("spy"), factors], axis=1).dropna()
    names = list(factors.columns)
    rows = []
    for end in range(window, len(df) + 1):
        chunk = df.iloc[end - window:end]
        rec = window_attribution(chunk["spy"].to_numpy(), chunk[names].to_numpy(), names)
        if rec:
            rows.append({"date": chunk.index[-1], **rec})
    return pd.DataFrame(rows).set_index("date")


# ── the board the home page reads ─────────────────────────────────

def _short(label: str) -> str:
    return label.split(" (")[0]


def driver_board(window: int = WINDOW, force: bool = False) -> dict:
    """Current driver ranking plus the same ranking a year ago.

    The year-ago window is the whole point of including this on a page that
    already shows today's cross-asset quotes: what SPY moves with ROTATES, and a
    reader cannot see that rotation in a snapshot. Rates led 2012-15 and 2019-20,
    oil led 2016 and 2018, the dollar led the 2022-24 tightening.

    Never raises. On any failure it returns ``available: False`` and the caller
    leaves the block out of the payload rather than shipping a half-filled one.
    """
    hit = _CACHE.get(window)
    if hit and not force and (_now() - hit[0]) < _TTL_S:
        return hit[1]

    try:
        tickers = sorted({t for spec in (MACRO_DRIVERS, RISK_COMOVEMENT)
                          for pair in spec.values() for t in pair if t} | {"SPY"})
        # Two windows plus slack for holidays and a late-reporting symbol.
        rets = load_returns(tickers, start=(pd.Timestamp.today()
                                            - pd.Timedelta(days=int((window + _LOOKBACK_SESSIONS) * 1.9))
                                            ).strftime("%Y-%m-%d"))
        spy = rets["SPY"]
        macro = build_factors(rets, MACRO_DRIVERS)
        credit = build_factors(rets, RISK_COMOVEMENT)

        names = list(macro.columns)
        if len(names) < 2:
            raise RuntimeError(f"only {len(names)} macro drivers available")

        df = pd.concat([spy.rename("spy"), macro, credit], axis=1).dropna()
        if len(df) < window + 20:
            raise RuntimeError(f"only {len(df)} aligned sessions")

        cur = df.iloc[-window:]
        now_attr = window_attribution(cur["spy"].to_numpy(), cur[names].to_numpy(), names)
        if not now_attr:
            raise RuntimeError("attribution failed on the current window")

        credit_names = list(credit.columns)
        with_credit = names + credit_names
        credit_attr = window_attribution(cur["spy"].to_numpy(),
                                         cur[with_credit].to_numpy(), with_credit)

        prior_attr = {}
        if len(df) >= window + _LOOKBACK_SESSIONS:
            prior = df.iloc[-(window + _LOOKBACK_SESSIONS):-_LOOKBACK_SESSIONS]
            prior_attr = window_attribution(prior["spy"].to_numpy(),
                                            prior[names].to_numpy(), names)

        prior_rank = {}
        if prior_attr:
            for i, nm in enumerate(sorted(names, key=lambda n: -prior_attr.get(n, 0)), start=1):
                prior_rank[nm] = i

        ranking = []
        for i, nm in enumerate(sorted(names, key=lambda n: -now_attr.get(n, 0)), start=1):
            ranking.append({
                "driver": _short(nm),
                "ticker": MACRO_DRIVERS[nm][0],
                "rank": i,
                "share_of_variance": round(float(now_attr.get(nm, 0)), 4),
                "corr_with_spy": round(float(cur["spy"].corr(cur[nm])), 3),
                "rank_a_year_ago": prior_rank.get(nm),
            })

        board = {
            "available": True,
            "window_sessions": int(window),
            "as_of": str(df.index[-1].date()),
            "explained_share": round(float(now_attr["r2_total"]), 4),
            "ranking": ranking,
            "credit_increment": (round(float(credit_attr.get(credit_names[0], 0)), 4)
                                 if credit_attr and credit_names else None),
            "explained_share_a_year_ago": (round(float(prior_attr["r2_total"]), 4)
                                           if prior_attr else None),
            "note": (
                "Same-day co-movement of SPY with four macro markets over the last "
                f"{window} sessions, measured. `share_of_variance` is how much of SPY's "
                "daily variation is lost when that market is dropped from the regression; "
                "shares do not sum to the total because the drivers move together. "
                "`corr_with_spy` carries the sign. NOT predictive: the same study measured "
                "next-day correlations at essentially zero for all four. Credit is measured "
                "separately and kept out of the ranking because high yield is itself a risk "
                "asset, so its co-movement with equities is closer to a definition than an "
                "explanation."
            ),
        }
        _CACHE[window] = (_now(), board)
        return board

    except Exception as e:
        logger.warning(f"market_drivers: board failed ({e})")
        return {"available": False, "error": str(e)}


def prewarm() -> None:
    """Fill the cache at startup so no visitor pays the ~6s cold fetch."""
    try:
        driver_board()
    except Exception as e:
        logger.debug(f"market_drivers prewarm failed: {e}")
