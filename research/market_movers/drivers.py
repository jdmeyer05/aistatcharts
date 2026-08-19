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

# The driver specs and the regression primitives live in src/market_drivers.py,
# which is what the home page reads. Importing them here rather than restating
# them is the point: if the study and the page ever measured different baskets
# with different code, the page would be citing a methodology nobody had run.
from src.market_drivers import (  # noqa: E402
    MACRO_DRIVERS,
    RISK_COMOVEMENT,
    COMPOSITION,
    WINDOW as _WINDOW,
    load_returns,
    build_factors,
    ols_r2 as _ols_r2,
    rolling_attribution,
)


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
