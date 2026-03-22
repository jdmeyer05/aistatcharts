import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from scipy.stats import norm as _norm
from itertools import combinations
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.layout import setup_page, get_active_ticker, set_active_ticker, fun_loader
setup_page("11_Algo_Backtester")

st.title("🏗️ Algo Backtester & Optimizer")
st.markdown("Test trading strategies, optimize parameters, and analyze performance with institutional-grade analytics.")


# ═══════════════════════════════════════════════
# DE PRADO METHODS
# ═══════════════════════════════════════════════

def deflated_sharpe_ratio(observed_sr: float, sr_std: float, n_trials: int, n_obs: int,
                          skew: float = 0, kurtosis: float = 3) -> float:
    """Deflated Sharpe Ratio (López de Prado, 2014).
    Adjusts the observed Sharpe for multiple testing bias.
    Returns the probability that the observed SR is significant after accounting
    for the number of trials (parameter combinations) tested.
    """
    if n_trials <= 1 or sr_std <= 0 or n_obs <= 1:
        return 1.0
    # Expected max SR under null (Euler-Mascheroni approximation)
    euler = 0.5772156649
    e_max_sr = sr_std * ((1 - euler) * _norm.ppf(1 - 1 / n_trials) + euler * _norm.ppf(1 - 1 / (n_trials * np.e)))
    # SR standard error with skew/kurtosis correction
    sr_se = np.sqrt((1 - skew * observed_sr + (kurtosis - 1) / 4 * observed_sr ** 2) / (n_obs - 1))
    if sr_se <= 0:
        return 1.0
    # Probability that observed SR is significant
    z = (observed_sr - e_max_sr) / sr_se
    return float(_norm.cdf(z))


def probability_of_backtest_overfitting(returns_df: pd.DataFrame, strategy_func, strategy_name: str,
                                        n_groups: int = 8, cost_pct: float = 0, borrow_daily: float = 0) -> dict:
    """Combinatorial Purged Cross-Validation PBO (López de Prado, 2018).
    Partitions data into n_groups, tests C(n, n/2) train/test splits.
    Returns PBO (probability best IS underperforms OOS) and logit distribution.
    """
    n = len(returns_df)
    group_size = n // n_groups
    if group_size < 50 or n_groups < 4:
        return {"pbo": None, "msg": "Not enough data for PBO analysis"}

    # Create group indices
    groups = list(range(n_groups))
    half = n_groups // 2

    # Get all C(n_groups, half) combinations for training sets
    train_combos = list(combinations(groups, half))
    if len(train_combos) > 100:
        # Cap at 100 random combinations for performance
        rng = np.random.default_rng(42)
        indices = rng.choice(len(train_combos), 100, replace=False)
        train_combos = [train_combos[i] for i in indices]

    grid_p1, grid_p2, _, _ = get_optimization_grid(strategy_name)
    grid_p1_list, grid_p2_list = list(grid_p1), list(grid_p2)

    logits = []

    for train_groups in train_combos:
        test_groups = tuple(g for g in groups if g not in train_groups)

        # Build train/test indices
        train_idx = np.concatenate([np.arange(g * group_size, (g + 1) * group_size) for g in train_groups])
        test_idx = np.concatenate([np.arange(g * group_size, (g + 1) * group_size) for g in test_groups])

        df_train = returns_df.iloc[train_idx].sort_index()
        df_test = returns_df.iloc[test_idx].sort_index()

        if len(df_train) < 50 or len(df_test) < 50:
            continue

        # Find best strategy on IS (training)
        best_sharpe_is = -np.inf
        best_params = (None, None)
        all_is_sharpes = {}

        for p1_v in grid_p1_list:
            for p2_v in grid_p2_list:
                pos = run_strategy(df_train, strategy_name, p1_v, p2_v)
                ret = pos.shift(1) * df_train["Returns"]
                ret = ret - pos.diff().abs().clip(upper=2) * cost_pct
                ret = ret - (pos.shift(1) == -1).astype(float) * borrow_daily
                clean = ret.dropna()
                if len(clean) > 10 and clean.std() > 0:
                    s = (clean.mean() / clean.std()) * np.sqrt(252)
                else:
                    s = -np.inf
                all_is_sharpes[(p1_v, p2_v)] = s
                if s > best_sharpe_is:
                    best_sharpe_is = s
                    best_params = (p1_v, p2_v)

        # Evaluate all strategies on OOS (testing)
        all_oos_sharpes = {}
        for (p1_v, p2_v) in all_is_sharpes:
            pos = run_strategy(df_test, strategy_name, p1_v, p2_v)
            ret = pos.shift(1) * df_test["Returns"]
            ret = ret - pos.diff().abs().clip(upper=2) * cost_pct
            ret = ret - (pos.shift(1) == -1).astype(float) * borrow_daily
            clean = ret.dropna()
            if len(clean) > 10 and clean.std() > 0:
                all_oos_sharpes[(p1_v, p2_v)] = (clean.mean() / clean.std()) * np.sqrt(252)
            else:
                all_oos_sharpes[(p1_v, p2_v)] = -np.inf

        # Rank: what's the OOS rank of the best IS strategy?
        best_oos = all_oos_sharpes.get(best_params, -np.inf)
        oos_values = sorted(all_oos_sharpes.values(), reverse=True)
        n_better = sum(1 for v in oos_values if v > best_oos)
        rank_pct = n_better / max(len(oos_values), 1)  # 0 = best OOS, 1 = worst OOS

        # Logit: log(rank / (1 - rank)), capped
        rank_pct = np.clip(rank_pct, 0.01, 0.99)
        logits.append(np.log(rank_pct / (1 - rank_pct)))

    if not logits:
        return {"pbo": None, "msg": "Could not compute PBO — not enough valid splits"}

    # PBO = fraction of logits > 0 (IS best underperforms OOS median)
    pbo = np.mean([l > 0 for l in logits])
    return {"pbo": pbo, "logits": logits, "n_splits": len(logits)}


def apply_triple_barrier(df: pd.DataFrame, position: pd.Series, pt_mult: float = 2.0,
                         sl_mult: float = 1.0, max_hold: int = 20, atr_period: int = 14) -> pd.Series:
    """Triple Barrier Method (López de Prado).
    Applies profit-taking, stop-loss, and time-expiry barriers to trade execution.
    Returns modified position series with barrier-based exits.
    """
    c = df["Close"].values
    high = df["High"].values if "High" in df.columns else c
    low = df["Low"].values if "Low" in df.columns else c

    # ATR for barrier sizing
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(c, 1)), np.abs(low - np.roll(c, 1))))
    atr = pd.Series(tr).rolling(atr_period).mean().values

    result = np.zeros(len(c))
    in_trade = False
    entry_price = 0
    direction = 0
    bars_held = 0

    for i in range(1, len(c)):
        signal = position.iloc[i] if i < len(position) else 0

        if in_trade:
            bars_held += 1
            atr_val = atr[i] if not np.isnan(atr[i]) else 0

            # Check barriers
            if direction == 1:  # Long
                pt_hit = c[i] >= entry_price + atr_val * pt_mult
                sl_hit = c[i] <= entry_price - atr_val * sl_mult
            else:  # Short
                pt_hit = c[i] <= entry_price - atr_val * pt_mult
                sl_hit = c[i] >= entry_price + atr_val * sl_mult

            time_hit = bars_held >= max_hold

            if pt_hit or sl_hit or time_hit:
                result[i] = 0  # Exit
                in_trade = False
            else:
                result[i] = direction  # Hold
        else:
            # New signal
            if signal != 0:
                in_trade = True
                direction = 1 if signal > 0 else -1
                entry_price = c[i]
                bars_held = 0
                result[i] = direction
            else:
                result[i] = 0

    return pd.Series(result, index=df.index)


def apply_bet_sizing(position: pd.Series, signal_strength: pd.Series) -> pd.Series:
    """Meta-Labeling / Bet Sizing (López de Prado).
    Scales position size by signal confidence (0-1) instead of always ±1.
    signal_strength should be 0-1 where 1 = max confidence.
    """
    return position * signal_strength.clip(0, 1)


def compute_signal_strength(df: pd.DataFrame, strat_name: str, p1=None, p2=None) -> pd.Series:
    """Compute a 0-1 confidence score based on how far the signal is from its threshold."""
    c = df["Close"]
    strength = pd.Series(0.5, index=df.index)

    if "SMA" in strat_name or "Cross" in strat_name or "EMA" in strat_name or "Momentum" in strat_name:
        p1_v = p1 or 10
        p2_v = p2 or 21
        if "EMA" in strat_name:
            fast = c.ewm(span=int(p1_v), adjust=False).mean()
            slow = c.ewm(span=int(p2_v), adjust=False).mean()
        else:
            fast = c.rolling(int(p1_v)).mean()
            slow = c.rolling(int(p2_v)).mean()
        spread = (fast - slow) / slow
        strength = spread.abs().clip(0, 0.05) / 0.05  # Normalize: 5% spread = max confidence

    elif "RSI" in strat_name:
        p1_v = p1 or 14
        rsi = calculate_rsi(c, p1_v)
        # Distance from 50 (neutral), normalized
        strength = ((rsi - 50).abs() / 50).clip(0, 1)

    elif "Bollinger" in strat_name or "Z-Score" in strat_name:
        p1_v = p1 or 20
        p2_v = p2 or 2.0
        z = (c - c.rolling(int(p1_v)).mean()) / c.rolling(int(p1_v)).std()
        strength = (z.abs() / (p2_v * 2)).clip(0, 1)  # At 2x threshold = max confidence

    elif "MACD" in strat_name:
        p1_v, p2_v = p1 or 12, p2 or 26
        ema_fast = c.ewm(span=int(p1_v), adjust=False).mean()
        ema_slow = c.ewm(span=int(p2_v), adjust=False).mean()
        macd = ema_fast - ema_slow
        signal = macd.ewm(span=9, adjust=False).mean()
        hist = (macd - signal).abs()
        strength = (hist / hist.rolling(50).max()).clip(0, 1)

    return strength.fillna(0.5)


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

with st.expander("Advanced Settings"):
    adv1, adv2 = st.columns(2)
    with adv1:
        borrow_rate = st.slider("Short Borrow Rate (% ann.)", 0.0, 10.0, 1.5, step=0.5,
                                help="Annual cost of borrowing shares for short positions.")
        use_bet_sizing = st.checkbox("Enable Bet Sizing (Meta-Labeling)",
                                     help="Scale position size by signal confidence instead of always ±1.")
    with adv2:
        use_triple_barrier = st.checkbox("Enable Triple Barrier Exits",
                                         help="Apply profit-taking, stop-loss, and time-expiry barriers (López de Prado).")
        if use_triple_barrier:
            tb1, tb2, tb3 = st.columns(3)
            with tb1:
                tb_pt = st.number_input("Take Profit (ATR mult)", value=2.0, step=0.5, min_value=0.5)
            with tb2:
                tb_sl = st.number_input("Stop Loss (ATR mult)", value=1.0, step=0.5, min_value=0.5)
            with tb3:
                tb_hold = st.number_input("Max Hold (days)", value=20, step=5, min_value=5)
        else:
            tb_pt, tb_sl, tb_hold = 2.0, 1.0, 20

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
            all_tested_sharpes = []

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

                    all_tested_sharpes.append(sharpe)
                    if sharpe > best_sharpe:
                        best_sharpe = sharpe
                        p1_final, p2_final = p1, p2

            progress_bar.empty()
            # Store for DSR calculation
            valid_sharpes = [s for s in all_tested_sharpes if s > -np.inf]
            st.session_state.opt_n_trials = total_iterations
            st.session_state.opt_all_sharpes = valid_sharpes

            params_str = f"{name_p1} = `{p1_final}`"
            if p2_final is not None and name_p2 is not None:
                params_str += f" | {name_p2} = `{p2_final}`"
            opt_msg = (
                f"**Optimal Parameters:** {params_str} | In-sample Sharpe: `{best_sharpe:.2f}` — "
                f"optimized across {total_iterations} parameter combinations. "
                f"These are in-sample results; use the Walk-Forward tab to validate out-of-sample."
            )

            st.session_state.opt_msg = opt_msg
    else:
        st.session_state.opt_msg = None

    # Run final backtest
    with fun_loader("compute"):
        df = df_base.copy()
        raw_position = run_strategy(df, strategy, p1_final, p2_final)

        # Apply Triple Barrier exits if enabled
        if use_triple_barrier:
            raw_position = apply_triple_barrier(df, raw_position, pt_mult=tb_pt, sl_mult=tb_sl, max_hold=int(tb_hold))

        # Apply Bet Sizing if enabled
        if use_bet_sizing:
            sig_strength = compute_signal_strength(df, strategy, p1_final, p2_final)
            df["Position"] = apply_bet_sizing(raw_position, sig_strength)
            df["Signal_Strength"] = sig_strength
        else:
            df["Position"] = raw_position

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

    # Deflated Sharpe Ratio (if optimizer was used)
    n_trials = st.session_state.get("opt_n_trials", 1)
    if n_trials > 1:
        all_sharpes = st.session_state.get("opt_all_sharpes", [])
        sr_std = np.std(all_sharpes) if all_sharpes else 1.0
        skewness = float(df["Strat_Returns"].skew())
        kurt = float(df["Strat_Returns"].kurtosis()) + 3  # scipy kurtosis is excess
        dsr_prob = deflated_sharpe_ratio(strat_sharpe, sr_std, n_trials, days, skewness, kurt)

        dsr1, dsr2, dsr3 = st.columns(3)
        dsr1.metric("Deflated Sharpe (DSR)", f"{dsr_prob:.1%}",
                     help="Probability the Sharpe is significant after adjusting for multiple testing")
        dsr2.metric("Trials Tested", f"{n_trials}")
        dsr3.metric("Expected Max Random Sharpe",
                     f"{sr_std * ((1-0.5772)*_norm.ppf(1-1/n_trials) + 0.5772*_norm.ppf(1-1/(n_trials*np.e))):.2f}" if sr_std > 0 else "N/A")

        if dsr_prob > 0.95:
            st.success(
                f"**Deflated Sharpe: {dsr_prob:.1%} confidence.** After adjusting for {n_trials} parameter "
                f"combinations tested, there is a {dsr_prob:.1%} probability this Sharpe ({strat_sharpe:.2f}) "
                f"is genuinely skillful rather than the best of many random tries."
            )
        elif dsr_prob > 0.50:
            st.warning(
                f"**Deflated Sharpe: {dsr_prob:.1%} confidence.** Adjusting for {n_trials} trials, "
                f"the observed Sharpe ({strat_sharpe:.2f}) has moderate evidence of being real. "
                f"A DSR > 95% would indicate strong significance."
            )
        else:
            st.error(
                f"**Deflated Sharpe: {dsr_prob:.1%} confidence.** After adjusting for {n_trials} trials, "
                f"this Sharpe ({strat_sharpe:.2f}) is likely explained by optimization luck. "
                f"The expected best random Sharpe from {n_trials} tries exceeds the observed value."
            )

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

            median_random = np.median(boot_sharpes)
            pct_rank = (1 - p_value) * 100

            if p_value < 0.01:
                st.success(
                    f"**Statistically significant at the 1% level (p = {p_value:.3f}).** "
                    f"Your strategy's Sharpe ({actual_sharpe:.2f}) ranks in the top {pct_rank:.0f}% of "
                    f"{n_boot} random permutations (median random Sharpe: {median_random:.2f}). "
                    f"This edge is very unlikely to be explained by chance alone."
                )
            elif p_value < 0.05:
                st.success(
                    f"**Statistically significant at the 5% level (p = {p_value:.3f}).** "
                    f"Your Sharpe ({actual_sharpe:.2f}) beats {pct_rank:.0f}% of random permutations "
                    f"(median random: {median_random:.2f}). The result is unlikely due to luck, "
                    f"but consider walk-forward validation to confirm the edge persists out-of-sample."
                )
            elif p_value < 0.10:
                st.warning(
                    f"**Marginally significant (p = {p_value:.3f}).** "
                    f"Your Sharpe ({actual_sharpe:.2f}) beats {pct_rank:.0f}% of random permutations "
                    f"(median random: {median_random:.2f}). This falls between the 5% and 10% significance "
                    f"thresholds — the result could be a real edge or noise. "
                    f"Run walk-forward validation before acting on this."
                )
            else:
                st.error(
                    f"**Not statistically significant (p = {p_value:.3f}).** "
                    f"Your Sharpe ({actual_sharpe:.2f}) only beats {pct_rank:.0f}% of random permutations "
                    f"(median random: {median_random:.2f}). A random strategy achieves similar or better "
                    f"performance — this result should not be used for trading decisions."
                )

            fig_boot = go.Figure()
            fig_boot.add_trace(go.Histogram(x=boot_sharpes, nbinsx=50, marker_color="#444", name="Random"))
            fig_boot.add_vline(x=actual_sharpe, line_color="#00d1ff", line_width=3,
                              annotation_text=f"Your strategy: {actual_sharpe:.2f}")
            fig_boot.update_layout(
                template="plotly_dark", height=250, margin=dict(t=30, b=0, l=0, r=0),
                xaxis_title="Sharpe Ratio", yaxis_title="Count",
            )
            st.plotly_chart(fig_boot, use_container_width=True)

        # Probability of Backtest Overfitting (PBO)
        if st.session_state.get("opt_n_trials", 1) > 1:
            st.divider()
            st.subheader("Probability of Backtest Overfitting (PBO)")
            st.markdown(
                "López de Prado's CPCV method: partitions data into groups, tests all train/test "
                "splits, and measures how often the best in-sample strategy underperforms out-of-sample. "
                "PBO > 50% = likely overfit."
            )
            if st.button("Run PBO Analysis", key="pbo_btn"):
                with st.spinner("Running combinatorial cross-validation (this may take a minute)..."):
                    pbo_result = probability_of_backtest_overfitting(
                        df, strategy_func=run_strategy, strategy_name=st.session_state.bt_strat,
                        n_groups=8, cost_pct=total_cost_pct, borrow_daily=daily_borrow_cost,
                    )

                if pbo_result.get("pbo") is not None:
                    pbo_val = pbo_result["pbo"]
                    logits = pbo_result["logits"]

                    pb1, pb2, pb3 = st.columns(3)
                    pb1.metric("PBO", f"{pbo_val:.1%}")
                    pb2.metric("CPCV Splits Tested", f"{pbo_result['n_splits']}")
                    pb3.metric("Overfit?", "Yes" if pbo_val > 0.5 else "No")

                    if pbo_val < 0.25:
                        st.success(
                            f"**PBO = {pbo_val:.1%} — Low overfitting risk.** In {pbo_result['n_splits']} CPCV splits, "
                            f"the best in-sample parameters ranked well out-of-sample {(1-pbo_val)*100:.0f}% of the time. "
                            f"This strategy has strong evidence of a persistent, real edge."
                        )
                    elif pbo_val < 0.50:
                        st.warning(
                            f"**PBO = {pbo_val:.1%} — Moderate overfitting risk.** The best IS parameters "
                            f"underperformed OOS in {pbo_val*100:.0f}% of splits. The edge may be partially real "
                            f"but is not fully robust across all data partitions."
                        )
                    else:
                        st.error(
                            f"**PBO = {pbo_val:.1%} — High overfitting risk.** In the majority of CPCV splits, "
                            f"the best in-sample strategy performed below median out-of-sample. "
                            f"The optimization is fitting to noise, not signal. Do not trade this."
                        )

                    # Logit distribution
                    fig_pbo = go.Figure()
                    fig_pbo.add_trace(go.Histogram(
                        x=logits, nbinsx=30,
                        marker_color=["#ff4b4b" if l > 0 else "#00ff96" for l in sorted(logits)],
                    ))
                    fig_pbo.add_vline(x=0, line_color="white", line_width=2,
                                     annotation_text="Overfit threshold")
                    fig_pbo.update_layout(
                        template="plotly_dark", height=250, margin=dict(t=30, b=0, l=0, r=0),
                        xaxis_title="Logit (negative = IS best ranks well OOS)", yaxis_title="Count",
                    )
                    st.plotly_chart(fig_pbo, use_container_width=True)
                    st.caption("Each bar is one CPCV split. Bars left of 0 = IS winner also performed well OOS. Right of 0 = overfit.")
                else:
                    st.warning(pbo_result.get("msg", "PBO analysis could not be completed."))

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

    # Position timeline (shows bet size if meta-labeling enabled)
    st.subheader("Position Timeline" + (" (Bet-Sized)" if use_bet_sizing else ""))
    fig_pos_bar = go.Figure()
    held_pos = df["Position"].shift(1).fillna(0)
    pos_colors = ["#00ff96" if v > 0 else "#ff4b4b" if v < 0 else "#333" for v in held_pos]
    fig_pos_bar.add_trace(go.Bar(
        x=df.index, y=held_pos, marker_color=pos_colors,
    ))
    fig_pos_bar.update_layout(
        template="plotly_dark", height=150, margin=dict(t=0, b=0, l=0, r=0),
        yaxis_title="Position Size" if use_bet_sizing else None,
    )
    if not use_bet_sizing:
        fig_pos_bar.update_layout(yaxis=dict(tickvals=[-1, 0, 1], ticktext=["Short", "Flat", "Long"]))
    st.plotly_chart(fig_pos_bar, use_container_width=True)

    if use_bet_sizing and "Signal_Strength" in df.columns:
        st.subheader("Signal Confidence")
        fig_conf = go.Figure()
        fig_conf.add_trace(go.Scatter(
            x=df.index, y=df["Signal_Strength"].shift(1).fillna(0), mode="lines",
            line=dict(color="#ffaa00", width=1.5), fill="tozeroy", fillcolor="rgba(255,170,0,0.1)",
        ))
        fig_conf.update_layout(
            template="plotly_dark", height=120, margin=dict(t=0, b=0, l=0, r=0),
            yaxis=dict(range=[0, 1], title="Confidence"), hovermode="x unified",
        )
        st.plotly_chart(fig_conf, use_container_width=True)
        st.caption("Signal confidence scales position size: 0 = no position, 1 = full position (±1).")


# ---- Walk-forward engine (reusable) ----
def _run_walk_forward(df_wf_base, strat_name, train_d, test_d, cost_pct, borrow_daily):
    """Run one walk-forward pass. Returns (oos_segments, fold_results, grid names)."""
    grid_p1, grid_p2, name_p1, name_p2 = get_optimization_grid(strat_name)
    grid_p1_list, grid_p2_list = list(grid_p1), list(grid_p2)

    oos_segments, fold_results = [], []
    total_len = len(df_wf_base)
    start_idx, fold_num = 0, 0

    while start_idx + train_d + test_d <= total_len:
        fold_num += 1
        train_end = start_idx + train_d
        test_end = train_end + test_d
        df_train = df_wf_base.iloc[start_idx:train_end]

        best_sharpe, best_p1, best_p2 = -np.inf, None, None
        for p1_v in grid_p1_list:
            for p2_v in grid_p2_list:
                if p2_v is not None and isinstance(p2_v, int) and p1_v >= p2_v and (
                    "Crossover" in strat_name or "Cross" in strat_name or "MACD" in strat_name or "Dual" in strat_name
                ):
                    continue
                pos = run_strategy(df_train, strat_name, p1_v, p2_v)
                ret = pos.shift(1) * df_train["Returns"]
                ret = ret - pos.diff().abs().clip(upper=2) * cost_pct
                ret = ret - (pos.shift(1) == -1).astype(float) * borrow_daily
                clean = ret.dropna()
                if len(clean) > 20 and clean.std() > 0:
                    s = (clean.mean() / clean.std()) * np.sqrt(252)
                else:
                    s = -np.inf
                if s > best_sharpe:
                    best_sharpe = s
                    best_p1, best_p2 = p1_v, p2_v

        warmup = min(250, len(df_train))
        df_test_full = df_wf_base.iloc[train_end - warmup:test_end].copy()
        df_test_full["Position"] = run_strategy(df_test_full, strat_name, best_p1, best_p2)
        df_test_full["Strat_Returns"] = df_test_full["Position"].shift(1) * df_test_full["Returns"]
        df_test_full["Strat_Returns"] -= df_test_full["Position"].diff().abs().clip(upper=2) * cost_pct
        df_test_full["Strat_Returns"] -= (df_test_full["Position"].shift(1) == -1).astype(float) * borrow_daily
        df_oos = df_test_full.iloc[warmup:]
        oos_segments.append(df_oos)

        oos_cum = np.exp(df_oos["Strat_Returns"].dropna().cumsum()).iloc[-1] if not df_oos["Strat_Returns"].dropna().empty else 1.0
        hold_cum = np.exp(df_oos["Returns"].dropna().cumsum()).iloc[-1] if not df_oos["Returns"].dropna().empty else 1.0

        fold_results.append({
            "Fold": fold_num,
            "Period": f"{df_oos.index[0].strftime('%Y-%m-%d')} → {df_oos.index[-1].strftime('%Y-%m-%d')}",
            "Params": f"{name_p1}={best_p1}" + (f", {name_p2}={best_p2}" if best_p2 is not None and name_p2 else ""),
            "Strategy": f"{(oos_cum - 1) * 100:+.2f}%",
            "Buy & Hold": f"{(hold_cum - 1) * 100:+.2f}%",
            "Alpha": f"{(oos_cum - hold_cum) * 100:+.2f}%",
            "Trades": int(df_oos["Position"].diff().abs().clip(upper=2).sum()),
        })
        start_idx += test_d

    return oos_segments, fold_results


def _summarize_wf(oos_segments, fold_results):
    """Compute summary stats from walk-forward results."""
    if not oos_segments:
        return None
    df_all = pd.concat(oos_segments)
    df_all["Cum_Strat"] = np.exp(df_all["Strat_Returns"].cumsum()) * 100
    df_all["Cum_Hold"] = np.exp(df_all["Returns"].cumsum()) * 100
    total_ret = (df_all["Cum_Strat"].iloc[-1] / 100) - 1
    hold_ret = (df_all["Cum_Hold"].iloc[-1] / 100) - 1
    yrs = len(df_all) / 252
    cagr = (df_all["Cum_Strat"].iloc[-1] / 100) ** (1 / yrs) - 1 if yrs > 0 else 0
    sharpe = (df_all["Strat_Returns"].mean() / df_all["Strat_Returns"].std()) * np.sqrt(252) if df_all["Strat_Returns"].std() > 0 else 0
    max_dd = ((df_all["Cum_Strat"] / df_all["Cum_Strat"].cummax()) - 1).min()
    folds_win = sum(1 for f in fold_results if float(f["Alpha"].replace("%", "").replace("+", "")) > 0)
    return {
        "df": df_all, "total_ret": total_ret, "hold_ret": hold_ret, "cagr": cagr,
        "sharpe": sharpe, "max_dd": max_dd, "folds_win": folds_win, "folds_total": len(fold_results),
    }


# ---- TAB 7: Walk-Forward Validation ----
with tab7:
    st.subheader("Walk-Forward Validation")
    st.markdown(
        "Runs walk-forward optimization across **all 9 combinations** of training and testing windows. "
        "Parameters are optimized on each training window and tested on the unseen next period. "
        "Results show which window sizes produce consistent out-of-sample alpha."
    )

    TRAIN_OPTIONS = [252, 504, 756]
    TEST_OPTIONS = [63, 126, 252]

    if st.button("Run Walk-Forward (All Windows)", type="primary", use_container_width=True):
        df_wf_base = fetch_massive_data(ticker, lookback + 250)
        if df_wf_base is None or df_wf_base.empty:
            st.error("Failed to load data.")
        else:
            df_wf_base["Returns"] = np.log(df_wf_base["Close"] / df_wf_base["Close"].shift(1))
            df_wf_base = df_wf_base.dropna()

            all_wf_results = {}
            progress = st.progress(0)
            combos = [(t, te) for t in TRAIN_OPTIONS for te in TEST_OPTIONS]

            for idx, (tr, te) in enumerate(combos):
                progress.progress((idx + 1) / len(combos))
                segs, folds = _run_walk_forward(df_wf_base, strategy, tr, te, total_cost_pct, daily_borrow_cost)
                summary = _summarize_wf(segs, folds)
                if summary:
                    all_wf_results[(tr, te)] = {"summary": summary, "segments": segs, "folds": folds}

            progress.empty()
            st.session_state.wf_all = all_wf_results

    wf_all = st.session_state.get("wf_all")

    if not wf_all:
        st.info("Click **Run Walk-Forward** to test all 9 train/test window combinations.")
    else:
        # ── Summary grid (heatmap) ──
        st.subheader("OOS Sharpe by Window Size")
        sharpe_grid = []
        for tr in TRAIN_OPTIONS:
            row = []
            for te in TEST_OPTIONS:
                r = wf_all.get((tr, te))
                row.append(r["summary"]["sharpe"] if r else np.nan)
            sharpe_grid.append(row)

        fig_heat = go.Figure(data=go.Heatmap(
            z=sharpe_grid,
            x=[f"{te}d test" for te in TEST_OPTIONS],
            y=[f"{tr}d train" for tr in TRAIN_OPTIONS],
            text=[[f"{v:.2f}" if not np.isnan(v) else "N/A" for v in row] for row in sharpe_grid],
            texttemplate="%{text}", textfont=dict(size=14),
            colorscale=[[0, "#cc0000"], [0.4, "#661111"], [0.5, "#1a1a2e"], [0.6, "#116633"], [1, "#00cc66"]],
            zmid=0, showscale=True, xgap=3, ygap=3,
            colorbar=dict(title="Sharpe"),
        ))
        fig_heat.update_layout(
            template="plotly_dark", height=250, margin=dict(t=10, b=0, l=0, r=0),
        )
        st.plotly_chart(fig_heat, use_container_width=True)

        # ── Ranked table of all combos ──
        st.subheader("All Window Combinations Ranked")
        combo_rows = []
        for (tr, te), data in sorted(wf_all.items(), key=lambda x: x[1]["summary"]["sharpe"], reverse=True):
            s = data["summary"]
            combo_rows.append({
                "Train": f"{tr}d",
                "Test": f"{te}d",
                "Folds": s["folds_total"],
                "OOS Sharpe": f"{s['sharpe']:.2f}",
                "OOS CAGR": f"{s['cagr']*100:+.1f}%",
                "Alpha": f"{(s['total_ret'] - s['hold_ret'])*100:+.1f}%",
                "Max DD": f"{s['max_dd']*100:.1f}%",
                "Folds Winning": f"{s['folds_win']}/{s['folds_total']}",
            })
        st.dataframe(pd.DataFrame(combo_rows), use_container_width=True, hide_index=True)

        # ── Aggregate verdict ──
        sharpes = [d["summary"]["sharpe"] for d in wf_all.values()]
        avg_sharpe = np.mean(sharpes)
        positive_combos = sum(1 for s in sharpes if s > 0)
        strong_combos = sum(1 for s in sharpes if s > 0.5)
        total_combos = len(sharpes)

        st.divider()
        if strong_combos >= total_combos * 0.5:
            st.success(
                f"**Robust across window sizes.** {strong_combos}/{total_combos} combinations produce OOS Sharpe > 0.5 "
                f"(avg: {avg_sharpe:.2f}). The strategy's edge is not sensitive to the choice of training/testing window, "
                f"which is a strong indicator of a real, persistent pattern rather than overfitting."
            )
        elif positive_combos >= total_combos * 0.5:
            st.warning(
                f"**Partially robust.** {positive_combos}/{total_combos} combinations show positive OOS Sharpe "
                f"(avg: {avg_sharpe:.2f}), but only {strong_combos} exceed the 0.5 confidence threshold. "
                f"The strategy may have a weak edge that is sensitive to window size — use the heatmap above "
                f"to identify which configurations work best and test with more data."
            )
        else:
            st.error(
                f"**Not robust.** Only {positive_combos}/{total_combos} combinations produce positive OOS Sharpe "
                f"(avg: {avg_sharpe:.2f}). The in-sample results do not generalize across different walk-forward "
                f"configurations. This strategy should not be relied upon for trading."
            )

        # ── Best combo detail ──
        best_key = max(wf_all.keys(), key=lambda k: wf_all[k]["summary"]["sharpe"])
        best = wf_all[best_key]
        st.divider()
        st.subheader(f"Best Configuration: {best_key[0]}d train / {best_key[1]}d test")

        s = best["summary"]
        bm1, bm2, bm3, bm4, bm5 = st.columns(5)
        bm1.metric("OOS Return", f"{s['total_ret']*100:.2f}%", f"{(s['total_ret']-s['hold_ret'])*100:+.2f}% vs B&H")
        bm2.metric("OOS CAGR", f"{s['cagr']*100:.2f}%")
        bm3.metric("OOS Sharpe", f"{s['sharpe']:.2f}")
        bm4.metric("OOS Max DD", f"{s['max_dd']*100:.1f}%")
        bm5.metric("Folds Beating B&H", f"{s['folds_win']}/{s['folds_total']}")

        # Best combo equity curve
        df_best = s["df"]
        fig_best = go.Figure()
        fig_best.add_trace(go.Scatter(
            x=df_best.index, y=df_best["Cum_Hold"], mode="lines",
            name="Buy & Hold", line=dict(color="white", width=2, dash="dot"),
        ))
        fig_best.add_trace(go.Scatter(
            x=df_best.index, y=df_best["Cum_Strat"], mode="lines",
            name="Strategy (OOS)", line=dict(color="#00d1ff", width=3),
        ))
        for seg in best["segments"]:
            fig_best.add_vline(x=seg.index[0], line_dash="dot", line_color="rgba(255,255,255,0.15)")
        fig_best.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Portfolio Value ($100 Base)", hovermode="x unified",
        )
        st.plotly_chart(fig_best, use_container_width=True)

        # Fold detail for best combo
        st.subheader("Fold-by-Fold (Best Configuration)")
        df_folds = pd.DataFrame(best["folds"])
        st.dataframe(df_folds, use_container_width=True, hide_index=True)


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
