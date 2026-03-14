import streamlit as st
import pandas as pd
import numpy as np
import yfinance as yf
import os
from supabase import create_client, Client
from massive import RESTClient
from datetime import date, timedelta

# --- 1. INITIALIZATION ---
# Pulling from Google Cloud Run Environment Variables
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY")

# Initialize Clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
massive_client = RESTClient(MASSIVE_API_KEY)

# --- 2. HELPERS ---
def format_massive_ticker(user_input: str) -> str:
    """Standardizes user input for the Massive/Polygon API."""
    t = user_input.strip().upper()
    if ":" in t or t.startswith("ERCOT."): return t
    if "-USD" in t: return f"X:{t}"
    if any(x in t for x in ["HB_", "LZ_", "RT_", "DA_"]): return f"ERCOT.{t}"
    return t

def translate_to_yahoo(symbol: str) -> str:
    """Strips Massive prefixes so Yahoo Finance understands the ticker."""
    if symbol.startswith("X:"):
        return symbol.replace("X:", "")
    if symbol.startswith("ERCOT."):
        return symbol.replace("ERCOT.", "")
    return symbol

# --- 3. THE CORE ENGINE ---
def fetch_massive_data(symbol, days):
    """
    Tiered Data Fetcher:
    1. Check Supabase (Cache)
    2. Try Massive API (Primary)
    3. Reroute to yfinance (Fallback)
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    formatted_symbol = format_massive_ticker(symbol)
    
    # --- TIER 1: SUPABASE CACHE ---
    try:
        response = supabase.table("price_data") \
            .select("*") \
            .eq("ticker", formatted_symbol) \
            .gte("timestamp", start_date.isoformat()) \
            .order("timestamp", desc=False) \
            .execute()
        
        if response.data:
            cached_df = pd.DataFrame(response.data)
            cached_df['Date'] = pd.to_datetime(cached_df['timestamp']).dt.tz_localize(None)
            cached_df.set_index('Date', inplace=True)
            
            # If data is recent (within 24h), return it
            if cached_df.index[-1].date() >= (end_date - timedelta(days=1)):
                return cached_df[['close_price']].rename(columns={'close_price': 'Close'})
    except Exception:
        pass # Silently fail to API if cache is broken

    # --- TIER 2: MASSIVE API (with Pagination) ---
    all_aggs = []
    current_to = end_date.strftime("%Y-%m-%d")
    
    try:
        # Loop up to 5 times (max 25,000 rows) to ensure full data depth
        for _ in range(5):
            aggs = massive_client.list_aggs(
                ticker=formatted_symbol, multiplier=1, timespan="day",
                from_=start_date.strftime("%Y-%m-%d"), to=current_to, limit=5000
            )
            if not aggs: break
            
            batch = list(aggs)
            all_aggs.extend(batch)
            
            earliest_ms = min(a.timestamp for a in batch)
            earliest_dt = pd.to_datetime(earliest_ms, unit='ms').date()
            
            if earliest_dt <= start_date or len(batch) < 100: break
            current_to = (earliest_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        if all_aggs:
            df = pd.DataFrame(all_aggs)
            df['Date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize(None)
            df.set_index('Date', inplace=True)
            df.sort_index(inplace=True)
            df.rename(columns={'close': 'Close'}, inplace=True)
            
            # Save to Cache for next time
            rows = [{"ticker": formatted_symbol, "timestamp": idx.isoformat(), "close_price": float(row['Close'])} 
                    for idx, row in df.iterrows()]
            supabase.table("price_data").upsert(rows).execute()
            
            return df[['Close']]

    except Exception as e:
        if "429" in str(e):
            st.warning("⚠️ Massive API Rate Limit. Rerouting to Yahoo Finance...")
        else:
            st.sidebar.error(f"Massive API Error: {e}")

    # --- TIER 3: YFINANCE FALLBACK ---
    try:
        yahoo_ticker = translate_to_yahoo(formatted_symbol)
        # yahoo period uses 'd' for days
        df_yf = yf.download(yahoo_ticker, start=start_date, end=end_date, progress=False)
        
        if not df_yf.empty:
            st.info(f"💡 Fallback active: Data for {yahoo_ticker} pulled from Yahoo.")
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            return df_yf[['Close']]
    except Exception as y_err:
        st.error(f"All data sources failed for {symbol}.")
    
    return None
