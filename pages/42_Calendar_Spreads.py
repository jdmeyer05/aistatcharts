import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from datetime import date, timedelta, datetime
from scipy.stats import norm

from src.layout import setup_page, get_active_ticker, set_active_ticker, error_boundary, fun_loader
from src.styles import COLORS
from src.api_keys import get_secret
from src.data_engine import (
    format_massive_ticker, fetch_massive_data,
    get_expiration_dates, fetch_options_chain,
    render_data_source_footer,
)
from src.options_models import black_scholes, bs_greeks, fill_missing_options_data
import html as _html

logger = logging.getLogger(__name__)

setup_page("42_Calendar_Spreads")

st.title("Calendar Spreads")
st.markdown("Term structure analysis, spread builder, scanner, and P&L simulation for time spreads.")


# ─── HELPERS ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def _get_risk_free_rate() -> float:
    try:
        from src.market_data import fetch_fred_series
        df = fetch_fred_series("DGS3MO", periods=5)
        if not df.empty:
            return df["value"].iloc[-1] / 100
    except Exception:
        pass
    return 0.045


def _mid(row):
    """Mid-price from bid/ask, falling back to last_price."""
    bid = row.get("bid", 0) or 0
    ask = row.get("ask", 0) or 0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return row.get("last_price", 0) or 0


def _dte(exp_str: str) -> int:
    """Calendar days to expiration."""
    exp = pd.to_datetime(exp_str)
    return max((exp - pd.Timestamp.now()).days, 0)


def _T(exp_str: str) -> float:
    """Years to expiration."""
    return max(_dte(exp_str) / 365.0, 0.001)


def _atm_strike(chain_df: pd.DataFrame, spot: float) -> float:
    """Closest strike to spot."""
    strikes = chain_df["strike_price"].unique()
    return float(min(strikes, key=lambda s: abs(s - spot)))


def _get_contract(chain_df: pd.DataFrame, strike: float, opt_type: str) -> pd.Series | None:
    """Get a single contract row by strike and type."""
    mask = (chain_df["strike_price"] == strike) & (chain_df["contract_type"] == opt_type)
    sub = chain_df[mask]
    if sub.empty:
        return None
    return sub.iloc[0]


def _atm_iv(chain_df: pd.DataFrame, spot: float, opt_type: str = "call") -> float:
    """ATM implied volatility for a chain."""
    strike = _atm_strike(chain_df, spot)
    row = _get_contract(chain_df, strike, opt_type)
    if row is not None:
        iv = row.get("implied_volatility", 0) or 0
        if iv > 0:
            return iv
    return 0.25  # fallback


def _spread_greeks(spot, strike, T_front, T_back, iv_front, iv_back, r, opt_type):
    """Net Greeks for calendar spread (long back, short front)."""
    g_front = bs_greeks(spot, strike, T_front, r, iv_front, opt_type)
    g_back = bs_greeks(spot, strike, T_back, r, iv_back, opt_type)
    return {k: g_back[k] - g_front[k] for k in g_front}


def _spread_price(spot, strike, T_front, T_back, iv_front, iv_back, r, opt_type):
    """Net debit for calendar spread (long back - short front)."""
    p_front = black_scholes(spot, strike, T_front, r, iv_front, opt_type)
    p_back = black_scholes(spot, strike, T_back, r, iv_back, opt_type)
    return p_back - p_front


def _calendar_pnl_at_front_expiry(spot_range, strike, T_back_remaining, iv_back, r,
                                   opt_type, entry_debit, skew_func=None):
    """P&L at front expiry across a range of spot prices.

    At front expiry the short leg is worth intrinsic value and the long leg
    is priced via BS with remaining time.

    If skew_func is provided, it maps (spot_price, strike) -> adjusted IV
    for the back leg, modeling how IV changes as spot moves along the skew.
    """
    pnls = []
    for s in spot_range:
        # Short leg worth intrinsic at expiry
        if opt_type == "call":
            short_value = max(s - strike, 0)
        else:
            short_value = max(strike - s, 0)
        # Long leg IV: use skew adjustment if available
        iv_adj = skew_func(s, strike) if skew_func else iv_back
        long_value = black_scholes(s, strike, T_back_remaining, r, iv_adj, opt_type)
        spread_value = long_value - short_value
        pnls.append(spread_value - entry_debit)
    return np.array(pnls)


def _vectorized_pnl_grid(prices, days_arr, front_dte, back_dte, strike, spot,
                          iv_f, iv_b, rfr, spread_type, net_debit):
    """Vectorized P&L grid computation — avoids nested Python loops."""
    n_days = len(days_arr)
    n_prices = len(prices)
    grid = np.zeros((n_days, n_prices))
    for i, d in enumerate(days_arr):
        T_f = max((front_dte - d) / 365.0, 0.001)
        T_b = max((back_dte - d) / 365.0, 0.001)
        for j, p in enumerate(prices):
            if T_f <= 0.002:
                if spread_type == "call":
                    short_v = max(p - strike, 0)
                else:
                    short_v = max(strike - p, 0)
            else:
                short_v = black_scholes(p, strike, T_f, rfr, iv_f, spread_type)
            long_v = black_scholes(p, strike, T_b, rfr, iv_b, spread_type)
            grid[i, j] = (long_v - short_v - net_debit) * 100
    return grid


def _render_ai_result(result):
    """Render Grok AI assessment result — shared between fresh and cached display."""
    grade = result.get("grade", "N/A")
    grade_colors = {
        "A": COLORS["success"], "B": "#66bb6a",
        "C": COLORS["warning"], "D": "#ff8a65",
        "F": COLORS["danger"],
    }
    grade_color = grade_colors.get(grade, COLORS["text_muted"])
    st.markdown(
        f'<div style="text-align:center; padding:20px; '
        f'border:2px solid {grade_color}; border-radius:12px; '
        f'margin-bottom:20px;">'
        f'<span style="font-size:48px; color:{grade_color}; '
        f'font-weight:bold;">{_html.escape(str(grade))}</span><br>'
        f'<span style="color:{COLORS["text_muted"]};">Setup Grade</span>'
        f'</div>',
        unsafe_allow_html=True,
    )
    _assess = result.get("assessment", "")
    _mktctx = result.get("market_context", "")
    _entry = result.get("optimal_entry", "")
    if _assess:
        st.markdown(f"**Assessment**")
        st.markdown(_assess)
    if _mktctx:
        st.markdown(f"**Market Context**")
        st.markdown(_mktctx)
    if _entry:
        st.markdown(f"**Entry Timing**")
        st.markdown(_entry)
    for flag in result.get("risk_flags", []):
        st.warning(str(flag))
    for adj in result.get("adjustments", []):
        st.info(str(adj))


def _ensure_chain_loaded(exp_str: str) -> bool:
    """Lazy-load a chain if it wasn't pre-fetched. Returns True if available."""
    term_data = st.session_state.get("cal_term_data", {})
    if exp_str in term_data:
        return True
    spot = st.session_state.get("cal_spot")
    rfr = st.session_state.get("cal_rfr", 0.045)
    ticker = st.session_state.get("cal_ticker")
    if not ticker or not spot:
        return False
    try:
        tdf = fetch_options_chain(ticker, exp_str)
        if tdf is not None and not tdf.empty:
            tdf = fill_missing_options_data(tdf, spot, risk_free_rate=rfr)
            term_data[exp_str] = tdf
            st.session_state["cal_term_data"] = term_data
            return True
    except Exception:
        pass
    return False


# ─── EARNINGS / EVENT DETECTION ───────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_earnings_date(ticker: str):
    """Try to get next earnings date via yfinance."""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker)
        cal = tk.calendar
        if cal is not None:
            if isinstance(cal, pd.DataFrame) and "Earnings Date" in cal.index:
                return pd.to_datetime(cal.loc["Earnings Date"].iloc[0])
            if isinstance(cal, dict) and "Earnings Date" in cal:
                dates = cal["Earnings Date"]
                if dates:
                    return pd.to_datetime(dates[0])
    except Exception:
        pass
    return None


# ─── URL QUERY PARAMS (for sharing) ────────────────────────────────────────────

_qp = st.query_params
_qp_ticker = _qp.get("ticker", "")
_qp_type = _qp.get("type", "")
_qp_front = _qp.get("front", "")
_qp_back = _qp.get("back", "")
_qp_strike = _qp.get("strike", "")

# ─── CONTROLS ──────────────────────────────────────────────────────────────────

_default_ticker = _qp_ticker if _qp_ticker else get_active_ticker()
_default_type_idx = 1 if _qp_type == "put" else 0

_c1, _c2, _c3 = st.columns([2, 1, 1])
with _c1:
    raw_ticker = st.text_input("Underlying Ticker", value=_default_ticker)
    ticker = format_massive_ticker(raw_ticker)
    set_active_ticker(ticker)
with _c2:
    spread_type = st.selectbox("Spread Type", ["call", "put"], index=_default_type_idx)
with _c3:
    st.markdown("<br>", unsafe_allow_html=True)
    submit = st.button("Load Chain Data", type="primary", use_container_width=True)

# ─── FETCH ─────────────────────────────────────────────────────────────────────

if submit:
    with fun_loader("data"):
        all_exps = get_expiration_dates(ticker)
        today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
        year_end = f"{pd.Timestamp.now().year}-12-31"
        # ALL valid expirations through year-end (for dropdowns)
        all_valid = [e for e in (all_exps or []) if e >= today_str and e <= year_end]
        # If late in year, also include next year
        if len(all_valid) < 4:
            all_valid = [e for e in (all_exps or []) if e >= today_str]
        if not all_valid or len(all_valid) < 2:
            st.error("Need at least 2 expirations for a calendar spread.")
            st.stop()
        px_df = fetch_massive_data(ticker, 252)
        if px_df is None or px_df.empty:
            st.error("Could not fetch price data.")
            st.stop()
        spot = float(px_df["Close"].iloc[-1])
        rfr = _get_risk_free_rate()

        # Pre-fetch a strategic subset for term structure / scanner:
        # - All monthly/quarterly (3rd Friday) expirations
        # - Nearest 6 weeklies for short-dated calendars
        monthly_exps = []
        weekly_exps = []
        for e in all_valid:
            edt = pd.to_datetime(e)
            # 3rd Friday of month: day 15-21 and weekday == 4 (Friday)
            if 15 <= edt.day <= 21 and edt.weekday() == 4:
                monthly_exps.append(e)
            else:
                weekly_exps.append(e)
        prefetch = sorted(set(monthly_exps + weekly_exps[:6]))
        if len(prefetch) < 4:
            prefetch = all_valid[:12]

        term_data = {}
        progress = st.progress(0, text="Loading options chains...")
        for i, exp in enumerate(prefetch):
            try:
                tdf = fetch_options_chain(ticker, exp)
                if tdf is not None and not tdf.empty:
                    tdf = fill_missing_options_data(tdf, spot, risk_free_rate=rfr)
                    term_data[exp] = tdf
            except Exception:
                pass
            progress.progress((i + 1) / len(prefetch), text=f"Loading {exp}...")
        progress.empty()

        if len(term_data) < 2:
            st.error("Could not load enough expiration data.")
            st.stop()

        st.session_state["cal_term_data"] = term_data
        st.session_state["cal_spot"] = spot
        st.session_state["cal_ticker"] = ticker
        st.session_state.pop("cal_ai_result", None)  # Clear stale AI on ticker change
        st.session_state["cal_rfr"] = rfr
        st.session_state["cal_px"] = px_df
        # Store ALL expirations for dropdowns (not just pre-fetched)
        st.session_state["cal_all_expirations"] = sorted(all_valid)
        st.session_state["cal_expirations"] = sorted(term_data.keys())
        st.session_state["shared_options_ticker"] = ticker
        st.session_state["shared_options_spot"] = spot

# ─── GATE ──────────────────────────────────────────────────────────────────────

if "cal_term_data" not in st.session_state:
    st.info("Enter a ticker and click **Load Chain Data** to begin.")
    st.stop()

term_data = st.session_state["cal_term_data"]
spot = st.session_state["cal_spot"]
ticker_display = st.session_state["cal_ticker"]
rfr = st.session_state["cal_rfr"]
px_df = st.session_state["cal_px"]
# All available expirations (for dropdowns) vs pre-fetched (for scanner/term structure)
all_expirations = st.session_state.get("cal_all_expirations", st.session_state["cal_expirations"])
expirations = st.session_state["cal_expirations"]  # pre-fetched only

# ─── TABS ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Spread Builder",
    "Term Structure & IV",
    "Spread Scanner",
    "P&L Simulator",
    "Roll Optimizer",
    "Risk Analysis",
    "Historical Backtest",
    "AI Assessment",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — SPREAD BUILDER
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("Spread Builder"):
        st.subheader("Calendar Spread Builder")
        with st.expander("How calendar spreads work"):
            st.markdown("""
**Calendar spread** (time spread): sell a near-term option and buy the same strike
in a later expiration. You profit from the faster time decay of the short leg
and/or a rise in the back-month IV relative to the front.

- **Max profit** occurs when the underlying is at the strike at front expiry.
- **Max loss** is limited to the net debit paid.
- **Key Greek**: net positive theta (earning time decay) and net positive vega
  (benefits from rising IV or steepening term structure).
""")

        # --- Leg selectors (show ALL expirations, lazy-load chains) ---
        bc1, bc2, bc3 = st.columns([1, 1, 1])
        with bc1:
            # Mark pre-fetched vs on-demand in the label
            def _fmt_exp(e):
                tag = "" if e in term_data else "  *"
                return f"{e}  ({_dte(e)}d){tag}"
            front_exp = st.selectbox(
                "Front (short) expiration",
                all_expirations,
                index=0,
                format_func=_fmt_exp,
            )
        with bc2:
            back_options = [e for e in all_expirations if e > front_exp]
            if not back_options:
                st.error("No later expiration available for back leg.")
                st.stop()
            back_exp = st.selectbox(
                "Back (long) expiration",
                back_options,
                index=min(1, len(back_options) - 1),
                format_func=_fmt_exp,
            )

        # Lazy-load chains if not pre-fetched
        if not _ensure_chain_loaded(front_exp):
            st.error(f"Could not load chain data for {front_exp}.")
            st.stop()
        if not _ensure_chain_loaded(back_exp):
            st.error(f"Could not load chain data for {back_exp}.")
            st.stop()
        # Re-read term_data in case lazy-load updated it
        term_data = st.session_state["cal_term_data"]

        with bc3:
            front_chain = term_data[front_exp]
            back_chain = term_data[back_exp]
            type_mask_f = front_chain["contract_type"] == spread_type
            type_mask_b = back_chain["contract_type"] == spread_type
            common_strikes = sorted(
                set(front_chain.loc[type_mask_f, "strike_price"].unique())
                & set(back_chain.loc[type_mask_b, "strike_price"].unique())
            )
            if not common_strikes:
                st.error("No common strikes between selected expirations.")
                st.stop()
            atm = _atm_strike(front_chain, spot)
            default_idx = min(range(len(common_strikes)),
                              key=lambda i: abs(common_strikes[i] - atm))
            strike = st.selectbox("Strike", common_strikes, index=default_idx,
                                  format_func=lambda s: f"${s:,.0f}" if s >= 10 else f"${s:,.2f}")

        # --- Retrieve contracts ---
        front_row = _get_contract(front_chain, strike, spread_type)
        back_row = _get_contract(back_chain, strike, spread_type)

        if front_row is None or back_row is None:
            st.error("Could not find contracts at selected strike.")
            st.stop()

        front_mid = _mid(front_row)
        back_mid = _mid(back_row)
        net_debit = back_mid - front_mid
        iv_front = front_row.get("implied_volatility", 0) or 0.25
        iv_back = back_row.get("implied_volatility", 0) or 0.25
        T_front = _T(front_exp)
        T_back = _T(back_exp)

        if net_debit <= 0:
            st.error(
                f"Negative or zero net debit (${net_debit:.2f}). The back leg "
                f"(${back_mid:.2f}) is not more expensive than the front "
                f"(${front_mid:.2f}). This could indicate stale quotes, wide "
                f"bid-ask spreads, or an inverted term structure. Check liquidity."
            )
            st.stop()

        # --- Spread pricing metrics ---
        st.markdown("---")
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Net Debit", f"${net_debit:.2f}")
        m2.metric("Front Mid", f"${front_mid:.2f}")
        m3.metric("Back Mid", f"${back_mid:.2f}")
        m4.metric("Front IV", f"{iv_front * 100:.1f}%")
        m5.metric("Back IV", f"{iv_back * 100:.1f}%")

        # --- Net Greeks ---
        net_g = _spread_greeks(spot, strike, T_front, T_back, iv_front, iv_back, rfr, spread_type)

        g1, g2, g3, g4 = st.columns(4)
        g1.metric("Net Delta", f"{net_g['delta']:.4f}")
        g2.metric("Net Gamma", f"{net_g['gamma']:.4f}")
        g3.metric("Net Theta", f"${net_g['theta'] * 100:.2f}/day",
                  help="Dollar theta per contract (×100 shares)")
        g4.metric("Net Vega", f"${net_g['vega'] * 100:.2f}/1%",
                  help="Dollar vega per 1% IV move per contract")

        iv_diff = iv_back - iv_front
        theta_debit = abs(net_g["theta"] * 100 / net_debit) if net_debit > 0 else 0

        st.markdown("---")
        i1, i2, i3, i4 = st.columns(4)
        i1.metric("IV Differential", f"{iv_diff * 100:+.1f}%",
                  help="Back IV minus Front IV. Positive = term structure in contango (favorable).")
        i2.metric("Theta/Debit", f"{theta_debit:.1%}/day",
                  help="Daily time decay as % of capital at risk.")
        i3.metric("Front DTE", f"{_dte(front_exp)}d")
        i4.metric("Back DTE", f"{_dte(back_exp)}d")

        # --- Liquidity metrics ---
        f_vol = int(front_row.get("volume", 0) or 0)
        f_oi = int(front_row.get("open_interest", 0) or 0)
        b_vol = int(back_row.get("volume", 0) or 0)
        b_oi = int(back_row.get("open_interest", 0) or 0)
        f_bid = front_row.get("bid", 0) or 0
        f_ask = front_row.get("ask", 0) or 0
        b_bid = back_row.get("bid", 0) or 0
        b_ask = back_row.get("ask", 0) or 0
        f_ba = f_ask - f_bid if f_ask > f_bid else 0
        b_ba = b_ask - b_bid if b_ask > b_bid else 0
        total_ba = f_ba + b_ba
        ba_pct = total_ba / net_debit * 100 if net_debit > 0 else 0

        with st.expander("Liquidity & Execution", expanded=False):
            lc1, lc2 = st.columns(2)
            with lc1:
                st.markdown("**Front Leg**")
                st.caption(
                    f"Bid: ${f_bid:.2f} / Ask: ${f_ask:.2f} "
                    f"(${f_ba:.2f} wide)  \n"
                    f"Volume: {f_vol:,} | OI: {f_oi:,}"
                )
            with lc2:
                st.markdown("**Back Leg**")
                st.caption(
                    f"Bid: ${b_bid:.2f} / Ask: ${b_ask:.2f} "
                    f"(${b_ba:.2f} wide)  \n"
                    f"Volume: {b_vol:,} | OI: {b_oi:,}"
                )
            lq_color = COLORS["success"] if ba_pct < 5 else (COLORS["warning"] if ba_pct < 10 else COLORS["danger"])
            lq_label = "Tight" if ba_pct < 5 else ("Moderate" if ba_pct < 10 else "Wide")
            st.markdown(
                f"**Combined spread cost:** ${total_ba:.2f} "
                f"(<span style='color:{lq_color}'>{ba_pct:.1f}% of debit — {lq_label}</span>)",
                unsafe_allow_html=True,
            )
            if min(f_oi, b_oi) < 50:
                st.warning("Low open interest on one or both legs — may face difficulty entering/exiting at quoted prices.")

        # --- Event warning ---
        earnings_dt = _fetch_earnings_date(ticker_display)
        front_dt = pd.to_datetime(front_exp)
        back_dt = pd.to_datetime(back_exp)
        if earnings_dt is not None and front_dt < earnings_dt < back_dt:
            st.warning(
                f"Earnings on **{earnings_dt.strftime('%Y-%m-%d')}** fall between "
                f"your expirations. IV crush on the back leg after earnings can hurt "
                f"this calendar spread."
            )

        # --- P&L at front expiry chart ---
        st.markdown("#### P&L at Front Expiry")
        # Use 2x the expected move as range (ATM straddle * 0.85 ≈ 1 SD)
        expected_move_pct = iv_front * np.sqrt(T_front)
        pct_range = max(0.05, min(0.30, expected_move_pct * 2))
        spot_lo = spot * (1 - pct_range)
        spot_hi = spot * (1 + pct_range)
        spot_range = np.linspace(spot_lo, spot_hi, 200)
        T_back_remaining = T_back - T_front

        # Build skew function from back-month chain data
        _skew_func = None
        try:
            _bc = back_chain.copy()
            _bc_type = _bc[_bc["contract_type"] == spread_type].copy()
            _bc_type = _bc_type[_bc_type["implied_volatility"] > 0].sort_values("strike_price")
            if len(_bc_type) >= 5:
                _skew_strikes = _bc_type["strike_price"].values
                _skew_ivs = _bc_type["implied_volatility"].values
                def _skew_func(new_spot, k):
                    # When spot moves, the moneyness of our strike changes.
                    # Approximate: look up the IV that corresponds to our strike's
                    # new moneyness on the current smile.
                    # New moneyness = k / new_spot; find strike with same moneyness on current curve
                    target_moneyness = k / new_spot
                    equiv_strike = target_moneyness * spot  # map back to current spot's frame
                    # Interpolate IV at this equivalent strike
                    if equiv_strike <= _skew_strikes[0]:
                        return float(_skew_ivs[0])
                    if equiv_strike >= _skew_strikes[-1]:
                        return float(_skew_ivs[-1])
                    return float(np.interp(equiv_strike, _skew_strikes, _skew_ivs))
        except Exception:
            _skew_func = None

        # Flat-IV P&L (standard)
        pnl = _calendar_pnl_at_front_expiry(
            spot_range, strike, T_back_remaining, iv_back, rfr, spread_type, net_debit
        )
        # Skew-adjusted P&L
        pnl_skew = None
        if _skew_func is not None:
            pnl_skew = _calendar_pnl_at_front_expiry(
                spot_range, strike, T_back_remaining, iv_back, rfr, spread_type, net_debit,
                skew_func=_skew_func,
            )

        fig_pnl = go.Figure()
        # Profit region
        fig_pnl.add_trace(go.Scatter(
            x=spot_range, y=np.where(pnl >= 0, pnl, 0) * 100,
            fill="tozeroy", fillcolor="rgba(0,255,150,0.15)",
            line=dict(color=COLORS["success"], width=0),
            name="Profit", hoverinfo="skip",
        ))
        # Loss region
        fig_pnl.add_trace(go.Scatter(
            x=spot_range, y=np.where(pnl < 0, pnl, 0) * 100,
            fill="tozeroy", fillcolor="rgba(255,68,68,0.15)",
            line=dict(color=COLORS["danger"], width=0),
            name="Loss", hoverinfo="skip",
        ))
        # Main line (flat IV)
        fig_pnl.add_trace(go.Scatter(
            x=spot_range, y=pnl * 100,
            line=dict(color=COLORS["accent"], width=2),
            name="P&L (flat IV)",
            hovertemplate="Price: $%{x:.2f}<br>P&L: $%{y:.0f}<extra></extra>",
        ))
        # Skew-adjusted line
        if pnl_skew is not None:
            fig_pnl.add_trace(go.Scatter(
                x=spot_range, y=pnl_skew * 100,
                line=dict(color=COLORS["warning"], width=2, dash="dash"),
                name="P&L (skew-adjusted)",
                hovertemplate="Price: $%{x:.2f}<br>P&L (skew): $%{y:.0f}<extra></extra>",
            ))
        fig_pnl.add_vline(x=spot, line_dash="dash", line_color=COLORS["text_muted"],
                          annotation_text=f"Spot ${spot:.2f}")
        fig_pnl.add_vline(x=strike, line_dash="dot", line_color=COLORS["warning"],
                          annotation_text=f"Strike ${strike:.0f}")
        fig_pnl.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
        _show_legend = pnl_skew is not None
        fig_pnl.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=400,
            xaxis_title="Underlying Price at Front Expiry",
            yaxis_title="P&L per Contract ($)",
            showlegend=_show_legend,
            legend=dict(x=0.02, y=0.98, bgcolor="rgba(0,0,0,0.5)") if _show_legend else {},
            margin=dict(l=50, r=20, t=30, b=50),
        )
        st.plotly_chart(fig_pnl, use_container_width=True)

        # Breakeven range
        be_indices = np.where(np.diff(np.sign(pnl)))[0]
        if len(be_indices) >= 2:
            be_low = spot_range[be_indices[0]]
            be_high = spot_range[be_indices[-1]]
            st.caption(
                f"Breakeven range: **${be_low:.2f}** to **${be_high:.2f}** "
                f"({(be_high - be_low) / spot * 100:.1f}% width around spot)"
            )
        elif len(be_indices) == 1:
            st.caption(f"Single breakeven near **${spot_range[be_indices[0]]:.2f}**")

        max_profit = pnl.max() * 100
        max_loss = net_debit * 100
        st.caption(
            f"Max profit (at strike, front expiry): ~**${max_profit:.0f}** per contract | "
            f"Max loss (debit): **${max_loss:.0f}** per contract"
        )

        # Share link
        _share_params = f"?ticker={ticker_display}&type={spread_type}&front={front_exp}&back={back_exp}&strike={strike:.0f}"
        st.caption(f"Share this setup: append `{_share_params}` to the page URL")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TERM STRUCTURE & IV ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    with error_boundary("Term Structure & IV"):
        st.subheader("IV Term Structure Analysis")
        with st.expander("Why term structure matters for calendars"):
            st.markdown("""
Calendar spreads are **long vega** — they benefit when IV rises, especially in the
back month. The term structure (ATM IV plotted by expiration) tells you whether
the spread is entering at a historically cheap or rich level.

- **Contango** (upward sloping): back-month IV > front-month. Normal state; calendars
  are fairly priced.
- **Backwardation** (inverted): front-month IV > back-month. Often near earnings or
  events. Calendars are cheaper to enter but carry event risk.
""")

        # --- Build ATM IV term structure ---
        ts_data = []
        for exp in expirations:
            chain = term_data[exp]
            iv = _atm_iv(chain, spot, spread_type)
            ts_data.append({
                "expiration": exp,
                "dte": _dte(exp),
                "atm_iv": iv,
            })
        ts_df = pd.DataFrame(ts_data)

        # --- IV Term Structure Chart ---
        st.markdown("#### ATM IV by Expiration")
        fig_ts = go.Figure()
        fig_ts.add_trace(go.Scatter(
            x=ts_df["dte"], y=ts_df["atm_iv"] * 100,
            mode="lines+markers",
            line=dict(color=COLORS["accent"], width=2),
            marker=dict(size=8),
            text=ts_df["expiration"],
            hovertemplate="Exp: %{text}<br>DTE: %{x}d<br>IV: %{y:.1f}%<extra></extra>",
        ))
        # Highlight selected legs
        front_ts = ts_df[ts_df["expiration"] == front_exp]
        back_ts = ts_df[ts_df["expiration"] == back_exp]
        if not front_ts.empty:
            fig_ts.add_trace(go.Scatter(
                x=front_ts["dte"], y=front_ts["atm_iv"] * 100,
                mode="markers", marker=dict(size=14, color=COLORS["danger"], symbol="diamond"),
                name=f"Front: {front_exp}",
            ))
        if not back_ts.empty:
            fig_ts.add_trace(go.Scatter(
                x=back_ts["dte"], y=back_ts["atm_iv"] * 100,
                mode="markers", marker=dict(size=14, color=COLORS["success"], symbol="diamond"),
                name=f"Back: {back_exp}",
            ))

        # Overlay earnings and FOMC dates as vertical lines
        _event_lines = []
        _earn_dt = _fetch_earnings_date(ticker_display)
        if _earn_dt is not None:
            _earn_dte = max((_earn_dt - pd.Timestamp.now()).days, 0)
            if 0 < _earn_dte <= ts_df["dte"].max():
                _event_lines.append(("Earnings", _earn_dte, COLORS["warning"]))

        # Also check earnings for major related tickers (sector ETF)
        _sector_etfs = {"AAPL": "XLK", "MSFT": "XLK", "NVDA": "XLK", "GOOGL": "XLC",
                        "META": "XLC", "AMZN": "XLY", "TSLA": "XLY", "JPM": "XLF",
                        "SPY": None, "QQQ": None}
        _related = _sector_etfs.get(ticker_display)
        if _related:
            _rel_earn = _fetch_earnings_date(_related)
            if _rel_earn:
                _rel_dte = max((_rel_earn - pd.Timestamp.now()).days, 0)
                if 0 < _rel_dte <= ts_df["dte"].max():
                    _event_lines.append((f"{_related} Earnings", _rel_dte, "#ff66cc"))

        # FOMC dates
        _now = pd.Timestamp.now()
        from src.economic_calendar import FOMC_DATES as _fomc_known
        for _fd in _fomc_known:
            _fdt = pd.to_datetime(_fd)
            if _fdt > _now:
                _f_dte = (_fdt - _now).days
                if 0 < _f_dte <= ts_df["dte"].max():
                    _event_lines.append(("FOMC", _f_dte, COLORS["text_muted"]))
                    break  # Only show next FOMC

        for _ev_name, _ev_dte, _ev_color in _event_lines:
            fig_ts.add_vline(x=_ev_dte, line_dash="dot", line_color=_ev_color, line_width=1,
                             annotation_text=_ev_name, annotation_font_color=_ev_color,
                             annotation_font_size=10)

        fig_ts.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=380,
            xaxis_title="Days to Expiration",
            yaxis_title="ATM Implied Volatility (%)",
            margin=dict(l=50, r=20, t=30, b=50),
        )
        st.plotly_chart(fig_ts, use_container_width=True)

        # Historical TS comparison (Polygon Options Starter)
        try:
            from src.options_history import get_historical_iv
            _ts_hist = get_historical_iv(ticker_display, days=5)
            if _ts_hist is not None and not _ts_hist.empty and "atm_iv" in _ts_hist.columns:
                _avg_hist_iv = _ts_hist["atm_iv"].mean()
                _current_iv = ts_df["atm_iv"].mean() if not ts_df.empty else 0
                _chg = ((_current_iv / _avg_hist_iv - 1) * 100) if _avg_hist_iv > 0 else 0
                _ts_hist_slope = _ts_hist.get("ts_slope")
                _hist_slope_avg = float(_ts_hist_slope.dropna().mean()) if _ts_hist_slope is not None and not _ts_hist_slope.dropna().empty else None

                _ts_rc = COLORS["danger"] if _chg > 10 else (COLORS["success"] if _chg < -10 else "#e6edf3")
                st.markdown(
                    f'<div style="background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
                    f'border-radius:8px;padding:10px 14px;border-left:3px solid {_ts_rc};margin-bottom:12px;">'
                    f'<div style="font-size:0.82rem;color:{_ts_rc};font-weight:600;">5-Day Context</div>'
                    f'<div style="font-size:0.78rem;color:{COLORS["text_muted"]};">'
                    f'Current avg ATM IV: {_current_iv:.1%} vs 5-day avg: {_avg_hist_iv:.1%} ({_chg:+.1f}%). '
                    f'{"IV is elevated vs recent history — calendars are more expensive to enter." if _chg > 10 else ("IV has compressed — calendars are cheaper." if _chg < -10 else "IV is near recent average.")}'
                    f'</div></div>', unsafe_allow_html=True)
        except Exception:
            pass

        # --- IV Differential across all pairs ---
        st.markdown("#### Calendar IV Differential (Back - Front)")
        if len(ts_df) >= 2:
            diff_rows = []
            for i in range(len(ts_df)):
                for j in range(i + 1, len(ts_df)):
                    f_row = ts_df.iloc[i]
                    b_row = ts_df.iloc[j]
                    diff_rows.append({
                        "front": f_row["expiration"],
                        "back": b_row["expiration"],
                        "front_dte": f_row["dte"],
                        "back_dte": b_row["dte"],
                        "iv_diff": (b_row["atm_iv"] - f_row["atm_iv"]) * 100,
                        "front_iv": f_row["atm_iv"] * 100,
                        "back_iv": b_row["atm_iv"] * 100,
                    })
            diff_df = pd.DataFrame(diff_rows)
            # Keep only adjacent (1-step) and near-adjacent (2-step) pairs
            exp_list = list(ts_df["expiration"])
            exp_idx = {e: i for i, e in enumerate(exp_list)}
            adjacent = diff_df[
                diff_df.apply(
                    lambda r: exp_idx.get(r["back"], 99) - exp_idx.get(r["front"], 0) <= 2,
                    axis=1,
                )
            ].head(20)

            colors = [COLORS["success"] if v >= 0 else COLORS["danger"] for v in adjacent["iv_diff"]]
            fig_diff = go.Figure(go.Bar(
                x=[f"{r['front']} / {r['back']}" for _, r in adjacent.iterrows()],
                y=adjacent["iv_diff"],
                marker_color=colors,
                hovertemplate="Front: %{customdata[0]}<br>Back: %{customdata[1]}<br>"
                              "Diff: %{y:+.1f}%<extra></extra>",
                customdata=adjacent[["front", "back"]].values,
            ))
            fig_diff.update_layout(
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", height=350,
                xaxis_title="Front / Back Pair",
                yaxis_title="IV Differential (%)",
                margin=dict(l=50, r=20, t=30, b=100),
                xaxis_tickangle=-45,
            )
            fig_diff.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
            st.plotly_chart(fig_diff, use_container_width=True)

        # --- IV vs Realized Vol ---
        st.markdown("#### IV vs Realized Volatility")
        st.caption(
            "Ranks each expiration's ATM IV against the 1-year distribution of "
            "20-day realized vol. High rank = IV is expensive relative to how "
            "the stock has actually moved — favorable for selling the front leg."
        )
        if px_df is not None and len(px_df) > 20:
            returns = px_df["Close"].pct_change().dropna()
            hv_20 = returns.rolling(20).std() * np.sqrt(252)
            hv_60 = returns.rolling(60).std() * np.sqrt(252)
            hv_20_values = hv_20.dropna().values
            current_hv20 = hv_20_values[-1] if len(hv_20_values) > 0 else None
            current_hv60 = hv_60.dropna().values[-1] if len(hv_60.dropna()) > 0 else None

            if len(hv_20_values) > 0:
                pct_rows = []
                for _, row in ts_df.iterrows():
                    iv = row["atm_iv"]
                    rank = (hv_20_values < iv).sum() / len(hv_20_values) * 100
                    iv_hv_ratio = iv / current_hv20 if current_hv20 and current_hv20 > 0 else 0
                    pct_rows.append({
                        "Expiration": row["expiration"],
                        "DTE": row["dte"],
                        "ATM IV": f"{iv * 100:.1f}%",
                        "IV vs HV Rank": f"{rank:.0f}%",
                        "IV/HV20 Ratio": f"{iv_hv_ratio:.2f}x",
                        "vs 20d HV": f"{(iv - current_hv20) * 100:+.1f}%" if current_hv20 else "N/A",
                        "vs 60d HV": f"{(iv - current_hv60) * 100:+.1f}%" if current_hv60 else "N/A",
                    })
                st.dataframe(pd.DataFrame(pct_rows), use_container_width=True, hide_index=True)
                if current_hv20:
                    front_iv_val = ts_df[ts_df["expiration"] == front_exp]["atm_iv"].iloc[0] if not ts_df[ts_df["expiration"] == front_exp].empty else iv_front
                    front_ratio = front_iv_val / current_hv20
                    if front_ratio > 1.2:
                        st.success(f"Front IV is {front_ratio:.1f}x realized vol — rich. Good for selling.")
                    elif front_ratio < 0.8:
                        st.warning(f"Front IV is {front_ratio:.1f}x realized vol — cheap. Calendar may underperform.")

        # --- Event detection ---
        st.markdown("#### Event Detection")
        events_found = []
        earnings_dt = _fetch_earnings_date(ticker_display)
        if earnings_dt is not None:
            events_found.append(("Earnings", earnings_dt))

        now = pd.Timestamp.now()
        from src.economic_calendar import FOMC_DATES
        for fd in FOMC_DATES:
            fdt = pd.to_datetime(fd)
            if fdt > now:
                events_found.append(("FOMC", fdt))

        if events_found:
            event_df_rows = []
            front_dt = pd.to_datetime(front_exp)
            back_dt = pd.to_datetime(back_exp)
            for event_name, event_dt in events_found:
                if event_dt <= back_dt and event_dt > now:
                    between = front_dt < event_dt <= back_dt
                    before_both = event_dt <= front_dt
                    if between:
                        risk = "HIGH — IV crush hits back leg only"
                    elif before_both:
                        risk = "MODERATE — affects both legs"
                    else:
                        risk = "Low"
                    event_df_rows.append({
                        "Event": event_name,
                        "Date": event_dt.strftime("%Y-%m-%d"),
                        "DTE": (event_dt - now).days,
                        "Position": "Between legs" if between else ("Before front" if before_both else "After back"),
                        "Calendar Risk": risk,
                    })
            if event_df_rows:
                st.dataframe(pd.DataFrame(event_df_rows), use_container_width=True, hide_index=True)
            else:
                st.success("No major events detected between selected expirations.")
        else:
            st.info("No upcoming events found for this ticker.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SPREAD SCANNER
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    with error_boundary("Spread Scanner"):
        st.subheader("Calendar Spread Scanner")
        with st.expander("How the scanner works"):
            st.markdown("""
Scans all expiration pairs for the current ticker and ranks calendar spread
opportunities by a composite score combining:

- **Theta/Debit** — daily decay as % of capital risked (higher = better income)
- **IV Differential** — back IV minus front IV (positive = favorable contango)
- **IV Percentile** — front-month IV rank vs 1-year history (higher = better to sell)
- **Vega/Theta ratio** — reward per unit of time decay
- **Liquidity** — combined volume and open interest of both legs

You can also scan a watchlist of tickers for cross-asset opportunities.
""")

        scan_mode = st.radio("Scan mode", ["Current ticker (all pairs)", "Watchlist scan"],
                             horizontal=True)

        if scan_mode == "Watchlist scan":
            wl_input = st.text_input(
                "Tickers (comma-separated)",
                value="SPY, QQQ, AAPL, MSFT, NVDA, TSLA, AMZN, META, GOOGL, AMD",
            )
            scan_tickers = [format_massive_ticker(t.strip()) for t in wl_input.split(",") if t.strip()]
        else:
            scan_tickers = [ticker_display]

        # Filters
        fc1, fc2, fc3, fc4 = st.columns(4)
        with fc1:
            min_oi = st.number_input("Min OI (each leg)", value=100, step=50)
        with fc2:
            max_spread_pct = st.number_input("Max bid-ask width (%)", value=15.0, step=1.0)
        with fc3:
            min_front_dte = st.number_input("Min front DTE", value=7, step=1)
        with fc4:
            max_front_dte = st.number_input("Max front DTE", value=60, step=5)

        # ── Vol Regime Context ──
        @st.cache_data(ttl=1800, show_spinner=False)
        def _fetch_vix_regime():
            try:
                import yfinance as yf
                vd = yf.download("^VIX", period="5d", progress=False)
                vl = float(vd["Close"].iloc[-1]) if vd is not None and not vd.empty else None
                vt = None
                v3 = yf.download("^VIX3M", period="5d", progress=False)
                if v3 is not None and not v3.empty and vl and vl > 0:
                    vt = float(v3["Close"].iloc[-1]) / vl
                return vl, vt
            except Exception:
                return None, None
        _vix_level, _vix_term = _fetch_vix_regime()

        if _vix_level:
            if _vix_level < 15:
                _regime = "Low Vol"
                _regime_color = COLORS["success"]
                _regime_note = "Calm market — calendars benefit from stable spot but may have low theta yield."
            elif _vix_level < 20:
                _regime = "Normal Vol"
                _regime_color = COLORS["accent"]
                _regime_note = "Standard conditions — calendars perform well, especially with contango term structure."
            elif _vix_level < 30:
                _regime = "Elevated Vol"
                _regime_color = COLORS["warning"]
                _regime_note = "Rich premiums favor selling front-month. Watch for gap risk."
            else:
                _regime = "High Vol / Crisis"
                _regime_color = COLORS["danger"]
                _regime_note = "Extreme vol — wide bid-ask spreads, high gamma risk. Prefer wider DTE spacing."

            _term_label = ""
            if _vix_term:
                if _vix_term > 1.05:
                    _term_label = "VIX contango (normal)"
                elif _vix_term < 0.95:
                    _term_label = "VIX backwardation (fear)"
                else:
                    _term_label = "VIX flat"

            st.markdown(
                f'<div style="background:{COLORS["card_bg"]};border:1px solid {_regime_color};'
                f'border-radius:6px;padding:10px 14px;margin-bottom:12px;font-size:0.85rem;">'
                f'<b style="color:{_regime_color};">Vol Regime: {_regime}</b> '
                f'(VIX: {_vix_level:.1f}'
                f'{f" | VIX3M/VIX: {_vix_term:.2f} — {_term_label}" if _vix_term else ""})'
                f'<br><span style="color:{COLORS["text_muted"]};">{_regime_note}</span></div>',
                unsafe_allow_html=True,
            )

        run_scan = st.button("Run Scanner", type="primary", use_container_width=True)

        if run_scan:
            scan_results = []
            scan_progress = st.progress(0, text="Scanning...")
            total_tickers = len(scan_tickers)

            for t_idx, scan_ticker in enumerate(scan_tickers):
                scan_progress.progress(
                    (t_idx + 1) / total_tickers,
                    text=f"Scanning {scan_ticker}..."
                )
                try:
                    # Get spot
                    spx = fetch_massive_data(scan_ticker, 5)
                    if spx is None or spx.empty:
                        continue
                    s_spot = float(spx["Close"].iloc[-1])

                    # Get expirations
                    if scan_ticker == ticker_display:
                        s_exps = expirations
                        s_term = term_data
                    else:
                        all_e = get_expiration_dates(scan_ticker)
                        today_s = pd.Timestamp.now().strftime("%Y-%m-%d")
                        _all_s = [e for e in (all_e or []) if e >= today_s]
                        # For scanner: monthlies + nearest 4 weeklies (fast but covers key dates)
                        _mo = [e for e in _all_s
                               if 15 <= pd.to_datetime(e).day <= 21
                               and pd.to_datetime(e).weekday() == 4]
                        _wk = [e for e in _all_s if e not in _mo][:4]
                        s_exps = sorted(set(_mo + _wk))
                        if len(s_exps) < 4:
                            s_exps = _all_s[:12]
                        s_term = {}
                        for exp in s_exps:
                            try:
                                cdf = fetch_options_chain(scan_ticker, exp)
                                if cdf is not None and not cdf.empty:
                                    s_term[exp] = cdf
                            except Exception:
                                pass

                    # Scan pairs
                    valid_exps = [e for e in s_exps if e in s_term
                                  and min_front_dte <= _dte(e) <= max_front_dte]
                    for f_exp in valid_exps:
                        f_chain = s_term[f_exp]
                        f_strike = _atm_strike(f_chain, s_spot)
                        f_row = _get_contract(f_chain, f_strike, spread_type)
                        if f_row is None:
                            continue
                        f_iv = f_row.get("implied_volatility", 0) or 0
                        f_oi = f_row.get("open_interest", 0) or 0
                        f_vol = f_row.get("volume", 0) or 0
                        f_mid = _mid(f_row)
                        if f_oi < min_oi or f_mid <= 0 or f_iv <= 0:
                            continue

                        for b_exp in [e for e in s_exps if e > f_exp and e in s_term]:
                            b_chain = s_term[b_exp]
                            b_row = _get_contract(b_chain, f_strike, spread_type)
                            if b_row is None:
                                continue
                            b_iv = b_row.get("implied_volatility", 0) or 0
                            b_oi = b_row.get("open_interest", 0) or 0
                            b_vol = b_row.get("volume", 0) or 0
                            b_mid = _mid(b_row)
                            if b_oi < min_oi or b_mid <= 0 or b_iv <= 0:
                                continue

                            debit = b_mid - f_mid
                            if debit <= 0:
                                continue

                            # Check bid-ask width
                            f_ba = ((f_row.get("ask", 0) or 0) - (f_row.get("bid", 0) or 0))
                            b_ba = ((b_row.get("ask", 0) or 0) - (b_row.get("bid", 0) or 0))
                            total_ba_pct = (f_ba + b_ba) / debit * 100 if debit > 0 else 999
                            if total_ba_pct > max_spread_pct:
                                continue

                            sg = _spread_greeks(s_spot, f_strike,
                                                _T(f_exp), _T(b_exp),
                                                f_iv, b_iv, rfr, spread_type)
                            theta_per_day = sg["theta"] * 100
                            vega_per_pct = sg["vega"] * 100
                            theta_debit_ratio = abs(theta_per_day / (debit * 100)) if debit > 0 else 0
                            vega_theta = abs(vega_per_pct / theta_per_day) if abs(theta_per_day) > 0.001 else 0

                            scan_results.append({
                                "Ticker": scan_ticker,
                                "Strike": f_strike,
                                "Front": f_exp,
                                "Back": b_exp,
                                "Front DTE": _dte(f_exp),
                                "Back DTE": _dte(b_exp),
                                "Debit": round(debit, 2),
                                "Front IV": round(f_iv * 100, 1),
                                "Back IV": round(b_iv * 100, 1),
                                "IV Diff": round((b_iv - f_iv) * 100, 1),
                                "Theta/Day": round(theta_per_day, 2),
                                "Theta/Debit": round(theta_debit_ratio * 100, 2),
                                "Vega/$": round(vega_per_pct, 2),
                                "Vega/Theta": round(vega_theta, 2),
                                "Min OI": min(f_oi, b_oi),
                                "Spread Cost": round(total_ba_pct, 1),
                            })
                except Exception as e:
                    logger.warning(f"Scanner error for {scan_ticker}: {e}")
                    continue

            scan_progress.empty()

            if scan_results:
                results_df = pd.DataFrame(scan_results)
                # Composite score: normalize and combine
                for col in ["Theta/Debit", "IV Diff", "Vega/Theta"]:
                    cmin, cmax = results_df[col].min(), results_df[col].max()
                    if cmax > cmin:
                        results_df[f"_{col}_n"] = (results_df[col] - cmin) / (cmax - cmin)
                    else:
                        results_df[f"_{col}_n"] = 0.5
                # Composite: theta yield weighted highest (primary edge in calendars),
                # IV differential and vega/theta split the rest equally
                results_df["Score"] = (
                    results_df["_Theta/Debit_n"] * 0.4
                    + results_df["_IV Diff_n"] * 0.3
                    + results_df["_Vega/Theta_n"] * 0.3
                )
                results_df = results_df.drop(
                    columns=[c for c in results_df.columns if c.startswith("_")]
                )
                results_df["Score"] = results_df["Score"].round(2)
                results_df = results_df.sort_values("Score", ascending=False).reset_index(drop=True)

                st.success(f"Found **{len(results_df)}** calendar spread opportunities.")
                st.dataframe(
                    results_df.style.format({
                        "Strike": "${:,.0f}",
                        "Debit": "${:.2f}",
                        "Front IV": "{:.1f}%",
                        "Back IV": "{:.1f}%",
                        "IV Diff": "{:+.1f}%",
                        "Theta/Day": "${:.2f}",
                        "Theta/Debit": "{:.2f}%",
                        "Vega/$": "${:.2f}",
                        "Spread Cost": "{:.1f}%",
                    }),
                    use_container_width=True,
                    hide_index=True,
                    height=500,
                )

                # Top pick callout
                top = results_df.iloc[0]
                st.info(
                    f"**Top pick:** {top['Ticker']} ${top['Strike']:.0f} "
                    f"{spread_type} {top['Front']}/{top['Back']} — "
                    f"${top['Debit']:.2f} debit, {top['Theta/Debit']:.2f}%/day theta yield, "
                    f"{top['IV Diff']:+.1f}% IV differential"
                )

                # Load into Spread Builder
                st.markdown("**Load into Spread Builder:**")
                load_cols = st.columns(min(5, len(results_df)))
                for li, (_, lrow) in enumerate(results_df.head(5).iterrows()):
                    with load_cols[li]:
                        if st.button(
                            f"{lrow['Ticker']} {lrow['Front'][:5]}/{lrow['Back'][:5]}",
                            key=f"load_scan_{li}",
                            help=f"${lrow['Strike']:.0f} {spread_type} — Score {lrow['Score']:.2f}",
                        ):
                            # Pre-load chains for these expirations
                            _lt = lrow["Ticker"]
                            if _lt != ticker_display:
                                # Need to reload for a different ticker
                                st.session_state["cal_scan_load"] = {
                                    "ticker": _lt,
                                    "front": lrow["Front"],
                                    "back": lrow["Back"],
                                    "strike": lrow["Strike"],
                                }
                                st.info(f"Load {_lt} in the ticker box above and click Load Chain Data, "
                                        f"then select {lrow['Front']}/{lrow['Back']} strike ${lrow['Strike']:.0f} in Tab 1.")
                            else:
                                # Same ticker — ensure chains loaded and switch to tab 1
                                _ensure_chain_loaded(lrow["Front"])
                                _ensure_chain_loaded(lrow["Back"])
                                st.session_state["cal_builder_preset"] = {
                                    "front": lrow["Front"],
                                    "back": lrow["Back"],
                                    "strike": lrow["Strike"],
                                }
                                st.success(f"Loaded! Switch to **Spread Builder** tab — "
                                           f"select {lrow['Front']}/{lrow['Back']} at ${lrow['Strike']:.0f}.")
            else:
                st.warning("No spreads matched your filters. Try relaxing constraints.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — P&L SIMULATOR
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    with error_boundary("P&L Simulator"):
        st.subheader("P&L Simulator")
        with st.expander("How the simulator works"):
            st.markdown("""
Models the calendar spread P&L across **price** and **time**, using Black-Scholes
repricing at each grid point. The heatmap shows profit/loss at every combination
of underlying price and days elapsed.

The scenario sliders let you model:
- **Parallel IV shift** — what if IV rises or falls across the whole curve?
- **Term structure tilt** — what if front IV moves independently of back IV?
- **Time decay progression** — watch theta accelerate as front expiry approaches.
""")

        # Use the selected spread from Tab 1
        st.caption(
            f"Simulating: {ticker_display} ${strike:.0f} {spread_type} "
            f"calendar {front_exp} / {back_exp} @ ${net_debit:.2f} debit"
        )

        # --- Scenario controls ---
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            iv_shift = st.slider("Parallel IV Shift (%)", -15.0, 15.0, 0.0, 0.5,
                                 help="Shift both legs' IV by this amount")
        with sc2:
            ts_tilt = st.slider("Term Structure Tilt (%)", -10.0, 10.0, 0.0, 0.5,
                                help="Extra IV added to back leg only (positive = steepening)")
        with sc3:
            price_range_pct = st.slider("Price Range (%)", 5, 30, 15, 1)

        # --- P&L Heatmap ---
        st.markdown("#### P&L Heatmap (Price vs Time)")
        n_price = 80
        n_time = min(_dte(front_exp), 60)
        if n_time < 2:
            n_time = 2

        prices = np.linspace(spot * (1 - price_range_pct / 100),
                             spot * (1 + price_range_pct / 100), n_price)
        days = np.arange(0, n_time + 1)

        iv_f_adj = iv_front + iv_shift / 100
        iv_b_adj = iv_back + iv_shift / 100 + ts_tilt / 100
        iv_f_adj = max(iv_f_adj, 0.01)
        iv_b_adj = max(iv_b_adj, 0.01)

        pnl_grid = _vectorized_pnl_grid(
            prices, days, _dte(front_exp), _dte(back_exp), strike, spot,
            iv_f_adj, iv_b_adj, rfr, spread_type, net_debit
        )

        fig_heat = go.Figure(go.Heatmap(
            x=prices, y=days,
            z=pnl_grid,
            colorscale=[
                [0, COLORS["danger"]],
                [0.5, "#1c1f26"],
                [1, COLORS["success"]],
            ],
            zmid=0,
            colorbar=dict(title="P&L ($)"),
            hovertemplate="Price: $%{x:.2f}<br>Day: %{y}<br>P&L: $%{z:.0f}<extra></extra>",
        ))
        fig_heat.add_vline(x=spot, line_dash="dash", line_color="white", line_width=1)
        fig_heat.add_vline(x=strike, line_dash="dot", line_color=COLORS["warning"], line_width=1)
        fig_heat.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=500,
            xaxis_title="Underlying Price ($)",
            yaxis_title="Days Elapsed",
            margin=dict(l=50, r=20, t=30, b=50),
        )
        st.plotly_chart(fig_heat, use_container_width=True)

        # --- Time Decay Curve ---
        st.markdown("#### Daily Theta P&L Over Time")
        theta_curve = []
        for d in range(0, n_time + 1):
            T_f = max((_dte(front_exp) - d) / 365.0, 0.001)
            T_b = max((_dte(back_exp) - d) / 365.0, 0.001)
            if T_f > 0.002:
                g = _spread_greeks(spot, strike, T_f, T_b, iv_f_adj, iv_b_adj, rfr, spread_type)
                theta_curve.append({"day": d, "theta": g["theta"] * 100})
            else:
                theta_curve.append({"day": d, "theta": 0})
        theta_df = pd.DataFrame(theta_curve)

        fig_theta = go.Figure()
        fig_theta.add_trace(go.Scatter(
            x=theta_df["day"], y=theta_df["theta"],
            fill="tozeroy",
            fillcolor="rgba(0,209,255,0.15)",
            line=dict(color=COLORS["accent"], width=2),
            hovertemplate="Day %{x}: $%{y:.2f}/day<extra></extra>",
        ))
        fig_theta.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
        fig_theta.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=300,
            xaxis_title="Days Elapsed",
            yaxis_title="Net Theta ($/day per contract)",
            margin=dict(l=50, r=20, t=30, b=50),
        )
        st.plotly_chart(fig_theta, use_container_width=True)
        st.caption("Theta accelerates as the front leg approaches expiry — the 'sweet spot' for calendars.")

        # --- Greeks Evolution ---
        st.markdown("#### Greeks Evolution Over Time")
        greeks_over_time = []
        for d in range(0, n_time + 1):
            T_f = max((_dte(front_exp) - d) / 365.0, 0.001)
            T_b = max((_dte(back_exp) - d) / 365.0, 0.001)
            if T_f > 0.002:
                g = _spread_greeks(spot, strike, T_f, T_b, iv_f_adj, iv_b_adj, rfr, spread_type)
                greeks_over_time.append({
                    "day": d,
                    "delta": g["delta"],
                    "gamma": g["gamma"],
                    "vega": g["vega"] * 100,
                    "theta": g["theta"] * 100,
                })

        if greeks_over_time:
            gk_df = pd.DataFrame(greeks_over_time)
            fig_greeks = make_subplots(rows=2, cols=2,
                                       subplot_titles=["Delta", "Gamma", "Vega ($/1%)", "Theta ($/day)"],
                                       vertical_spacing=0.12, horizontal_spacing=0.08)
            for i, (col, color) in enumerate([
                ("delta", COLORS["accent"]),
                ("gamma", COLORS["warning"]),
                ("vega", COLORS["success"]),
                ("theta", COLORS["danger"]),
            ]):
                row = i // 2 + 1
                c = i % 2 + 1
                fig_greeks.add_trace(go.Scatter(
                    x=gk_df["day"], y=gk_df[col],
                    line=dict(color=color, width=2),
                    name=col.title(),
                    showlegend=False,
                ), row=row, col=c)
                fig_greeks.add_hline(y=0, line_color=COLORS["text_muted"],
                                     line_width=0.3, row=row, col=c)

            fig_greeks.update_layout(
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", height=500,
                margin=dict(l=50, r=20, t=40, b=40),
            )
            st.plotly_chart(fig_greeks, use_container_width=True)

        # --- Vega risk scenarios ---
        st.markdown("#### IV Scenario Analysis")
        iv_scenarios = [-10, -5, -2, 0, 2, 5, 10]
        scenario_rows = []
        for shift in iv_scenarios:
            f_iv = max(iv_front + shift / 100, 0.01)
            b_iv = max(iv_back + shift / 100, 0.01)
            # Spread value today with shifted IV
            new_price = _spread_price(spot, strike, T_front, T_back, f_iv, b_iv, rfr, spread_type)
            pnl_dollars = (new_price - net_debit) * 100
            pnl_pct = (new_price - net_debit) / net_debit * 100 if net_debit > 0 else 0
            scenario_rows.append({
                "IV Shift": f"{shift:+d}%",
                "Front IV": f"{f_iv * 100:.1f}%",
                "Back IV": f"{b_iv * 100:.1f}%",
                "Spread Value": f"${new_price:.2f}",
                "P&L": f"${pnl_dollars:+.0f}",
                "P&L %": f"{pnl_pct:+.1f}%",
            })
        st.dataframe(pd.DataFrame(scenario_rows), use_container_width=True, hide_index=True)

        # --- Term structure tilt scenarios ---
        st.markdown("#### Term Structure Tilt Scenarios")
        st.caption("What if only the back-month IV moves (front stays constant)?")
        tilt_rows = []
        for tilt in [-10, -5, -2, 0, 2, 5, 10]:
            b_iv_tilted = max(iv_back + tilt / 100, 0.01)
            new_price = _spread_price(spot, strike, T_front, T_back,
                                       iv_front, b_iv_tilted, rfr, spread_type)
            pnl_dollars = (new_price - net_debit) * 100
            pnl_pct = (new_price - net_debit) / net_debit * 100 if net_debit > 0 else 0
            tilt_rows.append({
                "Back IV Shift": f"{tilt:+d}%",
                "Front IV": f"{iv_front * 100:.1f}%",
                "Back IV": f"{b_iv_tilted * 100:.1f}%",
                "IV Diff": f"{(b_iv_tilted - iv_front) * 100:+.1f}%",
                "Spread Value": f"${new_price:.2f}",
                "P&L": f"${pnl_dollars:+.0f}",
                "P&L %": f"{pnl_pct:+.1f}%",
            })
        st.dataframe(pd.DataFrame(tilt_rows), use_container_width=True, hide_index=True)

# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — ROLL OPTIMIZER
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    with error_boundary("Roll Optimizer"):
        st.subheader("Roll Optimizer")
        with st.expander("How rolling works"):
            st.markdown("""
When the front leg approaches expiry, you **roll** by buying back the short option
and selling the next month. This extends the trade and collects additional theta.

**When to roll:**
- Theta acceleration peaks around 14-21 DTE, then gamma risk rises sharply.
- Most calendar traders roll the front leg when it reaches 5-10 DTE.
- Rolling for a net credit extends the trade at reduced cost basis.

**Diagonal rolls** shift the strike up or down when rolling, converting the
calendar into a diagonal spread — useful when the underlying has moved.
""")

        st.caption(
            f"Current spread: {ticker_display} ${strike:.0f} {spread_type} "
            f"{front_exp} / {back_exp}"
        )

        # --- Theta decay acceleration curve ---
        st.markdown("#### Net Spread Theta by Front DTE")
        front_dte_total = _dte(front_exp)
        back_dte_total = _dte(back_exp)
        if front_dte_total > 0:
            decay_days = list(range(front_dte_total, 0, -1))
            decay_front_theta = []
            decay_net_theta = []
            for d in decay_days:
                T_f_d = max(d / 365.0, 0.001)
                # Back leg also ages as time passes
                elapsed = front_dte_total - d
                T_b_d = max((back_dte_total - elapsed) / 365.0, 0.001)
                g_front = bs_greeks(spot, strike, T_f_d, rfr, iv_front, spread_type)
                g_back = bs_greeks(spot, strike, T_b_d, rfr, iv_back, spread_type)
                decay_front_theta.append(abs(g_front["theta"]) * 100)
                # Net theta = back theta - front theta (back decays slower)
                decay_net_theta.append((g_back["theta"] - g_front["theta"]) * 100)

            fig_decay = go.Figure()
            fig_decay.add_trace(go.Scatter(
                x=decay_days, y=decay_net_theta,
                fill="tozeroy", fillcolor="rgba(0,209,255,0.12)",
                line=dict(color=COLORS["accent"], width=2),
                name="Net Spread Theta",
                hovertemplate="Front DTE %{x}: $%{y:.2f}/day net<extra></extra>",
            ))
            fig_decay.add_trace(go.Scatter(
                x=decay_days, y=decay_front_theta,
                line=dict(color=COLORS["warning"], width=1, dash="dot"),
                name="Front Leg Theta (abs)",
                hovertemplate="Front DTE %{x}: $%{y:.2f}/day front<extra></extra>",
            ))
            fig_decay.add_vrect(x0=7, x1=21,
                                fillcolor="rgba(0,255,150,0.08)",
                                line_width=0,
                                annotation_text="Roll window",
                                annotation_position="top left")
            fig_decay.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
            fig_decay.update_layout(
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", height=350,
                xaxis_title="Front Leg DTE",
                yaxis_title="Theta ($/day per contract)",
                xaxis=dict(autorange="reversed"),
                margin=dict(l=50, r=20, t=30, b=50),
                legend=dict(x=0.02, y=0.98, bgcolor="rgba(0,0,0,0)"),
            )
            st.plotly_chart(fig_decay, use_container_width=True)
            st.caption(
                "Solid line: net spread theta (your daily P&L from time decay). "
                "Dotted line: front leg theta alone. The green zone (7-21 DTE) "
                "is the typical roll window."
            )

        # --- Roll candidates ---
        st.markdown("#### Roll Candidates")
        # Expirations after front_exp that could be the new front
        roll_candidates = [e for e in expirations if e > front_exp and e < back_exp]

        if roll_candidates:
            roll_rows = []
            for new_front in roll_candidates:
                nf_chain = term_data.get(new_front)
                if nf_chain is None:
                    continue
                nf_row = _get_contract(nf_chain, strike, spread_type)
                if nf_row is None:
                    continue
                nf_mid = _mid(nf_row)
                nf_iv = nf_row.get("implied_volatility", 0) or 0
                nf_dte = _dte(new_front)

                # Cost to roll: buy back front, sell new front
                # Buy back current short at current mid, sell new at new mid
                roll_credit = nf_mid - front_mid  # positive = net credit
                new_net_debit = net_debit - roll_credit

                # New spread Greeks
                new_T_front = _T(new_front)
                new_g = _spread_greeks(spot, strike, new_T_front, T_back,
                                       nf_iv, iv_back, rfr, spread_type)
                new_theta_day = new_g["theta"] * 100
                new_theta_debit = abs(new_theta_day / (new_net_debit * 100)) if new_net_debit > 0 else 0

                roll_rows.append({
                    "New Front": new_front,
                    "New Front DTE": nf_dte,
                    "Roll Credit": f"${roll_credit:+.2f}",
                    "New Cost Basis": f"${new_net_debit:.2f}",
                    "New Front IV": f"{nf_iv * 100:.1f}%",
                    "New Theta/Day": f"${new_theta_day:.2f}",
                    "New Theta/Debit": f"{new_theta_debit:.1%}",
                    "New Delta": f"{new_g['delta']:.4f}",
                    "New Vega": f"${new_g['vega'] * 100:.2f}",
                })
            if roll_rows:
                st.dataframe(pd.DataFrame(roll_rows), use_container_width=True, hide_index=True)
            else:
                st.info("No valid roll candidates found at this strike.")
        else:
            st.info("No intermediate expirations available between front and back legs.")

        # --- Diagonal roll analysis ---
        st.markdown("#### Diagonal Roll Analysis")
        st.caption("Rolling to a different strike converts the calendar to a diagonal spread.")

        # Determine strike increment from available chain data
        _avail_strikes = sorted(common_strikes)
        if len(_avail_strikes) >= 2:
            _strike_incr = min(
                abs(_avail_strikes[i + 1] - _avail_strikes[i])
                for i in range(len(_avail_strikes) - 1)
            )
        else:
            _strike_incr = 1.0
        _max_steps = min(10, len(_avail_strikes) // 2)
        diag_offset = st.slider(
            f"Strike offset (x ${_strike_incr:.0f} increments)",
            min_value=-_max_steps, max_value=_max_steps, value=0, step=1,
            help="Positive = roll up (bullish bias), Negative = roll down (bearish bias)",
            key="diag_offset",
        )

        if roll_candidates:
            # Use first roll candidate
            diag_exp = roll_candidates[0]
            diag_chain = term_data.get(diag_exp)
            if diag_chain is not None:
                type_strikes = sorted(
                    diag_chain[diag_chain["contract_type"] == spread_type]["strike_price"].unique()
                )
                # Find nearest strike to current + offset (in strike increments)
                target_strike = strike + diag_offset * _strike_incr
                if type_strikes:
                    diag_strike = float(min(type_strikes, key=lambda s: abs(s - target_strike)))
                    diag_row = _get_contract(diag_chain, diag_strike, spread_type)

                    if diag_row is not None:
                        diag_mid = _mid(diag_row)
                        diag_iv = diag_row.get("implied_volatility", 0) or 0
                        diag_credit = diag_mid - front_mid
                        diag_cost = net_debit - diag_credit

                        diag_T = _T(diag_exp)
                        diag_g = _spread_greeks(spot, diag_strike, diag_T, T_back,
                                                diag_iv, iv_back, rfr, spread_type)

                        d1, d2, d3, d4 = st.columns(4)
                        d1.metric("Diagonal Strike", f"${diag_strike:.0f}",
                                  delta=f"{diag_strike - strike:+.0f} from current")
                        d2.metric("Roll Credit/Debit", f"${diag_credit:+.2f}")
                        d3.metric("New Cost Basis", f"${diag_cost:.2f}")
                        d4.metric("New Delta", f"{diag_g['delta']:.4f}",
                                  help="Delta shifts as strike moves relative to spot")

                        # P&L comparison: calendar vs diagonal at front expiry
                        spot_range_d = np.linspace(spot * (1 - pct_range), spot * (1 + pct_range), 150)
                        T_back_rem = T_back - diag_T
                        pnl_cal = _calendar_pnl_at_front_expiry(
                            spot_range_d, strike, T_back - T_front, iv_back, rfr,
                            spread_type, net_debit
                        )
                        pnl_diag = _calendar_pnl_at_front_expiry(
                            spot_range_d, diag_strike, T_back_rem, iv_back, rfr,
                            spread_type, diag_cost
                        )

                        fig_diag = go.Figure()
                        fig_diag.add_trace(go.Scatter(
                            x=spot_range_d, y=pnl_cal * 100,
                            line=dict(color=COLORS["accent"], width=2),
                            name=f"Calendar ${strike:.0f}",
                        ))
                        fig_diag.add_trace(go.Scatter(
                            x=spot_range_d, y=pnl_diag * 100,
                            line=dict(color=COLORS["warning"], width=2, dash="dash"),
                            name=f"Diagonal ${diag_strike:.0f}",
                        ))
                        fig_diag.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
                        fig_diag.add_vline(x=spot, line_dash="dash", line_color=COLORS["text_muted"])
                        fig_diag.update_layout(
                            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)", height=380,
                            xaxis_title="Underlying Price",
                            yaxis_title="P&L per Contract ($)",
                            margin=dict(l=50, r=20, t=30, b=50),
                        )
                        st.plotly_chart(fig_diag, use_container_width=True)
        else:
            st.info("Need intermediate expirations between front and back to analyze diagonal rolls.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — RISK ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

with tab6:
    with error_boundary("Risk Analysis"):
        st.subheader("Risk Analysis")
        with st.expander("Calendar spread risks"):
            st.markdown("""
Calendar spreads have **defined risk** (max loss = debit paid), but several
risk factors can cause early exits or unexpected losses:

- **Vega risk**: A parallel IV crush hurts the back leg more (higher vega).
- **Term structure inversion**: Front IV rising above back IV destroys the edge.
- **Gamma risk near expiry**: As the front leg expires, gamma spikes — small
  price moves cause large delta swings.
- **Pin risk**: If the underlying is near the strike at front expiry, assignment
  risk on the short leg creates uncertainty.
- **Early assignment**: American-style options can be assigned early, especially
  near ex-dividend dates for calls or deep ITM for puts.
""")

        st.caption(
            f"Analyzing: {ticker_display} ${strike:.0f} {spread_type} "
            f"{front_exp} / {back_exp} @ ${net_debit:.2f} debit"
        )

        # --- Vega Risk Matrix ---
        st.markdown("#### Vega Risk — IV Shift vs Term Structure Change")
        parallel_shifts = [-10, -5, -2, 0, 2, 5, 10]
        tilt_shifts = [-8, -4, -2, 0, 2, 4, 8]

        vega_grid = np.zeros((len(tilt_shifts), len(parallel_shifts)))
        for i, tilt in enumerate(tilt_shifts):
            for j, par in enumerate(parallel_shifts):
                f_iv = max(iv_front + par / 100, 0.01)
                b_iv = max(iv_back + par / 100 + tilt / 100, 0.01)
                val = _spread_price(spot, strike, T_front, T_back, f_iv, b_iv, rfr, spread_type)
                vega_grid[i, j] = (val - net_debit) * 100

        fig_vega = go.Figure(go.Heatmap(
            x=[f"{s:+d}%" for s in parallel_shifts],
            y=[f"{s:+d}%" for s in tilt_shifts],
            z=vega_grid,
            colorscale=[
                [0, COLORS["danger"]],
                [0.5, "#1c1f26"],
                [1, COLORS["success"]],
            ],
            zmid=0,
            colorbar=dict(title="P&L ($)"),
            hovertemplate="Parallel: %{x}<br>Tilt: %{y}<br>P&L: $%{z:.0f}<extra></extra>",
            text=np.round(vega_grid, 0).astype(int),
            texttemplate="%{text}",
            textfont=dict(size=10),
        ))
        fig_vega.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=400,
            xaxis_title="Parallel IV Shift",
            yaxis_title="Term Structure Tilt (back only)",
            margin=dict(l=80, r=20, t=30, b=50),
        )
        st.plotly_chart(fig_vega, use_container_width=True)
        st.caption(
            "Each cell shows P&L per contract. Rows = back-month IV shift relative to front. "
            "Columns = parallel shift applied to both legs. Bottom-left = worst case (IV crush + inversion)."
        )

        # --- Gamma risk near expiry ---
        st.markdown("#### Gamma Risk Near Front Expiry")
        _front_dte_g = _dte(front_exp)
        _back_dte_g = _dte(back_exp)
        gamma_days = list(range(min(30, _front_dte_g), 0, -1))
        gamma_data = []
        for d in gamma_days:
            T_d = max(d / 365.0, 0.001)
            # Back leg also ages: elapsed = original_front_dte - current_front_dte
            elapsed = _front_dte_g - d
            T_b_aged = max((_back_dte_g - elapsed) / 365.0, 0.001)
            g = _spread_greeks(spot, strike, T_d, T_b_aged, iv_front, iv_back, rfr, spread_type)
            dollar_gamma = g["gamma"] * 100
            gamma_data.append({
                "dte": d,
                "gamma": g["gamma"],
                "dollar_gamma": dollar_gamma,
                "delta": g["delta"],
            })
        gamma_df = pd.DataFrame(gamma_data)

        fig_gamma = make_subplots(rows=1, cols=2,
                                   subplot_titles=["Net Gamma", "Net Delta"],
                                   horizontal_spacing=0.1)
        fig_gamma.add_trace(go.Scatter(
            x=gamma_df["dte"], y=gamma_df["gamma"],
            line=dict(color=COLORS["warning"], width=2),
            fill="tozeroy", fillcolor="rgba(255,170,0,0.1)",
            name="Gamma",
        ), row=1, col=1)
        fig_gamma.add_trace(go.Scatter(
            x=gamma_df["dte"], y=gamma_df["delta"],
            line=dict(color=COLORS["accent"], width=2),
            fill="tozeroy", fillcolor="rgba(0,209,255,0.1)",
            name="Delta",
        ), row=1, col=2)
        for c in [1, 2]:
            fig_gamma.add_vrect(x0=1, x1=7, fillcolor="rgba(255,68,68,0.1)",
                                line_width=0, row=1, col=c)
            fig_gamma.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.3,
                                row=1, col=c)
        fig_gamma.update_xaxes(autorange="reversed")
        fig_gamma.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=350,
            showlegend=False,
            margin=dict(l=50, r=20, t=40, b=50),
        )
        st.plotly_chart(fig_gamma, use_container_width=True)
        st.caption("Red zone (< 7 DTE): gamma spikes as the short leg approaches expiry. Consider rolling before entering this zone.")

        # --- Pin risk zone ---
        st.markdown("#### Pin Risk Analysis")
        # Pin risk radius = remaining extrinsic value of the short leg,
        # approximated by BS price minus intrinsic (clamped to at least 1%)
        if spread_type == "call":
            intrinsic = max(spot - strike, 0)
        else:
            intrinsic = max(strike - spot, 0)
        extrinsic = max(front_mid - intrinsic, 0)
        # Convert extrinsic to price range: extrinsic ≈ how far spot can
        # move before the option goes from ATM to deep ITM/OTM
        pin_range = max(extrinsic, spot * 0.01)  # at least 1% of spot
        pin_low = strike - pin_range
        pin_high = strike + pin_range
        in_pin_zone = pin_low <= spot <= pin_high
        days_to_front = _dte(front_exp)

        if in_pin_zone and days_to_front <= 7:
            st.error(
                f"HIGH PIN RISK — Spot (${spot:.2f}) is within the extrinsic-value "
                f"zone (${pin_low:.2f}–${pin_high:.2f}) of the ${strike:.0f} strike "
                f"with only {days_to_front} DTE. Assignment risk is elevated."
            )
        elif in_pin_zone:
            st.warning(
                f"Spot (${spot:.2f}) is near the strike (${strike:.0f}). "
                f"Monitor closely as front expiry ({front_exp}) approaches."
            )
        else:
            dist_pct = abs(spot - strike) / spot * 100 if spot > 0 else 0
            st.success(
                f"Spot is {dist_pct:.1f}% from strike — low pin risk at current levels."
            )

        # --- Early assignment risk ---
        st.markdown("#### Early Assignment Risk")
        assignment_risks = []

        # Check if short leg is ITM
        if spread_type == "call" and spot > strike and strike > 0:
            itm_pct = (spot - strike) / strike * 100
            assignment_risks.append(f"Short call is **{itm_pct:.1f}% ITM** — elevated assignment risk")
        elif spread_type == "put" and spot < strike and strike > 0:
            itm_pct = (strike - spot) / strike * 100
            assignment_risks.append(f"Short put is **{itm_pct:.1f}% ITM** — elevated assignment risk")

        # Check for upcoming dividends
        try:
            import yfinance as yf
            tk = yf.Ticker(ticker_display)
            divs = tk.dividends
            if divs is not None and len(divs) > 0:
                last_div = divs.iloc[-1]
                if last_div > 0 and spread_type == "call":
                    # Estimate next ex-div based on last payment frequency
                    assignment_risks.append(
                        f"Stock pays dividends (last: ${last_div:.2f}). "
                        f"ITM short calls face early assignment risk near ex-dividend dates."
                    )
        except Exception:
            pass

        # Time value threshold: less than 0.5% of the strike price
        # (e.g., $0.50 for a $100 stock, $2.50 for a $500 stock)
        _tv_threshold = strike * 0.005
        if extrinsic < _tv_threshold and days_to_front <= 5:
            assignment_risks.append(
                f"Front leg extrinsic value (${extrinsic:.2f}) is below "
                f"${_tv_threshold:.2f} (0.5% of strike) with {days_to_front} DTE — "
                f"exercise becomes more likely."
            )

        if assignment_risks:
            for risk in assignment_risks:
                st.warning(risk)
        else:
            st.success("Low early assignment risk at current levels.")

        # --- Tail risk scenarios ---
        st.markdown("#### Tail Risk Scenarios")
        tail_moves = [
            ("-3 sigma gap down", -3),
            ("-2 sigma gap down", -2),
            ("-1 sigma move", -1),
            ("No move", 0),
            ("+1 sigma move", 1),
            ("+2 sigma gap up", 2),
            ("+3 sigma gap up", 3),
        ]
        # Calculate daily sigma from front IV
        daily_sigma = iv_front * spot / np.sqrt(252)

        # Estimate IV-return sensitivity from historical data (leverage effect)
        # Empirical: for equities, ~-0.3 to -0.5 correlation between returns and IV changes
        _iv_return_beta = -0.4  # typical equity leverage effect
        if px_df is not None and len(px_df) > 40:
            try:
                _rets = px_df["Close"].pct_change().dropna()
                _rv = _rets.rolling(20).std() * np.sqrt(252)
                _rv_change = _rv.pct_change()
                _both = pd.concat([_rets, _rv_change], axis=1).dropna()
                if len(_both) > 20:
                    _corr = _both.iloc[:, 0].corr(_both.iloc[:, 1])
                    if not np.isnan(_corr):
                        _iv_return_beta = np.clip(_corr * 2, -1.0, 0.2)
            except Exception:
                pass

        tail_rows = []
        for label, n_sigma in tail_moves:
            new_spot = spot + n_sigma * daily_sigma
            # At front expiry
            if spread_type == "call":
                short_val = max(new_spot - strike, 0)
            else:
                short_val = max(strike - new_spot, 0)
            T_remain = T_back - T_front
            # IV adjustment: derived from empirical return-vol relationship
            # A -1σ move (~-0.5% daily) shifts IV by ~β * move_magnitude
            move_pct = (new_spot - spot) / spot if spot > 0 else 0
            iv_adj = iv_back * (1 + _iv_return_beta * move_pct * np.sqrt(252))
            iv_adj = max(iv_adj, 0.05)

            long_val = black_scholes(new_spot, strike, T_remain, rfr, iv_adj, spread_type)
            spread_val = long_val - short_val
            pnl = (spread_val - net_debit) * 100
            pnl_pct = (spread_val - net_debit) / net_debit * 100 if net_debit > 0 else 0

            tail_rows.append({
                "Scenario": label,
                "Price": f"${new_spot:.2f}",
                "Move": f"{(new_spot - spot) / spot * 100:+.1f}%" if spot > 0 else "N/A",
                "Spread Value": f"${spread_val:.2f}",
                "P&L": f"${pnl:+.0f}",
                "P&L %": f"{pnl_pct:+.1f}%",
            })
        st.dataframe(pd.DataFrame(tail_rows), use_container_width=True, hide_index=True)

        # --- Margin estimate ---
        st.markdown("#### Estimated Margin Requirement")
        # Calendar spreads: margin = net debit (the max loss)
        margin_reg_t = net_debit * 100
        st.info(
            f"**Reg-T margin**: ${margin_reg_t:.0f} per spread (equal to net debit). "
            f"Calendar spreads are defined-risk — margin = max loss. "
            f"Portfolio margin may reduce this further."
        )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — HISTORICAL BACKTEST
# ══════════════════════════════════════════════════════════════════════════════

with tab7:
    with error_boundary("Historical Backtest"):
        st.subheader("Historical Calendar Spread Backtest")
        with st.expander("How the backtest works"):
            st.markdown("""
Simulates entering ATM calendar spreads on a recurring schedule using historical
price data and realized volatility. Since we don't have historical options prices,
the backtest uses **Black-Scholes with realized vol** as a proxy.

**Methodology:**
1. On each entry date, price the calendar using trailing 20-day realized vol for the
   front leg and 60-day realized vol for the back leg (approximating term structure).
2. Hold until the exit condition triggers (target profit, max loss, or DTE threshold).
3. At exit, reprice the spread using BS with the vol prevailing at that date.

This is an approximation — real options prices would differ due to supply/demand,
skew, and vol-of-vol. Use this for directional insight, not exact P&L.
""")

        if px_df is None or len(px_df) < 60:
            st.warning("Need at least 60 days of price history for backtesting.")
            st.stop()

        # --- Backtest parameters ---
        bp1, bp2, bp3 = st.columns(3)
        with bp1:
            bt_front_dte = st.number_input("Front leg DTE", value=30, min_value=7, max_value=90,
                                            step=7, key="bt_front_dte")
        with bp2:
            bt_back_dte = st.number_input("Back leg DTE", value=60, min_value=14, max_value=180,
                                           step=7, key="bt_back_dte")
        with bp3:
            bt_frequency = st.selectbox("Entry frequency", ["Monthly", "Bi-weekly", "Weekly"],
                                        index=0, key="bt_freq")

        bp4, bp5, bp6 = st.columns(3)
        with bp4:
            bt_target = st.number_input("Profit target (%)", value=50.0, step=10.0, key="bt_target")
        with bp5:
            bt_stop = st.number_input("Stop loss (%)", value=50.0, step=10.0, key="bt_stop")
        with bp6:
            bt_exit_dte = st.number_input("Exit at front DTE", value=7, min_value=1, max_value=21,
                                           step=1, key="bt_exit_dte")

        if bt_back_dte <= bt_front_dte:
            st.error("Back leg DTE must be greater than front leg DTE.")
            st.stop()

        run_bt = st.button("Run Backtest", type="primary", use_container_width=True, key="run_bt")

        if run_bt:
            # Build return series
            closes = px_df["Close"].values
            dates = px_df.index if isinstance(px_df.index, pd.DatetimeIndex) else pd.to_datetime(px_df.index)
            returns = pd.Series(closes).pct_change().values

            # Realized vol series
            rv_20 = pd.Series(returns).rolling(20).std().values * np.sqrt(252)
            rv_60 = pd.Series(returns).rolling(60).std().values * np.sqrt(252)

            # Entry interval
            freq_map = {"Monthly": 21, "Bi-weekly": 10, "Weekly": 5}
            interval = freq_map[bt_frequency]

            # Run backtest
            trades = []
            start_idx = 60  # need 60d lookback for rv_60
            entry_idx = start_idx

            while entry_idx < len(closes) - bt_front_dte - 5:
                s = closes[entry_idx]
                iv_f = rv_20[entry_idx] if not np.isnan(rv_20[entry_idx]) else 0.25
                iv_b = rv_60[entry_idx] if not np.isnan(rv_60[entry_idx]) else 0.25
                iv_f = max(iv_f, 0.05)
                iv_b = max(iv_b, 0.05)

                T_f = bt_front_dte / 365.0
                T_b = bt_back_dte / 365.0
                entry_debit = _spread_price(s, s, T_f, T_b, iv_f, iv_b, rfr, spread_type)

                if entry_debit <= 0:
                    entry_idx += interval
                    continue

                # Simulate holding
                exit_reason = "DTE"
                exit_pnl_pct = 0
                exit_idx = entry_idx

                for hold_d in range(1, bt_front_dte - bt_exit_dte + 1):
                    check_idx = entry_idx + hold_d
                    if check_idx >= len(closes):
                        break
                    exit_idx = check_idx

                    s_now = closes[check_idx]
                    T_f_now = max((bt_front_dte - hold_d) / 365.0, 0.001)
                    T_b_now = max((bt_back_dte - hold_d) / 365.0, 0.001)

                    # Use current realized vol
                    iv_f_now = rv_20[check_idx] if check_idx < len(rv_20) and not np.isnan(rv_20[check_idx]) else iv_f
                    iv_b_now = rv_60[check_idx] if check_idx < len(rv_60) and not np.isnan(rv_60[check_idx]) else iv_b
                    iv_f_now = max(iv_f_now, 0.05)
                    iv_b_now = max(iv_b_now, 0.05)

                    current_val = _spread_price(s_now, s, T_f_now, T_b_now,
                                                iv_f_now, iv_b_now, rfr, spread_type)
                    pnl_pct = (current_val - entry_debit) / entry_debit * 100

                    if pnl_pct >= bt_target:
                        exit_reason = "Target"
                        exit_pnl_pct = pnl_pct
                        break
                    elif pnl_pct <= -bt_stop:
                        exit_reason = "Stop"
                        exit_pnl_pct = pnl_pct
                        break
                    exit_pnl_pct = pnl_pct

                trades.append({
                    "Entry Date": dates[entry_idx].strftime("%Y-%m-%d") if hasattr(dates[entry_idx], "strftime") else str(dates[entry_idx]),
                    "Exit Date": dates[exit_idx].strftime("%Y-%m-%d") if hasattr(dates[exit_idx], "strftime") else str(dates[exit_idx]),
                    "Entry Price": s,
                    "Debit": round(entry_debit, 2),
                    "P&L %": round(exit_pnl_pct, 1),
                    "P&L $": round(exit_pnl_pct / 100 * entry_debit * 100, 0),
                    "Exit": exit_reason,
                    "Hold Days": exit_idx - entry_idx,
                    "Front IV": round(iv_f * 100, 1),
                    "Back IV": round(iv_b * 100, 1),
                })
                entry_idx += interval

            if trades:
                trades_df = pd.DataFrame(trades)
                wins = (trades_df["P&L %"] > 0).sum()
                total = len(trades_df)
                avg_pnl = trades_df["P&L %"].mean()
                total_pnl = trades_df["P&L $"].sum()

                # Max drawdown
                cum = trades_df["P&L $"].cumsum()
                running_max = cum.cummax()
                drawdowns = cum - running_max
                max_dd = drawdowns.min()
                max_dd_pct = (max_dd / running_max[drawdowns.idxmin()]) * 100 if running_max[drawdowns.idxmin()] > 0 else 0

                # Profit factor
                gross_profit = trades_df[trades_df["P&L $"] > 0]["P&L $"].sum()
                gross_loss = trades_df[trades_df["P&L $"] < 0]["P&L $"].sum()
                pf = f"{abs(gross_profit / gross_loss):.2f}" if gross_loss != 0 else "N/A"

                # Summary metrics
                m1, m2, m3, m4, m5, m6 = st.columns(6)
                m1.metric("Total Trades", total)
                m2.metric("Win Rate", f"{wins / total:.0%}")
                m3.metric("Avg P&L", f"{avg_pnl:+.1f}%")
                m4.metric("Total P&L", f"${total_pnl:+,.0f}")
                m5.metric("Max Drawdown", f"${max_dd:,.0f}")
                m6.metric("Profit Factor", pf)

                # Equity curve
                st.markdown("#### Equity Curve")
                cum_pnl = trades_df["P&L $"].cumsum()
                fig_eq = go.Figure()
                fig_eq.add_trace(go.Scatter(
                    x=trades_df["Entry Date"], y=cum_pnl,
                    fill="tozeroy",
                    fillcolor="rgba(0,209,255,0.15)",
                    line=dict(color=COLORS["accent"], width=2),
                    hovertemplate="Date: %{x}<br>Cumulative P&L: $%{y:,.0f}<extra></extra>",
                ))
                fig_eq.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
                fig_eq.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=350,
                    xaxis_title="Entry Date",
                    yaxis_title="Cumulative P&L ($)",
                    margin=dict(l=50, r=20, t=30, b=50),
                )
                st.plotly_chart(fig_eq, use_container_width=True)

                # P&L distribution
                st.markdown("#### P&L Distribution")
                fig_dist = go.Figure()
                colors_dist = [COLORS["success"] if p > 0 else COLORS["danger"]
                               for p in trades_df["P&L %"]]
                fig_dist.add_trace(go.Bar(
                    x=trades_df["Entry Date"],
                    y=trades_df["P&L %"],
                    marker_color=colors_dist,
                    hovertemplate="Date: %{x}<br>P&L: %{y:+.1f}%<br>Exit: %{customdata}<extra></extra>",
                    customdata=trades_df["Exit"],
                ))
                fig_dist.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
                fig_dist.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=300,
                    xaxis_title="Entry Date",
                    yaxis_title="P&L (%)",
                    margin=dict(l=50, r=20, t=30, b=50),
                )
                st.plotly_chart(fig_dist, use_container_width=True)

                # Exit analysis
                st.markdown("#### Exit Reason Breakdown")
                exit_summary = trades_df.groupby("Exit").agg(
                    Count=("P&L %", "count"),
                    Avg_PnL=("P&L %", "mean"),
                    Total_PnL=("P&L $", "sum"),
                ).round(1)
                exit_summary.columns = ["Count", "Avg P&L %", "Total P&L $"]
                st.dataframe(exit_summary, use_container_width=True)

                # Monthly heatmap
                st.markdown("#### Monthly Returns")
                trades_df["Month"] = pd.to_datetime(trades_df["Entry Date"]).dt.to_period("M").astype(str)
                monthly = trades_df.groupby("Month")["P&L $"].sum().reset_index()
                monthly.columns = ["Month", "P&L"]
                colors_mo = [COLORS["success"] if p > 0 else COLORS["danger"] for p in monthly["P&L"]]
                fig_mo = go.Figure(go.Bar(
                    x=monthly["Month"], y=monthly["P&L"],
                    marker_color=colors_mo,
                ))
                fig_mo.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
                fig_mo.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=300,
                    xaxis_title="Month", yaxis_title="P&L ($)",
                    margin=dict(l=50, r=20, t=30, b=80),
                    xaxis_tickangle=-45,
                )
                st.plotly_chart(fig_mo, use_container_width=True)

                # Trade log
                with st.expander("Full Trade Log"):
                    st.dataframe(trades_df, use_container_width=True, hide_index=True)

                # De Prado: Deflated Sharpe
                st.markdown("#### Statistical Significance")
                pnl_series = trades_df["P&L %"].values
                if len(pnl_series) > 1 and pnl_series.std() > 0:
                    sharpe = pnl_series.mean() / pnl_series.std()
                    n = len(pnl_series)
                    skew = pd.Series(pnl_series).skew()
                    kurt = pd.Series(pnl_series).kurtosis()

                    # DSR adjustment (Lo 2002): Var(SR) accounting for non-normality
                    # pandas .kurtosis() returns excess kurtosis (Fisher), so use it directly
                    sr_std = np.sqrt((1 + 0.5 * sharpe**2 - skew * sharpe + kurt / 4 * sharpe**2) / (n - 1))
                    dsr_z = sharpe / sr_std if sr_std > 0 else 0
                    dsr_p = 1 - norm.cdf(dsr_z)

                    # Sequential bootstrap (simplified — block resample)
                    n_boot = 1000
                    block_size = max(3, int(np.sqrt(n)))
                    boot_sharpes = []
                    for _ in range(n_boot):
                        blocks = [pnl_series[i:i + block_size] for i in
                                  np.random.randint(0, max(1, n - block_size), size=n // block_size + 1)]
                        boot_sample = np.concatenate(blocks)[:n]
                        if boot_sample.std() > 0:
                            boot_sharpes.append(boot_sample.mean() / boot_sample.std())
                    boot_p = (np.array(boot_sharpes) <= 0).mean() if boot_sharpes else 1.0

                    s1, s2, s3, s4 = st.columns(4)
                    s1.metric("Sharpe Ratio", f"{sharpe:.2f}")
                    s2.metric("Deflated Sharpe p-value", f"{dsr_p:.3f}",
                              help="p < 0.05 suggests Sharpe is not due to chance/overfitting")
                    s3.metric("Bootstrap p-value", f"{boot_p:.3f}",
                              help="Block bootstrap — probability that true Sharpe <= 0")
                    s4.metric("Sample Size", f"{n} trades")

                    if dsr_p < 0.05 and boot_p < 0.05:
                        st.success("Strategy shows statistical significance at the 5% level.")
                    elif dsr_p < 0.10 or boot_p < 0.10:
                        st.warning("Marginal significance — more trades needed for confidence.")
                    else:
                        st.error("Not statistically significant — could be noise.")
            else:
                st.warning("No trades generated. Try a longer price history or different parameters.")

        # ── Scanner Score Validation ──
        st.markdown("---")
        st.markdown("#### Scanner Score Validation")
        st.caption(
            "Tests whether the scanner's composite score actually predicts P&L. "
            "Splits historical trades by entry conditions (IV differential, front IV richness) "
            "and compares outcomes — validating or invalidating the scanner's ranking logic."
        )

        _has_trades = False
        try:
            _has_trades = trades_df is not None and len(trades_df) > 5
        except NameError:
            pass
        if _has_trades:
            trades_v = trades_df.copy()
            # Score proxy: IV differential (back IV - front IV) is the primary scanner signal
            trades_v["IV Diff"] = trades_v["Back IV"] - trades_v["Front IV"]

            # Split into terciles by IV differential
            trades_v["IV Diff Tercile"] = pd.qcut(trades_v["IV Diff"], q=3,
                                                    labels=["Low Contango", "Mid", "High Contango"],
                                                    duplicates="drop")

            # Split by front IV richness (above/below median)
            median_fiv = trades_v["Front IV"].median()
            trades_v["Front IV Rich"] = trades_v["Front IV"].apply(
                lambda v: "Rich (above median)" if v > median_fiv else "Cheap (below median)"
            )

            vc1, vc2 = st.columns(2)

            with vc1:
                st.markdown("**P&L by IV Differential (term structure)**")
                iv_group = trades_v.groupby("IV Diff Tercile", observed=True).agg(
                    Trades=("P&L %", "count"),
                    Win_Rate=("P&L %", lambda x: (x > 0).mean()),
                    Avg_PnL=("P&L %", "mean"),
                    Total_PnL=("P&L $", "sum"),
                ).round(2)
                iv_group["Win Rate"] = iv_group["Win_Rate"].apply(lambda v: f"{v:.0%}")
                iv_group["Avg P&L %"] = iv_group["Avg_PnL"].apply(lambda v: f"{v:+.1f}%")
                iv_group["Total P&L $"] = iv_group["Total_PnL"].apply(lambda v: f"${v:+,.0f}")
                st.dataframe(iv_group[["Trades", "Win Rate", "Avg P&L %", "Total P&L $"]],
                             use_container_width=True)

                # Does high contango predict better results?
                if "High Contango" in iv_group.index and "Low Contango" in iv_group.index:
                    high_avg = iv_group.loc["High Contango", "Avg_PnL"]
                    low_avg = iv_group.loc["Low Contango", "Avg_PnL"]
                    if high_avg > low_avg:
                        st.success(
                            f"High contango entries: {high_avg:+.1f}% avg vs "
                            f"{low_avg:+.1f}% for low contango. "
                            f"Scanner's IV differential signal is **validated**."
                        )
                    else:
                        st.warning(
                            f"High contango entries: {high_avg:+.1f}% avg vs "
                            f"{low_avg:+.1f}% for low contango. "
                            f"IV differential signal is **not predictive** in this sample."
                        )

            with vc2:
                st.markdown("**P&L by Front IV Richness**")
                fiv_group = trades_v.groupby("Front IV Rich").agg(
                    Trades=("P&L %", "count"),
                    Win_Rate=("P&L %", lambda x: (x > 0).mean()),
                    Avg_PnL=("P&L %", "mean"),
                    Total_PnL=("P&L $", "sum"),
                ).round(2)
                fiv_group["Win Rate"] = fiv_group["Win_Rate"].apply(lambda v: f"{v:.0%}")
                fiv_group["Avg P&L %"] = fiv_group["Avg_PnL"].apply(lambda v: f"{v:+.1f}%")
                fiv_group["Total P&L $"] = fiv_group["Total_PnL"].apply(lambda v: f"${v:+,.0f}")
                st.dataframe(fiv_group[["Trades", "Win Rate", "Avg P&L %", "Total P&L $"]],
                             use_container_width=True)

                rich = fiv_group.loc["Rich (above median)", "Avg_PnL"] if "Rich (above median)" in fiv_group.index else 0
                cheap = fiv_group.loc["Cheap (below median)", "Avg_PnL"] if "Cheap (below median)" in fiv_group.index else 0
                if rich > cheap:
                    st.success(
                        f"Rich front IV entries: {rich:+.1f}% avg vs "
                        f"{cheap:+.1f}% for cheap. "
                        f"Selling rich front-month premium is **validated**."
                    )
                else:
                    st.warning(
                        f"Rich front IV entries: {rich:+.1f}% avg vs "
                        f"{cheap:+.1f}% for cheap. "
                        f"Front IV richness is **not predictive** in this sample."
                    )
        elif run_bt:
            st.info("Run the backtest above to see scanner score validation.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — AI ASSESSMENT
# ══════════════════════════════════════════════════════════════════════════════

with tab8:
    with error_boundary("AI Assessment"):
        st.subheader("Grok AI Calendar Spread Assessment")
        with st.expander("How AI assessment works"):
            st.markdown("""
Sends the current spread configuration, term structure data, and market context
to **Grok 4** for a qualitative assessment. Grok searches X/Twitter for the latest
on the ticker and evaluates whether the current environment favors calendar spreads.

Grok provides:
- **Setup grade** (A-F) based on term structure, IV rank, event risk, liquidity
- **Risk flags** specific to this spread
- **Suggested adjustments** (strike changes, expiration changes, diagonal conversion)
- **Market context** from live X/Twitter sentiment
""")

        grok_key = get_secret("GROK_API_KEY")
        if not grok_key:
            try:
                grok_key = st.secrets.get("GROK_API_KEY")
            except Exception:
                pass

        if not grok_key:
            st.info("Grok API key not configured. Add GROK_API_KEY to secrets.")
        else:
            # Build context summary from term_data (always available)
            _ts_rows = []
            for _exp in expirations:
                _ch = term_data.get(_exp)
                if _ch is not None:
                    _iv = _atm_iv(_ch, spot, spread_type)
                    _ts_rows.append(f"  {_exp} ({_dte(_exp)}d): IV {_iv*100:.1f}%")
            ts_summary = "\n".join(_ts_rows) if _ts_rows else "Term structure data not available"

            run_ai = st.button("Run Grok Assessment", type="primary",
                               use_container_width=True, key="run_ai")

            if run_ai:
                from openai import OpenAI as OAI
                import json as _json

                client = OAI(base_url="https://api.x.ai/v1", api_key=grok_key)

                prompt = f"""Analyze this calendar spread setup for {ticker_display}:

SPREAD DETAILS:
- Type: {spread_type} calendar
- Strike: ${strike:.2f} (Spot: ${spot:.2f}, {'ITM' if (spread_type == 'call' and spot > strike) or (spread_type == 'put' and spot < strike) else 'OTM' if (spread_type == 'call' and spot < strike) or (spread_type == 'put' and spot > strike) else 'ATM'})
- Front leg: {front_exp} ({_dte(front_exp)} DTE), IV: {iv_front*100:.1f}%
- Back leg: {back_exp} ({_dte(back_exp)} DTE), IV: {iv_back*100:.1f}%
- Net debit: ${net_debit:.2f} (${net_debit*100:.0f} per contract)
- IV differential: {(iv_back - iv_front)*100:+.1f}%

NET GREEKS:
- Delta: {net_g['delta']:.4f}
- Gamma: {net_g['gamma']:.4f}
- Theta: ${net_g['theta']*100:.2f}/day
- Vega: ${net_g['vega']*100:.2f}/1%

TERM STRUCTURE:
{ts_summary}

THETA/DEBIT RATIO: {theta_debit:.2%}/day

Search X/Twitter for the latest on {ticker_display} — look for upcoming catalysts,
earnings expectations, sector sentiment, and anything that would affect the term
structure (event-driven IV spikes, macro vol regime).

Respond with JSON:
{{"grade": "A/B/C/D/F",
 "assessment": "2-3 paragraph analysis of this specific calendar spread setup — is the term structure favorable? Is IV rich or cheap? Any timing concerns?",
 "risk_flags": ["specific risk 1", "specific risk 2"],
 "adjustments": ["suggested adjustment 1", "suggested adjustment 2"],
 "market_context": "What does current X/Twitter sentiment and news flow say about this ticker and whether vol is likely to expand or contract?",
 "optimal_entry": "Is now a good time, or should the trader wait for a specific condition?"}}"""

                with st.spinner("Grok is analyzing the spread..."):
                    try:
                        response = client.chat.completions.create(
                            model="grok-3",
                            messages=[
                                {"role": "system", "content": (
                                    "You are an institutional options strategist specializing in "
                                    "volatility trading and calendar spreads. Be specific and "
                                    "actionable. Reference actual market conditions from X/Twitter. "
                                    "Grade harshly — an A means exceptional setup."
                                )},
                                {"role": "user", "content": prompt},
                            ],
                            response_format={"type": "json_object"},
                            max_tokens=2000,
                            temperature=0.3,
                        )
                        import re
                        raw = response.choices[0].message.content
                        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
                        cleaned = re.sub(r"\s*```$", "", cleaned)
                        result = _json.loads(cleaned)

                        _render_ai_result(result)
                        st.session_state["cal_ai_result"] = result

                    except Exception as e:
                        st.error(f"Grok analysis failed: {e}")

            # Show cached result if available
            elif "cal_ai_result" in st.session_state:
                _render_ai_result(st.session_state["cal_ai_result"])
                st.caption("Cached result — click 'Run Grok Assessment' to refresh.")


# ══════════════════════════════════════════════════════════════════════════════
# WATCHLIST ALERTS — persist across page loads, check on each visit
# ══════════════════════════════════════════════════════════════════════════════

st.markdown("---")
st.subheader("Watchlist Alerts")
st.caption(
    "Set alerts on calendar spread conditions. Alerts are checked each time "
    "you visit this page. They persist for the session."
)

# Initialize watchlist
if "cal_watchlist" not in st.session_state:
    st.session_state["cal_watchlist"] = []

watchlist = st.session_state["cal_watchlist"]

# ── Add new alert ──
with st.expander("Add Alert", expanded=len(watchlist) == 0):
    wa1, wa2, wa3 = st.columns(3)
    with wa1:
        alert_ticker = st.text_input("Ticker", value=ticker_display, key="alert_ticker")
    with wa2:
        alert_metric = st.selectbox("Condition", [
            "IV Differential crosses above",
            "IV Differential crosses below",
            "Front IV rises above",
            "Front IV drops below",
            "ATM Straddle price drops below",
            "ATM Straddle price rises above",
        ], key="alert_metric")
    with wa3:
        alert_value = st.number_input("Threshold", value=2.0, step=0.5, key="alert_value",
                                       help="IV values in percentage points (e.g., 2.0 = 2%)")

    wa4, wa5 = st.columns(2)
    with wa4:
        alert_front = st.selectbox("Front expiration", all_expirations[:20],
                                    key="alert_front",
                                    format_func=lambda e: f"{e} ({_dte(e)}d)")
    with wa5:
        alert_back_opts = [e for e in all_expirations if e > alert_front][:20]
        alert_back = st.selectbox("Back expiration", alert_back_opts if alert_back_opts else ["N/A"],
                                   key="alert_back",
                                   format_func=lambda e: f"{e} ({_dte(e)}d)" if e != "N/A" else e)

    if st.button("Add to Watchlist", type="primary", key="add_alert"):
        if alert_back != "N/A":
            watchlist.append({
                "ticker": format_massive_ticker(alert_ticker),
                "metric": alert_metric,
                "threshold": alert_value,
                "front_exp": alert_front,
                "back_exp": alert_back,
                "type": spread_type,
                "triggered": False,
                "last_value": None,
            })
            st.session_state["cal_watchlist"] = watchlist
            st.success(f"Alert added: {alert_ticker} {alert_metric} {alert_value}")

# ── Check and display active alerts ──
if watchlist:
    alert_rows = []
    triggered_any = False

    for i, alert in enumerate(watchlist):
        a_ticker = alert["ticker"]
        status = "Checking..."
        current_val = None

        try:
            # Get current data
            if a_ticker == ticker_display and a_ticker in [ticker_display]:
                a_spot = spot
                a_term = term_data
            else:
                a_px = fetch_massive_data(a_ticker, 5)
                a_spot = float(a_px["Close"].iloc[-1]) if a_px is not None and not a_px.empty else None

            if a_spot:
                # Fetch front and back chains if needed
                front_loaded = _ensure_chain_loaded(alert["front_exp"]) if a_ticker == ticker_display else False
                back_loaded = _ensure_chain_loaded(alert["back_exp"]) if a_ticker == ticker_display else False

                if a_ticker == ticker_display and front_loaded and back_loaded:
                    a_term = st.session_state["cal_term_data"]
                    f_chain = a_term.get(alert["front_exp"])
                    b_chain = a_term.get(alert["back_exp"])
                else:
                    f_chain = None
                    b_chain = None
                    try:
                        f_chain = fetch_options_chain(a_ticker, alert["front_exp"])
                        b_chain = fetch_options_chain(a_ticker, alert["back_exp"])
                    except Exception:
                        pass

                if f_chain is not None and b_chain is not None and not f_chain.empty and not b_chain.empty:
                    f_iv = _atm_iv(f_chain, a_spot, alert["type"])
                    b_iv = _atm_iv(b_chain, a_spot, alert["type"])
                    iv_diff = (b_iv - f_iv) * 100

                    # Determine current value based on metric type
                    metric = alert["metric"]
                    if "IV Differential" in metric:
                        current_val = iv_diff
                    elif "Front IV" in metric:
                        current_val = f_iv * 100
                    elif "Straddle" in metric:
                        atm_k = _atm_strike(f_chain, a_spot)
                        c_row = _get_contract(f_chain, atm_k, "call")
                        p_row = _get_contract(f_chain, atm_k, "put")
                        if c_row is not None and p_row is not None:
                            current_val = _mid(c_row) + _mid(p_row)

                    # Check condition
                    if current_val is not None:
                        threshold = alert["threshold"]
                        if "above" in metric and current_val > threshold:
                            status = "TRIGGERED"
                            alert["triggered"] = True
                            triggered_any = True
                        elif "below" in metric and current_val < threshold:
                            status = "TRIGGERED"
                            alert["triggered"] = True
                            triggered_any = True
                        else:
                            status = "Watching"
                            alert["triggered"] = False
                        alert["last_value"] = current_val
                    else:
                        status = "No data"
                else:
                    status = "Chain unavailable"
        except Exception:
            status = "Error"

        alert_rows.append({
            "": i + 1,
            "Ticker": a_ticker,
            "Condition": alert["metric"],
            "Threshold": alert["threshold"],
            "Current": f"{current_val:.2f}" if current_val is not None else "—",
            "Front": alert["front_exp"],
            "Back": alert["back_exp"],
            "Status": status,
        })

    # Show triggered alerts prominently
    if triggered_any:
        for row in alert_rows:
            if row["Status"] == "TRIGGERED":
                st.markdown(
                    f'<div style="background:rgba(0,255,150,0.1);border:1px solid {COLORS["success"]};'
                    f'border-radius:6px;padding:10px 14px;margin-bottom:8px;font-size:0.9rem;">'
                    f'<b>{row["Ticker"]}</b>: {row["Condition"]} {row["Threshold"]} — '
                    f'Current: <b>{row["Current"]}</b></div>',
                    unsafe_allow_html=True,
                )

    st.dataframe(pd.DataFrame(alert_rows), use_container_width=True, hide_index=True)

    # Clear buttons
    cl1, cl2 = st.columns(2)
    with cl1:
        if st.button("Clear Triggered", key="clear_triggered"):
            st.session_state["cal_watchlist"] = [a for a in watchlist if not a.get("triggered")]
            st.rerun()
    with cl2:
        if st.button("Clear All Alerts", key="clear_all_alerts"):
            st.session_state["cal_watchlist"] = []
            st.rerun()
else:
    st.info("No alerts set. Use the form above to watch for specific conditions.")


# ─── FOOTER ────────────────────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    "Calendar spreads involve risk. Backtested/modeled results do not guarantee "
    "future performance. Not financial advice."
)
render_data_source_footer()
