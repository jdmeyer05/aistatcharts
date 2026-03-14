import os
import logging
import pandas as pd
import numpy as np
from scipy.stats import norm
import requests
import yfinance as yf
from datetime import date, timedelta
from supabase import create_client, Client
import streamlit as st

logger = logging.getLogger(__name__)

# --- HELPER FUNCTIONS ---
def get_supabase_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key: return None
    try: return create_client(url, key)
    except Exception as e:
        logger.error(f"Failed to initialize Supabase: {e}")
        return None

def format_massive_ticker(user_input: str) -> str:
    if not user_input: return ""
    t = user_input.strip().upper()
    if ":" in t or t.startswith("ERCOT."): return t
    if "-USD" in t: return f"X:{t}"
    if any(x in t for x in ["HB_", "LZ_", "RT_", "DA_"]): return f"ERCOT.{t}"
    return t

def translate_to_yahoo(formatted_symbol: str) -> str:
    return formatted_symbol.replace("X:", "").replace("ERCOT.", "")

# --- PRICE DATA ENGINE ---
@st.cache_data(ttl=3600, show_spinner="Fetching market data...")
def fetch_massive_data(symbol: str, days: int) -> pd.DataFrame:
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    formatted_symbol = format_massive_ticker(symbol)
    
    # Tier 1: Massive
    api_key = os.environ.get("MASSIVE_API_KEY")
    if api_key:
        try:
            url = f"https://api.polygon.io/v2/aggs/ticker/{formatted_symbol}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
            res = requests.get(url, params={"apiKey": api_key})
            res.raise_for_status()
            data = res.json()
            if 'results' in data:
                st.session_state['current_data_source'] = "Massive API (Live Feed)"
                df = pd.DataFrame(data['results'])
                df['Date'] = pd.to_datetime(df['t'], unit='ms')
                df.set_index('Date', inplace=True)
                return df[['c']].rename(columns={'c': 'Close'})
        except: pass

    # Tier 2: Yahoo Fallback
    try:
        df_yf = yf.download(translate_to_yahoo(formatted_symbol), start=start_date, end=end_date, progress=False)
        if not df_yf.empty:
            st.session_state['current_data_source'] = "Yahoo Finance (Fallback API)"
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            return df_yf[['Close']]
    except: pass
    return None

# --- UNLIMITED OPTIONS ENGINE ---
@st.cache_data(ttl=3600)
def get_expiration_dates(symbol: str):
    """Fetches all available expiration dates instantly."""
    try:
        tk = yf.Ticker(translate_to_yahoo(symbol))
        return list(tk.options)
    except Exception as e:
        logger.error(f"Failed to fetch expirations: {e}")
        return []

@st.cache_data(ttl=3600, show_spinner="Calculating Black-Scholes Greeks...")
def fetch_options_chain(symbol: str, expiration: str = None) -> pd.DataFrame:
    """Fetches chain and calculates Delta dynamically to bypass API limits."""
    try:
        tk = yf.Ticker(translate_to_yahoo(symbol))
        exps = tk.options
        if not exps: return None
        
        target_exp = expiration if expiration else exps[0]
        chain = tk.option_chain(target_exp)
        
        calls, puts = chain.calls, chain.puts
        calls['contract_type'] = 'call'
        puts['contract_type'] = 'put'
        
        df = pd.concat([calls, puts], ignore_index=True)
        df = df.rename(columns={
            'strike': 'strike_price', 'impliedVolatility': 'implied_volatility',
            'openInterest': 'open_interest', 'volume': 'volume'
        })
        df['expiration_date'] = target_exp
        
        # Black-Scholes Delta Approximation
        hist = tk.history(period="5d")
        spot = hist['Close'].iloc[-1] if not hist.empty else df['strike_price'].median()
        t = (pd.to_datetime(target_exp) - pd.Timestamp.now()).days / 365.0
        if t <= 0: t = 0.001
        
        iv = np.where((df['implied_volatility'].isna()) | (df['implied_volatility'] <= 0), 0.001, df['implied_volatility'])
        d1 = (np.log(spot / df['strike_price']) + (0.04 + (iv**2)/2) * t) / (iv * np.sqrt(t))
        
        df['delta'] = np.where(df['contract_type'] == 'call', norm.cdf(d1), norm.cdf(d1) - 1)
        df['theta'] = 0.0 # Placeholder to prevent UI crash
        df['gamma'] = 0.0 # Placeholder to prevent UI crash
        
        st.session_state['current_data_source'] = "Unlimited Options Engine (YFinance + SciPy)"
        return df
    except Exception as e:
        logger.error(f"Options fetch failed: {e}")
        return None

def render_data_source_footer():
    st.markdown("---")
    source = st.session_state.get('current_data_source', 'Awaiting Data...')
    st.caption(f"📡 **Active Data Source:** `{source}`")
