"""Does a move in the priced Fed path predict anything, or only describe it?

The question the card cannot answer about itself. `cumulative_bp` is a level the
rates market agrees on, so the null is strong going in: a number every desk sees
should already be in the price of everything that keys off it. Two prior results
on this platform point the same way — the Polymarket-vs-ZQ spread was a coin
flip once the estimator's own noise came out, and of 23 dated macro events only
nonfarm payrolls survived FDR.

WHAT IS TESTED. The predictor is the CHANGE in the priced path (1-day and 5-day),
not its level: a level test on a two-year sample with one big regime swing from
cuts-priced to hikes-priced would mostly measure that swing. The target is the
forward return of each asset over 1, 5 and 20 sessions.

THREE THINGS THAT WOULD OTHERWISE FAKE A RESULT:

  1. LOOK-AHEAD. A settlement is published after its session closes, so the
     predictor formed on date D is matched to returns starting from the NEXT
     session. `tradable_from` carries that offset and this module refuses to run
     without it.

  2. OVERLAPPING WINDOWS. A 20-day forward return sampled daily reuses 19 of its
     20 days, so ordinary standard errors are far too small. t-stats here are
     Newey-West with lag = horizon, which is the correction the swing-screen work
     also needed.

  3. SEARCHING A GRID. Four assets times three horizons times two predictors is
     24 tests, and at p<0.05 roughly one will clear by chance. Benjamini-Hochberg
     is applied across the whole grid and the FDR verdict is what counts — a
     raw p-value in this table means nothing on its own.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
SERIES_PARQUET = os.path.join(HERE, "priced_path.parquet")

# SPY for equities, TLT for duration (the most direct rates expression), GLD for
# the real-rate channel, UUP for the dollar. Each is a different reason the path
# could matter, so a null across all four is a more informative null.
ASSETS = ["SPY", "TLT", "GLD", "UUP"]
HORIZONS = [1, 5, 20]
PREDICTORS = ["d1_bp", "d5_bp"]


def _newey_west_t(x: np.ndarray, y: np.ndarray, lag: int) -> tuple[float, float, int]:
    """Slope t-stat of y on x with Newey-West errors. Returns (beta, t, n)."""
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    n = len(x)
    if n < 30:
        return np.nan, np.nan, n
    X = np.column_stack([np.ones(n), x])
    beta = np.linalg.lstsq(X, y, rcond=None)[0]
    resid = y - X @ beta
    XtX_inv = np.linalg.inv(X.T @ X)

    S = (resid[:, None] * X).T @ (resid[:, None] * X)
    for L in range(1, min(lag, n - 1) + 1):
        w = 1.0 - L / (lag + 1.0)
        u = (resid[:, None] * X)
        G = u[L:].T @ u[:-L]
        S += w * (G + G.T)
    cov = XtX_inv @ S @ XtX_inv
    se = float(np.sqrt(max(cov[1, 1], 0.0)))
    if se == 0:
        return float(beta[1]), np.nan, n
    return float(beta[1]), float(beta[1] / se), n


def _p_from_t(t: float, n: int) -> float:
    if not np.isfinite(t):
        return np.nan
    from scipy import stats
    return float(2 * (1 - stats.t.cdf(abs(t), df=max(n - 2, 1))))


def _bh(pvals: pd.Series, q: float = 0.05) -> pd.Series:
    """Benjamini-Hochberg: True where the discovery survives at FDR q."""
    p = pvals.dropna().sort_values()
    m = len(p)
    if m == 0:
        return pd.Series(dtype=bool)
    thresh = pd.Series(np.arange(1, m + 1) / m * q, index=p.index)
    passed = p <= thresh
    if not passed.any():
        return pd.Series(False, index=pvals.index)
    cutoff = p[passed].max()
    return pvals <= cutoff


def load_prices(assets=ASSETS) -> pd.DataFrame:
    from src.ohlcv_cache import fetch_ohlcv
    out = {}
    for tk in assets:
        df = fetch_ohlcv(tk, lookback_days=1260)
        if df is None or df.empty:
            print(f"  {tk}: NO DATA — dropped from the grid")
            continue
        # fetch_ohlcv returns a `date` index on the Supabase path and a tz-aware
        # DatetimeIndex on the Polygon path; comparing the two raises, so both are
        # normalised to plain dates here.
        idx = pd.to_datetime(df.index)
        if getattr(idx, "tz", None) is not None:
            idx = idx.tz_localize(None)
        s = pd.Series(pd.to_numeric(df["Close"], errors="coerce").values,
                      index=idx.date).dropna()
        out[tk] = s[~s.index.duplicated(keep="last")].sort_index()
    return pd.DataFrame(out)


def run(q: float = 0.05) -> pd.DataFrame:
    path = pd.read_parquet(SERIES_PARQUET)
    if "tradable_from" not in path.columns:
        raise RuntimeError("series lacks `tradable_from` — rebuild it; without "
                           "that offset every test below is fitted, not forward")

    path = path.dropna(subset=["tradable_from"]).copy()
    path["d1_bp"] = path["cumulative_bp"].diff()
    path["d5_bp"] = path["cumulative_bp"].diff(5)
    path["entry"] = pd.to_datetime(path["tradable_from"]).dt.date

    px = load_prices()
    print(f"prices: {list(px.columns)}  {px.index.min()} to {px.index.max()}")

    rows = []
    for tk in px.columns:
        s = px[tk].dropna()
        pos = pd.Series(np.arange(len(s)), index=s.index)
        for h in HORIZONS:
            fwd = (s.shift(-h) / s - 1.0) * 100.0     # percent
            for pred in PREDICTORS:
                # Match each as-of row to the entry session, then read that
                # session's forward return. An entry date with no bar (holiday)
                # drops out rather than being filled forward onto a stale price.
                m = path[["entry", pred]].dropna()
                m = m[m["entry"].isin(pos.index)]
                y = fwd.reindex(m["entry"]).values
                x = m[pred].values
                beta, t, n = _newey_west_t(x, y, lag=h)
                # A null needs a size attached or it is just a shrug. The CI is
                # what bounds the edge: "no effect" and "an effect too small to
                # find in 500 overlapping observations" are different claims,
                # and only the interval separates them.
                se = abs(beta / t) if (np.isfinite(t) and t != 0) else np.nan
                rows.append({"asset": tk, "horizon_d": h, "predictor": pred,
                             "beta_pct_per_bp": beta, "t_nw": t, "n": n,
                             "p_raw": _p_from_t(t, n),
                             "ci_lo": beta - 1.96 * se, "ci_hi": beta + 1.96 * se})

    res = pd.DataFrame(rows)
    res["survives_fdr"] = _bh(res["p_raw"], q=q)
    return res.sort_values("p_raw").reset_index(drop=True)


if __name__ == "__main__":
    res = run()
    pd.set_option("display.width", 140)
    print()
    print(res.round(4).to_string(index=False))
    n_sig = int(res["survives_fdr"].sum())
    print()
    print(f"{int((res['p_raw'] < 0.05).sum())} of {len(res)} cells at raw p<0.05 "
          f"(about {0.05*len(res):.1f} expected by chance)")
    print(f"{n_sig} survive Benjamini-Hochberg at FDR 5%")

    # What a 1-SD day in the predictor could be worth at the TOP of each interval.
    # This is the number that says how big an edge the sample can still hide.
    path = pd.read_parquet(SERIES_PARQUET)
    sd1 = float(path["cumulative_bp"].diff().std())
    print()
    print(f"a 1-SD daily revision is {sd1:.1f}bp; at the 95% upper bound that buys:")
    for _, r in res.nsmallest(4, "p_raw").iterrows():
        print(f"  {r['asset']:>4} {int(r['horizon_d']):>3}d on {r['predictor']}: "
              f"point {r['beta_pct_per_bp']*sd1:+.3f}%, "
              f"bound [{r['ci_lo']*sd1:+.3f}%, {r['ci_hi']*sd1:+.3f}%]")
