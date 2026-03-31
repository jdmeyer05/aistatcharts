"""Historical Options Data Cache — proper IV percentiles and skew trends from Polygon Options Starter.

Builds on fetch_options_surface_history() to compute and cache daily IV metrics.
All pages can import these functions for historical context without redundant API calls.

Usage:
    from src.options_history import get_historical_iv, get_iv_percentile, get_skew_trend

    hist = get_historical_iv("SPY", days=10)  # DataFrame: date, atm_iv, put_skew, ts_slope, vrp
    pctile = get_iv_percentile("SPY", current_iv=0.25, days=10)  # 0-100
    trend = get_skew_trend("SPY", days=10)  # {direction: "rising"/"falling"/"stable", values: [...]}
"""

import logging
import numpy as np
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)


def _get_rfr():
    try:
        from src.market_data import fetch_fred_series
        df = fetch_fred_series("DGS3MO", periods=5)
        if not df.empty:
            return df["value"].iloc[-1] / 100
    except Exception:
        pass
    return 0.045


def get_historical_iv(ticker, days=10):
    """Fetch historical daily IV metrics from Polygon options data.

    Returns DataFrame with columns: date, spot, atm_iv, put_skew_25d, ts_slope, vrp
    Cached in session state per ticker+days.
    """
    cache_key = f"_opt_hist_{ticker}_{days}"
    if cache_key in st.session_state:
        return st.session_state[cache_key]

    try:
        from src.data_engine import fetch_options_surface_history, fetch_massive_data
        from src.options_models import implied_vol

        hist_raw = fetch_options_surface_history(ticker, days=days, max_contracts=100)
        if not hist_raw or len(hist_raw) < 2:
            st.session_state[cache_key] = pd.DataFrame()
            return pd.DataFrame()

        rfr = _get_rfr()

        # Get HV20 series for VRP computation
        px = fetch_massive_data(ticker, 252)
        hv_by_date = {}
        if px is not None and len(px) > 30:
            hv_series = (px["Close"].pct_change().rolling(20).std() * np.sqrt(252)).dropna()
            for d in hv_series.index:
                hv_by_date[d.strftime("%Y-%m-%d")] = float(hv_series.loc[d])

        rows = []
        for dt, day_data in sorted(hist_raw.items()):
            day_spot = day_data["spot"]
            if day_spot <= 0:
                continue

            # Compute IV for each contract
            ivs_by_strike = {}
            for c in day_data["data"]:
                K = c["strike"]
                dte = c["dte"]
                T = dte / 365
                price = c["close"]
                otype = c["type"]
                if price <= 0 or T <= 0 or K <= 0:
                    continue
                iv = implied_vol(price, day_spot, K, T, rfr, otype)
                if 0.01 < iv < 3.0:
                    moneyness = K / day_spot
                    ivs_by_strike[(K, otype)] = {"iv": iv, "moneyness": moneyness, "dte": dte}

            if len(ivs_by_strike) < 10:
                continue

            # ATM IV: closest to moneyness=1.0 call
            atm_iv = None
            best_dist = 999
            for (K, ot), v in ivs_by_strike.items():
                if ot == "call" and abs(v["moneyness"] - 1.0) < best_dist:
                    best_dist = abs(v["moneyness"] - 1.0)
                    atm_iv = v["iv"]

            if atm_iv is None or atm_iv <= 0:
                continue

            # 25-delta put skew: find put closest to 0.90 moneyness
            put_25d_iv = None
            best_put_dist = 999
            for (K, ot), v in ivs_by_strike.items():
                if ot == "put" and abs(v["moneyness"] - 0.92) < best_put_dist:
                    best_put_dist = abs(v["moneyness"] - 0.92)
                    put_25d_iv = v["iv"]

            put_skew = (put_25d_iv / atm_iv) if put_25d_iv and atm_iv > 0 else None

            # Term structure slope: front vs back ATM
            front_iv, back_iv = None, None
            min_dte, max_dte = 999, 0
            for (K, ot), v in ivs_by_strike.items():
                if ot == "call" and abs(v["moneyness"] - 1.0) < 0.05:
                    if v["dte"] < min_dte:
                        min_dte = v["dte"]
                        front_iv = v["iv"]
                    if v["dte"] > max_dte:
                        max_dte = v["dte"]
                        back_iv = v["iv"]
            ts_slope = None
            if front_iv and back_iv and max_dte > min_dte:
                ts_slope = (back_iv - front_iv) / max(max_dte - min_dte, 1) * 30

            # VRP
            hv20 = hv_by_date.get(dt)
            vrp = (atm_iv - hv20) if hv20 and hv20 > 0 else None

            rows.append({
                "date": dt, "spot": day_spot, "atm_iv": atm_iv,
                "put_skew_25d": put_skew, "ts_slope": ts_slope, "vrp": vrp,
            })

        result = pd.DataFrame(rows)
        st.session_state[cache_key] = result
        return result

    except Exception as e:
        logger.warning(f"Historical IV fetch failed for {ticker}: {e}")
        st.session_state[cache_key] = pd.DataFrame()
        return pd.DataFrame()


def get_iv_percentile(ticker, current_iv, days=10):
    """Rank current IV against historical IV distribution (proper percentile).

    Returns float 0-100 or None if insufficient data.
    """
    hist = get_historical_iv(ticker, days=days)
    if hist.empty or len(hist) < 3 or current_iv <= 0:
        return None
    iv_vals = hist["atm_iv"].dropna().values
    if len(iv_vals) < 3:
        return None
    return float((iv_vals < current_iv).mean() * 100)


def get_skew_trend(ticker, days=10):
    """Analyze put skew trend over N days.

    Returns dict: {direction: "rising"/"falling"/"stable", values: list, change: float}
    """
    hist = get_historical_iv(ticker, days=days)
    if hist.empty or "put_skew_25d" not in hist.columns:
        return {"direction": "stable", "values": [], "change": 0}

    skew_vals = hist["put_skew_25d"].dropna().values
    if len(skew_vals) < 3:
        return {"direction": "stable", "values": list(skew_vals), "change": 0}

    # Linear regression slope
    x = np.arange(len(skew_vals))
    slope = np.polyfit(x, skew_vals, 1)[0]
    change = skew_vals[-1] - skew_vals[0]

    if slope > 0.005:
        direction = "rising"
    elif slope < -0.005:
        direction = "falling"
    else:
        direction = "stable"

    return {"direction": direction, "values": list(skew_vals), "change": float(change)}


def get_ts_slope_history(ticker, days=10):
    """Get term structure slope history.

    Returns list of {date, ts_slope} dicts.
    """
    hist = get_historical_iv(ticker, days=days)
    if hist.empty or "ts_slope" not in hist.columns:
        return []
    return hist[["date", "ts_slope"]].dropna().to_dict(orient="records")


def get_iv_summary(ticker, current_iv, days=10):
    """Get a complete IV context summary for AI prompts and display.

    Returns dict with percentile, vs_avg, trend, skew_trend.
    """
    hist = get_historical_iv(ticker, days=days)
    if hist.empty:
        return None

    iv_vals = hist["atm_iv"].dropna()
    if len(iv_vals) < 3:
        return None

    avg_iv = float(iv_vals.mean())
    pctile = get_iv_percentile(ticker, current_iv, days)
    skew = get_skew_trend(ticker, days)

    return {
        "percentile": pctile,
        "avg_iv_nd": avg_iv,
        "vs_avg_pct": ((current_iv / avg_iv - 1) * 100) if avg_iv > 0 else 0,
        "iv_high": float(iv_vals.max()),
        "iv_low": float(iv_vals.min()),
        "skew_direction": skew["direction"],
        "skew_change": skew["change"],
        "n_days": len(iv_vals),
    }
