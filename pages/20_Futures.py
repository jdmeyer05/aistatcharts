import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
import yfinance as yf
import logging
from src.layout import setup_page, error_boundary, fun_loader

logger = logging.getLogger(__name__)

setup_page("20_Futures")

st.title("📈 Futures Dashboard")
st.markdown("Real-time futures snapshot, term structure, cross-asset correlations, and volatility monitoring.")

# --- FUTURES UNIVERSE ---
FUTURES = {
    "Indices": {
        "ES=F": "S&P 500",
        "NQ=F": "Nasdaq 100",
        "YM=F": "Dow Jones",
        "RTY=F": "Russell 2000",
    },
    "Energy": {
        "CL=F": "Crude Oil (WTI)",
        "NG=F": "Natural Gas",
        "RB=F": "Gasoline (RBOB)",
        "HO=F": "Heating Oil",
    },
    "Metals": {
        "GC=F": "Gold",
        "SI=F": "Silver",
        "HG=F": "Copper",
        "PL=F": "Platinum",
    },
    "Rates": {
        "ZB=F": "30-Year Bond",
        "ZN=F": "10-Year Note",
        "ZF=F": "5-Year Note",
        "ZT=F": "2-Year Note",
    },
    "Agriculture": {
        "ZC=F": "Corn",
        "ZS=F": "Soybeans",
        "ZW=F": "Wheat",
        "KC=F": "Coffee",
    },
    "FX": {
        "6E=F": "Euro",
        "6J=F": "Yen",
        "6B=F": "British Pound",
        "DX=F": "Dollar Index",
    },
}

TERM_STRUCTURES = {
    "Crude Oil": {
        "tickers": [
            "CL=F", "CLK26.NYM", "CLM26.NYM", "CLN26.NYM", "CLQ26.NYM",
            "CLU26.NYM", "CLV26.NYM", "CLX26.NYM", "CLZ26.NYM",
            "CLF27.NYM", "CLG27.NYM", "CLH27.NYM", "CLJ27.NYM", "CLM27.NYM", "CLZ27.NYM",
        ],
        "labels": [
            "Front", "May 26", "Jun 26", "Jul 26", "Aug 26",
            "Sep 26", "Oct 26", "Nov 26", "Dec 26",
            "Jan 27", "Feb 27", "Mar 27", "Apr 27", "Jun 27", "Dec 27",
        ],
        "color": "#ff9900",
    },
    "Natural Gas": {
        "tickers": [
            "NG=F", "NGK26.NYM", "NGM26.NYM", "NGN26.NYM", "NGQ26.NYM",
            "NGU26.NYM", "NGV26.NYM", "NGX26.NYM", "NGZ26.NYM",
            "NGF27.NYM", "NGG27.NYM", "NGH27.NYM", "NGJ27.NYM", "NGM27.NYM", "NGZ27.NYM",
        ],
        "labels": [
            "Front", "May 26", "Jun 26", "Jul 26", "Aug 26",
            "Sep 26", "Oct 26", "Nov 26", "Dec 26",
            "Jan 27", "Feb 27", "Mar 27", "Apr 27", "Jun 27", "Dec 27",
        ],
        "color": "#ff4b4b",
    },
    "Gold": {
        "tickers": [
            "GC=F", "GCM26.CMX", "GCQ26.CMX", "GCV26.CMX", "GCZ26.CMX",
            "GCG27.CMX", "GCJ27.CMX", "GCM27.CMX", "GCZ27.CMX",
        ],
        "labels": [
            "Front", "Jun 26", "Aug 26", "Oct 26", "Dec 26",
            "Feb 27", "Apr 27", "Jun 27", "Dec 27",
        ],
        "color": "#ffdd00",
    },
}

VOL_TICKERS = {
    "^VIX": "VIX (S&P 500)",
    "^OVX": "OVX (Oil)",
    "^GVZ": "GVZ (Gold)",
}

SECTOR_COLORS = {
    "Indices": "#00d1ff",
    "Energy": "#ff9900",
    "Metals": "#ffdd00",
    "Rates": "#ad7fff",
    "Agriculture": "#00ff96",
    "FX": "#ff4b4b",
}


@st.cache_data(ttl=300)
def fetch_futures_snapshot():
    """Fetch current price and daily change for all futures."""
    rows = []
    for sector, tickers in FUTURES.items():
        for ticker, name in tickers.items():
            try:
                hist = yf.Ticker(ticker).history(period="5d")
                if not hist.empty and len(hist) >= 2:
                    close = hist["Close"].iloc[-1]
                    prev = hist["Close"].iloc[-2]
                    change = close - prev
                    pct = (change / prev) * 100
                    rows.append({
                        "ticker": ticker, "name": name, "sector": sector,
                        "price": close, "change": change, "pct_change": pct,
                    })
            except Exception as e:
                logger.warning(f"Failed to fetch {ticker}: {e}")
    return pd.DataFrame(rows)


@st.cache_data(ttl=300)
def fetch_futures_history(ticker: str, period: str = "6mo"):
    """Fetch historical data for a single futures contract."""
    try:
        df = yf.download(ticker, period=period, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df
    except Exception as e:
        logger.error(f"Failed to fetch history for {ticker}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def fetch_term_structure(name: str):
    """Fetch current prices for a term structure."""
    config = TERM_STRUCTURES[name]
    prices = []
    for ticker, label in zip(config["tickers"], config["labels"]):
        try:
            hist = yf.Ticker(ticker).history(period="1d")
            if not hist.empty:
                prices.append({"label": label, "ticker": ticker, "price": hist["Close"].iloc[-1]})
        except:
            pass
    return prices


@st.cache_data(ttl=3600)
def fetch_curve_history(name: str, period: str = "3mo"):
    """Fetch historical closing prices for all contract months to track curve changes."""
    config = TERM_STRUCTURES[name]
    all_hist = {}
    for ticker, label in zip(config["tickers"], config["labels"]):
        if label == "Front":
            continue  # Front month rolls, skip for historical comparison
        try:
            df = yf.download(ticker, period=period, progress=False)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            if not df.empty:
                df.index = pd.to_datetime(df.index).tz_localize(None)
                all_hist[label] = df["Close"]
        except:
            pass
    if all_hist:
        return pd.DataFrame(all_hist).dropna()
    return pd.DataFrame()


@st.cache_data(ttl=300)
def fetch_vol_data():
    """Fetch volatility indices."""
    rows = []
    for ticker, name in VOL_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period="6mo")
            if not hist.empty:
                latest = hist["Close"].iloc[-1]
                prev = hist["Close"].iloc[-2] if len(hist) >= 2 else latest
                rows.append({
                    "ticker": ticker, "name": name,
                    "value": latest, "change": latest - prev,
                    "hist": hist,
                })
        except:
            pass
    return rows


# --- FETCH DATA ---
with fun_loader("data"):
    df_snap = fetch_futures_snapshot()
    vol_data = fetch_vol_data()

if df_snap.empty:
    st.error("Failed to load futures data.")
    st.stop()

# --- TABS ---
tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "Heatmap & Snapshot",
    "Term Structure",
    "Historical Charts",
    "Correlations",
    "Sector Performance",
    "Volatility Monitor",
])


# ---- TAB 1: Heatmap & Snapshot ----
with tab1:
    st.subheader("Futures Market Heatmap")

    # Grid heatmap — sectors as rows, contracts as cells
    sectors_list = list(FUTURES.keys())
    max_contracts = max(len(v) for v in FUTURES.values())

    # Build grid data
    z_vals = []
    hover_text = []
    y_labels = []
    x_labels = [f"Contract {i+1}" for i in range(max_contracts)]

    for sector in sectors_list:
        sector_df = df_snap[df_snap["sector"] == sector].sort_values("pct_change", ascending=False)
        row_z = []
        row_hover = []
        for i in range(max_contracts):
            if i < len(sector_df):
                r = sector_df.iloc[i]
                row_z.append(r["pct_change"])
                if r["price"] > 1000:
                    p_fmt = f"{r['price']:,.0f}"
                elif r["price"] > 10:
                    p_fmt = f"{r['price']:,.2f}"
                else:
                    p_fmt = f"{r['price']:.4f}"
                row_hover.append(f"<b>{r['name']}</b><br>{p_fmt}<br>{r['pct_change']:+.2f}%")
            else:
                row_z.append(None)
                row_hover.append("")
        z_vals.append(row_z)
        hover_text.append(row_hover)
        y_labels.append(sector)

    # Build annotation text (name + change) for each cell
    annot_text = []
    for sector in sectors_list:
        sector_df = df_snap[df_snap["sector"] == sector].sort_values("pct_change", ascending=False)
        row_annot = []
        for i in range(max_contracts):
            if i < len(sector_df):
                r = sector_df.iloc[i]
                if r["price"] > 1000:
                    p_fmt = f"{r['price']:,.0f}"
                elif r["price"] > 10:
                    p_fmt = f"{r['price']:,.2f}"
                else:
                    p_fmt = f"{r['price']:.4f}"
                row_annot.append(f"{r['name']}<br>{p_fmt}<br>{r['pct_change']:+.2f}%")
            else:
                row_annot.append("")
        annot_text.append(row_annot)

    fig_heat = go.Figure(data=go.Heatmap(
        z=z_vals,
        y=y_labels,
        colorscale=[
            [0, "#cc0000"],
            [0.35, "#661111"],
            [0.5, "#1a1a2e"],
            [0.65, "#116633"],
            [1, "#00cc66"],
        ],
        zmid=0,
        text=annot_text,
        texttemplate="%{text}",
        textfont=dict(size=12),
        hovertext=hover_text,
        hovertemplate="%{hovertext}<extra></extra>",
        showscale=False,
        xgap=3, ygap=3,
    ))

    fig_heat.update_layout(
        template="plotly_dark", height=480, margin=dict(t=10, b=10, l=100, r=10),
        xaxis=dict(visible=False),
        yaxis=dict(autorange="reversed", tickfont=dict(size=13)),
    )
    st.plotly_chart(fig_heat, use_container_width=True)

    st.divider()

    # Snapshot metrics by sector
    for sector in FUTURES.keys():
        sector_df = df_snap[df_snap["sector"] == sector].copy()
        if sector_df.empty:
            continue

        st.markdown(f"**{sector}**")
        cols = st.columns(len(sector_df))
        for col, (_, row) in zip(cols, sector_df.iterrows()):
            delta_color = "normal" if row["pct_change"] >= 0 else "inverse"
            if row["price"] > 1000:
                price_fmt = f"{row['price']:,.0f}"
            elif row["price"] > 10:
                price_fmt = f"{row['price']:,.2f}"
            else:
                price_fmt = f"{row['price']:.4f}"
            col.metric(row["name"], price_fmt,
                       f"{row['pct_change']:+.2f}%", delta_color=delta_color)


# ---- TAB 2: Term Structure ----
with tab2:
    st.subheader("Futures Forward Curves")
    st.markdown("**Contango** = upward slope (later months more expensive). **Backwardation** = downward slope (front month premium).")

    for name, config in TERM_STRUCTURES.items():
        prices = fetch_term_structure(name)
        if len(prices) >= 2:
            st.markdown(f"### {name}")

            front = prices[0]["price"]
            back = prices[-1]["price"]
            spread = back - front
            structure = "Contango" if spread > 0 else "Backwardation"
            spread_pct = (spread / front) * 100

            tc_cols = st.columns(4)
            tc_cols[0].metric("Front Month", f"{front:.2f}")
            tc_cols[1].metric("Back Month", f"{back:.2f}")
            tc_cols[2].metric("Structure", structure, f"{spread:+.2f}",
                               delta_color="inverse" if spread > 0 else "normal")
            tc_cols[3].metric("Spread %", f"{spread_pct:+.1f}%")

            fig_curve = go.Figure()
            price_vals = [p["price"] for p in prices]
            label_vals = [p["label"] for p in prices]

            fill_color = "rgba(255, 75, 75, 0.1)" if spread > 0 else "rgba(0, 255, 150, 0.1)"
            fig_curve.add_trace(go.Scatter(
                x=label_vals, y=price_vals,
                mode="lines+markers", name="Current Curve",
                line=dict(color=config["color"], width=3),
                marker=dict(size=8),
                fill="tozeroy", fillcolor=fill_color,
                hovertemplate="%{x}: %{y:.2f}<extra></extra>",
            ))
            fig_curve.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Price", hovermode="x unified",
            )
            st.plotly_chart(fig_curve, use_container_width=True)

            # --- Curve Changes Over Time ---
            st.markdown(f"**{name} — Curve History**")
            df_curve_hist = fetch_curve_history(name, "3mo")

            if not df_curve_hist.empty and len(df_curve_hist) > 5:
                # Show curves at different historical snapshots
                snapshot_offsets = {
                    "Current": -1,
                    "1 Week Ago": -5,
                    "1 Month Ago": -22,
                    "3 Months Ago": 0,
                }
                snapshot_colors = {
                    "Current": config["color"],
                    "1 Week Ago": "#888888",
                    "1 Month Ago": "#ffaa00",
                    "3 Months Ago": "#ad7fff",
                }

                fig_hist_curves = go.Figure()
                for label_snap, offset in snapshot_offsets.items():
                    idx = offset if offset != 0 else 0
                    if abs(idx) < len(df_curve_hist):
                        row = df_curve_hist.iloc[idx]
                        snap_labels = list(row.index)
                        snap_prices = list(row.values)
                        is_current = label_snap == "Current"
                        fig_hist_curves.add_trace(go.Scatter(
                            x=snap_labels, y=snap_prices,
                            mode="lines+markers", name=label_snap,
                            line=dict(
                                color=snapshot_colors[label_snap],
                                width=3 if is_current else 1.5,
                                dash="solid" if is_current else "dot",
                            ),
                            marker=dict(size=6 if is_current else 4),
                        ))

                fig_hist_curves.update_layout(
                    template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Price", hovermode="x unified",
                )
                st.plotly_chart(fig_hist_curves, use_container_width=True)

                # Individual contract month price history
                st.markdown(f"**{name} — Contract Month Price Tracking**")
                selected_contracts = st.multiselect(
                    f"Select contract months to track ({name})",
                    df_curve_hist.columns.tolist(),
                    default=df_curve_hist.columns.tolist()[:4],
                    key=f"track_{name}",
                )
                if selected_contracts:
                    fig_track = go.Figure()
                    track_colors = ["#ff4b4b", "#00d1ff", "#00ff96", "#ffaa00", "#ad7fff",
                                    "#ff69b4", "#ffdd00", "#888888", "#ff9900", "#00ffcc"]
                    for i, col in enumerate(selected_contracts):
                        fig_track.add_trace(go.Scatter(
                            x=df_curve_hist.index, y=df_curve_hist[col],
                            mode="lines", name=col,
                            line=dict(color=track_colors[i % len(track_colors)], width=2),
                        ))
                    fig_track.update_layout(
                        template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                        yaxis_title="Price", hovermode="x unified",
                    )
                    st.plotly_chart(fig_track, use_container_width=True)

            st.divider()


# ---- TAB 3: Historical Charts ----
with tab3:
    st.subheader("Historical Futures Prices")

    # Build flat ticker list
    all_tickers = {}
    for sector, tickers in FUTURES.items():
        for ticker, name in tickers.items():
            all_tickers[f"{name} ({ticker})"] = ticker

    hc1, hc2 = st.columns(2)
    with hc1:
        selected = st.selectbox("Contract", list(all_tickers.keys()))
    with hc2:
        period = st.selectbox("Period", ["1mo", "3mo", "6mo", "1y", "2y"], index=2)

    hist_ticker = all_tickers[selected]
    df_hist = fetch_futures_history(hist_ticker, period)

    if not df_hist.empty:
        fig_hist = go.Figure()

        # Candlestick
        fig_hist.add_trace(go.Candlestick(
            x=df_hist.index,
            open=df_hist["Open"], high=df_hist["High"],
            low=df_hist["Low"], close=df_hist["Close"],
            name="OHLC",
        ))

        # Moving averages
        if len(df_hist) >= 20:
            ma20 = df_hist["Close"].rolling(20).mean()
            fig_hist.add_trace(go.Scatter(
                x=ma20.index, y=ma20.values,
                mode="lines", name="20-Day MA",
                line=dict(color="#ffaa00", width=1.5, dash="dot"),
            ))
        if len(df_hist) >= 50:
            ma50 = df_hist["Close"].rolling(50).mean()
            fig_hist.add_trace(go.Scatter(
                x=ma50.index, y=ma50.values,
                mode="lines", name="50-Day MA",
                line=dict(color="#00d1ff", width=1.5, dash="dot"),
            ))

        fig_hist.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_rangeslider_visible=False, hovermode="x unified",
        )
        st.plotly_chart(fig_hist, use_container_width=True)

        # Volume with color based on up/down day
        if "Volume" in df_hist.columns:
            vol_colors = ["#00ff96" if c >= o else "#ff4b4b"
                          for c, o in zip(df_hist["Close"], df_hist["Open"])]
            fig_vol = go.Figure()
            fig_vol.add_trace(go.Bar(
                x=df_hist.index, y=df_hist["Volume"],
                marker_color=vol_colors, opacity=0.7,
            ))
            fig_vol.update_layout(
                template="plotly_dark", height=150, margin=dict(t=0, b=0, l=0, r=0),
                yaxis_title="Volume",
            )
            st.plotly_chart(fig_vol, use_container_width=True)

        # Stats
        latest = df_hist["Close"].iloc[-1]
        high = df_hist["High"].max()
        low = df_hist["Low"].min()
        ret = (df_hist["Close"].iloc[-1] / df_hist["Close"].iloc[0] - 1) * 100
        vol = df_hist["Close"].pct_change().std() * np.sqrt(252) * 100

        sc1, sc2, sc3, sc4, sc5 = st.columns(5)
        sc1.metric("Last", f"{latest:.2f}")
        sc2.metric("Period High", f"{high:.2f}")
        sc3.metric("Period Low", f"{low:.2f}")
        sc4.metric("Period Return", f"{ret:+.1f}%")
        sc5.metric("Annualized Vol", f"{vol:.1f}%")
    else:
        st.warning("No historical data available.")


# ---- TAB 4: Correlations ----
with tab4:
    st.subheader("Cross-Asset Correlation Matrix")

    cor_period = st.selectbox("Correlation Period", ["1mo", "3mo", "6mo", "1y"], index=1, key="cor_period")

    # Fetch returns for all contracts
    returns_dict = {}
    for sector, tickers in FUTURES.items():
        for ticker, name in tickers.items():
            df_c = fetch_futures_history(ticker, cor_period)
            if not df_c.empty:
                returns_dict[name] = df_c["Close"].pct_change().dropna()

    if len(returns_dict) >= 4:
        df_returns = pd.DataFrame(returns_dict).dropna()
        corr_matrix = df_returns.corr()

        # Shorten names for readability
        short_names = {
            "S&P 500": "S&P", "Nasdaq 100": "NQ", "Dow Jones": "Dow", "Russell 2000": "Russ",
            "Crude Oil (WTI)": "Crude", "Natural Gas": "NatGas", "Gasoline (RBOB)": "Gas",
            "Heating Oil": "HeatOil", "30-Year Bond": "30Y", "10-Year Note": "10Y",
            "5-Year Note": "5Y", "2-Year Note": "2Y", "British Pound": "GBP",
            "Dollar Index": "DXY",
        }
        corr_labels = [short_names.get(c, c) for c in corr_matrix.columns]

        fig_corr = go.Figure(data=go.Heatmap(
            z=corr_matrix.values,
            x=corr_labels,
            y=corr_labels,
            colorscale=[
                [0, "#cc0000"], [0.25, "#661111"],
                [0.5, "#1a1a2e"],
                [0.75, "#116633"], [1, "#00cc66"],
            ],
            zmid=0, zmin=-1, zmax=1,
            text=np.round(corr_matrix.values, 2),
            texttemplate="%{text}",
            textfont=dict(size=10),
            xgap=2, ygap=2,
            hovertemplate="%{x} vs %{y}: %{z:.2f}<extra></extra>",
        ))
        fig_corr.update_layout(
            template="plotly_dark", height=650,
            margin=dict(t=10, b=0, l=80, r=0),
            xaxis=dict(tickfont=dict(size=10), tickangle=-45),
            yaxis=dict(tickfont=dict(size=10)),
        )
        st.plotly_chart(fig_corr, use_container_width=True)

        # Most/least correlated pairs
        st.subheader("Notable Correlations")
        pairs = []
        for i in range(len(corr_matrix)):
            for j in range(i + 1, len(corr_matrix)):
                pairs.append({
                    "Pair": f"{corr_matrix.index[i]} / {corr_matrix.columns[j]}",
                    "Correlation": corr_matrix.iloc[i, j],
                })
        df_pairs = pd.DataFrame(pairs).sort_values("Correlation")

        pc1, pc2 = st.columns(2)
        with pc1:
            st.markdown("**Most Negatively Correlated**")
            top_neg = df_pairs.head(5).copy()
            top_neg["Correlation"] = top_neg["Correlation"].apply(lambda x: f"{x:.3f}")
            st.dataframe(top_neg, use_container_width=True, hide_index=True)
        with pc2:
            st.markdown("**Most Positively Correlated**")
            top_pos = df_pairs.tail(5).iloc[::-1].copy()
            top_pos["Correlation"] = top_pos["Correlation"].apply(lambda x: f"{x:.3f}")
            st.dataframe(top_pos, use_container_width=True, hide_index=True)
    else:
        st.warning("Not enough data for correlation matrix.")


# ---- TAB 5: Sector Performance ----
with tab5:
    st.subheader("Performance by Sector & Contract")

    # Calculate returns for different periods
    perf_data = []
    for sector, tickers in FUTURES.items():
        for ticker, name in tickers.items():
            df_p = fetch_futures_history(ticker, "1y")
            if not df_p.empty and len(df_p) > 5:
                close = df_p["Close"]
                ret_1d = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else 0
                ret_1w = (close.iloc[-1] / close.iloc[-min(5, len(close))] - 1) * 100
                ret_1m = (close.iloc[-1] / close.iloc[-min(22, len(close))] - 1) * 100
                ret_3m = (close.iloc[-1] / close.iloc[-min(66, len(close))] - 1) * 100
                ret_ytd = (close.iloc[-1] / close.iloc[0] - 1) * 100

                perf_data.append({
                    "Name": name, "Sector": sector,
                    "1D": ret_1d, "1W": ret_1w, "1M": ret_1m, "3M": ret_3m, "YTD": ret_ytd,
                })

    if perf_data:
        df_perf = pd.DataFrame(perf_data)

        # Sector averages
        sector_avg = df_perf.groupby("Sector")[["1D", "1W", "1M", "3M"]].mean()

        fig_sector = go.Figure()
        periods = ["1D", "1W", "1M", "3M"]
        for period_label in periods:
            fig_sector.add_trace(go.Bar(
                x=sector_avg.index,
                y=sector_avg[period_label],
                name=period_label,
            ))
        fig_sector.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            barmode="group", yaxis_title="Average Return (%)", hovermode="x unified",
        )
        st.plotly_chart(fig_sector, use_container_width=True)

        # Individual contract performance table
        st.subheader("Contract Performance Table")
        display_perf = df_perf.copy()
        for col in ["1D", "1W", "1M", "3M", "YTD"]:
            display_perf[col] = display_perf[col].apply(lambda x: f"{x:+.2f}%")
        st.dataframe(display_perf, use_container_width=True, hide_index=True)

        # Bar chart of 1-month returns sorted
        st.subheader("1-Month Return Ranking")
        df_sorted = df_perf.sort_values("1M")
        colors_ret = ["#00ff96" if v >= 0 else "#ff4b4b" for v in df_sorted["1M"]]

        fig_rank = go.Figure()
        fig_rank.add_trace(go.Bar(
            y=df_sorted["Name"], x=df_sorted["1M"],
            orientation="h", marker_color=colors_ret,
            text=[f"{v:+.1f}%" for v in df_sorted["1M"]],
            textposition="outside",
        ))
        fig_rank.add_vline(x=0, line_color="white", line_width=1)
        fig_rank.update_layout(
            template="plotly_dark", height=max(400, len(df_sorted) * 25),
            margin=dict(t=10, b=0, l=130, r=50),
            xaxis_title="1-Month Return (%)",
        )
        st.plotly_chart(fig_rank, use_container_width=True)


# ---- TAB 6: Volatility Monitor ----
with tab6:
    st.subheader("Volatility Indices")

    if vol_data:
        # Metrics
        vcols = st.columns(len(vol_data))
        for col, vd in zip(vcols, vol_data):
            delta_color = "inverse" if vd["change"] > 0 else "normal"
            col.metric(vd["name"], f"{vd['value']:.2f}", f"{vd['change']:+.2f}",
                       delta_color=delta_color)

        # Historical vol charts
        vol_colors = ["#ff4b4b", "#ffaa00", "#00d1ff"]
        fig_vol_hist = go.Figure()
        for i, vd in enumerate(vol_data):
            hist = vd["hist"]
            fig_vol_hist.add_trace(go.Scatter(
                x=hist.index, y=hist["Close"],
                mode="lines", name=vd["name"],
                line=dict(color=vol_colors[i % len(vol_colors)], width=2),
            ))

        fig_vol_hist.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Volatility Level", hovermode="x unified",
        )
        st.plotly_chart(fig_vol_hist, use_container_width=True)

    # Realized volatility for key contracts
    st.subheader("Realized Volatility (20-Day Rolling)")
    rv_contracts = {"ES=F": "S&P 500", "CL=F": "Crude Oil", "GC=F": "Gold", "ZN=F": "10Y Note"}
    rv_colors = ["#00d1ff", "#ff9900", "#ffdd00", "#ad7fff"]

    fig_rv = go.Figure()
    for (ticker, name), color in zip(rv_contracts.items(), rv_colors):
        df_rv = fetch_futures_history(ticker, "6mo")
        if not df_rv.empty:
            returns = df_rv["Close"].pct_change()
            rv_20d = returns.rolling(20).std() * np.sqrt(252) * 100
            fig_rv.add_trace(go.Scatter(
                x=rv_20d.index, y=rv_20d.values,
                mode="lines", name=name,
                line=dict(color=color, width=2),
            ))

    fig_rv.update_layout(
        template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Annualized Volatility (%)", hovermode="x unified",
    )
    st.plotly_chart(fig_rv, use_container_width=True)

    # Vol regime indicator
    st.subheader("Volatility Regime")
    if vol_data:
        vix = next((v for v in vol_data if "VIX" in v["name"]), None)
        if vix:
            vix_val = vix["value"]
            hist_vix = vix["hist"]["Close"]
            pct_rank = (hist_vix < vix_val).mean() * 100

            vr1, vr2, vr3 = st.columns(3)
            vr1.metric("VIX Current", f"{vix_val:.2f}")
            vr2.metric("6-Month Percentile", f"{pct_rank:.0f}%")

            if vix_val < 15:
                vr3.metric("Regime", "Low Vol / Complacent")
                st.success("Low volatility environment. Options are cheap.")
            elif vix_val < 20:
                vr3.metric("Regime", "Normal")
                st.info("Normal volatility conditions.")
            elif vix_val < 30:
                vr3.metric("Regime", "Elevated")
                st.warning("Elevated volatility. Hedging costs rising.")
            else:
                vr3.metric("Regime", "Fear / Crisis")
                st.error("High fear. Extreme hedging demand.")
