import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.data_engine import polygon_history, polygon_ticker_details, polygon_snapshot
import logging
import threading
from datetime import datetime, timedelta
from collections import deque
from src.layout import setup_page, card_header, error_boundary, get_active_ticker, set_active_ticker, fun_loader
from src.styles import COLORS

setup_page("04_RL_Trading")
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# NEURAL NETWORK (Pure Numpy DQN)
# ═══════════════════════════════════════════════
class DuelingDQN:
    """Dueling DQN in pure numpy: separates value and advantage streams.
    Q(s,a) = V(s) + A(s,a) - mean(A(s,:))
    This learns state value independently from action advantage, improving stability."""

    def __init__(self, state_dim: int, action_dim: int, hidden: int = 128, lr: float = 0.001):
        self.lr = lr
        self.action_dim = action_dim
        # Shared layers
        s1 = np.sqrt(2.0 / (state_dim + hidden))
        s2 = np.sqrt(2.0 / (hidden + hidden))
        self.W1 = np.random.randn(state_dim, hidden) * s1
        self.b1 = np.zeros(hidden)
        self.W2 = np.random.randn(hidden, hidden) * s2
        self.b2 = np.zeros(hidden)
        # Value stream: hidden -> 1
        sv = np.sqrt(2.0 / (hidden + 1))
        self.Wv = np.random.randn(hidden, 1) * sv
        self.bv = np.zeros(1)
        # Advantage stream: hidden -> action_dim
        sa = np.sqrt(2.0 / (hidden + action_dim))
        self.Wa = np.random.randn(hidden, action_dim) * sa
        self.ba = np.zeros(action_dim)

    def forward(self, x):
        self._z1 = x @ self.W1 + self.b1
        self._a1 = np.maximum(0, self._z1)
        self._z2 = self._a1 @ self.W2 + self.b2
        self._a2 = np.maximum(0, self._z2)
        # Value stream
        self._v = self._a2 @ self.Wv + self.bv  # (batch, 1)
        # Advantage stream
        self._adv = self._a2 @ self.Wa + self.ba  # (batch, action_dim)
        # Combine: Q = V + (A - mean(A))
        return self._v + self._adv - self._adv.mean(axis=1, keepdims=True)

    def predict(self, x):
        if x.ndim == 1:
            x = x.reshape(1, -1)
        return self.forward(x)

    def train_step(self, states, targets, weights=None):
        batch_size = states.shape[0]
        q_values = self.forward(states)
        td_errors = q_values - targets

        if weights is not None:
            loss = np.mean(weights[:, None] * td_errors ** 2)
            dq = 2 * weights[:, None] * td_errors / batch_size
        else:
            loss = np.mean(td_errors ** 2)
            dq = 2 * td_errors / batch_size

        # Backward through dueling heads
        dv = dq.sum(axis=1, keepdims=True)  # gradient flows to V for all actions
        da = dq - dq.mean(axis=1, keepdims=True)  # gradient for advantage

        dWv = self._a2.T @ dv
        dbv = dv.sum(axis=0)
        dWa = self._a2.T @ da
        dba = da.sum(axis=0)

        da2 = dv @ self.Wv.T + da @ self.Wa.T
        dz2 = da2 * (self._z2 > 0)
        dW2 = self._a1.T @ dz2
        db2 = dz2.sum(axis=0)
        da1 = dz2 @ self.W2.T
        dz1 = da1 * (self._z1 > 0)
        dW1 = states.T @ dz1
        db1 = dz1.sum(axis=0)

        for g in [dW1, db1, dW2, db2, dWv, dbv, dWa, dba]:
            np.clip(g, -1.0, 1.0, out=g)

        self.W1 -= self.lr * dW1
        self.b1 -= self.lr * db1
        self.W2 -= self.lr * dW2
        self.b2 -= self.lr * db2
        self.Wv -= self.lr * dWv
        self.bv -= self.lr * dbv
        self.Wa -= self.lr * dWa
        self.ba -= self.lr * dba

        return loss, np.abs(td_errors).mean(axis=1)  # return per-sample TD errors for PER

    def copy_from(self, other):
        self.W1, self.b1 = other.W1.copy(), other.b1.copy()
        self.W2, self.b2 = other.W2.copy(), other.b2.copy()
        self.Wv, self.bv = other.Wv.copy(), other.bv.copy()
        self.Wa, self.ba = other.Wa.copy(), other.ba.copy()

# Alias for backward compat
NumpyDQN = DuelingDQN


class PrioritizedReplayBuffer:
    """Prioritized Experience Replay — samples transitions proportional to TD error.
    High-error transitions are replayed more often, accelerating learning."""

    def __init__(self, capacity=10000, alpha=0.6, beta_start=0.4, beta_frames=10000):
        self.capacity = capacity
        self.alpha = alpha  # priority exponent
        self.beta_start = beta_start
        self.beta_frames = beta_frames
        self.buffer = []
        self.priorities = np.zeros(capacity, dtype=np.float64)
        self.pos = 0
        self.frame = 0

    def add(self, transition, td_error=1.0):
        priority = (abs(td_error) + 1e-5) ** self.alpha
        if len(self.buffer) < self.capacity:
            self.buffer.append(transition)
        else:
            self.buffer[self.pos] = transition
        self.priorities[self.pos] = priority
        self.pos = (self.pos + 1) % self.capacity

    def sample(self, batch_size):
        self.frame += 1
        n = len(self.buffer)
        probs = self.priorities[:n] / (self.priorities[:n].sum() + 1e-8)

        indices = np.random.choice(n, batch_size, p=probs, replace=False)

        # Importance sampling weights
        beta = min(1.0, self.beta_start + self.frame * (1.0 - self.beta_start) / self.beta_frames)
        weights = (n * probs[indices]) ** (-beta)
        weights /= weights.max()

        batch = [self.buffer[i] for i in indices]
        return batch, indices, weights

    def update_priorities(self, indices, td_errors):
        for idx, td in zip(indices, td_errors):
            self.priorities[idx] = (abs(td) + 1e-5) ** self.alpha

    def __len__(self):
        return len(self.buffer)


# ═══════════════════════════════════════════════
# TRADING ENVIRONMENT (Enhanced)
# ═══════════════════════════════════════════════
class TradingEnv:
    ACTION_NAMES = ["Hold", "Buy 25%", "Buy 50%", "Buy 100%",
                    "Sell 25%", "Sell 50%", "Sell 100%", "Short 50%"]
    N_ACTIONS = 8

    def __init__(self, prices, features, initial_cash=100000, commission=0.001,
                 spread_bps=2, slippage_bps=5, borrow_rate=0.03,
                 stop_loss_pct=0.05, max_daily_loss_pct=0.03,
                 stack_size=5):
        self.prices = prices
        self.features = features
        self.initial_cash = initial_cash
        self.commission = commission
        self.spread = spread_bps / 10000
        self.slippage = slippage_bps / 10000
        self.borrow_rate = borrow_rate / 252  # daily
        self.stop_loss_pct = stop_loss_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.stack_size = stack_size
        self.n_steps = len(prices)
        self._state_history = deque(maxlen=stack_size)
        self.reset()

    def reset(self):
        self.step_idx = 0
        self.cash = self.initial_cash
        self.position = 0.0
        self.entry_price = 0.0
        self.portfolio_values = [self.initial_cash]
        self.daily_start_value = self.initial_cash
        self.trades = []
        self.done = False
        self.stopped_out = False
        self._state_history.clear()
        base = self._get_base_state()
        for _ in range(self.stack_size):
            self._state_history.append(base)
        return self._get_stacked_state()

    @property
    def state_dim(self):
        return (self.features.shape[1] + 4) * self.stack_size

    def _get_base_state(self):
        market = self.features[min(self.step_idx, self.n_steps - 1)]
        port_value = self.cash + self.position * self.prices[min(self.step_idx, self.n_steps - 1)]
        position_pct = (self.position * self.prices[min(self.step_idx, self.n_steps - 1)]) / max(port_value, 1)
        unrealized = (self.prices[min(self.step_idx, self.n_steps - 1)] / self.entry_price - 1) if self.entry_price > 0 and self.position > 0 else 0
        daily_pnl = (port_value / max(self.daily_start_value, 1) - 1) if self.daily_start_value > 0 else 0
        return np.concatenate([market, [position_pct, unrealized, self.cash / self.initial_cash, daily_pnl]])

    def _get_stacked_state(self):
        return np.concatenate(list(self._state_history))

    def _total_cost(self, shares, price):
        return abs(shares) * price * (self.commission + self.spread + self.slippage)

    def step(self, action):
        price = self.prices[self.step_idx]
        port_value = self.cash + self.position * price

        # Risk management: stop-loss check
        if self.position > 0 and self.entry_price > 0:
            loss_pct = 1 - price / self.entry_price
            if loss_pct >= self.stop_loss_pct:
                action = 6  # force sell all
                self.stopped_out = True

        # Risk management: max daily loss
        daily_loss = 1 - port_value / max(self.daily_start_value, 1)
        if daily_loss >= self.max_daily_loss_pct and action in (1, 2, 3, 7):
            action = 0  # block new positions

        # Execute action
        if action == 0:
            pass  # hold
        elif action in (1, 2, 3):  # buy
            pct = [0, 0.25, 0.50, 0.99][action]
            buy_amount = port_value * pct
            shares = buy_amount / price
            cost = self._total_cost(shares, price)
            if shares * price + cost <= self.cash and shares > 0:
                self.cash -= shares * price + cost
                self.position += shares
                if self.entry_price == 0:
                    self.entry_price = price
                else:
                    self.entry_price = (self.entry_price + price) / 2
                self.trades.append({"step": self.step_idx, "action": "buy", "shares": shares, "price": price})
        elif action in (4, 5, 6):  # sell
            pct = [0, 0, 0, 0, 0.25, 0.50, 1.0][action]
            if self.position > 0:
                shares = self.position * pct
                proceeds = shares * price - self._total_cost(shares, price)
                self.cash += proceeds
                self.position -= shares
                self.trades.append({"step": self.step_idx, "action": "sell", "shares": shares, "price": price})
                if self.position <= 0.001:
                    self.position = 0
                    self.entry_price = 0
            elif self.position < 0:  # cover short
                shares = abs(self.position) * pct
                cost = shares * price + self._total_cost(shares, price)
                self.cash -= cost
                self.position += shares
                self.trades.append({"step": self.step_idx, "action": "cover", "shares": shares, "price": price})
                if abs(self.position) <= 0.001:
                    self.position = 0
                    self.entry_price = 0
        elif action == 7:  # short
            short_amount = port_value * 0.50
            shares = short_amount / price
            proceeds = shares * price - self._total_cost(shares, price)
            self.cash += proceeds
            self.position -= shares
            self.entry_price = price
            self.trades.append({"step": self.step_idx, "action": "short", "shares": shares, "price": price})

        # Borrowing cost for shorts
        if self.position < 0:
            borrow_cost = abs(self.position) * price * self.borrow_rate
            self.cash -= borrow_cost

        # Advance
        self.step_idx += 1
        if self.step_idx >= self.n_steps - 1:
            self.done = True
            if self.position != 0:
                fp = self.prices[self.step_idx]
                if self.position > 0:
                    self.cash += self.position * fp - self._total_cost(self.position, fp)
                else:
                    self.cash -= abs(self.position) * fp + self._total_cost(abs(self.position), fp)
                self.position = 0

        new_price = self.prices[min(self.step_idx, self.n_steps - 1)]
        new_port = self.cash + self.position * new_price
        self.portfolio_values.append(new_port)

        # Update state history
        self._state_history.append(self._get_base_state())

        # Reward
        ret = (new_port / max(port_value, 1) - 1)
        peak = max(self.portfolio_values)
        dd = (peak - new_port) / peak if peak > 0 else 0

        reward = ret * 100 - dd * 50
        if action != 0:
            reward -= 0.1
        if self.stopped_out:
            reward -= 2
            self.stopped_out = False

        return self._get_stacked_state(), reward, self.done


# ═══════════════════════════════════════════════
# FEATURE ENGINEERING (Enhanced)
# ═══════════════════════════════════════════════
def _ema(arr, span):
    alpha = 2 / (span + 1)
    result = np.zeros_like(arr, dtype=float)
    result[0] = arr[0]
    for i in range(1, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result

def _sma(arr, window):
    result = np.zeros_like(arr, dtype=float)
    for i in range(window, len(arr)):
        result[i] = np.mean(arr[i - window:i])
    return result


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_intermarket(period="3y"):
    """Fetch intermarket data: VIX, 10Y yield, Dollar, Gold, Oil, sector ETFs."""
    data = {}
    symbols = [
        ("^VIX", "vix"), ("^TNX", "tnx"), ("DX-Y.NYB", "dxy"),
        ("GLD", "gold"), ("USO", "oil"),  # cross-asset momentum
    ]
    days_map = {"1y": 365, "2y": 730, "3y": 1095, "5y": 1825, "10y": 3650}
    days = days_map.get(period, 1095)
    for sym, label in symbols:
        try:
            df = polygon_history(sym, days)
            if not df.empty:
                data[label] = df["Close"]
        except Exception:
            pass
    return data


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_stock_extras(ticker: str) -> dict:
    """Fetch fundamental/alternative data for RL features: insider txns, short interest, earnings."""
    extras = {}
    try:
        info = polygon_ticker_details(ticker) or {}
        extras["short_pct"] = 0  # Not available via Polygon free tier
        extras["beta"] = 1.0  # Not available via Polygon free tier
        extras["sector"] = info.get("sector", "Unknown")
        extras["next_earnings"] = None  # Not available via Polygon free tier
        extras["insider_net"] = 0  # Not available via Polygon free tier

        # Sector ETF for relative strength
        sector_etfs = {
            "Technology": "XLK", "Healthcare": "XLV", "Financial Services": "XLF",
            "Energy": "XLE", "Consumer Cyclical": "XLY", "Consumer Defensive": "XLP",
            "Industrials": "XLI", "Basic Materials": "XLB", "Real Estate": "XLRE",
            "Utilities": "XLU", "Communication Services": "XLC",
        }
        extras["sector_etf"] = sector_etfs.get(extras["sector"], "SPY")
    except Exception as e:
        logger.warning(f"Failed to fetch extras for {ticker}: {e}")
        extras.setdefault("short_pct", 0)
        extras.setdefault("beta", 1.0)
        extras.setdefault("insider_net", 0)
        extras.setdefault("next_earnings", None)
        extras.setdefault("sector_etf", "SPY")
    return extras


def _fourier_features(arr, n_harmonics=3):
    """Extract dominant frequency components via FFT."""
    n = len(arr)
    features = []
    # Detrend
    detrended = arr - _sma(arr, min(50, n // 2 + 1))
    fft = np.fft.rfft(detrended)
    freqs = np.fft.rfftfreq(n)
    magnitudes = np.abs(fft)

    # Top harmonics as rolling signals
    top_idx = np.argsort(magnitudes[1:])[-n_harmonics:] + 1
    for idx in top_idx:
        # Reconstruct this frequency component
        filtered = np.zeros_like(fft)
        filtered[idx] = fft[idx]
        component = np.fft.irfft(filtered, n=n)
        features.append(component / (np.std(component) + 1e-8))

    return features


def compute_features(df, intermarket=None, stock_extras=None, sector_df=None):
    """Enhanced feature set: technicals + intermarket + fundamentals + Fourier + relative strength."""
    close = df["Close"].values.astype(float)
    high = df["High"].values.astype(float)
    low = df["Low"].values.astype(float)
    volume = df["Volume"].values.astype(float)
    n = len(close)
    features = []

    # ── Core Technicals ──
    # Returns (1, 5, 10, 20 day)
    daily_ret = np.zeros(n)
    daily_ret[1:] = close[1:] / close[:-1] - 1
    for lb in [1, 5, 10, 20]:
        ret = np.zeros(n)
        if lb < n:
            ret[lb:] = close[lb:] / close[:-lb] - 1
        features.append(ret)

    # Volatility (20-day)
    vol = np.zeros(n)
    for i in range(21, n):
        vol[i] = np.std(daily_ret[i - 20:i])
    features.append(vol)

    # RSI (14)
    rsi = np.full(n, 0.0)
    delta = np.diff(close, prepend=close[0])
    for i in range(15, n):
        gains = np.mean(np.maximum(delta[i - 14:i], 0))
        losses = np.mean(np.maximum(-delta[i - 14:i], 0))
        rsi[i] = (100 - 100 / (1 + gains / (losses + 1e-10))) / 50 - 1
    features.append(rsi)

    # MACD histogram
    macd_hist = _ema(close, 12) - _ema(close, 26)
    macd_hist = macd_hist - _ema(macd_hist, 9)
    features.append(macd_hist / (close + 1e-8) * 100)

    # Bollinger %B
    sma20 = _sma(close, 20)
    std20 = np.zeros(n)
    for i in range(20, n):
        std20[i] = np.std(close[i - 20:i])
    bb_w = 2 * std20
    bb_pct = np.where(bb_w > 0, (close - (sma20 - std20)) / (bb_w + 1e-8), 0.5) - 0.5
    features.append(bb_pct)

    # EMA trends
    for span in [20, 50]:
        features.append((close - _ema(close, span)) / (close + 1e-8))

    # Volume ratio
    avg_vol = _sma(volume, 20)
    vol_ratio = np.where(avg_vol > 0, volume / (avg_vol + 1e-8) - 1, 0)
    features.append(np.clip(vol_ratio, -3, 3) / 3)

    # ATR normalized
    tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
    atr = _sma(tr, 14)
    features.append(atr / (close + 1e-8))

    # Weekly momentum
    ret_20 = np.zeros(n)
    if n > 20:
        ret_20[20:] = close[20:] / close[:-20] - 1
    features.append(_sma(ret_20, 5))

    # ── Fourier Cycle Detection ──
    if n > 100:
        try:
            fourier_feats = _fourier_features(close, n_harmonics=3)
            for ff in fourier_feats:
                features.append(ff)
        except Exception:
            for _ in range(3):
                features.append(np.zeros(n))
    else:
        for _ in range(3):
            features.append(np.zeros(n))

    # ── Intermarket Features ──
    if intermarket:
        dates = df.index
        for key in ["vix", "tnx", "dxy", "gold", "oil"]:
            if key in intermarket:
                aligned = intermarket[key].reindex(dates, method="ffill")
                vals = aligned.values.astype(float)
                vals = np.nan_to_num(vals, nan=0)
                mean20 = _sma(vals, 20)
                normed = np.where(mean20 > 0, vals / (mean20 + 1e-8) - 1, 0)
                features.append(np.clip(normed, -3, 3))
            else:
                features.append(np.zeros(n))

    # ── Sector Relative Strength ──
    if sector_df is not None:
        sector_close = sector_df.reindex(df.index, method="ffill").values.astype(float)
        sector_close = np.nan_to_num(sector_close, nan=0)
        # Relative strength: stock return - sector return (20-day)
        if len(sector_close) == n and np.any(sector_close > 0):
            stock_ret20 = np.zeros(n)
            sect_ret20 = np.zeros(n)
            if n > 20:
                stock_ret20[20:] = close[20:] / close[:-20] - 1
                sect_ret20[20:] = sector_close[20:] / (sector_close[:-20] + 1e-8) - 1
            features.append(stock_ret20 - sect_ret20)  # relative strength
        else:
            features.append(np.zeros(n))
    else:
        features.append(np.zeros(n))

    # Also add relative strength vs SPY
    if intermarket and "spy" not in intermarket:
        # Use gold as proxy check — if we have intermarket we can compute
        pass
    features.append(np.zeros(n))  # placeholder for SPY relative strength

    # ── Fundamental/Alternative Data Features (static, broadcast) ──
    if stock_extras:
        # Short interest (static, normalized)
        short_pct = stock_extras.get("short_pct", 0)
        features.append(np.full(n, np.clip(short_pct * 10, -3, 3)))  # scale

        # Insider net buying signal
        insider_net = stock_extras.get("insider_net", 0)
        features.append(np.full(n, np.clip(insider_net / 5, -1, 1)))  # normalize

        # Days to earnings (dynamic if we have the date)
        earnings_date = stock_extras.get("next_earnings")
        dte = np.zeros(n)
        if earnings_date is not None:
            try:
                for i, date in enumerate(df.index):
                    days = (pd.Timestamp(earnings_date) - pd.Timestamp(date)).days
                    if 0 <= days <= 60:
                        dte[i] = 1 - days / 60  # 1.0 at earnings, 0 at 60 days out
            except Exception:
                pass
        features.append(dte)

        # Beta
        features.append(np.full(n, np.clip((stock_extras.get("beta", 1.0) - 1) * 2, -3, 3)))
    else:
        features.extend([np.zeros(n)] * 4)

    feature_matrix = np.column_stack(features)
    feature_matrix = np.nan_to_num(feature_matrix, 0)
    feature_matrix = np.clip(feature_matrix, -5, 5)
    return close, feature_matrix


# ═══════════════════════════════════════════════
# DQN TRAINING (Enhanced)
# ═══════════════════════════════════════════════
REWARD_FUNCTIONS = {
    "Sharpe-Style": lambda ret, dd: ret * 100 - dd * 20,
    "Downside-Penalized": lambda ret, dd: (ret * 100 if ret >= 0 else ret * 300) - dd * 30,
    "Drawdown-Heavy": lambda ret, dd: ret * 100 - dd * 80,
    "Risk-Adjusted (Default)": lambda ret, dd: ret * 100 - dd * 50,
}


def train_dqn(env, n_episodes=200, batch_size=64, gamma=0.99,
              epsilon_start=1.0, epsilon_end=0.05, epsilon_decay=0.995,
              target_update=10, reward_key="Risk-Adjusted (Default)",
              progress_callback=None):
    """Train Dueling DQN with Prioritized Experience Replay."""
    state_dim = env.state_dim
    action_dim = TradingEnv.N_ACTIONS

    policy_net = DuelingDQN(state_dim, action_dim, hidden=128, lr=0.0005)
    target_net = DuelingDQN(state_dim, action_dim, hidden=128)
    target_net.copy_from(policy_net)

    replay = PrioritizedReplayBuffer(capacity=15000)
    epsilon = epsilon_start
    episode_rewards, episode_values, losses = [], [], []

    for episode in range(n_episodes):
        state = env.reset()
        total_reward = 0
        ep_loss = 0
        n_steps = 0

        while not env.done:
            if np.random.random() < epsilon:
                action = np.random.randint(action_dim)
            else:
                q = policy_net.predict(state)
                action = np.argmax(q[0])

            next_state, reward, done = env.step(action)
            replay.add((state, action, reward, next_state, done))
            total_reward += reward
            state = next_state

            if len(replay) >= batch_size:
                batch, indices, weights = replay.sample(batch_size)
                s = np.array([b[0] for b in batch])
                a = np.array([b[1] for b in batch])
                r = np.array([b[2] for b in batch])
                ns = np.array([b[3] for b in batch])
                d = np.array([b[4] for b in batch])

                cur_q = policy_net.predict(s)
                nxt_q = target_net.predict(ns)
                targets = cur_q.copy()
                for i in range(batch_size):
                    targets[i, a[i]] = r[i] if d[i] else r[i] + gamma * np.max(nxt_q[i])

                loss, td_errors = policy_net.train_step(s, targets, weights=weights)
                replay.update_priorities(indices, td_errors)
                ep_loss += loss
                n_steps += 1

        if (episode + 1) % target_update == 0:
            target_net.copy_from(policy_net)

        epsilon = max(epsilon_end, epsilon * epsilon_decay)
        episode_rewards.append(total_reward)
        episode_values.append(env.portfolio_values[-1])
        losses.append(ep_loss / max(n_steps, 1))

        if progress_callback:
            progress_callback(episode, n_episodes, total_reward, env.portfolio_values[-1], epsilon)

    return {"policy_net": policy_net, "episode_rewards": episode_rewards,
            "episode_values": episode_values, "losses": losses}


def train_ensemble(env_factory, n_agents=5, **train_kwargs):
    """Train multiple agents with different seeds."""
    agents = []
    all_results = []
    for seed in range(n_agents):
        np.random.seed(seed * 42 + 7)
        env = env_factory()
        result = train_dqn(env, **train_kwargs)
        agents.append(result["policy_net"])
        all_results.append(result)
    return agents, all_results


def ensemble_predict(agents, state):
    """Average Q-values from multiple agents."""
    q_sum = None
    for agent in agents:
        q = agent.predict(state)
        q_sum = q if q_sum is None else q_sum + q
    return q_sum / len(agents)


# ═══════════════════════════════════════════════
# BACKTESTING
# ═══════════════════════════════════════════════
def run_backtest(env, policy_net=None, agents=None):
    state = env.reset()
    actions_taken = []

    while not env.done:
        if agents:
            q = ensemble_predict(agents, state)
        elif policy_net:
            q = policy_net.predict(state)
        else:
            q = np.zeros((1, TradingEnv.N_ACTIONS))
        action = np.argmax(q[0])
        actions_taken.append(action)
        state, _, _ = env.step(action)

    pv = np.array(env.portfolio_values)
    returns = np.diff(pv) / pv[:-1]
    total_ret = (pv[-1] / pv[0] - 1) * 100
    sharpe = np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252)
    downside = returns[returns < 0]
    sortino = np.mean(returns) / (np.std(downside) + 1e-8) * np.sqrt(252) if len(downside) > 0 else sharpe
    peak = np.maximum.accumulate(pv)
    dd = (peak - pv) / peak
    max_dd = np.max(dd) * 100
    calmar = (total_ret / 100) / (max_dd / 100 + 1e-8)

    return {"portfolio_values": pv, "actions": actions_taken, "trades": env.trades,
            "total_return": total_ret, "sharpe": sharpe, "sortino": sortino,
            "calmar": calmar, "max_drawdown": max_dd, "n_trades": len(env.trades),
            "drawdowns": dd, "returns": returns}


def run_buy_hold(prices, initial_cash=100000):
    shares = initial_cash / prices[0]
    pv = shares * prices
    returns = np.diff(pv) / pv[:-1]
    return {"portfolio_values": pv,
            "total_return": (pv[-1] / pv[0] - 1) * 100,
            "sharpe": np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252),
            "max_drawdown": np.max((np.maximum.accumulate(pv) - pv) / np.maximum.accumulate(pv)) * 100}


def run_sma_crossover(prices, fast=20, slow=50, initial_cash=100000, commission=0.001):
    """Simple SMA crossover benchmark."""
    sma_fast = _sma(prices, fast)
    sma_slow = _sma(prices, slow)
    cash, pos = initial_cash, 0.0
    pv = [initial_cash]
    for i in range(slow + 1, len(prices)):
        if sma_fast[i] > sma_slow[i] and sma_fast[i - 1] <= sma_slow[i - 1] and pos == 0:
            pos = (cash * 0.99) / (prices[i] * (1 + commission))
            cash -= pos * prices[i] * (1 + commission)
        elif sma_fast[i] < sma_slow[i] and sma_fast[i - 1] >= sma_slow[i - 1] and pos > 0:
            cash += pos * prices[i] * (1 - commission)
            pos = 0
        pv.append(cash + pos * prices[i])
    pv = np.array(pv)
    returns = np.diff(pv) / pv[:-1]
    return {"portfolio_values": pv, "total_return": (pv[-1] / pv[0] - 1) * 100,
            "sharpe": np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252),
            "max_drawdown": np.max((np.maximum.accumulate(pv) - pv) / np.maximum.accumulate(pv)) * 100,
            "label": f"SMA {fast}/{slow}"}


def run_mean_reversion(prices, lookback=20, z_entry=2.0, initial_cash=100000, commission=0.001):
    """Mean reversion (Bollinger bounce) benchmark."""
    sma = _sma(prices, lookback)
    std = np.zeros_like(prices)
    for i in range(lookback, len(prices)):
        std[i] = np.std(prices[i - lookback:i])
    cash, pos = initial_cash, 0.0
    pv = [initial_cash]
    for i in range(lookback + 1, len(prices)):
        z = (prices[i] - sma[i]) / (std[i] + 1e-8)
        if z < -z_entry and pos == 0:
            pos = (cash * 0.99) / (prices[i] * (1 + commission))
            cash -= pos * prices[i] * (1 + commission)
        elif z > 0 and pos > 0:
            cash += pos * prices[i] * (1 - commission)
            pos = 0
        pv.append(cash + pos * prices[i])
    pv = np.array(pv)
    returns = np.diff(pv) / pv[:-1]
    return {"portfolio_values": pv, "total_return": (pv[-1] / pv[0] - 1) * 100,
            "sharpe": np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252),
            "max_drawdown": np.max((np.maximum.accumulate(pv) - pv) / np.maximum.accumulate(pv)) * 100,
            "label": "Mean Reversion"}


def run_momentum(prices, lookback=60, hold_period=20, initial_cash=100000, commission=0.001):
    """Momentum strategy benchmark."""
    cash, pos = initial_cash, 0.0
    hold_counter = 0
    pv = [initial_cash]
    for i in range(lookback + 1, len(prices)):
        mom = prices[i] / prices[i - lookback] - 1
        if mom > 0.05 and pos == 0:
            pos = (cash * 0.99) / (prices[i] * (1 + commission))
            cash -= pos * prices[i] * (1 + commission)
            hold_counter = hold_period
        elif pos > 0:
            hold_counter -= 1
            if hold_counter <= 0:
                cash += pos * prices[i] * (1 - commission)
                pos = 0
        pv.append(cash + pos * prices[i])
    pv = np.array(pv)
    returns = np.diff(pv) / pv[:-1]
    return {"portfolio_values": pv, "total_return": (pv[-1] / pv[0] - 1) * 100,
            "sharpe": np.mean(returns) / (np.std(returns) + 1e-8) * np.sqrt(252),
            "max_drawdown": np.max((np.maximum.accumulate(pv) - pv) / np.maximum.accumulate(pv)) * 100,
            "label": "Momentum"}


# ═══════════════════════════════════════════════
# WALK-FORWARD VALIDATION
# ═══════════════════════════════════════════════
def walk_forward_validation(prices, features, n_folds=4, train_ratio=0.7,
                            initial_cash=100000, commission=0.001,
                            n_episodes=100, n_agents=3, progress_callback=None, **env_kwargs):
    """Rolling window walk-forward: train on window, test on next segment, slide."""
    total_len = len(prices)
    fold_size = total_len // n_folds
    results = []

    for fold in range(n_folds):
        start = fold * fold_size
        end = min(start + fold_size, total_len)
        if end - start < 100:
            continue
        split = start + int((end - start) * train_ratio)

        train_p = prices[start:split]
        train_f = features[start:split]
        test_p = prices[split:end]
        test_f = features[split:end]

        if len(train_p) < 50 or len(test_p) < 20:
            continue

        def env_factory():
            return TradingEnv(train_p, train_f, initial_cash=initial_cash,
                              commission=commission, **env_kwargs)

        agents, _ = train_ensemble(env_factory, n_agents=n_agents, n_episodes=n_episodes)

        test_env = TradingEnv(test_p, test_f, initial_cash=initial_cash,
                              commission=commission, **env_kwargs)
        bt = run_backtest(test_env, agents=agents)
        bh = run_buy_hold(test_p, initial_cash)

        results.append({
            "fold": fold + 1,
            "train_bars": len(train_p),
            "test_bars": len(test_p),
            "rl_return": bt["total_return"],
            "bh_return": bh["total_return"],
            "rl_sharpe": bt["sharpe"],
            "bh_sharpe": bh["sharpe"],
            "rl_max_dd": bt["max_drawdown"],
            "excess_return": bt["total_return"] - bh["total_return"],
        })

        if progress_callback:
            progress_callback(fold, n_folds)

    return results


# ═══════════════════════════════════════════════
# FEATURE IMPORTANCE (SHAP-like)
# ═══════════════════════════════════════════════
def compute_feature_importance(policy_net, sample_states, feature_names):
    """Permutation importance: shuffle each feature and measure Q-value change."""
    base_q = policy_net.predict(sample_states)
    base_actions = np.argmax(base_q, axis=1)
    base_values = np.max(base_q, axis=1)

    importances = {}
    n_features_per_step = len(feature_names)

    for i, name in enumerate(feature_names):
        perturbed = sample_states.copy()
        # Shuffle this feature across all stacked time steps
        for stack_offset in range(sample_states.shape[1] // n_features_per_step):
            col = stack_offset * n_features_per_step + i
            if col < perturbed.shape[1]:
                np.random.shuffle(perturbed[:, col])

        pert_q = policy_net.predict(perturbed)
        pert_values = np.max(pert_q, axis=1)
        importance = np.mean(np.abs(base_values - pert_values))
        importances[name] = importance

    # Normalize
    total = sum(importances.values()) + 1e-8
    return {k: v / total for k, v in importances.items()}


# ═══════════════════════════════════════════════
# STATISTICAL SIGNIFICANCE (Bootstrap)
# ═══════════════════════════════════════════════
def bootstrap_significance(rl_returns, bh_returns, n_bootstrap=5000):
    """Bootstrap test: is RL excess return statistically significant?
    Returns p-value and confidence interval for excess Sharpe."""
    n = min(len(rl_returns), len(bh_returns))
    rl_r = rl_returns[:n]
    bh_r = bh_returns[:n]
    excess = rl_r - bh_r

    observed_sharpe_diff = (np.mean(rl_r) / (np.std(rl_r) + 1e-8) - np.mean(bh_r) / (np.std(bh_r) + 1e-8)) * np.sqrt(252)

    boot_sharpe_diffs = []
    rng = np.random.default_rng(42)
    for _ in range(n_bootstrap):
        idx = rng.integers(0, n, n)
        rl_boot = rl_r[idx]
        bh_boot = bh_r[idx]
        s_rl = np.mean(rl_boot) / (np.std(rl_boot) + 1e-8) * np.sqrt(252)
        s_bh = np.mean(bh_boot) / (np.std(bh_boot) + 1e-8) * np.sqrt(252)
        boot_sharpe_diffs.append(s_rl - s_bh)

    boot_sharpe_diffs = np.array(boot_sharpe_diffs)
    p_value = np.mean(boot_sharpe_diffs <= 0)  # proportion where RL is NOT better
    ci_lo = np.percentile(boot_sharpe_diffs, 5)
    ci_hi = np.percentile(boot_sharpe_diffs, 95)

    # Also bootstrap excess return
    boot_excess_ret = []
    for _ in range(n_bootstrap):
        idx = rng.integers(0, n, n)
        boot_excess_ret.append(np.mean(excess[idx]) * 252 * 100)  # annualized %
    boot_excess_ret = np.array(boot_excess_ret)
    ret_p = np.mean(boot_excess_ret <= 0)

    return {
        "observed_sharpe_diff": observed_sharpe_diff,
        "p_value_sharpe": p_value,
        "ci_sharpe": (ci_lo, ci_hi),
        "boot_sharpe_diffs": boot_sharpe_diffs,
        "p_value_return": ret_p,
        "boot_excess_returns": boot_excess_ret,
    }


# ═══════════════════════════════════════════════
# MONTE CARLO ROBUSTNESS
# ═══════════════════════════════════════════════
def monte_carlo_robustness(env_factory, agents, n_sims=200, noise_std=0.001):
    """Test strategy robustness by adding noise to prices and re-running backtests.
    If performance degrades sharply with small noise, the strategy is fragile."""
    results = []
    rng = np.random.default_rng(42)

    for i in range(n_sims):
        env = env_factory()
        # Add small Gaussian noise to prices
        noise = rng.normal(0, noise_std, len(env.prices))
        env.prices = env.prices * (1 + noise)

        bt = run_backtest(env, agents=agents)
        results.append({
            "total_return": bt["total_return"],
            "sharpe": bt["sharpe"],
            "max_drawdown": bt["max_drawdown"],
        })

    df = pd.DataFrame(results)
    return {
        "returns": df["total_return"].values,
        "sharpes": df["sharpe"].values,
        "drawdowns": df["max_drawdown"].values,
        "mean_return": df["total_return"].mean(),
        "std_return": df["total_return"].std(),
        "mean_sharpe": df["sharpe"].mean(),
        "pct_profitable": (df["total_return"] > 0).mean() * 100,
        "worst_return": df["total_return"].min(),
        "best_return": df["total_return"].max(),
    }


# ═══════════════════════════════════════════════
# GROK STRATEGY ANALYSIS
# ═══════════════════════════════════════════════
def grok_analyze_strategy(api_key, ticker, train_bt, test_bt, feat_imp, wf_results,
                          boot_stats, mc_stats, n_agents, action_pcts):
    """Have Grok analyze the RL agent's learned strategy and provide a qualitative assessment."""
    from openai import OpenAI as OAI
    import json as _json

    client = OAI(base_url="https://api.x.ai/v1", api_key=api_key)

    prompt = f"""Analyze this reinforcement learning trading strategy for {ticker}:

PERFORMANCE:
- In-sample: Return {train_bt['total_return']:+.1f}%, Sharpe {train_bt['sharpe']:.2f}, Max DD {train_bt['max_drawdown']:.1f}%
- Out-of-sample: Return {test_bt['total_return']:+.1f}%, Sharpe {test_bt['sharpe']:.2f}, Max DD {test_bt['max_drawdown']:.1f}%
- Ensemble size: {n_agents} agents

ACTION DISTRIBUTION (out-of-sample):
- Buy: {action_pcts.get('buy', 0):.0f}%, Sell: {action_pcts.get('sell', 0):.0f}%, Hold: {action_pcts.get('hold', 0):.0f}%, Short: {action_pcts.get('short', 0):.0f}%

TOP FEATURES (by importance):
{chr(10).join(f'- {k}: {v:.1%}' for k, v in sorted(feat_imp.items(), key=lambda x: x[1], reverse=True)[:7])}

WALK-FORWARD: {'Avg excess return: ' + f"{np.mean([r['excess_return'] for r in wf_results]):+.1f}%" + f', Won {sum(1 for r in wf_results if r["excess_return"] > 0)}/{len(wf_results)} folds' if wf_results else 'Not run'}

STATISTICAL SIGNIFICANCE:
- Bootstrap p-value (Sharpe): {boot_stats['p_value_sharpe']:.3f}
- Excess Sharpe 90% CI: [{boot_stats['ci_sharpe'][0]:.2f}, {boot_stats['ci_sharpe'][1]:.2f}]

MONTE CARLO ROBUSTNESS:
- Mean return under noise: {mc_stats['mean_return']:+.1f}% (std: {mc_stats['std_return']:.1f}%)
- % profitable under noise: {mc_stats['pct_profitable']:.0f}%
- Worst case: {mc_stats['worst_return']:+.1f}%

Search X/Twitter for the latest on {ticker} and whether current market conditions favor this strategy type.

Respond with JSON:
{{"assessment": "2-3 paragraph qualitative analysis of the strategy",
 "grade": "A/B/C/D/F",
 "strengths": ["...", "..."],
 "weaknesses": ["...", "..."],
 "recommendation": "Should this strategy be deployed, paper-traded, or discarded?",
 "market_context": "Is the current market environment favorable for this strategy type?"}}"""

    try:
        response = client.chat.completions.create(
            model="grok-3",
            messages=[
                {"role": "system", "content": "You are a quantitative portfolio manager reviewing an RL trading strategy. Be rigorous and honest. Grade harshly."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=2000,
            temperature=0.3,
        )
        import re
        raw = response.choices[0].message.content
        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        result = _json.loads(cleaned)
        result["success"] = True
        return result
    except Exception as e:
        logger.error(f"Grok strategy analysis failed: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════
# PAGE UI
# ═══════════════════════════════════════════════
st.title("🦾 RL Trading Strategy Optimizer")
st.markdown("Deep Q-Network ensemble discovers optimal trading strategies with walk-forward validation.")

with st.expander("Training Parameters", expanded=True):
    _p1, _p2, _p3, _p4 = st.columns(4)
    with _p1:
        ticker_input = st.text_input("Ticker", value=get_active_ticker())
    with _p2:
        timeframe = st.selectbox("Timeframe", ["Daily", "Weekly"], index=0)
    with _p3:
        train_years = st.slider("Data Window (years)", 1, 10, 3)
    with _p4:
        test_pct = st.slider("Out-of-Sample %", 10, 40, 20)

    _m1, _m2, _m3 = st.columns(3)
    with _m1:
        n_episodes = st.select_slider("Episodes per Agent", [50, 100, 200, 500], value=200)
    with _m2:
        n_agents = st.select_slider("Ensemble Size", [1, 3, 5, 7], value=3)
    with _m3:
        reward_fn = st.selectbox("Reward Function", list(REWARD_FUNCTIONS.keys()), index=3)

    _r1, _r2, _r3, _r4, _r5, _r6 = st.columns(6)
    with _r1:
        commission = st.number_input("Commission (%)", 0.0, 1.0, 0.1, 0.05) / 100
    with _r2:
        spread_bps = st.number_input("Spread (bps)", 0, 20, 2)
    with _r3:
        slippage_bps = st.number_input("Slippage (bps)", 0, 20, 5)
    with _r4:
        stop_loss = st.number_input("Stop Loss (%)", 1.0, 20.0, 5.0, 1.0) / 100
    with _r5:
        max_daily_loss = st.number_input("Max Daily Loss (%)", 1.0, 10.0, 3.0, 0.5) / 100
    with _r6:
        initial_cash = st.number_input("Capital ($)", 10000, 1000000, 100000, 10000)

    _o1, _o2, _o3, _o4 = st.columns(4)
    with _o1:
        use_intermarket = st.checkbox("Include Intermarket Features (VIX, 10Y, Dollar)", value=True)
    with _o2:
        run_walkforward = st.checkbox("Run Walk-Forward Validation", value=False)
    with _o3:
        train_btn = st.button("Train Agent", type="primary", use_container_width=True)
    with _o4:
        train_bg = st.button("Train in Background", use_container_width=True,
                            help="Start training and explore other pages. You'll see a notification when it's done.")

ticker = ticker_input.strip().upper()
set_active_ticker(ticker)

# Background training handler
if train_bg:
    import threading

    def _bg_train():
        try:
            st.session_state["rl_bg_status"] = "running"
            st.session_state["rl_bg_ticker"] = ticker
            st.session_state["rl_bg_progress"] = 0

            _days_bg = train_years * 365
            df_bg = polygon_history(ticker, _days_bg)
            if df_bg.empty or len(df_bg) < 100:
                st.session_state["rl_bg_status"] = "error"
                st.session_state["rl_bg_error"] = "Not enough data"
                return
            if timeframe == "Weekly":
                df_bg = df_bg.resample("W-FRI").last().dropna()

            intermarket_bg = fetch_intermarket(f"{train_years}y") if use_intermarket else None
            extras_bg = fetch_stock_extras(ticker)
            sect_bg = None
            try:
                sdf = polygon_history(extras_bg.get("sector_etf", "SPY"), _days_bg)
                if not sdf.empty:
                    sect_bg = sdf["Close"]
            except Exception:
                pass

            split = int(len(df_bg) * (1 - test_pct / 100))
            train_df_bg = df_bg.iloc[:split]
            test_df_bg = df_bg.iloc[split:]
            warmup_bg = 60

            tp, tf = compute_features(train_df_bg, intermarket_bg, extras_bg, sect_bg)
            tep, tef = compute_features(test_df_bg, intermarket_bg, extras_bg, sect_bg)
            tp, tf = tp[warmup_bg:], tf[warmup_bg:]
            tep, tef = tep[warmup_bg:], tef[warmup_bg:]

            ek = dict(initial_cash=initial_cash, commission=commission,
                     spread_bps=spread_bps, slippage_bps=slippage_bps,
                     stop_loss_pct=stop_loss, max_daily_loss_pct=max_daily_loss)

            def ef():
                return TradingEnv(tp, tf, **ek)

            def prog(ep, total, *_):
                st.session_state["rl_bg_progress"] = (ep + 1) / total * 100

            agents_bg, atr_bg = train_ensemble(ef, n_agents=n_agents, n_episodes=n_episodes,
                                               reward_key=reward_fn, progress_callback=prog)

            # Backtests
            train_env_bg = TradingEnv(tp, tf, **ek)
            test_env_bg = TradingEnv(tep, tef, **ek)
            trbt = run_backtest(train_env_bg, agents=agents_bg)
            tebt = run_backtest(test_env_bg, agents=agents_bg)
            trbh = run_buy_hold(tp, initial_cash)
            tebh = run_buy_hold(tep, initial_cash)

            bms = [
                run_sma_crossover(tep, 20, 50, initial_cash, commission),
                run_mean_reversion(tep, 20, 2.0, initial_cash, commission),
                run_momentum(tep, 60, 20, initial_cash, commission),
            ]

            # Bootstrap + MC
            bh_ret = np.diff(tebh["portfolio_values"]) / tebh["portfolio_values"][:-1]
            bs = bootstrap_significance(tebt["returns"], bh_ret)
            def mc_ef():
                return TradingEnv(tep.copy(), tef.copy(), **ek)
            mcs = monte_carlo_robustness(mc_ef, agents_bg, n_sims=200)

            # Feature importance
            sample_env_bg = TradingEnv(tep, tef, **ek)
            state_bg = sample_env_bg.reset()
            ss = [state_bg]
            for _ in range(min(200, len(tep) - 2)):
                a = np.argmax(ensemble_predict(agents_bg, state_bg)[0])
                state_bg, _, d = sample_env_bg.step(a)
                ss.append(state_bg)
                if d:
                    break

            fn = ["Ret1d", "Ret5d", "Ret10d", "Ret20d", "Vol20d", "RSI", "MACD", "BBand%B",
                  "EMA20trend", "EMA50trend", "VolRatio", "ATR", "WeeklyMom",
                  "Fourier1", "Fourier2", "Fourier3"]
            if use_intermarket:
                fn += ["VIX", "10Y Yield", "Dollar", "Gold", "Oil"]
            fn += ["SectorRelStr", "SPYRelStr", "ShortInterest", "InsiderNet",
                   "DaysToEarnings", "Beta", "Position%", "UnrealPnL", "Cash%", "DailyPnL"]

            fi = compute_feature_importance(agents_bg[0], np.array(ss), fn)

            # Grok assessment
            grok_key_bg = None
            try:
                grok_key_bg = st.secrets.get("GROK_API_KEY")
            except Exception:
                pass
            gs = None
            if grok_key_bg:
                act_arr = np.array(tebt["actions"])
                ap = {"buy": np.isin(act_arr, [1,2,3]).sum()/len(act_arr)*100,
                      "sell": np.isin(act_arr, [4,5,6]).sum()/len(act_arr)*100,
                      "hold": (act_arr==0).sum()/len(act_arr)*100,
                      "short": (act_arr==7).sum()/len(act_arr)*100}
                gs = grok_analyze_strategy(grok_key_bg, ticker, trbt, tebt, fi, None, bs, mcs, n_agents, ap)

            st.session_state[f"rl_results_{ticker}"] = {
                "agents": agents_bg, "all_train_results": atr_bg,
                "train_bt": trbt, "test_bt": tebt, "train_bh": trbh, "test_bh": tebh,
                "benchmarks": bms, "wf_results": None,
                "feat_imp": fi, "feature_names": fn,
                "train_prices": tp, "test_prices": tep, "test_features": tef,
                "ticker": ticker, "n_agents": n_agents,
                "boot_stats": bs, "mc_stats": mcs, "grok_strat": gs, "env_kwargs": ek,
            }
            st.session_state["rl_bg_status"] = "done"
        except Exception as e:
            st.session_state["rl_bg_status"] = "error"
            st.session_state["rl_bg_error"] = str(e)
            logger.error(f"Background RL training failed: {e}", exc_info=True)

    thread = threading.Thread(target=_bg_train, daemon=True)
    thread.start()
    st.toast(f"RL training started for {ticker} in background. You can navigate away.", icon="🧠")
    st.rerun()

if train_btn or f"rl_results_{ticker}" in st.session_state:
    if train_btn:
        with fun_loader("data"):
            _days_fg = train_years * 365
            df = polygon_history(ticker, _days_fg)
            if df.empty or len(df) < 100:
                st.error("Not enough data.")
                st.stop()
            if timeframe == "Weekly":
                df = df.resample("W-FRI").last().dropna()

        intermarket = fetch_intermarket(f"{train_years}y") if use_intermarket else None

        # Fetch enhanced data
        with fun_loader("data"):
            stock_extras = fetch_stock_extras(ticker)

            # Sector ETF for relative strength
            sector_etf = stock_extras.get("sector_etf", "SPY")
            sector_data = None
            try:
                sdf = polygon_history(sector_etf, _days_fg)
                if not sdf.empty:
                    sector_data = sdf["Close"]
            except Exception:
                pass

        split_idx = int(len(df) * (1 - test_pct / 100))
        train_df = df.iloc[:split_idx]
        test_df = df.iloc[split_idx:]

        train_prices, train_features = compute_features(train_df, intermarket, stock_extras, sector_data)
        test_prices, test_features = compute_features(test_df, intermarket, stock_extras, sector_data)

        warmup = 60
        train_prices, train_features = train_prices[warmup:], train_features[warmup:]
        test_prices, test_features = test_prices[warmup:], test_features[warmup:]

        env_kwargs = dict(initial_cash=initial_cash, commission=commission,
                         spread_bps=spread_bps, slippage_bps=slippage_bps,
                         stop_loss_pct=stop_loss, max_daily_loss_pct=max_daily_loss)

        # Train ensemble
        progress_bar = st.progress(0, text="Training RL ensemble...")

        def progress_cb(ep, total, reward, value, eps):
            progress_bar.progress((ep + 1) / total,
                                  text=f"Agent training: Episode {ep+1}/{total} | "
                                       f"Reward: {reward:.0f} | Value: ${value:,.0f}")

        def env_factory():
            return TradingEnv(train_prices, train_features, **env_kwargs)

        with fun_loader("compute"):
            agents, all_train_results = train_ensemble(
                env_factory, n_agents=n_agents, n_episodes=n_episodes,
                reward_key=reward_fn, progress_callback=progress_cb,
            )

        progress_bar.empty()

        # Backtest — ensemble on train and test
        train_env = TradingEnv(train_prices, train_features, **env_kwargs)
        test_env = TradingEnv(test_prices, test_features, **env_kwargs)
        train_bt = run_backtest(train_env, agents=agents)
        test_bt = run_backtest(test_env, agents=agents)
        train_bh = run_buy_hold(train_prices, initial_cash)
        test_bh = run_buy_hold(test_prices, initial_cash)

        # Benchmark strategies
        benchmarks = [
            run_sma_crossover(test_prices, 20, 50, initial_cash, commission),
            run_mean_reversion(test_prices, 20, 2.0, initial_cash, commission),
            run_momentum(test_prices, 60, 20, initial_cash, commission),
        ]

        # Walk-forward (if enabled)
        wf_results = None
        if run_walkforward:
            with fun_loader("compute"):
                all_p, all_f = compute_features(df, intermarket, stock_extras, sector_data)
                all_p, all_f = all_p[warmup:], all_f[warmup:]
                wf_results = walk_forward_validation(
                    all_p, all_f, n_folds=4, n_episodes=min(n_episodes, 100),
                    n_agents=min(n_agents, 3), initial_cash=initial_cash,
                    commission=commission, **{k: v for k, v in env_kwargs.items()
                                             if k not in ("initial_cash", "commission")},
                )

        # Feature importance
        sample_env = TradingEnv(test_prices, test_features, **env_kwargs)
        state = sample_env.reset()
        sample_states = [state]
        for _ in range(min(200, len(test_prices) - 2)):
            action = np.argmax(ensemble_predict(agents, state)[0])
            state, _, done = sample_env.step(action)
            sample_states.append(state)
            if done:
                break
        sample_states = np.array(sample_states)

        base_feature_names = [
            "Ret1d", "Ret5d", "Ret10d", "Ret20d", "Vol20d", "RSI",
            "MACD", "BBand%B", "EMA20trend", "EMA50trend", "VolRatio",
            "ATR", "WeeklyMom",
            "Fourier1", "Fourier2", "Fourier3",  # cycle detection
        ]
        if use_intermarket:
            base_feature_names += ["VIX", "10Y Yield", "Dollar", "Gold", "Oil"]
        base_feature_names += [
            "SectorRelStr", "SPYRelStr",  # relative strength
            "ShortInterest", "InsiderNet", "DaysToEarnings", "Beta",  # fundamentals
            "Position%", "UnrealPnL", "Cash%", "DailyPnL",  # portfolio state
        ]

        feat_imp = compute_feature_importance(agents[0], sample_states, base_feature_names)

        # Bootstrap significance test
        with fun_loader("compute"):
            bh_returns = np.diff(test_bh["portfolio_values"]) / test_bh["portfolio_values"][:-1]
            boot_stats = bootstrap_significance(test_bt["returns"], bh_returns)

        # Monte Carlo robustness
        with fun_loader("compute"):
            def mc_env_factory():
                return TradingEnv(test_prices.copy(), test_features.copy(), **env_kwargs)
            mc_stats = monte_carlo_robustness(mc_env_factory, agents, n_sims=200)

        # Grok strategy analysis
        from src.api_keys import get_secret
        grok_key = get_secret("GROK_API_KEY")
        if not grok_key:
            try:
                grok_key = st.secrets.get("GROK_API_KEY")
            except Exception:
                pass

        grok_strat = None
        if grok_key:
            with fun_loader("ai"):
                actions_arr = np.array(test_bt["actions"])
                act_pcts = {
                    "buy": np.isin(actions_arr, [1, 2, 3]).sum() / len(actions_arr) * 100,
                    "sell": np.isin(actions_arr, [4, 5, 6]).sum() / len(actions_arr) * 100,
                    "hold": (actions_arr == 0).sum() / len(actions_arr) * 100,
                    "short": (actions_arr == 7).sum() / len(actions_arr) * 100,
                }
                grok_strat = grok_analyze_strategy(
                    grok_key, ticker, train_bt, test_bt, feat_imp, wf_results,
                    boot_stats, mc_stats, n_agents, act_pcts,
                )

        st.session_state[f"rl_results_{ticker}"] = {
            "agents": agents, "all_train_results": all_train_results,
            "train_bt": train_bt, "test_bt": test_bt,
            "train_bh": train_bh, "test_bh": test_bh,
            "benchmarks": benchmarks, "wf_results": wf_results,
            "feat_imp": feat_imp, "feature_names": base_feature_names,
            "train_prices": train_prices, "test_prices": test_prices,
            "test_features": test_features,
            "ticker": ticker, "n_agents": n_agents,
            "boot_stats": boot_stats, "mc_stats": mc_stats,
            "grok_strat": grok_strat, "env_kwargs": env_kwargs,
        }

    cached = st.session_state.get(f"rl_results_{ticker}")
    if not cached:
        st.info("Configure and click **Train Agent**.")
        st.stop()

    train_bt = cached["train_bt"]
    test_bt = cached["test_bt"]
    train_bh = cached["train_bh"]
    test_bh = cached["test_bh"]
    benchmarks = cached["benchmarks"]
    wf_results = cached.get("wf_results")
    feat_imp = cached["feat_imp"]
    all_train_results = cached["all_train_results"]
    boot_stats = cached.get("boot_stats", {})
    mc_stats = cached.get("mc_stats", {})
    grok_strat = cached.get("grok_strat")

    # ═══════════════════════════════════════════
    # TABS
    # ═══════════════════════════════════════════
    tabs = st.tabs(["How It Works", "Performance", "Out-of-Sample", "Walk-Forward",
                    "Statistical Tests", "Robustness", "AI Assessment",
                    "Training Diagnostics", "Trade Analysis", "Strategy Insights"])

    with tabs[0]:
        st.markdown("### How the RL Trading Agent Works")
        st.markdown("""
This page trains a **reinforcement learning agent** that discovers optimal trading strategies
directly from historical market data. Unlike traditional rule-based strategies (SMA crossover, RSI),
the agent learns its own rules through trial and error — millions of simulated trades across
hundreds of training episodes.

---

**The Learning Loop**

The agent follows a cycle every trading day:

1. **Observe** — Reads 31 market features (technicals, intermarket, fundamentals) across the last 5 days (155 total inputs)
2. **Decide** — Neural network outputs a Q-value for each of 8 possible actions
3. **Act** — Executes the action (buy, sell, hold, or short at various sizes)
4. **Learn** — Receives a reward based on risk-adjusted return and updates its neural network

Over hundreds of episodes, the agent discovers which patterns lead to profitable trades and which don't.

---

**Architecture: Dueling DQN with Prioritized Experience Replay**

| Component | What It Does |
|-----------|-------------|
| **Dueling DQN** | Separates "how good is this state?" (Value) from "which action is best?" (Advantage). This means the agent can learn that a market environment is dangerous even before deciding what to do about it. |
| **Prioritized Replay** | Stores every experience. Replays surprising experiences (high prediction error) more often — the agent learns faster from its biggest mistakes. |
| **Target Network** | A frozen copy of the neural network updated every 10 episodes. Prevents the agent from chasing a moving target during training. |
| **Epsilon-Greedy** | Starts by exploring randomly (100%), gradually shifts to exploiting its learned policy (95%+ exploitation by end). Balances discovery vs optimization. |
| **Ensemble** | Multiple agents trained with different random seeds. Actions decided by averaging their opinions. Reduces variance and overfitting. |

---

**State Space: What the Agent Sees (31 features × 5 timesteps = 155 inputs)**
""")

        feat_categories = {
            "Core Technicals (13)": [
                "**Returns** (1, 5, 10, 20 day) — recent price momentum at multiple horizons",
                "**Volatility** (20-day) — current risk level",
                "**RSI** (14-day) — overbought/oversold signal",
                "**MACD Histogram** — trend momentum and crossover signal",
                "**Bollinger %B** — where price sits within its normal range",
                "**EMA Trends** (20, 50) — price position vs moving averages",
                "**Volume Ratio** — unusual trading activity vs 20-day average",
                "**ATR** — average daily price range (volatility measure)",
                "**Weekly Momentum** — medium-term trend strength",
            ],
            "Cycle Detection (3)": [
                "**Fourier Harmonics** — dominant cyclical patterns extracted via FFT (Fast Fourier Transform). Captures weekly, monthly, and seasonal cycles that moving averages miss.",
            ],
            "Intermarket (5)": [
                "**VIX** — market fear/complacency",
                "**10-Year Yield** — rate sensitivity and bond market signal",
                "**Dollar Index** — currency regime",
                "**Gold** — risk-off/inflation hedge signal",
                "**Oil** — energy/inflation regime",
            ],
            "Relative Strength (2)": [
                "**Sector Relative Strength** — is the stock outperforming its sector ETF?",
                "**SPY Relative Strength** — is it outperforming the broad market?",
            ],
            "Fundamentals (4)": [
                "**Short Interest** — % of float sold short (squeeze risk)",
                "**Insider Net Buying** — are insiders buying or selling?",
                "**Days to Earnings** — ramps to 1.0 as earnings approach (behavior changes near events)",
                "**Beta** — how much the stock moves with the market",
            ],
            "Portfolio State (4)": [
                "**Position %** — current exposure as % of portfolio",
                "**Unrealized P&L** — profit/loss on open position",
                "**Cash %** — available buying power",
                "**Daily P&L** — intraday performance (triggers risk limits)",
            ],
        }

        for cat, items in feat_categories.items():
            with st.expander(cat):
                for item in items:
                    st.markdown(f"- {item}")

        st.markdown("""
---

**Action Space: What the Agent Can Do (8 actions)**

| Action | Description |
|--------|------------|
| **Hold** | Do nothing — maintain current position |
| **Buy 25%** | Invest 25% of portfolio value |
| **Buy 50%** | Invest 50% of portfolio value |
| **Buy 100%** | Go all-in (99% of cash) |
| **Sell 25%** | Reduce position by 25% |
| **Sell 50%** | Reduce position by 50% |
| **Sell 100%** | Close entire position (or cover short) |
| **Short 50%** | Bet against the stock with 50% of portfolio |

---

**Risk Management (Hard Constraints)**

These are NOT learned — they are enforced rules the agent cannot override:

| Rule | What It Does |
|------|-------------|
| **Stop-Loss** | Automatically sells if position drops below threshold (default: 5%) |
| **Max Daily Loss** | Blocks new positions if daily P&L exceeds limit (default: 3%) |
| **Commission** | Realistic trading cost applied to every transaction |
| **Spread** | Bid-ask spread cost in basis points |
| **Slippage** | Market impact cost in basis points |
| **Borrow Rate** | Annualized cost for short positions |

---

**Reward Function**

The agent is rewarded for risk-adjusted returns, not just raw profit:

`Reward = Return × 100 − Drawdown × 50 − Transaction Cost Penalty`

This means the agent learns to:
- Make money (positive return term)
- Avoid large drawdowns (drawdown penalty)
- Trade only when the expected edge exceeds costs (transaction penalty)

Different reward functions (Sharpe, Sortino, Calmar) can be selected in the sidebar
to produce different strategy styles.

---

**Validation: How We Know It's Not Overfitting**

| Method | What It Tests |
|--------|-------------|
| **Out-of-Sample** | Held-out data the agent never saw during training. If it works here, it generalizes. |
| **Walk-Forward** | Retrain on rolling windows, test on next segment, repeat. Simulates real-world deployment. |
| **Bootstrap Significance** | 5,000 resamples testing if excess return is statistically real (p-value). |
| **Monte Carlo Robustness** | Add random noise to prices and re-test. If performance collapses, strategy is fragile. |
| **Ensemble Averaging** | Multiple agents with different seeds must agree — reduces single-model overfitting. |
| **Grok AI Assessment** | Independent qualitative analysis by Grok 3, graded A-F. |
""")

    with tabs[1]:
        with error_boundary("In-Sample Performance"):
            st.markdown("### In-Sample Performance (Training Data)")
            m = st.columns(6)
            m[0].metric("RL Return", f"{train_bt['total_return']:+.1f}%",
                       f"{train_bt['total_return'] - train_bh['total_return']:+.1f}% vs B&H")
            m[1].metric("Buy & Hold", f"{train_bh['total_return']:+.1f}%")
            m[2].metric("Sharpe", f"{train_bt['sharpe']:.2f}")
            m[3].metric("Sortino", f"{train_bt['sortino']:.2f}")
            m[4].metric("Max DD", f"{train_bt['max_drawdown']:.1f}%", delta_color="inverse")
            m[5].metric("Trades", train_bt["n_trades"])

            fig_eq = go.Figure()
            fig_eq.add_trace(go.Scatter(y=train_bt["portfolio_values"], mode="lines",
                                       name=f"RL Ensemble ({cached['n_agents']} agents)",
                                       line=dict(color=COLORS["accent"], width=2)))
            fig_eq.add_trace(go.Scatter(y=train_bh["portfolio_values"], mode="lines",
                                       name="Buy & Hold", line=dict(color="#888", dash="dash")))
            fig_eq.update_layout(template="plotly_dark", height=400,
                                margin=dict(t=30, b=0, l=0, r=0),
                                yaxis_title="Portfolio ($)", legend=dict(orientation="h", y=1.02))
            st.plotly_chart(fig_eq, use_container_width=True)

            fig_dd = go.Figure()
            fig_dd.add_trace(go.Scatter(y=-train_bt["drawdowns"] * 100, fill="tozeroy",
                                       fillcolor="rgba(255,68,68,0.2)",
                                       line=dict(color=COLORS["danger"], width=1), name="Drawdown"))
            fig_dd.update_layout(template="plotly_dark", height=180,
                                margin=dict(t=10, b=0, l=0, r=0), yaxis_title="Drawdown (%)")
            st.plotly_chart(fig_dd, use_container_width=True)

    with tabs[2]:
        with error_boundary("Out-of-Sample"):
            st.markdown("### Out-of-Sample Performance (Unseen Data)")
            st.caption("The agent has NEVER seen this data during training.")

            m2 = st.columns(6)
            m2[0].metric("RL Return", f"{test_bt['total_return']:+.1f}%",
                        f"{test_bt['total_return'] - test_bh['total_return']:+.1f}% vs B&H")
            m2[1].metric("Buy & Hold", f"{test_bh['total_return']:+.1f}%")
            m2[2].metric("Sharpe", f"{test_bt['sharpe']:.2f}")
            m2[3].metric("Sortino", f"{test_bt['sortino']:.2f}")
            m2[4].metric("Max DD", f"{test_bt['max_drawdown']:.1f}%", delta_color="inverse")
            m2[5].metric("Trades", test_bt["n_trades"])

            # Overfit check
            ratio = test_bt["sharpe"] / (train_bt["sharpe"] + 1e-8)
            if ratio >= 0.5 and test_bt["sharpe"] > 0:
                st.success(f"Healthy generalization: OOS/IS Sharpe ratio = {ratio:.2f}")
            elif test_bt["sharpe"] > 0:
                st.warning(f"Possible overfitting: OOS/IS Sharpe ratio = {ratio:.2f}")
            else:
                st.error(f"Strategy does not generalize. OOS Sharpe = {test_bt['sharpe']:.2f}")

            fig_test = go.Figure()
            fig_test.add_trace(go.Scatter(y=test_bt["portfolio_values"], mode="lines",
                                         name="RL Ensemble", line=dict(color=COLORS["accent"], width=2)))
            fig_test.add_trace(go.Scatter(y=test_bh["portfolio_values"], mode="lines",
                                         name="Buy & Hold", line=dict(color="#888", dash="dash")))
            for bm in benchmarks:
                fig_test.add_trace(go.Scatter(y=bm["portfolio_values"], mode="lines",
                                             name=bm["label"], line=dict(width=1)))
            fig_test.update_layout(template="plotly_dark", height=400,
                                  margin=dict(t=30, b=0, l=0, r=0),
                                  yaxis_title="Portfolio ($)", legend=dict(orientation="h", y=1.02))
            st.plotly_chart(fig_test, use_container_width=True)

            # Full comparison table
            st.markdown("**Strategy Comparison (Out-of-Sample)**")
            rows = [{"Strategy": "RL Ensemble", "Return": f"{test_bt['total_return']:+.1f}%",
                    "Sharpe": f"{test_bt['sharpe']:.2f}", "Max DD": f"{test_bt['max_drawdown']:.1f}%"},
                   {"Strategy": "Buy & Hold", "Return": f"{test_bh['total_return']:+.1f}%",
                    "Sharpe": f"{test_bh['sharpe']:.2f}", "Max DD": f"{test_bh['max_drawdown']:.1f}%"}]
            for bm in benchmarks:
                rows.append({"Strategy": bm["label"], "Return": f"{bm['total_return']:+.1f}%",
                            "Sharpe": f"{bm['sharpe']:.2f}", "Max DD": f"{bm['max_drawdown']:.1f}%"})
            st.dataframe(pd.DataFrame(rows).set_index("Strategy"), use_container_width=True)

    with tabs[3]:
        with error_boundary("Walk-Forward"):
            st.markdown("### Walk-Forward Validation")
            if wf_results:
                st.caption(f"{len(wf_results)} folds — retrain on rolling windows, test on next segment.")

                wf_df = pd.DataFrame(wf_results)
                avg_excess = wf_df["excess_return"].mean()
                win_folds = (wf_df["excess_return"] > 0).sum()

                wc = st.columns(4)
                wc[0].metric("Avg Excess Return", f"{avg_excess:+.1f}%")
                wc[1].metric("Win Folds", f"{win_folds}/{len(wf_results)}")
                wc[2].metric("Avg RL Sharpe", f"{wf_df['rl_sharpe'].mean():.2f}")
                wc[3].metric("Avg B&H Sharpe", f"{wf_df['bh_sharpe'].mean():.2f}")

                fig_wf = go.Figure()
                fig_wf.add_trace(go.Bar(x=[f"Fold {r['fold']}" for r in wf_results],
                                       y=[r["rl_return"] for r in wf_results],
                                       name="RL", marker_color=COLORS["accent"]))
                fig_wf.add_trace(go.Bar(x=[f"Fold {r['fold']}" for r in wf_results],
                                       y=[r["bh_return"] for r in wf_results],
                                       name="B&H", marker_color="#888"))
                fig_wf.update_layout(template="plotly_dark", barmode="group", height=300,
                                    margin=dict(t=10, b=0, l=0, r=0), yaxis_title="Return (%)")
                st.plotly_chart(fig_wf, use_container_width=True)

                st.dataframe(wf_df.set_index("fold"), use_container_width=True)

                if avg_excess > 0 and win_folds > len(wf_results) / 2:
                    st.success("Walk-forward results are positive — strategy shows real edge.")
                else:
                    st.warning("Walk-forward results are mixed — strategy may not have a consistent edge.")
            else:
                st.info("Enable **Walk-Forward Validation** in the sidebar to run this analysis.")

    with tabs[4]:
        with error_boundary("Statistical Tests"):
            st.markdown("### Statistical Significance Testing")
            st.caption("Bootstrap test (5,000 resamples): Is the RL strategy's excess return statistically significant?")

            if boot_stats:
                sc = st.columns(4)
                sc[0].metric("Excess Sharpe", f"{boot_stats['observed_sharpe_diff']:+.2f}")
                sc[1].metric("p-value (Sharpe)", f"{boot_stats['p_value_sharpe']:.3f}",
                            help="Probability that RL is NOT better than B&H. Lower = more significant.")
                sc[2].metric("90% CI", f"[{boot_stats['ci_sharpe'][0]:.2f}, {boot_stats['ci_sharpe'][1]:.2f}]")
                sc[3].metric("p-value (Return)", f"{boot_stats['p_value_return']:.3f}")

                if boot_stats["p_value_sharpe"] < 0.05:
                    st.success(f"Statistically significant at 95% confidence (p={boot_stats['p_value_sharpe']:.3f}). "
                              f"The RL strategy's edge is real with high probability.")
                elif boot_stats["p_value_sharpe"] < 0.10:
                    st.warning(f"Marginally significant (p={boot_stats['p_value_sharpe']:.3f}). "
                              f"Some evidence of edge but not conclusive.")
                else:
                    st.error(f"Not statistically significant (p={boot_stats['p_value_sharpe']:.3f}). "
                            f"Cannot conclude the strategy has a real edge over buy-and-hold.")

                # Bootstrap distribution plot
                fig_boot = make_subplots(rows=1, cols=2,
                                        subplot_titles=["Excess Sharpe Distribution", "Excess Return Distribution"])
                fig_boot.add_trace(go.Histogram(x=boot_stats["boot_sharpe_diffs"], nbinsx=50,
                                               marker_color=COLORS["accent"], opacity=0.7), row=1, col=1)
                fig_boot.add_vline(x=0, line_dash="dash", line_color="white", row=1, col=1)
                fig_boot.add_vline(x=boot_stats["observed_sharpe_diff"], line_color=COLORS["success"],
                                  annotation_text="Observed", row=1, col=1)

                fig_boot.add_trace(go.Histogram(x=boot_stats["boot_excess_returns"], nbinsx=50,
                                               marker_color=COLORS["warning"], opacity=0.7), row=1, col=2)
                fig_boot.add_vline(x=0, line_dash="dash", line_color="white", row=1, col=2)

                fig_boot.update_layout(template="plotly_dark", height=300,
                                      margin=dict(t=30, b=0, l=0, r=0), showlegend=False)
                st.plotly_chart(fig_boot, use_container_width=True)
            else:
                st.info("No bootstrap results available.")

    with tabs[5]:
        with error_boundary("Robustness"):
            st.markdown("### Monte Carlo Robustness Testing")
            st.caption("200 simulations with small random price noise (0.1%). "
                      "If performance degrades sharply, the strategy is fragile and curve-fit.")

            if mc_stats:
                rc = st.columns(5)
                rc[0].metric("Mean Return", f"{mc_stats['mean_return']:+.1f}%")
                rc[1].metric("Std Dev", f"{mc_stats['std_return']:.1f}%")
                rc[2].metric("% Profitable", f"{mc_stats['pct_profitable']:.0f}%")
                rc[3].metric("Best Case", f"{mc_stats['best_return']:+.1f}%")
                rc[4].metric("Worst Case", f"{mc_stats['worst_return']:+.1f}%")

                if mc_stats["pct_profitable"] > 80:
                    st.success(f"Highly robust: {mc_stats['pct_profitable']:.0f}% of noisy simulations are profitable.")
                elif mc_stats["pct_profitable"] > 60:
                    st.info(f"Moderately robust: {mc_stats['pct_profitable']:.0f}% profitable under noise.")
                else:
                    st.warning(f"Fragile: only {mc_stats['pct_profitable']:.0f}% profitable under noise. Likely curve-fit.")

                # Return distribution under noise
                fig_mc = go.Figure()
                fig_mc.add_trace(go.Histogram(x=mc_stats["returns"], nbinsx=40,
                                             marker_color=COLORS["accent"], opacity=0.7))
                fig_mc.add_vline(x=0, line_dash="dash", line_color="white")
                fig_mc.add_vline(x=mc_stats["mean_return"], line_color=COLORS["success"],
                                annotation_text=f"Mean: {mc_stats['mean_return']:+.1f}%")
                fig_mc.add_vrect(x0=mc_stats["worst_return"], x1=0,
                                fillcolor="rgba(255,68,68,0.08)", line_width=0)
                fig_mc.update_layout(template="plotly_dark", height=300,
                                    margin=dict(t=30, b=0, l=0, r=0),
                                    xaxis_title="Total Return (%)", yaxis_title="Frequency")
                st.plotly_chart(fig_mc, use_container_width=True)

                # Sharpe distribution
                fig_mcs = go.Figure()
                fig_mcs.add_trace(go.Histogram(x=mc_stats["sharpes"], nbinsx=40,
                                              marker_color=COLORS["warning"], opacity=0.7))
                fig_mcs.add_vline(x=0, line_dash="dash", line_color="white")
                fig_mcs.update_layout(template="plotly_dark", height=250,
                                    margin=dict(t=10, b=0, l=0, r=0),
                                    xaxis_title="Sharpe Ratio", yaxis_title="Frequency")
                st.plotly_chart(fig_mcs, use_container_width=True)
            else:
                st.info("No Monte Carlo results available.")

    with tabs[6]:
        with error_boundary("AI Assessment"):
            st.markdown("### Grok AI Strategy Assessment")
            st.caption("Independent qualitative analysis of the learned strategy by Grok 3.")

            if grok_strat and grok_strat.get("success"):
                # Grade badge
                from html import escape as _esc
                grade = _esc(str(grok_strat.get("grade", "?")))
                grade_colors = {"A": "#00ff96", "B": "#00cc66", "C": "#ffaa00", "D": "#ff6644", "F": "#ff4444"}
                g_color = grade_colors.get(grade[0] if grade else "?", "#888")
                st.markdown(f'<div style="display:inline-block;background:{g_color};color:#000;'
                           f'padding:8px 20px;border-radius:8px;font-weight:700;font-size:1.5rem;">'
                           f'Grade: {grade}</div>', unsafe_allow_html=True)

                # Assessment
                assessment = grok_strat.get("assessment", "")
                if assessment:
                    st.markdown(assessment)

                # Recommendation
                rec = grok_strat.get("recommendation", "")
                if rec:
                    st.info(f"**Recommendation:** {rec}")

                # Market context
                mkt_ctx = grok_strat.get("market_context", "")
                if mkt_ctx:
                    st.caption(f"**Market Context:** {mkt_ctx}")

                # Strengths / Weaknesses
                sw = st.columns(2)
                with sw[0]:
                    st.markdown("**Strengths**")
                    for s in grok_strat.get("strengths", []):
                        st.markdown(f"- {s}")
                with sw[1]:
                    st.markdown("**Weaknesses**")
                    for w in grok_strat.get("weaknesses", []):
                        st.markdown(f"- {w}")
            elif grok_strat:
                st.warning(f"Grok analysis failed: {grok_strat.get('error', 'Unknown')}")
            else:
                st.info("Grok API key not configured. Add GROK_API_KEY to secrets.")

    with tabs[7]:
        with error_boundary("Training Diagnostics"):
            st.markdown("### Training Convergence")

            # Show all agents' learning curves
            fig_train = make_subplots(rows=2, cols=1, shared_xaxes=True,
                                     subplot_titles=["Episode Reward (per agent)", "Portfolio Value"],
                                     vertical_spacing=0.08)

            colors_list = ["#00d1ff", "#00ff96", "#ffaa00", "#ff4444", "#aa66ff", "#ff69b4", "#4caf50"]
            for idx, result in enumerate(all_train_results):
                c = colors_list[idx % len(colors_list)]
                rewards = result["episode_rewards"]
                smooth = pd.Series(rewards).rolling(max(1, len(rewards) // 15), min_periods=1).mean()
                fig_train.add_trace(go.Scatter(y=smooth, mode="lines", name=f"Agent {idx+1}",
                                              line=dict(color=c, width=1.5)), row=1, col=1)
                fig_train.add_trace(go.Scatter(y=result["episode_values"], mode="lines",
                                              showlegend=False, line=dict(color=c, width=1)), row=2, col=1)

            fig_train.update_layout(template="plotly_dark", height=450,
                                   margin=dict(t=30, b=0, l=0, r=0))
            st.plotly_chart(fig_train, use_container_width=True)

    with tabs[8]:
        with error_boundary("Trade Analysis"):
            st.markdown("### Trade Analysis (Out-of-Sample)")

            actions = test_bt["actions"]
            counts = np.bincount(actions, minlength=TradingEnv.N_ACTIONS)
            act_colors = [COLORS["text_muted"], COLORS["success"], COLORS["success"],
                         COLORS["success"], COLORS["danger"], COLORS["danger"],
                         COLORS["danger"], COLORS["warning"]]

            fig_act = go.Figure(go.Bar(x=TradingEnv.ACTION_NAMES, y=counts,
                                      marker_color=act_colors, text=counts, textposition="outside"))
            fig_act.update_layout(template="plotly_dark", height=280,
                                 margin=dict(t=10, b=0, l=0, r=0))
            st.plotly_chart(fig_act, use_container_width=True)

            if test_bt["trades"]:
                rows = [{"Bar": t["step"], "Action": t["action"].title(),
                        "Shares": f"{t['shares']:.1f}", "Price": f"${t['price']:.2f}"}
                       for t in test_bt["trades"][-30:]]
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    with tabs[9]:
        with error_boundary("Strategy Insights"):
            st.markdown("### What the Agent Learned")

            # Feature importance
            st.markdown("**Feature Importance (Permutation)**")
            st.caption("Which inputs most influence the agent's decisions. Higher = more important.")
            sorted_imp = sorted(feat_imp.items(), key=lambda x: x[1], reverse=True)
            fig_imp = go.Figure(go.Bar(
                x=[v for _, v in sorted_imp], y=[k for k, _ in sorted_imp],
                orientation="h", marker_color=COLORS["accent"],
                text=[f"{v:.1%}" for _, v in sorted_imp], textposition="outside",
            ))
            fig_imp.update_layout(template="plotly_dark", height=max(250, len(sorted_imp) * 22),
                                 margin=dict(t=10, b=0, l=120, r=0), xaxis_title="Relative Importance")
            st.plotly_chart(fig_imp, use_container_width=True)

            # Feature redundancy check (de Prado: correlated features inflate overfitting)
            if len(sorted_imp) > 5:
                top_features = [k for k, _ in sorted_imp[:15]]
                # Get the feature matrix for correlation
                try:
                    feat_matrix = pd.DataFrame(
                        sample_states[:, :len(base_feature_names)],
                        columns=base_feature_names,
                    )
                    corr = feat_matrix[top_features].corr().abs()
                    # Find pairs with correlation > 0.8
                    redundant = []
                    for ii in range(len(top_features)):
                        for jj in range(ii + 1, len(top_features)):
                            if corr.iloc[ii, jj] > 0.8:
                                redundant.append((top_features[ii], top_features[jj], corr.iloc[ii, jj]))
                    if redundant:
                        st.warning(
                            f"**Feature Redundancy Detected:** {len(redundant)} highly correlated pairs "
                            f"(|r| > 0.8). Correlated features inflate the effective feature count and "
                            f"increase overfitting risk. Consider dropping one from each pair."
                        )
                        red_df = pd.DataFrame(redundant, columns=["Feature A", "Feature B", "Correlation"])
                        red_df["Correlation"] = red_df["Correlation"].apply(lambda x: f"{x:.2f}")
                        st.dataframe(red_df, use_container_width=True, hide_index=True)
                    else:
                        st.success("**No redundant features detected** — all top features have |r| < 0.8.")
                except Exception:
                    pass

            # Action patterns
            actions_arr = np.array(test_bt["actions"])
            buy_pct = np.isin(actions_arr, [1, 2, 3]).sum() / len(actions_arr) * 100
            sell_pct = np.isin(actions_arr, [4, 5, 6]).sum() / len(actions_arr) * 100
            hold_pct = (actions_arr == 0).sum() / len(actions_arr) * 100
            short_pct = (actions_arr == 7).sum() / len(actions_arr) * 100

            pc = st.columns(4)
            pc[0].metric("Buy %", f"{buy_pct:.0f}%")
            pc[1].metric("Sell %", f"{sell_pct:.0f}%")
            pc[2].metric("Hold %", f"{hold_pct:.0f}%")
            pc[3].metric("Short %", f"{short_pct:.0f}%")

            if hold_pct > 60:
                st.info("**Patient/Selective** — Agent waits for high-conviction entries.")
            elif buy_pct > 35 and sell_pct < 20:
                st.info("**Trend Following** — Agent rides positions, slow to exit.")
            elif sell_pct > buy_pct:
                st.info("**Mean Reverting/Defensive** — Quick profit-taking, risk-averse.")
            elif short_pct > 10:
                st.info("**Directional Flexible** — Uses both long and short positions.")
            else:
                st.info("**Balanced** — Mix of strategies depending on conditions.")

            # Full comparison
            st.divider()
            st.markdown("### Full Metrics Comparison")
            comp = pd.DataFrame({
                "Metric": ["Return", "Sharpe", "Sortino", "Calmar", "Max DD", "Trades"],
                "RL (Train)": [f"{train_bt['total_return']:+.1f}%", f"{train_bt['sharpe']:.2f}",
                              f"{train_bt['sortino']:.2f}", f"{train_bt['calmar']:.2f}",
                              f"{train_bt['max_drawdown']:.1f}%", str(train_bt["n_trades"])],
                "RL (Test)": [f"{test_bt['total_return']:+.1f}%", f"{test_bt['sharpe']:.2f}",
                             f"{test_bt['sortino']:.2f}", f"{test_bt['calmar']:.2f}",
                             f"{test_bt['max_drawdown']:.1f}%", str(test_bt["n_trades"])],
                "B&H (Test)": [f"{test_bh['total_return']:+.1f}%", f"{test_bh['sharpe']:.2f}",
                              "—", "—", f"{test_bh['max_drawdown']:.1f}%", "1"],
            })
            for bm in benchmarks:
                comp[bm["label"]] = [f"{bm['total_return']:+.1f}%", f"{bm['sharpe']:.2f}",
                                    "—", "—", f"{bm['max_drawdown']:.1f}%", "—"]
            st.dataframe(comp.set_index("Metric"), use_container_width=True)

    st.divider()
    st.caption(
        "**Methodology:** DQN ensemble (N agents, different random seeds) with experience replay, target network, "
        "epsilon-greedy exploration. State: 5-step stacked history of 16+ features (returns, RSI, MACD, BBands, "
        "EMAs, volume, ATR, weekly momentum, VIX, 10Y yield, dollar index, position %, unrealized P&L). "
        "8 actions with realistic cost model (commission + spread + slippage + borrow cost for shorts). "
        "Risk management: stop-loss, max daily loss. Walk-forward validation on rolling windows. "
        "Permutation feature importance for explainability. "
        "This is a research tool, not a live trading system."
    )

else:
    st.info("Configure parameters in the sidebar and click **Train Agent** to begin.")
