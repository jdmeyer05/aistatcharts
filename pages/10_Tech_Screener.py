import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.data_engine import (
    fetch_massive_data, format_massive_ticker, render_data_source_footer,
    fetch_polygon_sma, fetch_polygon_rsi, fetch_polygon_macd,
)
from src.chatbot import run_sidebar_chatbot

from src.layout import setup_page, get_active_ticker, set_active_ticker, fun_loader
setup_page("10_Tech_Screener")

st.title("🛰️ Advanced Technical Screener")
st.markdown("Multi-dimensional technical analysis: Trend (EMAs), Momentum (MACD/RSI), and Volatility (Bollinger Bands).")

# --- Controls ---
with st.form("tech_settings"):
    _c1, _c2, _c3, _c4, _c5, _c6 = st.columns([2, 2, 1, 1, 1, 1])
    with _c1:
        raw_ticker = st.text_input("Ticker", value=get_active_ticker())
    with _c2:
        lookback = st.slider("Lookback (Days)", 90, 730, 365, step=30)
    with _c3:
        rsi_period = st.number_input("RSI Period", value=14)
    with _c4:
        macd_fast = st.number_input("MACD Fast", value=12)
    with _c5:
        macd_slow = st.number_input("MACD Slow", value=26)
    with _c6:
        bb_window = st.number_input("Bollinger Period", value=20)
    submit = st.form_submit_button("Run Technicals", use_container_width=True)

ticker = format_massive_ticker(raw_ticker)
set_active_ticker(ticker)

# --- CALCULATE INDICATORS ---
if submit or 'tech_df' not in st.session_state or st.session_state.get('tech_ticker') != ticker:
    with fun_loader("compute"):
        df = fetch_massive_data(ticker, lookback)
        
        if df is None or df.empty:
            st.error(f"Failed to fetch data for {ticker}.")
            st.stop()
            
        # 1. EMAs (Trend)
        df['EMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()
        df['EMA_50'] = df['Close'].ewm(span=50, adjust=False).mean()
        df['EMA_200'] = df['Close'].ewm(span=200, adjust=False).mean()
        
        # 2. RSI (Momentum)
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0.0).ewm(alpha=1/rsi_period, adjust=False).mean()
        loss = (-delta.where(delta < 0, 0.0)).ewm(alpha=1/rsi_period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        df['RSI'] = (100 - (100 / (1 + rs))).fillna(50)
        
        # 3. MACD (Trend/Momentum)
        ema_fast = df['Close'].ewm(span=macd_fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=macd_slow, adjust=False).mean()
        df['MACD'] = ema_fast - ema_slow
        df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
        df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']
        
        # 4. Bollinger Bands (Volatility)
        df['BB_Mid'] = df['Close'].rolling(window=bb_window).mean()
        df['BB_Std'] = df['Close'].rolling(window=bb_window).std()
        df['BB_Upper'] = df['BB_Mid'] + (df['BB_Std'] * 2)
        df['BB_Lower'] = df['BB_Mid'] - (df['BB_Std'] * 2)

        st.session_state.tech_df = df
        st.session_state.tech_ticker = ticker

# --- RENDER DASHBOARD ---
if 'tech_df' in st.session_state:
    df = st.session_state.tech_df
    current_px = df['Close'].iloc[-1]
    
    # Trim NA values from the beginning caused by rolling windows
    plot_df = df.dropna().tail(252) # Show last 1 trading year in charts to keep it readable
    
    # --- 2x2 CHART GRID ---
    r1c1, r1c2 = st.columns(2)
    
    # 1. Trend (Price + EMAs)
    with r1c1:
        st.subheader("1. Price Action & Trend (EMAs)")
        fig_trend = go.Figure()
        fig_trend.add_trace(go.Scatter(x=plot_df.index, y=plot_df['Close'], name='Close', line=dict(color='white', width=2)))
        fig_trend.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA_20'], name='EMA 20', line=dict(color='#00d1ff', width=1.5)))
        fig_trend.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA_50'], name='EMA 50', line=dict(color='#ffaa00', width=1.5)))
        fig_trend.add_trace(go.Scatter(x=plot_df.index, y=plot_df['EMA_200'], name='EMA 200', line=dict(color='#ff4b4b', width=1.5, dash='dot')))
        fig_trend.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), hovermode='x unified')
        st.plotly_chart(fig_trend, use_container_width=True)

    # 2. Volatility (Bollinger Bands)
    with r1c2:
        st.subheader("2. Volatility (Bollinger Bands)")
        fig_bb = go.Figure()
        fig_bb.add_trace(go.Scatter(x=plot_df.index, y=plot_df['Close'], name='Close', line=dict(color='white', width=1.5)))
        fig_bb.add_trace(go.Scatter(x=plot_df.index, y=plot_df['BB_Upper'], name='Upper Band', line=dict(color='rgba(173, 127, 255, 0.5)')))
        fig_bb.add_trace(go.Scatter(x=plot_df.index, y=plot_df['BB_Lower'], name='Lower Band', line=dict(color='rgba(173, 127, 255, 0.5)'), fill='tonexty', fillcolor='rgba(173, 127, 255, 0.1)'))
        fig_bb.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), hovermode='x unified')
        st.plotly_chart(fig_bb, use_container_width=True)

    r2c1, r2c2 = st.columns(2)
    
    # 3. Momentum (MACD)
    with r2c1:
        st.subheader("3. Momentum (MACD)")
        fig_macd = go.Figure()
        
        # Color histogram based on positive/negative
        colors = np.where(plot_df['MACD_Hist'] >= 0, '#00ff96', '#ff4b4b')
        fig_macd.add_trace(go.Bar(x=plot_df.index, y=plot_df['MACD_Hist'], name='Histogram', marker_color=colors))
        fig_macd.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MACD'], name='MACD Line', line=dict(color='#00d1ff', width=2)))
        fig_macd.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MACD_Signal'], name='Signal Line', line=dict(color='#ffaa00', width=2)))
        fig_macd.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), hovermode='x unified')
        st.plotly_chart(fig_macd, use_container_width=True)

    # 4. Strength (RSI)
    with r2c2:
        st.subheader("4. Relative Strength (RSI)")
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Scatter(x=plot_df.index, y=plot_df['RSI'], name='RSI', line=dict(color='#ad7fff', width=2)))
        
        # Overbought / Oversold zones
        fig_rsi.add_hline(y=70, line_dash="dash", line_color="#ff4b4b", annotation_text="Overbought (70)")
        fig_rsi.add_hline(y=30, line_dash="dash", line_color="#00ff96", annotation_text="Oversold (30)")
        
        # Color background for extreme zones
        fig_rsi.add_hrect(y0=70, y1=100, fillcolor="rgba(255, 75, 75, 0.1)", line_width=0)
        fig_rsi.add_hrect(y0=0, y1=30, fillcolor="rgba(0, 255, 150, 0.1)", line_width=0)
        
        fig_rsi.update_layout(template="plotly_dark", height=350, margin=dict(t=20, b=0, l=0, r=0), yaxis=dict(range=[0, 100]))
        st.plotly_chart(fig_rsi, use_container_width=True)

    # --- AI CONTEXT INJECTION ---
    latest_rsi = df['RSI'].iloc[-1]
    latest_macd = df['MACD'].iloc[-1]
    latest_sig = df['MACD_Signal'].iloc[-1]
    macd_cross = "Bullish" if latest_macd > latest_sig else "Bearish"
    ema_trend = "Bullish" if df['EMA_20'].iloc[-1] > df['EMA_50'].iloc[-1] else "Bearish"
    
    ctx = (f"Technical Scan for {ticker}. Spot: ${current_px:.2f}. "
           f"RSI: {latest_rsi:.1f}. MACD Cross: {macd_cross}. Short-Term Trend (EMA20 vs EMA50): {ema_trend}.")
    run_sidebar_chatbot(ctx)


# ═══════════════════════════════════════════════
# UNIVERSE SCAN — rank multiple tickers by technical quality
# ═══════════════════════════════════════════════

st.markdown("---")
st.subheader("Universe Technical Scan")
with st.expander("How the scan works"):
    st.markdown("""
Scans a universe of tickers and ranks them by technical setup quality.
A **bullish alignment** means EMA trend, RSI, and MACD all agree.

**Scores:**
- EMA Trend: +1 if EMA20 > EMA50 (bullish), -1 if bearish
- RSI Signal: +1 if 40-70 (healthy momentum), -1 if <30 (oversold) or >70 (overbought)
- MACD Cross: +1 if MACD > Signal (bullish), -1 if bearish
- **Total Score**: -3 (max bearish) to +3 (max bullish)
""")

scan_universe = st.text_input(
    "Tickers (comma-separated)",
    value="SPY, QQQ, AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, AMD, "
          "XLE, XLF, XLK, XLV, XLI, XLC, XLY, XLP, XLU, XLB, XLRE, "
          "TLT, GLD, USO, IWM, EEM",
    key="scan_universe_input",
)

if st.button("Run Universe Scan", type="primary", use_container_width=True, key="run_universe_scan"):
    scan_tickers = [format_massive_ticker(t.strip()) for t in scan_universe.split(",") if t.strip()]

    scan_rows = []
    progress = st.progress(0, text="Scanning...")
    for idx, stk in enumerate(scan_tickers):
        progress.progress((idx + 1) / len(scan_tickers), text=f"Scanning {stk}...")
        try:
            sdf = fetch_massive_data(stk, 252)
            if sdf is None or sdf.empty or len(sdf) < 60:
                continue

            sc = sdf["Close"]
            spot = float(sc.iloc[-1])

            # EMA trend
            ema20 = sc.ewm(span=20).mean()
            ema50 = sc.ewm(span=50).mean()
            ema_bull = 1 if float(ema20.iloc[-1]) > float(ema50.iloc[-1]) else -1

            # RSI
            _delta = sc.diff()
            _gain = _delta.where(_delta > 0, 0.0).ewm(alpha=1/14, adjust=False).mean()
            _loss = (-_delta.where(_delta < 0, 0.0)).ewm(alpha=1/14, adjust=False).mean()
            _rs = _gain / _loss.replace(0, np.nan)
            rsi_val = float((100 - (100 / (1 + _rs))).fillna(50).iloc[-1])
            rsi_score = 1 if 40 <= rsi_val <= 70 else (-1 if rsi_val < 30 or rsi_val > 70 else 0)

            # MACD
            _e12 = sc.ewm(span=12).mean()
            _e26 = sc.ewm(span=26).mean()
            _macd = _e12 - _e26
            _sig = _macd.ewm(span=9).mean()
            macd_bull = 1 if float(_macd.iloc[-1]) > float(_sig.iloc[-1]) else -1

            total_score = ema_bull + rsi_score + macd_bull
            alignment = "Bullish" if total_score >= 2 else ("Bearish" if total_score <= -2 else "Mixed")

            # 20-day performance
            ret_20 = (spot / float(sc.iloc[-20]) - 1) * 100 if len(sc) > 20 else 0

            scan_rows.append({
                "Ticker": stk,
                "Price": round(spot, 2),
                "20d Return": round(ret_20, 1),
                "EMA Trend": "Bull" if ema_bull > 0 else "Bear",
                "RSI": round(rsi_val, 1),
                "MACD": "Bull" if macd_bull > 0 else "Bear",
                "Score": total_score,
                "Alignment": alignment,
            })
        except Exception:
            continue

    progress.empty()

    if scan_rows:
        scan_df = pd.DataFrame(scan_rows).sort_values("Score", ascending=False)

        # Write signals for aligned tickers
        try:
            from src.signal_engine import write_signal
            for _, _sr in scan_df.iterrows():
                if _sr["Score"] >= 2:
                    write_signal("tech_screener", _sr["Ticker"], "bull", 0.7,
                                 reasoning=f"Score {_sr['Score']}/3 — EMA/RSI/MACD aligned bullish")
                elif _sr["Score"] <= -2:
                    write_signal("tech_screener", _sr["Ticker"], "bear", 0.7,
                                 reasoning=f"Score {_sr['Score']}/3 — EMA/RSI/MACD aligned bearish")
        except Exception:
            pass
        st.dataframe(
            scan_df.style.apply(
                lambda row: ["background-color: rgba(0,255,150,0.1)"] * len(row)
                if row["Score"] >= 2 else (
                    ["background-color: rgba(255,68,68,0.1)"] * len(row)
                    if row["Score"] <= -2 else [""] * len(row)
                ), axis=1,
            ),
            use_container_width=True, hide_index=True,
        )

        bull_count = (scan_df["Score"] >= 2).sum()
        bear_count = (scan_df["Score"] <= -2).sum()
        st.caption(
            f"**{bull_count}** tickers in bullish alignment | "
            f"**{bear_count}** bearish | "
            f"**{len(scan_df) - bull_count - bear_count}** mixed"
        )

        if bull_count > len(scan_df) * 0.6:
            st.success("Broad bullish alignment — most tickers confirm uptrend across all indicators.")
        elif bear_count > len(scan_df) * 0.6:
            st.error("Broad bearish alignment — most tickers confirm downtrend.")
        else:
            st.info("Mixed signals — no clear market-wide direction. Focus on individual setups.")
    else:
        st.warning("No data returned. Check tickers and try again.")

render_data_source_footer()
