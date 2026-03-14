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
        logger.warning("Supabase credentials missing from environment.")
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

    # ---------------------------------------------------------
    # TIER 1: Massive REST API (Live Feed)
    # ---------------------------------------------------------
    api_key = os.environ.get("MASSIVE_API_KEY")
    if api_key:
        try:
            logger.info(f"🌐 Querying Massive API for {formatted_symbol}")
            base_url = "https://api.polygon.io/v2/aggs/ticker" 
            url = f"{base_url}/{formatted_symbol}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
            
            response = requests.get(url, params={"apiKey": api_key})
            response.raise_for_status()
            data = response.json()
            
            if 'results' in data:
                logger.info("✅ Massive API success.")
                st.session_state['current_data_source'] = "Massive API (Live Feed)"
                df = pd.DataFrame(data['results'])
                df['Date'] = pd.to_datetime(df['t'], unit='ms')
                df.set_index('Date', inplace=True)
                return df[['c']].rename(columns={'c': 'Close'})
            else:
                logger.warning(f"No 'results' array found in Massive response for {formatted_symbol}")
        except Exception as e:
            logger.error(f"❌ Massive REST API failed: {e}")
    else:
        logger.warning("MASSIVE_API_KEY not found. Skipping Tier 1.")

    # ---------------------------------------------------------
    # TIER 2: Supabase Cache (Backup)
    # ---------------------------------------------------------
    logger.info("Attempting Supabase fallback...")
    sb = get_supabase_client()
    if sb:
        try:
            res = sb.table("price_data").select("*").eq("ticker", formatted_symbol).gte("timestamp", start_date.isoformat()).execute()
            if res.data:
                logger.info(f"✅ Backup success: Data loaded from Supabase for {formatted_symbol}")
                st.session_state['current_data_source'] = "Supabase Database (Backup)"
                df = pd.DataFrame(res.data)
                df['Date'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
                df.set_index('Date', inplace=True)
                return df[['close_price']].rename(columns={'close_price': 'Close'})
        except Exception as e:
            logger.warning(f"Supabase backup failed: {e}")

    # ---------------------------------------------------------
    # TIER 3: Yahoo Finance Fallback
    # ---------------------------------------------------------
    try:
        yahoo_ticker = translate_to_yahoo(formatted_symbol)
        logger.info(f"🦆 Attempting yfinance fallback for {yahoo_ticker}...")
        
        df_yf = yf.download(yahoo_ticker, start=start_date, end=end_date, progress=False)
        
        if not df_yf.empty:
            logger.info("✅ yfinance success.")
            st.session_state['current_data_source'] = "Yahoo Finance (Fallback API)"
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            return df_yf[['Close']]
        else:
            logger.warning(f"yfinance returned an empty dataframe for {yahoo_ticker}.")
    except Exception as e:
        logger.exception(f"🚨 Critical failure: All sources failed for {formatted_symbol}. Error: {e}")
    
    return None

# --- OPTIONS DATA ENGINE ---

def fetch_options_chain(underlying_symbol: str, expiration_date: str = None) -> pd.DataFrame:
    """
    Fetches the options chain snapshot for a given underlying ticker via REST.
    If expiration_date is provided (YYYY-MM-DD), it filters for that expiry.
    """
    logger.info(f"Fetching options chain for {underlying_symbol}")
    
    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        st.error("MASSIVE_API_KEY is missing from environment variables.")
        return None
        
    try:
        formatted_sym = translate_to_yahoo(underlying_symbol).upper()
        
        # Standard Polygon/Massive Options Reference Endpoint
        url = "https://api.polygon.io/v3/reference/options/contracts"
        params = {
            "underlying_ticker": formatted_sym,
            "limit": 1000,
            "apiKey": api_key
        }
        
        if expiration_date:
            params["expiration_date"] = expiration_date
            
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        if 'results' in data and len(data['results']) > 0:
            logger.info(f"✅ Successfully retrieved {len(data['results'])} contracts.")
            df = pd.DataFrame(data['results'])
            return df
        else:
            logger.warning("No options contracts found for this ticker.")
            return None
            
    except Exception as e:
        logger.exception(f"❌ Options chain REST fetch failed: {e}")
        return None
