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
    if not api_key:
        try:
            api_key = st.secrets["MASSIVE_API_KEY"]
        except Exception:
            pass
    if api_key:
        try:
            url = f"https://api.polygon.io/v2/aggs/ticker/{formatted_symbol}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
            res = requests.get(url, params={"apiKey": api_key}, timeout=30)
            res.raise_for_status()
            data = res.json()
            if 'results' in data:
                st.session_state['current_data_source'] = "Massive API (Live Feed)"
                df = pd.DataFrame(data['results'])
                df['Date'] = pd.to_datetime(df['t'], unit='ms')
                df.set_index('Date', inplace=True)
                return df[['c']].rename(columns={'c': 'Close'})
        except Exception as e:
            logger.warning(f"Massive API fetch failed for {formatted_symbol}: {e}")

    # Tier 2: Yahoo Fallback
    try:
        df_yf = yf.download(translate_to_yahoo(formatted_symbol), start=start_date, end=end_date, progress=False)
        if not df_yf.empty:
            st.session_state['current_data_source'] = "Yahoo Finance (Fallback API)"
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            # Flatten MultiIndex columns from newer yfinance versions
            if isinstance(df_yf.columns, pd.MultiIndex):
                df_yf.columns = df_yf.columns.get_level_values(0)
            return df_yf[['Close']]
    except Exception as e:
        logger.warning(f"Yahoo Finance fallback failed for {formatted_symbol}: {e}")
    return None

# --- OPTIONS ENGINE ---
def _get_massive_key():
    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["MASSIVE_API_KEY"]
        except Exception:
            pass
    return api_key


def _polygon_paginate(url: str, api_key: str) -> list:
    results = []
    while url:
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        data = res.json()
        results.extend(data.get('results', []))
        next_url = data.get('next_url')
        url = f"{next_url}&apiKey={api_key}" if next_url else None
    return results


@st.cache_data(ttl=3600)
def get_expiration_dates(symbol: str):
    """Fetches all available expiration dates."""
    api_key = _get_massive_key()
    if api_key:
        try:
            url = f"https://api.polygon.io/v3/reference/options/contracts?underlying_ticker={symbol}&limit=1000&apiKey={api_key}"
            contracts = _polygon_paginate(url, api_key)
            exps = sorted(set(c['expiration_date'] for c in contracts))
            if exps:
                return exps
        except Exception as e:
            logger.warning(f"Massive expirations fetch failed for {symbol}: {e}")

    # Fallback: yfinance
    try:
        tk = yf.Ticker(translate_to_yahoo(symbol))
        return list(tk.options)
    except Exception as e:
        logger.error(f"Failed to fetch expirations: {e}")
        return []


@st.cache_data(ttl=3600, show_spinner="Fetching options chain from Massive...")
def fetch_options_chain(symbol: str, expiration: str = None) -> pd.DataFrame:
    """Fetches full options chain with Greeks from Massive (Polygon), yfinance fallback."""
    api_key = _get_massive_key()
    if api_key:
        try:
            url = f"https://api.polygon.io/v3/snapshot/options/{symbol}?expiration_date={expiration}&limit=250&apiKey={api_key}"
            results = _polygon_paginate(url, api_key)
            if results:
                rows = []
                for r in results:
                    d = r.get('details', {})
                    g = r.get('greeks', {})
                    day = r.get('day', {})
                    rows.append({
                        'strike_price': d.get('strike_price'),
                        'contract_type': d.get('contract_type'),
                        'expiration_date': d.get('expiration_date'),
                        'bid': r.get('last_quote', {}).get('bid', 0),
                        'ask': r.get('last_quote', {}).get('ask', 0),
                        'volume': day.get('volume', 0),
                        'open_interest': r.get('open_interest', 0),
                        'implied_volatility': r.get('implied_volatility', 0),
                        'delta': g.get('delta', 0),
                        'gamma': g.get('gamma', 0),
                        'theta': g.get('theta', 0),
                        'vega': g.get('vega', 0),
                    })
                df = pd.DataFrame(rows)
                st.session_state['current_data_source'] = "Massive API (Live Feed)"
                return df
        except Exception as e:
            logger.warning(f"Massive options chain fetch failed for {symbol}: {e}")

    # Fallback: yfinance + Black-Scholes
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

        RISK_FREE_RATE = 0.045
        hist = tk.history(period="5d")
        spot = hist['Close'].iloc[-1] if not hist.empty else df['strike_price'].median()
        t = (pd.to_datetime(target_exp) - pd.Timestamp.now()).days / 365.0
        if t <= 0: t = 0.001

        iv = np.where((df['implied_volatility'].isna()) | (df['implied_volatility'] <= 0), 0.001, df['implied_volatility'])
        d1 = (np.log(spot / df['strike_price']) + (RISK_FREE_RATE + (iv**2)/2) * t) / (iv * np.sqrt(t))

        df['delta'] = np.where(df['contract_type'] == 'call', norm.cdf(d1), norm.cdf(d1) - 1)
        df['gamma'] = norm.pdf(d1) / (spot * iv * np.sqrt(t))
        df['theta'] = (-(spot * iv * norm.pdf(d1)) / (2 * np.sqrt(t)) - RISK_FREE_RATE * df['strike_price'] * np.exp(-RISK_FREE_RATE * t) * norm.cdf(d1 - iv * np.sqrt(t))) / 365

        st.session_state['current_data_source'] = "Yahoo Finance + SciPy (Fallback)"
        return df
    except Exception as e:
        logger.error(f"Options fetch failed: {e}")
        return None

def render_data_source_footer():
    st.markdown("---")
    source = st.session_state.get('current_data_source', 'No data loaded yet')
    st.caption(f"📡 **Active Data Source:** `{source}`")
