import pandas as pd
import numpy as np
import os
from massive import RESTClient
from datetime import date, timedelta
import streamlit as st

# Initialize client
api_key = os.environ.get("MASSIVE_API_KEY")
client = RESTClient(api_key)

def format_massive_ticker(user_input: str) -> str:
    """Auto-prefixes tickers for user convenience."""
    t = user_input.strip().upper()
    if ":" in t or t.startswith("ERCOT."): return t
    if "-USD" in t: return f"X:{t}"
    if any(x in t for x in ["HB_", "LZ_", "RT_", "DA_"]): return f"ERCOT.{t}"
    return t

def fetch_massive_data(symbol, days):
    """Fetches data with a pagination loop to ensure full lookback."""
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        all_aggs = []
        current_to = end_date.strftime("%Y-%m-%d")
        
        # Pagination loop (Safety cap of 10 pages / 50,000 records)
        for _ in range(10):
            aggs = client.list_aggs(
                ticker=symbol,
                multiplier=1,
                timespan="day",
                from_=start_date.strftime("%Y-%m-%d"),
                to=current_to,
                limit=5000
            )
            
            if not aggs:
                break
            
            batch = list(aggs)
            all_aggs.extend(batch)
            
            # Find earliest date in this batch
            earliest_ms = min(a.timestamp for a in batch)
            earliest_dt = pd.to_datetime(earliest_ms, unit='ms').date()
            
            # Break if we've reached our target or batch is too small to have more history
            if earliest_dt <= start_date or len(batch) < 100:
                break
                
            # Move the 'to' date back 1 day for the next loop
            current_to = (earliest_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        if not all_aggs:
            return None

        df = pd.DataFrame(all_aggs)
        df['Date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('Date', inplace=True)
        df.sort_index(inplace=True) 
        
        if 'close' in df.columns:
            df.rename(columns={'close': 'Close'}, inplace=True)
            
        return df[['Close']].dropna()
    except Exception as e:
        st.error(f"Data Engine Error: {e}")
        return None
