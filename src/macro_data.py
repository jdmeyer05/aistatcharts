"""Extended macro data sources — free APIs for institutional-grade analysis.

Provides:
- VIX term structure (CBOE via yfinance)
- Short interest (yfinance ticker.info + FINRA)
- Fed balance sheet (FRED: WALCL, RRPONTSYD, WRESBAL)
- Treasury auction results (Treasury Fiscal Data API)
- OECD Composite Leading Indicators
- CFTC Commitments of Traders — managed money positioning
- BIS Credit-to-GDP gap — financial crisis predictor

All sources are free with no API keys required (except FRED, which is already configured).
"""
import streamlit as st
import pandas as pd
import numpy as np
import requests
import logging

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════
# VIX TERM STRUCTURE
# ═══════════════════════════════════════════════

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_skew_index() -> pd.DataFrame:
    """Fetch CBOE SKEW index — measures tail risk premium.

    SKEW = 100 + 10 * S, where S is the risk-neutral skewness.
    Normal range: 110-150. Above 150 = elevated crash fear.
    Below 120 = complacent (cheap tail hedges).

    The SKEW-VIX divergence is the key signal:
    - VIX low + SKEW high = calm surface, fear underneath → contrarian warning
    - VIX high + SKEW low = panic but not crash-specific → may be near a bottom
    """
    try:
        import yfinance as yf
        data = yf.download("^SKEW", period="1y", progress=False)
        if data is not None and not data.empty:
            if isinstance(data.columns, pd.MultiIndex):
                closes = data["Close"]
                if isinstance(closes, pd.DataFrame):
                    closes.columns = ["SKEW"]
                    return closes
                return closes.to_frame("SKEW")
            return data[["Close"]].rename(columns={"Close": "SKEW"})
    except Exception as e:
        logger.warning(f"SKEW fetch failed: {e}")
    return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_growth_proxies() -> dict:
    """Fetch real-time growth proxies: Copper/Gold ratio, Baltic Dry via yfinance.

    Copper/Gold ratio: rising = economic expansion (copper = industrial demand,
    gold = safe haven). Falling = contraction fears.
    """
    result = {}
    try:
        import yfinance as yf
        # Copper/Gold ratio
        cu = yf.download("HG=F", period="60d", progress=False)
        au = yf.download("GC=F", period="60d", progress=False)
        if cu is not None and au is not None and not cu.empty and not au.empty:
            _cu_close = cu["Close"].iloc[-1]
            _au_close = au["Close"].iloc[-1]
            _cu = float(_cu_close.iloc[0]) if hasattr(_cu_close, 'iloc') else float(_cu_close)
            _au = float(_au_close.iloc[0]) if hasattr(_au_close, 'iloc') else float(_au_close)
            if _au > 0:
                ratio = _cu / _au * 1000  # scale for readability
                result["copper_gold"] = round(ratio, 2)
                # Trend
                _cu_20_v = cu["Close"].tail(20).iloc[0]
                _au_20_v = au["Close"].tail(20).iloc[0]
                _cu_20 = float(_cu_20_v.iloc[0]) if hasattr(_cu_20_v, 'iloc') else float(_cu_20_v)
                _au_20 = float(_au_20_v.iloc[0]) if hasattr(_au_20_v, 'iloc') else float(_au_20_v)
                prev_ratio = _cu_20 / _au_20 * 1000 if _au_20 > 0 else ratio
                result["copper_gold_trend"] = "rising" if ratio > prev_ratio else "falling"
    except Exception:
        pass
    return result


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_vix_term_structure() -> pd.DataFrame:
    """Fetch VIX term structure: VIX9D, VIX, VIX3M, VIX6M, VIX1Y.

    Returns DataFrame with columns for each tenor and derived metrics:
    - vix_ratio_3m: VIX3M/VIX (>1 = contango, <1 = backwardation)
    - vix_ratio_6m: VIX6M/VIX
    - term_slope: linear slope of the term structure
    """
    try:
        import yfinance as yf
        tickers = {"VIX9D": "^VIX9D", "VIX": "^VIX", "VIX3M": "^VIX3M",
                    "VIX6M": "^VIX6M", "VIX1Y": "^VIX1Y"}
        data = yf.download(list(tickers.values()), period="1y", progress=False)
        if data is None or data.empty:
            return pd.DataFrame()
        if isinstance(data.columns, pd.MultiIndex):
            closes = data["Close"]
        else:
            closes = data[["Close"]].rename(columns={"Close": list(tickers.values())[0]})

        # Rename to friendly names
        rename = {v: k for k, v in tickers.items()}
        closes = closes.rename(columns=rename)

        # Derived metrics
        if "VIX" in closes.columns and "VIX3M" in closes.columns:
            closes["vix_ratio_3m"] = closes["VIX3M"] / closes["VIX"].replace(0, np.nan)
        if "VIX" in closes.columns and "VIX6M" in closes.columns:
            closes["vix_ratio_6m"] = closes["VIX6M"] / closes["VIX"].replace(0, np.nan)

        return closes.dropna(how="all")
    except Exception as e:
        logger.warning(f"VIX term structure fetch failed: {e}")
        return pd.DataFrame()


def get_vix_snapshot() -> dict:
    """Get current VIX term structure levels, SKEW, and regime."""
    df = fetch_vix_term_structure()
    if df.empty:
        return {}
    last = df.iloc[-1]
    result = {}
    for col in ["VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y"]:
        if col in last and pd.notna(last[col]):
            result[col] = round(float(last[col]), 2)

    # Add SKEW
    skew_df = fetch_skew_index()
    if not skew_df.empty:
        result["SKEW"] = round(float(skew_df["SKEW"].iloc[-1]), 1)

    vix = result.get("VIX", 0)
    vix3m = result.get("VIX3M", 0)
    skew = result.get("SKEW", 0)
    if vix > 0 and vix3m > 0:
        result["ratio_3m"] = round(vix3m / vix, 3)
        result["contango"] = vix3m > vix
        result["regime"] = (
            "Steep Contango" if vix3m / vix > 1.10 else
            "Contango" if vix3m / vix > 1.02 else
            "Flat" if vix3m / vix > 0.98 else
            "Backwardation" if vix3m / vix > 0.90 else
            "Steep Backwardation"
        )

    # VIX-SKEW divergence signal
    if vix > 0 and skew > 0:
        if vix < 18 and skew > 145:
            result["divergence"] = "VIX low / SKEW high — calm surface, hidden crash fear"
        elif vix > 25 and skew < 125:
            result["divergence"] = "VIX high / SKEW low — broad panic but not tail-specific, may be near bottom"
        elif skew > 150:
            result["divergence"] = "Extreme tail risk premium — market heavily hedging crashes"
        elif skew < 115:
            result["divergence"] = "Complacent — tail hedges are cheap, smart money accumulates protection"

    return result


# ═══════════════════════════════════════════════
# SHORT INTEREST
# ═══════════════════════════════════════════════

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_short_interest(ticker: str) -> dict:
    """Fetch short interest metrics for a ticker via yfinance.

    Returns dict with:
    - short_ratio (days to cover)
    - short_pct_float (% of float sold short)
    - short_pct_shares (% of shares outstanding)
    - short_shares (total shares short)
    """
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        return {
            "short_ratio": info.get("shortRatio"),
            "short_pct_float": info.get("shortPercentOfFloat"),
            "short_pct_shares": info.get("heldPercentInsiders"),
            "short_shares": info.get("sharesShort"),
            "short_prior": info.get("sharesShortPriorMonth"),
            "short_date": info.get("dateShortInterest"),
        }
    except Exception as e:
        logger.warning(f"Short interest fetch failed for {ticker}: {e}")
        return {}


# ═══════════════════════════════════════════════
# FED BALANCE SHEET (via FRED)
# ═══════════════════════════════════════════════

FED_BALANCE_SERIES = {
    "WALCL": "Total Assets",
    "WTREGEN": "Treasury General Account",
    "RRPONTSYD": "Reverse Repo (ON)",
    "WRESBAL": "Reserve Balances",
    "WSHOSHO": "Treasury Securities Held",
    "WSHOMCB": "MBS Held",
}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fed_balance_sheet() -> pd.DataFrame:
    """Fetch Fed balance sheet components from FRED.

    Returns DataFrame with weekly data for major balance sheet items.
    Requires FRED_API_KEY in secrets.
    """
    try:
        from src.api_keys import get_secret
        from src.market_data import fetch_fred_series
        fred_key = get_secret("FRED_API_KEY")
        if not fred_key:
            return pd.DataFrame()

        rows = {}
        for sid, label in FED_BALANCE_SERIES.items():
            try:
                df = fetch_fred_series(sid, periods=104)  # ~2 years weekly
                if not df.empty:
                    rows[label] = df.set_index("date")["value"]
            except Exception:
                pass

        if rows:
            result = pd.DataFrame(rows)
            return result.sort_index()
        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"Fed balance sheet fetch failed: {e}")
        return pd.DataFrame()


def get_fed_liquidity_snapshot() -> dict:
    """Compute net liquidity = Total Assets - TGA - Reverse Repo."""
    df = fetch_fed_balance_sheet()
    if df.empty:
        return {}
    last = df.iloc[-1]
    prev = df.iloc[-5] if len(df) > 5 else last  # ~1 month ago

    total = last.get("Total Assets", 0) or 0
    tga = last.get("Treasury General Account", 0) or 0
    rrp = last.get("Reverse Repo (ON)", 0) or 0
    net_liq = total - tga - rrp

    total_prev = prev.get("Total Assets", 0) or 0
    tga_prev = prev.get("Treasury General Account", 0) or 0
    rrp_prev = prev.get("Reverse Repo (ON)", 0) or 0
    net_liq_prev = total_prev - tga_prev - rrp_prev

    return {
        "total_assets": round(total / 1e6, 1) if total > 1e6 else total,  # in trillions
        "tga": round(tga / 1e3, 1) if tga > 1e3 else tga,  # in billions
        "rrp": round(rrp / 1e3, 1) if rrp > 1e3 else rrp,
        "net_liquidity": round(net_liq / 1e6, 2) if net_liq > 1e6 else net_liq,
        "net_liq_change": round((net_liq - net_liq_prev) / 1e3, 1) if abs(net_liq - net_liq_prev) > 1e3 else 0,
        "draining": net_liq < net_liq_prev,
    }


# ═══════════════════════════════════════════════
# TREASURY AUCTION RESULTS
# ═══════════════════════════════════════════════

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_treasury_auctions(days_back: int = 90) -> pd.DataFrame:
    """Fetch recent Treasury auction results from Treasury Fiscal Data API.

    Returns DataFrame with auction date, security type, bid-to-cover, yield, etc.
    """
    try:
        from datetime import datetime, timedelta
        start = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        url = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query"
        params = {
            "filter": f"auction_date:gte:{start}",
            "sort": "-auction_date",
            "page[size]": 100,
            "format": "json",
        }
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        # Clean up columns
        for col in ["high_investment_rate", "bid_to_cover_ratio", "total_accepted"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "auction_date" in df.columns:
            df["auction_date"] = pd.to_datetime(df["auction_date"])
        return df
    except Exception as e:
        logger.warning(f"Treasury auction fetch failed: {e}")
        return pd.DataFrame()


# ═══════════════════════════════════════════════
# OECD COMPOSITE LEADING INDICATORS
# ═══════════════════════════════════════════════

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_oecd_cli(countries: list = None) -> pd.DataFrame:
    """Fetch OECD Composite Leading Indicators.

    CLI > 100 = expansion, CLI < 100 = contraction.
    The turning points in CLI lead GDP by 6-9 months.

    Returns DataFrame with columns per country, indexed by date.
    """
    if countries is None:
        countries = ["USA", "GBR", "DEU", "JPN", "CHN", "OECD"]
    try:
        url = (
            "https://sdmx.oecd.org/public/rest/data/"
            "OECD.SDD.STES,DSD_STES@DF_CLI/.M.LI...AA...H"
            "?startPeriod=2020-01&format=csvfilewithlabels"
        )
        df = pd.read_csv(url, low_memory=False)
        if df.empty:
            return pd.DataFrame()

        # Parse the SDMX CSV format
        result = {}
        ref_area_col = "REF_AREA" if "REF_AREA" in df.columns else df.columns[0]
        time_col = "TIME_PERIOD" if "TIME_PERIOD" in df.columns else "Time"
        value_col = "OBS_VALUE" if "OBS_VALUE" in df.columns else "Value"

        for country in countries:
            sub = df[df[ref_area_col] == country]
            if not sub.empty:
                series = sub.set_index(time_col)[value_col].astype(float)
                series.index = pd.to_datetime(series.index, format="%Y-%m", errors="coerce")
                result[country] = series.sort_index()

        if result:
            return pd.DataFrame(result).sort_index()
        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"OECD CLI fetch failed: {e}")
        return pd.DataFrame()


# ═══════════════════════════════════════════════
# CFTC COMMITMENTS OF TRADERS
# ═══════════════════════════════════════════════

# Key commodity contract codes for COT data
COT_CONTRACTS = {
    "Crude Oil": "067651",
    "Natural Gas": "023651",
    "Gold": "088691",
    "Silver": "084691",
    "Copper": "085692",
    "Corn": "002602",
    "Soybeans": "005602",
    "Wheat": "001602",
    "S&P 500": "13874A",
    "Nasdaq 100": "20974A",
    "10-Year Notes": "043602",
    "US Dollar Index": "098662",
    "Euro FX": "099741",
    "Yen": "097741",
    "VIX Futures": "1170E1",
}


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_cot_managed_money(contract_name: str = None) -> pd.DataFrame:
    """Fetch CFTC Disaggregated COT data for managed money positioning.

    Uses the CFTC Public Reporting API (Socrata).
    Returns DataFrame with date, long, short, net, and % of OI.
    """
    try:
        # Disaggregated Futures Only dataset
        url = "https://publicreporting.cftc.gov/resource/72hh-3qpy.json"
        params = {
            "$limit": 5000,
            "$order": "report_date_as_yyyy_mm_dd DESC",
        }
        if contract_name and contract_name in COT_CONTRACTS:
            params["cftc_contract_market_code"] = COT_CONTRACTS[contract_name]

        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data:
            return pd.DataFrame()

        rows = []
        for entry in data:
            try:
                rows.append({
                    "date": pd.to_datetime(entry.get("report_date_as_yyyy_mm_dd")),
                    "contract": entry.get("contract_market_name", ""),
                    "mm_long": int(entry.get("m_money_positions_long_all", 0)),
                    "mm_short": int(entry.get("m_money_positions_short_all", 0)),
                    "mm_spread": int(entry.get("m_money_positions_spread_all", 0)),
                    "oi": int(entry.get("open_interest_all", 0)),
                })
            except (ValueError, TypeError):
                continue

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["mm_net"] = df["mm_long"] - df["mm_short"]
        df["mm_net_pct_oi"] = df["mm_net"] / df["oi"].replace(0, np.nan) * 100
        return df.sort_values("date")
    except Exception as e:
        logger.warning(f"CFTC COT fetch failed: {e}")
        return pd.DataFrame()


def get_cot_positioning_snapshot() -> dict:
    """Get latest managed money positioning for key commodities."""
    result = {}
    for name in ["Crude Oil", "Gold", "S&P 500", "10-Year Notes", "VIX Futures"]:
        df = fetch_cot_managed_money(name)
        if not df.empty:
            last = df.iloc[-1]
            prev = df.iloc[-5] if len(df) > 5 else last
            result[name] = {
                "net": int(last["mm_net"]),
                "net_pct_oi": round(float(last["mm_net_pct_oi"]), 1),
                "change": int(last["mm_net"] - prev["mm_net"]),
                "direction": "Long" if last["mm_net"] > 0 else "Short",
            }
    return result


# ═══════════════════════════════════════════════
# BIS CREDIT-TO-GDP GAP
# ═══════════════════════════════════════════════

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_bis_credit_gap(countries: list = None) -> pd.DataFrame:
    """Fetch BIS credit-to-GDP gap data.

    The credit-to-GDP gap is the single best early warning indicator for
    financial crises (2-3 year horizon). Basel III uses it for setting
    countercyclical capital buffers.

    Gap > 10: elevated risk. Gap > 2: above trend. Gap < -2: below trend.

    Returns DataFrame with columns per country, indexed by quarterly date.
    """
    if countries is None:
        countries = ["US", "GB", "DE", "JP", "CN", "FR"]
    try:
        # BIS SDMX REST API for credit-to-GDP gap
        url = "https://stats.bis.org/api/v2/data/dataflow/BIS/WS_CREDIT_GAP/1.0"
        params = {
            "startPeriod": "2015-Q1",
            "format": "csv",
        }
        r = requests.get(url, params=params, timeout=20)
        if r.status_code != 200:
            # Fallback: try the older API format
            return pd.DataFrame()

        from io import StringIO
        df = pd.read_csv(StringIO(r.text), low_memory=False)
        if df.empty:
            return pd.DataFrame()

        # Parse BIS CSV format
        result = {}
        ref_col = [c for c in df.columns if "REF" in c.upper() or "COUNTRY" in c.upper()]
        time_col = [c for c in df.columns if "TIME" in c.upper() or "PERIOD" in c.upper()]
        val_col = [c for c in df.columns if "OBS" in c.upper() or "VALUE" in c.upper()]

        if ref_col and time_col and val_col:
            for country in countries:
                sub = df[df[ref_col[0]].astype(str).str.contains(country, case=False, na=False)]
                if not sub.empty:
                    series = pd.to_numeric(sub[val_col[0]], errors="coerce")
                    try:
                        series.index = pd.PeriodIndex(sub[time_col[0]], freq="Q").to_timestamp()
                    except Exception:
                        # Fallback: try parsing as dates directly
                        series.index = pd.to_datetime(sub[time_col[0]], errors="coerce")
                    series = series.dropna()
                    if not series.empty:
                        result[country] = series.sort_index()

        if result:
            return pd.DataFrame(result).sort_index()
        return pd.DataFrame()
    except Exception as e:
        logger.warning(f"BIS credit gap fetch failed: {e}")
        return pd.DataFrame()
