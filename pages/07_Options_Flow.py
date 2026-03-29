import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from src.data_engine import format_massive_ticker, fetch_massive_data
from src.api_keys import get_secret
from src.layout import setup_page, get_active_ticker, set_active_ticker, fun_loader

logger = logging.getLogger(__name__)

setup_page("07_Options_Flow")

st.title("💧 Options Flow Intelligence")
st.markdown("Unusual activity scanner, put/call analysis, and gamma exposure profiling.")


_get_massive_key = lambda: get_secret("MASSIVE_API_KEY")


from src.data_engine import fetch_options_chain as _fetch_chain_raw, get_expiration_dates

# Column mapping: data_engine names → page 07 names
_COL_MAP = {
    "strike_price": "strike", "contract_type": "type",
    "expiration_date": "expiration", "last_price": "close",
}

def fetch_full_chain(symbol: str, api_key: str = None, expiration: str = None):
    """Fetch full options chain — delegates to data_engine."""
    df = _fetch_chain_raw(symbol, expiration=expiration)
    if df is None:
        return pd.DataFrame()
    if not df.empty:
        df = df.rename(columns=_COL_MAP)
        if "close" not in df.columns:
            df["close"] = np.nan  # NaN, not 0 — zeros corrupt ratios and IV
    return df

def fetch_all_expirations(symbol: str, api_key: str = None):
    return get_expiration_dates(symbol)


# --- Controls ---
_c1, _c2 = st.columns([3, 1])
with _c1:
    raw_ticker = st.text_input("Ticker", value=get_active_ticker())
    ticker = format_massive_ticker(raw_ticker)
    set_active_ticker(ticker)
with _c2:
    st.markdown("<br>", unsafe_allow_html=True)
    submit = st.button("Load Options Data", type="primary", use_container_width=True)

api_key = _get_massive_key()

if not api_key:
    st.error("Massive API key not configured.")
    st.stop()

# --- FETCH & STORE ---
if submit:
    with fun_loader("data"):
        expirations = fetch_all_expirations(ticker, api_key)
        df_chain = fetch_full_chain(ticker, api_key)
        px_df = fetch_massive_data(ticker, 5)
        spot = px_df["Close"].iloc[-1] if px_df is not None and not px_df.empty else None

        if not df_chain.empty:
            st.session_state["flow_chain"] = df_chain
            st.session_state["flow_exps"] = expirations
            st.session_state["flow_spot"] = spot
            st.session_state["flow_ticker"] = ticker
            st.session_state["shared_options_ticker"] = ticker
            st.session_state["shared_options_spot"] = spot
        else:
            st.error("No options data returned.")
            st.stop()

# --- RENDER ---
if "flow_chain" in st.session_state:
    df = st.session_state["flow_chain"]
    expirations = st.session_state["flow_exps"]
    spot = st.session_state["flow_spot"]
    ticker_display = st.session_state["flow_ticker"]

    calls = df[df["type"] == "call"]
    puts = df[df["type"] == "put"]

    tab1, tab2, tab3, tab4 = st.tabs([
        "Unusual Activity Scanner",
        "Put/Call Analysis",
        "Gamma Exposure (GEX)",
        "Block Trade Detection",
    ])

    # ---- TAB 1: Unusual Activity Scanner ----
    with tab1:
        st.subheader("Unusual Options Activity")
        st.markdown("Contracts where **volume significantly exceeds open interest**, signaling new institutional positioning.")

        uc1, uc2 = st.columns(2)
        with uc1:
            min_volume = st.number_input("Min Volume", value=100, step=50)
        with uc2:
            min_ratio = st.number_input("Min Vol/OI Ratio", value=2.0, step=0.5)

        # Filter
        df_unusual = df[(df["volume"] > min_volume) & (df["open_interest"] > 0)].copy()
        df_unusual["vol_oi_ratio"] = df_unusual["volume"] / df_unusual["open_interest"]
        df_unusual = df_unusual[df_unusual["vol_oi_ratio"] >= min_ratio]
        df_unusual = df_unusual.sort_values("vol_oi_ratio", ascending=False)

        if not df_unusual.empty:
            # Summary metrics
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("Unusual Contracts", f"{len(df_unusual)}")
            unusual_calls = df_unusual[df_unusual["type"] == "call"]
            unusual_puts = df_unusual[df_unusual["type"] == "put"]
            sc2.metric("Unusual Calls", f"{len(unusual_calls)}")
            sc3.metric("Unusual Puts", f"{len(unusual_puts)}")
            total_unusual_vol = df_unusual["volume"].sum()
            sc4.metric("Total Unusual Volume", f"{total_unusual_vol:,.0f}")

            # Table
            display_unusual = df_unusual[["strike", "type", "expiration", "volume", "open_interest",
                                           "vol_oi_ratio", "implied_volatility", "delta", "close"]].head(30).copy()
            display_unusual["vol_oi_ratio"] = display_unusual["vol_oi_ratio"].apply(lambda x: f"{x:.1f}x")
            display_unusual["implied_volatility"] = display_unusual["implied_volatility"].apply(lambda x: f"{x:.1%}")
            display_unusual["delta"] = display_unusual["delta"].apply(lambda x: f"{x:.3f}")
            display_unusual["close"] = display_unusual["close"].apply(lambda x: f"${x:.2f}")
            display_unusual.columns = ["Strike", "Type", "Expiration", "Volume", "OI", "Vol/OI",
                                        "IV", "Delta", "Last Price"]
            st.dataframe(display_unusual, use_container_width=True, hide_index=True)

            # Scatter plot: Volume vs OI colored by type
            fig_scatter = go.Figure()
            for ct, color, name in [("call", "#00ff96", "Calls"), ("put", "#ff4b4b", "Puts")]:
                subset = df_unusual[df_unusual["type"] == ct]
                fig_scatter.add_trace(go.Scatter(
                    x=subset["open_interest"], y=subset["volume"],
                    mode="markers", name=name,
                    marker=dict(color=color, size=subset["vol_oi_ratio"].clip(upper=50) * 2, opacity=0.7),
                    hovertemplate="Strike: %{text}<br>Vol: %{y:,.0f}<br>OI: %{x:,.0f}<extra></extra>",
                    text=subset["strike"],
                ))

            fig_scatter.add_shape(type="line", x0=0, y0=0,
                                  x1=df_unusual["open_interest"].max(),
                                  y1=df_unusual["open_interest"].max(),
                                  line=dict(color="white", width=1, dash="dot"))

            fig_scatter.update_layout(
                template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Open Interest", yaxis_title="Volume",
                hovermode="closest",
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
            st.caption("Bubble size = Vol/OI ratio. Dotted line = 1:1 (Volume = OI).")
        else:
            st.info("No unusual activity found with current filters.")

    # ---- TAB 2: Put/Call Analysis ----
    with tab2:
        st.subheader("Put/Call Ratios & Positioning")

        total_call_vol = calls["volume"].sum()
        total_put_vol = puts["volume"].sum()
        total_call_oi = calls["open_interest"].sum()
        total_put_oi = puts["open_interest"].sum()

        pc_vol_ratio = total_put_vol / total_call_vol if total_call_vol > 0 else 0
        pc_oi_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else 0

        # Metrics
        pc1, pc2, pc3, pc4 = st.columns(4)
        pc1.metric("P/C Volume Ratio", f"{pc_vol_ratio:.2f}",
                    delta_color="inverse" if pc_vol_ratio > 1 else "normal")
        pc2.metric("P/C OI Ratio", f"{pc_oi_ratio:.2f}",
                    delta_color="inverse" if pc_oi_ratio > 1 else "normal")
        pc3.metric("Total Call Volume", f"{total_call_vol:,.0f}")
        pc4.metric("Total Put Volume", f"{total_put_vol:,.0f}")

        st.caption("> 1.0 = Bearish skew (more puts) | < 1.0 = Bullish skew (more calls)")

        # --- Historical P/C Ratio Context ---
        st.divider()
        st.subheader("Historical P/C Ratio Context")
        st.caption(
            "Current P/C ratio compared to historical equity P/C ratios. "
            "**Extreme readings** (above 1.2 or below 0.6) are contrarian signals — "
            "high put buying often marks bottoms, low put buying marks complacency."
        )

        # Historical CBOE equity P/C range (well-known benchmark)
        hist_mean = 0.70
        hist_std = 0.15
        z_score = (pc_vol_ratio - hist_mean) / hist_std if hist_std > 0 else 0

        hpc1, hpc2, hpc3, hpc4 = st.columns(4)
        hpc1.metric("Current P/C Ratio", f"{pc_vol_ratio:.2f}")
        hpc2.metric("Historical Mean (Equities)", f"{hist_mean:.2f}")
        hpc3.metric("Z-Score vs History", f"{z_score:+.1f}σ",
                     help="How many standard deviations from the long-term average.")

        if z_score > 1.5:
            regime = "Extreme Fear"
            regime_color = "#ff4b4b"
        elif z_score > 0.5:
            regime = "Elevated Hedging"
            regime_color = "#ffaa00"
        elif z_score < -1.5:
            regime = "Extreme Complacency"
            regime_color = "#ad7fff"
        elif z_score < -0.5:
            regime = "Low Hedging"
            regime_color = "#ffaa00"
        else:
            regime = "Neutral"
            regime_color = "#00ff96"

        hpc4.markdown(
            f'<div style="text-align:center;padding:8px;">'
            f'<div style="font-size:0.7rem;color:#888;text-transform:uppercase;">Sentiment</div>'
            f'<div style="font-size:1.2rem;font-weight:700;color:{regime_color};">{regime}</div>'
            f'<div style="font-size:0.7rem;color:#888;">{z_score:+.1f}σ from mean</div>'
            f'</div>', unsafe_allow_html=True,
        )

        # Visual gauge: where current P/C sits on the historical spectrum
        fig_gauge = go.Figure()
        # Background distribution
        gauge_x = np.linspace(0.2, 1.5, 200)
        gauge_y = np.exp(-0.5 * ((gauge_x - hist_mean) / hist_std) ** 2)
        fig_gauge.add_trace(go.Scatter(
            x=gauge_x, y=gauge_y, fill="tozeroy",
            fillcolor="rgba(0, 209, 255, 0.15)", line=dict(color="#00d1ff", width=1),
            showlegend=False, hoverinfo="skip",
        ))
        # Current P/C marker
        fig_gauge.add_vline(x=pc_vol_ratio, line_color="#ffaa00", line_width=3,
                             annotation_text=f"Current: {pc_vol_ratio:.2f}")
        # Reference lines
        fig_gauge.add_vline(x=0.5, line_dash="dot", line_color="#00ff96",
                             annotation_text="Bullish (<0.5)")
        fig_gauge.add_vline(x=1.0, line_dash="dot", line_color="#ff4b4b",
                             annotation_text="Bearish (>1.0)")
        fig_gauge.update_layout(
            template="plotly_dark", height=200, margin=dict(t=30, b=0, l=0, r=0),
            xaxis_title="Put/Call Volume Ratio", yaxis=dict(visible=False),
        )
        st.plotly_chart(fig_gauge, use_container_width=True)

        if z_score > 1.5:
            st.success(
                f"**Contrarian Bullish Signal:** P/C ratio of {pc_vol_ratio:.2f} is {z_score:.1f}σ above the mean. "
                f"Extreme put buying historically marks local bottoms. Smart money may be hedging, not predicting."
            )
        elif z_score < -1.5:
            st.warning(
                f"**Contrarian Bearish Signal:** P/C ratio of {pc_vol_ratio:.2f} is {abs(z_score):.1f}σ below the mean. "
                f"Low hedging activity suggests complacency — historically a warning sign."
            )

        st.divider()

        # Volume by strike
        st.subheader("Volume Distribution by Strike")
        if spot:
            x_min = spot * 0.9
            x_max = spot * 1.1
        else:
            x_min = df["strike"].min()
            x_max = df["strike"].max()

        calls_vis = calls[(calls["strike"] >= x_min) & (calls["strike"] <= x_max)]
        puts_vis = puts[(puts["strike"] >= x_min) & (puts["strike"] <= x_max)]

        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(
            x=calls_vis["strike"], y=calls_vis["volume"],
            name="Call Volume", marker_color="#00ff96", opacity=1.0,
        ))
        fig_vol.add_trace(go.Bar(
            x=puts_vis["strike"], y=-puts_vis["volume"],
            name="Put Volume", marker_color="#ff4b4b", opacity=1.0,
        ))
        if spot:
            fig_vol.add_vline(x=spot, line_dash="dot", line_color="#ffaa00")
        fig_vol.add_hline(y=0, line_color="white", line_width=1)
        fig_vol.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            barmode="overlay", yaxis_title="Volume (Puts negative)", hovermode="x unified",
            xaxis=dict(range=[x_min, x_max]),
        )
        st.plotly_chart(fig_vol, use_container_width=True)

        # OI by strike
        st.subheader("Open Interest Distribution by Strike")
        fig_oi = go.Figure()
        fig_oi.add_trace(go.Bar(
            x=calls_vis["strike"], y=calls_vis["open_interest"],
            name="Call OI", marker_color="#00ff96", opacity=1.0,
        ))
        fig_oi.add_trace(go.Bar(
            x=puts_vis["strike"], y=-puts_vis["open_interest"],
            name="Put OI", marker_color="#ff4b4b", opacity=1.0,
        ))
        if spot:
            fig_oi.add_vline(x=spot, line_dash="dot", line_color="#ffaa00")
        fig_oi.add_hline(y=0, line_color="white", line_width=1)
        fig_oi.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            barmode="overlay", yaxis_title="Open Interest (Puts negative)", hovermode="x unified",
            xaxis=dict(range=[x_min, x_max]),
        )
        st.plotly_chart(fig_oi, use_container_width=True)

        # P/C ratio by expiration
        st.subheader("Put/Call Ratio by Expiration")
        pc_by_exp = df.groupby(["expiration", "type"])["volume"].sum().unstack(fill_value=0)
        if "call" in pc_by_exp.columns and "put" in pc_by_exp.columns:
            pc_by_exp["pc_ratio"] = pc_by_exp["put"] / pc_by_exp["call"].replace(0, 1)
            pc_by_exp = pc_by_exp.sort_index()

            fig_pc_exp = go.Figure()
            colors_pc = ["#ff4b4b" if v > 1 else "#00ff96" for v in pc_by_exp["pc_ratio"]]
            fig_pc_exp.add_trace(go.Bar(
                x=pc_by_exp.index, y=pc_by_exp["pc_ratio"],
                marker_color=colors_pc,
            ))
            fig_pc_exp.add_hline(y=1.0, line_dash="dot", line_color="white",
                                  annotation_text="Neutral (1.0)")
            fig_pc_exp.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Put/Call Volume Ratio", hovermode="x unified",
            )
            st.plotly_chart(fig_pc_exp, use_container_width=True)

    # ---- TAB 3: Gamma Exposure (GEX) ----
    with tab3:
        st.subheader("Dealer Gamma Exposure (GEX) by Strike")
        st.markdown("""
        Estimated net dealer gamma exposure. **Positive GEX** = dealers are long gamma (market-stabilizing).
        **Negative GEX** = dealers are short gamma (market-amplifying, more volatile).
        """)

        if spot:
            # GEX = Gamma × OI × 100 × Spot²  / 10^7  (for readability)
            # Calls: dealers are typically short calls → negative gamma for them when we flip sign
            # Puts: dealers are typically short puts → positive gamma for them when we flip sign
            # Net GEX = Call_GEX - Put_GEX (from dealer perspective)

            df_gex = df[(df["strike"] >= spot * 0.85) & (df["strike"] <= spot * 1.15)].copy()

            if df_gex.empty or "gamma" not in df_gex.columns:
                st.warning("No options with gamma data in the ±15% strike range.")
            else:
                df_gex["contract_gex"] = df_gex["gamma"] * df_gex["open_interest"] * 100 * spot * spot / 1e7

                # Dealer convention: short calls (negative gamma), short puts (positive gamma)
                call_gex = df_gex[df_gex["type"] == "call"].groupby("strike")["contract_gex"].sum()
                put_gex = df_gex[df_gex["type"] == "put"].groupby("strike")["contract_gex"].sum()

                # Net GEX from dealer perspective
                all_strikes = sorted(set(call_gex.index) | set(put_gex.index))
                net_gex = pd.Series(index=all_strikes, dtype=float)
                for s in all_strikes:
                    c = call_gex.get(s, 0)
                    p = put_gex.get(s, 0)
                    net_gex[s] = c - p  # Calls contribute positive GEX, puts negative

                # Total GEX
                total_gex = net_gex.sum()
                max_gex_strike = net_gex.idxmax() if not net_gex.empty else 0
                min_gex_strike = net_gex.idxmin() if not net_gex.empty else 0

                gc1, gc2, gc3, gc4 = st.columns(4)
                gc1.metric("Net GEX", f"{total_gex:,.0f}",
                            "Long Gamma (Stable)" if total_gex > 0 else "Short Gamma (Volatile)",
                            delta_color="normal" if total_gex > 0 else "inverse")
                gc2.metric("Max GEX Strike (Pin)", f"${max_gex_strike:,.0f}")
                gc3.metric("Min GEX Strike (Vol)", f"${min_gex_strike:,.0f}")
                gc4.metric("Current Spot", f"${spot:,.2f}")

                # GEX bar chart
                fig_gex = go.Figure()
                colors_gex = ["#00ff96" if v > 0 else "#ff4b4b" for v in net_gex.values]
                fig_gex.add_trace(go.Bar(
                    x=net_gex.index, y=net_gex.values,
                    marker_color=colors_gex,
                    hovertemplate="Strike: $%{x}<br>GEX: %{y:,.0f}<extra></extra>",
                ))
                fig_gex.add_vline(x=spot, line_dash="dot", line_color="#ffaa00",
                                   annotation_text="Spot")
                fig_gex.add_hline(y=0, line_color="white", line_width=1)

                fig_gex.update_layout(
                    template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
                    xaxis_title="Strike Price", yaxis_title="Gamma Exposure (GEX)",
                    hovermode="x unified",
                )
                st.plotly_chart(fig_gex, use_container_width=True)

                # Call vs Put GEX breakdown
                st.subheader("GEX Breakdown: Calls vs Puts")
                fig_gex_split = go.Figure()
                fig_gex_split.add_trace(go.Bar(
                    x=call_gex.index, y=call_gex.values,
                    name="Call GEX", marker_color="#00ff96", opacity=0.8,
                ))
                fig_gex_split.add_trace(go.Bar(
                    x=put_gex.index, y=-put_gex.values,
                    name="Put GEX (inverted)", marker_color="#ff4b4b", opacity=0.8,
                ))
                fig_gex_split.add_vline(x=spot, line_dash="dot", line_color="#ffaa00")
                fig_gex_split.add_hline(y=0, line_color="white", line_width=1)
                fig_gex_split.update_layout(
                    template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                    barmode="overlay", yaxis_title="Gamma Exposure",
                    hovermode="x unified",
                )
                st.plotly_chart(fig_gex_split, use_container_width=True)

                # GEX by expiration
                st.subheader("GEX by Expiration")
                gex_by_exp = df_gex.groupby(["expiration", "type"])["contract_gex"].sum().unstack(fill_value=0)
                if "call" in gex_by_exp.columns and "put" in gex_by_exp.columns:
                    gex_by_exp["net"] = gex_by_exp["call"] - gex_by_exp["put"]
                    gex_by_exp = gex_by_exp.sort_index()

                    fig_gex_exp = go.Figure()
                    colors_exp = ["#00ff96" if v > 0 else "#ff4b4b" for v in gex_by_exp["net"]]
                    fig_gex_exp.add_trace(go.Bar(
                        x=gex_by_exp.index, y=gex_by_exp["net"], marker_color=colors_exp,
                    ))
                    fig_gex_exp.add_hline(y=0, line_color="white", line_width=1)
                    fig_gex_exp.update_layout(
                        template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                        yaxis_title="Net GEX", hovermode="x unified",
                    )
                    st.plotly_chart(fig_gex_exp, use_container_width=True)
        else:
            st.warning("Could not fetch spot price.")

    # ---- TAB 4: Block Trade Detection ----
    with tab4:
        st.subheader("Block Trade Detection")
        st.markdown(
            "Identifies **large single-contract trades** that likely represent institutional positioning. "
            "Block trades are defined as contracts where volume exceeds a statistical threshold "
            "relative to the overall chain — these often precede major directional moves."
        )

        bt1, bt2 = st.columns(2)
        with bt1:
            block_min_vol = st.number_input("Min Contract Volume", value=500, step=100, key="block_min")
        with bt2:
            block_min_notional = st.number_input("Min Notional Value ($)", value=50000, step=10000, key="block_notional")

        # Detect block trades: high volume + high notional
        df_block = df.copy()
        df_block["notional"] = df_block["volume"] * df_block["close"] * 100
        df_block = df_block[
            (df_block["volume"] >= block_min_vol) &
            (df_block["notional"] >= block_min_notional) &
            (df_block["close"] > 0)
        ].copy()

        # Score by percentile rank within chain
        if not df_block.empty:
            vol_p90 = df["volume"].quantile(0.90)
            df_block["vol_percentile"] = df_block["volume"].rank(pct=True) * 100
            df_block = df_block.sort_values("notional", ascending=False)

            # Summary
            bc1, bc2, bc3, bc4 = st.columns(4)
            bc1.metric("Block Trades Found", f"{len(df_block)}")
            block_calls = df_block[df_block["type"] == "call"]
            block_puts = df_block[df_block["type"] == "put"]
            bc2.metric("Block Calls", f"{len(block_calls)}")
            bc3.metric("Block Puts", f"{len(block_puts)}")
            total_notional = df_block["notional"].sum()
            bc4.metric("Total Notional", f"${total_notional:,.0f}")

            # Directional bias from block flow
            call_notional = block_calls["notional"].sum()
            put_notional = block_puts["notional"].sum()
            if call_notional + put_notional > 0:
                call_pct = call_notional / (call_notional + put_notional) * 100
                put_pct = 100 - call_pct
                bias = "Bullish" if call_pct > 60 else ("Bearish" if put_pct > 60 else "Neutral")
                bias_color = "#00ff96" if bias == "Bullish" else ("#ff4b4b" if bias == "Bearish" else "#ffaa00")
                st.markdown(
                    f'<div style="text-align:center;padding:12px;border:1px solid #30363d;border-radius:8px;">'
                    f'<span style="color:#888;font-size:0.8rem;">INSTITUTIONAL BLOCK FLOW BIAS</span><br>'
                    f'<span style="font-size:1.5rem;font-weight:700;color:{bias_color};">{bias}</span><br>'
                    f'<span style="color:#888;">Calls: ${call_notional:,.0f} ({call_pct:.0f}%) '
                    f'| Puts: ${put_notional:,.0f} ({put_pct:.0f}%)</span>'
                    f'</div>', unsafe_allow_html=True,
                )

            st.divider()

            # Block trades table
            st.subheader("Top Block Trades by Notional Value")
            display_block = df_block[[
                "strike", "type", "expiration", "volume", "open_interest",
                "close", "notional", "implied_volatility", "delta", "vol_percentile"
            ]].head(25).copy()

            display_block["close"] = display_block["close"].apply(lambda x: f"${x:.2f}")
            display_block["notional"] = display_block["notional"].apply(lambda x: f"${x:,.0f}")
            display_block["implied_volatility"] = display_block["implied_volatility"].apply(lambda x: f"{x:.1%}")
            display_block["delta"] = display_block["delta"].apply(lambda x: f"{x:.3f}")
            display_block["vol_percentile"] = display_block["vol_percentile"].apply(lambda x: f"{x:.0f}th")
            display_block.columns = [
                "Strike", "Type", "Expiration", "Volume", "OI",
                "Price", "Notional", "IV", "Delta", "Vol %ile"
            ]
            st.dataframe(display_block, use_container_width=True, hide_index=True)

            # Block trade strike heatmap
            st.subheader("Block Trade Concentration by Strike")
            block_by_strike = df_block.groupby(["strike", "type"])["notional"].sum().reset_index()

            fig_block = go.Figure()
            for ct, color, name in [("call", "#00ff96", "Call Blocks"), ("put", "#ff4b4b", "Put Blocks")]:
                subset = block_by_strike[block_by_strike["type"] == ct]
                if not subset.empty:
                    fig_block.add_trace(go.Bar(
                        x=subset["strike"], y=subset["notional"],
                        name=name, marker_color=color, opacity=0.8,
                    ))

            if spot:
                fig_block.add_vline(x=spot, line_dash="dot", line_color="#ffaa00",
                                     annotation_text="Spot")
            fig_block.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                barmode="group", xaxis_title="Strike Price",
                yaxis_title="Total Block Notional ($)", hovermode="x unified",
            )
            if spot:
                fig_block.update_xaxes(range=[spot * 0.9, spot * 1.1])
            st.plotly_chart(fig_block, use_container_width=True)

            # Moneyness distribution of blocks
            if spot:
                st.subheader("Block Trade Moneyness Distribution")
                df_block["moneyness"] = df_block.apply(
                    lambda r: (r["strike"] / spot - 1) * 100
                    if r["type"] == "call"
                    else (1 - r["strike"] / spot) * 100,
                    axis=1,
                )
                df_block["moneyness_label"] = df_block["moneyness"].apply(
                    lambda m: "Deep ITM" if m < -5 else ("ITM" if m < 0 else ("ATM" if m < 2 else ("OTM" if m < 10 else "Deep OTM")))
                )
                money_summary = df_block.groupby("moneyness_label")["notional"].sum()
                ordered_labels = ["Deep ITM", "ITM", "ATM", "OTM", "Deep OTM"]
                money_summary = money_summary.reindex([l for l in ordered_labels if l in money_summary.index])

                fig_money = go.Figure(go.Bar(
                    x=money_summary.index, y=money_summary.values,
                    marker_color=["#ad7fff", "#00d1ff", "#ffaa00", "#00ff96", "#ff4b4b"],
                    text=[f"${v:,.0f}" for v in money_summary.values],
                    textposition="outside",
                ))
                fig_money.update_layout(
                    template="plotly_dark", height=300, margin=dict(t=10, b=0, l=50, r=0),
                    yaxis_title="Block Notional ($)",
                )
                st.plotly_chart(fig_money, use_container_width=True)

                st.caption(
                    "**Deep ITM blocks** often indicate hedging or delta-one replacement. "
                    "**OTM blocks** signal directional bets or tail hedges. "
                    "**ATM blocks** suggest volatility plays (straddles/strangles)."
                )
        else:
            st.info("No block trades found with current filters. Try lowering thresholds.")

    # ---- Write unified signal (P/C ratio + GEX) ----
    try:
        from src.signal_engine import write_signal
        _pc = pc_vol_ratio
        _dir = "bull" if _pc < 0.7 else ("bear" if _pc > 1.2 else "neutral")
        _tgex = total_gex if "total_gex" in dir() else 0
        _vv = "short_vol" if _tgex > 0 else ("long_vol" if _tgex < 0 else "neutral")
        _reason = f"P/C ratio {_pc:.2f}"
        if _tgex:
            _reason += f", GEX {_tgex:+,.0f} ({'long' if _tgex > 0 else 'short'} gamma)"
        write_signal("options_flow", ticker_display, _dir, 0.6,
                     vol_view=_vv, reasoning=_reason)
    except Exception:
        pass
