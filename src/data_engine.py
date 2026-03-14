import streamlit as st
import pandas as pd
import numpy as np
import os
from supabase import create_client, Client
from massive import RESTClient
from datetime import date, timedelta

# 1. AUTHENTICATION (Pulls from Cloud Run Variables)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
MASSIVE_API_KEY = os.environ.get("MASSIVE_API_KEY")

# Initialize Clients
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
massive_client = RESTClient(MASSIVE_API_KEY)

def fetch_massive_data(symbol, days):
    """
    Smart Cache: 
    1. Checks Supabase for existing data.
    2. If missing, fetches from Massive.
    3. Saves new data back to Supabase to prevent future API calls.
    """
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    # --- STEP A: CHECK SUPABASE CACHE ---
    try:
        response = supabase.table("price_data") \
            .select("*") \
            .eq("ticker", symbol) \
            .gte("timestamp", start_date.isoformat()) \
            .order("timestamp", desc=False) \
            .execute()
        
        cached_df = pd.DataFrame(response.data)
        
        if not cached_df.empty:
            cached_df['Date'] = pd.to_datetime(cached_df['timestamp'])
            cached_df.set_index('Date', inplace=True)
            # If the data is fresh (within 24h), return it immediately
            if cached_df.index[-1].date() >= (end_date - timedelta(days=1)):
                return cached_df[['close_price']].rename(columns={'close_price': 'Close'})
    except Exception as e:
        st.sidebar.warning(f"Cache miss or error: {e}")

    # --- STEP B: FETCH FROM MASSIVE (API CALL) ---
    try:
        aggs = massive_client.list_aggs(
            ticker=symbol, multiplier=1, timespan="day",
            from_=start_date.strftime("%Y-%m-%d"),
            to=end_date.strftime("%Y-%m-%d"),
            limit=5000
        )
        
        if not aggs: return None
        
        df = pd.DataFrame(aggs)
        df['Date'] = pd.to_datetime(df['timestamp'], unit='ms')
        
        # --- STEP C: UPDATE CACHE ---
        rows_to_upsert = [
            {"ticker": symbol, "timestamp": row['Date'].isoformat(), "close_price": float(row['close'])}
            for _, row in df.iterrows()
        ]
        supabase.table("price_data").upsert(rows_to_upsert).execute()
        
        df.set_index('Date', inplace=True)
        return df[['close']].rename(columns={'close': 'Close'})

    except Exception as e:
        st.error(f"API/Cache Failure: {e}")
        return None
