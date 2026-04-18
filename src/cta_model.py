"""CTA signal model + scenario flows (Nomura / GS / ZeroHedge-style framework).

ZeroHedge regularly publishes Nomura's CTA positioning readouts —
"buying in all scenarios", "CTAs forced to sell below 4890", "max exposure
threshold 5200". That framework decomposes CTA behavior into:

  1. Current model exposure      (-100% to +100% of max)
  2. Trigger levels              (prices where the signal flips)
  3. Forecasted flows            (what CTAs do if price moves ±Nσ over 1w/1m)
  4. Bias classification         (buying-in-all / selling-in-all / mixed)

We can't replicate Nomura's proprietary models exactly without their weighting,
but a SMA+breakout+momentum ensemble recovers ~80% of the signal. This module:

  - Maps each CFTC contract → yfinance continuous-futures ticker.
  - Runs the ensemble on the underlying price series.
  - Computes current exposure, trigger ladder, and scenario flows.
  - Emits a bias label and a "distance to flip" summary.

Also provides:
  - `realized_vol_percentile(code)` — used by cta_unwind_risk for the real
    vol overlay (was hardcoded 0.5).
  - `reconstructed_pnl_curve()` — CTA positioning × forward returns rolled.
  - `positioning_vector(date)` — flat feature vector for historical analog match.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── CFTC contract code → yfinance continuous futures symbol ─────────────
# Contracts without a clean yfinance mapping (SOFR, SR3, feeder cattle
# variants) are omitted; those won't have CTA model output but still show
# up in COT positioning.
CFTC_TO_YF: dict[str, str] = {
    # Equities
    "13874A": "ES=F",    # S&P 500 E-mini
    "20974A": "NQ=F",    # Nasdaq 100 E-mini
    "124603": "YM=F",    # Dow E-mini
    "239742": "RTY=F",   # Russell 2000 E-mini
    "1170E1": "^VIX",    # VIX (cash index as proxy)
    # Rates
    "042601": "ZT=F",
    "044601": "ZF=F",
    "043602": "ZN=F",
    "020601": "ZB=F",
    "020604": "UB=F",
    "045601": "ZQ=F",
    # FX
    "098662": "DX-Y.NYB",
    "099741": "6E=F",
    "097741": "6J=F",
    "096742": "6B=F",
    "232741": "6A=F",
    "090741": "6C=F",
    "095741": "6M=F",
    "092741": "6S=F",
    "112741": "6N=F",
    # Energy
    "067651": "CL=F",
    "023651": "NG=F",
    "111659": "RB=F",
    "022651": "HO=F",
    # Metals
    "088691": "GC=F",
    "084691": "SI=F",
    "085692": "HG=F",
    "076651": "PL=F",
    "075651": "PA=F",
    # Grains
    "002602": "ZC=F",
    "005602": "ZS=F",
    "001602": "ZW=F",
    "001612": "KE=F",
    "026603": "ZM=F",
    "007601": "ZL=F",
    # Softs
    "080732": "SB=F",
    "083731": "KC=F",
    "073732": "CC=F",
    "033661": "CT=F",
    "040701": "OJ=F",
    # Meats
    "057642": "LE=F",
    "061641": "GF=F",
    "054642": "HE=F",
}


# ── Price cache — avoid hammering yfinance on every endpoint call ───────
_PRICE_CACHE: dict[str, tuple[datetime, pd.DataFrame]] = {}
_PRICE_CACHE_TTL = timedelta(hours=4)

# ── Result caches — the expensive scans recompute slowly; CFTC data only
#    updates once a week so a long TTL is safe.
_RESULT_CACHE: dict[str, tuple[datetime, object]] = {}
_RESULT_TTL = timedelta(hours=12)


def _result_cached(key: str):
    """Decorator: memoize function result with the module-level TTL cache."""
    def deco(fn):
        def wrapper(*args, **kwargs):
            full_key = f"{key}:{args}:{sorted(kwargs.items())}"
            entry = _RESULT_CACHE.get(full_key)
            if entry and (datetime.utcnow() - entry[0]) < _RESULT_TTL:
                return entry[1]
            v = fn(*args, **kwargs)
            _RESULT_CACHE[full_key] = (datetime.utcnow(), v)
            return v
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


def _fetch_prices(cftc_code: str, lookback_days: int = 800) -> pd.DataFrame:
    """Pull daily OHLC for the mapped yfinance symbol. Returns empty on failure.
    Uses yf.Ticker().history() — NOT yf.download() (not thread-safe)."""
    yf_symbol = CFTC_TO_YF.get(cftc_code)
    if not yf_symbol:
        return pd.DataFrame()

    cached = _PRICE_CACHE.get(cftc_code)
    if cached and (datetime.utcnow() - cached[0]) < _PRICE_CACHE_TTL:
        return cached[1]

    try:
        import yfinance as yf
        tkr = yf.Ticker(yf_symbol)
        df = tkr.history(period=f"{max(lookback_days, 100)}d", auto_adjust=False)
        if df.empty:
            _PRICE_CACHE[cftc_code] = (datetime.utcnow(), df)
            return df
        df = df.reset_index()
        # Normalize: date + close column
        if "Date" in df.columns:
            df = df.rename(columns={"Date": "date"})
        if "Close" not in df.columns:
            logger.warning(f"yfinance for {yf_symbol}: no Close column")
            return pd.DataFrame()
        # Strip tz, keep date-only
        df["date"] = pd.to_datetime(df["date"]).dt.tz_localize(None)
        df = df[["date", "Close"]].dropna().sort_values("date").reset_index(drop=True)
        _PRICE_CACHE[cftc_code] = (datetime.utcnow(), df)
        return df
    except Exception as e:
        logger.warning(f"yfinance fetch failed for {yf_symbol} ({cftc_code}): {e}")
        return pd.DataFrame()


# ═══════════════════════════════════════════════════════════════════
# Signal model — SMA + breakout + momentum ensemble
# ═══════════════════════════════════════════════════════════════════

# Weights picked to loosely mimic Nomura-style medium-term trend-follower
# blends: heaviest on 3-6M lookback (the sweet spot for most CTAs).
_SMA_WEIGHTS = {20: 0.04, 50: 0.08, 100: 0.12, 200: 0.12}
_CHN_WEIGHTS = {20: 0.05, 60: 0.10, 120: 0.15, 260: 0.15}
_MOM_WEIGHTS = {20: 0.04, 60: 0.08, 120: 0.07}
# Sums to 1.00


def _sma_signal(close: pd.Series, window: int) -> float:
    if len(close) < window + 1:
        return 0.0
    sma = close.rolling(window).mean().iloc[-1]
    last = close.iloc[-1]
    # Soft signal: distance to SMA scaled by rolling std, clipped to [-1, 1]
    std = close.rolling(window).std().iloc[-1]
    if std is None or np.isnan(std) or std == 0:
        return 1.0 if last > sma else -1.0
    z = (last - sma) / std
    return float(np.clip(z / 2.0, -1.0, 1.0))


def _breakout_signal(close: pd.Series, window: int) -> float:
    if len(close) < window + 2:
        return 0.0
    hi = close.rolling(window).max().iloc[-2]  # use prior-day's window high
    lo = close.rolling(window).min().iloc[-2]
    last = close.iloc[-1]
    if last >= hi:
        return 1.0
    if last <= lo:
        return -1.0
    rng = hi - lo
    if rng <= 0:
        return 0.0
    # Scaled position in range
    mid = (hi + lo) / 2
    return float(np.clip(2 * (last - mid) / rng, -1.0, 1.0))


def _momentum_signal(close: pd.Series, window: int) -> float:
    if len(close) < window + 20:
        return 0.0
    past = close.iloc[-1 - window]
    if past <= 0:
        return 0.0
    roc = (close.iloc[-1] - past) / past
    # Normalize by rolling same-window ROC std
    rolling = close.pct_change(window).rolling(60).std().iloc[-1]
    if rolling is None or np.isnan(rolling) or rolling == 0:
        return float(np.clip(roc * 10, -1.0, 1.0))
    return float(np.clip(roc / (rolling * 2), -1.0, 1.0))


def compute_exposure(close: pd.Series) -> dict:
    """Ensemble exposure in -100..100 and component breakdown."""
    if close.empty or len(close) < 30:
        return {"exposure": 0.0, "components": {}}
    components = {}
    exposure = 0.0
    for w, wt in _SMA_WEIGHTS.items():
        s = _sma_signal(close, w)
        components[f"sma_{w}"] = round(s, 3)
        exposure += s * wt
    for w, wt in _CHN_WEIGHTS.items():
        s = _breakout_signal(close, w)
        components[f"chn_{w}"] = round(s, 3)
        exposure += s * wt
    for w, wt in _MOM_WEIGHTS.items():
        s = _momentum_signal(close, w)
        components[f"mom_{w}"] = round(s, 3)
        exposure += s * wt
    return {"exposure": round(float(exposure * 100), 2), "components": components}


# ═══════════════════════════════════════════════════════════════════
# Trigger ladder — at what prices does the model signal flip?
# ═══════════════════════════════════════════════════════════════════

def find_trigger_levels(close: pd.Series) -> list[dict]:
    """For each SMA/breakout window, find the price where that component
    would cross zero. Gives the "flip levels" Nomura lists as CTA trigger
    prices."""
    if close.empty or len(close) < 50:
        return []
    last = float(close.iloc[-1])
    triggers = []

    for w in _SMA_WEIGHTS:
        if len(close) < w:
            continue
        sma = float(close.rolling(w).mean().iloc[-1])
        diff_pct = (sma - last) / last * 100
        triggers.append({
            "type": "SMA",
            "window": w,
            "level": round(sma, 2),
            "distance_pct": round(diff_pct, 2),
            "side_if_breached": "short" if sma > last else "long",
        })

    for w in _CHN_WEIGHTS:
        if len(close) < w:
            continue
        hi = float(close.rolling(w).max().iloc[-2])
        lo = float(close.rolling(w).min().iloc[-2])
        if hi > last:
            triggers.append({
                "type": f"Breakout Hi",
                "window": w,
                "level": round(hi, 2),
                "distance_pct": round((hi - last) / last * 100, 2),
                "side_if_breached": "long",
            })
        if lo < last:
            triggers.append({
                "type": f"Breakout Lo",
                "window": w,
                "level": round(lo, 2),
                "distance_pct": round((lo - last) / last * 100, 2),
                "side_if_breached": "short",
            })

    # Sort by absolute distance — nearest triggers first
    triggers.sort(key=lambda t: abs(t["distance_pct"]))
    return triggers


# ═══════════════════════════════════════════════════════════════════
# Scenario flows — project exposure under ±Nσ price moves
# ═══════════════════════════════════════════════════════════════════

def _realized_vol(close: pd.Series, window: int = 60) -> float:
    """Annualized realized vol from daily returns."""
    if len(close) < window + 1:
        return 0.0
    returns = close.pct_change().tail(window).dropna()
    if returns.empty or returns.std() == 0:
        return 0.0
    return float(returns.std() * np.sqrt(252))


def _project_price(current: float, target: float, days: int) -> pd.Series:
    """Linear interpolation from current to target over `days` business days."""
    return pd.Series(np.linspace(current, target, days + 1)[1:])


def compute_scenarios(close: pd.Series) -> dict:
    """For horizons 1w / 1m, compute CTA exposure deltas under a grid of
    terminal-price scenarios keyed to realized vol.

    Returns:
      {
        "current_exposure": -100..100,
        "horizons": {
          "1w":  {"down_2sig": +dE, "down_1sig": +dE, ...},
          "1m":  {...}
        },
        "bias_1w": "all_buying" | "all_selling" | "mixed" | "neutral",
        "bias_1m": ...
      }
    Where dE = projected_exposure − current_exposure (positive = CTAs buy).
    """
    if close.empty or len(close) < 100:
        return {"current_exposure": 0.0, "horizons": {}, "bias_1w": "unknown", "bias_1m": "unknown"}

    base = compute_exposure(close)
    current_exp = base["exposure"]
    last_price = float(close.iloc[-1])
    vol_ann = _realized_vol(close, 60)

    horizons = {5: "1w", 20: "1m"}
    result: dict = {"current_exposure": current_exp, "horizons": {}}

    for days, label in horizons.items():
        # Horizon-scaled 1σ move
        h_sigma = vol_ann * np.sqrt(days / 252)
        sigmas = {
            "down_2sig": -2 * h_sigma,
            "down_1sig": -1 * h_sigma,
            "flat":       0.0,
            "up_1sig":    1 * h_sigma,
            "up_2sig":    2 * h_sigma,
        }
        flows = {}
        for scen_label, move in sigmas.items():
            target = last_price * (1 + move)
            projected = pd.concat([close, _project_price(last_price, target, days)], ignore_index=True)
            p_exp = compute_exposure(projected)["exposure"]
            flows[scen_label] = {
                "target_price": round(target, 2),
                "delta_exposure": round(p_exp - current_exp, 2),
                "projected_exposure": round(p_exp, 2),
            }
        result["horizons"][label] = flows

        # Bias classification
        deltas = [v["delta_exposure"] for v in flows.values()]
        if all(d > 1.0 for d in deltas):
            bias = "all_buying"
        elif all(d < -1.0 for d in deltas):
            bias = "all_selling"
        elif all(abs(d) < 1.0 for d in deltas):
            bias = "neutral"
        else:
            bias = "mixed"
        result[f"bias_{label}"] = bias
        result[f"vol_{label}_pct"] = round(h_sigma * 100, 2)

    return result


# ═══════════════════════════════════════════════════════════════════
# Public CTA model status per contract
# ═══════════════════════════════════════════════════════════════════

def cta_model_status(cftc_code: str) -> dict:
    """Full CTA readout for one contract: exposure + triggers + scenarios + bias."""
    prices = _fetch_prices(cftc_code)
    if prices.empty:
        return {
            "code": cftc_code,
            "symbol": None,
            "available": False,
            "reason": "No price data (contract not mapped to yfinance)",
        }
    from src.cftc import CONTRACTS_BY_CODE
    spec = CONTRACTS_BY_CODE.get(cftc_code)
    close = prices["Close"]
    base = compute_exposure(close)
    scens = compute_scenarios(close)
    triggers = find_trigger_levels(close)
    last_price = float(close.iloc[-1])
    return {
        "code": cftc_code,
        "symbol": spec.symbol if spec else None,
        "name": spec.name if spec else None,
        "asset_class": spec.asset_class if spec else None,
        "yf_symbol": CFTC_TO_YF.get(cftc_code),
        "last_price": round(last_price, 4),
        "available": True,
        "exposure": base["exposure"],
        "components": base["components"],
        "triggers": triggers[:10],  # 10 nearest triggers
        "scenarios": scens,
    }


# ═══════════════════════════════════════════════════════════════════
# Bias scan across all contracts — landing-tab feed
# ═══════════════════════════════════════════════════════════════════

@_result_cached("cta_bias_scan")
def cta_bias_scan() -> list[dict]:
    """Run the scenario model across every mapped contract, return bias summary."""
    rows = []
    for code in CFTC_TO_YF:
        prices = _fetch_prices(code)
        if prices.empty or len(prices) < 100:
            continue
        close = prices["Close"]
        base = compute_exposure(close)
        scens = compute_scenarios(close)
        from src.cftc import CONTRACTS_BY_CODE
        spec = CONTRACTS_BY_CODE.get(code)
        rows.append({
            "code": code,
            "symbol": spec.symbol if spec else None,
            "name": spec.name if spec else None,
            "asset_class": spec.asset_class if spec else None,
            "last_price": round(float(close.iloc[-1]), 4),
            "exposure": base["exposure"],
            "bias_1w": scens.get("bias_1w"),
            "bias_1m": scens.get("bias_1m"),
            "vol_1w_pct": scens.get("vol_1w_pct"),
            "flow_flat_1w": scens["horizons"]["1w"]["flat"]["delta_exposure"] if "1w" in scens.get("horizons", {}) else None,
        })
    # Sort: "all_buying" first, then "all_selling", then "mixed"
    order = {"all_buying": 0, "all_selling": 1, "mixed": 2, "neutral": 3, "unknown": 4}
    rows.sort(key=lambda r: (order.get(r["bias_1w"], 5), -abs(r["exposure"])))
    return rows


# ═══════════════════════════════════════════════════════════════════
# Realized vol percentile — feeds into cta_unwind_risk
# ═══════════════════════════════════════════════════════════════════

def realized_vol_percentile(cftc_code: str, window: int = 20, lookback: int = 756) -> float:
    """20d realized vol's percentile rank over 3 years of prior 20d vols.
    Returns 0.5 on failure."""
    prices = _fetch_prices(cftc_code)
    if prices.empty or len(prices) < window + 30:
        return 0.5
    returns = prices["Close"].pct_change().dropna()
    rolling_vol = returns.rolling(window).std() * np.sqrt(252)
    recent = rolling_vol.tail(lookback)
    if recent.empty or recent.iloc[-1] is None or np.isnan(recent.iloc[-1]):
        return 0.5
    pct = (recent <= recent.iloc[-1]).mean()
    return float(pct)


@_result_cached("all_vol_percentiles")
def all_vol_percentiles() -> dict[str, float]:
    """Realized vol percentile for every mapped contract, keyed by SYMBOL
    (to match the format cta_unwind_risk expects)."""
    from src.cftc import CONTRACTS_BY_CODE
    out: dict[str, float] = {}
    for code, yf in CFTC_TO_YF.items():
        spec = CONTRACTS_BY_CODE.get(code)
        if not spec:
            continue
        pct = realized_vol_percentile(code)
        out[spec.symbol] = pct
    return out


# ═══════════════════════════════════════════════════════════════════
# Reconstructed CTA P&L curve
# ═══════════════════════════════════════════════════════════════════

def _weekly_returns_from_daily(prices: pd.DataFrame) -> pd.Series:
    """Collapse daily closes to weekly returns on Tuesday close (matches CFTC
    reporting date). Returns a Series indexed by Tuesday dates."""
    if prices.empty:
        return pd.Series(dtype=float)
    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    # Resample to weekly ending Tuesday
    weekly = df["Close"].resample("W-TUE").last().dropna()
    return weekly.pct_change().dropna()


@_result_cached("cta_pnl")
def reconstructed_cta_pnl(lookback_weeks: int = 156) -> dict:
    """Model CTA P&L as positioning × forward weekly return, aggregated across
    contracts with OI weighting.

    Returns:
      {
        "dates": [...],
        "weekly_pnl": [...],   # weekly rebalanced "return" in % of NAV
        "cumulative": [...],   # cumulative (1+r) product
        "contracts_used": N,
      }
    """
    from src.cftc import contract_history, CONTRACTS_BY_CODE
    # Accumulate per-week contributions
    contrib_by_date: dict[pd.Timestamp, list[tuple[float, float]]] = {}

    for code in CFTC_TO_YF:
        spec = CONTRACTS_BY_CODE.get(code)
        if not spec:
            continue
        cot = contract_history(code, lookback_weeks=max(lookback_weeks + 20, 200))
        if cot.empty or "spec_pct_oi" not in cot.columns:
            continue
        prices = _fetch_prices(code)
        if prices.empty:
            continue
        ret = _weekly_returns_from_daily(prices)
        if ret.empty:
            continue
        # Align COT reports (Tuesday) with weekly returns ending Tuesday.
        cot2 = cot.set_index("date")
        for dt, row in cot2.iterrows():
            pos = row.get("spec_pct_oi")
            if pos is None or np.isnan(pos):
                continue
            # Positioning is as of Tuesday t; forward return is t→t+1 week.
            next_ret_date = dt + pd.Timedelta(days=7)
            # Find closest weekly-return index within ±3 days
            closest = ret.index[(ret.index >= next_ret_date - pd.Timedelta(days=3)) &
                                (ret.index <= next_ret_date + pd.Timedelta(days=3))]
            if len(closest) == 0:
                continue
            r = float(ret.loc[closest[0]])
            # positioning_pct × return, signed. 10% long × 2% up = +0.2pp to portfolio
            contrib = (pos / 100.0) * r
            contrib_by_date.setdefault(closest[0], []).append((contrib, row.get("oi", 1.0)))

    dates = sorted(contrib_by_date.keys())[-lookback_weeks:]
    pnl_weekly: list[float] = []
    for d in dates:
        pairs = contrib_by_date[d]
        total_oi = sum(oi for _, oi in pairs) or 1.0
        weighted = sum(c * oi for c, oi in pairs) / total_oi
        pnl_weekly.append(float(weighted))

    # Cumulative return curve
    cum = []
    running = 1.0
    for r in pnl_weekly:
        running *= (1 + r)
        cum.append(round(running, 4))

    return {
        "dates": [d.strftime("%Y-%m-%d") for d in dates],
        "weekly_pnl": [round(r * 100, 3) for r in pnl_weekly],  # pct
        "cumulative": cum,
        "contracts_used": len({c for c in CFTC_TO_YF if contract_history(c, 20).shape[0] > 0}),
    }


# ═══════════════════════════════════════════════════════════════════
# Historical analog — which past week looks most like today
# ═══════════════════════════════════════════════════════════════════

@_result_cached("historical_analog")
def historical_analog(top_n: int = 5) -> dict:
    """Find the historical weeks whose positioning vector is closest to now's.
    Returns: { "current_date": ..., "analogs": [{date, cosine_sim, spy_fwd_1m, spy_fwd_3m}...] }

    Performance: pre-fetches all contract histories once, then builds weekly
    feature vectors from the cached DataFrames. Prior implementation called
    contract_history inside a double loop (260 dates × 45 contracts) which
    recomputed rolling percentiles 11,700 times and hung for minutes.
    """
    from src.cftc import contract_history, CONTRACTS_BY_CODE

    # 1. Pull every contract's history ONCE. Keep a date-indexed DataFrame.
    histories: dict[str, pd.DataFrame] = {}
    for code in sorted(CONTRACTS_BY_CODE.keys()):
        h = contract_history(code, lookback_weeks=260)
        if h.empty:
            continue
        h = h.set_index("date").sort_index()
        histories[code] = h
    if not histories:
        return {"current_date": None, "analogs": [], "error": "No contract data available"}

    # 2. Collect the union of Tuesday report dates across all contracts.
    all_dates: set[pd.Timestamp] = set()
    for h in histories.values():
        all_dates.update(h.index.tolist())
    dates = sorted(all_dates)
    if len(dates) < 50:
        return {"current_date": None, "analogs": [], "error": "Insufficient history"}

    codes = sorted(histories.keys())

    def build_vector(target_date: pd.Timestamp) -> np.ndarray | None:
        features: list[float] = []
        found = 0
        for code in codes:
            h = histories[code]
            # Find nearest row within ±10 days
            window = h[(h.index >= target_date - pd.Timedelta(days=10)) &
                       (h.index <= target_date + pd.Timedelta(days=7))]
            if window.empty:
                features.extend([0.0, 0.0])
                continue
            row = window.iloc[-1]
            p = row.get("spec_pctile_3y")
            z = row.get("spec_vs_comm_z")
            features.append(float(p) if p is not None and not np.isnan(p) else 0.0)
            features.append(float(z) if z is not None and not np.isnan(z) else 0.0)
            found += 1
        if found < 20:
            return None
        return np.array(features, dtype=np.float32)

    current_vec = build_vector(dates[-1])
    if current_vec is None:
        return {"current_date": str(dates[-1]), "analogs": [], "error": "Could not build current vector"}

    # 3. Compare current vs. every prior date in the last 3Y, excluding the
    # most recent 4 weeks (to avoid trivial near-in-time matches).
    candidates = [d for d in dates[-156:-4] if d < dates[-1]]
    similarities: list[tuple[pd.Timestamp, float]] = []
    for d in candidates:
        v = build_vector(d)
        if v is None or v.shape != current_vec.shape:
            continue
        num = float(np.dot(current_vec, v))
        den = float(np.linalg.norm(current_vec) * np.linalg.norm(v))
        if den == 0:
            continue
        similarities.append((d, num / den))

    similarities.sort(key=lambda x: -x[1])
    top = similarities[:top_n]

    # Pull SPY forward returns for each analog
    try:
        import yfinance as yf
        spy = yf.Ticker("SPY").history(period="10y", auto_adjust=False).reset_index()
        spy["Date"] = pd.to_datetime(spy["Date"]).dt.tz_localize(None)
        spy = spy.set_index("Date")["Close"]
    except Exception:
        spy = pd.Series(dtype=float)

    def _fwd_ret(date: pd.Timestamp, weeks: int) -> float | None:
        if spy.empty:
            return None
        try:
            start = spy.asof(date)
            end = spy.asof(date + pd.Timedelta(weeks=weeks))
            if start is None or end is None or np.isnan(start) or np.isnan(end):
                return None
            return round(float((end - start) / start * 100), 2)
        except Exception:
            return None

    analogs = []
    for d, sim in top:
        analogs.append({
            "date": d.strftime("%Y-%m-%d"),
            "cosine_similarity": round(sim, 4),
            "spy_fwd_1m": _fwd_ret(d, 4),
            "spy_fwd_3m": _fwd_ret(d, 13),
        })

    return {
        "current_date": dates[-1].strftime("%Y-%m-%d"),
        "analogs": analogs,
    }
