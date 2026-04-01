"""Iron Condor Scanner — finds the best short iron condor setups across a universe of tickers."""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.layout import setup_page, error_boundary, fun_loader
from src.styles import COLORS
from src.data_engine import fetch_options_chain, polygon_batch_snapshot

logger = logging.getLogger(__name__)

setup_page("50_Iron_Condor_Scanner")

st.title("Iron Condor Scanner")
st.markdown("Scan a universe of tickers for the highest-quality short iron condor setups ranked by credit, probability of profit, and IV percentile.")


# ═══════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════

DEFAULT_UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META",
    "JPM", "GS", "XLE", "GLD", "TLT", "EEM", "HYG", "XLF", "XLK", "DIA",
]

with st.container(border=True):
    cc1, cc2, cc3, cc4 = st.columns(4)
    with cc1:
        target_dte_min = st.number_input("Min DTE", value=25, min_value=7, max_value=90, step=5)
    with cc2:
        target_dte_max = st.number_input("Max DTE", value=50, min_value=14, max_value=120, step=5)
    with cc3:
        short_delta = st.number_input("Short strike delta", value=0.16, min_value=0.05, max_value=0.40, step=0.02, format="%.2f",
                                       help="Delta for short legs. 0.16 ≈ 1σ (84% POP). Lower = wider/safer, less credit.")
    with cc4:
        wing_width = st.number_input("Wing width ($)", value=5, min_value=1, max_value=50, step=1,
                                      help="Distance between short and long strikes. Wider = more credit but more risk.")

    ticker_input = st.text_area("Tickers (comma-separated)", value=", ".join(DEFAULT_UNIVERSE), height=68)
    tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]

    scan = st.button("Scan for Iron Condors", type="primary", use_container_width=True)


# ═══════════════════════════════════════════════
# IRON CONDOR FINDER
# ═══════════════════════════════════════════════

def _find_best_condor(chain: pd.DataFrame, spot: float, dte_min: int, dte_max: int,
                       target_delta: float, width: float) -> dict | None:
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

    # Find long legs
    long_put_row = puts[puts["strike_price"] == lp_strike]
    long_call_row = calls[calls["strike_price"] == lc_strike]

    # If exact strikes don't exist, find closest
    if long_put_row.empty:
        long_put_row = puts[(puts["strike_price"] >= lp_strike - 1) & (puts["strike_price"] <= lp_strike + 1)]
    if long_call_row.empty:
        long_call_row = calls[(calls["strike_price"] >= lc_strike - 1) & (calls["strike_price"] <= lc_strike + 1)]

    if long_put_row.empty or long_call_row.empty:
        return None

    long_put = long_put_row.iloc[0]
    long_call = long_call_row.iloc[0]

    # Calculate credit and risk (handle NaN/zero bids)
    def _mid(row):
        b = row.get("bid", 0) or 0
        a = row.get("ask", 0) or 0
        lp = row.get("last_price", 0) or 0
        mid = (b + a) / 2 if (b > 0 and a > 0) else lp
        return mid if mid > 0 else 0

    sp_mid = _mid(short_put)
    sc_mid = _mid(short_call)
    lp_mid = _mid(long_put)
    lc_mid = _mid(long_call)

    credit = (sp_mid + sc_mid) - (lp_mid + lc_mid)
    if credit <= 0.01:
        return None

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

    # Theta collected (daily decay, handle NaN)
    def _safe_theta(row):
        v = row.get("theta", 0)
        return abs(float(v)) if v and not np.isnan(float(v)) else 0
    theta_collected = _safe_theta(short_put) + _safe_theta(short_call) \
                    - _safe_theta(long_put) - _safe_theta(long_call)

    # Composite score: credit/risk ratio × POP × IV boost
    score = (credit / max_risk) * pop * (1 + avg_iv)

    return {
        "expiration": best_exp,
        "dte": actual_dte,
        "spot": spot,
        # Legs
        "short_put": sp_strike,
        "long_put": long_put["strike_price"],
        "short_call": sc_strike,
        "long_call": long_call["strike_price"],
        # Economics
        "credit": round(credit, 2),
        "credit_100": round(credit * 100, 0),
        "max_risk": round(max_risk, 2),
        "max_risk_100": round(max_risk * 100, 0),
        "return_on_risk": round(credit / max_risk * 100, 1),
        # Probability
        "pop": round(pop * 100, 1),
        "ev_per_contract": round(ev * 100, 0),
        # Greeks
        "avg_iv": round(avg_iv * 100, 1),
        "sp_delta": round(sp_delta, 3),
        "sc_delta": round(sc_delta, 3),
        "net_theta": round(theta_collected, 4),
        # Score
        "score": round(score, 4),
    }


def _scan_ticker(ticker: str, dte_min: int, dte_max: int, delta: float, width: float) -> dict | None:
    """Scan a single ticker for best iron condor. Returns result dict or None."""
    try:
        chain = fetch_options_chain(ticker)
        if chain is None or chain.empty:
            return None

        # Get spot price from chain or snapshot
        snap = polygon_batch_snapshot([ticker])
        spot = snap.get(ticker, {}).get("price")
        if not spot:
            return None

        result = _find_best_condor(chain, spot, dte_min, dte_max, delta, width)
        if result:
            result["ticker"] = ticker
        return result
    except Exception as e:
        logger.warning(f"Iron condor scan failed for {ticker}: {e}")
        return None


# ═══════════════════════════════════════════════
# SCAN EXECUTION
# ═══════════════════════════════════════════════

if scan and tickers:
    results = []
    with fun_loader("data"):
        progress = st.progress(0, text="Scanning tickers...")
        with ThreadPoolExecutor(max_workers=5) as ex:
            futures = {ex.submit(_scan_ticker, tk, target_dte_min, target_dte_max, short_delta, wing_width): tk for tk in tickers}
            done = 0
            for fut in as_completed(futures):
                result = fut.result()
                if result:
                    results.append(result)
                done += 1
                progress.progress(done / len(tickers), text=f"Scanned {done}/{len(tickers)} tickers...")
        progress.empty()

    if results:
        st.session_state["ic_results"] = results
    else:
        st.warning("No valid iron condor setups found. Try relaxing the delta or DTE range.")


# ═══════════════════════════════════════════════
# RESULTS
# ═══════════════════════════════════════════════

if "ic_results" in st.session_state and st.session_state["ic_results"]:
    results = sorted(st.session_state["ic_results"], key=lambda r: r["score"], reverse=True)

    st.markdown(f"##### Found {len(results)} setups, ranked by composite score")

    # ── Summary Table ──
    with error_boundary("Results Table"):
        table_rows = []
        for r in results:
            table_rows.append({
                "Ticker": r["ticker"],
                "Exp": r["expiration"],
                "DTE": r["dte"],
                "Short Put": r["short_put"],
                "Short Call": r["short_call"],
                "Credit": f"${r['credit_100']:,.0f}",
                "Max Risk": f"${r['max_risk_100']:,.0f}",
                "Return/Risk": f"{r['return_on_risk']}%",
                "POP": f"{r['pop']}%",
                "EV/Contract": f"${r['ev_per_contract']:+,.0f}",
                "Avg IV": f"{r['avg_iv']}%",
                "Net Theta": f"${r['net_theta'] * 100:+.1f}/day",
                "Score": f"{r['score']:.3f}",
            })
        df_results = pd.DataFrame(table_rows)
        st.dataframe(df_results, use_container_width=True, hide_index=True)

    # ── Top Picks Detail Cards ──
    st.markdown("##### Top Setups")

    for r in results[:5]:
        with st.container(border=True):
            with error_boundary(f"Detail {r['ticker']}"):
                h1, h2, h3, h4, h5 = st.columns(5)
                h1.metric(r["ticker"], f"${r['spot']:,.2f}")
                h2.metric("Credit", f"${r['credit_100']:,.0f}")
                h3.metric("Max Risk", f"${r['max_risk_100']:,.0f}")
                h4.metric("POP", f"{r['pop']}%")
                h5.metric("Return/Risk", f"{r['return_on_risk']}%")

                # Leg diagram
                lp = r["long_put"]
                sp = r["short_put"]
                sc = r["short_call"]
                lc = r["long_call"]
                r_spot = r["spot"]

                st.markdown(
                    f'<div style="display:flex;align-items:center;justify-content:center;gap:4px;'
                    f'font-size:0.78rem;padding:8px 0;font-family:monospace;">'
                    f'<span style="color:{COLORS["danger"]};border:1px solid {COLORS["danger"]};padding:2px 8px;border-radius:4px;">'
                    f'Buy ${lp:.0f}P</span>'
                    f'<span style="color:#888;">—</span>'
                    f'<span style="color:{COLORS["warning"]};border:1px solid {COLORS["warning"]};padding:2px 8px;border-radius:4px;">'
                    f'Sell ${sp:.0f}P</span>'
                    f'<span style="color:#888;">···${r_spot:.0f}···</span>'
                    f'<span style="color:{COLORS["warning"]};border:1px solid {COLORS["warning"]};padding:2px 8px;border-radius:4px;">'
                    f'Sell ${sc:.0f}C</span>'
                    f'<span style="color:#888;">—</span>'
                    f'<span style="color:{COLORS["danger"]};border:1px solid {COLORS["danger"]};padding:2px 8px;border-radius:4px;">'
                    f'Buy ${lc:.0f}C</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                # P&L at expiration payoff diagram
                prices = np.linspace(lp - 5, lc + 5, 200)
                credit = r["credit"]
                pnl = np.zeros_like(prices)
                for i, px in enumerate(prices):
                    short_put_pnl = -max(sp - px, 0)
                    long_put_pnl = max(lp - px, 0)
                    short_call_pnl = -max(px - sc, 0)
                    long_call_pnl = max(px - lc, 0)
                    pnl[i] = (credit + short_put_pnl + long_put_pnl + short_call_pnl + long_call_pnl) * 100

                fig_pnl = go.Figure()
                fig_pnl.add_trace(go.Scatter(
                    x=prices, y=pnl,
                    fill="tozeroy",
                    fillcolor="rgba(0,255,150,0.08)",
                    line=dict(color=COLORS["success"], width=2),
                    hovertemplate="Price: $%{x:.1f}<br>P&L: $%{y:,.0f}<extra></extra>",
                ))
                # Color the loss region
                fig_pnl.add_trace(go.Scatter(
                    x=prices, y=np.where(pnl < 0, pnl, 0),
                    fill="tozeroy",
                    fillcolor="rgba(255,68,68,0.12)",
                    line=dict(color=COLORS["danger"], width=0),
                    hoverinfo="skip", showlegend=False,
                ))
                fig_pnl.add_hline(y=0, line_dash="dot", line_color="#555", line_width=1)
                fig_pnl.add_vline(x=r_spot, line_dash="dash", line_color=COLORS["accent"], line_width=1,
                                   annotation_text="Spot", annotation_position="top")
                fig_pnl.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=250,
                    margin=dict(l=50, r=20, t=30, b=40),
                    xaxis_title="Price at Expiration ($)",
                    yaxis_title="P&L ($)",
                    showlegend=False,
                )
                st.plotly_chart(fig_pnl, use_container_width=True)

                # Details row
                d1, d2, d3, d4 = st.columns(4)
                d1.caption(f"Exp: {r['expiration']} ({r['dte']}d)")
                d2.caption(f"IV: {r['avg_iv']}%")
                d3.caption(f"Theta: ${r['net_theta'] * 100:+.1f}/day")
                d4.caption(f"EV: ${r['ev_per_contract']:+,.0f}/contract")

elif not scan:
    st.info("Enter tickers and click **Scan for Iron Condors** to find the best setups.")
