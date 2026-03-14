import os
import logging
import pandas as pd
import requests
import yfinance as yf
from datetime import date, timedelta
from supabase import create_client, Client
import streamlit as st

# Initialize the logger for this specific file
logger = logging.getLogger(__name__)

# --- HELPER FUNCTIONS ---

def get_supabase_client() -> Client:
    """Safely initializes the Supabase client."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        return None
    try:
        return create_client(url, key)
    except Exception as e:
        logger.error(f"Failed to initialize Supabase: {e}")
        return None

def format_massive_ticker(user_input: str) -> str:
    """Formats the user input for the Massive/Polygon API."""
    if not user_input: return ""
    t = user_input.strip().upper()
    if ":" in t or t.startswith("ERCOT."): return t
    if "-USD" in t: return f"X:{t}"
    if any(x in t for x in ["HB_", "LZ_", "RT_", "DA_"]): return f"ERCOT.{t}"
    return t

def translate_to_yahoo(formatted_symbol: str) -> str:
    """Translates a Massive/Polygon ticker back to a Yahoo Finance ticker."""
    return formatted_symbol.replace("X:", "").replace("ERCOT.", "")

# --- MAIN DATA FETCHING ENGINE ---

@st.cache_data(ttl=3600, show_spinner="Fetching market data...")
def fetch_massive_data(symbol: str, days: int) -> pd.DataFrame:
    """
    The UNLIMITED Data Engine:
    1. Fetch live via Massive REST API (Primary).
    2. Fallback to Supabase if Massive is down.
    3. Fallback to yfinance if all else fails.
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    formatted_symbol = format_massive_ticker(symbol)
    
    logger.info(f"Fetching data for {formatted_symbol} ({days} days)")

    # TIER 1: Massive REST API (Live Feed)
    api_key = os.environ.get("MASSIVE_API_KEY")
    if api_key:
        try:
            # We use the standard Polygon/Massive historical aggregates endpoint
            base_url = "https://api.polygon.io/v2/aggs/ticker" 
            url = f"{base_url}/{formatted_symbol}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
            
            response = requests.get(url, params={"apiKey": api_key})
            response.raise_for_status()
            data = response.json()
            
            if 'results' in data:
                st.session_state['current_data_source'] = "Massive API (Live Feed)"
                df = pd.DataFrame(data['results'])
                df['Date'] = pd.to_datetime(df['t'], unit='ms')
                df.set_index('Date', inplace=True)
                return df[['c']].rename(columns={'c': 'Close'})
        except Exception as e:
            logger.error(f"Massive REST API failed: {e}")

    # TIER 2: Supabase Cache (Backup)
    sb = get_supabase_client()
    if sb:
        try:
            res = sb.table("price_data").select("*").eq("ticker", formatted_symbol).gte("timestamp", start_date.isoformat()).execute()
            if res.data:
                st.session_state['current_data_source'] = "Supabase Database (Backup)"
                df = pd.DataFrame(res.data)
                df['Date'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
                df.set_index('Date', inplace=True)
                return df[['close_price']].rename(columns={'close_price': 'Close'})
        except Exception as e:
            logger.warning(f"Supabase backup failed: {e}")

    # TIER 3: Yahoo Finance Fallback
    try:
        yahoo_ticker = translate_to_yahoo(formatted_symbol)
        df_yf = yf.download(yahoo_ticker, start=start_date, end=end_date, progress=False)
        if not df_yf.empty:
            st.session_state['current_data_source'] = "Yahoo Finance (Fallback API)"
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            return df_yf[['Close']]
    except Exception as e:
        logger.exception(f"All sources failed for {formatted_symbol}")
    
    return None

# --- OPTIONS DATA ENGINE ---

@st.cache_data(ttl=3600, show_spinner="Fetching live options chain...")
def fetch_options_chain(underlying_symbol: str, expiration_date: str = None) -> pd.DataFrame:
    """
    Fetches the options chain snapshot for a given underlying ticker via REST.
    Flattens the nested JSON into a clean dataframe for charting.
    """
    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        return None
        
    try:
        formatted_sym = translate_to_yahoo(underlying_symbol).upper()
        
        # We use the Snapshot API to get Pricing, IV, and Greeks in one call
        url = f"https://api.polygon.io/v3/snapshot/options/{formatted_sym}"
        params = {"limit": 250, "apiKey": api_key}
        
        if expiration_date:
            params["expiration_date"] = expiration_date
            
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        if 'results' in data and len(data['results']) > 0:
            st.session_state['current_data_source'] = "Massive API (Options Snapshot Feed)"
            
            # Flattening logic to prevent "Missing Columns" errors in the UI
            contracts = []
            for r in data['results']:
                contract = {
                    "ticker": r.get("details", {}).get("ticker"),
                    "contract_type": r.get("details", {}).get("contract_type"),
                    "strike_price": r.get("details", {}).get("strike_price"),
                    "expiration_date": r.get("details", {}).get("expiration_date"),
                    "implied_volatility": r.get("implied_volatility"),
                    "open_interest": r.get("open_interest"),
                    "volume": r.get("day", {}).get("volume", 0),
                    "bid": r.get("last_quote", {}).get("bid", 0),
                    "ask": r.get("last_quote", {}).get("ask", 0),
                    "delta": r.get("greeks", {}).get("delta"),
                    "gamma": r.get("greeks", {}).get("gamma"),
                    "theta": r.get("greeks", {}).get("theta"),
                    "vega": r.get("greeks", {}).get("vega"),
                }
                contracts.append(contract)
                
            return pd.DataFrame(contracts)
        return None
            
    except Exception as e:
        logger.exception(f"Options chain fetch failed: {e}")
        return None

# --- GLOBAL UI COMPONENTS ---

def render_data_source_footer():
    """Neatly displays the active data source badge at the bottom of the page."""
    st.markdown("---")
    source = st.session_state.get('current_data_source', 'Awaiting Data...')
    st.caption(f"📡 **Active Data Source:** `{source}` (Cached for performance)")
