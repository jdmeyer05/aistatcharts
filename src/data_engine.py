import pandas as pd
import numpy as np
import os
from massive import RESTClient
from datetime import date, timedelta

# Initialize client
api_key = os.environ.get("MASSIVE_API_KEY")
client = RESTClient(api_key)

def format_massive_ticker(user_input: str) -> str:
    """Auto-prefixes tickers for user convenience."""
    t = user_input.strip().upper()
    if ":" in t or t.startswith("ERCOT."): return t
    if "-USD" in t: return f"X:{t}"
    # Detect common ERCOT patterns
    if any(x in t for x in ["HB_", "LZ_", "RT_", "DA_"]): return f"ERCOT.{t}"
    return t

def fetch_massive_data(symbol, days):
    """Fetches data with a pagination loop to ensure full lookback."""
    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        
        all_aggs = []
        # Initial 'to' date is today
        current_to = end_date.strftime("%Y-%m-%d")
        
        # Loop to ensure we get all data requested (Pagination)
        while True:
            aggs = client.list_aggs(
                ticker=symbol,
                multiplier=1,
                timespan="day",
                from_=start_date.strftime("%Y-%m-%d"),
                to=current_to,
                limit=5000
            )
            
            if not aggs: break
            
            all_aggs.extend(aggs)
            
            # Find earliest timestamp in this batch
            earliest_ms = min(a.timestamp for a in aggs)
            earliest_dt = pd.to_datetime(earliest_ms, unit='ms').date()
            
            # If we've reached our goal or no more new data is found, break
            if earliest_dt <= start_date or len(aggs) < 100:
                break
                
            # Move the 'to' date back for the next pull
            current_to = (earliest_dt - timedelta(days=1)).strftime("%Y-%m-%d")

        df = pd.DataFrame(all_aggs)
        if df.empty: return None
        
        df['Date'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('Date', inplace=True)
        df.sort_index(inplace=True) 
        df.rename(columns={'close': 'Close'}, inplace=True)
        return df[['Close']].dropna()
    except Exception:
        return None
