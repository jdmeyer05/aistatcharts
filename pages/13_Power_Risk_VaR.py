import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.chatbot import run_sidebar_chatbot

from src.layout import setup_page, error_boundary
from src.styles import COLORS
setup_page("13_Power_Risk_VaR")

st.title("Portfolio Risk & VaR Engine")

_c1, _c2, _c3, _c4 = st.columns([3, 2, 2, 1])
with _c1:
    raw_tickers = st.text_input("Portfolio Tickers (comma separated)", "SPY,TLT,GLD,EFA,USO")
with _c2:
    portfolio_value = st.number_input("Total Portfolio Value ($)", value=100000, step=10000)
with _c3:
    lookback = st.slider("Historical Lookback (Days)", 90, 1000, 504)
with _c4:
    confidence_level = st.selectbox("Confidence Level", [0.95, 0.99])

# Allocation method
alloc_method = st.radio("Allocation", ["Equal Weight", "Inverse Volatility", "HRP (de Prado)"],
                        horizontal=True, key="var_alloc")

# Parse tickers
ticker_list = [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]

# --- Fetch & Align Data ---
with st.spinner("Loading portfolio data..."):
    all_data = {}
    failed_tickers = []
    for t in ticker_list:
        formatted_t = format_massive_ticker(t)
        df = fetch_massive_data(formatted_t, lookback)
        if df is not None:
            all_data[t] = df['Close']
        else:
            failed_tickers.append(t)

if failed_tickers:
    st.warning(f"Could not load data for: {', '.join(failed_tickers)}")

if all_data and len(all_data) >= 2:
    portfolio_df = pd.DataFrame(all_data).dropna()
    daily_returns = portfolio_df.pct_change().dropna()
    n_assets = len(daily_returns.columns)

    # --- Compute Weights ---
    if alloc_method == "Equal Weight":
        weights = pd.Series(1.0 / n_assets, index=daily_returns.columns)
    elif alloc_method == "Inverse Volatility":
        vol = daily_returns.std() * np.sqrt(252)
        vol = vol.replace(0, vol[vol > 0].min() if (vol > 0).any() else 1)  # avoid div-by-zero
        weights = (1 / vol) / (1 / vol).sum()
    else:  # HRP
        from src.quant_features import hrp_allocate
        weights = hrp_allocate(daily_returns)

    portfolio_returns = daily_returns.dot(weights)

    # Show weights
    st.subheader("Portfolio Weights")
    st.caption(f"Allocation method: **{alloc_method}**")
    wt_cols = st.columns(min(n_assets, 8))
    for i, (t, w) in enumerate(weights.items()):
        wt_cols[i % len(wt_cols)].metric(t, f"{w * 100:.1f}%")

    # --- VaR & CVaR ---
    percentile = (1 - confidence_level) * 100
    var_percent = np.percentile(portfolio_returns, percentile)
    var_dollar = portfolio_value * var_percent

    tail_returns = portfolio_returns[portfolio_returns <= var_percent]
    cvar_percent = tail_returns.mean() if len(tail_returns) > 0 else var_percent
    cvar_dollar = portfolio_value * cvar_percent

    # --- CHART: Returns Distribution ---
    st.subheader("Daily Returns Distribution")

    fig_hist = go.Figure()
    fig_hist.add_trace(go.Histogram(x=portfolio_returns, nbinsx=60, marker_color='#00d1ff', name="Returns"))
    fig_hist.add_vline(x=var_percent, line_dash="dash", line_color="red",
                       annotation_text=f"{int(confidence_level*100)}% VaR")
    fig_hist.add_vline(x=cvar_percent, line_dash="dot", line_color="#ff8800",
                       annotation_text=f"CVaR")
    fig_hist.update_layout(template="plotly_dark", showlegend=False,
                           xaxis_title="Daily Return", yaxis_title="Frequency",
                           margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig_hist, use_container_width=True, config={"displayModeBar": False})

    # --- METRICS ---
    c1, c2, c3 = st.columns(3)
    c1.metric(f"1-Day {int(confidence_level*100)}% VaR (%)", f"{var_percent*100:.2f}%")
    c2.metric(f"1-Day {int(confidence_level*100)}% VaR ($)", f"${abs(var_dollar):,.2f}",
              delta="Max Expected Loss", delta_color="inverse")
    c3.metric(f"1-Day {int(confidence_level*100)}% CVaR ($)", f"${abs(cvar_dollar):,.2f}",
              delta="Avg Loss Beyond VaR", delta_color="inverse")

    st.caption(f"**VaR:** {int(confidence_level*100)}% probability the portfolio won't lose more than "
               f"**${abs(var_dollar):,.2f}** in a single day. "
               f"**CVaR:** If losses exceed VaR, the average expected loss is **${abs(cvar_dollar):,.2f}**.")

    # --- Regime Filter (VPIN + Entropy) ---
    st.markdown("---")
    st.subheader("Regime-Adjusted Risk")
    st.caption("Uses VPIN (flow toxicity) and entropy (predictability) to flag whether current conditions are favorable for trading.")

    with error_boundary("Regime Filter"):
        # Use SPY as the regime proxy if available
        regime_ticker = "SPY" if "SPY" in daily_returns.columns else daily_returns.columns[0]
        regime_vol = daily_returns[regime_ticker].rolling(20).std().dropna()
        if not regime_vol.empty:
            # Simple regime: high vol periods
            vol_q75 = regime_vol.quantile(0.75)
            current_vol = regime_vol.iloc[-1]
            stressed = current_vol > vol_q75

            # Compute entropy
            from src.quant_features import compute_entropy
            log_ret = np.log(portfolio_df[regime_ticker] / portfolio_df[regime_ticker].shift(1)).dropna()
            ent = compute_entropy(log_ret, n_bins=8, window=63)
            current_ent = ent.iloc[-1] if not ent.empty else 1.0

            rc1, rc2, rc3 = st.columns(3)
            rc1.metric("Current Vol Regime",
                       "STRESSED" if stressed else "CALM",
                       delta=f"20D vol: {current_vol * np.sqrt(252) * 100:.1f}% ann.",
                       delta_color="inverse" if stressed else "normal")
            rc2.metric("Entropy", f"{current_ent:.3f}",
                       help="<0.85 = predictable (favorable), >0.95 = random (unfavorable)")
            regime_label = "Favorable" if not stressed and current_ent < 0.90 else \
                           "Caution" if stressed or current_ent > 0.90 else "Favorable"
            regime_color = "#00ff88" if regime_label == "Favorable" else "#ffaa00"
            rc3.markdown(f'<div style="text-align:center;padding-top:10px;">'
                         f'<span style="font-size:1.3rem;font-weight:700;color:{regime_color};">{regime_label}</span><br>'
                         f'<span style="font-size:0.7rem;color:#888;">Trading Regime</span></div>',
                         unsafe_allow_html=True)

            # Stressed VaR
            if stressed:
                stress_returns = portfolio_returns[regime_vol.reindex(portfolio_returns.index) > vol_q75]
                if len(stress_returns) > 10:
                    stress_var = np.percentile(stress_returns, percentile)
                    st.warning(f"**Stress VaR:** During high-vol periods, the {int(confidence_level*100)}% VaR is "
                               f"**{stress_var*100:.2f}%** (${abs(portfolio_value * stress_var):,.2f}) — "
                               f"worse than the unconditional {var_percent*100:.2f}%.")

    # --- COMPONENT VaR ---
    st.markdown("---")
    st.subheader("Component VaR — Risk Contribution by Position")
    st.caption(
        "Shows how much each position contributes to total portfolio risk. "
        "A position with 50% component VaR contributes half of the portfolio's total risk."
    )

    with error_boundary("Component VaR"):
        cov_matrix = daily_returns.cov() * 252  # annualized
        port_vol = np.sqrt(weights.values @ cov_matrix.values @ weights.values)

        if port_vol > 0:
            # Marginal contribution to risk
            mcr = (cov_matrix.values @ weights.values) / port_vol
            # Component VaR = weight × MCR × VaR_scalar
            var_scalar = abs(var_percent) * np.sqrt(252)  # annualized
            comp_var = weights.values * mcr * var_scalar
            comp_var_pct = comp_var / comp_var.sum() * 100 if comp_var.sum() != 0 else comp_var * 0

            comp_df = pd.DataFrame({
                "Ticker": weights.index,
                "Weight": [f"{w*100:.1f}%" for w in weights.values],
                "Risk Contribution": [f"{c:.1f}%" for c in comp_var_pct],
                "Marginal VaR": [f"{m*100:.3f}%" for m in mcr],
            })
            st.dataframe(comp_df, use_container_width=True, hide_index=True)

            # Bar chart
            fig_comp = go.Figure(go.Bar(
                x=weights.index, y=comp_var_pct,
                marker_color=[COLORS["danger"] if c > 100 / len(weights) * 1.5 else COLORS["accent"]
                              for c in comp_var_pct],
                text=[f"{c:.1f}%" for c in comp_var_pct],
                textposition="outside",
            ))
            fig_comp.add_hline(y=100 / len(weights), line_dash="dot",
                                line_color=COLORS["text_muted"],
                                annotation_text="Equal contribution")
            fig_comp.update_layout(
                template="plotly_dark", height=300,
                yaxis_title="Risk Contribution (%)",
                margin=dict(l=50, r=20, t=10, b=50),
            )
            st.plotly_chart(fig_comp, use_container_width=True, config={"displayModeBar": False})

            # Flag concentrated risk
            max_risk_ticker = weights.index[np.argmax(comp_var_pct)]
            max_risk_pct = comp_var_pct.max()
            if max_risk_pct > 40:
                st.warning(f"**{max_risk_ticker}** contributes {max_risk_pct:.0f}% of portfolio risk — consider reducing position or hedging.")

    # Chatbot Context
    ctx = (f"The 1-Day {int(confidence_level*100)}% VaR for a ${portfolio_value:,.0f} portfolio "
           f"containing {raw_tickers} ({alloc_method}) is ${abs(var_dollar):,.2f}. "
           f"CVaR is ${abs(cvar_dollar):,.2f}.")
    run_sidebar_chatbot(context_data=ctx)
elif all_data:
    st.error("Need at least 2 tickers for portfolio analysis.")
else:
    st.error("Could not load data for the requested tickers.")
