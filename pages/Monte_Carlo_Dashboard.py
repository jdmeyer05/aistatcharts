import streamlit as st
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import yfinance as yf
import datetime
from datetime import date
import re
from typing import Optional, Tuple

# ===============================
# CONFIG & STYLING
# ===============================
st.set_page_config(page_title="Seasonality & Monte Carlo", layout="wide")

# ===============================
# CORE ANALYTICS ENGINE (Ticker Logic)
# ===============================
def _build_yahoo_candidates(ticker: str) -> list[str]:
    t = ticker.strip().upper().replace("$", "")
    cands = [t]
    ROOTS = {"NG","CL","ES","NQ","GC","SI","HG","ZC","ZW","ZS","KC","SB","CC","RB","HO"}
    m = re.fullmatch(r"([A-Z]{1,3})([FGHJKMNQUVXZ])(\d{2})", t)
    if m:
        root, mcode, yy = m.groups()
        cands.append(f"{root}{mcode}{yy}=NG")
        cands.append(f"{root}{mcode}{yy}.NYM")
        cands.append(f"{root}=F")
    else:
        if t in ROOTS and not t.endswith("=F"):
            cands.append(f"{t}=F")
    seen, uniq = set(), []
    for s in cands:
        if s not in seen:
            seen.add(s); uniq.append(s)
    return uniq

def _normalize_close(df: pd.DataFrame) -> Optional[pd.DataFrame]:
    if df is None or df.empty: return None
    if isinstance(df.columns, pd.MultiIndex):
        lvl0 = df.columns.get_level_values(0)
        if "Close" in set(lvl0):
            s = df["Close"]; s = s.iloc[:, 0] if isinstance(s, pd.DataFrame) else s
            return pd.DataFrame({"Close": s}).dropna()
    if "Close" in df.columns: return df[["Close"]].dropna()
    if "Adj Close" in df.columns: return df[["Adj Close"]].rename(columns={"Adj Close":"Close"}).dropna()
    return None

@st.cache_data(ttl=3600)
def fetch_prices(ticker: str, period: str) -> pd.DataFrame:
    candidates = _build_yahoo_candidates(ticker)
    for sym in candidates:
        for aa in (True, False):
            try:
                df = yf.download(sym, period=period, auto_adjust=aa, progress=False)
                out = _normalize_close(df)
                if out is not None and not out.empty:
                    out.attrs["ticker_used"] = sym
                    return out
            except: continue
    raise RuntimeError(f"Could not fetch data for {ticker}")

# ===============================
# RETURN CALCULATIONS
# ===============================
def daily_returns(close: pd.Series, method: str = "log") -> pd.Series:
    return np.log(close / close.shift(1)).dropna() if method == "log" else close.pct_change().dropna()

def weekly_returns(close: pd.Series, method: str = "log") -> pd.Series:
    dlog = np.log(close / close.shift(1)).dropna()
    df = pd.DataFrame({"dlog": dlog})
    ic = dlog.index.isocalendar()
    df["iso_year"], df["iso_week"] = ic["year"].astype(int), ic["week"].astype(int)
    wk_log = df.groupby(["iso_year", "iso_week"])["dlog"].sum()
    monday_dates = pd.to_datetime([date.fromisocalendar(int(y), int(w), 1) for (y, w) in wk_log.index])
    wk_log.index = monday_dates
    return wk_log if method == "log" else np.exp(wk_log) - 1.0

def monthly_returns(close: pd.Series, method: str = "log") -> pd.Series:
    dlog = np.log(close / close.shift(1)).dropna()
    df = pd.DataFrame({"dlog": dlog, "year": dlog.index.year, "month": dlog.index.month})
    mo_log = df.groupby(["year", "month"])["dlog"].sum()
    dt_idx = pd.to_datetime([f"{y}-{m:02d}-01" for y, m in mo_log.index]) + pd.offsets.MonthEnd(0)
    mo_log.index = dt_idx
    return mo_log if method == "log" else np.exp(mo_log) - 1.0

# ===============================
# PLOTTING FUNCTIONS
# ===============================
def plot_seasonality_box(series: pd.Series, is_week: bool, years_back: int):
    fig, ax = plt.subplots(figsize=(10, 4))
    if is_week:
        labels = series.index.isocalendar().week.astype(int)
        title, xlabel, count = "Weekly Seasonality", "ISO Week", 53
    else:
        labels = series.index.month
        title, xlabel, count = "Monthly Seasonality", "Month", 12
    
    df = pd.DataFrame({"ret": series, "grp": labels})
    data = [df.loc[df["grp"] == i, "ret"].values for i in range(1, count + 1)]
    
    ax.boxplot(data, positions=np.arange(1, count + 1), showfliers=False)
    ax.set_title(f"{title} (Last {years_back}y)")
    ax.set_xlabel(xlabel)
    ax.grid(True, axis="y", linestyle=":", alpha=0.6)
    plt.tight_layout()
    return fig

# ===============================
# STREAMLIT UI
# ===============================
st.title("📈 Advanced Seasonality & Monte Carlo")

# --- Sidebar Controls ---
with st.sidebar:
    st.header("Data Settings")
    ticker = st.text_input("Ticker Symbol", value="BTC-USD")
    hist_period = st.selectbox("Historical Range", ["2y", "5y", "10y", "max"], index=1)
    years_back = st.slider("Lookback Window (Years)", 2, 20, 8)
    ret_method = st.radio("Return Method", ["log", "pct"])
    
    st.divider()
    st.header("Monte Carlo Settings")
    n_sims = st.number_input("Simulations", 1000, 50000, 10000, step=1000)
    vol_mult = st.slider("Volatility Multiplier", 0.5, 2.0, 1.0, 0.1)
    drift_bias = st.slider("Annual Drift Bias (%)", -20.0, 20.0, 0.0, 0.5)

# --- Execution ---
try:
    px_df = fetch_prices(ticker, hist_period)
    px = px_df["Close"].tail(int(years_back * 252))
    used_ticker = px_df.attrs.get("ticker_used")

    st.success(f"Loaded {used_ticker} ({len(px)} days)")

    # --- Seasonality Row ---
    col1, col2 = st.columns(2)
    
    with col1:
        st.subheader("Weekly Distribution")
        wk_ret = weekly_returns(px, method=ret_method)
        st.pyplot(plot_seasonality_box(wk_ret, True, years_back))

    with col2:
        st.subheader("Monthly Distribution")
        mo_ret = monthly_returns(px, method=ret_method)
        st.pyplot(plot_seasonality_box(mo_ret, False, years_back))

    # --- YTD Overlay ---
    st.divider()
    st.subheader("Yearly YTD Performance Overlay")
    
    fig_ytd, ax_ytd = plt.subplots(figsize=(12, 5))
    years = sorted(px.index.year.unique())
    for y in years:
        yr_data = px[px.index.year == y]
        ytd = (yr_data / yr_data.iloc[0]) - 1.0
        alpha = 1.0 if y == years[-1] else 0.3
        linewidth = 2.5 if y == years[-1] else 1.0
        ax_ytd.plot(range(len(ytd)), ytd.values, label=str(y), alpha=alpha, lw=linewidth)
    
    ax_ytd.set_title("Returns Since Jan 1st")
    ax_ytd.legend(ncol=3, loc='upper left', fontsize='small')
    st.pyplot(fig_ytd)

except Exception as e:
    st.error(f"Error: {e}")
    st.info("Try a common symbol like 'BTC-USD' or 'AAPL'.")

# --- Next Step ---
# Would you like me to help you integrate the specific Monte Carlo simulation logic 
# into this page to show the price 'fan' chart?
