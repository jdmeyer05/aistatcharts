import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.layout import setup_page, get_active_ticker, set_active_ticker, fun_loader
setup_page("11_Algo_Backtester")

st.title("🏗️ Algo Backtester & Optimizer")
st.markdown("Test trading strategies, optimize parameters, and analyze performance with institutional-grade analytics.")

# --- STRATEGY ENGINE ---
def calculate_rsi(data, periods=14):
    delta = data.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=int(periods)).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=int(periods)).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def calculate_atr(df, period=14):
    high, low, close = df["High"] if "High" in df.columns else df["Close"], df["Low"] if "Low" in df.columns else df["Close"], df["Close"]
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(int(period)).mean()


STRATEGIES = [
    "1. SMA Crossover",
    "2. Golden/Death Cross",
    "3. EMA Crossover",
    "4. MACD Momentum",
    "5. RSI Mean Reversion",
    "6. Bollinger Band Mean Reversion",
    "7. Bollinger Band Breakout",
    "8. Donchian Channel Breakout",
    "9. Z-Score Mean Reversion",
    "10. Simple Momentum (Price > SMA)",
    "11. Dual Momentum (Abs + Relative SMA)",
    "12. Volume-Weighted Momentum",
    "13. ATR Trailing Stop",
]


def run_strategy(df, strat_name, p1=None, p2=None):
    df = df.copy()
    c = df["Close"]
    df["Position"] = 0
    df["Signal"] = np.nan

    if strat_name == "1. SMA Crossover":
        p1, p2 = p1 or 10, p2 or 21
        df["Fast"] = c.rolling(int(p1)).mean()
        df["Slow"] = c.rolling(int(p2)).mean()
        df["Position"] = np.where(df["Fast"] > df["Slow"], 1, -1)

    elif strat_name == "2. Golden/Death Cross":
        p1, p2 = p1 or 50, p2 or 200
        df["Fast"] = c.rolling(int(p1)).mean()
        df["Slow"] = c.rolling(int(p2)).mean()
        df["Position"] = np.where(df["Fast"] > df["Slow"], 1, -1)

    elif strat_name == "3. EMA Crossover":
        p1, p2 = p1 or 9, p2 or 21
        df["Fast"] = c.ewm(span=int(p1), adjust=False).mean()
        df["Slow"] = c.ewm(span=int(p2), adjust=False).mean()
        df["Position"] = np.where(df["Fast"] > df["Slow"], 1, -1)

    elif strat_name == "4. MACD Momentum":
        p1, p2 = p1 or 12, p2 or 26
        ema_fast = c.ewm(span=int(p1), adjust=False).mean()
        ema_slow = c.ewm(span=int(p2), adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=9, adjust=False).mean()
        df["Position"] = np.where(macd > signal, 1, -1)

    elif strat_name == "5. RSI Mean Reversion":
        p1, p2 = p1 or 14, p2 or 30
        df["RSI"] = calculate_rsi(c, p1)
        df.loc[df["RSI"] < p2, "Signal"] = 1
        df.loc[df["RSI"] > (100 - p2), "Signal"] = -1
        df["Position"] = df["Signal"].ffill().fillna(0)

    elif "Bollinger" in strat_name:
        p1, p2 = p1 or 20, p2 or 2.0
        sma = c.rolling(int(p1)).mean()
        std = c.rolling(int(p1)).std()
        upper = sma + (std * p2)
        lower = sma - (std * p2)
        if strat_name == "6. Bollinger Band Mean Reversion":
            df.loc[c < lower, "Signal"] = 1
            df.loc[c > upper, "Signal"] = -1
        else:
            df.loc[c > upper, "Signal"] = 1
            df.loc[c < lower, "Signal"] = -1
        df["Position"] = df["Signal"].ffill().fillna(0)

    elif strat_name == "8. Donchian Channel Breakout":
        p1 = p1 or 20
        df["Upper"] = c.rolling(int(p1)).max().shift(1)
        df["Lower"] = c.rolling(int(p1)).min().shift(1)
        df.loc[c > df["Upper"], "Signal"] = 1
        df.loc[c < df["Lower"], "Signal"] = -1
        df["Position"] = df["Signal"].ffill().fillna(0)

    elif strat_name == "9. Z-Score Mean Reversion":
        p1, p2 = p1 or 20, p2 or 2.0
        df["Z"] = (c - c.rolling(int(p1)).mean()) / c.rolling(int(p1)).std()
        df.loc[df["Z"] < -p2, "Signal"] = 1
        df.loc[df["Z"] > p2, "Signal"] = -1
        df["Position"] = df["Signal"].ffill().fillna(0)

    elif strat_name == "10. Simple Momentum (Price > SMA)":
        p1 = p1 or 20
        sma = c.rolling(int(p1)).mean()
        df["Position"] = np.where(c > sma, 1, -1)

    elif strat_name == "11. Dual Momentum (Abs + Relative SMA)":
        p1, p2 = p1 or 50, p2 or 200
        sma_fast = c.rolling(int(p1)).mean()
        sma_slow = c.rolling(int(p2)).mean()
        momentum = c / c.shift(int(p1)) - 1
        df["Position"] = np.where((c > sma_fast) & (c > sma_slow) & (momentum > 0), 1,
                         np.where((c < sma_fast) & (c < sma_slow) & (momentum < 0), -1, 0))

    elif strat_name == "12. Volume-Weighted Momentum":
        p1 = p1 or 20
        if "Volume" in df.columns:
            vwap = (c * df["Volume"]).rolling(int(p1)).sum() / df["Volume"].rolling(int(p1)).sum()
        else:
            vwap = c.rolling(int(p1)).mean()
        df["Position"] = np.where(c > vwap, 1, -1)

    elif strat_name == "13. ATR Trailing Stop":
        p1, p2 = p1 or 14, p2 or 2.0
        atr = calculate_atr(df, int(p1))
        # Proper trailing stop: track high/low from trade entry, reset on flip
        position = np.zeros(len(c))
        trade_high = c.iloc[0]
        trade_low = c.iloc[0]
        for i in range(1, len(c)):
            price = c.iloc[i]
            atr_val = atr.iloc[i] if pd.notna(atr.iloc[i]) else 0
            if position[i - 1] >= 0:  # Long or flat — track for long stop
                trade_high = max(trade_high, price)
                stop_long = trade_high - atr_val * p2
                if price < stop_long and position[i - 1] == 1:
                    position[i] = -1
                    trade_low = price  # Reset for short tracking
                    trade_high = price
                elif price > (trade_low + atr_val * p2) or position[i - 1] == 0:
                    position[i] = 1
                    trade_high = max(trade_high, price)
                else:
                    position[i] = position[i - 1]
            if position[i - 1] < 0:  # Short — track for short stop
                trade_low = min(trade_low, price)
                stop_short = trade_low + atr_val * p2
                if price > stop_short:
                    position[i] = 1
                    trade_high = price  # Reset for long tracking
                    trade_low = price
                else:
                    position[i] = -1
                    trade_low = min(trade_low, price)
        df["Position"] = position

    return df["Position"]


def get_optimization_grid(strat_name):
    if "Crossover" in strat_name or "Cross" in strat_name or "MACD" in strat_name or "Dual" in strat_name:
        return range(5, 50, 5), range(15, 100, 5), "Fast Period", "Slow Period"
    elif "RSI" in strat_name:
        return range(5, 30, 2), range(15, 45, 5), "RSI Period", "Oversold Boundary"
    elif "Bollinger" in strat_name or "Z-Score" in strat_name or "ATR" in strat_name:
        return range(10, 60, 5), [1.5, 2.0, 2.5, 3.0], "Lookback Period", "Multiplier"
    elif "Donchian" in strat_name or "Simple Momentum" in strat_name or "Volume" in strat_name:
        return range(10, 100, 5), [None], "Lookback Period", None
    return [None], [None], "Param 1", "Param 2"


def extract_trades(df):
    """Extract individual trades from position series."""
    trades = []
    pos = df["Position"]
    in_trade = False
    entry_date = None
    entry_price = None
    direction = 0

    for i in range(1, len(df)):
        curr_pos = pos.iloc[i]
        prev_pos = pos.iloc[i - 1]

        if curr_pos != prev_pos:
            # Close existing trade
            if in_trade:
                exit_date = df.index[i]
                exit_price = df["Close"].iloc[i]
                if direction == 1:
                    pnl_pct = (exit_price / entry_price - 1) * 100
                else:
                    pnl_pct = (entry_price / exit_price - 1) * 100
                duration = (exit_date - entry_date).days
                trades.append({
                    "Entry": entry_date.strftime("%Y-%m-%d"),
                    "Exit": exit_date.strftime("%Y-%m-%d"),
                    "Direction": "Long" if direction == 1 else "Short",
                    "Entry Price": entry_price,
                    "Exit Price": exit_price,
                    "P&L %": pnl_pct,
                    "Duration": duration,
                })

            # Open new trade
            if curr_pos != 0:
                in_trade = True
                entry_date = df.index[i]
                entry_price = df["Close"].iloc[i]
                direction = curr_pos
            else:
                in_trade = False

    return pd.DataFrame(trades)


# --- Controls ---
_c1, _c2, _c3, _c4, _c5 = st.columns([2, 2, 2, 1, 1])
with _c1:
    raw_ticker = st.text_input("Ticker", value=get_active_ticker())
with _c2:
    lookback = st.slider("Historical Data (Days)", 252, 5040, 1260, step=252)
with _c3:
    strategy = st.selectbox("Algorithmic Strategy", STRATEGIES)
with _c4:
    commission_bps = st.number_input("Commission (bps)", value=5, step=1)
with _c5:
    slippage_bps = st.number_input("Slippage (bps)", value=5, step=1)
_b1, _b2, _b3, _ = st.columns([1, 1, 1, 3])
run_test = _b1.button("Run Standard", use_container_width=True)
run_opt = _b2.button("Optimize", type="primary", use_container_width=True)
run_compare = _b3.button("Compare All", use_container_width=True)

with st.expander("Advanced Costs"):
    borrow_rate = st.slider("Short Borrow Rate (% annualized)", 0.0, 10.0, 1.5, step=0.5,
                            help="Annual cost of borrowing shares for short positions. Typical: 0.5-3% for liquid stocks, 5-10% for hard-to-borrow.")

ticker = format_massive_ticker(raw_ticker)
set_active_ticker(ticker)
total_cost_pct = (commission_bps + slippage_bps) / 10000  # Convert bps to decimal
daily_borrow_cost = borrow_rate / 100 / 252  # Annualized → daily

# --- EXECUTION ---
if run_test or run_opt or "bt_data" not in st.session_state or st.session_state.get("bt_ticker") != ticker:

    df_base = fetch_massive_data(ticker, lookback + 250)
    if df_base is None or df_base.empty:
        st.error("Failed to load data.")
        st.stop()

    df_base["Returns"] = np.log(df_base["Close"] / df_base["Close"].shift(1))

    p1_final, p2_final = None, None
    opt_msg = ""

    if run_opt:
        with fun_loader("compute"):
            grid_p1, grid_p2, name_p1, name_p2 = get_optimization_grid(strategy)
            best_sharpe = -np.inf

            progress_bar = st.progress(0)
            total_iterations = len(list(grid_p1)) * len(list(grid_p2))
            current_iter = 0

            for p1 in grid_p1:
                for p2 in grid_p2:
                    current_iter += 1
                    progress_bar.progress(current_iter / total_iterations)

                    if p2 is not None and isinstance(p2, int) and p1 >= p2 and ("Crossover" in strategy or "Cross" in strategy or "MACD" in strategy or "Dual" in strategy):
                        continue

                    pos = run_strategy(df_base, strategy, p1, p2)
                    strat_ret = pos.shift(1) * df_base["Returns"]
                    # Deduct transaction costs (reversals = 2 legs)
                    trade_legs = pos.diff().abs().clip(upper=2)
                    strat_ret = strat_ret - (trade_legs * total_cost_pct)
                    # Deduct short borrow cost
                    strat_ret = strat_ret - (pos.shift(1) == -1).astype(float) * daily_borrow_cost

                    temp_eval = strat_ret.dropna().tail(lookback)
                    # Optimize for Sharpe ratio (risk-adjusted) instead of raw return
                    if len(temp_eval) > 20 and temp_eval.std() > 0:
                        sharpe = (temp_eval.mean() / temp_eval.std()) * np.sqrt(252)
                    else:
                        sharpe = -np.inf

                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        p1_final, p2_final = p1, p2

            progress_bar.empty()

            if p2_final is not None and name_p2 is not None:
                opt_msg = f"**Optimal Parameters:** {name_p1} = `{p1_final}` | {name_p2} = `{p2_final}`"
            else:
                opt_msg = f"**Optimal Parameters:** {name_p1} = `{p1_final}`"

            st.session_state.opt_msg = opt_msg
    else:
        st.session_state.opt_msg = None

    # Run final backtest
    with fun_loader("compute"):
        df = df_base.copy()
        df["Position"] = run_strategy(df, strategy, p1_final, p2_final)
        df["Strat_Returns"] = df["Position"].shift(1) * df["Returns"]

        # Apply transaction costs on position changes
        # A reversal (long→short or short→long) is 2 trades (close + open)
        pos_diff = df["Position"].diff().abs()
        df["Trades"] = (pos_diff > 0).astype(int)
        df["Trade_Legs"] = pos_diff.clip(upper=2)  # 0→1=1 leg, 1→-1=2 legs, 1→0=1 leg
        df["Strat_Returns"] = df["Strat_Returns"] - (df["Trade_Legs"] * total_cost_pct)

        # Apply short borrow cost (daily, only when short)
        df["Strat_Returns"] = df["Strat_Returns"] - (df["Position"].shift(1) == -1).astype(float) * daily_borrow_cost

        df = df.dropna().tail(lookback)

        df["Cum_Hold"] = np.exp(df["Returns"].cumsum()) * 100
        df["Cum_Strat"] = np.exp(df["Strat_Returns"].cumsum()) * 100

        st.session_state.bt_data = df
        st.session_state.bt_ticker = ticker
        st.session_state.bt_strat = strategy

# --- STRATEGY COMPARISON ---
if run_compare:
    df_cmp_base = fetch_massive_data(ticker, lookback + 250)
    if df_cmp_base is not None and not df_cmp_base.empty:
        df_cmp_base["Returns"] = np.log(df_cmp_base["Close"] / df_cmp_base["Close"].shift(1))
        cmp_results = []

        with fun_loader("compute"):
            for strat_name in STRATEGIES:
                pos = run_strategy(df_cmp_base, strat_name)
                ret = pos.shift(1) * df_cmp_base["Returns"]
                legs = pos.diff().abs().clip(upper=2)
                ret = ret - (legs * total_cost_pct)
                ret = ret - (pos.shift(1) == -1).astype(float) * daily_borrow_cost
                ret = ret.dropna().tail(lookback)

                if len(ret) < 20 or ret.std() == 0:
                    continue

                cum = np.exp(ret.cumsum())
                total_ret = (cum.iloc[-1] - 1) * 100
                yrs = len(ret) / 252
                cagr = (cum.iloc[-1] ** (1 / yrs) - 1) * 100 if yrs > 0 else 0
                sharpe = (ret.mean() / ret.std()) * np.sqrt(252)
                max_dd = ((cum / cum.cummax()) - 1).min() * 100
                n_trades = int(legs.tail(lookback).sum())
                short_pct = (pos.tail(lookback) == -1).sum() / len(ret) * 100

                cmp_results.append({
                    "Strategy": strat_name,
                    "Return": total_ret,
                    "CAGR": cagr,
                    "Sharpe": sharpe,
                    "Max DD": max_dd,
                    "Trades": n_trades,
                    "Short %": short_pct,
                })

        st.session_state.cmp_results = cmp_results

# --- RENDER ---
if "bt_data" not in st.session_state:
    st.info("Configure parameters and click **Run Standard** or **Optimize**.")
    st.stop()

df = st.session_state.bt_data

days = len(df)
years = days / 252

hold_return = (df["Cum_Hold"].iloc[-1] / 100) - 1
strat_return = (df["Cum_Strat"].iloc[-1] / 100) - 1

hold_cagr = (df["Cum_Hold"].iloc[-1] / 100) ** (1 / years) - 1 if years > 0 else 0
strat_cagr = (df["Cum_Strat"].iloc[-1] / 100) ** (1 / years) - 1 if years > 0 else 0

roll_max_strat = df["Cum_Strat"].cummax()
max_dd_strat = ((df["Cum_Strat"] / roll_max_strat) - 1).min()

roll_max_hold = df["Cum_Hold"].cummax()
max_dd_hold = ((df["Cum_Hold"] / roll_max_hold) - 1).min()

strat_sharpe = (df["Strat_Returns"].mean() / df["Strat_Returns"].std()) * np.sqrt(252) if df["Strat_Returns"].std() != 0 else 0
hold_sharpe = (df["Returns"].mean() / df["Returns"].std()) * np.sqrt(252) if df["Returns"].std() != 0 else 0

strat_vol = df["Strat_Returns"].std() * np.sqrt(252)
sortino_denom = df["Strat_Returns"][df["Strat_Returns"] < 0].std() * np.sqrt(252)
strat_sortino = (df["Strat_Returns"].mean() * 252) / sortino_denom if sortino_denom != 0 else 0

calmar = strat_cagr / abs(max_dd_strat) if max_dd_strat != 0 else 0
total_trades = int(df["Trade_Legs"].sum())

st.subheader(f"{st.session_state.bt_strat} vs Buy & Hold — {st.session_state.bt_ticker}")

if st.session_state.get("opt_msg"):
    st.success(st.session_state.opt_msg)

# --- TABS ---
tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Equity Curve",
    "Drawdown & Risk",
    "Trade Log & Stats",
    "Monthly Returns",
    "Return Distribution",
    "Position Chart",
    "Walk-Forward",
    "Strategy Comparison",
])


# ---- TAB 1: Equity Curve ----
with tab1:
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Strategy Return", f"{strat_return * 100:.2f}%", f"{(strat_return - hold_return) * 100:+.2f}% vs B&H")
    c2.metric("CAGR", f"{strat_cagr * 100:.2f}%", f"B&H: {hold_cagr * 100:.1f}%")
    c3.metric("Sharpe Ratio", f"{strat_sharpe:.2f}", f"B&H: {hold_sharpe:.2f}")
    c4.metric("Max Drawdown", f"{max_dd_strat * 100:.1f}%", f"B&H: {max_dd_hold * 100:.1f}%", delta_color="inverse")
    c5.metric("Total Trades", f"{total_trades:,.0f}", f"Cost: {total_cost_pct * 10000:.0f} bps/trade")

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df.index, y=df["Cum_Hold"], mode="lines", name="Buy & Hold",
        line=dict(color="white", width=2, dash="dot"),
    ))
    fig.add_trace(go.Scatter(
        x=df.index, y=df["Cum_Strat"], mode="lines", name="Strategy",
        line=dict(color="#00d1ff", width=3),
    ))

    # Drawdown shading
    fig.add_trace(go.Scatter(x=df.index, y=roll_max_strat, mode="lines",
                              line=dict(color="rgba(0,0,0,0)"), showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=df.index, y=df["Cum_Strat"], mode="lines",
                              fill="tonexty", fillcolor="rgba(255, 0, 0, 0.08)",
                              line=dict(color="rgba(0,0,0,0)"), name="Drawdown", hoverinfo="skip"))

    fig.update_layout(
        template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Portfolio Value ($100 Base)", hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)

    st.caption(f"Time in Market: {((df['Position'] != 0).sum() / len(df)) * 100:.1f}% | "
               f"Longs: {(df['Position'] == 1).sum()} days | Shorts: {(df['Position'] == -1).sum()} days | "
               f"Flat: {(df['Position'] == 0).sum()} days")


# ---- TAB 2: Drawdown & Risk ----
with tab2:
    st.subheader("Drawdown Analysis")

    # Underwater chart
    dd_strat = (df["Cum_Strat"] / roll_max_strat) - 1
    dd_hold = (df["Cum_Hold"] / roll_max_hold) - 1

    fig_dd = go.Figure()
    fig_dd.add_trace(go.Scatter(
        x=df.index, y=dd_strat * 100, mode="lines", name="Strategy",
        line=dict(color="#ff4b4b", width=2), fill="tozeroy", fillcolor="rgba(255, 75, 75, 0.15)",
    ))
    fig_dd.add_trace(go.Scatter(
        x=df.index, y=dd_hold * 100, mode="lines", name="Buy & Hold",
        line=dict(color="white", width=1, dash="dot"),
    ))
    fig_dd.add_hline(y=0, line_color="white", line_width=1)
    fig_dd.update_layout(
        template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Drawdown (%)", hovermode="x unified",
    )
    st.plotly_chart(fig_dd, use_container_width=True)

    # Risk metrics
    rm1, rm2, rm3, rm4, rm5 = st.columns(5)
    rm1.metric("Sortino Ratio", f"{strat_sortino:.2f}")
    rm2.metric("Calmar Ratio", f"{calmar:.2f}")
    rm3.metric("Ann. Volatility", f"{strat_vol * 100:.1f}%")
    rm4.metric("Avg Daily Return", f"{df['Strat_Returns'].mean() * 100:.3f}%")
    rm5.metric("Skewness", f"{df['Strat_Returns'].skew():.2f}")

    # Rolling Sharpe
    st.subheader("Rolling Sharpe Ratio (60-Day)")
    rolling_sharpe = (df["Strat_Returns"].rolling(60).mean() / df["Strat_Returns"].rolling(60).std()) * np.sqrt(252)

    fig_rs = go.Figure()
    rs_colors = ["#00ff96" if v > 0 else "#ff4b4b" for v in rolling_sharpe.fillna(0)]
    fig_rs.add_trace(go.Scatter(
        x=rolling_sharpe.index, y=rolling_sharpe.values,
        mode="lines", line=dict(color="#00d1ff", width=2),
    ))
    fig_rs.add_hline(y=0, line_color="white", line_width=1)
    fig_rs.add_hline(y=1, line_dash="dot", line_color="#00ff96", annotation_text="Sharpe = 1")
    fig_rs.add_hline(y=-1, line_dash="dot", line_color="#ff4b4b", annotation_text="Sharpe = -1")
    fig_rs.add_hrect(y0=0, y1=10, fillcolor="rgba(0, 255, 150, 0.03)", line_width=0)
    fig_rs.add_hrect(y0=-10, y1=0, fillcolor="rgba(255, 75, 75, 0.03)", line_width=0)
    fig_rs.update_layout(
        template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Rolling Sharpe", hovermode="x unified",
    )
    st.plotly_chart(fig_rs, use_container_width=True)


# ---- TAB 3: Trade Log & Stats ----
with tab3:
    df_trades = extract_trades(df)

    if not df_trades.empty:
        # Trade statistics
        winners = df_trades[df_trades["P&L %"] > 0]
        losers = df_trades[df_trades["P&L %"] <= 0]

        win_rate = len(winners) / len(df_trades) * 100 if len(df_trades) > 0 else 0
        avg_win = winners["P&L %"].mean() if not winners.empty else 0
        avg_loss = losers["P&L %"].mean() if not losers.empty else 0
        profit_factor = abs(winners["P&L %"].sum() / losers["P&L %"].sum()) if not losers.empty and losers["P&L %"].sum() != 0 else float("inf")
        avg_duration = df_trades["Duration"].mean()
        max_win = df_trades["P&L %"].max()
        max_loss = df_trades["P&L %"].min()

        # Streaks
        results = (df_trades["P&L %"] > 0).astype(int)
        streaks = results.groupby((results != results.shift()).cumsum())
        win_streaks = [len(g) for _, g in streaks if g.iloc[0] == 1]
        lose_streaks = [len(g) for _, g in streaks if g.iloc[0] == 0]
        max_win_streak = max(win_streaks) if win_streaks else 0
        max_lose_streak = max(lose_streaks) if lose_streaks else 0

        st.subheader("Trade Statistics")
        ts1, ts2, ts3, ts4, ts5, ts6 = st.columns(6)
        ts1.metric("Total Trades", f"{len(df_trades)}")
        ts2.metric("Win Rate", f"{win_rate:.1f}%")
        ts3.metric("Profit Factor", f"{profit_factor:.2f}" if profit_factor != float("inf") else "∞")
        ts4.metric("Avg Winner", f"{avg_win:+.2f}%")
        ts5.metric("Avg Loser", f"{avg_loss:+.2f}%")
        ts6.metric("Avg Duration", f"{avg_duration:.0f}d")

        ts7, ts8, ts9, ts10 = st.columns(4)
        ts7.metric("Best Trade", f"{max_win:+.2f}%")
        ts8.metric("Worst Trade", f"{max_loss:+.2f}%")
        ts9.metric("Win Streak", f"{max_win_streak}")
        ts10.metric("Lose Streak", f"{max_lose_streak}")

        st.divider()

        # Trade P&L chart
        st.subheader("Trade P&L Sequence")
        fig_trades = go.Figure()
        trade_colors = ["#00ff96" if v > 0 else "#ff4b4b" for v in df_trades["P&L %"]]
        fig_trades.add_trace(go.Bar(
            x=list(range(1, len(df_trades) + 1)),
            y=df_trades["P&L %"], marker_color=trade_colors,
            hovertemplate="Trade #%{x}<br>P&L: %{y:.2f}%<extra></extra>",
        ))
        fig_trades.add_hline(y=0, line_color="white", line_width=1)
        fig_trades.update_layout(
            template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Trade #", yaxis_title="P&L (%)",
        )
        st.plotly_chart(fig_trades, use_container_width=True)

        # Trade log table
        st.subheader("Trade Log")
        display_trades = df_trades.copy()
        display_trades["Entry Price"] = display_trades["Entry Price"].apply(lambda x: f"${x:.2f}")
        display_trades["Exit Price"] = display_trades["Exit Price"].apply(lambda x: f"${x:.2f}")
        display_trades["P&L %"] = display_trades["P&L %"].apply(lambda x: f"{x:+.2f}%")
        display_trades["Duration"] = display_trades["Duration"].apply(lambda x: f"{x}d")
        st.dataframe(display_trades, use_container_width=True, hide_index=True)

        # CSV download
        csv_data = df_trades.to_csv(index=False)
        st.download_button("Download Trade Log (CSV)", csv_data, f"trades_{ticker}_{strategy.split('.')[0].strip()}.csv",
                          "text/csv", use_container_width=True)

        # Bootstrap significance test
        st.divider()
        st.subheader("Statistical Significance")
        st.markdown("Bootstrap test: shuffles daily returns 1,000 times to estimate the probability "
                    "of achieving this Sharpe ratio by random chance.")

        n_boot = 1000
        actual_sharpe = strat_sharpe
        boot_sharpes = []
        daily_rets = df["Returns"].dropna().values
        rng = np.random.default_rng(42)
        for _ in range(n_boot):
            shuffled = rng.permutation(daily_rets)
            pos_boot = run_strategy(
                pd.DataFrame({"Close": np.exp(np.cumsum(shuffled)) * 100}),
                st.session_state.bt_strat,
            )
            boot_ret = pos_boot.shift(1).values[1:] * shuffled[1:]
            if len(boot_ret) > 0 and np.std(boot_ret) > 0:
                boot_sharpes.append((np.mean(boot_ret) / np.std(boot_ret)) * np.sqrt(252))

        if boot_sharpes:
            p_value = np.mean([s >= actual_sharpe for s in boot_sharpes])
            sig1, sig2, sig3 = st.columns(3)
            sig1.metric("Strategy Sharpe", f"{actual_sharpe:.2f}")
            sig2.metric("p-value", f"{p_value:.3f}")
            sig3.metric("Significance", "Yes (p < 0.05)" if p_value < 0.05 else "No (p >= 0.05)")

            if p_value < 0.01:
                st.success(f"Highly significant (p = {p_value:.3f}). Less than 1% chance this Sharpe is due to luck.")
            elif p_value < 0.05:
                st.success(f"Significant (p = {p_value:.3f}). Less than 5% chance this is random.")
            elif p_value < 0.10:
                st.warning(f"Marginally significant (p = {p_value:.3f}). Results could be noise.")
            else:
                st.error(f"Not significant (p = {p_value:.3f}). Cannot distinguish from random trading.")

            fig_boot = go.Figure()
            fig_boot.add_trace(go.Histogram(x=boot_sharpes, nbinsx=50, marker_color="#444", name="Random"))
            fig_boot.add_vline(x=actual_sharpe, line_color="#00d1ff", line_width=3,
                              annotation_text=f"Your strategy: {actual_sharpe:.2f}")
            fig_boot.update_layout(
                template="plotly_dark", height=250, margin=dict(t=30, b=0, l=0, r=0),
                xaxis_title="Sharpe Ratio", yaxis_title="Count",
            )
            st.plotly_chart(fig_boot, use_container_width=True)
    else:
        st.info("No completed trades in this period.")


# ---- TAB 4: Monthly Returns ----
with tab4:
    st.subheader("Monthly Returns Heatmap")

    df_monthly = df["Strat_Returns"].copy()
    df_monthly.index = pd.to_datetime(df_monthly.index)
    monthly = df_monthly.resample("ME").sum() * 100  # Log returns sum for monthly

    # Build year x month matrix
    monthly_df = pd.DataFrame({
        "Year": monthly.index.year,
        "Month": monthly.index.month,
        "Return": monthly.values,
    })
    pivot = monthly_df.pivot_table(index="Year", columns="Month", values="Return", aggfunc="sum")

    month_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    pivot.columns = [month_labels[m - 1] for m in pivot.columns]

    # Add annual total
    pivot["Annual"] = pivot.sum(axis=1)

    fig_monthly = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=pivot.columns,
        y=pivot.index.astype(str),
        colorscale=[
            [0, "#cc0000"], [0.35, "#661111"],
            [0.5, "#1a1a2e"],
            [0.65, "#116633"], [1, "#00cc66"],
        ],
        zmid=0,
        text=np.round(pivot.values, 1),
        texttemplate="%{text}%",
        textfont=dict(size=11),
        xgap=2, ygap=2,
        hovertemplate="%{y} %{x}: %{z:.2f}%<extra></extra>",
        showscale=False,
    ))
    fig_monthly.update_layout(
        template="plotly_dark", height=max(200, len(pivot) * 40 + 50),
        margin=dict(t=10, b=0, l=50, r=0),
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig_monthly, use_container_width=True)

    # Monthly stats
    st.subheader("Monthly Summary")
    ms1, ms2, ms3, ms4 = st.columns(4)
    monthly_vals = monthly.values
    ms1.metric("Best Month", f"{monthly_vals.max():.2f}%")
    ms2.metric("Worst Month", f"{monthly_vals.min():.2f}%")
    ms3.metric("Avg Month", f"{monthly_vals.mean():.2f}%")
    ms4.metric("% Positive Months", f"{(monthly_vals > 0).sum() / len(monthly_vals) * 100:.0f}%")


# ---- TAB 5: Return Distribution ----
with tab5:
    st.subheader("Daily Return Distribution")

    fig_dist = go.Figure()
    fig_dist.add_trace(go.Histogram(
        x=df["Strat_Returns"] * 100, nbinsx=80,
        marker_color="#00d1ff", opacity=0.7, name="Strategy",
    ))
    fig_dist.add_trace(go.Histogram(
        x=df["Returns"] * 100, nbinsx=80,
        marker_color="white", opacity=0.3, name="Buy & Hold",
    ))
    fig_dist.add_vline(x=0, line_color="white", line_width=1)
    fig_dist.update_layout(
        template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
        barmode="overlay", xaxis_title="Daily Return (%)", yaxis_title="Frequency",
        hovermode="x unified",
    )
    st.plotly_chart(fig_dist, use_container_width=True)

    # Stats comparison
    dc1, dc2, dc3, dc4 = st.columns(4)
    dc1.metric("Strat Mean", f"{df['Strat_Returns'].mean() * 100:.3f}%")
    dc2.metric("Strat Std Dev", f"{df['Strat_Returns'].std() * 100:.3f}%")
    dc3.metric("B&H Mean", f"{df['Returns'].mean() * 100:.3f}%")
    dc4.metric("B&H Std Dev", f"{df['Returns'].std() * 100:.3f}%")

    # QQ-style: strategy vs normal
    st.subheader("Tail Risk Analysis")
    from scipy.stats import norm as _norm
    strat_rets = df["Strat_Returns"].dropna()
    mu, sigma = strat_rets.mean(), strat_rets.std()
    percentiles = [1, 5, 10, 25, 50, 75, 90, 95, 99]
    tail_data = []
    for p in percentiles:
        actual = np.percentile(strat_rets.values, p)
        expected = _norm.ppf(p / 100, loc=mu, scale=sigma)
        tail_data.append({
            "Percentile": f"{p}%",
            "Strategy": f"{actual * 100:.3f}%",
            "Normal": f"{expected * 100:.3f}%",
            "Excess": f"{(actual - expected) * 100:+.3f}%",
        })
    st.dataframe(pd.DataFrame(tail_data), use_container_width=True, hide_index=True)


# ---- TAB 6: Position Chart ----
with tab6:
    st.subheader("Price Chart with Position Overlay")

    fig_pos = go.Figure()

    # Price line
    fig_pos.add_trace(go.Scatter(
        x=df.index, y=df["Close"], mode="lines", name="Price",
        line=dict(color="white", width=2),
    ))

    # Color regions for long/short/flat (shifted to match actual held position in equity curve)
    pos = df["Position"].shift(1).fillna(0)
    i = 0
    while i < len(df) - 1:
        curr_pos = pos.iloc[i]
        j = i
        while j < len(df) - 1 and pos.iloc[j] == curr_pos:
            j += 1

        if curr_pos == 1:
            fig_pos.add_vrect(
                x0=df.index[i], x1=df.index[min(j, len(df) - 1)],
                fillcolor="rgba(0, 255, 150, 0.08)", line_width=0,
            )
        elif curr_pos == -1:
            fig_pos.add_vrect(
                x0=df.index[i], x1=df.index[min(j, len(df) - 1)],
                fillcolor="rgba(255, 75, 75, 0.08)", line_width=0,
            )
        i = j

    fig_pos.update_layout(
        template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="Price ($)", hovermode="x unified",
    )
    st.plotly_chart(fig_pos, use_container_width=True)
    st.caption("🟢 Green = Long | 🔴 Red = Short | No shading = Flat")

    # Position timeline
    st.subheader("Position Timeline")
    fig_pos_bar = go.Figure()
    held_pos = df["Position"].shift(1).fillna(0)
    pos_colors = ["#00ff96" if v == 1 else "#ff4b4b" if v == -1 else "#333" for v in held_pos]
    fig_pos_bar.add_trace(go.Bar(
        x=df.index, y=held_pos, marker_color=pos_colors,
    ))
    fig_pos_bar.update_layout(
        template="plotly_dark", height=150, margin=dict(t=0, b=0, l=0, r=0),
        yaxis=dict(tickvals=[-1, 0, 1], ticktext=["Short", "Flat", "Long"]),
    )
    st.plotly_chart(fig_pos_bar, use_container_width=True)


# ---- TAB 7: Walk-Forward Validation ----
with tab7:
    st.subheader("Walk-Forward Validation")
    st.markdown(
        "Splits history into rolling train/test windows. Parameters are optimized on each "
        "training window and tested on the **unseen** subsequent period. The equity curve below "
        "is stitched from out-of-sample segments only — what you'd have actually earned."
    )

    wf1, wf2 = st.columns(2)
    with wf1:
        train_days = st.selectbox("Training Window (days)", [252, 504, 756], index=1)
    with wf2:
        test_days = st.selectbox("Testing Window (days)", [63, 126, 252], index=1)

    expected_folds = max(0, (lookback - train_days) // test_days)
    if expected_folds < 6:
        days_needed = train_days + (6 * test_days)
        st.caption(f"Expected folds: **{expected_folds}** with current data. Increase historical data to **{days_needed}+ days** for 6+ folds.")

    if st.button("Run Walk-Forward", type="primary", use_container_width=True):
        # Need the full base data — re-fetch with extra warmup
        df_wf_base = fetch_massive_data(ticker, lookback + 250)
        if df_wf_base is None or df_wf_base.empty:
            st.error("Failed to load data.")
        else:
            df_wf_base["Returns"] = np.log(df_wf_base["Close"] / df_wf_base["Close"].shift(1))
            df_wf_base = df_wf_base.dropna()

            grid_p1, grid_p2, name_p1, name_p2 = get_optimization_grid(strategy)
            # Convert to lists for re-use across folds
            grid_p1_list = list(grid_p1)
            grid_p2_list = list(grid_p2)

            # Walk-forward loop
            oos_segments = []
            fold_results = []
            total_len = len(df_wf_base)
            start_idx = 0
            fold_num = 0

            progress = st.progress(0)

            while start_idx + train_days + test_days <= total_len:
                fold_num += 1
                train_end = start_idx + train_days
                test_end = train_end + test_days

                df_train = df_wf_base.iloc[start_idx:train_end]
                df_test = df_wf_base.iloc[train_end:test_end]

                # Optimize on training window
                best_ret = -np.inf
                best_p1, best_p2 = None, None

                for p1_v in grid_p1_list:
                    for p2_v in grid_p2_list:
                        if p2_v is not None and isinstance(p2_v, int) and p1_v >= p2_v and (
                            "Crossover" in strategy or "Cross" in strategy or "MACD" in strategy or "Dual" in strategy
                        ):
                            continue
                        pos = run_strategy(df_train, strategy, p1_v, p2_v)
                        ret = pos.shift(1) * df_train["Returns"]
                        legs = pos.diff().abs().clip(upper=2)
                        ret = ret - (legs * total_cost_pct)
                        cum = np.exp(ret.dropna().cumsum())
                        final = cum.iloc[-1] if not cum.empty else -np.inf
                        if final > best_ret:
                            best_ret = final
                            best_p1, best_p2 = p1_v, p2_v

                # Test on out-of-sample window with optimized params
                # Include some train data at the end for indicator warmup
                warmup = min(250, len(df_train))
                df_test_full = df_wf_base.iloc[train_end - warmup:test_end].copy()
                df_test_full["Position"] = run_strategy(df_test_full, strategy, best_p1, best_p2)
                df_test_full["Strat_Returns"] = df_test_full["Position"].shift(1) * df_test_full["Returns"]
                trade_legs = df_test_full["Position"].diff().abs().clip(upper=2)
                df_test_full["Strat_Returns"] = df_test_full["Strat_Returns"] - (trade_legs * total_cost_pct)
                # Keep only the actual test period (trim warmup)
                df_oos = df_test_full.iloc[warmup:]

                oos_segments.append(df_oos)

                oos_cum = np.exp(df_oos["Strat_Returns"].dropna().cumsum()).iloc[-1] if not df_oos["Strat_Returns"].dropna().empty else 1.0
                hold_cum = np.exp(df_oos["Returns"].dropna().cumsum()).iloc[-1] if not df_oos["Returns"].dropna().empty else 1.0
                n_trades = int(df_oos["Position"].diff().abs().clip(upper=2).sum())

                fold_results.append({
                    "Fold": fold_num,
                    "Period": f"{df_oos.index[0].strftime('%Y-%m-%d')} → {df_oos.index[-1].strftime('%Y-%m-%d')}",
                    "Params": f"{name_p1}={best_p1}" + (f", {name_p2}={best_p2}" if best_p2 is not None and name_p2 else ""),
                    "Strategy": f"{(oos_cum - 1) * 100:+.2f}%",
                    "Buy & Hold": f"{(hold_cum - 1) * 100:+.2f}%",
                    "Alpha": f"{(oos_cum - hold_cum) * 100:+.2f}%",
                    "Trades": n_trades,
                })

                progress.progress(min((start_idx + train_days + test_days) / total_len, 1.0))
                start_idx += test_days  # Slide forward by test window

            progress.empty()

            if not oos_segments:
                st.warning("Not enough data for walk-forward with these window sizes. Increase historical data or reduce window sizes.")
            else:
                # Stitch OOS equity curve
                df_oos_all = pd.concat(oos_segments)
                df_oos_all["Cum_Strat_OOS"] = np.exp(df_oos_all["Strat_Returns"].cumsum()) * 100
                df_oos_all["Cum_Hold_OOS"] = np.exp(df_oos_all["Returns"].cumsum()) * 100

                # Summary metrics
                total_oos_ret = (df_oos_all["Cum_Strat_OOS"].iloc[-1] / 100) - 1
                total_hold_ret = (df_oos_all["Cum_Hold_OOS"].iloc[-1] / 100) - 1
                oos_years = len(df_oos_all) / 252
                oos_cagr = (df_oos_all["Cum_Strat_OOS"].iloc[-1] / 100) ** (1 / oos_years) - 1 if oos_years > 0 else 0
                oos_sharpe = (df_oos_all["Strat_Returns"].mean() / df_oos_all["Strat_Returns"].std()) * np.sqrt(252) if df_oos_all["Strat_Returns"].std() != 0 else 0
                oos_max_dd = ((df_oos_all["Cum_Strat_OOS"] / df_oos_all["Cum_Strat_OOS"].cummax()) - 1).min()
                folds_winning = sum(1 for f in fold_results if float(f["Alpha"].replace("%", "").replace("+", "")) > 0)

                wm1, wm2, wm3, wm4, wm5 = st.columns(5)
                wm1.metric("OOS Return", f"{total_oos_ret * 100:.2f}%", f"{(total_oos_ret - total_hold_ret) * 100:+.2f}% vs B&H")
                wm2.metric("OOS CAGR", f"{oos_cagr * 100:.2f}%")
                wm3.metric("OOS Sharpe", f"{oos_sharpe:.2f}")
                wm4.metric("OOS Max DD", f"{oos_max_dd * 100:.1f}%")
                wm5.metric("Folds Beating B&H", f"{folds_winning}/{len(fold_results)}")

                # Verdict
                if oos_sharpe > 0.5 and folds_winning > len(fold_results) * 0.5:
                    st.success(f"Walk-forward results are encouraging — positive OOS alpha in {folds_winning}/{len(fold_results)} folds with Sharpe {oos_sharpe:.2f}.")
                elif oos_sharpe > 0:
                    st.warning(f"Marginal OOS performance — some alpha but inconsistent across folds ({folds_winning}/{len(fold_results)} positive).")
                else:
                    st.error(f"Strategy does not survive walk-forward validation — OOS Sharpe {oos_sharpe:.2f}. In-sample results are likely overfit.")

                # OOS equity curve
                st.subheader("Out-of-Sample Equity Curve")
                fig_wf = go.Figure()
                fig_wf.add_trace(go.Scatter(
                    x=df_oos_all.index, y=df_oos_all["Cum_Hold_OOS"], mode="lines",
                    name="Buy & Hold", line=dict(color="white", width=2, dash="dot"),
                ))
                fig_wf.add_trace(go.Scatter(
                    x=df_oos_all.index, y=df_oos_all["Cum_Strat_OOS"], mode="lines",
                    name="Strategy (OOS)", line=dict(color="#00d1ff", width=3),
                ))

                # Shade fold boundaries
                for seg in oos_segments:
                    fig_wf.add_vline(x=seg.index[0], line_dash="dot", line_color="rgba(255,255,255,0.15)")

                fig_wf.update_layout(
                    template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
                    yaxis_title="Portfolio Value ($100 Base)", hovermode="x unified",
                )
                st.plotly_chart(fig_wf, use_container_width=True)
                st.caption("Dotted vertical lines mark fold boundaries. Each segment uses parameters optimized on prior data only.")

                # Fold detail table
                st.subheader("Fold-by-Fold Results")
                df_folds = pd.DataFrame(fold_results)
                st.dataframe(df_folds, use_container_width=True, hide_index=True)

                # Parameter stability
                st.subheader("Parameter Stability Across Folds")
                st.markdown(
                    "If optimal parameters vary wildly between folds, the strategy is likely "
                    "fitting to noise rather than capturing a real pattern."
                )
                st.dataframe(
                    df_folds[["Fold", "Period", "Params"]],
                    use_container_width=True, hide_index=True,
                )


# ---- TAB 8: Strategy Comparison ----
with tab8:
    st.subheader("Strategy Comparison")
    st.markdown("Run all 13 strategies with default parameters on the same data. Click **Compare All** above to populate.")

    cmp_results = st.session_state.get("cmp_results")

    if not cmp_results:
        st.info("Click **Compare All** in the controls bar to run all strategies.")
    else:
        df_cmp = pd.DataFrame(cmp_results).sort_values("Sharpe", ascending=False)

        # Ranked table
        df_display = df_cmp.copy()
        df_display.insert(0, "Rank", range(1, len(df_display) + 1))
        df_display["Return"] = df_display["Return"].apply(lambda x: f"{x:+.1f}%")
        df_display["CAGR"] = df_display["CAGR"].apply(lambda x: f"{x:+.1f}%")
        df_display["Sharpe"] = df_display["Sharpe"].apply(lambda x: f"{x:.2f}")
        df_display["Max DD"] = df_display["Max DD"].apply(lambda x: f"{x:.1f}%")
        df_display["Trades"] = df_display["Trades"].astype(int)
        df_display["Short %"] = df_display["Short %"].apply(lambda x: f"{x:.0f}%")
        st.dataframe(df_display, use_container_width=True, hide_index=True)

        # Sharpe comparison chart
        st.subheader("Sharpe Ratio Comparison")
        df_chart = pd.DataFrame(cmp_results).sort_values("Sharpe", ascending=True)
        short_names = [s.split(". ", 1)[1] if ". " in s else s for s in df_chart["Strategy"]]
        bar_colors = ["#00ff96" if s > 0.5 else "#ffaa00" if s > 0 else "#ff4b4b" for s in df_chart["Sharpe"]]

        fig_cmp = go.Figure()
        fig_cmp.add_trace(go.Bar(
            y=short_names, x=df_chart["Sharpe"], orientation="h",
            marker_color=bar_colors,
            text=[f"{s:.2f}" for s in df_chart["Sharpe"]], textposition="outside",
        ))
        fig_cmp.add_vline(x=hold_sharpe, line_dash="dot", line_color="white",
                         annotation_text=f"Buy & Hold: {hold_sharpe:.2f}")
        fig_cmp.update_layout(
            template="plotly_dark", height=max(400, len(df_chart) * 35),
            margin=dict(t=10, b=0, l=200, r=60),
            xaxis_title="Sharpe Ratio",
        )
        st.plotly_chart(fig_cmp, use_container_width=True)

        # Risk-return scatter
        st.subheader("Risk vs Return")
        df_scatter = pd.DataFrame(cmp_results)
        fig_rr = go.Figure()
        fig_rr.add_trace(go.Scatter(
            x=df_scatter["Max DD"].abs(), y=df_scatter["CAGR"],
            mode="markers+text", text=[s.split(". ")[0] for s in df_scatter["Strategy"]],
            textposition="top center", textfont=dict(size=10, color="#aaa"),
            marker=dict(size=12, color=df_scatter["Sharpe"], colorscale="Viridis",
                       showscale=True, colorbar=dict(title="Sharpe")),
        ))
        fig_rr.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            xaxis_title="Max Drawdown (%, absolute)", yaxis_title="CAGR (%)",
        )
        st.plotly_chart(fig_rr, use_container_width=True)
