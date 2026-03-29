"""
Systematic Signal Scanner — Institutional-Grade Cross-Sectional Factor Analysis

Scans the investable universe across 6 factor dimensions:
1. Momentum — 12-1 cross-sectional, acceleration, risk-adjusted, consistency
2. Mean Reversion — RSI, Bollinger Band, z-score, confluence scoring
3. Value — P/E, P/B, EV/EBITDA, FCF yield relative ranking
4. Quality — ROE, margins, earnings growth, balance sheet strength
5. Earnings Momentum — EPS revisions, insider buying/selling
6. Microstructure — VPIN, entropy, vol regime classification

8 tabs: Dashboard, Momentum, Mean Reversion, Value & Quality,
Earnings & Sentiment, Regime & Microstructure, Factor Correlation,
Composite Ranking (configurable weights).
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from concurrent.futures import ThreadPoolExecutor
import logging
from src.layout import setup_page, error_boundary
from src.styles import COLORS
from src.quant_features import compute_vpin, compute_entropy

logger = logging.getLogger(__name__)
setup_page("39_Signal_Scanner")

st.title("Systematic Signal Scanner")
st.markdown(
    "Institutional-grade cross-sectional factor analysis — momentum, value, "
    "quality, earnings, and microstructure dimensions."
)

PLOTLY_NOBAR = {"displayModeBar": False}

# ═══════════════════════════════════════════════
# UNIVERSE DEFINITIONS
# ═══════════════════════════════════════════════

UNIVERSES = {
    "S&P 500 Sectors": [
        "XLK", "XLF", "XLE", "XLV", "XLI", "XLU", "XLP", "XLY", "XLC", "XLB", "XLRE",
    ],
    "Mega Caps": [
        "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JPM", "V",
        "UNH", "LLY", "XOM", "JNJ", "PG", "MA", "HD", "COST", "ABBV", "MRK",
    ],
    "Growth vs Value": [
        "VUG", "VTV", "IWF", "IWD", "SPYG", "SPYV", "QQQ", "SCHD", "MGK", "RPV",
        "MTUM", "VLUE", "QUAL", "SIZE", "USMV",
    ],
    "Multi-Asset": [
        "SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG",
        "GLD", "SLV", "USO", "UNG", "DBA", "VNQ", "VIXY",
    ],
    "Custom": [],
}


# ═══════════════════════════════════════════════
# CACHED DATA HELPERS
# ═══════════════════════════════════════════════

@st.cache_data(ttl=300, show_spinner=False)
def _download_market_data(tickers_tuple, period):
    """Download close prices and volume for all tickers in one yfinance call."""
    import yfinance as yf

    tickers = list(tickers_tuple)
    data = yf.download(tickers, period=period, progress=False, threads=True)
    if isinstance(data.columns, pd.MultiIndex):
        close = data["Close"]
        volume = data["Volume"]
    else:
        close = data[["Close"]].rename(columns={"Close": tickers[0]})
        volume = data[["Volume"]].rename(columns={"Volume": tickers[0]})
    return close, volume


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_fundamentals(tickers_tuple):
    """Fetch valuation + quality metrics in a single yfinance pass (parallelized)."""
    import yfinance as yf

    def _fetch_one(t):
        try:
            info = yf.Ticker(t).info or {}
            mktcap = info.get("marketCap")
            fcf = info.get("freeCashflow")
            debt = info.get("totalDebt")
            cash = info.get("totalCash")
            ebitda = info.get("ebitda")
            return {
                "ticker": t,
                # ── Valuation ──
                "forward_pe": info.get("forwardPE"),
                "trailing_pe": info.get("trailingPE"),
                "price_to_book": info.get("priceToBook"),
                "ev_ebitda": info.get("enterpriseToEbitda"),
                "dividend_yield": (info.get("dividendYield") or 0) * 100
                    if info.get("dividendYield") else None,
                "fcf_yield": (fcf / mktcap * 100)
                    if fcf and mktcap and mktcap > 0 else None,
                # ── Quality ──
                "roe": (info.get("returnOnEquity") or 0) * 100
                    if info.get("returnOnEquity") else None,
                "profit_margin": (info.get("profitMargins") or 0) * 100
                    if info.get("profitMargins") else None,
                "operating_margin": (info.get("operatingMargins") or 0) * 100
                    if info.get("operatingMargins") else None,
                "gross_margin": (info.get("grossMargins") or 0) * 100
                    if info.get("grossMargins") else None,
                "revenue_growth": (info.get("revenueGrowth") or 0) * 100
                    if info.get("revenueGrowth") else None,
                "earnings_growth": (info.get("earningsGrowth") or 0) * 100
                    if info.get("earningsGrowth") else None,
                # ── Risk / Balance Sheet ──
                "beta": info.get("beta"),
                "net_debt_ebitda": ((debt or 0) - (cash or 0)) / ebitda
                    if debt and ebitda and ebitda > 0 else None,
                "current_ratio": info.get("currentRatio"),
            }
        except Exception as e:
            logger.warning(f"Fundamentals fetch failed for {t}: {e}")
            return None

    tickers = list(tickers_tuple)
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(_fetch_one, tickers))
    rows = [r for r in results if r is not None]
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

c1, c2, c3 = st.columns([2, 1, 1])
with c1:
    universe_name = st.selectbox("Universe", list(UNIVERSES.keys()), key="ss_universe")
    if universe_name == "Custom":
        custom_raw = st.text_input(
            "Custom tickers (comma-separated)",
            "AAPL,MSFT,GOOGL,AMZN,META,NVDA,TSLA,JPM,V,UNH",
            key="ss_custom",
        )
        tickers = [t.strip().upper() for t in custom_raw.split(",") if t.strip()]
    else:
        tickers = UNIVERSES[universe_name]
with c2:
    ss_lookback = st.selectbox("Lookback", ["6M", "1Y", "2Y"], index=1, key="ss_lookback")
    lookback_map = {"6M": "6mo", "1Y": "1y", "2Y": "2y"}
with c3:
    st.markdown("<br>", unsafe_allow_html=True)
    ss_load = st.button("Scan Signals", type="primary", use_container_width=True, key="ss_load")

if ss_load:
    st.session_state["ss_loaded"] = True
if not st.session_state.get("ss_loaded"):
    st.info(f"Select a universe ({len(tickers)} assets) and click **Scan Signals**.")
    st.stop()

if len(tickers) < 3:
    st.error("Need at least 3 tickers.")
    st.stop()


# ═══════════════════════════════════════════════
# DATA LOADING (PARALLEL)
# ═══════════════════════════════════════════════

with st.spinner(f"Scanning {len(tickers)} assets — prices, fundamentals, earnings, insider data..."):
    tickers_tuple = tuple(tickers)

    from src.market_data import fetch_eps_revisions, fetch_insider_summary

    with ThreadPoolExecutor(max_workers=4) as executor:
        price_vol_future = executor.submit(
            _download_market_data, tickers_tuple, lookback_map[ss_lookback]
        )
        fund_future = executor.submit(_fetch_fundamentals, tickers_tuple)
        eps_future = executor.submit(fetch_eps_revisions, tickers)
        insider_future = executor.submit(fetch_insider_summary, tickers)

    prices, volumes = price_vol_future.result()
    fund_df = fund_future.result()
    eps_df = eps_future.result()
    insider_df = insider_future.result()

if prices.empty or len(prices.columns) < 3:
    st.error("Insufficient price data returned.")
    st.stop()

# Clean up
prices = prices.dropna(axis=1, how="all")
volumes = volumes.reindex(columns=prices.columns)
returns = prices.pct_change().dropna()
avail_tickers = sorted(prices.columns.tolist())
n = len(avail_tickers)


# ═══════════════════════════════════════════════
# COMPUTE ALL SIGNALS
# ═══════════════════════════════════════════════

signals = pd.DataFrame(index=avail_tickers)

# ── MOMENTUM ──────────────────────────────────
for period, days in [("1M", 21), ("3M", 63), ("6M", 126), ("12M", 252)]:
    if len(prices) >= days:
        signals[f"Mom_{period}"] = (prices.iloc[-1] / prices.iloc[-days] - 1) * 100

# 12-1 month momentum (skip most recent month — short-term reversal)
if len(prices) >= 252:
    signals["Mom_12-1"] = (prices.iloc[-21] / prices.iloc[-252] - 1) * 100

# Momentum acceleration: recent vs long-term
if "Mom_3M" in signals.columns and "Mom_12M" in signals.columns:
    signals["Mom_Accel"] = signals["Mom_3M"] * 4 - signals["Mom_12M"]

# Momentum consistency: % of timeframes with positive return
mom_period_cols = [c for c in signals.columns if c.startswith("Mom_") and c not in ("Mom_Accel", "Mom_12-1")]
if mom_period_cols:
    for t in avail_tickers:
        vals = [signals.loc[t, c] for c in mom_period_cols if pd.notna(signals.loc[t, c])]
        signals.loc[t, "Mom_Consistency"] = (
            sum(1 for v in vals if v > 0) / len(vals) * 100 if vals else np.nan
        )

# Risk-adjusted momentum (return / realized vol)
for t in avail_tickers:
    vol_half = returns[t].tail(min(126, len(returns))).std() * np.sqrt(252)
    if vol_half > 0 and "Mom_6M" in signals.columns and pd.notna(signals.loc[t, "Mom_6M"]):
        signals.loc[t, "Mom_RiskAdj"] = signals.loc[t, "Mom_6M"] / (vol_half * 100)

# ── MEAN REVERSION ────────────────────────────
for t in avail_tickers:
    # RSI-14
    delta = prices[t].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean().replace(0, np.nan)
    rs = gain / loss
    rsi = (100 - 100 / (1 + rs)).iloc[-1]
    signals.loc[t, "RSI_14"] = rsi if not np.isnan(rsi) else np.nan

    # Bollinger Band position (0–100)
    ma20 = prices[t].rolling(20).mean()
    std20 = prices[t].rolling(20).std()
    if std20.iloc[-1] > 0:
        bb_pos = (
            (prices[t].iloc[-1] - (ma20.iloc[-1] - 2 * std20.iloc[-1]))
            / (4 * std20.iloc[-1]) * 100
        )
        signals.loc[t, "BB_Position"] = np.clip(bb_pos, 0, 100)
    else:
        signals.loc[t, "BB_Position"] = 50

    # Z-score (63D)
    mu_63 = prices[t].rolling(63).mean().iloc[-1]
    std_63 = prices[t].rolling(63).std().iloc[-1]
    signals.loc[t, "Z_Score_63D"] = (
        (prices[t].iloc[-1] - mu_63) / std_63 if std_63 > 0 else np.nan
    )

# Mean reversion confluence: -3 (oversold) to +3 (overbought)
for t in avail_tickers:
    os_count = ob_count = 0
    _rsi = signals.loc[t, "RSI_14"]
    _bb = signals.loc[t, "BB_Position"]
    _z = signals.loc[t, "Z_Score_63D"]
    if pd.notna(_rsi):
        if _rsi < 30: os_count += 1
        elif _rsi > 70: ob_count += 1
    if pd.notna(_bb):
        if _bb < 20: os_count += 1
        elif _bb > 80: ob_count += 1
    if pd.notna(_z):
        if _z < -1.5: os_count += 1
        elif _z > 1.5: ob_count += 1
    signals.loc[t, "MR_Confluence"] = ob_count - os_count

# ── VOLATILITY & DRAWDOWN ─────────────────────
for t in avail_tickers:
    signals.loc[t, "Vol_20D"] = returns[t].tail(20).std() * np.sqrt(252) * 100
    if len(returns) >= 63:
        vol_20 = returns[t].tail(20).std()
        vol_63 = returns[t].tail(63).std()
        signals.loc[t, "Vol_Ratio"] = vol_20 / vol_63 if vol_63 > 0 else 1.0
    dd = ((prices[t] / prices[t].cummax()) - 1).iloc[-1] * 100
    signals.loc[t, "Drawdown"] = dd

# ── VALUE & QUALITY ────────────────────────────
if not fund_df.empty:
    fnd = fund_df.set_index("ticker") if "ticker" in fund_df.columns else fund_df
    val_qual_cols = [
        "forward_pe", "trailing_pe", "price_to_book", "ev_ebitda", "dividend_yield",
        "fcf_yield", "roe", "profit_margin", "operating_margin", "gross_margin",
        "revenue_growth", "earnings_growth", "beta", "net_debt_ebitda", "current_ratio",
    ]
    for col in val_qual_cols:
        if col in fnd.columns:
            for t in avail_tickers:
                if t in fnd.index and pd.notna(fnd.loc[t, col]):
                    signals.loc[t, col] = fnd.loc[t, col]

# ── EARNINGS MOMENTUM ─────────────────────────
if not eps_df.empty:
    eidx = eps_df.set_index("ticker") if "ticker" in eps_df.columns else eps_df
    for t in avail_tickers:
        if t in eidx.index:
            row = eidx.loc[t]
            signals.loc[t, "EPS_Rev_Net30"] = row.get("net_30d", np.nan)
            up = row.get("up_30d", 0)
            down = row.get("down_30d", 0)
            total = up + down
            signals.loc[t, "EPS_Rev_Ratio"] = up / total * 100 if total > 0 else np.nan

# ── INSIDER ACTIVITY ───────────────────────────
if not insider_df.empty:
    iidx = insider_df.set_index("ticker") if "ticker" in insider_df.columns else insider_df
    for t in avail_tickers:
        if t in iidx.index:
            row = iidx.loc[t]
            signals.loc[t, "Insider_Net"] = row.get("net_value", np.nan)
            bc = row.get("buy_count", 0)
            sc = row.get("sell_count", 0)
            total = bc + sc
            signals.loc[t, "Insider_BuySellRatio"] = bc / total * 100 if total > 0 else np.nan

# ── MICROSTRUCTURE (VPIN & ENTROPY) ────────────
for t in avail_tickers:
    try:
        vol_s = volumes[t].dropna()
        ret_s = returns[t].dropna()
        common_idx = vol_s.index.intersection(ret_s.index)
        vol_s = vol_s.loc[common_idx]
        ret_s = ret_s.loc[common_idx]
        if len(ret_s) >= 63:
            vpin = compute_vpin(vol_s, ret_s, window=50)
            signals.loc[t, "VPIN"] = vpin.iloc[-1] if len(vpin) > 0 else np.nan
            ent = compute_entropy(ret_s, n_bins=10, window=63)
            signals.loc[t, "Entropy"] = ent.iloc[-1] if len(ent) > 0 else np.nan
    except Exception:
        pass


# ═══════════════════════════════════════════════
# COMPUTE RANKS (cross-sectional percentile 0-100)
# ═══════════════════════════════════════════════

ranks = pd.DataFrame(index=avail_tickers)

# Momentum: higher return → higher rank
for col in [c for c in signals.columns if c.startswith("Mom_") and c not in ("Mom_Accel", "Mom_Consistency")]:
    ranks[col] = signals[col].rank(pct=True) * 100
if "Mom_RiskAdj" in signals.columns:
    ranks["Mom_RiskAdj"] = signals["Mom_RiskAdj"].rank(pct=True) * 100
if "Mom_Consistency" in signals.columns:
    ranks["Mom_Consistency"] = signals["Mom_Consistency"].rank(pct=True) * 100

# Mean reversion: oversold → higher rank (buy signal)
ranks["RSI_Signal"] = signals["RSI_14"].apply(
    lambda v: 100 if v < 30 else 0 if v > 70 else 50 if pd.notna(v) else np.nan
)
ranks["MeanRev"] = (-signals["Z_Score_63D"]).rank(pct=True) * 100

# Value: lower PE → better, higher yield → better
if "forward_pe" in signals.columns:
    ranks["Value_PE"] = (-signals["forward_pe"]).rank(pct=True) * 100
if "price_to_book" in signals.columns:
    ranks["Value_PB"] = (-signals["price_to_book"]).rank(pct=True) * 100
if "ev_ebitda" in signals.columns:
    ranks["Value_EVEBITDA"] = (-signals["ev_ebitda"]).rank(pct=True) * 100
if "fcf_yield" in signals.columns:
    ranks["Value_FCF"] = signals["fcf_yield"].rank(pct=True) * 100
if "dividend_yield" in signals.columns:
    ranks["Carry"] = signals["dividend_yield"].rank(pct=True) * 100

# Quality: higher → better
if "roe" in signals.columns:
    ranks["Quality_ROE"] = signals["roe"].rank(pct=True) * 100
if "profit_margin" in signals.columns:
    ranks["Quality_Margin"] = signals["profit_margin"].rank(pct=True) * 100
if "revenue_growth" in signals.columns:
    ranks["Growth"] = signals["revenue_growth"].rank(pct=True) * 100

# Earnings momentum
if "EPS_Rev_Ratio" in signals.columns:
    ranks["EPS_Mom"] = signals["EPS_Rev_Ratio"].rank(pct=True) * 100
if "Insider_Net" in signals.columns:
    ranks["Insider"] = signals["Insider_Net"].rank(pct=True) * 100

# Risk: lower vol → higher rank
ranks["LowVol"] = (-signals["Vol_20D"]).rank(pct=True) * 100

# Composite (equal weight)
rank_cols_all = [c for c in ranks.columns]
if rank_cols_all:
    ranks["Composite"] = ranks[rank_cols_all].mean(axis=1, skipna=True)


# ═══════════════════════════════════════════════
# FACTOR GROUPS
# ═══════════════════════════════════════════════

FACTOR_GROUPS = {
    "Momentum": [c for c in ranks.columns if c.startswith("Mom_")],
    "Mean Reversion": [c for c in ranks.columns if c in ("RSI_Signal", "MeanRev")],
    "Value": [c for c in ranks.columns if c.startswith("Value_") or c == "Carry"],
    "Quality": [c for c in ranks.columns if c.startswith("Quality_") or c == "Growth"],
    "Earnings": [c for c in ranks.columns if c in ("EPS_Mom", "Insider")],
    "Risk": [c for c in ranks.columns if c == "LowVol"],
}
FACTOR_GROUPS = {k: v for k, v in FACTOR_GROUPS.items() if v}

# Pre-compute group averages
group_ranks = {}
for gname, gcols in FACTOR_GROUPS.items():
    group_ranks[gname] = ranks[gcols].mean(axis=1, skipna=True)


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

tab_dash, tab_mom, tab_mr, tab_vq, tab_earn, tab_regime, tab_corr, tab_comp = st.tabs([
    "Dashboard", "Momentum", "Mean Reversion", "Value & Quality",
    "Earnings & Sentiment", "Regime", "Factor Correlation", "Composite",
])


# ═══════════════════════════════════════════════
# TAB 1 — DASHBOARD
# ═══════════════════════════════════════════════
with tab_dash, error_boundary("Signal Dashboard"):
    # ── Market regime indicator ──
    avg_rsi = signals["RSI_14"].mean()
    avg_mom = signals.get("Mom_3M", pd.Series(dtype=float)).mean()
    avg_vol = signals["Vol_20D"].mean()
    if pd.notna(avg_rsi) and pd.notna(avg_mom):
        if avg_rsi > 60 and avg_mom > 5:
            regime_label, regime_color = "Bullish — broad momentum", "#00ff88"
        elif avg_rsi < 40 and avg_mom < -5:
            regime_label, regime_color = "Bearish — broad weakness", "#ff4444"
        elif avg_vol > 30:
            regime_label, regime_color = "High Volatility — risk-off", "#ffaa00"
        else:
            regime_label, regime_color = "Neutral — mixed signals", "#888888"
    else:
        regime_label, regime_color = "Insufficient data", "#888888"

    _rsi_s = f"{avg_rsi:.0f}" if pd.notna(avg_rsi) else "N/A"
    _mom_s = f"{avg_mom:+.1f}%" if pd.notna(avg_mom) else "N/A"
    _vol_s = f"{avg_vol:.0f}%" if pd.notna(avg_vol) else "N/A"
    st.markdown(
        f'<div style="background:{COLORS["card_bg"]};border:1px solid {regime_color};'
        f'border-radius:8px;padding:12px 16px;margin-bottom:16px;">'
        f'<span style="color:{regime_color};font-weight:700;font-size:1.1rem;">'
        f'MARKET REGIME: {regime_label}</span>'
        f'<span style="color:{COLORS["text_muted"]};margin-left:16px;">'
        f'Avg RSI: {_rsi_s} · Avg 3M Mom: {_mom_s} · Avg Vol: {_vol_s}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── Factor spread (which factors have most signal power now) ──
    factor_spreads = {}
    for gname, gcols in FACTOR_GROUPS.items():
        gavg = ranks[gcols].mean(axis=1, skipna=True)
        q_size = max(1, n // 5)
        top_avg = gavg.nlargest(q_size).mean()
        bot_avg = gavg.nsmallest(q_size).mean()
        factor_spreads[gname] = top_avg - bot_avg

    fs_cols = st.columns(len(factor_spreads))
    for i, (fname, fspread) in enumerate(factor_spreads.items()):
        color = "#00ff88" if fspread > 40 else "#00d1ff" if fspread > 25 else "#ffaa00"
        fs_cols[i].metric(fname, f"{fspread:.0f}pt spread")

    st.caption("Factor spread = avg top-quintile rank minus avg bottom-quintile rank. Higher spread → stronger signal differentiation.")

    # ── Top & Bottom picks ──
    top_n = min(5, n)
    comp_sorted = ranks["Composite"].sort_values(ascending=False)
    top5 = comp_sorted.head(top_n)
    bot5 = comp_sorted.tail(top_n)

    tc1, tc2 = st.columns(2)
    with tc1:
        st.markdown("**Top Picks**")
        for t in top5.index:
            mom_val = signals.loc[t, "Mom_12-1"] if "Mom_12-1" in signals.columns else signals.get("Mom_6M", pd.Series(dtype=float)).get(t, np.nan)
            rsi_val = signals.loc[t, "RSI_14"]
            mom_s = f"Mom: {mom_val:+.1f}%" if pd.notna(mom_val) else ""
            rsi_s = f"RSI: {rsi_val:.0f}" if pd.notna(rsi_val) else ""
            st.markdown(
                f"- **{t}** — Composite: {top5[t]:.0f} · {mom_s} · {rsi_s}"
            )
    with tc2:
        st.markdown("**Bottom Concerns**")
        for t in bot5.index:
            mom_val = signals.loc[t, "Mom_12-1"] if "Mom_12-1" in signals.columns else signals.get("Mom_6M", pd.Series(dtype=float)).get(t, np.nan)
            dd_val = signals.loc[t, "Drawdown"]
            mom_s = f"Mom: {mom_val:+.1f}%" if pd.notna(mom_val) else ""
            dd_s = f"DD: {dd_val:.1f}%" if pd.notna(dd_val) else ""
            st.markdown(
                f"- **{t}** — Composite: {bot5[t]:.0f} · {mom_s} · {dd_s}"
            )

    # ── Multi-factor heatmap ──
    st.subheader("Multi-Factor Signal Heatmap")

    with st.expander("How to read this", expanded=False):
        st.markdown(
            "Each cell is a **cross-sectional percentile rank** (0–100). "
            "Green = strong signal in that factor's direction. Red = weak.\n\n"
            "- **Mom columns**: higher = stronger price trend\n"
            "- **RSI Signal**: 100 = oversold (buy), 0 = overbought (sell)\n"
            "- **MeanRev**: 100 = most oversold vs 63D mean\n"
            "- **Value/Carry**: 100 = cheapest / highest yield\n"
            "- **Quality/Growth**: 100 = highest ROE / margin / growth\n"
            "- **EPS Mom**: 100 = most upward revisions\n"
            "- **LowVol**: 100 = lowest realized volatility"
        )

    display_cols = [c for c in ranks.columns if c != "Composite"] + ["Composite"]
    display_ranks = ranks[display_cols].sort_values("Composite", ascending=False)

    fig_hm = go.Figure(data=go.Heatmap(
        z=display_ranks.values,
        x=display_cols,
        y=display_ranks.index.tolist(),
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
        zmid=50, zmin=0, zmax=100,
        text=[[f"{v:.0f}" for v in row] for row in display_ranks.values],
        texttemplate="%{text}", textfont={"size": 10},
        hovertemplate="%{y} — %{x}: %{z:.0f}th pctl<extra></extra>",
        colorbar=dict(title="Rank"),
    ))
    fig_hm.update_layout(
        template="plotly_dark", height=max(400, n * 28),
        title=f"Cross-Sectional Ranks — {universe_name} ({n} assets)",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_hm, use_container_width=True, config=PLOTLY_NOBAR)

    with st.expander("Raw Signal Values"):
        raw = signals.copy()
        for col in raw.columns:
            raw[col] = raw[col].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
        st.dataframe(raw, use_container_width=True)


# ═══════════════════════════════════════════════
# TAB 2 — MOMENTUM
# ═══════════════════════════════════════════════
with tab_mom, error_boundary("Momentum"):
    st.subheader("Cross-Sectional Momentum")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Cross-sectional momentum** ranks assets by past returns relative to peers "
            "(Jegadeesh & Titman, 1993).\n\n"
            "**12-1 month** momentum skips the most recent month to avoid short-term "
            "mean reversion. **Risk-adjusted** momentum divides return by volatility.\n\n"
            "**Acceleration** = annualized 3M vs 12M: positive = momentum is increasing. "
            "**Consistency** = % of timeframes with positive return.\n\n"
            "**L/S spread** goes long top quintile, short bottom quintile, rebalanced monthly."
        )

    all_mom_cols = [c for c in signals.columns if c.startswith("Mom_") and c not in ("Mom_Accel", "Mom_Consistency", "Mom_RiskAdj")]
    if all_mom_cols:
        mom_data = signals[all_mom_cols].sort_values(
            all_mom_cols[-1] if all_mom_cols else all_mom_cols[0], ascending=False,
        )
        fig_mom = go.Figure(data=go.Heatmap(
            z=mom_data.values,
            x=[c.replace("Mom_", "") for c in all_mom_cols],
            y=mom_data.index.tolist(),
            colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
            zmid=0,
            text=[[f"{v:+.1f}%" if pd.notna(v) else "—" for v in row] for row in mom_data.values],
            texttemplate="%{text}", textfont={"size": 11},
            colorbar=dict(title="Return %"),
        ))
        fig_mom.update_layout(
            template="plotly_dark", height=max(350, n * 25),
            title="Momentum Returns by Timeframe",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_mom, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Momentum bar chart ──
    mom_col = "Mom_12-1" if "Mom_12-1" in signals.columns else (
        "Mom_6M" if "Mom_6M" in signals.columns else all_mom_cols[-1] if all_mom_cols else None
    )
    if mom_col:
        sorted_mom = signals[mom_col].dropna().sort_values(ascending=True)
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            y=sorted_mom.index, x=sorted_mom.values, orientation="h",
            marker_color=["#00d1ff" if v >= 0 else "#ff4444" for v in sorted_mom],
            text=[f"{v:+.1f}%" for v in sorted_mom], textposition="outside",
        ))
        fig_bar.add_vline(x=0, line_dash="dash", line_color="#555")
        fig_bar.update_layout(
            template="plotly_dark", height=max(350, n * 25),
            title=f"{mom_col} Momentum Ranking",
            xaxis_title="Return (%)",
            margin=dict(l=0, r=80, t=40, b=0),
        )
        st.plotly_chart(fig_bar, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Momentum quality metrics ──
    mq_cols = []
    if "Mom_Accel" in signals.columns:
        mq_cols.append(("Acceleration", "Mom_Accel", "+.1f"))
    if "Mom_RiskAdj" in signals.columns:
        mq_cols.append(("Risk-Adj Mom", "Mom_RiskAdj", ".2f"))
    if "Mom_Consistency" in signals.columns:
        mq_cols.append(("Consistency (%)", "Mom_Consistency", ".0f"))

    if mq_cols:
        st.subheader("Momentum Quality")
        mqc = st.columns(len(mq_cols))
        for i, (label, col, fmt) in enumerate(mq_cols):
            sorted_s = signals[col].dropna().sort_values(ascending=True)
            fig_mq = go.Figure()
            fig_mq.add_trace(go.Bar(
                y=sorted_s.index, x=sorted_s.values, orientation="h",
                marker_color=[
                    "#00d1ff" if v > 0 else "#ff4444" for v in sorted_s
                ] if col != "Mom_Consistency" else [
                    "#00ff88" if v >= 75 else "#00d1ff" if v >= 50 else "#ffaa00" if v >= 25 else "#ff4444"
                    for v in sorted_s
                ],
                text=[f"{v:{fmt}}" for v in sorted_s], textposition="outside",
            ))
            if col != "Mom_Consistency":
                fig_mq.add_vline(x=0, line_dash="dash", line_color="#555")
            fig_mq.update_layout(
                template="plotly_dark", height=max(250, n * 20),
                title=label, margin=dict(l=0, r=50, t=40, b=0),
            )
            mqc[i].plotly_chart(fig_mq, use_container_width=True, config=PLOTLY_NOBAR)

    # ── L/S Spread Backtest ──
    if len(avail_tickers) >= 5 and len(returns) >= 252:
        st.subheader("Momentum Long/Short Spread")
        st.caption(
            "Cumulative return: long top 20% momentum, short bottom 20%, rebalanced monthly. "
            "Rising line = momentum factor is working."
        )
        quintile_size = max(1, n // 5)
        rebal_dates = returns.resample("ME").last().index
        ls_returns = []
        for i in range(len(rebal_dates)):
            rd = rebal_dates[i]
            rd_loc = returns.index.get_loc(rd)
            if rd_loc < 252:
                continue
            trail_ret = prices.iloc[rd_loc - 21] / prices.iloc[rd_loc - 252] - 1
            ranked = trail_ret.sort_values(ascending=False)
            longs = ranked.head(quintile_size).index.tolist()
            shorts = ranked.tail(quintile_size).index.tolist()
            end_rd = rebal_dates[i + 1] if i < len(rebal_dates) - 1 else returns.index[-1]
            period_ret = returns.loc[rd:end_rd]
            for dt, row in period_ret.iterrows():
                long_r = row[longs].mean() if longs else 0
                short_r = row[shorts].mean() if shorts else 0
                ls_returns.append({"date": dt, "return": long_r - short_r})

        if ls_returns:
            ls_df = pd.DataFrame(ls_returns).set_index("date")
            ls_df = ls_df[~ls_df.index.duplicated(keep="first")]
            ls_cum = (1 + ls_df["return"]).cumprod() * 100

            fig_ls = go.Figure()
            fig_ls.add_trace(go.Scatter(
                x=ls_cum.index, y=ls_cum, mode="lines",
                line=dict(color="#00d1ff", width=2), name="Momentum L/S",
            ))
            fig_ls.add_hline(y=100, line_dash="dash", line_color="#333")
            fig_ls.update_layout(
                template="plotly_dark", height=300,
                title="Momentum L/S Cumulative Return (indexed to 100)",
                yaxis_title="Indexed",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_ls, use_container_width=True, config=PLOTLY_NOBAR)

            ann_ret = ls_df["return"].mean() * 252
            ann_vol = ls_df["return"].std() * np.sqrt(252)
            sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
            lm1, lm2, lm3 = st.columns(3)
            lm1.metric("Ann. Return", f"{ann_ret * 100:.1f}%")
            lm2.metric("Ann. Vol", f"{ann_vol * 100:.1f}%")
            lm3.metric("Sharpe", f"{sharpe:.2f}")


# ═══════════════════════════════════════════════
# TAB 3 — MEAN REVERSION
# ═══════════════════════════════════════════════
with tab_mr, error_boundary("Mean Reversion"):
    st.subheader("Mean Reversion Signals")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**RSI < 30** = oversold (potential buy). **RSI > 70** = overbought (potential sell).\n\n"
            "**Bollinger position** 0–100: near 0 = lower band (oversold), near 100 = upper band.\n\n"
            "**Z-Score** = standard deviations from 63-day mean. |Z| > 2 is extreme.\n\n"
            "**Confluence** counts how many signals agree: −3 = all three say oversold, "
            "+3 = all three say overbought. Strongest setups have |confluence| ≥ 2."
        )

    mr_c1, mr_c2, mr_c3 = st.columns(3)

    with mr_c1:
        rsi_sorted = signals["RSI_14"].sort_values()
        fig_rsi = go.Figure()
        fig_rsi.add_trace(go.Bar(
            y=rsi_sorted.index, x=rsi_sorted.values, orientation="h",
            marker_color=[
                "#00ff88" if v < 30 else "#ff4444" if v > 70 else "#555"
                for v in rsi_sorted
            ],
            text=[f"{v:.0f}" for v in rsi_sorted], textposition="outside",
        ))
        fig_rsi.add_vline(x=30, line_dash="dash", line_color="#00ff88", annotation_text="Oversold")
        fig_rsi.add_vline(x=70, line_dash="dash", line_color="#ff4444", annotation_text="Overbought")
        fig_rsi.update_layout(
            template="plotly_dark", height=max(300, n * 22),
            title="RSI-14", xaxis=dict(range=[0, 100]),
            margin=dict(l=0, r=40, t=40, b=0),
        )
        st.plotly_chart(fig_rsi, use_container_width=True, config=PLOTLY_NOBAR)

    with mr_c2:
        bb_sorted = signals["BB_Position"].sort_values()
        fig_bb = go.Figure()
        fig_bb.add_trace(go.Bar(
            y=bb_sorted.index, x=bb_sorted.values, orientation="h",
            marker_color=[
                "#00ff88" if v < 20 else "#ff4444" if v > 80 else "#555"
                for v in bb_sorted
            ],
            text=[f"{v:.0f}" for v in bb_sorted], textposition="outside",
        ))
        fig_bb.add_vline(x=20, line_dash="dash", line_color="#00ff88")
        fig_bb.add_vline(x=80, line_dash="dash", line_color="#ff4444")
        fig_bb.update_layout(
            template="plotly_dark", height=max(300, n * 22),
            title="Bollinger Position (0–100)", xaxis=dict(range=[0, 100]),
            margin=dict(l=0, r=40, t=40, b=0),
        )
        st.plotly_chart(fig_bb, use_container_width=True, config=PLOTLY_NOBAR)

    with mr_c3:
        z_sorted = signals["Z_Score_63D"].sort_values()
        fig_z = go.Figure()
        fig_z.add_trace(go.Bar(
            y=z_sorted.index, x=z_sorted.values, orientation="h",
            marker_color=[
                "#00ff88" if v < -2 else "#ff4444" if v > 2
                else "#00d1ff" if v < 0 else "#ffaa00"
                for v in z_sorted
            ],
            text=[f"{v:+.1f}" for v in z_sorted], textposition="outside",
        ))
        fig_z.add_vline(x=-2, line_dash="dash", line_color="#00ff88", annotation_text="-2σ")
        fig_z.add_vline(x=2, line_dash="dash", line_color="#ff4444", annotation_text="+2σ")
        fig_z.add_vline(x=0, line_dash="dash", line_color="#333")
        fig_z.update_layout(
            template="plotly_dark", height=max(300, n * 22),
            title="Price Z-Score (63D)",
            margin=dict(l=0, r=40, t=40, b=0),
        )
        st.plotly_chart(fig_z, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Confluence & Alerts ──
    st.subheader("Signal Confluence & Alerts")

    conf_sorted = signals["MR_Confluence"].sort_values()
    fig_conf = go.Figure()
    fig_conf.add_trace(go.Bar(
        y=conf_sorted.index, x=conf_sorted.values, orientation="h",
        marker_color=[
            "#00ff88" if v <= -2 else "#88ffbb" if v == -1
            else "#ff4444" if v >= 2 else "#ffaa88" if v == 1 else "#555"
            for v in conf_sorted
        ],
        text=[f"{int(v):+d}" for v in conf_sorted], textposition="outside",
    ))
    fig_conf.update_layout(
        template="plotly_dark", height=max(250, n * 20),
        title="Mean Reversion Confluence (−3 = all oversold, +3 = all overbought)",
        xaxis=dict(range=[-3.5, 3.5]),
        margin=dict(l=0, r=40, t=40, b=0),
    )
    st.plotly_chart(fig_conf, use_container_width=True, config=PLOTLY_NOBAR)

    al1, al2 = st.columns(2)
    oversold = signals[(signals["RSI_14"].fillna(50) < 30) | (signals["Z_Score_63D"].fillna(0) < -2)]
    overbought = signals[(signals["RSI_14"].fillna(50) > 70) | (signals["Z_Score_63D"].fillna(0) > 2)]
    with al1:
        st.markdown("**Oversold (potential longs)**")
        if not oversold.empty:
            for t in oversold.index:
                rsi = signals.loc[t, "RSI_14"]
                z = signals.loc[t, "Z_Score_63D"]
                dd = signals.loc[t, "Drawdown"]
                parts = []
                if pd.notna(rsi): parts.append(f"RSI={rsi:.0f}")
                if pd.notna(z): parts.append(f"Z={z:+.1f}")
                if pd.notna(dd): parts.append(f"DD={dd:.1f}%")
                st.markdown(f"- **{t}**: {', '.join(parts)}")
        else:
            st.caption("No oversold assets detected.")
    with al2:
        st.markdown("**Overbought (potential shorts/trims)**")
        if not overbought.empty:
            for t in overbought.index:
                rsi = signals.loc[t, "RSI_14"]
                z = signals.loc[t, "Z_Score_63D"]
                parts = []
                if pd.notna(rsi): parts.append(f"RSI={rsi:.0f}")
                if pd.notna(z): parts.append(f"Z={z:+.1f}")
                st.markdown(f"- **{t}**: {', '.join(parts)}")
        else:
            st.caption("No overbought assets detected.")


# ═══════════════════════════════════════════════
# TAB 4 — VALUE & QUALITY
# ═══════════════════════════════════════════════
with tab_vq, error_boundary("Value & Quality"):
    st.subheader("Value & Quality Factor Analysis")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Value** signals identify cheap assets (low multiples, high yields). "
            "**Quality** signals identify strong businesses (high ROE, margins, growth).\n\n"
            "The quadrant scatter shows the intersection: **top-right** = high quality + cheap "
            "(best), **bottom-left** = low quality + expensive (avoid). "
            "**Top-left** = expensive quality. **Bottom-right** = value traps.\n\n"
            "Value signals work best over long horizons (1–3 years). "
            "Quality signals tend to be persistent and mean-reverting to strength."
        )

    val_cols = ["forward_pe", "price_to_book", "ev_ebitda", "fcf_yield", "dividend_yield"]
    qual_cols = ["roe", "profit_margin", "operating_margin", "gross_margin",
                 "revenue_growth", "earnings_growth"]

    has_val = any(c in signals.columns for c in val_cols)
    has_qual = any(c in signals.columns for c in qual_cols)

    if not has_val and not has_qual:
        st.warning("No fundamental data available for this universe (ETFs typically lack these metrics). "
                   "Try the **Mega Caps** or **Custom** universe with individual stocks.")
    else:
        # ── Valuation metrics ──
        if has_val:
            st.markdown("**Valuation Metrics**")
            avail_val = [c for c in val_cols if c in signals.columns]
            val_data = signals[avail_val].copy()
            val_labels = {
                "forward_pe": "Fwd P/E", "price_to_book": "P/B",
                "ev_ebitda": "EV/EBITDA", "fcf_yield": "FCF Yield %",
                "dividend_yield": "Div Yield %",
            }
            val_data.columns = [val_labels.get(c, c) for c in val_data.columns]

            # Invert rank for "lower = cheaper" metrics so green always = cheap
            val_rank = val_data.rank(pct=True)
            invert_set = {"Fwd P/E", "P/B", "EV/EBITDA"}
            for vc in val_rank.columns:
                if vc in invert_set:
                    val_rank[vc] = 1 - val_rank[vc]

            fig_val = go.Figure(data=go.Heatmap(
                z=val_rank.values * 100,
                x=val_data.columns.tolist(),
                y=val_data.index.tolist(),
                colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
                zmid=50, zmin=0, zmax=100,
                text=[[f"{v:.1f}" if pd.notna(v) else "—" for v in row] for row in val_data.values],
                texttemplate="%{text}", textfont={"size": 10},
                hovertemplate="%{y} — %{x}: %{text}<extra></extra>",
                colorbar=dict(title="Rank"),
            ))
            fig_val.update_layout(
                template="plotly_dark", height=max(300, n * 22),
                title="Valuation (raw values shown, color = percentile rank — green = cheapest)",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_val, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Quality metrics ──
        if has_qual:
            st.markdown("**Quality Metrics**")
            avail_qual = [c for c in qual_cols if c in signals.columns]
            qual_data = signals[avail_qual].copy()
            qual_labels = {
                "roe": "ROE %", "profit_margin": "Profit Margin %",
                "operating_margin": "Op Margin %", "gross_margin": "Gross Margin %",
                "revenue_growth": "Rev Growth %", "earnings_growth": "Earn Growth %",
            }
            qual_data.columns = [qual_labels.get(c, c) for c in qual_data.columns]

            fig_qual = go.Figure(data=go.Heatmap(
                z=qual_data.rank(pct=True).values * 100,
                x=qual_data.columns.tolist(),
                y=qual_data.index.tolist(),
                colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
                zmid=50, zmin=0, zmax=100,
                text=[[f"{v:.1f}" if pd.notna(v) else "—" for v in row] for row in qual_data.values],
                texttemplate="%{text}", textfont={"size": 10},
                colorbar=dict(title="Rank"),
            ))
            fig_qual.update_layout(
                template="plotly_dark", height=max(300, n * 22),
                title="Quality (raw values shown, color = percentile rank — green = highest quality)",
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_qual, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Quadrant scatter: Value rank vs Quality rank ──
        if "Value" in group_ranks and "Quality" in group_ranks:
            st.markdown("**Value vs Quality Quadrant**")
            val_r = group_ranks["Value"]
            qual_r = group_ranks["Quality"]

            fig_scatter = go.Figure()
            fig_scatter.add_trace(go.Scatter(
                x=val_r, y=qual_r, mode="markers+text",
                text=val_r.index.tolist(), textposition="top center",
                textfont=dict(size=10, color=COLORS["text_primary"]),
                marker=dict(
                    size=12,
                    color=ranks["Composite"].loc[val_r.index],
                    colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
                    colorbar=dict(title="Composite"),
                    line=dict(width=1, color="#555"),
                ),
            ))
            fig_scatter.add_hline(y=50, line_dash="dash", line_color="#333")
            fig_scatter.add_vline(x=50, line_dash="dash", line_color="#333")
            fig_scatter.add_annotation(x=85, y=85, text="Best: Cheap + Quality",
                                       showarrow=False, font=dict(color="#00ff88", size=10))
            fig_scatter.add_annotation(x=15, y=15, text="Avoid: Expensive + Weak",
                                       showarrow=False, font=dict(color="#ff4444", size=10))
            fig_scatter.add_annotation(x=15, y=85, text="Expensive Quality",
                                       showarrow=False, font=dict(color="#ffaa00", size=10))
            fig_scatter.add_annotation(x=85, y=15, text="Value Traps?",
                                       showarrow=False, font=dict(color="#ffaa00", size=10))
            fig_scatter.update_layout(
                template="plotly_dark", height=450,
                title="Value vs Quality (percentile ranks, color = composite)",
                xaxis_title="Value Rank (100 = cheapest)",
                yaxis_title="Quality Rank (100 = highest quality)",
                xaxis=dict(range=[-5, 105]),
                yaxis=dict(range=[-5, 105]),
                margin=dict(l=0, r=0, t=40, b=0),
            )
            st.plotly_chart(fig_scatter, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Balance sheet risk ──
        bs_cols = ["net_debt_ebitda", "current_ratio", "beta"]
        avail_bs = [c for c in bs_cols if c in signals.columns and signals[c].notna().sum() >= 3]
        if avail_bs:
            st.markdown("**Balance Sheet & Risk**")
            bs_data = signals[avail_bs].copy()
            bs_labels = {
                "net_debt_ebitda": "Net Debt / EBITDA",
                "current_ratio": "Current Ratio",
                "beta": "Beta",
            }
            bs_data.columns = [bs_labels.get(c, c) for c in bs_data.columns]
            for col in bs_data.columns:
                bs_data[col] = bs_data[col].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "—")
            st.dataframe(bs_data, use_container_width=True)


# ═══════════════════════════════════════════════
# TAB 5 — EARNINGS & SENTIMENT
# ═══════════════════════════════════════════════
with tab_earn, error_boundary("Earnings & Sentiment"):
    st.subheader("Earnings Momentum & Insider Activity")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**EPS revisions** track how analyst estimates are changing. "
            "Net positive revisions (more upgrades than downgrades) are strongly predictive "
            "of future returns (Glushkov, 2009).\n\n"
            "**Insider buying** — corporate insiders (C-suite, directors) buying their own stock "
            "is one of the strongest contrarian signals. Net buying in the last 90 days "
            "is tracked.\n\n"
            "Both signals work best as **confirmation** — an asset with positive EPS revisions "
            "AND insider buying AND strong momentum is a high-conviction pick."
        )

    has_eps = "EPS_Rev_Net30" in signals.columns and signals["EPS_Rev_Net30"].notna().sum() >= 2
    has_insider = "Insider_Net" in signals.columns and signals["Insider_Net"].notna().sum() >= 2

    if not has_eps and not has_insider:
        st.warning(
            "No earnings or insider data available for this universe. "
            "Try **Mega Caps** or **Custom** with individual stocks."
        )
    else:
        ec1, ec2 = st.columns(2)

        # ── EPS Revisions ──
        if has_eps:
            with ec1:
                st.markdown("**EPS Revisions (30D Net)**")
                eps_net = signals["EPS_Rev_Net30"].dropna().sort_values(ascending=True)
                fig_eps = go.Figure()
                fig_eps.add_trace(go.Bar(
                    y=eps_net.index, x=eps_net.values, orientation="h",
                    marker_color=[
                        "#00ff88" if v > 0 else "#ff4444" if v < 0 else "#555"
                        for v in eps_net
                    ],
                    text=[f"{int(v):+d}" for v in eps_net], textposition="outside",
                ))
                fig_eps.add_vline(x=0, line_dash="dash", line_color="#555")
                fig_eps.update_layout(
                    template="plotly_dark",
                    height=max(250, len(eps_net) * 22),
                    title="Net EPS Revisions (up − down, 30D)",
                    xaxis_title="Net Revisions",
                    margin=dict(l=0, r=50, t=40, b=0),
                )
                st.plotly_chart(fig_eps, use_container_width=True, config=PLOTLY_NOBAR)

            if "EPS_Rev_Ratio" in signals.columns:
                with ec1:
                    st.caption("EPS Revision Ratio (% of revisions that are upgrades):")
                    ratio_data = signals["EPS_Rev_Ratio"].dropna().sort_values(ascending=False)
                    for t in ratio_data.index:
                        v = ratio_data[t]
                        st.markdown(f"  **{t}**: {v:.0f}% upgrades")

        # ── Insider Activity ──
        if has_insider:
            with ec2:
                st.markdown("**Insider Net Buying (90D)**")
                ins_net = signals["Insider_Net"].dropna().sort_values(ascending=True)
                fig_ins = go.Figure()
                fig_ins.add_trace(go.Bar(
                    y=ins_net.index, x=ins_net.values, orientation="h",
                    marker_color=[
                        "#00ff88" if v > 0 else "#ff4444" if v < 0 else "#555"
                        for v in ins_net
                    ],
                    text=[
                        f"${v / 1e6:+.1f}M" if abs(v) >= 1e6 else f"${v / 1e3:+.0f}K"
                        for v in ins_net
                    ],
                    textposition="outside",
                ))
                fig_ins.add_vline(x=0, line_dash="dash", line_color="#555")
                fig_ins.update_layout(
                    template="plotly_dark",
                    height=max(250, len(ins_net) * 22),
                    title="Net Insider Activity (buy − sell value, 90D)",
                    xaxis_title="Net Value ($)",
                    margin=dict(l=0, r=80, t=40, b=0),
                )
                st.plotly_chart(fig_ins, use_container_width=True, config=PLOTLY_NOBAR)

            if "Insider_BuySellRatio" in signals.columns:
                with ec2:
                    st.caption("Insider Buy/Sell Ratio (% of transactions that are buys):")
                    bsr = signals["Insider_BuySellRatio"].dropna().sort_values(ascending=False)
                    for t in bsr.index:
                        v = bsr[t]
                        st.markdown(f"  **{t}**: {v:.0f}% buys")

        # ── Combined conviction ──
        if has_eps and has_insider:
            st.subheader("High-Conviction Signals")
            st.caption(
                "Assets with BOTH positive EPS revisions AND net insider buying — "
                "the strongest fundamental confirmation."
            )
            conviction = []
            for t in avail_tickers:
                eps_ok = pd.notna(signals.loc[t].get("EPS_Rev_Net30")) and signals.loc[t]["EPS_Rev_Net30"] > 0
                ins_ok = pd.notna(signals.loc[t].get("Insider_Net")) and signals.loc[t]["Insider_Net"] > 0
                if eps_ok and ins_ok:
                    conviction.append(t)
            if conviction:
                for t in conviction:
                    eps_v = signals.loc[t, "EPS_Rev_Net30"]
                    ins_v = signals.loc[t, "Insider_Net"]
                    ins_s = f"${ins_v / 1e6:.1f}M" if abs(ins_v) >= 1e6 else f"${ins_v / 1e3:.0f}K"
                    st.markdown(f"- **{t}**: EPS revisions {eps_v:+.0f}, insider net {ins_s}")
            else:
                st.caption("No assets currently have both positive EPS revisions and insider buying.")


# ═══════════════════════════════════════════════
# TAB 6 — REGIME & MICROSTRUCTURE
# ═══════════════════════════════════════════════
with tab_regime, error_boundary("Regime & Microstructure"):
    st.subheader("Regime & Microstructure Signals")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**VPIN** (Volume-Synchronized Probability of Informed Trading) measures market "
            "toxicity — the likelihood that informed traders are driving volume. "
            "VPIN > 0.6 warns of potential adverse selection (Easley, López de Prado, O'Hara, 2012).\n\n"
            "**Entropy** measures market orderliness (0–1). High entropy = random/unpredictable. "
            "Low entropy = orderly/trending. Entropy > 0.85 suggests noise-dominated markets.\n\n"
            "**Vol Ratio** (20D/63D vol) detects vol regime shifts. "
            "Ratio > 1.3 = volatility expanding (risk-off). Ratio < 0.7 = volatility compressing (breakout setup).\n\n"
            "**Regime** combines these into actionable states: favorable, caution, or avoid."
        )

    has_vpin = "VPIN" in signals.columns and signals["VPIN"].notna().sum() >= 2
    has_entropy = "Entropy" in signals.columns and signals["Entropy"].notna().sum() >= 2
    has_vol_ratio = "Vol_Ratio" in signals.columns

    rc1, rc2 = st.columns(2)

    # ── Vol Regime ──
    if has_vol_ratio:
        with rc1:
            vr_sorted = signals["Vol_Ratio"].dropna().sort_values(ascending=True)
            fig_vr = go.Figure()
            fig_vr.add_trace(go.Bar(
                y=vr_sorted.index, x=vr_sorted.values, orientation="h",
                marker_color=[
                    "#ff4444" if v > 1.3 else "#ffaa00" if v > 1.1
                    else "#00ff88" if v < 0.7 else "#00d1ff"
                    for v in vr_sorted
                ],
                text=[f"{v:.2f}" for v in vr_sorted], textposition="outside",
            ))
            fig_vr.add_vline(x=1.0, line_dash="dash", line_color="#555", annotation_text="Neutral")
            fig_vr.add_vline(x=1.3, line_dash="dash", line_color="#ff4444", annotation_text="Expanding")
            fig_vr.add_vline(x=0.7, line_dash="dash", line_color="#00ff88", annotation_text="Compressing")
            fig_vr.update_layout(
                template="plotly_dark",
                height=max(300, len(vr_sorted) * 22),
                title="Volatility Regime (20D / 63D Vol Ratio)",
                xaxis_title="Vol Ratio",
                margin=dict(l=0, r=60, t=40, b=0),
            )
            st.plotly_chart(fig_vr, use_container_width=True, config=PLOTLY_NOBAR)

    # ── VPIN ──
    if has_vpin:
        with rc2:
            vpin_sorted = signals["VPIN"].dropna().sort_values(ascending=True)
            fig_vpin = go.Figure()
            fig_vpin.add_trace(go.Bar(
                y=vpin_sorted.index, x=vpin_sorted.values, orientation="h",
                marker_color=[
                    "#ff4444" if v > 0.6 else "#ffaa00" if v > 0.45 else "#00d1ff"
                    for v in vpin_sorted
                ],
                text=[f"{v:.2f}" for v in vpin_sorted], textposition="outside",
            ))
            fig_vpin.add_vline(x=0.6, line_dash="dash", line_color="#ff4444",
                               annotation_text="High Toxicity")
            fig_vpin.update_layout(
                template="plotly_dark",
                height=max(300, len(vpin_sorted) * 22),
                title="VPIN — Informed Trading Probability",
                xaxis_title="VPIN (0–1)", xaxis=dict(range=[0, 1]),
                margin=dict(l=0, r=60, t=40, b=0),
            )
            st.plotly_chart(fig_vpin, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Entropy ──
    if has_entropy:
        ent_sorted = signals["Entropy"].dropna().sort_values(ascending=True)
        fig_ent = go.Figure()
        fig_ent.add_trace(go.Bar(
            y=ent_sorted.index, x=ent_sorted.values, orientation="h",
            marker_color=[
                "#ff4444" if v > 0.85 else "#ffaa00" if v > 0.7 else "#00d1ff"
                for v in ent_sorted
            ],
            text=[f"{v:.2f}" for v in ent_sorted], textposition="outside",
        ))
        fig_ent.add_vline(x=0.85, line_dash="dash", line_color="#ff4444",
                           annotation_text="Noise-Dominated")
        fig_ent.update_layout(
            template="plotly_dark",
            height=max(300, len(ent_sorted) * 22),
            title="Entropy — Market Orderliness (lower = more predictable)",
            xaxis_title="Entropy (0–1)", xaxis=dict(range=[0, 1]),
            margin=dict(l=0, r=60, t=40, b=0),
        )
        st.plotly_chart(fig_ent, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Regime classification ──
    st.subheader("Regime Classification")
    regime_data = []
    for t in avail_tickers:
        vpin_v = signals.loc[t].get("VPIN", np.nan)
        ent_v = signals.loc[t].get("Entropy", np.nan)
        vr_v = signals.loc[t].get("Vol_Ratio", np.nan)

        warnings = 0
        if pd.notna(vpin_v) and vpin_v > 0.6: warnings += 1
        if pd.notna(ent_v) and ent_v > 0.85: warnings += 1
        if pd.notna(vr_v) and vr_v > 1.3: warnings += 1

        if warnings >= 2:
            regime = "AVOID"
        elif warnings == 1:
            regime = "CAUTION"
        else:
            regime = "FAVORABLE"

        regime_data.append({
            "Ticker": t,
            "VPIN": f"{vpin_v:.2f}" if pd.notna(vpin_v) else "—",
            "Entropy": f"{ent_v:.2f}" if pd.notna(ent_v) else "—",
            "Vol Ratio": f"{vr_v:.2f}" if pd.notna(vr_v) else "—",
            "Vol (20D)": f"{signals.loc[t, 'Vol_20D']:.0f}%" if pd.notna(signals.loc[t, "Vol_20D"]) else "—",
            "Regime": regime,
        })

    regime_df = pd.DataFrame(regime_data)
    st.dataframe(
        regime_df.style.apply(
            lambda row: [
                "background-color: rgba(255,68,68,0.2)" if row["Regime"] == "AVOID"
                else "background-color: rgba(255,170,0,0.2)" if row["Regime"] == "CAUTION"
                else ""
            ] * len(row),
            axis=1,
        ),
        use_container_width=True,
        hide_index=True,
    )


# ═══════════════════════════════════════════════
# TAB 7 — FACTOR CORRELATION
# ═══════════════════════════════════════════════
with tab_corr, error_boundary("Factor Correlation"):
    st.subheader("Factor Correlation & Redundancy")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "**Factor correlation** reveals which signals move together and which are independent. "
            "Highly correlated factors (> 0.7) are redundant — they measure the same thing and "
            "should not both be double-counted in a composite.\n\n"
            "**Negative correlation** (< −0.3) means two factors conflict — e.g., momentum and "
            "mean reversion naturally oppose each other.\n\n"
            "**Effective number of factors** uses eigenvalue analysis to estimate how many truly "
            "independent signals your factor set contains. If you have 10 factors but only 4 "
            "effective factors, 6 are redundant."
        )

    rank_for_corr = ranks.drop(columns=["Composite"], errors="ignore").dropna(axis=1, how="all")

    if len(rank_for_corr.columns) >= 3:
        corr_matrix = rank_for_corr.corr(method="spearman")

        fig_fc = go.Figure(data=go.Heatmap(
            z=corr_matrix.values,
            x=corr_matrix.columns.tolist(),
            y=corr_matrix.index.tolist(),
            colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00d1ff"]],
            zmid=0, zmin=-1, zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in corr_matrix.values],
            texttemplate="%{text}", textfont={"size": 9},
            colorbar=dict(title="Spearman ρ"),
        ))
        fig_fc.update_layout(
            template="plotly_dark",
            height=max(400, len(corr_matrix) * 30),
            title="Factor Rank Correlation (Spearman)",
            margin=dict(l=0, r=0, t=40, b=0),
        )
        st.plotly_chart(fig_fc, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Redundancy & conflict alerts ──
        redundant_pairs = []
        conflict_pairs = []
        cols = corr_matrix.columns.tolist()
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                r = corr_matrix.iloc[i, j]
                if abs(r) > 0.7:
                    redundant_pairs.append((cols[i], cols[j], r))
                elif r < -0.3:
                    conflict_pairs.append((cols[i], cols[j], r))

        rc1, rc2 = st.columns(2)
        with rc1:
            st.markdown("**Redundant Pairs (|ρ| > 0.7)**")
            if redundant_pairs:
                for a, b, r in sorted(redundant_pairs, key=lambda x: -abs(x[2])):
                    st.markdown(f"- **{a}** ↔ **{b}**: ρ = {r:.2f}")
                st.caption("Consider removing one from each pair to avoid double-counting.")
            else:
                st.caption("No redundant factor pairs detected.")
        with rc2:
            st.markdown("**Conflicting Pairs (ρ < −0.3)**")
            if conflict_pairs:
                for a, b, r in sorted(conflict_pairs, key=lambda x: x[2]):
                    st.markdown(f"- **{a}** ↔ **{b}**: ρ = {r:.2f}")
                st.caption("Conflicting factors reduce composite score stability — weight carefully.")
            else:
                st.caption("No strongly conflicting factor pairs detected.")

        # ── Effective number of factors ──
        try:
            clean_corr = corr_matrix.dropna(axis=0, how="any").dropna(axis=1, how="any")
            if len(clean_corr) >= 3:
                eigenvalues = np.linalg.eigvalsh(clean_corr.values)
                eigenvalues = np.maximum(eigenvalues[::-1], 0)
                total = eigenvalues.sum()
                if total > 0:
                    eff_n = (total ** 2) / (eigenvalues ** 2).sum()
                    explained = np.cumsum(eigenvalues) / total * 100

                    ef1, ef2 = st.columns(2)
                    ef1.metric(
                        "Effective Factors",
                        f"{eff_n:.1f} / {len(clean_corr)}",
                        help="Participation ratio: how many truly independent signals exist",
                    )
                    # How many PCs explain 90%?
                    pcs_90 = np.searchsorted(explained, 90) + 1
                    ef2.metric(
                        "PCs for 90% Variance",
                        f"{pcs_90} / {len(clean_corr)}",
                        help="Number of principal components needed to explain 90% of rank variance",
                    )

                    fig_eig = go.Figure()
                    fig_eig.add_trace(go.Bar(
                        x=[f"PC{i + 1}" for i in range(len(eigenvalues))],
                        y=eigenvalues / total * 100,
                        marker_color="#00d1ff",
                        name="% Variance",
                    ))
                    fig_eig.add_trace(go.Scatter(
                        x=[f"PC{i + 1}" for i in range(len(explained))],
                        y=explained, mode="lines+markers",
                        line=dict(color="#00ff88", width=2),
                        name="Cumulative %",
                    ))
                    fig_eig.add_hline(y=90, line_dash="dash", line_color="#ffaa00",
                                      annotation_text="90%")
                    fig_eig.update_layout(
                        template="plotly_dark", height=300,
                        title="Factor Eigenvalue Decomposition",
                        yaxis_title="% of Variance Explained",
                        margin=dict(l=0, r=0, t=40, b=0),
                    )
                    st.plotly_chart(fig_eig, use_container_width=True, config=PLOTLY_NOBAR)
        except Exception:
            pass
    else:
        st.info("Need at least 3 factor ranks to compute correlations.")


# ═══════════════════════════════════════════════
# TAB 8 — COMPOSITE RANKING
# ═══════════════════════════════════════════════
with tab_comp, error_boundary("Composite Ranking"):
    st.subheader("Multi-Factor Composite Score")

    with st.expander("How to read this tab", expanded=False):
        st.markdown(
            "The composite score blends all factor signals into a single rank. "
            "Adjust weights below to emphasize your preferred factors.\n\n"
            "**Default**: equal weight across all factors. "
            "**Momentum-tilted**: emphasize momentum for trend-following. "
            "**Value-tilted**: emphasize value/quality for fundamental investing.\n\n"
            "Assets ranked highly across MULTIPLE factors are the strongest candidates. "
            "The **factor profile** heatmap shows which factors drive each pick."
        )

    # ── Configurable weights ──
    with st.expander("Factor Weights (adjust to customize composite)", expanded=False):
        w_cols = st.columns(len(FACTOR_GROUPS))
        weights = {}
        for i, gname in enumerate(FACTOR_GROUPS.keys()):
            default = {"Momentum": 25, "Mean Reversion": 10, "Value": 20,
                       "Quality": 20, "Earnings": 15, "Risk": 10}.get(gname, 15)
            weights[gname] = w_cols[i].slider(gname, 0, 100, default, 5, key=f"w_{gname}")

        total_w = sum(weights.values())
        if total_w > 0:
            norm_weights = {k: v / total_w for k, v in weights.items()}
        else:
            norm_weights = {k: 1 / len(weights) for k in weights}

        weight_str = " · ".join(f"{k}: {norm_weights[k] * 100:.0f}%" for k in norm_weights)
        st.caption(f"Normalized weights: {weight_str}")

    # Compute weighted composite
    weighted_composite = pd.Series(0.0, index=avail_tickers)
    total_weight_per_asset = pd.Series(0.0, index=avail_tickers)
    for gname, gcols in FACTOR_GROUPS.items():
        w = norm_weights.get(gname, 0)
        if w > 0 and gcols:
            group_avg = ranks[gcols].mean(axis=1, skipna=True)
            mask = group_avg.notna()
            weighted_composite[mask] += group_avg[mask] * w
            total_weight_per_asset[mask] += w

    weighted_composite = weighted_composite / total_weight_per_asset.replace(0, np.nan)

    # Track top/bottom predictions for accuracy measurement
    try:
        from src.prediction_tracker import record_prediction
        _top5 = weighted_composite.dropna().nlargest(5)
        _bot5 = weighted_composite.dropna().nsmallest(5)
        for _tk, _score in _top5.items():
            record_prediction(
                source="signal_scanner", ticker=_tk,
                prediction={"direction": "Bullish", "score": round(float(_score), 1), "quintile": "Top"},
                spot=0,  # scanner doesn't track individual prices
                metadata={"universe": len(weighted_composite.dropna())},
            )
        for _tk, _score in _bot5.items():
            record_prediction(
                source="signal_scanner", ticker=_tk,
                prediction={"direction": "Bearish", "score": round(float(_score), 1), "quintile": "Bottom"},
                spot=0,
                metadata={"universe": len(weighted_composite.dropna())},
            )
    except Exception:
        pass

    # Write cross-page context (limit to top/bottom 50 tickers to avoid memory bloat)
    try:
        from src.cross_context import write_context
        _comp_sorted = weighted_composite.dropna().sort_values()
        _keep = set(_comp_sorted.head(25).index) | set(_comp_sorted.tail(25).index)
        _scores_dict = {}
        for _tk in _keep:
            _scores_dict[_tk] = {
                "composite": round(float(weighted_composite[_tk]), 1),
            }
            for gname, gcols in FACTOR_GROUPS.items():
                if _tk in ranks.index:
                    _valid_cols = [c for c in gcols if c in ranks.columns]
                    if _valid_cols:
                        _gv = ranks.loc[_tk, _valid_cols].mean()
                        if pd.notna(_gv):
                            _scores_dict[_tk][gname] = round(float(_gv), 1)
        write_context("signal_scanner", {"scores": _scores_dict})

        # Write signals for top/bottom tickers
        from src.signal_engine import write_signal
        for _tk in _comp_sorted.tail(5).index:
            _sc = float(weighted_composite[_tk])
            write_signal("signal_scanner", _tk, "bull", min(1.0, _sc / 100),
                         reasoning=f"Composite score {_sc:.0f} — top-ranked by multi-factor scan")
        for _tk in _comp_sorted.head(5).index:
            _sc = float(weighted_composite[_tk])
            write_signal("signal_scanner", _tk, "bear", min(1.0, abs(_sc) / 100),
                         reasoning=f"Composite score {_sc:.0f} — bottom-ranked by multi-factor scan")
    except Exception:
        pass

    # ── Composite bar chart ──
    comp_sorted = weighted_composite.sort_values(ascending=True)
    fig_comp = go.Figure()
    fig_comp.add_trace(go.Bar(
        y=comp_sorted.index, x=comp_sorted.values, orientation="h",
        marker_color=[
            "#00ff88" if v > 70 else "#00d1ff" if v > 50
            else "#ffaa00" if v > 30 else "#ff4444"
            for v in comp_sorted
        ],
        text=[f"{v:.0f}" for v in comp_sorted], textposition="outside",
    ))
    fig_comp.add_vline(x=50, line_dash="dash", line_color="#555", annotation_text="Median")
    fig_comp.update_layout(
        template="plotly_dark", height=max(400, n * 28),
        title="Weighted Composite Score (0–100)",
        xaxis_title="Score", xaxis=dict(range=[0, 105]),
        margin=dict(l=0, r=50, t=40, b=0),
    )
    st.plotly_chart(fig_comp, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Factor profile heatmap (top 10) ──
    st.subheader("Factor Profile — Top 10")
    st.caption("Which factor groups drive each top asset's ranking.")

    # Build group rank DataFrame
    group_rank_df = pd.DataFrame(group_ranks)
    top10_tickers = weighted_composite.sort_values(ascending=False).head(min(10, n)).index
    top10_groups = group_rank_df.loc[top10_tickers]

    fig_profile = go.Figure(data=go.Heatmap(
        z=top10_groups.values,
        x=top10_groups.columns.tolist(),
        y=top10_groups.index.tolist(),
        colorscale=[[0, "#ff4444"], [0.5, "#1a1a2e"], [1, "#00ff88"]],
        zmid=50, zmin=0, zmax=100,
        text=[[f"{v:.0f}" for v in row] for row in top10_groups.values],
        texttemplate="%{text}", textfont={"size": 11},
        colorbar=dict(title="Rank"),
    ))
    fig_profile.update_layout(
        template="plotly_dark", height=max(250, len(top10_tickers) * 30),
        title="Factor Group Ranks — Top Assets",
        margin=dict(l=0, r=0, t=40, b=0),
    )
    st.plotly_chart(fig_profile, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Full ranking table ──
    st.subheader("Full Ranking Table")
    full_table = pd.DataFrame({"Ticker": avail_tickers})
    full_table["Composite"] = weighted_composite.values

    # Add key raw signals
    for col, fmt in [
        ("Mom_12-1", "+.1f"), ("Mom_3M", "+.1f"), ("RSI_14", ".0f"),
        ("Z_Score_63D", "+.1f"), ("Vol_20D", ".0f"), ("Drawdown", ".1f"),
    ]:
        if col in signals.columns:
            full_table[col] = signals[col].values

    if "forward_pe" in signals.columns:
        full_table["Fwd P/E"] = signals["forward_pe"].values
    if "roe" in signals.columns:
        full_table["ROE %"] = signals["roe"].values
    if "EPS_Rev_Net30" in signals.columns:
        full_table["EPS Rev"] = signals["EPS_Rev_Net30"].values

    full_table = full_table.sort_values("Composite", ascending=False)

    # Format columns
    full_table["Composite"] = full_table["Composite"].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "—")
    for col in [c for c in full_table.columns if c.startswith("Mom_")]:
        full_table[col] = full_table[col].apply(lambda v: f"{v:+.1f}%" if pd.notna(v) else "—")
    for col, fmt_fn in [
        ("RSI_14", lambda v: f"{v:.0f}"),
        ("Z_Score_63D", lambda v: f"{v:+.1f}"),
        ("Vol_20D", lambda v: f"{v:.0f}%"),
        ("Drawdown", lambda v: f"{v:.1f}%"),
        ("Fwd P/E", lambda v: f"{v:.1f}x"),
        ("ROE %", lambda v: f"{v:.1f}%"),
        ("EPS Rev", lambda v: f"{int(v):+d}"),
    ]:
        if col in full_table.columns:
            full_table[col] = full_table[col].apply(
                lambda v, fn=fmt_fn: fn(v) if pd.notna(v) else "—"
            )

    st.dataframe(full_table, use_container_width=True, hide_index=True)

    st.caption(
        "Backtested signals do not guarantee future returns. "
        "Cross-sectional ranks reflect relative positioning, not absolute quality. "
        "Always validate with independent analysis before trading."
    )
