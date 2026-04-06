"""Calendar Spread Scanner — finds the best calendar spread setups across a universe of liquid tickers."""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from scipy.stats import norm as _norm

import html as _html

from src.layout import setup_page, error_boundary, fun_loader, freshness_bar
from src.styles import COLORS
from src.data_engine import fetch_options_chain, polygon_batch_snapshot, fetch_massive_data
from src.options_models import black_scholes, bs_greeks, bs_higher_greeks
from src.metrics_store import save_snapshot, save_batch, percentile_rank, get_metric_timeseries

logger = logging.getLogger(__name__)

setup_page("51_Calendar_Spread_Scanner")

st.title("Calendar Spread Scanner")
st.markdown("Scan for the best calendar spread setups ranked by theta yield, term structure, IV percentile, and liquidity.")
with st.expander("How this scanner works"):
    st.markdown("""
**What it does:** Scans 20 liquid tickers to find the best calendar spread setups right now.

**Strategy:** Buy a longer-dated option, sell a shorter-dated option at the same strike. Profits from:
- **Time decay differential** — the front leg decays faster than the back leg
- **Term structure contango** — back-month IV stays higher, preserving the long leg's value

**How it ranks setups** — the composite score integrates 7 quantitative signals:
- **Theta/Debit × POP** — daily decay yield relative to capital at risk, weighted by probability of profit
- **IVR Band** — where current IV sits vs history. 50-75 IVR is optimal (rich front premium to sell)
- **Front IV / HV20** — is front-month vol overpriced vs realized? Ratio > 1.2 is favorable
- **Liquidity** — graded A-F from open interest, bid-ask width, and volume
- **Earnings Position** — earnings BETWEEN front/back expirations destroys the trade (IV crush kills back leg)
- **Historical Win Rate** — BS-based managed trade simulation over 252 days of price history
- **Term Structure Slope** — contango (back IV > front IV) is favorable; backwardation penalized

**Management framework:**
- **Profit target:** Close at 25-50% of max profit (front expiry is the theoretical max)
- **Stop loss:** Close when spread value drops below debit × (1 - stop%)
- **Time stop:** Close or roll front leg at 7 DTE to avoid gamma/pin risk
""")


# ═══════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════

DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "DIA",
    "AAPL", "TSLA", "NVDA", "AMD", "AMZN", "META", "MSFT", "GOOGL", "NFLX",
    "GLD", "SMH", "XLF", "TLT", "EEM",
    "JPM", "BA",
]

with st.form("cal_scan_form", border=True):
    cc1, cc2, cc3, cc4, cc5 = st.columns(5)
    with cc1:
        front_dte_min = st.number_input("Front min DTE", value=14, min_value=7, max_value=60, step=7)
    with cc2:
        front_dte_max = st.number_input("Front max DTE", value=45, min_value=14, max_value=90, step=7)
    with cc3:
        back_dte_min = st.number_input("Back min DTE", value=35, min_value=21, max_value=120, step=7)
    with cc4:
        back_dte_max = st.number_input("Back max DTE", value=90, min_value=30, max_value=180, step=7)
    with cc5:
        spread_type = st.selectbox("Type", ["call", "put"], index=0)

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        profit_target_pct = st.number_input("Profit target (%)", value=30, min_value=10, max_value=80, step=5,
                                             help="Close when this % of max theoretical profit is captured.")
    with fc2:
        stop_loss_pct = st.number_input("Stop loss (%)", value=50, min_value=10, max_value=90, step=5,
                                         help="Close when spread value drops this % below entry debit.")
    with fc3:
        min_dte_spread = st.number_input("Min DTE gap", value=14, min_value=7, max_value=60, step=7,
                                          help="Minimum days between front and back expiration.")

    ticker_input = st.text_area("Tickers (comma-separated)", value=", ".join(DEFAULT_UNIVERSE), height=68)
    tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

    with st.expander("Position Sizing (Kelly Criterion)"):
        ps1, ps2, ps3 = st.columns(3)
        with ps1:
            account_size = st.number_input("Account size ($)", value=25000, min_value=1000, max_value=10_000_000,
                                            step=5000, format="%d")
        with ps2:
            max_risk_pct = st.number_input("Hard cap per trade (%)", value=5.0, min_value=0.5, max_value=20.0,
                                            step=0.5, format="%.1f")
        with ps3:
            kelly_fraction = st.number_input("Kelly fraction", value=0.5, min_value=0.1, max_value=1.0,
                                              step=0.1, format="%.1f")

    scan = st.form_submit_button("Scan for Calendar Spreads", type="primary", use_container_width=True)


# ═══════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════

@st.cache_data(ttl=86400, show_spinner=False)
def _get_rfr() -> float:
    try:
        from src.market_data import fetch_fred_series
        df = fetch_fred_series("DGS3MO", periods=5)
        if not df.empty:
            return df["value"].iloc[-1] / 100
    except Exception:
        pass
    return 0.045

_RFR = _get_rfr()


def _mid(row):
    b = row.get("bid", 0) or 0
    a = row.get("ask", 0) or 0
    lp = row.get("last_price", 0) or 0
    return (b + a) / 2 if (b > 0 and a > 0) else (lp if lp > 0 else 0)


def _bid(row):
    v = row.get("bid", 0) or 0
    return v if v > 0 else _mid(row)


def _ask(row):
    v = row.get("ask", 0) or 0
    return v if v > 0 else _mid(row)


def _is_live(row):
    return bool(row.get("quote_live", True))


def _atm_strike(chain, spot):
    strikes = chain["strike_price"].unique()
    return float(min(strikes, key=lambda s: abs(s - spot)))


def _get_contract(chain, strike, opt_type, exp=None):
    mask = (chain["strike_price"] == strike) & (chain["contract_type"] == opt_type)
    if exp is not None:
        mask = mask & (chain["expiration_date"] == exp)
    sub = chain[mask]
    return sub.iloc[0] if not sub.empty else None


def _fmt_exp(exp_str):
    try:
        return pd.to_datetime(exp_str).strftime("%b %d")
    except Exception:
        return str(exp_str)


# ═══════════════════════════════════════════════
# CORE: FIND BEST CALENDAR
# ═══════════════════════════════════════════════

def _find_best_calendar(chain: pd.DataFrame, spot: float,
                         front_dte_min: int, front_dte_max: int,
                         back_dte_min: int, back_dte_max: int,
                         stype: str = "call", min_gap: int = 14,
                         profit_target: int = 30) -> dict | None:
    if chain is None or chain.empty or spot is None or spot <= 0:
        return None

    chain = chain.copy()
    chain["dte"] = (pd.to_datetime(chain["expiration_date"]) - pd.Timestamp.now()).dt.days
    chain = chain[chain["dte"] >= front_dte_min]
    if chain.empty:
        return None

    # Find ATM strike
    strike = _atm_strike(chain, spot)

    # Get all expirations with this strike and type
    type_chain = chain[(chain["contract_type"] == stype) & (chain["strike_price"] == strike)]
    if type_chain.empty:
        # Try nearest strike
        type_chain_all = chain[chain["contract_type"] == stype]
        if type_chain_all.empty:
            return None
        strike = _atm_strike(type_chain_all, spot)
        type_chain = type_chain_all[type_chain_all["strike_price"] == strike]
        if type_chain.empty:
            return None

    # Build candidate pairs
    best = None
    best_score = -999

    exps = type_chain.sort_values("dte")
    for _, front_row in exps.iterrows():
        f_dte = front_row["dte"]
        if f_dte < front_dte_min or f_dte > front_dte_max:
            continue
        f_exp = front_row["expiration_date"]
        f_mid = _mid(front_row)
        f_iv = float(front_row.get("implied_volatility", 0) or 0) or 0.25

        for _, back_row in exps.iterrows():
            b_dte = back_row["dte"]
            b_exp = back_row["expiration_date"]
            if b_dte <= f_dte + min_gap:
                continue
            if b_dte < back_dte_min or b_dte > back_dte_max:
                continue

            b_mid = _mid(back_row)
            b_iv = float(back_row.get("implied_volatility", 0) or 0) or 0.25

            # Debit = back (buy) - front (sell)
            debit = b_mid - f_mid
            if debit <= 0.01:
                continue

            # Conservative pricing for synthetic quotes
            f_live = _is_live(front_row)
            b_live = _is_live(back_row)
            n_synthetic = sum(1 for x in [f_live, b_live] if not x)
            if n_synthetic > 0:
                debit_conservative = _ask(back_row) - _bid(front_row)
                w = min(n_synthetic / 2, 0.75)
                debit = debit * (1 - w) + debit_conservative * w

            # Greeks via BS
            T_f = max(f_dte / 365, 0.001)
            T_b = max(b_dte / 365, 0.001)
            g_f = bs_greeks(spot, strike, T_f, _RFR, f_iv, stype)
            g_b = bs_greeks(spot, strike, T_b, _RFR, b_iv, stype)
            net_greeks = {k: g_b[k] - g_f[k] for k in g_f}

            # P&L at front expiry — find breakevens and max profit
            spot_range = np.linspace(spot * 0.85, spot * 1.15, 100)
            T_back_rem = (b_dte - f_dte) / 365
            pnl_curve = []
            for s in spot_range:
                if stype == "call":
                    short_val = max(s - strike, 0)
                else:
                    short_val = max(strike - s, 0)
                long_val = black_scholes(s, strike, T_back_rem, _RFR, b_iv, stype)
                pnl_curve.append((long_val - short_val) - debit)
            pnl_arr = np.array(pnl_curve)
            max_profit = float(pnl_arr.max())
            if max_profit <= 0:
                continue

            # Breakevens
            be_low, be_high = spot * 0.85, spot * 1.15
            for i in range(len(pnl_arr) - 1):
                if pnl_arr[i] <= 0 < pnl_arr[i + 1]:
                    be_low = spot_range[i]
                if pnl_arr[i] >= 0 > pnl_arr[i + 1]:
                    be_high = spot_range[i]

            # POP approximation from breakeven width vs expected move
            expected_move = spot * f_iv * np.sqrt(f_dte / 365)
            be_width = be_high - be_low
            pop = float(_norm.cdf(be_width / (2 * max(expected_move, 0.01))) -
                        _norm.cdf(-be_width / (2 * max(expected_move, 0.01))))
            pop = max(0.05, min(0.95, pop))

            # Theta yield = daily theta / debit
            theta_per_day = abs(net_greeks.get("theta", 0))
            theta_debit = theta_per_day / max(debit, 0.01)

            # Vega/Theta ratio — core risk metric per the quant manual
            vega_theta_ratio = abs(net_greeks.get("vega", 0) / net_greeks["theta"]) if net_greeks.get("theta", 0) != 0 else 0

            # IV differential (raw)
            iv_diff = b_iv - f_iv

            # Forward implied volatility — what you're actually trading
            # Forward implied volatility (Kellerer check: reject calendar arbitrage)
            # σ_fwd = sqrt((σ_back² × T_back - σ_front² × T_front) / (T_back - T_front))
            # If forward variance is negative, total variance is non-monotonic = calendar arbitrage in data
            _fwd_var_num = (b_iv ** 2) * T_b - (f_iv ** 2) * T_f
            _fwd_var_den = T_b - T_f
            if _fwd_var_num <= 0 or _fwd_var_den <= 0:
                continue  # calendar arbitrage in data — reject this pair
            fwd_vol = np.sqrt(_fwd_var_num / _fwd_var_den)

            # Higher-order Greeks (Vanna, Volga) for second-order risk assessment
            hg_f = bs_higher_greeks(spot, strike, T_f, _RFR, f_iv, stype)
            hg_b = bs_higher_greeks(spot, strike, T_b, _RFR, b_iv, stype)
            net_vanna = hg_b.get("vanna", 0) - hg_f.get("vanna", 0)
            net_volga = hg_b.get("volga", 0) - hg_f.get("volga", 0)
            net_charm = hg_b.get("charm", 0) - hg_f.get("charm", 0)

            # Liquidity
            legs_liq = []
            for leg in [front_row, back_row]:
                oi = float(leg.get("open_interest", 0) or 0)
                vol = float(leg.get("volume", 0) or 0)
                b_px = float(leg.get("bid", 0) or 0)
                a_px = float(leg.get("ask", 0) or 0)
                ba = (a_px - b_px) if (a_px > 0 and b_px > 0) else 999
                legs_liq.append({"oi": oi, "vol": vol, "ba": round(ba, 2)})
            min_oi = min(l["oi"] for l in legs_liq)
            total_vol = sum(l["vol"] for l in legs_liq)
            max_ba = max(l["ba"] for l in legs_liq)

            if min_oi >= 500 and max_ba <= 0.10 and total_vol >= 100:
                liq_grade = "A"
            elif min_oi >= 100 and max_ba <= 0.20 and total_vol >= 20:
                liq_grade = "B"
            elif min_oi >= 50 and max_ba <= 0.50:
                liq_grade = "C"
            elif min_oi >= 10 and max_ba <= 1.50:
                liq_grade = "D"
            else:
                liq_grade = "F"

            # Score this pair (simple base for ranking)
            score = theta_debit * pop

            if score > best_score:
                best_score = score
                # Spread pricing
                spread_natural = _bid(front_row) - _ask(back_row)  # what you'd receive if selling (negative for debit)
                # For calendars, natural debit = ask(back) - bid(front)
                nat_debit = _ask(back_row) - _bid(front_row)
                mid_debit = b_mid - f_mid

                # Fill estimate
                _FILL_PCT = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10, "F": 0.05}
                fill_pct = _FILL_PCT.get(liq_grade, 0.15)
                fill_debit = nat_debit - (nat_debit - mid_debit) * fill_pct if nat_debit > mid_debit else mid_debit

                # Days to target via BS forward pricing
                days_to_target = f_dte
                target_value = debit + max_profit * (profit_target / 100)
                for day in range(1, f_dte, 3):
                    T_f_rem = max((f_dte - day) / 365, 0.001)
                    T_b_rem = max((b_dte - day) / 365, 0.001)
                    sv = (black_scholes(spot, strike, T_b_rem, _RFR, b_iv, stype)
                          - black_scholes(spot, strike, T_f_rem, _RFR, f_iv, stype))
                    if sv >= target_value:
                        days_to_target = max(1, day)
                        break

                slippage_est = sum(l["ba"] for l in legs_liq) / 2

                best = {
                    "strike": strike, "spread_type": stype,
                    "front_exp": f_exp, "back_exp": b_exp,
                    "front_dte": f_dte, "back_dte": b_dte,
                    "spot": spot,
                    "debit": round(debit, 2),
                    "debit_100": round(debit * 100, 0),
                    "max_loss": round(debit, 2),
                    "max_loss_100": round(debit * 100, 0),
                    "max_profit": round(max_profit, 2),
                    "max_profit_100": round(max_profit * 100, 0),
                    "front_iv": round(f_iv * 100, 1),
                    "back_iv": round(b_iv * 100, 1),
                    "iv_diff": round(iv_diff * 100, 1),
                    "fwd_vol": round(fwd_vol * 100, 1),
                    "vega_theta_ratio": round(vega_theta_ratio, 2),
                    "net_delta": round(net_greeks.get("delta", 0), 4),
                    "net_gamma": round(net_greeks.get("gamma", 0), 4),
                    "net_theta": round(net_greeks.get("theta", 0), 4),
                    "net_vega": round(net_greeks.get("vega", 0), 4),
                    "net_vanna": round(net_vanna, 4),
                    "net_volga": round(net_volga, 4),
                    "net_charm": round(net_charm, 4),
                    "theta_debit": round(theta_debit, 4),
                    "pop": round(pop * 100, 1),
                    "breakeven_low": round(be_low, 2),
                    "breakeven_high": round(be_high, 2),
                    "days_to_target": days_to_target,
                    "score": round(score, 4),
                    "nat_debit": round(nat_debit, 2),
                    "mid_debit": round(mid_debit, 2),
                    "fill_debit": round(fill_debit, 2),
                    "liq_grade": liq_grade,
                    "min_oi": int(min_oi),
                    "total_vol": int(total_vol),
                    "max_ba": round(max_ba, 2),
                    "slippage_est": round(slippage_est, 2),
                    "n_synthetic_legs": n_synthetic,
                    "_leg_params": (spot, strike, f_dte, b_dte, f_iv, b_iv, _RFR, stype),
                    "legs": [
                        {"label": f"{strike:.0f}{stype[0].upper()} {_fmt_exp(f_exp)} (short)", "bid": round(_bid(front_row), 2), "ask": round(_ask(front_row), 2), "mid": round(f_mid, 2), "oi": int(float(front_row.get("open_interest", 0) or 0)), "vol": int(float(front_row.get("volume", 0) or 0)), "live": f_live},
                        {"label": f"{strike:.0f}{stype[0].upper()} {_fmt_exp(b_exp)} (long)", "bid": round(_bid(back_row), 2), "ask": round(_ask(back_row), 2), "mid": round(b_mid, 2), "oi": int(float(back_row.get("open_interest", 0) or 0)), "vol": int(float(back_row.get("volume", 0) or 0)), "live": b_live},
                    ],
                }

    return best


def _compute_decay_path(r: dict) -> list:
    """Day-by-day spread value for theta decay chart."""
    params = r.get("_leg_params")
    if not params:
        return []
    spot, strike, f_dte, b_dte, f_iv, b_iv, rfr, stype = params
    path = []
    for day in range(0, f_dte):
        T_f = max((f_dte - day) / 365, 0.001)
        T_b = max((b_dte - day) / 365, 0.001)
        sv = (black_scholes(spot, strike, T_b, rfr, b_iv, stype)
              - black_scholes(spot, strike, T_f, rfr, f_iv, stype))
        path.append((day, sv))
    return path


def _scan_ticker(ticker, spot, chain, front_min, front_max, back_min, back_max,
                  stype, min_gap, profit_target):
    try:
        if chain is None or chain.empty:
            return None
        if not spot or spot <= 0:
            try:
                px = fetch_massive_data(ticker, 5)
                if px is not None and not px.empty:
                    spot = float(px["Close"].iloc[-1])
            except Exception:
                pass
        if not spot or spot <= 0:
            return None

        result = _find_best_calendar(chain, spot, front_min, front_max,
                                      back_min, back_max, stype, min_gap, profit_target)
        if result:
            result["ticker"] = ticker
            result["ivr"] = None
            result["vrp"] = None
            result["hv20"] = None
            result["ivr_band"] = "N/A"
        return result
    except Exception as e:
        logger.warning(f"Calendar scan failed for {ticker}: {e}")
        return None


def _fetch_price_history(ticker):
    try:
        return fetch_massive_data(ticker, 252)
    except Exception:
        return None


def _fetch_earnings_date(ticker):
    try:
        import yfinance as yf
        from datetime import datetime as dt
        info = yf.Ticker(ticker).info or {}
        ts = info.get("earningsTimestampStart")
        if ts and ts > 0:
            ed = dt.utcfromtimestamp(ts).date()
            days = (ed - dt.now().date()).days
            if 0 < days <= 120:
                return {"date": ed.isoformat(), "days": days}
    except Exception:
        pass
    return None


def _enrich_ivr_vrp(r, px):
    ticker = r["ticker"]
    current_iv = r["front_iv"] / 100
    hv20 = None
    vrp = None
    ivr = None

    hv_series = None
    if px is not None and len(px) > 30:
        hv_series = px["Close"].pct_change().rolling(20).std().dropna() * np.sqrt(252)
        if len(hv_series) > 0:
            hv20 = float(hv_series.iloc[-1] * 100)
        if hv20 is not None and current_iv > 0:
            vrp = round((current_iv * 100) - hv20, 1)

    if current_iv > 0:
        try:
            ivr = percentile_rank(ticker, "atm_iv", current_value=current_iv)
        except Exception:
            pass

    if ivr is None and current_iv > 0 and hv_series is not None and len(hv_series) >= 20:
        hv_vals = hv_series.values
        ivr = float(np.sum(hv_vals <= current_iv) / len(hv_vals) * 100)

    # Build snapshot for batch save (collected and written after loop)
    _snapshot = {"ticker": ticker, "atm_iv": current_iv}
    if hv20 is not None:
        _snapshot["hv20"] = hv20 / 100
    if vrp is not None:
        _snapshot["vrp"] = vrp / 100
    r["_metrics_snapshot"] = _snapshot

    r["ivr"] = round(ivr, 1) if ivr is not None else None
    r["vrp"] = round(vrp, 1) if vrp is not None else None
    r["hv20"] = round(hv20, 1) if hv20 is not None else None
    r["front_iv_hv_ratio"] = round(current_iv / (hv20 / 100), 2) if hv20 and hv20 > 0 else None
    if ivr is not None:
        if ivr < 30:
            r["ivr_band"] = "Low"
        elif ivr < 50:
            r["ivr_band"] = "Normal"
        elif ivr <= 75:
            r["ivr_band"] = "Optimal"
        else:
            r["ivr_band"] = "Extreme"

    # Term structure Z-score (OU mean reversion signal)
    # How far is current forward vol from its historical mean?
    # Uses metrics_store IV history (self-consistent dates, no alignment issues)
    fwd_vol_dec = r.get("fwd_vol", 0) / 100 if r.get("fwd_vol") else None
    ts_zscore = None
    if fwd_vol_dec and fwd_vol_dec > 0:
        try:
            iv_hist = get_metric_timeseries(ticker, "atm_iv", days=60)
            if len(iv_hist) >= 10:
                iv_vals = iv_hist.dropna().values
                if len(iv_vals) >= 10 and np.std(iv_vals) > 0:
                    # Z-score: how far is current forward vol from historical ATM IV mean
                    ts_zscore = round((fwd_vol_dec - np.mean(iv_vals)) / np.std(iv_vals), 2)
        except Exception:
            pass
    r["ts_zscore"] = ts_zscore


def _compute_calendar_backtest(px, spot, strike, front_dte, back_dte, stype,
                                profit_target_pct=30, stop_loss_pct=50):
    if px is None or len(px) < max(back_dte, 60) + 30:
        return None

    closes = px["Close"].values
    n = len(closes)
    if n < front_dte + 20:
        return None

    rv = pd.Series(closes).pct_change().rolling(20).std().dropna().values * np.sqrt(252)
    if len(rv) < front_dte + 10:
        return None

    wins = 0
    losses = 0
    target_exits = 0
    stop_exits = 0
    dte_exits = 0

    step = max(front_dte // 2, 5)
    for i in range(len(rv) - front_dte):
        if i % step != 0:
            continue
        entry_spot = closes[i + (len(closes) - len(rv))]
        if entry_spot <= 0:
            continue
        entry_iv = max(rv[i], 0.05)

        # Price at entry
        T_f = front_dte / 365
        T_b = back_dte / 365
        entry_debit = (black_scholes(entry_spot, entry_spot, T_b, _RFR, entry_iv * 1.05, stype)
                       - black_scholes(entry_spot, entry_spot, T_f, _RFR, entry_iv, stype))
        if entry_debit <= 0:
            continue

        max_profit_est = entry_debit * 0.6  # rough estimate
        target_val = entry_debit + max_profit_est * (profit_target_pct / 100)
        stop_val = entry_debit * (1 - stop_loss_pct / 100)

        outcome = None
        for d in range(1, front_dte - 1):
            idx = i + d
            if idx >= len(rv):
                break
            curr_spot = closes[idx + (len(closes) - len(rv))]
            curr_iv = max(rv[idx], 0.05)
            T_f_rem = max((front_dte - d) / 365, 0.001)
            T_b_rem = max((back_dte - d) / 365, 0.001)

            spread_val = (black_scholes(curr_spot, entry_spot, T_b_rem, _RFR, curr_iv * 1.05, stype)
                          - black_scholes(curr_spot, entry_spot, T_f_rem, _RFR, curr_iv, stype))

            if spread_val >= target_val:
                outcome = "target"
                break
            if spread_val <= stop_val:
                outcome = "stop"
                break

        if outcome == "target":
            wins += 1
            target_exits += 1
        elif outcome == "stop":
            losses += 1
            stop_exits += 1
        else:
            # Held to front expiry — spot near strike = win (calendar retains value)
            dte_exits += 1
            _offset = len(closes) - len(rv)
            _exit_idx = min(i + front_dte, len(rv) - 1)
            if _exit_idx < len(rv):
                _exit_spot = closes[_exit_idx + _offset]
                if entry_spot * 0.97 <= _exit_spot <= entry_spot * 1.03:
                    wins += 1
                else:
                    losses += 1
            else:
                losses += 1

    total = wins + losses
    if total < 5:
        return None

    return {
        "win_rate": round(wins / total * 100, 1),
        "n_trials": total,
        "wins": wins,
        "losses": losses,
        "target_exits": target_exits,
        "stop_exits": stop_exits,
        "dte_exits": dte_exits,
    }


# ═══════════════════════════════════════════════
# SCAN EXECUTION
# ═══════════════════════════════════════════════

if scan and tickers:
    st.session_state.pop("cal_results", None)
    st.session_state.pop("cal_ai_result", None)
    results = []
    with fun_loader("data"):
        progress = st.progress(0, text="Fetching spot prices...")

        snapshots = polygon_batch_snapshot(tickers)

        progress.progress(0.05, text="Pre-fetching price histories & earnings...")
        price_cache = {}
        earnings_cache = {}
        chain_cache = {}

        with ThreadPoolExecutor(max_workers=10) as ex:
            price_futures = {ex.submit(_fetch_price_history, tk): tk for tk in tickers}
            earnings_futures = {ex.submit(_fetch_earnings_date, tk): tk for tk in tickers}
            for fut in as_completed(list(price_futures.keys()) + list(earnings_futures.keys())):
                if fut in price_futures:
                    tk = price_futures[fut]
                    try:
                        price_cache[tk] = fut.result()
                    except Exception:
                        price_cache[tk] = None
                elif fut in earnings_futures:
                    tk = earnings_futures[fut]
                    try:
                        earnings_cache[tk] = fut.result()
                    except Exception:
                        earnings_cache[tk] = None

        # Fetch chains and scan sequentially
        for idx, tk in enumerate(tickers):
            progress.progress(0.15 + 0.5 * (idx + 1) / len(tickers),
                               text=f"Scanning {tk}... ({idx + 1}/{len(tickers)})")
            try:
                spot = snapshots.get(tk, {}).get("price")
                spot = round(spot, 2) if spot else 0
                chain = fetch_options_chain(tk)
                result = _scan_ticker(tk, spot, chain, front_dte_min, front_dte_max,
                                       back_dte_min, back_dte_max, spread_type,
                                       min_dte_spread, profit_target_pct)
                if result:
                    results.append(result)
            except Exception as e:
                logger.warning(f"Scan failed for {tk}: {e}")

        # Enrich
        if results:
            progress.progress(0.70, text="Computing IVR, VRP & earnings risk...")
            for r in results:
                try:
                    _enrich_ivr_vrp(r, price_cache.get(r["ticker"]))
                except Exception:
                    pass
            # Batch save all metrics snapshots (1 Supabase call instead of N)
            _snapshots = [r.pop("_metrics_snapshot") for r in results if "_metrics_snapshot" in r]
            if _snapshots:
                try:
                    save_batch(_snapshots)
                except Exception:
                    pass

            for r in results:
                # Earnings enrichment
                tk = r["ticker"]
                earn = earnings_cache.get(tk)
                if earn:
                    r["earnings_date"] = earn["date"]
                    r["earnings_days"] = earn["days"]
                    r["earnings_between"] = r["front_dte"] < earn["days"] < r["back_dte"]
                    r["earnings_before_front"] = earn["days"] <= r["front_dte"]
                else:
                    r["earnings_date"] = None
                    r["earnings_days"] = None
                    r["earnings_between"] = False
                    r["earnings_before_front"] = False

                # Corporate action check (OCC: splits/dividends corrupt backtest)
                r["has_corp_action"] = False
                _px = price_cache.get(tk)
                if _px is not None and len(_px) > 20:
                    try:
                        _returns = _px["Close"].pct_change().dropna()
                        # Flag if any single-day return > 15% (likely split/special div)
                        if (_returns.abs() > 0.15).any():
                            r["has_corp_action"] = True
                    except Exception:
                        pass

                # Backtest
                _px = price_cache.get(tk)
                r["backtest"] = _compute_calendar_backtest(
                    _px, r["spot"], r["strike"], r["front_dte"], r["back_dte"],
                    r["spread_type"], profit_target_pct, stop_loss_pct)

                # Position sizing (Kelly)
                bt = r.get("backtest")
                if bt and bt["win_rate"] > 0:
                    managed_wr = min(0.95, bt["win_rate"] / 100)
                else:
                    managed_wr = min(0.95, r["pop"] / 100)
                q = 1 - managed_wr
                win_amt = r["max_profit_100"] * (profit_target_pct / 100)
                loss_amt = r["debit_100"] * (stop_loss_pct / 100)
                if loss_amt > 0 and win_amt > 0:
                    b = win_amt / loss_amt
                    full_kelly = (managed_wr * b - q) / b if b > 0 else 0
                    adj_kelly = max(0, full_kelly) * kelly_fraction
                    capped_pct = min(adj_kelly, max_risk_pct / 100) if adj_kelly > 0 else max_risk_pct / 100
                    contracts = int(account_size * capped_pct / max(r["debit_100"], 1))
                else:
                    full_kelly = 0
                    adj_kelly = 0
                    capped_pct = 0
                    contracts = 0

                r["managed_wr"] = round(managed_wr * 100, 1)
                r["kelly_full"] = round(full_kelly * 100, 1)
                r["kelly_adj"] = round(adj_kelly * 100, 1)
                r["size_contracts"] = contracts
                r["size_total_risk"] = round(r["debit_100"] * contracts, 0)

            progress.progress(0.95, text="Done.")
        progress.empty()

    if results:
        st.session_state["cal_results"] = results
        st.session_state["cal_scan_time"] = pd.Timestamp.now()
    else:
        st.warning("No valid calendar spread setups found. Try adjusting DTE ranges.")


# ═══════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════

if "cal_results" in st.session_state and st.session_state["cal_results"]:
    raw_results = st.session_state["cal_results"]

    _scan_ts = st.session_state.get("cal_scan_time")
    if _scan_ts:
        _age_min = (pd.Timestamp.now() - _scan_ts).total_seconds() / 60
        _hour = _scan_ts.hour
        _weekday = _scan_ts.weekday()
        _is_market = 0 <= _weekday <= 4 and 9 <= _hour < 16
        if not _is_market:
            st.warning("Scanned outside market hours — quotes may be stale.")
        elif _age_min > 30:
            st.info(f"Results are {_age_min:.0f}min ago. Consider re-scanning.")

    freshness_bar(
        ("Chains", _scan_ts, 30, 120),
        ("Prices", _scan_ts, 10, 30),
        ("IVR", _scan_ts, 60, 240),
    )

    # ── Composite scoring ──
    IVR_BAND_WEIGHTS = {"Optimal": 1.5, "Normal": 1.0, "Extreme": 0.7, "Low": 0.6, "N/A": 0.8}
    _LIQ_MULT = {"A": 1.2, "B": 1.0, "C": 0.85, "D": 0.6, "F": 0.3}

    scored = []
    for r in raw_results:
        base = r["score"]  # theta_debit × pop

        ivr_w = IVR_BAND_WEIGHTS.get(r.get("ivr_band", "N/A"), 0.8)

        # Front IV / HV ratio
        ratio = r.get("front_iv_hv_ratio")
        vrp_w = max(0.5, 1.0 + ((ratio or 1.0) - 1.0) * 0.5)

        liq_w = _LIQ_MULT.get(r.get("liq_grade", "F"), 0.3)

        # Earnings penalty
        if r.get("earnings_between"):
            earn_w = 0.3
        elif r.get("earnings_before_front"):
            earn_w = 0.7
        else:
            earn_w = 1.0

        bt = r.get("backtest")
        hwr_w = (0.3 + bt["win_rate"] / 100) if bt and bt["win_rate"] > 0 else 0.9

        # Forward vol richness — are you buying cheap or expensive forward variance?
        # fwd_vol < HV20 = buying cheap forward vol (favorable)
        # fwd_vol > HV20 = buying expensive forward vol (unfavorable)
        _fwd = r.get("fwd_vol", 0) / 100 if r.get("fwd_vol") else 0
        _hv = (r.get("hv20", 0) / 100) if r.get("hv20") else 0
        if _fwd > 0 and _hv > 0:
            # Ratio < 1 means forward vol is cheap vs realized (good for calendars)
            _fwd_ratio = _fwd / _hv
            ts_w = max(0.6, min(1.6, 2.0 - _fwd_ratio))  # cheap fwd → high weight
        else:
            # Fallback to raw IV diff
            iv_diff = r.get("iv_diff", 0)
            ts_w = max(0.7, min(1.5, 1.0 + iv_diff / 100 * 0.1))

        adj = round(base * ivr_w * vrp_w * liq_w * earn_w * hwr_w * ts_w, 4)
        scored.append({**r, "adj_score": adj})

    # Sort & filter
    with st.container(border=True):
        sf1, sf2, sf3, sf4 = st.columns(4)
        with sf1:
            sort_by = st.selectbox("Sort by", ["Score", "POP", "Theta/Debit", "IVR", "Fwd Vol"], index=0)
        with sf2:
            show_n = st.selectbox("Show", ["All", "Top 10", "Top 5"], index=1)
        with sf3:
            min_pop = st.number_input("Min POP %", value=30, min_value=0, max_value=95, step=5)
        with sf4:
            min_liq = st.selectbox("Min liquidity", ["Any", "D+", "C+", "B+", "A only"], index=2)

    filtered = scored
    if min_pop > 0:
        filtered = [r for r in filtered if r["pop"] >= min_pop]
    _LIQ_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    if min_liq == "D+":
        filtered = [r for r in filtered if _LIQ_ORDER.get(r.get("liq_grade", "F"), 0) >= 1]
    elif min_liq == "C+":
        filtered = [r for r in filtered if _LIQ_ORDER.get(r.get("liq_grade", "F"), 0) >= 2]
    elif min_liq == "B+":
        filtered = [r for r in filtered if _LIQ_ORDER.get(r.get("liq_grade", "F"), 0) >= 3]
    elif min_liq == "A only":
        filtered = [r for r in filtered if r.get("liq_grade") == "A"]

    _sort_keys = {
        "Score": lambda r: r["adj_score"],
        "POP": lambda r: r["pop"],
        "Theta/Debit": lambda r: r.get("theta_debit", 0),
        "IVR": lambda r: r.get("ivr") or 0,
        "Fwd Vol": lambda r: r.get("fwd_vol", 0),
    }
    results = sorted(filtered, key=_sort_keys.get(sort_by, _sort_keys["Score"]), reverse=True)

    if show_n == "Top 5":
        results = results[:5]
    elif show_n == "Top 10":
        results = results[:10]

    st.markdown(f"##### {len(results)} setups")

    if not results:
        st.info("All setups were filtered out. Try relaxing the filters.")

    # Portfolio summary
    if results:
        _total_contracts = sum(r.get("size_contracts", 0) for r in results)
        _total_risk = sum(r.get("size_total_risk", 0) for r in results)
        _earn_count = sum(1 for r in results if r.get("earnings_between"))
        with st.container(border=True):
            p1, p2, p3, p4 = st.columns(4)
            p1.metric("Total Contracts", f"{_total_contracts}")
            p2.metric("Total Debit", f"${_total_risk:,.0f}")
            p3.metric("Earnings Risk", f"{_earn_count} tickers")
            p4.metric("Avg IV Diff", f"{np.mean([r.get('iv_diff', 0) for r in results]):.1f}%")

    # Detail cards
    top_results = results[:12]
    if top_results:
        st.markdown("##### Setup Details")
        detail_tabs = st.tabs([
            f"{r['ticker']} · {_fmt_exp(r['front_exp'])}→{_fmt_exp(r['back_exp'])} ({r.get('liq_grade', '?')})"
            for r in top_results
        ])

        for tab, r in zip(detail_tabs, top_results):
            with tab:
                with error_boundary(f"Detail {r['ticker']}"):
                    # Status banner
                    _ivr = r.get("ivr")
                    _vrp = r.get("vrp")
                    _band = r.get("ivr_band", "N/A")
                    _band_colors = {"Optimal": COLORS["success"], "Normal": COLORS["accent"],
                                    "Low": COLORS["danger"], "Extreme": COLORS["warning"], "N/A": COLORS["text_muted"]}
                    _bc = _band_colors.get(_band, COLORS["text_muted"])
                    _lg = r.get("liq_grade", "?")
                    _liq_colors = {"A": COLORS["success"], "B": "#66bb6a", "C": COLORS["warning"],
                                   "D": "#ff8a65", "F": COLORS["danger"]}
                    _lc = _liq_colors.get(_lg, COLORS["text_muted"])

                    _flags = []
                    if r.get("earnings_between"):
                        _flags.append(f'<span style="color:{COLORS["danger"]};">EARNINGS BETWEEN EXPS {r["earnings_days"]}d</span>')
                    if _band == "Low":
                        _flags.append(f'<span style="color:{COLORS["danger"]};">IVR &lt;30</span>')
                    if r.get("iv_diff", 0) < 0:
                        _flags.append(f'<span style="color:{COLORS["warning"]};">Backwardation</span>')
                    if r.get("n_synthetic_legs", 0) > 0:
                        _flags.append(f'<span style="color:{COLORS["warning"]};">{r["n_synthetic_legs"]} leg no live quote</span>')
                    if r.get("has_corp_action"):
                        _flags.append(f'<span style="color:{COLORS["warning"]};">Corp action in history (backtest may be unreliable)</span>')
                    _zs = r.get("ts_zscore")
                    if _zs is not None and _zs < -1.5:
                        _flags.append(f'<span style="color:{COLORS["success"]};">Fwd vol cheap (Z={_zs:+.1f})</span>')
                    elif _zs is not None and _zs > 1.5:
                        _flags.append(f'<span style="color:{COLORS["danger"]};">Fwd vol rich (Z={_zs:+.1f})</span>')
                    _flags_html = ' · '.join(_flags) if _flags else f'<span style="color:{COLORS["success"]};">No warnings</span>'

                    _ivr_str = f"{_ivr:.0f}" if _ivr is not None else "N/A"
                    st.markdown(
                        f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;'
                        f'padding:6px 14px;border:1px solid {_bc};border-radius:6px;margin-bottom:8px;'
                        f'font-size:0.78rem;font-family:monospace;gap:8px;">'
                        f'<span>IVR <b style="color:{_bc};">{_ivr_str}</b> · '
                        f'Front IV {r["front_iv"]}% · Back IV {r["back_iv"]}% · '
                        f'Fwd Vol <b>{r.get("fwd_vol", 0):.1f}%</b> · HV20 {r.get("hv20", "N/A")}%</span>'
                        f'<span>Liq <b style="color:{_lc};">{_html.escape(_lg)}</b> OI {r.get("min_oi", 0):,}</span>'
                        f'<span>{_flags_html}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    col_left, col_right = st.columns([2, 3])

                    with col_left:
                        h1, h2 = st.columns(2)
                        h1.metric(r["ticker"], f"${r['spot']:,.2f}")
                        h2.metric("Debit", f"${r['fill_debit'] * 100:,.0f}")

                        # Price transparency
                        st.markdown(
                            f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};font-family:monospace;margin:-8px 0 6px 0;">'
                            f'Natural ${r["nat_debit"] * 100:,.0f} · <b>Fill ${r["fill_debit"] * 100:,.0f}</b> · Mid ${r["mid_debit"] * 100:,.0f}</div>',
                            unsafe_allow_html=True,
                        )

                        h3, h4 = st.columns(2)
                        h3.metric("Max Profit", f"${r['max_profit_100']:,.0f}")
                        h4.metric("POP", f"{r['pop']}%")

                        h5, h6 = st.columns(2)
                        h5.metric("Front Exp", _fmt_exp(r["front_exp"]))
                        h5.caption(f"{r['front_dte']}d")
                        h6.metric("Back Exp", _fmt_exp(r["back_exp"]))
                        h6.caption(f"{r['back_dte']}d")

                        h7, h8 = st.columns(2)
                        h7.metric("Breakevens", f"${r['breakeven_low']:.0f} / ${r['breakeven_high']:.0f}")
                        _contracts = r.get("size_contracts", 0)
                        h8.metric("Contracts", f"{_contracts}")
                        if _contracts > 0:
                            h8.caption(f"Kelly: {r.get('kelly_adj', 0):.1f}%")

                        # Leg diagram
                        st.markdown(
                            f'<div style="display:flex;align-items:center;justify-content:center;gap:6px;'
                            f'font-size:0.72rem;padding:4px 0;font-family:monospace;flex-wrap:wrap;">'
                            f'<span style="color:{COLORS["warning"]};border:1px solid {COLORS["warning"]};padding:1px 6px;border-radius:3px;">'
                            f'{r["strike"]:.0f}{r["spread_type"][0].upper()} {_fmt_exp(r["front_exp"])}</span>'
                            f'<span style="color:#888;">sell →</span>'
                            f'<span style="color:#666;">·{r["spot"]:.0f}·</span>'
                            f'<span style="color:#888;">← buy</span>'
                            f'<span style="color:{COLORS["accent"]};border:1px solid {COLORS["accent"]};padding:1px 6px;border-radius:3px;">'
                            f'{r["strike"]:.0f}{r["spread_type"][0].upper()} {_fmt_exp(r["back_exp"])}</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    with col_right:
                        # P&L at front expiry
                        params = r.get("_leg_params")
                        if params:
                            spot_v, strike_v, f_dte_v, b_dte_v, f_iv_v, b_iv_v, rfr_v, stype_v = params
                            prices = np.linspace(spot_v * 0.85, spot_v * 1.15, 100)
                            T_back_rem = (b_dte_v - f_dte_v) / 365
                            pnl = []
                            for s in prices:
                                if stype_v == "call":
                                    sv = max(s - strike_v, 0)
                                else:
                                    sv = max(strike_v - s, 0)
                                lv = black_scholes(s, strike_v, T_back_rem, rfr_v, b_iv_v, stype_v)
                                pnl.append((lv - sv - r["debit"]) * 100)
                            pnl = np.array(pnl)

                            fig = go.Figure()
                            fig.add_trace(go.Scatter(x=prices, y=pnl, fill="tozeroy",
                                fillcolor="rgba(0,255,150,0.08)", line=dict(color=COLORS["success"], width=2),
                                hovertemplate="Price: $%{x:.1f}<br>P&L: $%{y:,.0f}<extra></extra>"))
                            fig.add_trace(go.Scatter(x=prices, y=np.where(pnl < 0, pnl, 0), fill="tozeroy",
                                fillcolor="rgba(255,68,68,0.12)", line=dict(color=COLORS["danger"], width=0),
                                hoverinfo="skip", showlegend=False))
                            fig.add_hline(y=0, line_dash="dot", line_color="#555", line_width=1)
                            fig.add_vline(x=spot_v, line_dash="dash", line_color=COLORS["accent"], line_width=1,
                                annotation_text="Spot", annotation_position="top")
                            fig.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                                plot_bgcolor="rgba(0,0,0,0)", height=220, margin=dict(l=40, r=10, t=20, b=35),
                                xaxis_title="Price at Front Exp", yaxis_title="P&L ($)", showlegend=False)
                            st.plotly_chart(fig, use_container_width=True)

                        # Theta decay
                        decay = _compute_decay_path(r)
                        if decay:
                            days_d = [d[0] for d in decay]
                            vals_d = [d[1] * 100 for d in decay]
                            fig2 = go.Figure()
                            fig2.add_trace(go.Scatter(x=days_d, y=vals_d,
                                line=dict(color=COLORS["accent"], width=2), fill="tozeroy",
                                fillcolor="rgba(0,200,255,0.06)",
                                hovertemplate="Day %{x}<br>Value: $%{y:,.0f}<extra></extra>"))
                            fig2.add_hline(y=r["debit_100"], line_dash="dot", line_color=COLORS["warning"], line_width=1,
                                annotation_text="Entry", annotation_font_size=8)
                            fig2.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                                plot_bgcolor="rgba(0,0,0,0)", height=180, margin=dict(l=40, r=10, t=20, b=35),
                                xaxis_title="Days Held", yaxis_title="Spread Value ($)", showlegend=False)
                            st.plotly_chart(fig2, use_container_width=True)

                    # Book It
                    @st.fragment
                    def _book_it(setup):
                        _tk = setup["ticker"]
                        _booked_key = f"cal_booked_{_tk}_{setup['front_exp']}_{setup['back_exp']}"
                        if st.session_state.get(_booked_key):
                            st.success(f"{_tk} calendar spread booked.")
                        else:
                            _b1, _b2 = st.columns([1, 3])
                            with _b1:
                                if st.button("Book It", key=f"book_cal_{_tk}_{setup['front_exp']}_{setup['back_exp']}",
                                             type="primary", use_container_width=True):
                                    from src.position_book import add_position
                                    _contracts = setup.get("size_contracts", 1) or 1
                                    _pos_id = add_position(
                                        ticker=_tk,
                                        type="calendar_spread",
                                        qty=_contracts,
                                        entry_price=setup["fill_debit"],
                                        details={
                                            "strategy": "calendar_spread",
                                            "strike": setup["strike"],
                                            "spread_type": setup["spread_type"],
                                            "front_exp": setup["front_exp"],
                                            "back_exp": setup["back_exp"],
                                            "front_dte": setup["front_dte"],
                                            "back_dte": setup["back_dte"],
                                            "debit_per_contract": setup["debit_100"],
                                            "max_profit_per_contract": setup["max_profit_100"],
                                            "front_iv": setup["front_iv"],
                                            "back_iv": setup["back_iv"],
                                            "iv_diff": setup["iv_diff"],
                                            "pop": setup["pop"],
                                            "adj_score": setup.get("adj_score"),
                                            "liq_grade": setup.get("liq_grade"),
                                        },
                                        source_page="51_Calendar_Spread_Scanner",
                                    )
                                    st.session_state[_booked_key] = _pos_id
                                    st.success(f"Booked {_contracts} × {_tk} calendar. ID: {_pos_id}")
                            with _b2:
                                st.caption(f"{setup.get('size_contracts', 1) or 1} contracts · "
                                           f"${setup['fill_debit'] * 100:,.0f}/contract · "
                                           f"{_fmt_exp(setup['front_exp'])} → {_fmt_exp(setup['back_exp'])}")
                    _book_it(r)

                    # Sub-tabs
                    sub_tabs = st.tabs(["Management", "Greeks", "Backtest"])

                    with sub_tabs[0]:
                        m1, m2, m3 = st.columns(3)
                        m1.markdown(f"**Take Profit**\n\n{profit_target_pct}% of max · ~day {r['days_to_target']}")
                        m2.markdown(f"**Stop Loss**\n\n{stop_loss_pct}% of debit · ${r['debit_100'] * stop_loss_pct / 100:,.0f}")
                        m3.markdown(f"**Time Stop**\n\nRoll front at 7 DTE")

                    with sub_tabs[1]:
                        # First-order Greeks
                        g1, g2, g3, g4, g5 = st.columns(5)
                        g1.metric("Δ Delta", f"{r['net_delta'] * 100:+.1f}")
                        g2.metric("Γ Gamma", f"{r['net_gamma'] * 100:+.2f}")
                        g3.metric("Θ Theta", f"${r['net_theta'] * 100:+.1f}/day")
                        g4.metric("ν Vega", f"${r['net_vega'] * 100:+.1f}/1%")
                        g5.metric("ν/Θ Ratio", f"{r.get('vega_theta_ratio', 0):.2f}")

                        # Second-order Greeks (Vanna, Volga, Charm)
                        h1, h2, h3, h4 = st.columns(4)
                        h1.metric("Vanna", f"{r.get('net_vanna', 0) * 100:+.2f}")
                        h1.caption("dΔ/dσ")
                        h2.metric("Volga", f"{r.get('net_volga', 0) * 100:+.2f}")
                        h2.caption("dν/dσ")
                        h3.metric("Charm", f"{r.get('net_charm', 0) * 100:+.3f}")
                        h3.caption("dΔ/dt per day")
                        _zs = r.get("ts_zscore")
                        _zs_color = COLORS["success"] if _zs and _zs < -1 else (COLORS["danger"] if _zs and _zs > 1 else COLORS["text_muted"])
                        h4.metric("TS Z-Score", f"{_zs:+.2f}" if _zs is not None else "N/A")
                        h4.caption("Fwd vol vs history")

                        st.caption(
                            f"**Fwd Vol:** {r.get('fwd_vol', 0):.1f}% "
                            f"({'cheap' if _zs and _zs < -0.5 else 'rich' if _zs and _zs > 0.5 else 'fair'} vs history). "
                            f"**Vanna:** how delta shifts when vol changes. "
                            f"**Volga:** vega convexity (P&L acceleration from vol moves). "
                            f"**Charm:** overnight delta drift."
                        )

                        _legs = r.get("legs")
                        if _legs:
                            _leg_rows = []
                            for leg in _legs:
                                _ba = leg["ask"] - leg["bid"] if leg["ask"] > 0 and leg["bid"] > 0 else None
                                _leg_rows.append({
                                    "Leg": leg["label"],
                                    "Bid": f"${leg['bid']:.2f}",
                                    "Ask": f"${leg['ask']:.2f}",
                                    "Mid": f"${leg['mid']:.2f}",
                                    "B/A": f"${_ba:.2f}" if _ba is not None else "—",
                                    "OI": f"{leg['oi']:,}",
                                    "Quote": "Live" if leg.get("live", True) else "Synthetic",
                                })
                            st.dataframe(pd.DataFrame(_leg_rows), use_container_width=True, hide_index=True)

                    with sub_tabs[2]:
                        bt = r.get("backtest")
                        if bt:
                            w1, w2, w3, w4 = st.columns(4)
                            w1.metric("Win Rate", f"{bt['win_rate']}%")
                            w2.metric("Trials", f"{bt['n_trials']}")
                            w3.metric("Target Exits", f"{bt['target_exits']}")
                            w4.metric("Stop Exits", f"{bt['stop_exits']}")
                        else:
                            st.caption("Insufficient price history for backtest.")

    # Full results table
    if results:
        with st.expander(f"Full Results Table ({len(results)} setups)", expanded=False):
            table_rows = []
            for r in results:
                bt = r.get("backtest")
                table_rows.append({
                    "Ticker": r["ticker"],
                    "Liq": r.get("liq_grade", "?"),
                    "Strike": f"${r['strike']:,.0f}",
                    "Front": f"{_fmt_exp(r['front_exp'])} ({r['front_dte']}d)",
                    "Back": f"{_fmt_exp(r['back_exp'])} ({r['back_dte']}d)",
                    "Debit": f"${r['fill_debit'] * 100:,.0f}",
                    "Max Profit": f"${r['max_profit_100']:,.0f}",
                    "POP": f"{r['pop']}%",
                    "Fwd Vol": f"{r.get('fwd_vol', 0):.1f}%",
                    "IVR": f"{r['ivr']:.0f}" if r.get("ivr") is not None else "—",
                    "WR": f"{bt['win_rate']}%" if bt else "—",
                    "Score": f"{r['adj_score']:.3f}",
                })
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)

elif not scan:
    st.info("Enter tickers and click **Scan for Calendar Spreads** to find the best setups.")
