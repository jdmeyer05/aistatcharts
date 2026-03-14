import pandas as pd
import yfinance as yf
import os
from supabase import create_client, Client
from massive import RESTClient
from datetime import date, timedelta
import streamlit as st

# Initialize Clients
def get_clients():
    return (
        create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")),
        RESTClient(os.environ.get("MASSIVE_API_KEY"))
    )

def fetch_massive_data(symbol, days):
    supabase, massive_client = get_clients()
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    
    # 1. Supabase Check
    try:
        res = supabase.table("price_data").select("*").eq("ticker", symbol).gte("timestamp", start_date.isoformat()).execute()
        if res.data:
            df = pd.DataFrame(res.data)
            df['Date'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
            df.set_index('Date', inplace=True)
            if df.index[-1].date() >= (end_date - timedelta(days=1)):
                return df[['close_price']].rename(columns={'close_price': 'Close'})
    except: pass

    # 2. Massive API with Loop
    all_aggs = []
    current_to = end_date.strftime("%Y-%m-%d")
    try:
        for _ in range(5):
            aggs = massive_client.list_aggs(ticker=symbol, multiplier=1, timespan="day", from_=start_date.strftime("%Y-%m-%d"), to=current_to, limit=5000)
            if not aggs: break
            batch = list(aggs)
            all_aggs.extend(batch)
            earliest_dt = pd.to_datetime(min(a.timestamp for a in batch), unit='ms').date()
            if earliest_dt <= start_date or len(batch) < 100: break
            current_to = (earliest_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        if all_aggs:
            df = pd.DataFrame(all_aggs)
            df['Date'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize(None)
            df.set_index('Date', inplace=True).sort_index(inplace=True)
            df.rename(columns={'close': 'Close'}, inplace=True)
            
            # Upsert to Supabase
            rows = [{"ticker": symbol, "timestamp": i.isoformat(), "close_price": float(r['Close'])} for i, r in df.iterrows()]
            supabase.table("price_data").upsert(rows).execute()
            return df[['Close']]
    except Exception as e:
        st.warning(f"Massive API Rate Limit/Error. Falling back to yfinance.")

    # 3. yfinance Fallback
    try:
        df_yf = yf.download(symbol.replace("X:", "").replace("ERCOT.", ""), start=start_date, end=end_date, progress=False)
        if not df_yf.empty:
            df_yf.index = pd.to_datetime(df_yf.index).tz_localize(None)
            return df_yf[['Close']]
    except: return None
