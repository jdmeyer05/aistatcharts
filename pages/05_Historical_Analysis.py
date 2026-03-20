import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import yfinance as yf
from src.data_engine import fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot
from src.layout import setup_page, get_active_ticker, set_active_ticker, fun_loader
setup_page("05_Historical_Analysis")

st.title("🕰️ Historical & Seasonal Analysis")
st.markdown("Price action, seasonality, volatility, drawdowns, and statistical profiling.")

# --- SIDEBAR ---
with st.sidebar:
    st.header("Analysis Settings")
    with st.form("historical_settings"):
        raw_ticker = st.text_input("Ticker", value=get_active_ticker())
        lookback_years = st.slider("Lookback (Years)", 1, 10, 5)
        benchmark_ticker = st.text_input("Benchmark", value="SPY")
        submit = st.form_submit_button("Run Analysis")

ticker = format_massive_ticker(raw_ticker)
set_active_ticker(ticker)
bench_ticker = format_massive_ticker(benchmark_ticker)
lookback_days = lookback_years * 365 + 180  # Buffer for full calendar years


@st.cache_data(ttl=3600)
def fetch_macro_series(yf_ticker: str, period: str = "5y"):
    try:
        df = yf.download(yf_ticker, period=period, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index).tz_localize(None)
        return df["Close"]
    except:
        return pd.Series()


# --- FETCH ---
if submit or "hist_df" not in st.session_state or st.session_state.get("hist_ticker") != ticker:
    with fun_loader("data"):
        df = fetch_massive_data(ticker, lookback_days)
        if df is None or df.empty:
            st.error(f"Failed to fetch data for {ticker}.")
            st.stop()

        # Benchmark
        df_bench = fetch_massive_data(bench_ticker, lookback_days) if bench_ticker != ticker else None

        # Macro
        vix = fetch_macro_series("^VIX", f"{lookback_years}y")
        tny = fetch_macro_series("^TNX", f"{lookback_years}y")  # 10Y yield
        dxy = fetch_macro_series("DX=F", f"{lookback_years}y")  # Dollar index

        st.session_state.hist_df = df
        st.session_state.hist_ticker = ticker
        st.session_state.hist_bench = df_bench
        st.session_state.hist_vix = vix
        st.session_state.hist_tny = tny
        st.session_state.hist_dxy = dxy

if "hist_df" not in st.session_state:
    st.info("Configure settings and click **Run Analysis**.")
    st.stop()

df = st.session_state.hist_df.copy()
df_bench = st.session_state.hist_bench
vix = st.session_state.hist_vix
tny = st.session_state.hist_tny
dxy = st.session_state.hist_dxy

# --- DATA PREP ---
df["Daily_Return"] = df["Close"].pct_change()
df["Log_Return"] = np.log(df["Close"] / df["Close"].shift(1))
df["Year"] = df.index.year
df["DOY"] = df.index.dayofyear
df["Month"] = df.index.month
df["Weekday"] = df.index.dayofweek

current_year = pd.Timestamp.now().year
valid_years = sorted(df["Year"].unique())

# Monthly returns
monthly_px = df["Close"].resample("ME").last()
monthly_ret = monthly_px.pct_change().dropna()
m_df = pd.DataFrame({"Return": monthly_ret})
m_df["Year"] = m_df.index.year
m_df["Month_Num"] = m_df.index.month
m_df["Month_Name"] = m_df.index.strftime("%b")
month_order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Thermal colors
thermal_gradient = ["#ff2a2a", "#ff7f00", "#ffd700", "#00d1ff", "#118ab2", "#ad7fff", "#666666", "#444444", "#333333", "#222222"]
year_colors = {year: thermal_gradient[i] for i, year in enumerate(sorted(valid_years, reverse=True)) if i < len(thermal_gradient)}

# --- PERFORMANCE STATS ---
days = len(df.dropna())
years = days / 252
total_return = (df["Close"].iloc[-1] / df["Close"].iloc[0]) - 1
cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0
ann_vol = df["Daily_Return"].std() * np.sqrt(252)
sharpe = (df["Daily_Return"].mean() / df["Daily_Return"].std()) * np.sqrt(252) if df["Daily_Return"].std() > 0 else 0
downside = df["Daily_Return"][df["Daily_Return"] < 0].std() * np.sqrt(252)
sortino = (df["Daily_Return"].mean() * 252) / downside if downside > 0 else 0
cum_max = df["Close"].cummax()
drawdown = (df["Close"] / cum_max) - 1
max_dd = drawdown.min()
skew = df["Daily_Return"].skew()
kurt = df["Daily_Return"].kurtosis()

# Top metrics
mc1, mc2, mc3, mc4, mc5, mc6 = st.columns(6)
mc1.metric("Total Return", f"{total_return:.1%}")
mc2.metric("CAGR", f"{cagr:.1%}")
mc3.metric("Sharpe", f"{sharpe:.2f}")
mc4.metric("Sortino", f"{sortino:.2f}")
mc5.metric("Max Drawdown", f"{max_dd:.1%}")
mc6.metric("Ann. Volatility", f"{ann_vol:.1%}")

st.divider()

# --- TABS ---
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Price & Volume",
    "Seasonality",
    "Volatility",
    "Drawdowns",
    "Distribution",
    "Performance Stats",
    "Benchmark Comparison",
    "Macro Correlation",
])


# ---- TAB 1: Price & Volume ----
with tab1:
    st.subheader("Price Chart")

    # Candlestick with MAs
    fig_price = go.Figure()

    # Check if we have OHLC data
    has_ohlc = all(c in df.columns for c in ["Open", "High", "Low"])

    if has_ohlc:
        fig_price.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"],
            low=df["Low"], close=df["Close"], name="OHLC",
        ))
    else:
        fig_price.add_trace(go.Scatter(
            x=df.index, y=df["Close"], mode="lines",
            line=dict(color="white", width=2), name="Close",
        ))

    # Moving averages
    for period, color, dash in [(20, "#ffaa00", "dot"), (50, "#00d1ff", "dot"), (200, "#00ff96", "dash")]:
        if len(df) >= period:
            ma = df["Close"].rolling(period).mean()
            fig_price.add_trace(go.Scatter(
                x=ma.index, y=ma.values, mode="lines",
                name=f"{period}-Day MA", line=dict(color=color, width=1.5, dash=dash),
            ))

    fig_price.update_layout(
        template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
        xaxis_rangeslider_visible=False, hovermode="x unified",
    )
    st.plotly_chart(fig_price, use_container_width=True)

    # Volume
    if "Volume" in df.columns:
        vol_colors = ["#00ff96" if c >= o else "#ff4b4b"
                      for c, o in zip(df["Close"], df["Close"].shift(1).fillna(df["Close"]))]
        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors, opacity=0.7))
        ma_vol = df["Volume"].rolling(20).mean()
        fig_vol.add_trace(go.Scatter(x=ma_vol.index, y=ma_vol.values, mode="lines",
                                      line=dict(color="#ffaa00", width=1.5), name="20-Day Avg"))
        fig_vol.update_layout(
            template="plotly_dark", height=200, margin=dict(t=0, b=0, l=0, r=0),
            yaxis_title="Volume", hovermode="x unified",
        )
        st.plotly_chart(fig_vol, use_container_width=True)

    # Key price stats
    ps1, ps2, ps3, ps4 = st.columns(4)
    ps1.metric("Current", f"${df['Close'].iloc[-1]:,.2f}")
    ps2.metric("52-Week High", f"${df['Close'].tail(252).max():,.2f}")
    ps3.metric("52-Week Low", f"${df['Close'].tail(252).min():,.2f}")
    pct_from_high = (df["Close"].iloc[-1] / df["Close"].tail(252).max() - 1) * 100
    ps4.metric("From 52W High", f"{pct_from_high:+.1f}%")


# ---- TAB 2: Seasonality ----
with tab2:
    r1c1, r1c2 = st.columns(2)

    # YTD Trajectory
    with r1c1:
        st.subheader("YTD Trajectory Comparison")
        fig_ytd = go.Figure()
        for year in sorted(valid_years):
            year_data = df[df["Year"] == year]
            if not year_data.empty:
                cum_ret = (1 + year_data["Daily_Return"].fillna(0)).cumprod() - 1
                is_current = year == current_year
                fig_ytd.add_trace(go.Scatter(
                    x=year_data["DOY"], y=cum_ret, name=str(year), mode="lines",
                    line=dict(color=year_colors.get(year, "#444"), width=3.5 if is_current else 1.5),
                    opacity=1.0 if is_current else 0.7,
                ))
        fig_ytd.update_layout(
            template="plotly_dark", height=380, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Day of Year", yaxis_title="Cumulative Return",
            yaxis=dict(tickformat=".1%"), hovermode="x unified",
        )
        st.plotly_chart(fig_ytd, use_container_width=True)

    # Monthly box plots
    with r1c2:
        st.subheader("Monthly Seasonality (Box Plots)")
        fig_box = go.Figure()
        for month in month_order:
            month_data = m_df[m_df["Month_Name"] == month]
            fig_box.add_trace(go.Box(
                y=month_data["Return"], name=month, marker_color="#ad7fff",
                boxpoints="all", jitter=0.3, pointpos=-1.8,
            ))
        fig_box.add_hline(y=0, line_color="white", opacity=0.3)
        fig_box.update_layout(
            template="plotly_dark", height=380, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Monthly Return", yaxis=dict(tickformat=".1%"), showlegend=False,
        )
        st.plotly_chart(fig_box, use_container_width=True)

    r2c1, r2c2 = st.columns(2)

    # Monthly heatmap
    with r2c1:
        st.subheader("Monthly Returns Heatmap")
        heatmap_data = m_df.pivot_table(index="Year", columns="Month_Name", values="Return")
        heatmap_data = heatmap_data.reindex(columns=month_order).sort_index(ascending=False)
        text_data = heatmap_data.map(lambda x: f"{x:.1%}" if pd.notnull(x) else "")

        fig_heat = go.Figure(data=go.Heatmap(
            z=heatmap_data.values, x=heatmap_data.columns, y=heatmap_data.index.astype(str),
            text=text_data.values, texttemplate="%{text}",
            colorscale="RdYlGn", zmid=0, showscale=False, xgap=2, ygap=2,
        ))
        fig_heat.update_layout(
            template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
            yaxis=dict(type="category"),
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    # Annual returns
    with r2c2:
        st.subheader("Total Annual Returns")
        annual_ret = df.groupby("Year")["Daily_Return"].apply(lambda x: (1 + x.fillna(0)).cumprod().iloc[-1] - 1).reset_index()
        annual_ret["Color"] = annual_ret["Year"].map(year_colors).fillna("#444")

        fig_annual = go.Figure()
        fig_annual.add_trace(go.Bar(
            x=annual_ret["Year"].astype(str), y=annual_ret["Daily_Return"],
            marker_color=annual_ret["Color"],
            text=annual_ret["Daily_Return"].apply(lambda x: f"{x:.1%}"),
            textposition="auto",
        ))
        fig_annual.update_layout(
            template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Total Return", yaxis=dict(tickformat=".1%"),
        )
        st.plotly_chart(fig_annual, use_container_width=True)

    # Day of week seasonality
    st.subheader("Day-of-Week Seasonality")
    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri"]
    dow_ret = df.groupby("Weekday")["Daily_Return"].mean() * 100

    dc1, dc2 = st.columns([2, 1])
    with dc1:
        fig_dow = go.Figure()
        dow_colors = ["#00ff96" if v > 0 else "#ff4b4b" for v in dow_ret.values]
        fig_dow.add_trace(go.Bar(
            x=dow_names, y=dow_ret.values, marker_color=dow_colors,
            text=[f"{v:.3f}%" for v in dow_ret.values], textposition="outside",
        ))
        fig_dow.add_hline(y=0, line_color="white", line_width=1)
        fig_dow.update_layout(
            template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Avg Daily Return (%)",
        )
        st.plotly_chart(fig_dow, use_container_width=True)

    with dc2:
        dow_stats = df.groupby("Weekday")["Daily_Return"].agg(["mean", "std", "count"])
        dow_stats.index = dow_names
        dow_stats["mean"] = dow_stats["mean"].apply(lambda x: f"{x*100:.3f}%")
        dow_stats["std"] = dow_stats["std"].apply(lambda x: f"{x*100:.3f}%")
        dow_stats.columns = ["Avg Return", "Std Dev", "Observations"]
        st.dataframe(dow_stats, use_container_width=True)


# ---- TAB 3: Volatility ----
with tab3:
    st.subheader("Volatility Analysis")

    # Rolling realized vol
    df["RV_20"] = df["Daily_Return"].rolling(20).std() * np.sqrt(252) * 100
    df["RV_60"] = df["Daily_Return"].rolling(60).std() * np.sqrt(252) * 100

    fig_rv = go.Figure()
    fig_rv.add_trace(go.Scatter(
        x=df.index, y=df["RV_20"], mode="lines", name="20-Day RV",
        line=dict(color="#ff4b4b", width=2),
    ))
    fig_rv.add_trace(go.Scatter(
        x=df.index, y=df["RV_60"], mode="lines", name="60-Day RV",
        line=dict(color="#00d1ff", width=2),
    ))

    # Long-term average
    avg_rv = df["RV_20"].mean()
    fig_rv.add_hline(y=avg_rv, line_dash="dot", line_color="#ffaa00",
                      annotation_text=f"Avg: {avg_rv:.1f}%")

    fig_rv.update_layout(
        template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Annualized Volatility (%)", hovermode="x unified",
    )
    st.plotly_chart(fig_rv, use_container_width=True)

    # Vol metrics
    current_rv = df["RV_20"].iloc[-1]
    rv_pct_rank = (df["RV_20"].dropna() < current_rv).mean() * 100

    vc1, vc2, vc3, vc4 = st.columns(4)
    vc1.metric("Current 20D Vol", f"{current_rv:.1f}%")
    vc2.metric("60D Vol", f"{df['RV_60'].iloc[-1]:.1f}%")
    vc3.metric("Vol Percentile", f"{rv_pct_rank:.0f}%")
    regime = "Low" if rv_pct_rank < 30 else ("Normal" if rv_pct_rank < 70 else "High")
    vc4.metric("Vol Regime", regime)

    # Vol of vol
    st.subheader("Volatility of Volatility")
    vol_of_vol = df["RV_20"].rolling(20).std()
    fig_vov = go.Figure()
    fig_vov.add_trace(go.Scatter(
        x=vol_of_vol.index, y=vol_of_vol.values, mode="lines",
        line=dict(color="#ad7fff", width=2), fill="tozeroy", fillcolor="rgba(173,127,255,0.1)",
    ))
    fig_vov.update_layout(
        template="plotly_dark", height=250, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Vol of Vol", hovermode="x unified",
    )
    st.plotly_chart(fig_vov, use_container_width=True)

    # Monthly vol seasonality
    st.subheader("Volatility Seasonality by Month")
    df["Month_Name"] = df.index.strftime("%b")
    vol_by_month = df.groupby("Month")["Daily_Return"].std() * np.sqrt(252) * 100
    fig_vol_season = go.Figure()
    fig_vol_season.add_trace(go.Bar(
        x=month_order, y=vol_by_month.values,
        marker_color="#ff4b4b", text=[f"{v:.1f}%" for v in vol_by_month.values],
        textposition="outside",
    ))
    fig_vol_season.update_layout(
        template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Annualized Vol (%)",
    )
    st.plotly_chart(fig_vol_season, use_container_width=True)


# ---- TAB 4: Drawdowns ----
with tab4:
    st.subheader("Drawdown Analysis")

    # Underwater chart
    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=drawdown.index, y=drawdown.values * 100, mode="lines",
        line=dict(color="#ff4b4b", width=2),
        fill="tozeroy", fillcolor="rgba(255, 75, 75, 0.15)",
    ))
    fig_dd.add_hline(y=0, line_color="white", line_width=1)
    fig_dd.update_layout(
        template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Drawdown (%)", hovermode="x unified",
    )
    st.plotly_chart(fig_dd, use_container_width=True)

    # Extract individual drawdown events
    st.subheader("Top Drawdown Events")
    in_dd = False
    dd_events = []
    dd_start = None
    dd_peak = None

    for i in range(len(df)):
        if drawdown.iloc[i] < 0 and not in_dd:
            in_dd = True
            dd_start = df.index[i]
            dd_peak = cum_max.iloc[i]
        elif drawdown.iloc[i] == 0 and in_dd:
            in_dd = False
            dd_trough_idx = drawdown.loc[dd_start:df.index[i]].idxmin()
            dd_depth = drawdown.loc[dd_trough_idx]
            dd_duration = (df.index[i] - dd_start).days
            recovery_days = (df.index[i] - dd_trough_idx).days
            dd_events.append({
                "Start": dd_start.strftime("%Y-%m-%d"),
                "Trough": dd_trough_idx.strftime("%Y-%m-%d"),
                "Recovery": df.index[i].strftime("%Y-%m-%d"),
                "Depth": dd_depth,
                "Duration (days)": dd_duration,
                "Recovery (days)": recovery_days,
            })

    # Handle ongoing drawdown
    if in_dd:
        dd_trough_idx = drawdown.loc[dd_start:].idxmin()
        dd_depth = drawdown.loc[dd_trough_idx]
        dd_events.append({
            "Start": dd_start.strftime("%Y-%m-%d"),
            "Trough": dd_trough_idx.strftime("%Y-%m-%d"),
            "Recovery": "Ongoing",
            "Depth": dd_depth,
            "Duration (days)": (df.index[-1] - dd_start).days,
            "Recovery (days)": "N/A",
        })

    if dd_events:
        df_dd = pd.DataFrame(dd_events).sort_values("Depth")
        top_dd = df_dd.head(10).copy()
        top_dd["Depth"] = top_dd["Depth"].apply(lambda x: f"{x:.1%}")
        st.dataframe(top_dd, use_container_width=True, hide_index=True)

    # Drawdown duration histogram
    if dd_events:
        durations = [e["Duration (days)"] for e in dd_events if isinstance(e["Duration (days)"], int)]
        if durations:
            st.subheader("Drawdown Duration Distribution")
            fig_dur = go.Figure()
            fig_dur.add_trace(go.Histogram(
                x=durations, nbinsx=30, marker_color="#ff4b4b", opacity=0.8,
            ))
            fig_dur.update_layout(
                template="plotly_dark", height=250, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Duration (days)", yaxis_title="Count",
            )
            st.plotly_chart(fig_dur, use_container_width=True)


# ---- TAB 5: Distribution ----
with tab5:
    st.subheader("Return Distribution")

    rets = df["Daily_Return"].dropna()

    fig_dist = go.Figure()
    fig_dist.add_trace(go.Histogram(
        x=rets * 100, nbinsx=100, marker_color="#00d1ff", opacity=0.8, name="Actual",
    ))

    # Normal overlay
    x_range = np.linspace(rets.min() * 100, rets.max() * 100, 200)
    normal_y = (1 / (rets.std() * 100 * np.sqrt(2 * np.pi))) * np.exp(
        -0.5 * ((x_range - rets.mean() * 100) / (rets.std() * 100)) ** 2
    ) * len(rets) * (rets.max() - rets.min()) * 100 / 100

    fig_dist.add_trace(go.Scatter(
        x=x_range, y=normal_y, mode="lines", name="Normal Distribution",
        line=dict(color="#ffaa00", width=2, dash="dash"),
    ))

    fig_dist.add_vline(x=0, line_color="white", line_width=1)
    fig_dist.update_layout(
        template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
        xaxis_title="Daily Return (%)", yaxis_title="Frequency", barmode="overlay",
    )
    st.plotly_chart(fig_dist, use_container_width=True)

    # Distribution stats
    ds1, ds2, ds3, ds4, ds5 = st.columns(5)
    ds1.metric("Mean", f"{rets.mean()*100:.3f}%")
    ds2.metric("Median", f"{rets.median()*100:.3f}%")
    ds3.metric("Std Dev", f"{rets.std()*100:.3f}%")
    ds4.metric("Skewness", f"{skew:.2f}")
    ds5.metric("Kurtosis", f"{kurt:.2f}")

    st.caption("Skewness < 0 = left tail (more large drops). Kurtosis > 3 = fat tails (more extreme moves than normal).")

    # Tail risk table
    st.subheader("Tail Risk Percentiles")
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    tail_rows = []
    normal_samples = np.random.normal(rets.mean(), rets.std(), 50000)
    for p in percentiles:
        actual = np.percentile(rets, p) * 100
        normal_val = np.percentile(normal_samples, p) * 100
        tail_rows.append({"Percentile": f"{p}%", "Actual": f"{actual:.3f}%", "Normal": f"{normal_val:.3f}%"})
    st.dataframe(pd.DataFrame(tail_rows), use_container_width=True, hide_index=True)

    # Largest moves
    st.subheader("Largest Daily Moves")
    lc1, lc2 = st.columns(2)
    with lc1:
        st.markdown("**Best Days**")
        best = rets.nlargest(10)
        best_df = pd.DataFrame({"Date": best.index.strftime("%Y-%m-%d"), "Return": best.apply(lambda x: f"{x:.2%}")})
        st.dataframe(best_df, use_container_width=True, hide_index=True)
    with lc2:
        st.markdown("**Worst Days**")
        worst = rets.nsmallest(10)
        worst_df = pd.DataFrame({"Date": worst.index.strftime("%Y-%m-%d"), "Return": worst.apply(lambda x: f"{x:.2%}")})
        st.dataframe(worst_df, use_container_width=True, hide_index=True)


# ---- TAB 6: Performance Stats ----
with tab6:
    st.subheader("Comprehensive Performance Statistics")

    best_day = df["Daily_Return"].max()
    worst_day = df["Daily_Return"].min()
    positive_days = (df["Daily_Return"] > 0).sum()
    negative_days = (df["Daily_Return"] < 0).sum()
    win_pct = positive_days / (positive_days + negative_days) * 100

    # Find longest drawdown
    longest_dd = 0
    current_dd_len = 0
    for val in drawdown:
        if val < 0:
            current_dd_len += 1
            longest_dd = max(longest_dd, current_dd_len)
        else:
            current_dd_len = 0

    stats_data = {
        "Return Metrics": {
            "Total Return": f"{total_return:.2%}",
            "CAGR": f"{cagr:.2%}",
            "Best Day": f"{best_day:.2%}",
            "Worst Day": f"{worst_day:.2%}",
            "Avg Daily Return": f"{df['Daily_Return'].mean():.4%}",
            "Median Daily Return": f"{df['Daily_Return'].median():.4%}",
        },
        "Risk Metrics": {
            "Annualized Volatility": f"{ann_vol:.2%}",
            "Max Drawdown": f"{max_dd:.2%}",
            "Longest Drawdown (days)": f"{longest_dd}",
            "Skewness": f"{skew:.3f}",
            "Kurtosis": f"{kurt:.3f}",
            "Downside Deviation": f"{downside:.2%}",
        },
        "Ratio Metrics": {
            "Sharpe Ratio": f"{sharpe:.3f}",
            "Sortino Ratio": f"{sortino:.3f}",
            "Calmar Ratio": f"{cagr / abs(max_dd):.3f}" if max_dd != 0 else "N/A",
            "Win Rate (Daily)": f"{win_pct:.1f}%",
            "Positive Days": f"{positive_days}",
            "Negative Days": f"{negative_days}",
        },
    }

    scols = st.columns(3)
    for col, (section, metrics) in zip(scols, stats_data.items()):
        with col:
            st.markdown(f"**{section}**")
            for k, v in metrics.items():
                st.markdown(f"- {k}: `{v}`")


# ---- TAB 7: Benchmark Comparison ----
with tab7:
    st.subheader(f"{ticker} vs {bench_ticker}")

    if df_bench is not None and not df_bench.empty:
        df_bench_ret = df_bench["Close"].pct_change().dropna()

        # Normalize both to 100
        norm_ticker = (1 + df["Daily_Return"].fillna(0)).cumprod() * 100
        # Align benchmark to same date range
        common_dates = norm_ticker.index.intersection(df_bench_ret.index)
        if len(common_dates) > 10:
            bench_aligned = df_bench_ret.loc[common_dates]
            norm_bench = (1 + bench_aligned).cumprod() * 100

            fig_comp = go.Figure()
            fig_comp.add_trace(go.Scatter(
                x=norm_ticker.index, y=norm_ticker.values, mode="lines",
                name=ticker, line=dict(color="#00d1ff", width=2.5),
            ))
            fig_comp.add_trace(go.Scatter(
                x=norm_bench.index, y=norm_bench.values, mode="lines",
                name=bench_ticker, line=dict(color="white", width=2, dash="dot"),
            ))
            fig_comp.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Normalized ($100 Base)", hovermode="x unified",
            )
            st.plotly_chart(fig_comp, use_container_width=True)

            # Relative performance
            st.subheader("Relative Performance (Alpha)")
            relative = norm_ticker.loc[common_dates] / norm_bench.values
            fig_rel = go.Figure()
            fig_rel.add_trace(go.Scatter(
                x=relative.index, y=relative.values, mode="lines",
                line=dict(color="#00ff96", width=2),
            ))
            fig_rel.add_hline(y=1.0, line_dash="dot", line_color="white")
            fig_rel.update_layout(
                template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title=f"{ticker} / {bench_ticker}", hovermode="x unified",
            )
            st.plotly_chart(fig_rel, use_container_width=True)
            st.caption("> 1.0 = outperforming benchmark, < 1.0 = underperforming")

            # Rolling beta
            st.subheader("Rolling Beta (60-Day)")
            ticker_rets = df["Daily_Return"].loc[common_dates]
            bench_rets = df_bench_ret.loc[common_dates]

            rolling_cov = ticker_rets.rolling(60).cov(bench_rets)
            rolling_var = bench_rets.rolling(60).var()
            rolling_beta = rolling_cov / rolling_var

            fig_beta = go.Figure()
            fig_beta.add_trace(go.Scatter(
                x=rolling_beta.index, y=rolling_beta.values, mode="lines",
                line=dict(color="#ffaa00", width=2),
            ))
            fig_beta.add_hline(y=1.0, line_dash="dot", line_color="white")
            fig_beta.update_layout(
                template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Beta", hovermode="x unified",
            )
            st.plotly_chart(fig_beta, use_container_width=True)
        else:
            st.warning("Insufficient overlapping dates for comparison.")
    else:
        if ticker == bench_ticker:
            st.info("Ticker and benchmark are the same. Enter a different benchmark in the sidebar.")
        else:
            st.warning("Benchmark data unavailable.")


# ---- TAB 8: Macro Correlation ----
with tab8:
    st.subheader("Correlation with Macro Variables")

    macro_series = {}
    if not vix.empty:
        macro_series["VIX"] = vix
    if not tny.empty:
        macro_series["10Y Yield"] = tny
    if not dxy.empty:
        macro_series["Dollar (DXY)"] = dxy

    if macro_series:
        ticker_rets_m = df["Daily_Return"].dropna()

        macro_colors = {"VIX": "#ff4b4b", "10Y Yield": "#00ff96", "Dollar (DXY)": "#ffaa00"}

        for macro_name, macro_data in macro_series.items():
            st.markdown(f"### {ticker} vs {macro_name}")

            macro_rets = macro_data.pct_change().dropna()
            common = ticker_rets_m.index.intersection(macro_rets.index)

            if len(common) > 60:
                t_rets = ticker_rets_m.loc[common]
                m_rets = macro_rets.loc[common]

                overall_corr = t_rets.corr(m_rets)
                rolling_corr = t_rets.rolling(60).corr(m_rets)

                cc1, cc2 = st.columns(2)
                cc1.metric(f"Overall Correlation", f"{overall_corr:.3f}")
                recent = rolling_corr.iloc[-1] if not rolling_corr.isna().all() else 0
                cc2.metric(f"Current 60D Correlation", f"{recent:.3f}")

                fig_mc = go.Figure()
                fig_mc.add_trace(go.Scatter(
                    x=rolling_corr.index, y=rolling_corr.values, mode="lines",
                    line=dict(color=macro_colors.get(macro_name, "#00d1ff"), width=2),
                ))
                fig_mc.add_hline(y=0, line_color="white", line_width=1)
                fig_mc.add_hrect(y0=0.3, y1=1, fillcolor="rgba(0,255,150,0.03)", line_width=0)
                fig_mc.add_hrect(y0=-1, y1=-0.3, fillcolor="rgba(255,75,75,0.03)", line_width=0)
                fig_mc.update_layout(
                    template="plotly_dark", height=250, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Rolling 60D Correlation", yaxis=dict(range=[-1, 1]),
                    hovermode="x unified",
                )
                st.plotly_chart(fig_mc, use_container_width=True)
            else:
                st.caption(f"Insufficient overlapping data for {macro_name}.")
    else:
        st.warning("Macro data unavailable.")


# --- AI Context & Footer ---
current_ytd_ret = df[df["Year"] == current_year]["Daily_Return"].fillna(0).add(1).cumprod().iloc[-1] - 1 if not df[df["Year"] == current_year].empty else 0
best_month = m_df.groupby("Month_Name")["Return"].mean().idxmax()
worst_month = m_df.groupby("Month_Name")["Return"].mean().idxmin()

ctx = (f"Historical Analysis for {ticker}. {current_year} YTD: {current_ytd_ret:.2%}. "
       f"CAGR: {cagr:.2%}. Sharpe: {sharpe:.2f}. Max DD: {max_dd:.1%}. "
       f"Best month historically: {best_month}. Worst: {worst_month}.")
run_sidebar_chatbot(ctx)
render_data_source_footer()
