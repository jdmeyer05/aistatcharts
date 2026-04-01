"""Iron Condor Scanner — finds the best short iron condor setups across a universe of tickers."""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import html as _html

from src.layout import setup_page, error_boundary, fun_loader, freshness_bar
from src.styles import COLORS
from src.data_engine import fetch_options_chain, polygon_batch_snapshot, fetch_massive_data
from src.options_models import black_scholes
from src.metrics_store import save_snapshot, percentile_rank
from src.api_keys import get_secret
from src.economic_calendar import FOMC_DATES, FOMC_SEP_DATES

logger = logging.getLogger(__name__)

setup_page("50_Iron_Condor_Scanner")

st.title("Iron Condor Scanner")
st.markdown("Scan a universe of tickers for the highest-quality short iron condor setups ranked by credit, probability of profit, and IV percentile.")
with st.expander("How this scanner works"):
    st.markdown("""
**What it does:** Scans 57 tickers across indices, sectors, commodities, and individual stocks to find the best short iron condor setups right now.

**How it ranks setups** — the composite score integrates 7 quantitative signals:
- **Credit / Risk × POP** — the base economic edge of the trade (net of estimated slippage)
- **IVR Band** — where current IV sits vs historical realized vol. The 50-75 IVR zone is optimal per institutional research (78% win rate on SPX). IVR >75 signals real catalysts (jump risk), IVR <30 means insufficient premium.
- **VRP (IV − HV20)** — the volatility risk premium you're capturing. Positive = implied vol overprices risk. Negative = you're selling cheap.
- **Liquidity** — graded A-F from open interest, bid-ask width, and volume. Illiquid setups get crushed in the score because slippage eats your edge.
- **Earnings Risk** — setups with earnings before expiration are heavily penalized (jump-diffusion risk). Strikes inside the expected move get penalized even more.
- **Historical Win Rate** — simulated managed trades over 252 days of price history. Uses your profit target and stop loss settings.
- **Theta Efficiency** — credit per day per dollar at risk. Rewards faster capital turnover.

**Management framework** (per the quantitative manual):
- **Profit target:** Close at 50% of credit received (default). Captures most of the edge before gamma risk accelerates.
- **Stop loss:** Close at 1.5× credit (configurable). Limits tail losses to a defined multiple.
- **Time stop:** Close or roll at 21 DTE regardless of P&L. Gamma and speed risk become unmanageable in the final 3 weeks.
- **30Δ adjustment:** When a short leg's delta reaches 0.30, roll the untested (profitable) side toward the money to collect more credit and widen breakevens.

**Data sources:** Polygon (options chains, prices), yfinance (earnings dates, price fallback), FOMC calendar (economic events).
""")



# ═══════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════

DEFAULT_UNIVERSE = [
    # Broad indices (penny-wide spreads, massive OI)
    "SPY", "QQQ", "IWM", "DIA",
    # Mega cap tech (deep chains, active market makers)
    "AAPL", "TSLA", "NVDA", "AMD", "AMZN", "META", "MSFT", "GOOGL", "NFLX",
    # Liquid ETFs (tight spreads across strikes)
    "GLD", "SMH", "XLF", "TLT", "EEM",
    # Liquid single names
    "JPM", "BA",
]

with st.form("ic_scan_form", border=True):
    cc1, cc2, cc3, cc4, cc5 = st.columns(5)
    with cc1:
        target_dte_min = st.number_input("Min DTE", value=7, min_value=1, max_value=90, step=5)
    with cc2:
        target_dte_max = st.number_input("Max DTE", value=90, min_value=14, max_value=180, step=5)
    with cc3:
        short_delta = st.number_input("Short strike delta", value=0.25, min_value=0.05, max_value=0.45, step=0.02, format="%.2f",
                                       help="Delta for short legs. 0.16 ≈ 1σ, 0.25 ≈ standard, 0.30 ≈ aggressive. Higher = more credit, lower POP.")
    with cc4:
        wing_width = st.number_input("Wing width ($)", value=10, min_value=1, max_value=100, step=1,
                                      help="Distance between short and long strikes. Wider = more credit but more risk. ~1/10th of underlying is optimal.")
    with cc5:
        profit_target_pct = st.number_input("Profit target (%)", value=50, min_value=10, max_value=90, step=5,
                                             help="Close when this % of max credit is captured. 50% is the standard playbook.")

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        ivr_filter = st.select_slider("IVR Filter", options=["All", "≥30", "50-75 (Optimal)", "≥50", "≥75"],
                                       value="All",
                                       help="IVR 50-75 is the quant-optimal zone. IVR >75 often signals real catalysts (jump risk).")
    with fc2:
        min_vrp = st.number_input("Min VRP (IV − HV20)", value=0.0, min_value=-20.0, max_value=50.0, step=1.0, format="%.1f",
                                   help="Volatility Risk Premium: the edge you're capturing. Positive = implied > realized.")
    with fc3:
        exclude_earnings = st.checkbox("Exclude earnings", value=False,
                                        help="Remove tickers with earnings before expiration. Earnings = jump-diffusion risk.")

    ticker_input = st.text_area("Tickers (comma-separated)", value=", ".join(DEFAULT_UNIVERSE), height=68)
    tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

    with st.expander("Position Sizing (Kelly Criterion)"):
        ps1, ps2, ps3 = st.columns(3)
        with ps1:
            account_size = st.number_input("Account size ($)", value=25000, min_value=1000, max_value=10_000_000,
                                            step=5000, format="%d")
        with ps2:
            max_risk_pct = st.number_input("Hard cap per trade (%)", value=5.0, min_value=0.5, max_value=20.0,
                                            step=0.5, format="%.1f",
                                            help="Maximum % of account risked per trade. Overrides Kelly if Kelly is higher.")
        with ps3:
            kelly_fraction = st.number_input("Kelly fraction", value=0.5, min_value=0.1, max_value=1.0,
                                              step=0.1, format="%.1f",
                                              help="0.5 = Half-Kelly (institutional standard). Full Kelly maximizes growth but with severe drawdowns.")
        ps4, ps5 = st.columns(2)
        with ps4:
            stop_multiplier = st.number_input("Stop loss (× credit)", value=1.5, min_value=0.5, max_value=3.0,
                                               step=0.25, format="%.2f",
                                               help="Close position when loss reaches this multiple of credit received. "
                                                    "2× = conservative (quant manual default). 1× = tighter. 1.5× = common.")
        with ps5:
            win_rate_bump = st.number_input("Managed win rate bump (pp)", value=12, min_value=0, max_value=25, step=2,
                                             help="Percentage points added to POP to estimate managed win rate. "
                                                  "Closing at 50% profit empirically adds ~10-15pp vs at-expiration POP.")
        st.caption("Kelly: f* = (p×b − q) / b. Win = profit at target. Loss = stop × credit. "
                   "Managed win rate = POP + bump (early profit-taking increases win rate). "
                   "Half-Kelly is the institutional standard.")

    scan = st.form_submit_button("Scan for Iron Condors", type="primary", use_container_width=True)


# ═══════════════════════════════════════════════
# IRON CONDOR FINDER
# ═══════════════════════════════════════════════

def _find_best_condor(chain: pd.DataFrame, spot: float, dte_min: int, dte_max: int,
                       target_delta: float, width: float, profit_target: int = 50) -> dict | None:
    """Find the optimal short iron condor from an options chain.

    Returns dict with legs, credit, max_risk, pop, score, or None if no valid setup.
    """
    if chain is None or chain.empty or spot is None or spot <= 0:
        return None

    # Filter to target DTE range
    chain = chain.copy()
    chain["dte"] = (pd.to_datetime(chain["expiration_date"]) - pd.Timestamp.now()).dt.days
    chain = chain[(chain["dte"] >= dte_min) & (chain["dte"] <= dte_max)]
    if chain.empty:
        return None

    # Pick the expiration closest to the midpoint of our DTE range
    target_dte = (dte_min + dte_max) // 2
    chain["dte_dist"] = abs(chain["dte"] - target_dte)
    best_exp = chain.loc[chain["dte_dist"].idxmin(), "expiration_date"]
    exp_chain = chain[chain["expiration_date"] == best_exp].copy()
    actual_dte = exp_chain["dte"].iloc[0]

    calls = exp_chain[exp_chain["contract_type"] == "call"].sort_values("strike_price")
    puts = exp_chain[exp_chain["contract_type"] == "put"].sort_values("strike_price")

    if calls.empty or puts.empty:
        return None

    # Find short put: highest strike with delta >= -target_delta (OTM put)
    puts_otm = puts[puts["strike_price"] < spot].copy()
    puts_otm["abs_delta"] = puts_otm["delta"].abs()
    # Target: delta closest to target_delta from below
    short_put_candidates = puts_otm[puts_otm["abs_delta"] <= target_delta + 0.05]
    if short_put_candidates.empty:
        short_put_candidates = puts_otm.tail(5)
    if short_put_candidates.empty:
        return None
    short_put = short_put_candidates.iloc[(short_put_candidates["abs_delta"] - target_delta).abs().argmin()]

    # Find short call: lowest strike with delta <= target_delta (OTM call)
    calls_otm = calls[calls["strike_price"] > spot].copy()
    calls_otm["abs_delta"] = calls_otm["delta"].abs()
    short_call_candidates = calls_otm[calls_otm["abs_delta"] <= target_delta + 0.05]
    if short_call_candidates.empty:
        short_call_candidates = calls_otm.head(5)
    if short_call_candidates.empty:
        return None
    short_call = short_call_candidates.iloc[(short_call_candidates["abs_delta"] - target_delta).abs().argmin()]

    sp_strike = short_put["strike_price"]
    sc_strike = short_call["strike_price"]
    lp_strike = sp_strike - width
    lc_strike = sc_strike + width

    # Find long legs — nearest available strike at or beyond the target width
    # Long put: nearest strike <= target (further OTM for protection)
    lp_candidates = puts[puts["strike_price"] <= lp_strike + 0.01]
    # Long call: nearest strike >= target (further OTM for protection)
    lc_candidates = calls[calls["strike_price"] >= lc_strike - 0.01]

    if lp_candidates.empty or lc_candidates.empty:
        return None

    # Pick the closest to our target width (maximizes credit while respecting wing width)
    long_put = lp_candidates.iloc[(lp_candidates["strike_price"] - lp_strike).abs().argmin()]
    long_call = lc_candidates.iloc[(lc_candidates["strike_price"] - lc_strike).abs().argmin()]

    # Reject if the actual wing is absurdly wide (>3× target) — means no nearby strikes
    actual_put_wing = sp_strike - long_put["strike_price"]
    actual_call_wing = long_call["strike_price"] - sc_strike
    if actual_put_wing > width * 3 or actual_call_wing > width * 3:
        return None

    # Calculate credit and risk (handle NaN/zero bids)
    def _mid(row):
        b = row.get("bid", 0) or 0
        a = row.get("ask", 0) or 0
        lp = row.get("last_price", 0) or 0
        mid = (b + a) / 2 if (b > 0 and a > 0) else lp
        return mid if mid > 0 else 0

    def _bid(row):
        v = row.get("bid", 0) or 0
        return v if v > 0 else _mid(row)

    def _ask(row):
        v = row.get("ask", 0) or 0
        return v if v > 0 else _mid(row)

    def _is_live(row):
        return bool(row.get("quote_live", True))

    # For short legs (we sell): use bid for natural, mid for theoretical
    # For long legs (we buy): use ask for natural, mid for theoretical
    # When a leg has no live quote, use conservative pricing:
    #   - Short legs: use bid (lower, conservative for credit)
    #   - Long legs: use ask (higher, conservative for cost)
    sp_mid = _mid(short_put)
    sc_mid = _mid(short_call)
    lp_mid = _mid(long_put)
    lc_mid = _mid(long_call)

    # Count legs with synthetic (non-live) quotes
    _live_flags = [_is_live(short_put), _is_live(long_put), _is_live(short_call), _is_live(long_call)]
    _n_synthetic = sum(1 for f in _live_flags if not f)

    # Conservative credit: if any leg has synthetic quotes, price shorts at bid
    # and longs at ask to avoid inflated credit estimates
    if _n_synthetic > 0:
        credit_conservative = (_bid(short_put) + _bid(short_call)) - (_ask(long_put) + _ask(long_call))
        credit_mid = (sp_mid + sc_mid) - (lp_mid + lc_mid)
        # Blend: weight toward conservative when more legs are synthetic
        _synth_weight = min(_n_synthetic / 4, 0.75)  # max 75% conservative
        credit = credit_mid * (1 - _synth_weight) + credit_conservative * _synth_weight
    else:
        credit = (sp_mid + sc_mid) - (lp_mid + lc_mid)

    if credit <= 0.01:
        return None

    # Spread bid/ask
    spread_natural = (_bid(short_put) + _bid(short_call)) - (_ask(long_put) + _ask(long_call))
    spread_mid = (sp_mid + sc_mid) - (lp_mid + lc_mid)

    # ── Liquidity scoring ──
    def _leg_liquidity(row):
        oi = float(row.get("open_interest", 0) or 0)
        vol = float(row.get("volume", 0) or 0)
        b = float(row.get("bid", 0) or 0)
        a = float(row.get("ask", 0) or 0)
        ba_width = (a - b) if (a > 0 and b > 0) else 999
        return {"oi": oi, "vol": vol, "ba_width": round(ba_width, 2)}

    legs_liq = [_leg_liquidity(r) for r in [short_put, short_call, long_put, long_call]]
    min_oi = min(l["oi"] for l in legs_liq)
    total_vol = sum(l["vol"] for l in legs_liq)
    max_ba = max(l["ba_width"] for l in legs_liq)
    avg_ba = sum(l["ba_width"] for l in legs_liq) / 4

    # Liquidity grade: A (excellent) to F (untradeable)
    if min_oi >= 500 and max_ba <= 0.10 and total_vol >= 100:
        liq_grade = "A"
    elif min_oi >= 100 and max_ba <= 0.30 and total_vol >= 20:
        liq_grade = "B"
    elif min_oi >= 50 and max_ba <= 0.60:
        liq_grade = "C"
    elif min_oi >= 10 and max_ba <= 1.50:
        liq_grade = "D"
    else:
        liq_grade = "F"

    # Slippage estimate: avg bid-ask / 2 × 4 legs (round-trip cost in per-share terms)
    slippage_est = avg_ba * 2  # 4 legs, crossing half the spread on each

    # Adaptive fill estimate based on liquidity grade
    # A-grade (tight markets, high OI): expect ~40% improvement from natural toward mid
    # B-grade: ~30%  C-grade: ~20%  D-grade: ~10%  F-grade: ~5% (basically stuck at natural)
    _FILL_PCT = {"A": 0.40, "B": 0.30, "C": 0.20, "D": 0.10, "F": 0.05}
    fill_pct = _FILL_PCT.get(liq_grade, 0.15)
    if spread_natural < spread_mid:
        spread_estimate = spread_natural + (spread_mid - spread_natural) * fill_pct
    else:
        spread_estimate = spread_mid

    put_width = abs(sp_strike - long_put["strike_price"])
    call_width = abs(long_call["strike_price"] - sc_strike)
    max_width = max(put_width, call_width)
    max_risk = max_width - credit
    if max_risk <= 0:
        return None

    # Probability of profit (approximate from short deltas, handle NaN)
    sp_delta = abs(float(short_put.get("delta", 0) or 0)) or target_delta
    sc_delta = abs(float(short_call.get("delta", 0) or 0)) or target_delta
    pop = 1 - sp_delta - sc_delta  # P(between short strikes)
    pop = max(0, min(1, pop))

    # Expected value
    ev = credit * pop - max_risk * (1 - pop)

    # IV from short legs (average, handle NaN)
    _sp_iv = float(short_put.get("implied_volatility", 0) or 0)
    _sc_iv = float(short_call.get("implied_volatility", 0) or 0)
    avg_iv = (_sp_iv + _sc_iv) / 2 if (_sp_iv + _sc_iv) > 0 else 0

    # ── Net position Greeks (DGTV) ──
    def _safe_greek(row, field):
        v = row.get(field, 0)
        try:
            v = float(v) if v else 0.0
            return v if not np.isnan(v) else 0.0
        except (TypeError, ValueError):
            return 0.0

    # Iron condor: short the short legs, long the long legs
    # Short leg sign = -1 (we sold), Long leg sign = +1 (we bought)
    net_delta = (-_safe_greek(short_put, "delta") + _safe_greek(long_put, "delta")
                 - _safe_greek(short_call, "delta") + _safe_greek(long_call, "delta"))
    net_gamma = (-_safe_greek(short_put, "gamma") + _safe_greek(long_put, "gamma")
                 - _safe_greek(short_call, "gamma") + _safe_greek(long_call, "gamma"))
    net_theta = (-_safe_greek(short_put, "theta") + _safe_greek(long_put, "theta")
                 - _safe_greek(short_call, "theta") + _safe_greek(long_call, "theta"))
    net_vega = (-_safe_greek(short_put, "vega") + _safe_greek(long_put, "vega")
                - _safe_greek(short_call, "vega") + _safe_greek(long_call, "vega"))

    # Theta/Vega ratio: daily theta earned per unit of vol risk
    theta_vega_ratio = abs(net_theta / net_vega) if net_vega != 0 else 0

    # Base score: credit/risk ratio × POP (pure economic edge)
    # IV signal captured by IVR band + VRP in adj_score, not here
    score = (credit / max_risk) * pop

    # ── Exit management / profit target ──
    target_pct = profit_target / 100
    target_debit = credit * (1 - target_pct)   # close spread for this debit
    profit_at_target = credit * target_pct
    return_at_target = profit_at_target / max_risk * 100 if max_risk > 0 else 0

    # Breakevens
    lp_strike_val = long_put["strike_price"]
    lc_strike_val = long_call["strike_price"]
    upper_be = sc_strike + credit
    lower_be = sp_strike - credit
    upper_be_pct = (upper_be - spot) / spot * 100
    lower_be_pct = (spot - lower_be) / spot * 100

    # Days to target via Black-Scholes forward pricing (spot & IV held constant)
    # Sampled every 3 days for speed; exact day found by interpolation
    rfr = 0.045
    sp_iv = float(short_put.get("implied_volatility", 0) or 0) or 0.20
    sc_iv = float(short_call.get("implied_volatility", 0) or 0) or 0.20
    lp_iv = float(long_put.get("implied_volatility", 0) or 0) or sp_iv
    lc_iv = float(long_call.get("implied_volatility", 0) or 0) or sc_iv

    # ── Adjustment zones ──
    # 30-delta trigger: spot price where the short leg's delta reaches ~0.30
    # Using BS inverse: for a put, delta = N(d1) - 1 ≈ -0.30 when spot drops to a certain level
    # Approximate by interpolating: at what spot does the short option become 30-delta?
    # For puts: lower spot → higher |delta|. For calls: higher spot → higher delta.
    from scipy.stats import norm as _norm

    def _find_delta_trigger(strike, iv, dte_days, opt_type, target_delta=0.30):
        """Find spot price where option delta reaches target_delta (absolute)."""
        T = dte_days / 365
        if T <= 0 or iv <= 0:
            return strike  # fallback to strike itself
        # Binary search for the spot that produces target delta
        lo, hi = strike * 0.70, strike * 1.30
        for _ in range(40):
            mid = (lo + hi) / 2
            d1 = (np.log(mid / strike) + (rfr + iv**2 / 2) * T) / (iv * np.sqrt(T))
            if opt_type == "put":
                delta_abs = abs(_norm.cdf(d1) - 1)
            else:
                delta_abs = _norm.cdf(d1)
            if delta_abs < target_delta:
                if opt_type == "put":
                    hi = mid  # need spot lower for put delta to increase
                else:
                    lo = mid  # need spot higher for call delta to increase
            else:
                if opt_type == "put":
                    lo = mid
                else:
                    hi = mid
        return round((lo + hi) / 2, 2)

    put_30d_trigger = _find_delta_trigger(sp_strike, sp_iv, actual_dte, "put", 0.30)
    call_30d_trigger = _find_delta_trigger(sc_strike, sc_iv, actual_dte, "call", 0.30)

    # 21 DTE time stop
    time_stop_dte = 21

    days_to_target = actual_dte
    _leg_params = (spot, sp_strike, sc_strike, lp_strike_val, lc_strike_val,
                   sp_iv, sc_iv, lp_iv, lc_iv, rfr)
    for day in range(1, actual_dte, 3):  # sample every 3 days
        T_rem = (actual_dte - day) / 365
        if T_rem <= 1 / 365:
            break
        sv = (black_scholes(spot, sp_strike, T_rem, rfr, sp_iv, "put")
              + black_scholes(spot, sc_strike, T_rem, rfr, sc_iv, "call")
              - black_scholes(spot, lp_strike_val, T_rem, rfr, lp_iv, "put")
              - black_scholes(spot, lc_strike_val, T_rem, rfr, lc_iv, "call"))
        if sv <= target_debit:
            days_to_target = max(1, day - 1)  # conservative (between this sample and prev)
            break

    return {
        "expiration": best_exp,
        "dte": actual_dte,
        "spot": spot,
        # Legs
        "short_put": sp_strike,
        "long_put": lp_strike_val,
        "short_call": sc_strike,
        "long_call": lc_strike_val,
        # Economics
        "credit": round(credit, 2),
        "credit_100": round(credit * 100, 0),
        "max_risk": round(max_risk, 2),
        "max_risk_100": round(max_risk * 100, 0),
        "return_on_risk": round(credit / max_risk * 100, 1),
        # Spread pricing (per share)
        "spread_natural": round(spread_natural, 2),
        "spread_mid": round(spread_mid, 2),
        "spread_estimate": round(spread_estimate, 2),
        # Probability
        "pop": round(pop * 100, 1),
        "ev_per_contract": round(ev * 100, 0),
        # Greeks (per share — multiply by 100 for per-contract dollar Greeks)
        "avg_iv": round(avg_iv * 100, 1),
        "sp_delta": round(sp_delta, 3),
        "sc_delta": round(sc_delta, 3),
        "net_delta": round(net_delta, 4),
        "net_gamma": round(net_gamma, 4),
        "net_theta": round(net_theta, 4),
        "net_vega": round(net_vega, 4),
        "theta_vega_ratio": round(theta_vega_ratio, 3),
        # Score
        "score": round(score, 4),
        # Exit management
        "target_pct": profit_target,
        "target_debit": round(target_debit, 2),
        "profit_at_target": round(profit_at_target, 2),
        "profit_at_target_100": round(profit_at_target * 100, 0),
        "return_at_target": round(return_at_target, 1),
        "days_to_target": days_to_target,
        # Leg IVs for deferred theta_path computation (top-5 only)
        "_leg_params": _leg_params,
        # Breakevens
        "upper_be": round(upper_be, 2),
        "lower_be": round(lower_be, 2),
        "upper_be_pct": round(upper_be_pct, 1),
        "lower_be_pct": round(lower_be_pct, 1),
        # Adjustment zones
        "put_30d_trigger": put_30d_trigger,
        "call_30d_trigger": call_30d_trigger,
        "time_stop_dte": time_stop_dte,
        # Liquidity
        "liq_grade": liq_grade,
        "min_oi": int(min_oi),
        "total_vol": int(total_vol),
        "max_ba": round(max_ba, 2),
        "slippage_est": round(slippage_est, 2),
        # Wing width context
        "wing_pct": round(width / spot * 100, 1) if spot > 0 else 0,
        "optimal_wing": round(spot / 10, 0),
        # Quote quality
        "n_synthetic_legs": _n_synthetic,
        # Per-leg pricing for transparency
        "legs": [
            {"label": f"{sp_strike:.0f}P (short)", "bid": round(_bid(short_put), 2), "ask": round(_ask(short_put), 2), "mid": round(sp_mid, 2), "oi": int(float(short_put.get("open_interest", 0) or 0)), "vol": int(float(short_put.get("volume", 0) or 0)), "live": _is_live(short_put)},
            {"label": f"{lp_strike_val:.0f}P (long)", "bid": round(_bid(long_put), 2), "ask": round(_ask(long_put), 2), "mid": round(lp_mid, 2), "oi": int(float(long_put.get("open_interest", 0) or 0)), "vol": int(float(long_put.get("volume", 0) or 0)), "live": _is_live(long_put)},
            {"label": f"{sc_strike:.0f}C (short)", "bid": round(_bid(short_call), 2), "ask": round(_ask(short_call), 2), "mid": round(sc_mid, 2), "oi": int(float(short_call.get("open_interest", 0) or 0)), "vol": int(float(short_call.get("volume", 0) or 0)), "live": _is_live(short_call)},
            {"label": f"{lc_strike_val:.0f}C (long)", "bid": round(_bid(long_call), 2), "ask": round(_ask(long_call), 2), "mid": round(lc_mid, 2), "oi": int(float(long_call.get("open_interest", 0) or 0)), "vol": int(float(long_call.get("volume", 0) or 0)), "live": _is_live(long_call)},
        ],
    }


def _find_alt_expirations(chain: pd.DataFrame, spot: float, dte_min: int, dte_max: int,
                           target_delta: float, width: float, best_exp: str,
                           profit_target: int = 50) -> list[dict]:
    """Find condor summaries for alternative expirations (excluding the best one).

    Returns a list of lightweight dicts: {exp, dte, credit, credit_per_day, max_risk, pop, return_on_risk}.
    Used only for top-5 detail card comparison tables. Max 3 alternatives.
    """
    if chain is None or chain.empty or spot is None or spot <= 0:
        return []

    chain = chain.copy()
    if "dte" not in chain.columns:
        chain["dte"] = (pd.to_datetime(chain["expiration_date"]) - pd.Timestamp.now()).dt.days
    chain = chain[(chain["dte"] >= dte_min) & (chain["dte"] <= dte_max)]
    if chain.empty:
        return []

    # Get unique expirations excluding the best one
    all_exps = chain[chain["expiration_date"] != best_exp]["expiration_date"].unique()
    if len(all_exps) == 0:
        return []

    # Sort by DTE
    exp_dte = []
    for exp in all_exps:
        dte = chain[chain["expiration_date"] == exp]["dte"].iloc[0]
        exp_dte.append((exp, dte))
    exp_dte.sort(key=lambda x: x[1])

    # Pick up to 3 spread across the range
    if len(exp_dte) <= 3:
        selected = exp_dte
    else:
        # First, middle, last
        mid_idx = len(exp_dte) // 2
        selected = [exp_dte[0], exp_dte[mid_idx], exp_dte[-1]]

    alts = []
    for exp, dte in selected:
        exp_chain = chain[chain["expiration_date"] == exp]
        calls = exp_chain[exp_chain["contract_type"] == "call"].sort_values("strike_price")
        puts = exp_chain[exp_chain["contract_type"] == "put"].sort_values("strike_price")
        if calls.empty or puts.empty:
            continue

        # Find short strikes at target delta
        puts_otm = puts[puts["strike_price"] < spot].copy()
        if puts_otm.empty:
            continue
        puts_otm["abs_delta"] = puts_otm["delta"].abs()
        sp_cands = puts_otm[puts_otm["abs_delta"] <= target_delta + 0.05]
        if sp_cands.empty:
            sp_cands = puts_otm.tail(3)
        if sp_cands.empty:
            continue
        sp_row = sp_cands.iloc[(sp_cands["abs_delta"] - target_delta).abs().argmin()]

        calls_otm = calls[calls["strike_price"] > spot].copy()
        if calls_otm.empty:
            continue
        calls_otm["abs_delta"] = calls_otm["delta"].abs()
        sc_cands = calls_otm[calls_otm["abs_delta"] <= target_delta + 0.05]
        if sc_cands.empty:
            sc_cands = calls_otm.head(3)
        if sc_cands.empty:
            continue
        sc_row = sc_cands.iloc[(sc_cands["abs_delta"] - target_delta).abs().argmin()]

        sp_k = sp_row["strike_price"]
        sc_k = sc_row["strike_price"]

        # Mid prices
        def _mid(row):
            b = row.get("bid", 0) or 0
            a = row.get("ask", 0) or 0
            lp = row.get("last_price", 0) or 0
            mid = (b + a) / 2 if (b > 0 and a > 0) else lp
            return mid if mid > 0 else 0

        # Find long legs — nearest available strike at or beyond target width
        lp_target = sp_k - width
        lc_target = sc_k + width
        lp_rows = puts[puts["strike_price"] <= lp_target + 0.01]
        lc_rows = calls[calls["strike_price"] >= lc_target - 0.01]
        if lp_rows.empty or lc_rows.empty:
            continue
        lp_row = lp_rows.iloc[(lp_rows["strike_price"] - lp_target).abs().argmin()]
        lc_row = lc_rows.iloc[(lc_rows["strike_price"] - lc_target).abs().argmin()]
        if (sp_k - lp_row["strike_price"]) > width * 3 or (lc_row["strike_price"] - sc_k) > width * 3:
            continue

        credit = (_mid(sp_row) + _mid(sc_row)) - (_mid(lp_row) + _mid(lc_row))
        if credit <= 0.01:
            continue

        put_w = abs(sp_k - lp_row["strike_price"])
        call_w = abs(lc_row["strike_price"] - sc_k)
        max_w = max(put_w, call_w)
        max_risk = max_w - credit
        if max_risk <= 0:
            continue

        sp_d = abs(float(sp_row.get("delta", 0) or 0)) or target_delta
        sc_d = abs(float(sc_row.get("delta", 0) or 0)) or target_delta
        pop = max(0, min(1, 1 - sp_d - sc_d))

        target_pct = profit_target / 100
        profit_at_target = credit * target_pct

        alts.append({
            "exp": exp,
            "dte": dte,
            "credit": round(credit * 100, 0),
            "credit_per_day": round(credit * 100 / max(dte, 1), 1),
            "max_risk": round(max_risk * 100, 0),
            "pop": round(pop * 100, 1),
            "return_on_risk": round(credit / max_risk * 100, 1),
            "profit_at_target": round(profit_at_target * 100, 0),
            "short_put": sp_k,
            "short_call": sc_k,
        })

    return alts


def _compute_theta_path(r: dict) -> list:
    """Compute full day-by-day theta decay path for chart display. Called only for top-5."""
    params = r.get("_leg_params")
    if not params:
        return []
    spot, sp_k, sc_k, lp_k, lc_k, sp_iv, sc_iv, lp_iv, lc_iv, rfr = params
    dte = r["dte"]
    path = []
    for day in range(1, dte):
        T_rem = (dte - day) / 365
        if T_rem <= 1 / 365:
            break
        sv = (black_scholes(spot, sp_k, T_rem, rfr, sp_iv, "put")
              + black_scholes(spot, sc_k, T_rem, rfr, sc_iv, "call")
              - black_scholes(spot, lp_k, T_rem, rfr, lp_iv, "put")
              - black_scholes(spot, lc_k, T_rem, rfr, lc_iv, "call"))
        path.append((day, sv))
    return path


def _scan_ticker(ticker: str, spot: float, dte_min: int, dte_max: int, delta: float,
                  width: float, profit_target: int = 50) -> dict | None:
    """Scan a single ticker for best iron condor.
    Spot price from batch snapshot; falls back to price history or chain midpoint."""
    try:
        chain = fetch_options_chain(ticker)
        if chain is None or chain.empty:
            return None

        # Spot fallback chain: snapshot → price history → chain strike midpoint
        if not spot or spot <= 0:
            try:
                px = fetch_massive_data(ticker, 5)
                if px is not None and not px.empty:
                    spot = float(px["Close"].iloc[-1])
            except Exception:
                pass
        if not spot or spot <= 0:
            # Last resort: midpoint of the chain's strike range near ATM
            _strikes = chain["strike_price"].dropna()
            if not _strikes.empty:
                spot = float(_strikes.median())
        if not spot or spot <= 0:
            return None

        result = _find_best_condor(chain, spot, dte_min, dte_max, delta, width, profit_target)
        if result:
            result["ticker"] = ticker
            result["ivr"] = None
            result["vrp"] = None
            result["hv20"] = None
            result["ivr_band"] = "N/A"
        return result
    except Exception as e:
        logger.warning(f"Iron condor scan failed for {ticker}: {e}")
        return None


# ═══════════════════════════════════════════════
# SCAN EXECUTION
# ═══════════════════════════════════════════════

def _compute_historical_winrate(px: pd.DataFrame, spot: float, sp_strike: float,
                                 sc_strike: float, credit: float, max_risk: float,
                                 dte: int, profit_target_pct: int = 50,
                                 stop_mult: float = 1.5) -> dict | None:
    """Simulate managed iron condor trades over historical price data.

    For each historical date, simulates a condor with the same strike distances,
    modeling three exit paths:
      1. Profit target hit (spread value decays to target via theta approximation)
      2. Stop loss hit (unrealized loss exceeds stop_mult × credit)
      3. Held to expiration (price stayed in range or breached)

    Uses daily price movement to estimate spread P&L progression, not just
    final range containment. This produces a realistic managed win rate.

    Returns dict with managed_wr, expiration_wr, n_trials, early_exits, etc.
    """
    if px is None or len(px) < dte + 30:
        return None

    closes = px["Close"].values
    n = len(closes)
    if n < dte + 10:
        return None

    # Strike distances as % of spot
    put_dist_pct = (spot - sp_strike) / spot
    call_dist_pct = (sc_strike - spot) / spot
    credit_pct = credit / spot  # credit as % of spot (for scaling to historical entries)
    target_pct = profit_target_pct / 100

    wins_managed = 0      # hit profit target or expired in range
    wins_expiration = 0   # expired in range (no early management)
    losses_stop = 0       # hit stop loss
    losses_breach = 0     # held to exp, breached
    early_profit = 0      # subset of wins: closed before expiration
    exp_in_range = 0      # for exp-only WR: how many ended in range (ignoring management)
    max_moves = []

    for i in range(n - dte):
        entry_price = closes[i]
        if entry_price <= 0:
            continue

        hist_sp = entry_price * (1 - put_dist_pct)
        hist_sc = entry_price * (1 + call_dist_pct)
        hist_credit = entry_price * credit_pct
        stop_level = hist_credit * stop_mult

        window = closes[i + 1: i + dte + 1]
        if len(window) < 2:
            continue

        max_move = max(abs(window.max() - entry_price), abs(entry_price - window.min())) / entry_price * 100
        max_moves.append(max_move)

        # Track expiration-only outcome (for comparison, independent of management)
        final_px = window[-1]
        if final_px >= hist_sp and final_px <= hist_sc:
            exp_in_range += 1

        # Simulate day-by-day managed exit
        outcome = None
        for d, px_d in enumerate(window):
            # Approximate unrealized P&L from price movement
            # Put side intrinsic loss (if spot below short put)
            put_loss = max(hist_sp - px_d, 0)
            # Call side intrinsic loss (if spot above short call)
            call_loss = max(px_d - hist_sc, 0)
            intrinsic_loss = put_loss + call_loss

            # Approximate theta decay: credit decays linearly over DTE
            # (simplified — real decay is non-linear, but directionally correct)
            days_held = d + 1
            theta_fraction = days_held / dte
            spread_value_approx = hist_credit * (1 - theta_fraction * 0.7)  # 70% decays by exp

            # Net P&L: credit received - current spread value - intrinsic loss
            if intrinsic_loss > 0:
                # Position is being tested — P&L = credit - intrinsic loss
                unrealized_pnl = hist_credit - intrinsic_loss
            else:
                # Position in profit zone — P&L = credit - remaining spread value
                unrealized_pnl = hist_credit - spread_value_approx

            # Check profit target
            if unrealized_pnl >= hist_credit * target_pct:
                outcome = "early_profit"
                break
            # Check stop loss
            if unrealized_pnl <= -stop_level:
                outcome = "stop_loss"
                break

        if outcome is None:
            # Held to expiration — check final range
            final_price = window[-1]
            if final_price >= hist_sp and final_price <= hist_sc:
                outcome = "exp_win"
            else:
                outcome = "exp_loss"

        if outcome == "early_profit":
            wins_managed += 1
            early_profit += 1
        elif outcome == "exp_win":
            wins_managed += 1
            wins_expiration += 1
        elif outcome == "stop_loss":
            losses_stop += 1
        elif outcome == "exp_loss":
            losses_breach += 1

    total = wins_managed + losses_stop + losses_breach
    if total < 10:
        return None

    # Expiration-only WR: what % expired in range (no management applied)
    exp_wr = round(exp_in_range / max(total, 1) * 100, 1)

    return {
        "win_rate": round(wins_managed / total * 100, 1),  # managed win rate
        "exp_win_rate": exp_wr,  # if held to expiration (no management)
        "n_trials": total,
        "wins": wins_managed,
        "losses": losses_stop + losses_breach,
        "early_profit": early_profit,
        "stopped_out": losses_stop,
        "breached_at_exp": losses_breach,
        "avg_max_move_pct": round(float(np.mean(max_moves)), 1) if max_moves else 0,
        "median_max_move_pct": round(float(np.median(max_moves)), 1) if max_moves else 0,
    }


def _fetch_price_history(ticker: str) -> pd.DataFrame | None:
    """Fetch 252-day price history for a ticker. Thread-safe (uses HTTP, not session_state)."""
    try:
        return fetch_massive_data(ticker, 252)
    except Exception:
        return None


def _fetch_earnings_date(ticker: str) -> dict | None:
    """Fetch next earnings date for a single ticker via yfinance. Thread-safe."""
    try:
        import yfinance as yf
        from datetime import datetime as dt
        info = yf.Ticker(ticker).info or {}
        ts = info.get("earningsTimestampStart")
        if ts and ts > 0:
            ed = dt.utcfromtimestamp(ts).date()
            days = (ed - dt.now().date()).days
            if 0 < days <= 90:
                return {"date": ed.isoformat(), "days": days}
    except Exception:
        pass
    return None


def _enrich_ivr_vrp(r: dict, px: pd.DataFrame | None) -> None:
    """Compute IVR, VRP, and HV20. Mutates r in place.

    IVR source priority:
      1. metrics_store percentile (accumulated daily ATM IV snapshots in Supabase)
      2. HV-based proxy (rank current IV against 252-day HV range from price history)
    Each scan also saves today's ATM IV to metrics_store, so IVR accuracy
    improves automatically over time as history deepens.
    """
    ticker = r["ticker"]
    current_iv = r["avg_iv"] / 100
    hv20 = None
    vrp = None
    ivr = None

    # HV20 and VRP from price history
    hv_series = None
    if px is not None and len(px) > 30:
        hv_series = px["Close"].pct_change().rolling(20).std().dropna() * np.sqrt(252)
        if len(hv_series) > 0:
            hv20 = float(hv_series.iloc[-1] * 100)
        if hv20 is not None and current_iv > 0:
            vrp = round((current_iv * 100) - hv20, 1)

    # IVR: try metrics_store first (fast Supabase read, ~100ms)
    if current_iv > 0:
        try:
            ivr = percentile_rank(ticker, "atm_iv", current_value=current_iv)
        except Exception:
            pass

    # Fallback: HV-based IVR proxy (rank current IV against 252-day HV range)
    if ivr is None and current_iv > 0 and hv_series is not None and len(hv_series) >= 20:
        hv_vals = hv_series.values
        ivr = float(np.sum(hv_vals <= current_iv) / len(hv_vals) * 100)

    # Save today's ATM IV to metrics_store (builds history for future IVR lookups)
    try:
        _snapshot = {"ticker": ticker, "atm_iv": current_iv}
        if hv20 is not None:
            _snapshot["hv20"] = hv20 / 100
        if vrp is not None:
            _snapshot["vrp"] = vrp / 100
        save_snapshot(ticker, _snapshot)
    except Exception:
        pass

    r["ivr"] = round(ivr, 1) if ivr is not None else None
    r["vrp"] = round(vrp, 1) if vrp is not None else None
    r["hv20"] = round(hv20, 1) if hv20 is not None else None
    if ivr is not None:
        if ivr < 30:
            r["ivr_band"] = "Low"
        elif ivr < 50:
            r["ivr_band"] = "Normal"
        elif ivr <= 75:
            r["ivr_band"] = "Optimal"
        else:
            r["ivr_band"] = "Extreme"


if scan and tickers:
    # Clear old results so stale filter/sort widgets don't render during scan
    st.session_state.pop("ic_results", None)
    st.session_state.pop("ic_ai_result", None)
    results = []
    with fun_loader("data"):
        progress = st.progress(0, text="Fetching spot prices...")

        # Step 1: Batch snapshot for spot prices
        snapshots = polygon_batch_snapshot(tickers)

        # Step 2: Fetch chains + scan condors on main thread (session_state not thread-safe)
        # Price histories + earnings fetched in parallel (thread-safe HTTP only)
        progress.progress(0.05, text="Scanning options chains...")
        price_cache = {}
        earnings_cache = {}

        # Pre-fetch price histories and earnings in parallel (no session_state dependency)
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

        # Scan condors sequentially on main thread (fetch_options_chain uses session_state)
        for idx, tk in enumerate(tickers):
            progress.progress(0.3 + 0.4 * (idx + 1) / len(tickers),
                               text=f"Scanning {tk}... ({idx + 1}/{len(tickers)})")
            try:
                spot = snapshots.get(tk, {}).get("price")
                spot = round(spot, 2) if spot else 0
                result = _scan_ticker(tk, spot, target_dte_min, target_dte_max,
                                       short_delta, wing_width, profit_target_pct)
                if result:
                    results.append(result)
            except Exception as e:
                logger.warning(f"Scan failed for {tk}: {e}")

        # Step 3: Enrich with IVR/VRP + earnings (pure computation, no API calls)
        if results:
            progress.progress(0.65, text="Computing IV percentiles, VRP & earnings risk...")
            for r in results:
                try:
                    _enrich_ivr_vrp(r, price_cache.get(r["ticker"]))
                except Exception as e:
                    logger.debug(f"IVR/VRP enrichment failed for {r['ticker']}: {e}")

                # Earnings enrichment
                tk = r["ticker"]
                earn = earnings_cache.get(tk)
                if earn and earn["days"] <= r["dte"]:
                    r["earnings_date"] = earn["date"]
                    r["earnings_days"] = earn["days"]
                    r["earnings_before_exp"] = True
                    # Expected move from ATM straddle price approximation
                    # Using BS: ATM straddle ≈ 2 × S × σ × √(T/365) × 0.8 (rule of thumb)
                    avg_iv_dec = r["avg_iv"] / 100
                    if avg_iv_dec > 0:
                        exp_move_pct = avg_iv_dec * np.sqrt(r["dte"] / 365) * 100
                        r["expected_move_pct"] = round(exp_move_pct, 1)
                        # Check if expected move exceeds distance to short strikes
                        sp_dist = (r["spot"] - r["short_put"]) / r["spot"] * 100
                        sc_dist = (r["short_call"] - r["spot"]) / r["spot"] * 100
                        r["strikes_inside_em"] = exp_move_pct > min(sp_dist, sc_dist)
                    else:
                        r["expected_move_pct"] = None
                        r["strikes_inside_em"] = False
                else:
                    r["earnings_date"] = earn["date"] if earn else None
                    r["earnings_days"] = earn["days"] if earn else None
                    r["earnings_before_exp"] = False
                    r["expected_move_pct"] = None
                    r["strikes_inside_em"] = False

                # ── Historical win rate (managed exit simulation) ──
                _px = price_cache.get(tk)
                hist_wr = _compute_historical_winrate(
                    _px, r["spot"], r["short_put"], r["short_call"],
                    r["credit"], r["max_risk"], r["dte"],
                    profit_target_pct, stop_multiplier)
                r["hist_winrate"] = hist_wr  # dict or None

                # ── Position sizing (Kelly Criterion) ──
                # Win = profit at target. Loss = stop_multiplier × credit.
                # Use historical managed WR if available, otherwise POP + bump
                pop_dec = r["pop"] / 100
                if hist_wr and hist_wr.get("win_rate", 0) > 0:
                    managed_wr = min(0.95, hist_wr["win_rate"] / 100)
                else:
                    managed_wr = min(0.95, pop_dec + win_rate_bump / 100)
                q = 1 - managed_wr
                profit_at_target_per = r["profit_at_target_100"]  # win per contract ($)
                stop_loss_per = min(r["credit_100"] * stop_multiplier, r["max_risk_100"])

                if stop_loss_per > 0 and profit_at_target_per > 0 and managed_wr > 0:
                    b = profit_at_target_per / stop_loss_per  # win/loss payout ratio
                    full_kelly = (managed_wr * b - q) / b if b > 0 else 0
                    # Kelly can be negative (meaning negative edge) — record raw value
                    adj_kelly = max(0, full_kelly) * kelly_fraction
                    # Hard cap — use this as the sizing basis (Kelly may be 0 for asymmetric payouts)
                    capped_pct = min(adj_kelly, max_risk_pct / 100) if adj_kelly > 0 else max_risk_pct / 100
                    # Contracts from sizing — don't force min 1 if account can't support it
                    size_dollars = account_size * capped_pct
                    contracts = int(size_dollars / stop_loss_per) if stop_loss_per > 0 else 0

                    # Reg-T margin: for defined-risk spreads, margin = max loss per contract
                    margin_per = r["max_risk_100"]
                    total_margin = margin_per * contracts
                    total_risk = stop_loss_per * contracts
                    total_credit = r["credit_100"] * contracts
                else:
                    full_kelly = 0
                    adj_kelly = 0
                    capped_pct = 0
                    contracts = 0
                    total_margin = 0
                    total_risk = 0
                    total_credit = 0

                r["managed_wr"] = round(managed_wr * 100, 1)
                r["stop_mult"] = stop_multiplier
                r["kelly_full"] = round(full_kelly * 100, 1)
                r["kelly_adj"] = round(adj_kelly * 100, 1)
                r["kelly_capped"] = round(capped_pct * 100, 1)
                r["size_contracts"] = contracts
                r["size_total_risk"] = round(total_risk, 0)
                r["size_total_credit"] = round(total_credit, 0)
                r["size_margin"] = round(total_margin, 0)
                r["size_pct_of_account"] = round(total_risk / account_size * 100, 1) if account_size > 0 else 0

            progress.progress(0.95, text="Done.")
        progress.empty()

    if results:
        st.session_state["ic_results"] = results
        st.session_state["ic_scan_time"] = pd.Timestamp.now()
    else:
        st.warning("No valid iron condor setups found. Try relaxing the delta or DTE range.")


# ═══════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════

if "ic_results" in st.session_state and st.session_state["ic_results"]:
    raw_results = st.session_state["ic_results"]

    # ── Scan timestamp & market hours warning ──
    _scan_ts = st.session_state.get("ic_scan_time")
    if _scan_ts:
        _age_min = (pd.Timestamp.now() - _scan_ts).total_seconds() / 60
        _ts_str = _scan_ts.strftime("%H:%M:%S")
        _hour = _scan_ts.hour
        _weekday = _scan_ts.weekday()
        _is_market = 0 <= _weekday <= 4 and 9 <= _hour < 16  # rough EST check
        _age_label = f"{_age_min:.0f}min ago" if _age_min < 60 else f"{_age_min / 60:.1f}hr ago"
        _stale = _age_min > 30
        if not _is_market:
            st.warning("Scanned outside market hours — quotes may be stale. Re-scan after 9:30 AM ET for live data.")
        elif _stale:
            st.info(f"Results are {_age_label} (scanned at {_ts_str}). Consider re-scanning for fresh quotes.")

    freshness_bar(
        ("Chains", _scan_ts, 30, 120),
        ("Prices", _scan_ts, 10, 30),
        ("IVR", _scan_ts, 60, 240),
    )

    # ── Integrated composite score (non-mutating) ──
    # Combines all computed signals into a single ranking metric.
    # Base: credit/risk × POP (economic edge)
    # Multipliers: IVR band, VRP, liquidity, earnings, historical win rate, theta efficiency
    IVR_BAND_WEIGHTS = {"Optimal": 1.5, "Normal": 1.0, "Extreme": 0.7, "Low": 0.6, "N/A": 0.8}
    _LIQ_MULT = {"A": 1.2, "B": 1.0, "C": 0.85, "D": 0.6, "F": 0.3}

    scored_results = []
    for r in raw_results:
        # Net credit after estimated slippage (the real edge after execution costs)
        slippage = r.get("slippage_est", 0)  # per-share slippage
        net_credit = max(0.01, r["credit"] - slippage)
        net_credit_100 = net_credit * 100
        net_risk = r["max_risk"] + slippage  # slippage widens effective risk
        base = (net_credit / net_risk) * (r["pop"] / 100) if net_risk > 0 else 0

        # IVR band weight (50-75 optimal zone per quant manual)
        ivr_w = IVR_BAND_WEIGHTS.get(r.get("ivr_band", "N/A"), 0.8)

        # VRP: positive = structural edge, negative = selling cheap vol (penalize)
        vrp_val = r.get("vrp") or 0
        vrp_w = 1.0 + vrp_val / 100  # +5 VRP → 1.05, -5 VRP → 0.95
        vrp_w = max(0.5, vrp_w)  # floor at 0.5 to avoid zeroing out

        # Liquidity penalty (illiquid setups are untradeable regardless of other metrics)
        liq_w = _LIQ_MULT.get(r.get("liq_grade", "F"), 0.3)

        # Earnings penalty (jump-diffusion risk if before expiration)
        earn_w = 0.4 if r.get("earnings_before_exp") else 1.0
        if r.get("strikes_inside_em"):
            earn_w = 0.2  # even worse: short strikes inside expected move

        # Historical win rate bonus (empirical validation of the setup)
        hwr = r.get("hist_winrate")
        if hwr and hwr["win_rate"] > 0:
            # Normalize: 70% → 1.0, 80% → 1.1, 60% → 0.9, 50% → 0.8
            hwr_w = 0.3 + hwr["win_rate"] / 100
        else:
            hwr_w = 0.9  # slight penalty for no data

        # Theta efficiency (credit per day / max risk — rewards faster capital turnover)
        dte = max(r["dte"], 1)
        credit_per_day = r["credit_100"] / dte
        theta_eff = 1.0 + credit_per_day / max(r["max_risk_100"], 1) * 10  # normalized boost

        adj = round(base * ivr_w * vrp_w * liq_w * earn_w * hwr_w * theta_eff, 4)
        scored = {**r, "adj_score": adj, "net_credit_100": round(net_credit_100, 0)}
        scored_results.append(scored)

    # ── Sort & Filter Controls ──
    # Compute score percentiles for the "Top N" filter
    _all_scores = sorted([r["adj_score"] for r in scored_results], reverse=True)
    _top10_cutoff = _all_scores[9] if len(_all_scores) >= 10 else 0
    _top20_cutoff = _all_scores[19] if len(_all_scores) >= 20 else 0

    with st.container(border=True):
        sf1, sf2, sf3, sf4, sf5, sf6 = st.columns(6)
        with sf1:
            sort_by = st.selectbox("Sort by", ["Score", "POP", "Credit", "IVR", "VRP", "Hist WR", "Liquidity"],
                                    index=0)
        with sf2:
            show_n = st.selectbox("Show", ["All", "Top 10", "Top 20", "Top 5"], index=1)
        with sf3:
            min_pop = st.number_input("Min POP %", value=40, min_value=0, max_value=95, step=5)
        with sf4:
            min_liq = st.selectbox("Min liquidity", ["Any", "D+", "C+", "B+", "A only"], index=2)
        with sf5:
            ev_filter = st.checkbox("Positive EV only", value=False)
        with sf6:
            if _scan_ts:
                _am = (pd.Timestamp.now() - _scan_ts).total_seconds() / 60
                st.caption(f"Scanned {_am:.0f}min ago" if _am < 60 else f"Scanned {_am / 60:.1f}hr ago")
            else:
                st.caption("")

    # ── Apply all filters ──
    filtered = scored_results
    if ivr_filter == "≥30":
        filtered = [r for r in filtered if r.get("ivr") is None or r["ivr"] >= 30]
    elif ivr_filter == "50-75 (Optimal)":
        filtered = [r for r in filtered if r.get("ivr") is not None and 50 <= r["ivr"] <= 75]
    elif ivr_filter == "≥50":
        filtered = [r for r in filtered if r.get("ivr") is None or r["ivr"] >= 50]
    elif ivr_filter == "≥75":
        filtered = [r for r in filtered if r.get("ivr") is None or r["ivr"] >= 75]

    if min_vrp != 0.0:
        filtered = [r for r in filtered if r.get("vrp") is not None and r["vrp"] >= min_vrp]
    if exclude_earnings:
        filtered = [r for r in filtered if not r.get("earnings_before_exp")]
    if min_pop > 0:
        filtered = [r for r in filtered if r["pop"] >= min_pop]
    if ev_filter:
        filtered = [r for r in filtered if r.get("ev_per_contract", 0) > 0]

    _LIQ_ORDER = {"A": 4, "B": 3, "C": 2, "D": 1, "F": 0}
    if min_liq == "D+":
        filtered = [r for r in filtered if _LIQ_ORDER.get(r.get("liq_grade", "F"), 0) >= 1]
    elif min_liq == "C+":
        filtered = [r for r in filtered if _LIQ_ORDER.get(r.get("liq_grade", "F"), 0) >= 2]
    elif min_liq == "B+":
        filtered = [r for r in filtered if _LIQ_ORDER.get(r.get("liq_grade", "F"), 0) >= 3]
    elif min_liq == "A only":
        filtered = [r for r in filtered if r.get("liq_grade") == "A"]

    # ── Sort ──
    _sort_keys = {
        "Score": lambda r: r["adj_score"],
        "POP": lambda r: r["pop"],
        "Credit": lambda r: r["credit_100"],
        "IVR": lambda r: r.get("ivr") or 0,
        "VRP": lambda r: r.get("vrp") or -999,
        "Hist WR": lambda r: r["hist_winrate"]["win_rate"] if r.get("hist_winrate") else 0,
        "Liquidity": lambda r: _LIQ_ORDER.get(r.get("liq_grade", "F"), 0),
    }
    results = sorted(filtered, key=_sort_keys.get(sort_by, _sort_keys["Score"]), reverse=True)

    # Apply Top N limit
    _pre_topn = len(results)
    if show_n == "Top 5":
        results = results[:5]
    elif show_n == "Top 10":
        results = results[:10]
    elif show_n == "Top 20":
        results = results[:20]

    _filtered_out = len(scored_results) - _pre_topn
    _topn_note = f", showing {show_n}" if show_n != "All" else ""
    _filter_note = f" ({_filtered_out} filtered out{_topn_note})" if _filtered_out > 0 or show_n != "All" else ""
    st.markdown(f"##### {len(results)} setups{_filter_note}")

    if not results:
        st.info("All setups were filtered out. Try relaxing the filters.")

    # ── Portfolio Summary Bar ──
    if results:
        _total_contracts = sum(r.get("size_contracts", 0) for r in results)
        _total_risk = sum(r.get("size_total_risk", 0) for r in results)
        _total_credit = sum(r.get("size_total_credit", 0) for r in results)
        _earn_count = sum(1 for r in results if r.get("earnings_before_exp"))
        _low_liq = sum(1 for r in results if r.get("liq_grade", "F") in ("D", "F"))
        with st.container(border=True):
            p1, p2, p3, p4, p5 = st.columns(5)
            p1.metric("Total Contracts", f"{_total_contracts}")
            p2.metric("Total Credit", f"${_total_credit:,.0f}")
            p3.metric("Total Risk", f"${_total_risk:,.0f}")
            p4.metric("Earnings Risk", f"{_earn_count} tickers")
            p5.metric("Low Liquidity", f"{_low_liq} tickers")

    # ── Detail Cards (tabbed — one at a time) ──
    top_results = results[:12]
    if top_results:
        st.markdown("##### Setup Details")
        def _fmt_exp(exp_str):
            try:
                return pd.to_datetime(exp_str).strftime("%b %d")
            except Exception:
                return exp_str
        detail_tabs = st.tabs([f"{r['ticker']} · {_fmt_exp(r['expiration'])} · {r['dte']}d ({r.get('liq_grade','?')})" for r in top_results])

        for tab, r in zip(detail_tabs, top_results):
            with tab:
                with error_boundary(f"Detail {r['ticker']}"):
                    # ── Status banner: consolidated warnings ──
                    _ivr = r.get("ivr")
                    _vrp = r.get("vrp")
                    _hv20 = r.get("hv20")
                    _band = r.get("ivr_band", "N/A")
                    _band_colors = {"Optimal": COLORS["success"], "Normal": COLORS["accent"],
                                    "Low": COLORS["danger"], "Extreme": COLORS["warning"], "N/A": COLORS["text_muted"]}
                    _bc = _band_colors.get(_band, COLORS["text_muted"])
                    _lg = r.get("liq_grade", "?")
                    _liq_colors = {"A": COLORS["success"], "B": "#66bb6a", "C": COLORS["warning"],
                                   "D": "#ff8a65", "F": COLORS["danger"]}
                    _lc = _liq_colors.get(_lg, COLORS["text_muted"])

                    _ivr_str = f"{_ivr:.0f}" if _ivr is not None else "N/A"
                    _vrp_str = f"{_vrp:+.1f}%" if _vrp is not None else "N/A"
                    _hv20_str = f"{_hv20:.1f}%" if _hv20 is not None else "N/A"
                    _ba_str = "N/A" if r.get("max_ba", 0) >= 99 else f"{r.get('max_ba', 0):.2f}"

                    # Compact status bar
                    _flags = []
                    if _band == "Extreme":
                        _flags.append(f'<span style="color:{COLORS["warning"]};">IVR &gt;75 jump risk</span>')
                    elif _band == "Low":
                        _flags.append(f'<span style="color:{COLORS["danger"]};">IVR &lt;30 low VRP</span>')
                    if _lg in ("D", "F"):
                        _flags.append(f'<span style="color:{COLORS["danger"]};">Low liquidity ({_lg})</span>')
                    _wing_pct = r.get("wing_pct", 0)
                    if _wing_pct < 1.5 and _wing_pct > 0:
                        _flags.append(f'<span style="color:{COLORS["accent"]};">Narrow wings ({_wing_pct:.1f}%)</span>')
                    if r.get("earnings_before_exp"):
                        _flags.append(f'<span style="color:{COLORS["danger"]};">EARNINGS {r["earnings_days"]}d</span>')
                    _n_synth = r.get("n_synthetic_legs", 0)
                    if _n_synth > 0:
                        _flags.append(f'<span style="color:{COLORS["warning"]};">{_n_synth} leg{"s" if _n_synth > 1 else ""} no live quote</span>')
                    _flags_html = f' · '.join(_flags) if _flags else f'<span style="color:{COLORS["success"]};">No warnings</span>'

                    st.markdown(
                        f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;'
                        f'padding:6px 14px;border:1px solid {_bc};border-radius:6px;margin-bottom:8px;'
                        f'font-size:0.78rem;font-family:monospace;gap:8px;">'
                        f'<span>IVR <b style="color:{_bc};">{_ivr_str}</b> · IV {r["avg_iv"]}% · HV20 {_hv20_str} · VRP <b>{_vrp_str}</b></span>'
                        f'<span>Liq <b style="color:{_lc};">{_html.escape(_lg)}</b> OI {r.get("min_oi", 0):,} · BA {_ba_str}</span>'
                        f'<span>{_flags_html}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    # ── Two-column layout: metrics left, charts right ──
                    col_left, col_right = st.columns([2, 3])

                    with col_left:
                        # Core metrics
                        h1, h2 = st.columns(2)
                        h1.metric(r["ticker"], f"${r['spot']:,.2f}")
                        h2.metric("Open For", f"${r['spread_estimate'] * 100:,.0f} cr")

                        # Price transparency: show natural / estimate / mid
                        _nat = r.get("spread_natural", 0) * 100
                        _est = r.get("spread_estimate", 0) * 100
                        _mid_px = r.get("spread_mid", 0) * 100
                        _fill = {"A": 40, "B": 30, "C": 20, "D": 10, "F": 5}.get(r.get("liq_grade", "F"), 15)
                        st.markdown(
                            f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};font-family:monospace;margin:-8px 0 6px 0;">'
                            f'Natural ${_nat:,.0f} · <b>Fill ${_est:,.0f}</b> · Mid ${_mid_px:,.0f}'
                            f' ({_fill}% improve)</div>',
                            unsafe_allow_html=True,
                        )

                        h3, h4 = st.columns(2)
                        h3.metric("Max Risk", f"${r['max_risk_100']:,.0f}")
                        h4.metric("POP", f"{r['pop']}%")

                        h5, h6 = st.columns(2)
                        h5.metric(f"{r['target_pct']}% Target", f"${r['profit_at_target_100']:,.0f}")
                        h6.metric("Expiration", _fmt_exp(r["expiration"]) + f", {pd.to_datetime(r['expiration']).year}" if r.get("expiration") else "?")
                        h6.caption(f"{r['dte']}d to exp · ~{r['days_to_target']}d to target")

                        h7, h8 = st.columns(2)
                        h7.metric("Breakevens", f"${r['lower_be']:.0f} / ${r['upper_be']:.0f}")
                        h7.caption(f"{r['lower_be_pct']}% / {r['upper_be_pct']}%")
                        _contracts = r.get("size_contracts", 0)
                        if _contracts > 0:
                            h8.metric("Contracts", f"{_contracts}")
                            _kf = r.get("kelly_full", 0)
                            _mwr = r.get("managed_wr", 0)
                            if _kf < 0:
                                h8.caption(f"Kelly: {_kf:.1f}% → {r.get('kelly_capped', 0):.1f}% cap")
                            else:
                                h8.caption(f"Kelly: {r.get('kelly_adj', 0):.1f}% → {r.get('kelly_capped', 0):.1f}%")
                        else:
                            h8.metric("Contracts", "0")

                        # Leg diagram (compact)
                        lp = r["long_put"]
                        sp = r["short_put"]
                        sc = r["short_call"]
                        lc = r["long_call"]
                        st.markdown(
                            f'<div style="display:flex;align-items:center;justify-content:center;gap:3px;'
                            f'font-size:0.72rem;padding:4px 0;font-family:monospace;flex-wrap:wrap;">'
                            f'<span style="color:{COLORS["danger"]};border:1px solid {COLORS["danger"]};padding:1px 6px;border-radius:3px;">'
                            f'{lp:.0f}P</span>'
                            f'<span style="color:#888;">—</span>'
                            f'<span style="color:{COLORS["warning"]};border:1px solid {COLORS["warning"]};padding:1px 6px;border-radius:3px;">'
                            f'{sp:.0f}P</span>'
                            f'<span style="color:#666;">·{r["spot"]:.0f}·</span>'
                            f'<span style="color:{COLORS["warning"]};border:1px solid {COLORS["warning"]};padding:1px 6px;border-radius:3px;">'
                            f'{sc:.0f}C</span>'
                            f'<span style="color:#888;">—</span>'
                            f'<span style="color:{COLORS["danger"]};border:1px solid {COLORS["danger"]};padding:1px 6px;border-radius:3px;">'
                            f'{lc:.0f}C</span>'
                            f'</div>',
                            unsafe_allow_html=True,
                        )

                    with col_right:
                        # Payoff diagram
                        r_spot = r["spot"]
                        prices = np.linspace(lp - 5, lc + 5, 200)
                        credit = r["credit"]
                        pnl = np.zeros_like(prices)
                        for i, px in enumerate(prices):
                            pnl[i] = (credit - max(sp - px, 0) + max(lp - px, 0) - max(px - sc, 0) + max(px - lc, 0)) * 100

                        fig_pnl = go.Figure()
                        fig_pnl.add_trace(go.Scatter(x=prices, y=pnl, fill="tozeroy",
                            fillcolor="rgba(0,255,150,0.08)", line=dict(color=COLORS["success"], width=2),
                            hovertemplate="Price: $%{x:.1f}<br>P&L: $%{y:,.0f}<extra></extra>"))
                        fig_pnl.add_trace(go.Scatter(x=prices, y=np.where(pnl < 0, pnl, 0), fill="tozeroy",
                            fillcolor="rgba(255,68,68,0.12)", line=dict(color=COLORS["danger"], width=0),
                            hoverinfo="skip", showlegend=False))
                        fig_pnl.add_hline(y=0, line_dash="dot", line_color="#555", line_width=1)
                        fig_pnl.add_hline(y=r["profit_at_target_100"], line_dash="dash", line_color=COLORS["warning"], line_width=1,
                            annotation_text=f"{r['target_pct']}% target", annotation_position="top left",
                            annotation_font_size=9, annotation_font_color=COLORS["warning"])
                        fig_pnl.add_vline(x=r_spot, line_dash="dash", line_color=COLORS["accent"], line_width=1,
                            annotation_text="Spot", annotation_position="top")
                        fig_pnl.add_vline(x=r["lower_be"], line_dash="dot", line_color="#ff6b6b", line_width=1,
                            annotation_text=f"BE ${r['lower_be']:.0f}", annotation_position="bottom left",
                            annotation_font_size=8, annotation_font_color="#ff6b6b")
                        fig_pnl.add_vline(x=r["upper_be"], line_dash="dot", line_color="#ff6b6b", line_width=1,
                            annotation_text=f"BE ${r['upper_be']:.0f}", annotation_position="bottom right",
                            annotation_font_size=8, annotation_font_color="#ff6b6b")
                        _put_trig = r.get("put_30d_trigger", sp)
                        _call_trig = r.get("call_30d_trigger", sc)
                        fig_pnl.add_vline(x=_put_trig, line_dash="dashdot", line_color="#ff9800", line_width=1,
                            annotation_text=f"30Δ", annotation_position="top left",
                            annotation_font_size=8, annotation_font_color="#ff9800")
                        fig_pnl.add_vline(x=_call_trig, line_dash="dashdot", line_color="#ff9800", line_width=1,
                            annotation_text=f"30Δ", annotation_position="top right",
                            annotation_font_size=8, annotation_font_color="#ff9800")
                        fig_pnl.add_vrect(x0=_put_trig, x1=sp, fillcolor="rgba(255,152,0,0.06)", line_width=0, layer="below")
                        fig_pnl.add_vrect(x0=sc, x1=_call_trig, fillcolor="rgba(255,152,0,0.06)", line_width=0, layer="below")
                        _stop_pnl = -r["credit_100"]
                        fig_pnl.add_hline(y=_stop_pnl, line_dash="dot", line_color=COLORS["danger"], line_width=1,
                            annotation_text=f"1× stop", annotation_position="bottom left",
                            annotation_font_size=8, annotation_font_color=COLORS["danger"])
                        fig_pnl.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)", height=250, margin=dict(l=40, r=10, t=20, b=35),
                            xaxis_title="Price at Exp ($)", yaxis_title="P&L ($)", showlegend=False)
                        st.plotly_chart(fig_pnl, use_container_width=True)

                        # Theta decay
                        theta_path = _compute_theta_path(r)
                        if theta_path:
                            tp_days = [p[0] for p in theta_path]
                            tp_vals = [p[1] * 100 for p in theta_path]
                            fig_decay = go.Figure()
                            fig_decay.add_trace(go.Scatter(x=tp_days, y=tp_vals,
                                line=dict(color=COLORS["accent"], width=2), fill="tozeroy",
                                fillcolor="rgba(0,200,255,0.06)",
                                hovertemplate="Day %{x}<br>Value: $%{y:,.0f}<extra></extra>"))
                            fig_decay.add_hline(y=r["credit_100"], line_dash="dot", line_color=COLORS["success"], line_width=1,
                                annotation_text=f"Credit", annotation_position="top left",
                                annotation_font_size=8, annotation_font_color=COLORS["success"])
                            fig_decay.add_hline(y=r["target_debit"] * 100, line_dash="dash", line_color=COLORS["warning"], line_width=1,
                                annotation_text=f"Target", annotation_position="bottom left",
                                annotation_font_size=8, annotation_font_color=COLORS["warning"])
                            _time_stop_day = r["dte"] - r.get("time_stop_dte", 21)
                            if 0 < _time_stop_day < r["dte"]:
                                fig_decay.add_vline(x=_time_stop_day, line_dash="dashdot", line_color="#ff9800", line_width=1,
                                    annotation_text="21 DTE", annotation_position="top right",
                                    annotation_font_size=8, annotation_font_color="#ff9800")
                            if r["days_to_target"] < r["dte"]:
                                fig_decay.add_trace(go.Scatter(x=[r["days_to_target"]], y=[r["target_debit"] * 100],
                                    mode="markers", marker=dict(color=COLORS["warning"], size=8, symbol="diamond"),
                                    hoverinfo="skip", showlegend=False))
                            fig_decay.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                                plot_bgcolor="rgba(0,0,0,0)", height=200, margin=dict(l=40, r=10, t=20, b=35),
                                xaxis_title="Days Held", yaxis_title="Spread Value ($)", showlegend=False)
                            st.plotly_chart(fig_decay, use_container_width=True)

                    st.caption("**Charts:** Payoff = P&L at expiration. Green = profit zone, red = loss. "
                               "Dashed lines: yellow = profit target, red = breakevens, orange = 30Δ adjustment triggers (shaded = warning zone). "
                               "Theta decay = spread value over time (should decay toward the target close line).")

                    # ── Book It button ──
                    @st.fragment
                    def _book_it(setup):
                        _tk = setup["ticker"]
                        _booked_key = f"ic_booked_{_tk}_{setup['expiration']}"
                        if st.session_state.get(_booked_key):
                            st.success(f"{_tk} iron condor booked to Position Book.")
                        else:
                            _b1, _b2 = st.columns([1, 3])
                            with _b1:
                                if st.button("Book It", key=f"book_{_tk}_{setup['expiration']}",
                                             type="primary", use_container_width=True):
                                    from src.position_book import add_position
                                    _contracts = setup.get("size_contracts", 1) or 1
                                    _credit_per = setup["spread_estimate"]
                                    _details = {
                                        "strategy": "iron_condor",
                                        "short_put": setup["short_put"],
                                        "long_put": setup["long_put"],
                                        "short_call": setup["short_call"],
                                        "long_call": setup["long_call"],
                                        "expiration": setup["expiration"],
                                        "dte_at_entry": setup["dte"],
                                        "credit_per_share": _credit_per,
                                        "credit_per_contract": round(_credit_per * 100, 0),
                                        "max_risk_per_contract": setup["max_risk_100"],
                                        "spread_natural": setup.get("spread_natural", 0),
                                        "spread_mid": setup.get("spread_mid", 0),
                                        "pop": setup["pop"],
                                        "ivr": setup.get("ivr"),
                                        "vrp": setup.get("vrp"),
                                        "liq_grade": setup.get("liq_grade"),
                                        "adj_score": setup.get("adj_score"),
                                        "profit_target_pct": setup["target_pct"],
                                        "stop_multiplier": setup.get("stop_mult", 1.5),
                                        "managed_wr": setup.get("managed_wr"),
                                        "kelly_adj": setup.get("kelly_adj"),
                                        "net_delta": setup.get("net_delta"),
                                        "net_theta": setup.get("net_theta"),
                                        "net_vega": setup.get("net_vega"),
                                    }
                                    _pos_id = add_position(
                                        ticker=_tk,
                                        type="iron_condor",
                                        qty=_contracts,
                                        entry_price=_credit_per,
                                        details=_details,
                                        source_page="50_Iron_Condor_Scanner",
                                    )
                                    st.session_state[_booked_key] = _pos_id
                                    st.success(f"Booked {_contracts} × {_tk} iron condor (ID: {_pos_id}). "
                                               f"Credit: ${_credit_per * 100 * _contracts:,.0f}. "
                                               f"Track P&L on the Portfolio Greeks page.")
                            with _b2:
                                st.caption(f"{setup.get('size_contracts', 1) or 1} contracts · "
                                           f"${setup['spread_estimate'] * 100:,.0f}/contract · "
                                           f"Exp {_fmt_exp(setup.get('expiration', ''))}")
                    _book_it(r)

                    # ── Below-card tabs: Management / Expirations / Greeks ──
                    sub_tabs = st.tabs(["Management", "Compare Expirations", "Greeks"])

                    with sub_tabs[0]:
                        _time_stop = r.get("time_stop_dte", 21)
                        _days_until_ts = r["dte"] - _time_stop
                        m1, m2, m3 = st.columns(3)
                        m1.markdown(f"**Take Profit**\n\nClose at {r['target_pct']}% · ~day {r['days_to_target']}")
                        m2.markdown(f"**Stop Loss**\n\n{stop_multiplier}× credit · \\${r['credit_100'] * stop_multiplier:,.0f}")
                        m3.markdown(f"**Time Stop**\n\n{_time_stop} DTE{f' · ~day {_days_until_ts}' if _days_until_ts > 0 else ''}")
                        st.markdown(f"**30Δ Triggers:** Put \\${_put_trig:,.0f} / Call \\${_call_trig:,.0f} — roll untested side")

                        _hwr = r.get("hist_winrate")
                        if _hwr:
                            st.markdown("---")
                            st.markdown(f"**Historical Backtest ({_hwr['n_trials']} simulated trades)**")
                            w1, w2, w3, w4, w5 = st.columns(5)
                            w1.metric("Managed WR", f"{_hwr['win_rate']}%")
                            w2.metric("Exp-Only WR", f"{_hwr.get('exp_win_rate', '?')}%")
                            w3.metric("Early Profits", f"{_hwr.get('early_profit', 0)}")
                            w4.metric("Stopped Out", f"{_hwr.get('stopped_out', 0)}")
                            w5.metric("Breached @ Exp", f"{_hwr.get('breached_at_exp', 0)}")
                            st.caption(f"Managed = closed at {profit_target_pct}% profit or {stop_multiplier}× stop. "
                                       f"Exp-only = held to expiration with no management. "
                                       f"Avg max move: ±{_hwr['avg_max_move_pct']}%, median: ±{_hwr['median_max_move_pct']}%.")

                        # ── Forward Event Stress Test ──
                        st.markdown("---")
                        st.markdown("**Forward Event Stress Test**")

                        # Find known events within DTE window
                        _exp_date = pd.to_datetime(r["expiration"])
                        _today = pd.Timestamp.now()
                        _events = []

                        # FOMC
                        for fd in FOMC_DATES:
                            fd_dt = pd.to_datetime(fd)
                            if _today < fd_dt <= _exp_date:
                                _is_sep = fd in FOMC_SEP_DATES
                                _days_to = (fd_dt - _today).days
                                _events.append({
                                    "name": f"FOMC{' + SEP/Dot Plot' if _is_sep else ''}",
                                    "date": fd, "days": _days_to,
                                    "typical_move_pct": 1.5 if _is_sep else 1.0,
                                })

                        # Earnings — single-day gap move, not full-DTE range
                        if r.get("earnings_before_exp") and r.get("earnings_date"):
                            # Earnings 1σ move ≈ IV × √(1/252) × 100 (one trading day)
                            # This is the daily expected move at current IV level
                            _avg_iv_dec = r["avg_iv"] / 100
                            _earn_1sig = _avg_iv_dec * np.sqrt(1 / 252) * 100 if _avg_iv_dec > 0 else 3.0
                            _events.append({
                                "name": "Earnings",
                                "date": r["earnings_date"], "days": r["earnings_days"],
                                "typical_move_pct": round(_earn_1sig, 1),
                            })

                        if _events:
                            _spot = r["spot"]
                            _sp_k = r["short_put"]
                            _sc_k = r["short_call"]
                            _credit = r["credit"]
                            _lp_k = r["long_put"]
                            _lc_k = r["long_call"]

                            # Scenario table: for each event, compute P&L at 1σ, 2σ, 3σ moves
                            _stress_rows = []
                            for ev in _events:
                                _mv = ev["typical_move_pct"]
                                for sigma, label in [(1, "1σ"), (2, "2σ"), (3, "3σ")]:
                                    move_pct = _mv * sigma
                                    # Down scenario
                                    px_down = _spot * (1 - move_pct / 100)
                                    pnl_down = (_credit
                                                - max(_sp_k - px_down, 0) + max(_lp_k - px_down, 0)
                                                - max(px_down - _sc_k, 0) + max(px_down - _lc_k, 0)) * 100
                                    # Up scenario
                                    px_up = _spot * (1 + move_pct / 100)
                                    pnl_up = (_credit
                                              - max(_sp_k - px_up, 0) + max(_lp_k - px_up, 0)
                                              - max(px_up - _sc_k, 0) + max(px_up - _lc_k, 0)) * 100

                                    _survives_down = pnl_down > -_credit * 100 * stop_multiplier
                                    _survives_up = pnl_up > -_credit * 100 * stop_multiplier

                                    _stress_rows.append({
                                        "Event": ev["name"],
                                        "Date": ev["date"],
                                        "Scenario": f"{label} (±{move_pct:.1f}%)",
                                        "Down P&L": f"${pnl_down:+,.0f}",
                                        "Down": "OK" if _survives_down else "STOP",
                                        "Up P&L": f"${pnl_up:+,.0f}",
                                        "Up": "OK" if _survives_up else "STOP",
                                    })

                            st.dataframe(pd.DataFrame(_stress_rows), use_container_width=True, hide_index=True)
                            _breached = [sr for sr in _stress_rows if sr["Down"] == "STOP" or sr["Up"] == "STOP"]
                            if _breached:
                                st.warning(f"{len(_breached)} scenario(s) hit the {stop_multiplier}× stop loss. "
                                           f"Consider tighter management or skipping this setup if a {_breached[0]['Scenario'].split(' ')[0]} move is plausible.")
                            else:
                                st.success("All scenarios survive within the stop loss threshold.")
                            st.caption("Forward-looking: prices the condor's intrinsic P&L at each scenario's spot level. "
                                       "Does not model IV crush post-event (which would improve P&L). "
                                       "Typical moves: FOMC ~1%, FOMC+SEP ~1.5%, earnings = ATM straddle implied move.")
                        else:
                            st.success("No known events (FOMC, earnings) within the DTE window.")

                    with sub_tabs[1]:
                        st.caption("Compare the same condor structure across different expirations. "
                                   "$/Day normalizes credit for time — higher = faster theta collection. "
                                   "★ = the scanner's selected expiration.")
                        _alt_chain = fetch_options_chain(r["ticker"])
                        _alts = _find_alt_expirations(_alt_chain, r["spot"], target_dte_min, target_dte_max,
                            short_delta, wing_width, r["expiration"], r["target_pct"])
                        if _alts:
                            _best_cpd = r["credit_100"] / max(r["dte"], 1)
                            _comp_rows = [{"Exp": f"{r['expiration']} ★", "DTE": r["dte"],
                                "Strikes": f"{r['short_put']:.0f}P / {r['short_call']:.0f}C",
                                "Credit": f"${r['credit_100']:,.0f}", "$/Day": f"${_best_cpd:.1f}",
                                "Risk": f"${r['max_risk_100']:,.0f}", "POP": f"{r['pop']}%"}]
                            for a in _alts:
                                _comp_rows.append({"Exp": a["exp"], "DTE": a["dte"],
                                    "Strikes": f"{a['short_put']:.0f}P / {a['short_call']:.0f}C",
                                    "Credit": f"${a['credit']:,.0f}", "$/Day": f"${a['credit_per_day']:.1f}",
                                    "Risk": f"${a['max_risk']:,.0f}", "POP": f"{a['pop']}%"})
                            st.dataframe(pd.DataFrame(_comp_rows), use_container_width=True, hide_index=True)
                        else:
                            st.caption("No alternative expirations in DTE range.")

                    with sub_tabs[2]:
                        st.caption("Net position Greeks (per contract). "
                                   "**Δ:** directional exposure (target ~0). "
                                   "**Γ:** rate of delta change (negative = risk accelerates toward strikes). "
                                   "**Θ:** daily time decay collected. "
                                   "**ν:** P&L per 1% IV change (negative = profits from vol contraction). "
                                   "**Θ/ν:** theta earned per unit of vol risk — higher = better compensated. "
                                   "DGTV limits per institutional risk management guidelines.")
                        _nd, _ng, _nt, _nv = r["net_delta"], r["net_gamma"], r["net_theta"], r["net_vega"]
                        _tvr = r.get("theta_vega_ratio", 0)
                        g1, g2, g3, g4, g5 = st.columns(5)
                        g1.metric("Δ Delta", f"{_nd * 100:+.1f}")
                        g2.metric("Γ Gamma", f"{_ng * 100:+.2f}")
                        g3.metric("Θ Theta", f"${_nt * 100:+.1f}/day")
                        g4.metric("ν Vega", f"${_nv * 100:+.1f}/1%")
                        g5.metric("Θ/ν Ratio", f"{_tvr:.2f}")
                        _LIMITS = {"delta": 0.30, "gamma": 0.03, "vega": 0.20}
                        _breaches = []
                        if abs(_nd * 100) > _LIMITS["delta"]:
                            _breaches.append(f"Delta |{abs(_nd * 100):.2f}| > ±{_LIMITS['delta']}")
                        if abs(_ng * 100) > _LIMITS["gamma"]:
                            _breaches.append(f"Gamma |{abs(_ng * 100):.3f}| > ±{_LIMITS['gamma']}")
                        if abs(_nv * 100) > _LIMITS["vega"]:
                            _breaches.append(f"Vega |{abs(_nv * 100):.2f}| > ±{_LIMITS['vega']}")
                        if _breaches:
                            st.warning(f"DGTV breach: {'; '.join(_breaches)}")
                        if _contracts > 0:
                            st.caption(f"{_contracts} contracts · \\${r.get('size_total_credit', 0):,.0f} credit · "
                                       f"\\${r.get('size_total_risk', 0):,.0f} risk · "
                                       f"\\${r.get('size_margin', 0):,.0f} margin")

                        # Per-leg pricing table
                        _legs = r.get("legs")
                        if _legs:
                            st.markdown("---")
                            st.markdown(f'<div style="font-size:0.72rem;font-weight:600;margin-bottom:4px;">Leg Pricing</div>', unsafe_allow_html=True)
                            _leg_rows = []
                            for leg in _legs:
                                _ba = leg["ask"] - leg["bid"] if leg["ask"] > 0 and leg["bid"] > 0 else None
                                _quote_status = "Live" if leg.get("live", True) else "Synthetic"
                                _leg_rows.append({
                                    "Leg": leg["label"],
                                    "Bid": f"${leg['bid']:.2f}",
                                    "Ask": f"${leg['ask']:.2f}",
                                    "Mid": f"${leg['mid']:.2f}",
                                    "B/A": f"${_ba:.2f}" if _ba is not None else "—",
                                    "OI": f"{leg['oi']:,}",
                                    "Vol": f"{leg['vol']:,}",
                                    "Quote": _quote_status,
                                })
                            st.dataframe(pd.DataFrame(_leg_rows), use_container_width=True, hide_index=True)
                            _synth_count = sum(1 for leg in _legs if not leg.get("live", True))
                            if _synth_count > 0:
                                st.caption(f"⚠ {_synth_count} leg(s) have synthetic quotes (estimated from daily close, not live bid/ask). "
                                           f"Actual fill price may differ — verify in your broker before trading.")

    # ── AI Assessment ──
    st.markdown("##### AI Assessment")
    st.caption("Grok 4 analyzes each setup with live X/Twitter search — validates the quantitative score against "
               "real-time sentiment, upcoming catalysts, sector rotation, and macro conditions. "
               "Provides per-setup grades (A-F), a portfolio recommendation (best 3-5 diversified picks), "
               "and correlation warnings for concentrated sector exposure.")
    with st.container(border=True):
        with error_boundary("AI Assessment"):
            grok_key = get_secret("GROK_API_KEY")
            if not grok_key:
                try:
                    grok_key = st.secrets.get("GROK_API_KEY")
                except Exception:
                    pass

            if not grok_key:
                st.info("Grok API key not configured. Add GROK_API_KEY to secrets.")
            else:
                top_n = min(len(top_results), len(results))
                _ai_placeholder = st.empty()
                if "ic_ai_result" not in st.session_state:
                    run_ai = _ai_placeholder.button(f"Run Grok Assessment on {top_n} Setups", type="primary",
                                                     use_container_width=True, key="ic_run_ai")
                else:
                    run_ai = _ai_placeholder.button(f"Re-run Grok Assessment on {top_n} Setups",
                                                     use_container_width=True, key="ic_run_ai")

                if run_ai:
                    _ai_placeholder.empty()  # hide button while loading
                    from openai import OpenAI as OAI
                    import json as _json
                    import re as _re

                    client = OAI(base_url="https://api.x.ai/v1", api_key=grok_key)

                    # Build comprehensive summary of all displayed setups
                    setup_lines = []
                    for i, r in enumerate(results[:top_n], 1):
                        _ivr = r.get("ivr")
                        _vrp = r.get("vrp")
                        _hv20 = r.get("hv20")

                        # Earnings context
                        _earn_line = ""
                        if r.get("earnings_before_exp"):
                            _em = r.get("expected_move_pct")
                            _em_part = f", expected move ±{_em:.1f}%" if _em else ""
                            _inside = " — STRIKES INSIDE EXPECTED MOVE" if r.get("strikes_inside_em") else ""
                            _earn_line = f"\n   ⚠ EARNINGS: {r['earnings_date']} ({r['earnings_days']}d){_em_part}{_inside}"
                        elif r.get("earnings_days"):
                            _earn_line = f"\n   Earnings: {r['earnings_date']} ({r['earnings_days']}d) — after expiration"

                        # Historical backtest
                        _hwr = r.get("hist_winrate")
                        _hist_line = ""
                        if _hwr:
                            _hist_line = (f"\n   Backtest ({_hwr['n_trials']} trials): managed WR {_hwr['win_rate']}%, "
                                          f"exp-only WR {_hwr.get('exp_win_rate', '?')}%, "
                                          f"{_hwr.get('early_profit', 0)} early profits, "
                                          f"{_hwr.get('stopped_out', 0)} stopped, "
                                          f"avg max move ±{_hwr['avg_max_move_pct']}%")

                        # Kelly / sizing
                        _kelly_line = ""
                        _kf = r.get("kelly_full", 0)
                        _contr = r.get("size_contracts", 0)
                        if _contr > 0:
                            _kelly_line = f"\n   Kelly: {_kf:.1f}% → {_contr} contracts, ${r.get('size_total_risk', 0):,.0f} total risk"

                        setup_lines.append(
                            f"{i}. **{r['ticker']}** — ${r['spot']:,.2f} | Liq: {r.get('liq_grade', '?')} | Score: {r['adj_score']:.3f}\n"
                            f"   Sell {r['short_put']:.0f}P / Buy {r['long_put']:.0f}P — "
                            f"Sell {r['short_call']:.0f}C / Buy {r['long_call']:.0f}C\n"
                            f"   Exp: {r['expiration']} ({r['dte']}d) | "
                            f"Open for: ${r['spread_estimate'] * 100:,.0f} cr (net after slippage: ${r.get('net_credit_100', r['credit_100']):,.0f}) | "
                            f"Max risk: ${r['max_risk_100']:,.0f} | POP: {r['pop']}%\n"
                            f"   IV: {r['avg_iv']}% | HV20: {f'{_hv20:.1f}%' if _hv20 else 'N/A'} | "
                            f"IVR: {f'{_ivr:.0f}' if _ivr is not None else 'N/A'} ({r.get('ivr_band', 'N/A')}) | "
                            f"VRP: {f'{_vrp:+.1f}' if _vrp is not None else 'N/A'}\n"
                            f"   BEs: ${r['lower_be']:.0f} (-{r['lower_be_pct']}%) / ${r['upper_be']:.0f} (+{r['upper_be_pct']}%) | "
                            f"30Δ triggers: ${r.get('put_30d_trigger', 0):,.0f}P / ${r.get('call_30d_trigger', 0):,.0f}C\n"
                            f"   {r['target_pct']}% target: ${r['profit_at_target_100']:,.0f} in ~{r['days_to_target']}d | "
                            f"Θ=${r['net_theta'] * 100:+.1f}/day | ν=${r['net_vega'] * 100:+.1f}/1% | Θ/ν={r.get('theta_vega_ratio', 0):.2f}"
                            f"{_earn_line}{_hist_line}{_kelly_line}"
                        )
                    setups_text = "\n\n".join(setup_lines)

                    # Portfolio-level context
                    _port_contracts = sum(r.get("size_contracts", 0) for r in results[:top_n])
                    _port_risk = sum(r.get("size_total_risk", 0) for r in results[:top_n])
                    _port_credit = sum(r.get("size_total_credit", 0) for r in results[:top_n])
                    _port_earn = sum(1 for r in results[:top_n] if r.get("earnings_before_exp"))

                    prompt = f"""You are analyzing {top_n} short iron condor setups ranked by our quantitative scanner.
Your job: determine which of these are actually tradeable RIGHT NOW and which should be skipped.

SETUPS (ranked by composite score — integrates credit/risk, IVR band, VRP, liquidity, earnings risk, historical backtest, theta efficiency):

{setups_text}

PORTFOLIO CONTEXT (if all were traded):
- Total contracts: {_port_contracts}, Total credit: ${_port_credit:,.0f}, Total risk: ${_port_risk:,.0f}
- Tickers with earnings risk: {_port_earn}
- Account size: ${account_size:,}

SCANNER PARAMETERS:
- DTE range: {target_dte_min}-{target_dte_max}d | Short delta: {short_delta} | Wing width: ${wing_width}
- Profit target: {profit_target_pct}% | Stop: {stop_multiplier}× credit

QUANTITATIVE FRAMEWORK:
- IVR 50-75 = optimal zone (78% hist WR on SPX). IVR >75 = jump-diffusion risk. IVR <30 = insufficient VRP.
- Positive VRP (IV > HV20) = structural edge. Negative VRP = selling cheap vol.
- Managed backtest uses {profit_target_pct}% profit target + {stop_multiplier}× stop to simulate realistic exits.
- Score penalizes: illiquid setups (slippage-adjusted credit), earnings, low historical WR, poor IVR band.

TASKS:
1. Search X/Twitter for EACH ticker — upcoming catalysts, sector news, macro headwinds, anything affecting range-bound probability.
2. For EACH setup, assess: is the quantitative score justified by current market conditions? Does live sentiment confirm or contradict?
3. Flag any CORRELATION RISK — if multiple setups are in the same sector or highly correlated, note the portfolio concentration.
4. Provide a final PORTFOLIO RECOMMENDATION: which 3-5 setups to actually trade, considering diversification and total risk.

Respond with JSON:
{{"setups": [
    {{"ticker": "TICKER",
      "grade": "A/B/C/D/F",
      "thesis": "2-3 sentences: why this is or isn't a good condor right now. Reference IVR, VRP, backtest WR, and live sentiment.",
      "key_risk": "The single biggest threat — be specific (e.g., 'FOMC on May 7 within DTE window' not just 'macro risk')",
      "iv_view": "Rich/fair/cheap relative to HV20 and historical range. Reference IVR band.",
      "events": "Specific dated catalysts within the DTE window from X/Twitter search",
      "verdict": "SELL / WAIT / SKIP"}}
  ],
  "correlation_warning": "Which tickers are correlated and shouldn't all be traded simultaneously",
  "portfolio_recommendation": "Top 3-5 picks for a diversified condor portfolio with reasoning",
  "overall": "2-3 sentences on the macro environment for selling premium right now — VIX regime, Fed, geopolitical",
  "best_pick": "TICKER — one sentence why this is the absolute #1 setup right now"}}"""

                    with st.spinner(f"Grok is analyzing {top_n} setups..."):
                        try:
                            response = client.chat.completions.create(
                                model="grok-3",
                                messages=[
                                    {"role": "system", "content": (
                                        "You are an institutional options strategist and portfolio manager "
                                        "specializing in short premium, iron condor strategies, and "
                                        "volatility risk premium capture. You manage a multi-strategy "
                                        "options book. Be specific, actionable, and quantitative. "
                                        "Reference actual market conditions from X/Twitter. "
                                        "Grade harshly — an A means exceptional setup with rich IV, "
                                        "no catalysts, confirmed range-bound sentiment, and A/B liquidity. "
                                        "Consider portfolio-level risk, not just individual trades."
                                    )},
                                    {"role": "user", "content": prompt},
                                ],
                                response_format={"type": "json_object"},
                                max_tokens=5000,
                                temperature=0.3,
                            )
                            raw = response.choices[0].message.content
                            cleaned = _re.sub(r"^```json?\s*", "", raw.strip())
                            cleaned = _re.sub(r"\s*```$", "", cleaned)
                            ai_result = _json.loads(cleaned)
                            st.session_state["ic_ai_result"] = ai_result
                        except Exception as e:
                            st.error(f"Grok analysis failed: {e}")

                if "ic_ai_result" in st.session_state:
                    ai_result = st.session_state["ic_ai_result"]

                    # Best pick + portfolio recommendation
                    best = ai_result.get("best_pick", "")
                    if best:
                        st.success(f"**Top Pick:** {best}")

                    port_rec = ai_result.get("portfolio_recommendation", "")
                    if port_rec:
                        st.info(f"**Portfolio Recommendation:** {port_rec}")

                    corr_warn = ai_result.get("correlation_warning", "")
                    if corr_warn:
                        st.warning(f"**Correlation Risk:** {corr_warn}")

                    overall = ai_result.get("overall", "")
                    if overall:
                        st.markdown(f"**Macro Environment:** {overall}")

                    # Per-setup assessments
                    for s in ai_result.get("setups", []):
                        ticker_name = _html.escape(str(s.get("ticker", "?")))
                        grade = s.get("grade", "?")
                        verdict = s.get("verdict", "?")
                        grade_colors = {
                            "A": COLORS["success"], "B": "#66bb6a",
                            "C": COLORS["warning"], "D": "#ff8a65",
                            "F": COLORS["danger"],
                        }
                        verdict_colors = {"SELL": COLORS["success"], "WAIT": COLORS["warning"], "SKIP": COLORS["danger"]}
                        gc = grade_colors.get(grade, COLORS["text_muted"])
                        vc = verdict_colors.get(verdict, COLORS["text_muted"])

                        with st.expander(f"{ticker_name} — Grade: {grade} | {verdict}"):
                            st.markdown(
                                f'<div style="display:flex;gap:20px;margin-bottom:12px;">'
                                f'<div style="text-align:center;padding:8px 16px;border:1px solid {gc};border-radius:8px;">'
                                f'<span style="font-size:28px;font-weight:bold;color:{gc};">{_html.escape(str(grade))}</span><br>'
                                f'<span style="font-size:0.7rem;color:{COLORS["text_muted"]};">Grade</span></div>'
                                f'<div style="text-align:center;padding:8px 16px;border:1px solid {vc};border-radius:8px;">'
                                f'<span style="font-size:28px;font-weight:bold;color:{vc};">{_html.escape(str(verdict))}</span><br>'
                                f'<span style="font-size:0.7rem;color:{COLORS["text_muted"]};">Verdict</span></div>'
                                f'</div>',
                                unsafe_allow_html=True,
                            )
                            thesis = s.get("thesis", "")
                            if thesis:
                                st.markdown(f"**Thesis:** {thesis}")
                            key_risk = s.get("key_risk", "")
                            if key_risk:
                                st.warning(f"**Key Risk:** {key_risk}")
                            iv_view = s.get("iv_view", "")
                            if iv_view:
                                st.markdown(f"**IV View:** {iv_view}")
                            events = s.get("events", "")
                            if events:
                                st.info(f"**Events:** {events}")

                    if not run_ai:
                        st.caption("Cached result — click button above to refresh.")

    # ── Full Results Table (collapsed) ──
    if results:
        with st.expander(f"Full Results Table ({len(results)} setups)", expanded=False):
            table_rows = []
            for r in results:
                _ivr = r.get("ivr")
                _vrp = r.get("vrp")
                _earn = r.get("earnings_before_exp")
                _earn_days = r.get("earnings_days")
                if _earn:
                    _earn_str = f"⚠ {_earn_days}d"
                elif _earn_days is not None:
                    _earn_str = f"{_earn_days}d"
                else:
                    _earn_str = "—"
                try:
                    _exp_dt = pd.to_datetime(r["expiration"]).strftime("%b %d")
                except Exception:
                    _exp_dt = r.get("expiration", "?")
                table_rows.append({
                    "Ticker": r["ticker"],
                    "Liq": r.get("liq_grade", "?"),
                    "IVR": f"{_ivr:.0f}" if _ivr is not None else "—",
                    "VRP": f"{_vrp:+.1f}" if _vrp is not None else "—",
                    "Earn": _earn_str,
                    "Strikes": f"{r['short_put']:.0f}P / {r['short_call']:.0f}C",
                    "Exp": f"{_exp_dt} ({r['dte']}d)",
                    "Credit": f"${r['spread_estimate'] * 100:,.0f}",
                    "Risk": f"${r['max_risk_100']:,.0f}",
                    "POP": f"{r['pop']}%",
                    "Target": f"${r['profit_at_target_100']:,.0f}",
                    "Hist WR": f"{r['hist_winrate']['win_rate']}%" if r.get("hist_winrate") else "—",
                    "Contracts": r.get("size_contracts", 0),
                    "Score": f"{r['adj_score']:.3f}",
                })
            st.dataframe(pd.DataFrame(table_rows), use_container_width=True, hide_index=True)
            st.caption("**Liq:** A=excellent, B=good, C=moderate, D/F=poor. "
                       "**IVR:** IV percentile vs historical (50-75 optimal). "
                       "**VRP:** IV minus HV20 (positive = edge). "
                       "**Hist WR:** managed backtest win rate. "
                       "**Score:** composite of all 7 factors.")

elif not scan:
    st.info("Enter tickers and click **Scan for Iron Condors** to find the best setups.")
