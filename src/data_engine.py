import pandas as pd
import yfinance as yf
import os
import logging
from supabase import create_client, Client
from massive import RESTClient
from datetime import date, timedelta
import streamlit as st

# Setup logging
logger = logging.getLogger(__name__)

# Initialize Clients
def get_clients():
    return (
        create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")),
        RESTClient(os.environ.get("MASSIVE_API_KEY"))
    )

def format_massive_ticker(raw_ticker):
    """
    Format ticker symbol for API compatibility.
    Handles crypto (BTC-USD), forex, and energy symbols (ERCOT.LMP).
    """
    ticker = raw_ticker.strip().upper()
    # Keep crypto pairs as-is (e.g., BTC-USD)
    # Remove X: prefix for forex (e.g., X:EURUSD -> EURUSD)
    if ticker.startswith("X:"):
        ticker = ticker[2:]
    return ticker

def fetch_massive_data(symbol, days):
    """
    Fetch price data with three-tier fallback strategy.
    Returns: DataFrame with 'Close' column or None if all sources fail.
    """
    supabase, massive_client = get_clients()
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    # 1. Supabase Check
    try:
        res = supabase.table("price_data").select("*").eq("ticker", symbol).gte("timestamp", start_date.isoformat()).execute()
        if res.data and len(res.data) > 0:
            df = pd.DataFrame(res.data)
            df['Date'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
            df.set_index('Date', inplace=True)
            # Check if data is recent enough
            if len(df) > 0 and df.index[-1].date() >= (end_date - timedelta(days=1)):
                return df[['close_price']].rename(columns={'close_price': 'Close'})
    except Exception as e:
        logger.warning(f"Supabase fetch failed for {symbol}: {str(e)}")

# Add this to the bottom of src/data_engine.py
def fetch_options_chain(underlying_symbol, expiration_date=None):
    """
    Fetches the options chain snapshot for a given underlying ticker.
    If expiration_date is provided (YYYY-MM-DD), it filters for that expiry.
    """
    mc = get_massive()
    if not mc: return None
    
    try:
        # Note: The exact method name depends on your massive-sdk version.
        # This uses the standard snapshot endpoint structure for options.
        formatted_sym = underlying_symbol.replace("X:", "").replace("ERCOT.", "").upper()
        
        # Pull all active contracts for the underlying
        # In a real API, this might be mc.get_snapshot_options(underlying=formatted_sym)
        # Using a generic call structure here for Massive/Polygon:
        response = mc.reference_options_contracts(underlying_ticker=formatted_sym, limit=1000)
        
        if not response: return None
        
        df = pd.DataFrame(response)
        
        # Filter by expiration if requested
        if expiration_date and 'expiration_date' in df.columns:
            df = df[df['expiration_date'] == expiration_date]
            
        return df
    except Exception as e:
        import streamlit as st
        st.error(f"Options API Error: {e}")
        return None
    
    
    # 2. Massive API with Loop
    all_aggs = []
    current_to = end_date.strftime("%Y-%m-%d")
    try:
        for _ in range(5):
            aggs = massive_client.list_aggs(ticker=symbol, multiplier=1, timespan="day", from_=start_date.strftime("%Y-%m-%d"), to=current_to, limit=5000)
            if not aggs: 
                break
            batch = list(aggs)
            all_aggs.extend(batch)
            if len(batch) == 0:
                break
            earliest_dt = pd.to_datetime(min(a.timestamp for a in batch), unit='ms').date()
            if earliest_dt <= start_date or len(batch) < 100: 
                break
            current_to = (earliest_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        if all_aggs and len(all_aggs) > 0:
            df = pd.DataFrame(all_aggs)
            df['Date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize(None)
            df.set_index('Date', inplace=True)
            df.sort_index(inplace=True)
            df.rename(columns={'close': 'Close'}, inplace=True)
            
            # Upsert to Supabase
            rows = [{"ticker": symbol, "timestamp": i.isoformat(), "close_price": float(r['Close'])} for i, r in df.iterrows()]
            supabase.table("price_data").upsert(rows).execute()
            return df[['Close']]
    except Exception as e:
        logger.warning(f"Massive API error for {symbol}: {str(e)}")
        st.warning(f"Massive API Rate Limit/Error. Falling back to yfinance.")

    # Tier 3: yfinance Fallback
    try:
        yahoo_ticker = translate_to_yahoo(formatted_symbol)
        st.write(f"🔍 DEBUG: Attempting yfinance fetch for {yahoo_ticker}...") # ADD THIS
        
        df_yf = yf.download(yahoo_ticker, start=start_date, end=end_date, progress=False)
        
        if not df_yf.empty:
            st.write("✅ DEBUG: yfinance succeeded!") # ADD THIS
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            return df_yf[['Close']]
        else:
            st.write("❌ DEBUG: yfinance returned an empty dataframe.") # ADD THIS
            return None
    except Exception as e:
        st.write(f"🚨 DEBUG: yfinance threw a hard error: {e}") # ADD THIS
        return None
    
    return None
