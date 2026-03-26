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
    if not df.empty:
        df = df.rename(columns=_COL_MAP)
        if "close" not in df.columns and "last_price" not in df.columns:
            df["close"] = 0
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
        spot = px_df["Close"].iloc[-1] if px_df is not None else None

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

    tab1, tab2, tab3 = st.tabs([
        "Unusual Activity Scanner",
        "Put/Call Analysis",
        "Gamma Exposure (GEX)",
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
