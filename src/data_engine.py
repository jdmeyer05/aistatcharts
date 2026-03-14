import streamlit as st
import pandas as pd
import yfinance as yf
import os
import logging
from supabase import create_client
from massive import RESTClient
from datetime import date, timedelta

# Create a logger specific to this file
logger = logging.getLogger(__name__)

def get_clients():
    logger.info("Initializing API clients...")
    return (
        create_client(os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_KEY")),
        RESTClient(os.environ.get("MASSIVE_API_KEY"))
    )

def format_massive_ticker(user_input: str) -> str:
    if not user_input: return ""
    t = user_input.strip().upper()
    if ":" in t or t.startswith("ERCOT."): return t
    if "-USD" in t: return f"X:{t}"
    if any(x in t for x in ["HB_", "LZ_", "RT_", "DA_"]): return f"ERCOT.{t}"
    return t

def fetch_massive_data(symbol, days):
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    formatted_symbol = format_massive_ticker(symbol)
    
    logger.info(f"Fetching data for {formatted_symbol} ({days} days)")

    # 1. Supabase Check
    try:
        sb, _ = get_clients()
        res = sb.table("price_data").select("*").eq("ticker", formatted_symbol).gte("timestamp", start_date.isoformat()).execute()
        if res.data:
            logger.info(f"Cache hit in Supabase for {formatted_symbol}")
            df = pd.DataFrame(res.data)
            df['Date'] = pd.to_datetime(df['timestamp']).dt.tz_localize(None)
            df.set_index('Date', inplace=True)
            return df[['close_price']].rename(columns={'close_price': 'Close'})
    except Exception:
        logger.warning("Supabase fetch failed or table empty.")

    # 2. Massive API
    try:
        _, mc = get_clients()
        logger.info(f"Querying Massive API for {formatted_symbol}")
        aggs = mc.list_aggs(ticker=formatted_symbol, multiplier=1, timespan="day", from_=start_date.strftime("%Y-%m-%d"), to=end_date.strftime("%Y-%m-%d"))
        if aggs:
            df = pd.DataFrame(list(aggs))
            logger.info("Massive API success.")
            # ... (Rest of processing)
            return df
    except Exception as e:
        logger.error(f"Massive API failed: {e}")

    # 3. yfinance Fallback
    try:
        logger.info("Attempting yfinance fallback...")
        yahoo_ticker = formatted_symbol.replace("X:", "").replace("ERCOT.", "")
        df_yf = yf.download(yahoo_ticker, start=start_date, end=end_date, progress=False)
        if not df_yf.empty:
            logger.info("yfinance success.")
            return df_yf[['Close']]
    except Exception as e:
        logger.exception(f"Critical failure: All sources failed for {formatted_symbol}")
    
    return None

def fetch_options_chain(underlying_symbol, expiration_date=None):
    logger.info(f"Fetching options chain for {underlying_symbol}")
    try:
        api_key = os.environ.get("MASSIVE_API_KEY")
        mc = RESTClient(api_key)
        formatted_sym = underlying_symbol.replace("X:", "").replace("ERCOT.", "").upper()
        response = mc.reference_options_contracts(underlying_ticker=formatted_sym, limit=1000)
        if response:
            logger.info(f"Successfully retrieved {len(response)} contracts.")
            return pd.DataFrame(response)
    except Exception as e:
        logger.exception("Options chain fetch failed.")
    return None
