"""
ERCOT Public API client with Azure B2C token management.

Provides access to official ERCOT market data:
- Actual system load by weather zone (NP6-345-CD)
- Wind generation actual + forecast (NP4-732-CD)
- Solar generation actual + forecast (NP4-737-CD)
- Real-time settlement point prices (NP6-905-CD)
- Day-ahead settlement point prices (NP4-190-CD)

Filter syntax: searchable fields use {fieldName}From / {fieldName}To for ranges.
"""

import logging
import time
import requests
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

# Azure B2C token endpoint and client ID (public, per ERCOT docs)
_TOKEN_URL = (
    "https://ercotb2c.b2clogin.com/ercotb2c.onmicrosoft.com/"
    "B2C_1_PUBAPI-ROPC-FLOW/oauth2/v2.0/token"
)
_CLIENT_ID = "fec253ea-0d06-4272-a5e6-b478baeecd70"
_API_BASE = "https://api.ercot.com/api/public-reports"

# Module-level token cache
_token_cache = {"id_token": None, "expires_at": 0}


def _get_credentials():
    """Retrieve ERCOT API credentials from Streamlit secrets or env."""
    import os
    sub_key = os.environ.get("ERCOT_API_KEY")
    username = os.environ.get("ERCOT_API_USERNAME")
    password = os.environ.get("ERCOT_API_PASSWORD")
    if not sub_key:
        try:
            sub_key = st.secrets["ERCOT_API_KEY"]
            username = st.secrets["ERCOT_API_USERNAME"]
            password = st.secrets["ERCOT_API_PASSWORD"]
        except Exception:
            pass
    return sub_key, username, password


def _get_token() -> str | None:
    """Get a valid ID token, refreshing if expired (tokens last 1 hour)."""
    now = time.time()
    if _token_cache["id_token"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["id_token"]

    _, username, password = _get_credentials()
    if not username or not password:
        logger.warning("ERCOT API credentials not configured.")
        return None

    try:
        r = requests.post(_TOKEN_URL, data={
            "username": username,
            "password": password,
            "grant_type": "password",
            "client_id": _CLIENT_ID,
            "scope": f"openid {_CLIENT_ID} offline_access",
            "response_type": "id_token",
        }, timeout=15)
        r.raise_for_status()
        tokens = r.json()
        _token_cache["id_token"] = tokens["id_token"]
        _token_cache["expires_at"] = now + int(tokens.get("expires_in", 3600))
        return _token_cache["id_token"]
    except Exception as e:
        logger.error(f"ERCOT token auth failed: {e}")
        return None


def _headers() -> dict | None:
    """Build request headers with auth token and subscription key."""
    sub_key, _, _ = _get_credentials()
    token = _get_token()
    if not token or not sub_key:
        return None
    return {
        "Authorization": f"Bearer {token}",
        "Ocp-Apim-Subscription-Key": sub_key,
    }


def _fetch_report(artifact_path: str, params: dict | None = None,
                   timeout: int = 30) -> list | None:
    """Fetch data rows from an ERCOT API artifact endpoint.

    Returns list of dicts with field names as keys, or None on failure.
    """
    hdrs = _headers()
    if not hdrs:
        return None

    url = f"{_API_BASE}/{artifact_path}"
    all_params = {"size": 1000}
    if params:
        all_params.update(params)

    try:
        r = requests.get(url, headers=hdrs, params=all_params, timeout=timeout)
        r.raise_for_status()
        d = r.json()
        fields = [f["name"] for f in d.get("fields", [])]
        raw_rows = d.get("data", [])
        if not fields or not raw_rows:
            return None
        return [dict(zip(fields, row)) for row in raw_rows]
    except Exception as e:
        logger.error(f"ERCOT API fetch failed for {artifact_path}: {e}")
        return None


def is_available() -> bool:
    """Check if ERCOT API credentials are configured."""
    sub_key, username, password = _get_credentials()
    return bool(sub_key and username and password)


# ════════════════════════════════════════════════
# PUBLIC DATA FUNCTIONS
# ════════════════════════════════════════════════

@st.cache_data(ttl=300)
def fetch_actual_load(date_from: str | None = None,
                       date_to: str | None = None) -> pd.DataFrame | None:
    """Fetch actual system load by weather zone (NP6-345-CD).

    Args:
        date_from: Start date 'YYYY-MM-DD'. None = latest available.
        date_to: End date 'YYYY-MM-DD'. None = same as date_from.

    Returns DataFrame with columns:
        operatingDay, hourEnding, coast, east, farWest, north, northC,
        southern, southC, west, total
    """
    params = {}
    if date_from:
        params["operatingDayFrom"] = date_from
        params["operatingDayTo"] = date_to or date_from
    rows = _fetch_report("np6-345-cd/act_sys_load_by_wzn", params)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in ["coast", "east", "farWest", "north", "northC", "southern", "southC", "west", "total"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=300)
def fetch_solar_hourly(date_from: str | None = None,
                        date_to: str | None = None) -> pd.DataFrame | None:
    """Fetch solar generation actual + forecast (NP4-737-CD).

    Returns DataFrame with:
        postedDatetime, deliveryDate, hourEnding, genSystemWide (actual MW),
        COPHSLSystemWide (capacity), STPPFSystemWide (short-term forecast),
        PVGRPPSystemWide (PVGRPP forecast), HSLSystemWide
    """
    params = {}
    if date_from:
        params["deliveryDateFrom"] = date_from
        params["deliveryDateTo"] = date_to or date_from
    rows = _fetch_report("np4-737-cd/spp_hrly_avrg_actl_fcast", params)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in ["genSystemWide", "COPHSLSystemWide", "STPPFSystemWide", "PVGRPPSystemWide", "HSLSystemWide"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["hourEnding"] = pd.to_numeric(df["hourEnding"], errors="coerce")
    return df


@st.cache_data(ttl=300)
def fetch_wind_hourly(date_from: str | None = None,
                       date_to: str | None = None) -> pd.DataFrame | None:
    """Fetch wind generation actual + forecast (NP4-732-CD).

    Returns DataFrame with:
        postedDatetime, deliveryDate, hourEnding, genSystemWide (actual MW),
        COPHSLSystemWide (capacity), STWPFSystemWide (short-term forecast),
        WGRPPSystemWide (WGRPP forecast) + regional breakdowns
    """
    params = {}
    if date_from:
        params["deliveryDateFrom"] = date_from
        params["deliveryDateTo"] = date_to or date_from
    rows = _fetch_report("np4-732-cd/wpp_hrly_avrg_actl_fcast", params)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    numeric_cols = [c for c in df.columns if c not in ("postedDatetime", "deliveryDate", "DSTFlag")]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=300)
def fetch_rt_spp(date_from: str | None = None, date_to: str | None = None,
                  settlement_point: str = "HB_HUBAVG") -> pd.DataFrame | None:
    """Fetch real-time settlement point prices (NP6-905-CD).

    Args:
        settlement_point: e.g. 'HB_HUBAVG', 'HB_HOUSTON', 'HB_NORTH', 'LZ_HOUSTON'

    Returns DataFrame with: deliveryDate, hourEnding, interval,
        settlementPoint, settlementPointType, settlementPointPrice
    """
    params = {"settlementPoint": settlement_point}
    if date_from:
        params["deliveryDateFrom"] = date_from
        params["deliveryDateTo"] = date_to or date_from
    rows = _fetch_report("np6-905-cd/spp_node_zone_hub", params, timeout=45)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["settlementPointPrice"] = pd.to_numeric(df["settlementPointPrice"], errors="coerce")
    df["deliveryHour"] = pd.to_numeric(df["deliveryHour"], errors="coerce")
    df["deliveryInterval"] = pd.to_numeric(df["deliveryInterval"], errors="coerce")
    return df


@st.cache_data(ttl=600)
def fetch_dam_spp(date_from: str | None = None, date_to: str | None = None,
                   settlement_point: str = "HB_HUBAVG") -> pd.DataFrame | None:
    """Fetch day-ahead settlement point prices (NP4-190-CD).

    Returns DataFrame with: deliveryDate, hourEnding,
        settlementPoint, settlementPointPrice
    """
    params = {"settlementPoint": settlement_point}
    if date_from:
        params["deliveryDateFrom"] = date_from
        params["deliveryDateTo"] = date_to or date_from
    rows = _fetch_report("np4-190-cd/dam_stlmnt_pnt_prices", params)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["settlementPointPrice"] = pd.to_numeric(df["settlementPointPrice"], errors="coerce")
    return df


@st.cache_data(ttl=300)
def fetch_sced_lambda(date_from: str | None = None,
                       date_to: str | None = None) -> pd.DataFrame | None:
    """Fetch SCED System Lambda — real-time marginal price (NP6-322-CD).

    Returns DataFrame with: SCEDTimestamp, repeatHourFlag, systemLambda
    System Lambda = marginal energy offer price clearing the market each SCED interval (~5 min).
    """
    params = {}
    if date_from:
        params["SCEDTimestampFrom"] = f"{date_from}T00:00:00"
        params["SCEDTimestampTo"] = f"{date_to or date_from}T23:59:59"
    rows = _fetch_report("np6-322-cd/sced_system_lambda", params, timeout=45)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["SCEDTimestamp"] = pd.to_datetime(df["SCEDTimestamp"])
    df["systemLambda"] = pd.to_numeric(df["systemLambda"], errors="coerce")
    return df


@st.cache_data(ttl=600)
def fetch_dam_lambda(date_from: str | None = None,
                      date_to: str | None = None) -> pd.DataFrame | None:
    """Fetch DAM System Lambda — day-ahead marginal price (NP4-523-CD).

    Returns DataFrame with: deliveryDate, hourEnding, systemLambda
    """
    params = {}
    if date_from:
        params["deliveryDateFrom"] = date_from
        params["deliveryDateTo"] = date_to or date_from
    rows = _fetch_report("np4-523-cd/dam_system_lambda", params)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    df["systemLambda"] = pd.to_numeric(df["systemLambda"], errors="coerce")
    return df


@st.cache_data(ttl=3600)
def fetch_load_history(days_back: int = 30) -> pd.DataFrame | None:
    """Fetch historical actual load for multi-day analysis."""
    from datetime import datetime, timedelta
    start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    params = {"operatingDayFrom": start}
    rows = _fetch_report("np6-345-cd/act_sys_load_by_wzn", params, timeout=45)
    if not rows:
        return None
    df = pd.DataFrame(rows)
    for col in ["coast", "east", "farWest", "north", "northC", "southern", "southC", "west", "total"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df
