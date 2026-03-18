import os
import logging
import pandas as pd
import requests
import streamlit as st

logger = logging.getLogger(__name__)


@st.cache_data(ttl=3600)
def fetch_eia_data(series_id: str, tail_rows: int = 156) -> pd.DataFrame:
    """Fetches timeseries data from the EIA API v2."""
    api_key = os.environ.get("EIA_API_KEY")
    if not api_key:
        try:
            api_key = st.secrets["EIA_API_KEY"]
        except Exception:
            logger.warning("EIA API key not found in env vars or secrets.")
            return None

    url = f"https://api.eia.gov/v2/seriesid/{series_id}?api_key={api_key}"

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        data = response.json()

        raw_data = data['response']['data']
        df = pd.DataFrame(raw_data)
        df['period'] = pd.to_datetime(df['period'])
        df = df.sort_values('period')
        df['value'] = pd.to_numeric(df['value'])
        df['wow_change'] = df['value'].diff()

        return df.tail(tail_rows)
    except Exception as e:
        logger.error(f"EIA API fetch failed for {series_id}: {e}")
        return None
