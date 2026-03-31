import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from scipy.stats import norm
from datetime import date, timedelta
from src.data_engine import format_massive_ticker, fetch_massive_data
from src.api_keys import get_secret
from src.layout import setup_page, fun_loader

logger = logging.getLogger(__name__)

setup_page("08_Options_Lab")

st.title("🧫 Options Lab")
st.markdown("Volatility surface, earnings move analyzer, and multi-leg strategy modeler with time decay.")


_get_massive_key = lambda: get_secret("MASSIVE_API_KEY")


from src.data_engine import fetch_options_chain as _fetch_chain_raw

def fetch_chain_all_exps(symbol: str, api_key: str = None):
    """Fetch options chain across ALL expirations — delegates to data_engine."""
    df = _fetch_chain_raw(symbol, expiration=None, max_pages=30)
    if not df.empty:
        col_map = {
            "strike_price": "strike", "contract_type": "type",
            "expiration_date": "expiration", "implied_volatility": "iv",
            "last_price": "close",
        }
        df = df.rename(columns=col_map)
    return df


def bs_price(S, K, T, r, sigma, opt_type="call"):
    """Black-Scholes option price."""
    if T <= 0:
        return max(S - K, 0) if opt_type == "call" else max(K - S, 0)
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if opt_type == "call":
        return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
    return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)


api_key = _get_massive_key()

# --- Controls ---
from src.layout import get_active_ticker, set_active_ticker
_c1, _c2 = st.columns([3, 1])
with _c1:
    raw_ticker = st.text_input("Ticker", value=get_active_ticker())
    ticker = format_massive_ticker(raw_ticker)
    set_active_ticker(ticker)
with _c2:
    st.markdown("<br>", unsafe_allow_html=True)
    submit = st.button("Load Data", type="primary", use_container_width=True)

if not api_key:
    st.error("Massive API key not configured.")
    st.stop()

# --- FETCH & STORE ---
if submit:
    with fun_loader("data"):
        df_all = fetch_chain_all_exps(ticker, api_key)
        px_df = fetch_massive_data(ticker, 252)
        spot = px_df["Close"].iloc[-1] if px_df is not None and not px_df.empty else None

        if not df_all.empty:
            st.session_state["lab_chain"] = df_all
            st.session_state["lab_spot"] = spot
            st.session_state["lab_ticker"] = ticker
            st.session_state["lab_px"] = px_df
            st.session_state["shared_options_ticker"] = ticker
            st.session_state["shared_options_spot"] = spot
        else:
            st.error("No options data returned.")
            st.stop()

# --- RENDER ---
if "lab_chain" not in st.session_state:
    st.info("Enter a ticker and click **Load Data** to begin.")
    st.stop()

df_all = st.session_state["lab_chain"]
spot = st.session_state["lab_spot"]
ticker_display = st.session_state["lab_ticker"]
px_df = st.session_state["lab_px"]

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Volatility Surface",
    "Earnings Move Analyzer",
    "Strategy P&L Modeler",
    "BS Pricing & Greeks",
    "Strategy Optimizer",
])


# ---- TAB 1: 3D Volatility Surface ----
with tab1:
    st.subheader("Implied Volatility Surface")

    # Filter to calls with meaningful data near the money
    df_surf = df_all[(df_all["type"] == "call") & (df_all["iv"] > 0) & (df_all["open_interest"] > 0)].copy()

    if not df_surf.empty and spot:
        df_surf["dte"] = (pd.to_datetime(df_surf["expiration"]) - pd.Timestamp.now()).dt.days
        df_surf = df_surf[(df_surf["dte"] > 0) & (df_surf["dte"] <= 365)]
        df_surf = df_surf[(df_surf["strike"] >= spot * 0.8) & (df_surf["strike"] <= spot * 1.2)]
        # Cap IV outliers
        iv_cap = df_surf["iv"].quantile(0.95)
        df_surf["iv"] = df_surf["iv"].clip(upper=iv_cap)

        if len(df_surf) > 20:
            fig_3d = go.Figure(data=[go.Mesh3d(
                x=df_surf["strike"],
                y=df_surf["dte"],
                z=df_surf["iv"],
                intensity=df_surf["iv"],
                colorscale="Turbo",
                opacity=0.8,
                hovertemplate="Strike: $%{x}<br>DTE: %{y}<br>IV: %{z:.1%}<extra></extra>",
            )])

            fig_3d.update_layout(
                template="plotly_dark", height=600,
                margin=dict(t=10, b=0, l=0, r=0),
                scene=dict(
                    xaxis_title="Strike ($)",
                    yaxis_title="Days to Expiration",
                    zaxis_title="Implied Volatility",
                ),
            )
            st.plotly_chart(fig_3d, use_container_width=True)

            # 2D Term Structure
            st.subheader("IV Term Structure (ATM)")
            atm_range = (spot * 0.98, spot * 1.02)
            df_atm = df_surf[(df_surf["strike"] >= atm_range[0]) & (df_surf["strike"] <= atm_range[1])]
            term_struct = df_atm.groupby("dte")["iv"].mean().sort_index()

            if not term_struct.empty:
                fig_term = go.Figure()
                fig_term.add_trace(go.Scatter(
                    x=term_struct.index, y=term_struct.values,
                    mode="lines+markers", line=dict(color="#00d1ff", width=2),
                    hovertemplate="DTE: %{x}<br>IV: %{y:.1%}<extra></extra>",
                ))
                fig_term.update_layout(
                    template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                    xaxis_title="Days to Expiration", yaxis_title="ATM Implied Volatility",
                    hovermode="x unified",
                )
                st.plotly_chart(fig_term, use_container_width=True)

            # Skew by expiration
            st.subheader("IV Skew by Expiration")
            exp_list = sorted(df_surf["expiration"].unique())[:6]  # First 6 expirations
            colors_skew = ["#ff4b4b", "#00d1ff", "#00ff96", "#ffaa00", "#ad7fff", "#ff69b4"]

            fig_skew = go.Figure()
            for i, exp in enumerate(exp_list):
                exp_data = df_surf[df_surf["expiration"] == exp].sort_values("strike")
                fig_skew.add_trace(go.Scatter(
                    x=exp_data["strike"], y=exp_data["iv"],
                    mode="lines", name=exp,
                    line=dict(color=colors_skew[i % len(colors_skew)], width=2),
                ))

            if spot:
                fig_skew.add_vline(x=spot, line_dash="dot", line_color="#ffaa00")
            fig_skew.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Strike", yaxis_title="Implied Volatility",
                xaxis=dict(range=[spot * 0.85, spot * 1.15]) if spot else {},
                hovermode="x unified",
            )
            st.plotly_chart(fig_skew, use_container_width=True)
            # ── Note about daily animation ──
            st.divider()
            st.caption(
                "For a **daily IV surface replay** with real historical snapshots, "
                "use the **Vol Surface** page (Surface Animation tab). "
                "Each time you load that page a snapshot is saved — "
                "after 2+ days you'll see the surface change day-over-day."
            )

        else:
            st.warning("Not enough data points for surface plot.")
    else:
        st.warning("No IV data available.")


# ---- TAB 2: Earnings Move Analyzer ----
with tab2:
    st.subheader("Earnings Implied vs Historical Moves")
    st.markdown("Compare the **implied move** from options pricing with **actual historical moves** on earnings days.")

    if px_df is not None and spot and not df_all.empty:
        # Calculate historical daily returns
        px_close = px_df["Close"]
        returns = px_close.pct_change().dropna()

        # Historical stats
        hist_vol = returns.std() * np.sqrt(252)
        avg_daily_move = returns.abs().mean() * 100
        max_daily_move = returns.abs().max() * 100

        # Implied move from nearest ATM straddle
        nearest_exps = sorted(df_all["expiration"].unique())[:3]
        implied_moves = []
        for exp in nearest_exps:
            exp_calls = df_all[(df_all["expiration"] == exp) & (df_all["type"] == "call")]
            exp_puts = df_all[(df_all["expiration"] == exp) & (df_all["type"] == "put")]
            if not exp_calls.empty and not exp_puts.empty:
                atm_call = exp_calls.iloc[(exp_calls["strike"] - spot).abs().argsort()[:1]]
                atm_put = exp_puts.iloc[(exp_puts["strike"] - spot).abs().argsort()[:1]]
                straddle_price = atm_call["close"].values[0] + atm_put["close"].values[0]
                implied_move_pct = (straddle_price / spot) * 100
                dte = (pd.to_datetime(exp) - pd.Timestamp.now()).days
                implied_moves.append({
                    "expiration": exp,
                    "dte": dte,
                    "straddle_price": straddle_price,
                    "implied_move_pct": implied_move_pct,
                })

        # Metrics
        ec1, ec2, ec3, ec4 = st.columns(4)
        ec1.metric("Historical Volatility (1Y)", f"{hist_vol:.1%}")
        ec2.metric("Avg Daily Move", f"{avg_daily_move:.2f}%")
        ec3.metric("Max Daily Move (1Y)", f"{max_daily_move:.2f}%")
        if implied_moves:
            ec4.metric(f"Implied Move ({implied_moves[0]['expiration']})",
                       f"{implied_moves[0]['implied_move_pct']:.2f}%")

        st.divider()

        # Implied moves table
        if implied_moves:
            st.subheader("Straddle-Implied Moves by Expiration")
            df_impl = pd.DataFrame(implied_moves)
            df_impl_display = df_impl.copy()
            df_impl_display["straddle_price"] = df_impl_display["straddle_price"].apply(lambda x: f"${x:.2f}")
            df_impl_display["implied_move_pct"] = df_impl_display["implied_move_pct"].apply(lambda x: f"{x:.2f}%")
            df_impl_display.columns = ["Expiration", "DTE", "Straddle Price", "Implied Move (%)"]
            st.dataframe(df_impl_display, use_container_width=True, hide_index=True)

        # Historical return distribution
        st.subheader("Historical Return Distribution (1Y)")
        fig_dist = go.Figure()
        fig_dist.add_trace(go.Histogram(
            x=returns * 100,
            nbinsx=80,
            marker_color="#00d1ff",
            opacity=0.8,
            hovertemplate="Return: %{x:.2f}%<br>Count: %{y}<extra></extra>",
        ))

        # Overlay implied move range if available
        if implied_moves:
            im = implied_moves[0]["implied_move_pct"]
            fig_dist.add_vline(x=im, line_dash="dash", line_color="#00ff96",
                               annotation_text=f"+{im:.1f}% implied")
            fig_dist.add_vline(x=-im, line_dash="dash", line_color="#ff4b4b",
                               annotation_text=f"-{im:.1f}% implied")

        fig_dist.add_vline(x=0, line_color="white", line_width=1)
        fig_dist.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Daily Return (%)", yaxis_title="Frequency",
            hovermode="x unified",
        )
        st.plotly_chart(fig_dist, use_container_width=True)

        # Largest moves table
        st.subheader("Largest 1-Day Moves (Past Year)")
        top_moves = returns.abs().nlargest(10)
        df_moves = pd.DataFrame({
            "Date": top_moves.index.strftime("%Y-%m-%d"),
            "Return": [f"{returns.loc[d]*100:+.2f}%" for d in top_moves.index],
            "Absolute Move": [f"{v*100:.2f}%" for v in top_moves.values],
        })
        st.dataframe(df_moves, use_container_width=True, hide_index=True)
    else:
        st.warning("Load data first.")


# ---- TAB 3: Strategy P&L Modeler with Time Decay ----
with tab3:
    st.subheader("Multi-Leg Strategy Modeler")
    st.markdown("Build strategies with **live pricing** and see P&L across price and time.")

    if spot:
        strategy = st.selectbox("Strategy Template", [
            "Custom", "Long Straddle", "Long Strangle", "Iron Condor",
            "Bull Call Spread", "Bear Put Spread", "Butterfly",
        ])

        r_rate = 0.045

        # Auto-populate legs
        if strategy == "Long Straddle":
            default_legs = [
                {"type": "call", "strike": round(spot), "premium": 5.0, "pos": 1},
                {"type": "put", "strike": round(spot), "premium": 5.0, "pos": 1},
            ]
        elif strategy == "Long Strangle":
            default_legs = [
                {"type": "call", "strike": round(spot * 1.03), "premium": 3.0, "pos": 1},
                {"type": "put", "strike": round(spot * 0.97), "premium": 3.0, "pos": 1},
            ]
        elif strategy == "Iron Condor":
            default_legs = [
                {"type": "put", "strike": round(spot * 0.93), "premium": 1.0, "pos": 1},
                {"type": "put", "strike": round(spot * 0.97), "premium": 2.5, "pos": -1},
                {"type": "call", "strike": round(spot * 1.03), "premium": 2.5, "pos": -1},
                {"type": "call", "strike": round(spot * 1.07), "premium": 1.0, "pos": 1},
            ]
        elif strategy == "Bull Call Spread":
            default_legs = [
                {"type": "call", "strike": round(spot), "premium": 5.0, "pos": 1},
                {"type": "call", "strike": round(spot * 1.05), "premium": 2.5, "pos": -1},
            ]
        elif strategy == "Bear Put Spread":
            default_legs = [
                {"type": "put", "strike": round(spot), "premium": 5.0, "pos": 1},
                {"type": "put", "strike": round(spot * 0.95), "premium": 2.5, "pos": -1},
            ]
        elif strategy == "Butterfly":
            default_legs = [
                {"type": "call", "strike": round(spot * 0.97), "premium": 6.0, "pos": 1},
                {"type": "call", "strike": round(spot), "premium": 4.0, "pos": -2},
                {"type": "call", "strike": round(spot * 1.03), "premium": 2.5, "pos": 1},
            ]
        else:
            default_legs = [
                {"type": "call", "strike": round(spot), "premium": 5.0, "pos": 1},
            ]

        # Leg inputs
        st.caption("Adjust legs below:")
        legs = []
        leg_cols = st.columns(len(default_legs))
        for i, (col, dl) in enumerate(zip(leg_cols, default_legs)):
            with col:
                st.markdown(f"**Leg {i+1}**")
                action = st.selectbox("Action", ["Buy", "Sell"],
                                       index=0 if dl["pos"] > 0 else 1, key=f"strat_act_{i}")
                opt_type = st.selectbox("Type", ["call", "put"],
                                         index=0 if dl["type"] == "call" else 1, key=f"strat_type_{i}")
                strike = st.number_input("Strike", value=float(dl["strike"]), step=1.0, key=f"strat_k_{i}")
                premium = st.number_input("Premium", value=float(dl["premium"]), step=0.1, key=f"strat_p_{i}")
                qty = st.number_input("Qty", value=abs(dl["pos"]), min_value=1, key=f"strat_q_{i}")
                pos = qty if action == "Buy" else -qty
                legs.append({"type": opt_type, "strike": strike, "premium": premium, "pos": pos})

        dte = st.slider("Days to Expiration", 1, 90, 30)
        iv = st.slider("Implied Volatility (%)", 5, 100, 25) / 100

        # Calculate P&L grid: price × time
        prices = np.linspace(spot * 0.85, spot * 1.15, 200)
        days = np.arange(dte, -1, -1)

        # Expiration P&L
        total_pnl_exp = np.zeros_like(prices)
        net_cost = 0
        for leg in legs:
            if leg["type"] == "call":
                intrinsic = np.maximum(prices - leg["strike"], 0)
            else:
                intrinsic = np.maximum(leg["strike"] - prices, 0)
            total_pnl_exp += (intrinsic - leg["premium"]) * leg["pos"] * 100
            net_cost -= leg["premium"] * leg["pos"] * 100

        # Current P&L (with time value)
        total_pnl_now = np.zeros_like(prices)
        for leg in legs:
            T = dte / 365
            for j, px in enumerate(prices):
                current_val = bs_price(px, leg["strike"], T, r_rate, iv, leg["type"])
                total_pnl_now[j] += (current_val - leg["premium"]) * leg["pos"] * 100

        # Mid-point P&L
        total_pnl_mid = np.zeros_like(prices)
        T_mid = (dte / 2) / 365
        for leg in legs:
            for j, px in enumerate(prices):
                mid_val = bs_price(px, leg["strike"], T_mid, r_rate, iv, leg["type"])
                total_pnl_mid[j] += (mid_val - leg["premium"]) * leg["pos"] * 100

        # Metrics
        max_profit = np.max(total_pnl_exp)
        max_loss = np.min(total_pnl_exp)
        zero_crossings = np.where(np.diff(np.sign(total_pnl_exp)))[0]
        breakevens = [prices[i] for i in zero_crossings]

        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("Net Cost", f"${net_cost:,.0f}", "Credit" if net_cost > 0 else "Debit",
                    delta_color="inverse" if net_cost < 0 else "normal")
        sm2.metric("Max Profit", "Unlimited" if max_profit > 100000 else f"${max_profit:,.0f}")
        sm3.metric("Max Loss", "Unlimited" if max_loss < -100000 else f"${max_loss:,.0f}")
        be_str = " / ".join([f"${b:.1f}" for b in breakevens]) if breakevens else "None"
        sm4.metric("Breakeven(s)", be_str)

        # P&L chart with time layers
        fig_pnl = go.Figure()

        # Profit/Loss shading at expiration
        fig_pnl.add_trace(go.Scatter(
            x=prices, y=np.where(total_pnl_exp > 0, total_pnl_exp, 0),
            fill="tozeroy", fillcolor="rgba(0, 255, 0, 0.15)",
            line=dict(color="rgba(0,0,0,0)"), showlegend=False, hoverinfo="skip",
        ))
        fig_pnl.add_trace(go.Scatter(
            x=prices, y=np.where(total_pnl_exp < 0, total_pnl_exp, 0),
            fill="tozeroy", fillcolor="rgba(255, 0, 0, 0.15)",
            line=dict(color="rgba(0,0,0,0)"), showlegend=False, hoverinfo="skip",
        ))

        # P&L lines
        fig_pnl.add_trace(go.Scatter(
            x=prices, y=total_pnl_now,
            mode="lines", name=f"Today ({dte} DTE)",
            line=dict(color="#00d1ff", width=2),
        ))
        fig_pnl.add_trace(go.Scatter(
            x=prices, y=total_pnl_mid,
            mode="lines", name=f"Midpoint ({dte//2} DTE)",
            line=dict(color="#ffaa00", width=2, dash="dash"),
        ))
        fig_pnl.add_trace(go.Scatter(
            x=prices, y=total_pnl_exp,
            mode="lines", name="At Expiration",
            line=dict(color="white", width=2.5),
        ))

        fig_pnl.add_hline(y=0, line_color="gray", line_width=1)
        fig_pnl.add_vline(x=spot, line_dash="dot", line_color="#ffaa00",
                           annotation_text="Spot")

        fig_pnl.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Underlying Price ($)", yaxis_title="Profit / Loss ($)",
            hovermode="x unified",
        )
        st.plotly_chart(fig_pnl, use_container_width=True)

        # Time decay heatmap
        st.subheader("P&L Heatmap (Price vs. Time)")
        price_steps = np.linspace(spot * 0.9, spot * 1.1, 40)
        z_matrix = np.zeros((len(price_steps), len(days)))

        for di, d in enumerate(days):
            T = max(d / 365, 0.001)
            for pi, px in enumerate(price_steps):
                pnl = 0
                for leg in legs:
                    if d == 0:
                        val = max(px - leg["strike"], 0) if leg["type"] == "call" else max(leg["strike"] - px, 0)
                    else:
                        val = bs_price(px, leg["strike"], T, r_rate, iv, leg["type"])
                    pnl += (val - leg["premium"]) * leg["pos"] * 100
                z_matrix[pi, di] = pnl

        fig_heat = go.Figure(data=go.Heatmap(
            z=z_matrix,
            x=days,
            y=price_steps,
            colorscale="RdYlGn",
            zmid=0,
            hovertemplate="DTE: %{x}<br>Price: $%{y:.1f}<br>P&L: $%{z:,.0f}<extra></extra>",
        ))
        fig_heat.update_layout(
            template="plotly_dark", height=500,
            margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Days to Expiration",
            yaxis_title="Underlying Price ($)",
            xaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig_heat, use_container_width=True)
    else:
        st.warning("Load data first to get the current spot price.")


# ---- TAB 4: BS Pricing & Greeks ----
with tab4:
    st.subheader("Black-Scholes Pricing & Greeks")
    st.markdown("Theoretical pricing, Greek exposures, and time-decay heatmap.")

    def bs_greeks(S, K, T, r, sigma, option_type="call"):
        if T <= 0:
            price = max(S - K, 0) if option_type == "call" else max(K - S, 0)
            return price, 0.0, 0.0, 0.0, 0.0, 0.0
        d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)
        npd1 = norm.pdf(d1)
        if option_type == "call":
            price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
            delta = norm.cdf(d1)
            theta = (-(S * sigma * npd1) / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
            rho = (K * T * np.exp(-r * T) * norm.cdf(d2)) / 100
        else:
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
            delta = norm.cdf(d1) - 1
            theta = (-(S * sigma * npd1) / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365
            rho = (-K * T * np.exp(-r * T) * norm.cdf(-d2)) / 100
        gamma = npd1 / (S * sigma * np.sqrt(T))
        vega = (S * np.sqrt(T) * npd1) / 100
        return price, delta, gamma, theta, vega, rho

    bc1, bc2 = st.columns(2)
    with bc1:
        bs_type = st.selectbox("Option Type", ["Call", "Put"], key="bs_type").lower()
        bs_S = st.number_input("Spot Price ($)", value=float(spot) if spot else 100.0, step=1.0, key="bs_S")
        bs_K = st.number_input("Strike Price ($)", value=float(spot) if spot else 100.0, step=1.0, key="bs_K")
    with bc2:
        bs_exp = st.date_input("Expiration Date",
                                value=date.today() + timedelta(days=30),
                                min_value=date.today(), key="bs_exp")
        bs_vol = st.number_input("Implied Volatility (%)", value=25.0, step=1.0, key="bs_vol") / 100
        bs_r = st.number_input("Risk-Free Rate (%)", value=4.5, step=0.1, key="bs_r") / 100

    bs_dte = max((bs_exp - date.today()).days, 0)
    bs_T = bs_dte / 365.0

    price, delta_v, gamma_v, theta_v, vega_v, rho_v = bs_greeks(bs_S, bs_K, bs_T, bs_r, bs_vol, bs_type)

    gc1, gc2, gc3, gc4, gc5, gc6 = st.columns(6)
    gc1.metric("Theo. Value", f"${price:.2f}")
    gc2.metric("Delta", f"{delta_v:.3f}")
    gc3.metric("Gamma", f"{gamma_v:.4f}")
    gc4.metric("Theta", f"${theta_v:.3f}")
    gc5.metric("Vega", f"${vega_v:.3f}")
    gc6.metric("Rho", f"${rho_v:.3f}")

    st.divider()

    # Time & Price Decay Heatmap
    if bs_dte > 0:
        st.subheader(f"Time & Price Decay Matrix ({bs_type.capitalize()})")
        price_range_pct = st.slider("Price Range (+/- %)", 5, 50, 15, step=5, key="bs_range")
        purchase_price = st.number_input("Purchase Price (for breakeven line)", value=0.0, step=0.1, key="bs_purchase")

        lower = bs_S * (1 - price_range_pct / 100)
        upper = bs_S * (1 + price_range_pct / 100)
        price_steps = np.linspace(lower, upper, 20)
        days_arr = np.arange(bs_dte, -1, -1)

        price_grid, days_grid = np.meshgrid(price_steps, days_arr, indexing="ij")
        T_grid = np.where(days_grid <= 0, 0.001, days_grid / 365.0)
        d1_g = (np.log(price_grid / bs_K) + (bs_r + 0.5 * bs_vol**2) * T_grid) / (bs_vol * np.sqrt(T_grid))
        d2_g = d1_g - bs_vol * np.sqrt(T_grid)

        if bs_type == "call":
            z = price_grid * norm.cdf(d1_g) - bs_K * np.exp(-bs_r * T_grid) * norm.cdf(d2_g)
            z = np.where(days_grid <= 0, np.maximum(price_grid - bs_K, 0), z)
        else:
            z = bs_K * np.exp(-bs_r * T_grid) * norm.cdf(-d2_g) - price_grid * norm.cdf(-d1_g)
            z = np.where(days_grid <= 0, np.maximum(bs_K - price_grid, 0), z)

        center_val = purchase_price if purchase_price > 0 else price

        fig_decay = go.Figure()
        fig_decay.add_trace(go.Heatmap(
            z=z, x=days_arr, y=price_steps,
            colorscale="RdYlGn" if bs_type == "call" else "RdYlGn_r",
            text=np.round(z, 1), texttemplate="%{text}",
            showscale=False,
            hovertemplate="DTE: %{x}<br>Spot: $%{y:.2f}<br>Value: $%{z:.2f}<extra></extra>",
        ))
        fig_decay.add_trace(go.Contour(
            z=z, x=days_arr, y=price_steps,
            contours=dict(start=center_val, end=center_val, size=0, coloring="none"),
            line=dict(color="black", width=3, dash="dash"),
            showscale=False, hoverinfo="skip",
        ))
        fig_decay.update_layout(
            template="plotly_dark", height=600,
            margin=dict(t=10, b=30, l=50, r=50),
            xaxis_title="Days to Expiration",
            yaxis_title="Stock Price ($)",
            xaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig_decay, use_container_width=True)
    else:
        st.warning("Option has expired. Set a future expiration date.")


# ---- TAB 5: Strategy Optimizer ----
with tab5:
    st.subheader("Strategy Optimizer — Live Chain Pricing")
    st.markdown(
        "Select a strategy type and target expiration. The optimizer scans the **live options chain** "
        "to find the best strikes by risk/reward, using actual market prices and IV skew."
    )

    if spot and not df_all.empty:
        so1, so2, so3 = st.columns(3)
        with so1:
            opt_strategy = st.selectbox("Strategy", [
                "Bull Call Spread", "Bear Put Spread", "Iron Condor",
                "Long Straddle", "Long Strangle", "Butterfly",
            ], key="opt_strat")
        with so2:
            opt_exps = sorted(df_all["expiration"].unique())
            opt_exp = st.selectbox("Expiration", opt_exps, key="opt_exp")
        with so3:
            max_risk = st.number_input("Max Risk per Contract ($)", value=500, step=50, key="opt_max_risk")

        if st.button("Find Optimal Strikes", type="primary", use_container_width=True, key="run_optimizer"):
            exp_chain = df_all[df_all["expiration"] == opt_exp].copy()
            exp_calls = exp_chain[exp_chain["type"] == "call"].sort_values("strike").reset_index(drop=True)
            exp_puts = exp_chain[exp_chain["type"] == "put"].sort_values("strike").reset_index(drop=True)

            dte = max((pd.to_datetime(opt_exp) - pd.Timestamp.now()).days, 1)

            results = []

            if opt_strategy == "Bull Call Spread" and len(exp_calls) > 2:
                # Buy lower strike call, sell higher strike call
                atm_idx = (exp_calls["strike"] - spot).abs().argsort().iloc[0]
                search_range = exp_calls.iloc[max(0, atm_idx - 5):min(len(exp_calls), atm_idx + 10)]

                for i, long_row in search_range.iterrows():
                    for j, short_row in search_range.iterrows():
                        if short_row["strike"] <= long_row["strike"]:
                            continue
                        debit = long_row["close"] - short_row["close"]
                        if debit <= 0:
                            continue
                        max_profit = (short_row["strike"] - long_row["strike"]) * 100 - debit * 100
                        max_loss = debit * 100
                        if max_loss > max_risk or max_loss <= 0:
                            continue
                        rr = max_profit / max_loss
                        breakeven = long_row["strike"] + debit
                        # Probability of profit (rough: use delta of short strike)
                        pop = (1 - abs(short_row.get("delta", 0.5))) * 100 if "delta" in short_row.index else 50

                        results.append({
                            "Long Strike": long_row["strike"],
                            "Short Strike": short_row["strike"],
                            "Debit": debit,
                            "Max Profit": max_profit,
                            "Max Loss": max_loss,
                            "R:R Ratio": rr,
                            "Breakeven": breakeven,
                            "Est. PoP (%)": pop,
                            "Long IV": long_row.get("iv", 0),
                            "Short IV": short_row.get("iv", 0),
                        })

            elif opt_strategy == "Bear Put Spread" and len(exp_puts) > 2:
                atm_idx = (exp_puts["strike"] - spot).abs().argsort().iloc[0]
                search_range = exp_puts.iloc[max(0, atm_idx - 10):min(len(exp_puts), atm_idx + 5)]

                for i, long_row in search_range.iterrows():
                    for j, short_row in search_range.iterrows():
                        if short_row["strike"] >= long_row["strike"]:
                            continue
                        debit = long_row["close"] - short_row["close"]
                        if debit <= 0:
                            continue
                        max_profit = (long_row["strike"] - short_row["strike"]) * 100 - debit * 100
                        max_loss = debit * 100
                        if max_loss > max_risk or max_loss <= 0:
                            continue
                        rr = max_profit / max_loss
                        breakeven = long_row["strike"] - debit

                        results.append({
                            "Long Strike": long_row["strike"],
                            "Short Strike": short_row["strike"],
                            "Debit": debit,
                            "Max Profit": max_profit,
                            "Max Loss": max_loss,
                            "R:R Ratio": rr,
                            "Breakeven": breakeven,
                            "Est. PoP (%)": abs(short_row.get("delta", 0.5)) * 100,
                            "Long IV": long_row.get("iv", 0),
                            "Short IV": short_row.get("iv", 0),
                        })

            elif opt_strategy == "Iron Condor" and len(exp_calls) > 2 and len(exp_puts) > 2:
                # Sell OTM call spread + sell OTM put spread
                otm_calls = exp_calls[exp_calls["strike"] > spot * 1.01].head(8)
                otm_puts = exp_puts[exp_puts["strike"] < spot * 0.99].tail(8)

                for _, sc in otm_calls.iterrows():
                    lc_row = exp_calls[exp_calls["strike"] == sc["strike"] + 5]
                    if lc_row.empty:
                        lc_candidates = exp_calls[exp_calls["strike"] > sc["strike"]]
                        if lc_candidates.empty:
                            continue
                        lc_row = lc_candidates.iloc[:1]
                    lc = lc_row.iloc[0]

                    for _, sp_row in otm_puts.iterrows():
                        lp_candidates = exp_puts[exp_puts["strike"] < sp_row["strike"]]
                        if lp_candidates.empty:
                            continue
                        lp = lp_candidates.iloc[-1]

                        credit = (sc["close"] - lc["close"]) + (sp_row["close"] - lp["close"])
                        if credit <= 0:
                            continue

                        call_width = lc["strike"] - sc["strike"]
                        put_width = sp_row["strike"] - lp["strike"]
                        max_width = max(call_width, put_width)
                        max_loss = (max_width - credit) * 100
                        max_profit = credit * 100

                        if max_loss > max_risk or max_loss <= 0:
                            continue

                        results.append({
                            "Put Wing": f"${lp['strike']}/{sp_row['strike']}",
                            "Call Wing": f"${sc['strike']}/{lc['strike']}",
                            "Credit": credit,
                            "Max Profit": max_profit,
                            "Max Loss": max_loss,
                            "R:R Ratio": max_profit / max_loss if max_loss > 0 else 0,
                            "Breakeven Low": sp_row["strike"] - credit,
                            "Breakeven High": sc["strike"] + credit,
                            "Width": max_width,
                        })

            elif opt_strategy == "Long Straddle" and len(exp_calls) > 0 and len(exp_puts) > 0:
                atm_calls = exp_calls.iloc[(exp_calls["strike"] - spot).abs().argsort()[:5]]
                for _, c in atm_calls.iterrows():
                    p_match = exp_puts[exp_puts["strike"] == c["strike"]]
                    if p_match.empty:
                        continue
                    p = p_match.iloc[0]
                    cost = (c["close"] + p["close"]) * 100
                    if cost > max_risk or cost <= 0:
                        continue
                    breakeven_up = c["strike"] + c["close"] + p["close"]
                    breakeven_down = c["strike"] - c["close"] - p["close"]
                    move_needed = (c["close"] + p["close"]) / spot * 100

                    results.append({
                        "Strike": c["strike"],
                        "Call Price": c["close"],
                        "Put Price": p["close"],
                        "Total Cost": cost,
                        "Move Needed (%)": move_needed,
                        "Breakeven Up": breakeven_up,
                        "Breakeven Down": breakeven_down,
                        "Avg IV": (c.get("iv", 0) + p.get("iv", 0)) / 2,
                    })

            elif opt_strategy == "Long Strangle" and len(exp_calls) > 0 and len(exp_puts) > 0:
                otm_calls = exp_calls[exp_calls["strike"] > spot * 1.01].head(5)
                otm_puts = exp_puts[exp_puts["strike"] < spot * 0.99].tail(5)
                for _, c in otm_calls.iterrows():
                    for _, p in otm_puts.iterrows():
                        cost = (c["close"] + p["close"]) * 100
                        if cost > max_risk or cost <= 0:
                            continue
                        breakeven_up = c["strike"] + c["close"] + p["close"]
                        breakeven_down = p["strike"] - c["close"] - p["close"]
                        move_needed = max(
                            (breakeven_up / spot - 1) * 100,
                            (1 - breakeven_down / spot) * 100,
                        )
                        results.append({
                            "Call Strike": c["strike"],
                            "Put Strike": p["strike"],
                            "Call Price": c["close"],
                            "Put Price": p["close"],
                            "Total Cost": cost,
                            "Move Needed (%)": move_needed,
                            "Breakeven Up": breakeven_up,
                            "Breakeven Down": breakeven_down,
                        })

            elif opt_strategy == "Butterfly" and len(exp_calls) > 4:
                atm_idx = (exp_calls["strike"] - spot).abs().argsort().iloc[0]
                center_range = exp_calls.iloc[max(0, atm_idx - 3):min(len(exp_calls), atm_idx + 4)]

                for _, center in center_range.iterrows():
                    wings = exp_calls[exp_calls["strike"] != center["strike"]]
                    lower_wings = wings[wings["strike"] < center["strike"]].tail(3)
                    upper_wings = wings[wings["strike"] > center["strike"]].head(3)

                    for _, lw in lower_wings.iterrows():
                        width = center["strike"] - lw["strike"]
                        uw_match = upper_wings[(upper_wings["strike"] - center["strike"]).abs() < width * 1.5]
                        if uw_match.empty:
                            continue
                        uw = uw_match.iloc[0]
                        actual_width_up = uw["strike"] - center["strike"]

                        cost = (lw["close"] - 2 * center["close"] + uw["close"]) * 100
                        if cost <= 0 or cost > max_risk:
                            continue
                        max_profit = min(width, actual_width_up) * 100 - cost

                        results.append({
                            "Lower Wing": lw["strike"],
                            "Center": center["strike"],
                            "Upper Wing": uw["strike"],
                            "Net Debit": cost,
                            "Max Profit": max_profit,
                            "R:R Ratio": max_profit / cost if cost > 0 else 0,
                            "Center IV": center.get("iv", 0),
                        })

            if results:
                res_df = pd.DataFrame(results)
                # Sort by risk/reward
                sort_col = "R:R Ratio" if "R:R Ratio" in res_df.columns else "Move Needed (%)"
                ascending = sort_col == "Move Needed (%)"
                res_df = res_df.sort_values(sort_col, ascending=ascending).head(15)

                st.success(f"Found **{len(res_df)}** {opt_strategy} setups within ${max_risk} risk budget.")

                # Format for display
                display_df = res_df.copy()
                for col in display_df.columns:
                    if "Price" in col or "Debit" in col or "Credit" in col or "Cost" in col:
                        display_df[col] = display_df[col].apply(lambda x: f"${x:.2f}" if isinstance(x, (int, float)) else x)
                    elif "Profit" in col or "Loss" in col:
                        display_df[col] = display_df[col].apply(lambda x: f"${x:,.0f}" if isinstance(x, (int, float)) else x)
                    elif "R:R" in col:
                        display_df[col] = display_df[col].apply(lambda x: f"{x:.2f}x" if isinstance(x, (int, float)) else x)
                    elif "Breakeven" in col:
                        display_df[col] = display_df[col].apply(lambda x: f"${x:.1f}" if isinstance(x, (int, float)) else x)
                    elif "IV" in col:
                        display_df[col] = display_df[col].apply(lambda x: f"{x:.1%}" if isinstance(x, (int, float)) else x)
                    elif "%" in col:
                        display_df[col] = display_df[col].apply(lambda x: f"{x:.1f}%" if isinstance(x, (int, float)) else x)

                st.dataframe(display_df, use_container_width=True, hide_index=True)

                # Skew context
                st.divider()
                st.subheader("IV Skew Context for Selected Expiration")
                exp_calls_plot = exp_calls[(exp_calls["strike"] >= spot * 0.85) & (exp_calls["strike"] <= spot * 1.15)]
                exp_puts_plot = exp_puts[(exp_puts["strike"] >= spot * 0.85) & (exp_puts["strike"] <= spot * 1.15)]

                if "iv" in exp_calls_plot.columns:
                    fig_skew = go.Figure()
                    if not exp_calls_plot.empty:
                        fig_skew.add_trace(go.Scatter(
                            x=exp_calls_plot["strike"], y=exp_calls_plot["iv"],
                            mode="lines+markers", name="Call IV",
                            line=dict(color="#00ff96", width=2),
                        ))
                    if not exp_puts_plot.empty:
                        fig_skew.add_trace(go.Scatter(
                            x=exp_puts_plot["strike"], y=exp_puts_plot["iv"],
                            mode="lines+markers", name="Put IV",
                            line=dict(color="#ff4b4b", width=2),
                        ))
                    fig_skew.add_vline(x=spot, line_dash="dot", line_color="#ffaa00",
                                        annotation_text="Spot")
                    fig_skew.update_layout(
                        template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                        xaxis_title="Strike", yaxis_title="Implied Volatility",
                        hovermode="x unified",
                    )
                    st.plotly_chart(fig_skew, use_container_width=True)

                    # Skew metrics
                    if not exp_puts_plot.empty and not exp_calls_plot.empty:
                        atm_iv = exp_calls_plot.iloc[(exp_calls_plot["strike"] - spot).abs().argsort()[:1]]["iv"].values[0]
                        otm_put_iv = exp_puts_plot[exp_puts_plot["strike"] < spot * 0.95]["iv"].mean()
                        otm_call_iv = exp_calls_plot[exp_calls_plot["strike"] > spot * 1.05]["iv"].mean()

                        sk1, sk2, sk3 = st.columns(3)
                        sk1.metric("ATM IV", f"{atm_iv:.1%}" if pd.notna(atm_iv) else "N/A")
                        sk2.metric("25Δ Put Skew", f"{(otm_put_iv - atm_iv):.1%}" if pd.notna(otm_put_iv) else "N/A",
                                    help="Positive = puts are more expensive (crash demand)")
                        sk3.metric("25Δ Call Skew", f"{(otm_call_iv - atm_iv):.1%}" if pd.notna(otm_call_iv) else "N/A",
                                    help="Positive = calls are more expensive (rally demand)")
            else:
                st.warning(f"No {opt_strategy} setups found within the ${max_risk} risk budget. Try increasing the limit.")
    else:
        st.warning("Load data first to use the strategy optimizer.")
