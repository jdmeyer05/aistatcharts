"""EIA API v2 helpers — energy supply data, Henry Hub, hourly grid monitor."""
import logging
from datetime import datetime, timedelta

import pandas as pd
import requests
import streamlit as st

logger = logging.getLogger(__name__)


def _get_eia_key() -> str | None:
    from src.api_keys import get_secret
    return get_secret("EIA_API_KEY")


@st.cache_data(ttl=3600)
def fetch_eia_data(series_id: str, tail_rows: int = 156) -> pd.DataFrame:
    """Fetches timeseries data from the EIA API v2. Uses Supabase cache (4h TTL)."""
    from src.api_cache import cached_request

    api_key = _get_eia_key()
    if not api_key:
        logger.warning("EIA API key not found in env vars or secrets.")
        return None

    try:
        data = cached_request(
            f"https://api.eia.gov/v2/seriesid/{series_id}",
            params={"api_key": api_key}, ttl=14400, timeout=30,  # 4h Supabase TTL
        )
        if not data:
            return None

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


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_henry_hub_spot() -> float | None:
    """Fetch latest Henry Hub spot price (weekly, NG.RNGWHHD.W). Supabase cached 4h."""
    from src.api_cache import cached_request
    api_key = _get_eia_key()
    if not api_key:
        return None
    try:
        raw = cached_request(
            "https://api.eia.gov/v2/seriesid/NG.RNGWHHD.W",
            params={"api_key": api_key}, ttl=14400, timeout=15,
        )
        if raw:
            data = raw.get("response", {}).get("data", [])
            if data:
                df = pd.DataFrame(data).sort_values("period")
                return float(df["value"].iloc[-1])
    except Exception as e:
        logger.error(f"EIA Henry Hub fetch failed: {e}")
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_henry_hub_daily(days_back: int = 30) -> pd.DataFrame | None:
    """Fetch daily Henry Hub spot prices (NG.RNGWHHD.D). Supabase cached 4h."""
    from src.api_cache import cached_request
    api_key = _get_eia_key()
    if not api_key:
        return None
    try:
        raw = cached_request(
            "https://api.eia.gov/v2/seriesid/NG.RNGWHHD.D",
            params={"api_key": api_key}, ttl=14400, timeout=15,
        )
        if raw:
            data = raw.get("response", {}).get("data", [])
            if data:
                df = pd.DataFrame(data).sort_values("period")
                df["period"] = pd.to_datetime(df["period"])
                df["value"] = pd.to_numeric(df["value"], errors="coerce")
                return df.tail(days_back)
    except Exception as e:
        logger.error(f"EIA daily Henry Hub fetch failed: {e}")
    return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_eia_hourly_grid(respondent: str = "ERCO", days_back: int = 31) -> pd.DataFrame | None:
    """Fetch hourly generation by fuel type from EIA Hourly Electric Grid Monitor API v2."""
    api_key = _get_eia_key()
    if not api_key:
        return None
    try:
        start_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%dT00")
        url = (
            f"https://api.eia.gov/v2/electricity/rto/fuel-type-data/data/"
            f"?api_key={api_key}"
            f"&frequency=hourly"
            f"&data[0]=value"
            f"&facets[respondent][]={respondent}"
            f"&start={start_date}"
            f"&sort[0][column]=period&sort[0][direction]=asc"
            f"&length=5000"
        )
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()["response"]["data"]
        if not data:
            return None
        df = pd.DataFrame(data)
        df["period"] = pd.to_datetime(df["period"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df
    except Exception as e:
        logger.error(f"EIA hourly grid fetch failed for {respondent}: {e}")
        return None
