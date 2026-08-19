"""What is moving the tape right now, and how that has changed.

THREE GROUPS, AND THE SPLIT IS THE POINT. Regressing SPY on SMH and calling the
R² "explanatory power" is regressing the market on a piece of itself; it will
always look enormous and it explains nothing. So:

  MACRO — rates, the dollar, oil, gold. Separate markets with their own
  participants and their own reasons to move. This is the set whose rolling R²
  is reported as the headline, and whose internal ranking is worth reading.

  RISK CO-MOVEMENT — high yield against duration. Measured, and then held out of
  the headline ranking on purpose. Credit topped every single year of this
  sample with a same-day correlation of +0.74, which is not the discovery it
  looks like: HYG is itself a risk asset, and equities and high yield selling
  off together is close to a definition rather than an explanation. It is the
  same reason VIX is absent. Its incremental R² over the macro set IS reported,
  because "how much does credit add once macro is accounted for" is a real
  question — it just is not "what drove the tape".

  COMPOSITION — equal-weight versus cap-weight, semis versus the index,
  defensives versus the index. These describe WHAT KIND of tape it is, not what
  drove it. Correlations only; they never enter an R².

CONTEMPORANEOUS, WHICH MEANS NOT PREDICTIVE AND NOT TRADEABLE. Every number here
is same-day. That a rising dollar coincided with equity weakness this quarter
says nothing about tomorrow, and the lagged column is included precisely so the
absence of a next-day relationship is visible rather than assumed away.

VIX IS DELIBERATELY ABSENT. It is a near-mechanical inverse of the same-day SPX
move; including it would produce a large R² that carries no information about
what moved anything.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# label -> (long leg, short leg or None)
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

_WINDOW = 126          # ~6 months of sessions


def load_returns(tickers: list[str], start: str = "2011-06-01") -> pd.DataFrame:
    """Daily returns, one column per ticker.

    ADJUSTED CLOSES. TLT, HYG and IEF all distribute MONTHLY — HYG's yield puts
    roughly half a percent of phantom drop into one session every month — so on
    unadjusted prices the credit spread carries a recurring step that is an
    accounting event, not a market one. Every correlation and R² below would be
    measuring it.

    `Ticker().history()` in a loop, never `download()` — the batch API is not
    thread-safe and has silently returned partially-empty frames on this
    platform before.
    """
    import yfinance as yf
    cols = {}
    for tk in tickers:
        try:
            df = yf.Ticker(tk).history(start=start, interval="1d", auto_adjust=True)
            if df is None or df.empty:
                logger.warning(f"drivers: no bars for {tk}")
                continue
            s = df["Close"].copy()
            s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
            cols[tk] = s
        except Exception as e:
            logger.warning(f"drivers: {tk} failed ({e})")
    if not cols:
        raise RuntimeError("no driver data")
    px = pd.DataFrame(cols).sort_index()
    return px.pct_change(fill_method=None).dropna(how="all")


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


def _ols_r2(y: np.ndarray, X: np.ndarray) -> float:
    """R² of an OLS fit with an intercept. Returns nan on a singular design."""
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


def rolling_attribution(spy: pd.Series, factors: pd.DataFrame,
                        window: int = _WINDOW) -> pd.DataFrame:
    """Rolling total R² and each factor's incremental contribution.

    A factor's share is the drop in R² when it alone is removed, so heavily
    correlated factors split the credit rather than each claiming it. The
    increments therefore do NOT sum to the total, and that gap is itself
    informative: a large one means the drivers were moving together.
    """
    df = pd.concat([spy.rename("spy"), factors], axis=1).dropna()
    names = list(factors.columns)
    rows = []
    for end in range(window, len(df) + 1):
        chunk = df.iloc[end - window:end]
        y = chunk["spy"].to_numpy()
        X = chunk[names].to_numpy()
        full = _ols_r2(y, X)
        rec = {"date": chunk.index[-1], "r2_total": full}
        for i, nm in enumerate(names):
            reduced = _ols_r2(y, np.delete(X, i, axis=1))
            rec[nm] = (full - reduced) if (full == full and reduced == reduced) else np.nan
        rows.append(rec)
    return pd.DataFrame(rows).set_index("date")


def correlation_table(spy: pd.Series, factors: pd.DataFrame,
                      by_year: bool = True) -> dict:
    """Same-day and next-day correlations with SPY.

    The lagged column is the honest control: if a factor's same-day correlation
    is large and its next-day correlation is zero, the relationship is
    accounting for what happened, not anticipating it.
    """
    df = pd.concat([spy.rename("spy"), factors], axis=1).dropna()
    out: dict = {"full": {}, "by_year": {}}
    for nm in factors.columns:
        same = float(df["spy"].corr(df[nm]))
        nxt = float(df["spy"].shift(-1).corr(df[nm]))
        out["full"][nm] = {"same_day": round(same, 4), "next_day": round(nxt, 4)}
    if by_year:
        for yr, sub in df.groupby(df.index.year):
            if len(sub) < 60:
                continue
            out["by_year"][str(yr)] = {
                nm: round(float(sub["spy"].corr(sub[nm])), 4) for nm in factors.columns
            }
    return out


def yearly_shares(attr: pd.DataFrame, names: list[str]) -> dict:
    """Average incremental R² per factor per year, plus that year's ranking."""
    out: dict = {}
    for yr, sub in attr.groupby(attr.index.year):
        if len(sub) < 60:
            continue
        shares = {nm: round(float(sub[nm].mean()), 4) for nm in names if nm in sub}
        ranked = sorted(shares.items(), key=lambda kv: -kv[1])
        out[str(yr)] = {
            "r2_total_mean": round(float(sub["r2_total"].mean()), 4),
            "shares": shares,
            "ranking": [nm for nm, _ in ranked],
        }
    return out


def run(start: str = "2011-06-01", window: int = _WINDOW) -> dict:
    tickers = sorted({t for spec in (MACRO_DRIVERS, RISK_COMOVEMENT, COMPOSITION)
                      for pair in spec.values() for t in pair if t} | {"SPY"})
    rets = load_returns(tickers, start=start)
    spy = rets["SPY"]

    macro = build_factors(rets, MACRO_DRIVERS)
    credit = build_factors(rets, RISK_COMOVEMENT)
    comp = build_factors(rets, COMPOSITION)

    attr = rolling_attribution(spy, macro, window=window)
    names = list(macro.columns)

    # How much credit adds ONCE macro is accounted for. Reported on its own so
    # the headline ranking is readable, and so the size of the overlap between
    # "credit sold off" and "equities sold off" is visible rather than hidden
    # inside a single R².
    with_credit = pd.concat([macro, credit], axis=1)
    attr_credit = rolling_attribution(spy, with_credit, window=window)

    latest = attr.iloc[-1] if len(attr) else None
    credit_latest = attr_credit.iloc[-1] if len(attr_credit) else None
    credit_name = list(credit.columns)[0] if len(credit.columns) else None

    return {
        "window_sessions": window,
        "sessions": int(len(spy)),
        "first": str(spy.index[0].date()),
        "last": str(spy.index[-1].date()),
        "macro": {
            "current": ({"as_of": str(attr.index[-1].date()),
                         "r2_total": round(float(latest["r2_total"]), 4),
                         "shares": {nm: round(float(latest[nm]), 4) for nm in names},
                         "ranking": sorted(names, key=lambda nm: -float(latest[nm]))}
                        if latest is not None else None),
            "by_year": yearly_shares(attr, names),
            "correlations": correlation_table(spy, macro),
        },
        "risk_comovement": {
            "current": ({"as_of": str(attr_credit.index[-1].date()),
                         "r2_macro_only": round(float(latest["r2_total"]), 4),
                         "r2_with_credit": round(float(credit_latest["r2_total"]), 4),
                         "credit_incremental_r2": round(float(credit_latest[credit_name]), 4)}
                        if credit_latest is not None and credit_name else None),
            "by_year": yearly_shares(attr_credit, list(with_credit.columns)),
            "correlations": correlation_table(spy, credit),
            "note": ("Held out of the headline ranking deliberately. HYG is itself a "
                     "risk asset; equities and high yield falling together is closer to a "
                     "definition than an explanation, which is why credit topped every "
                     "year of this sample. Its incremental R² over macro is the part "
                     "that is actually a finding."),
        },
        "composition": {
            "correlations": correlation_table(spy, comp),
            "note": ("Descriptive, not explanatory. These are equity spreads against the "
                     "index they are partly made of, so their co-movement with SPY is in "
                     "part arithmetic. They say what kind of tape it was."),
        },
        "attribution_series": {
            "dates": [str(d.date()) for d in attr.index],
            "r2_total": [round(float(v), 4) for v in attr["r2_total"]],
            **{nm: [round(float(v), 4) if v == v else None for v in attr[nm]] for nm in names},
        },
    }
