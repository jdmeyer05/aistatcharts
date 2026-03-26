"""Government data sources — all public domain, no API keys required.
CFTC COT, Treasury yield curve, USASpending defense contracts."""

import logging
import pandas as pd
import requests
import streamlit as st
from datetime import date, timedelta

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 1. CFTC COMMITMENTS OF TRADERS (COT)
# ─────────────────────────────────────────────

# Key futures contracts to track — CFTC code: label
COT_CONTRACTS = {
    "067651": "Crude Oil (WTI)",
    "088691": "Natural Gas",
    "084691": "Gold",
    "084692": "Silver",
    "099741": "US Dollar Index",
    "043602": "S&P 500 (E-mini)",
    "044601": "Nasdaq 100 (E-mini)",
    "020601": "US Treasury Bonds",
    "042601": "10-Year T-Notes",
    "001602": "Wheat",
    "002602": "Corn",
}


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_cot_data(limit: int = 10) -> pd.DataFrame:
    """Fetch latest CFTC COT data across ALL tracked contracts (JSON API).
    Returns multi-contract snapshot for AI context.
    For single-commodity time series charts, use src.market_data.fetch_cftc_cot() instead."""
    try:
        # CFTC publishes weekly — use the Quandl-style CFTC API (now on data.gov)
        url = "https://publicreporting.cftc.gov/api/views/deus-9w32/rows.csv?accessType=DOWNLOAD"
        # This is a large file — try the JSON API instead for recent data
        api_url = "https://publicreporting.cftc.gov/resource/deus-9w32.json"
        params = {
            "$order": "report_date_as_yyyy_mm_dd DESC",
            "$limit": 500,
            "$where": f"report_date_as_yyyy_mm_dd > '{(date.today() - timedelta(days=30)).isoformat()}'",
        }
        r = requests.get(api_url, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
        if not data:
            return pd.DataFrame()

        rows = []
        for entry in data:
            code = entry.get("cftc_contract_market_code", "")
            if code not in COT_CONTRACTS:
                continue
            rows.append({
                "date": entry.get("report_date_as_yyyy_mm_dd", ""),
                "contract": COT_CONTRACTS[code],
                "code": code,
                "commercial_long": int(entry.get("comm_positions_long_all", 0)),
                "commercial_short": int(entry.get("comm_positions_short_all", 0)),
                "noncommercial_long": int(entry.get("noncomm_positions_long_all", 0)),
                "noncommercial_short": int(entry.get("noncomm_positions_short_all", 0)),
                "total_oi": int(entry.get("open_interest_all", 0)),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df["spec_net"] = df["noncommercial_long"] - df["noncommercial_short"]
        df["comm_net"] = df["commercial_long"] - df["commercial_short"]
        df = df.sort_values(["contract", "date"])
        return df

    except Exception as e:
        logger.warning(f"CFTC COT fetch failed: {e}")
        return pd.DataFrame()


def get_cot_summary() -> list:
    """Get the latest COT positioning for each tracked contract.
    Returns list of dicts for AI context injection."""
    df = fetch_cot_data()
    if df.empty:
        return []

    summary = []
    for contract in df["contract"].unique():
        latest = df[df["contract"] == contract].iloc[-1]
        spec_net = latest["spec_net"]
        comm_net = latest["comm_net"]
        oi = latest["total_oi"]
        spec_pct = round(spec_net / oi * 100, 1) if oi > 0 else 0

        signal = "net long" if spec_net > 0 else "net short"
        summary.append({
            "contract": contract,
            "date": latest["date"].strftime("%Y-%m-%d"),
            "spec_net": int(spec_net),
            "comm_net": int(comm_net),
            "spec_pct_oi": spec_pct,
            "signal": f"Speculators {signal} ({spec_pct:+.1f}% of OI)",
        })
    return summary


# ─────────────────────────────────────────────
# 2. TREASURY YIELD CURVE + AUCTION DATA
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_treasury_yields() -> dict:
    """Fetch the latest Treasury yield curve from api.fiscaldata.treasury.gov."""
    try:
        end = date.today().isoformat()
        start = (date.today() - timedelta(days=7)).isoformat()
        r = requests.get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v2/accounting/od/avg_interest_rates",
            params={
                "filter": f"record_date:gte:{start}",
                "sort": "-record_date",
                "page[size]": "50",
            },
            timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("data", [])
        if not records:
            return {}

        # Also try the daily Treasury rates endpoint
        r2 = requests.get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/rates_of_exchange",
            timeout=10,
        )
    except Exception:
        pass

    # Fallback: use the Treasury XML feed for daily yield curve
    try:
        r = requests.get(
            "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/all/2026",
            params={"type": "daily_treasury_yield_curve", "page&field_tdr_date_value": "2026"},
            timeout=15,
        )
        if r.status_code == 200 and r.text:
            from io import StringIO
            df = pd.read_csv(StringIO(r.text))
            if not df.empty:
                latest = df.iloc[-1]
                tenors = {}
                for col in df.columns:
                    if "Mo" in col or "Yr" in col:
                        try:
                            tenors[col.strip()] = float(latest[col])
                        except (ValueError, TypeError):
                            pass
                if tenors:
                    return {
                        "date": str(latest.get("Date", date.today().isoformat())),
                        "yields": tenors,
                    }
    except Exception as e:
        logger.warning(f"Treasury yield curve fetch failed: {e}")

    return {}


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_treasury_auctions(days: int = 30) -> list:
    """Fetch recent Treasury auction results."""
    try:
        start = (date.today() - timedelta(days=days)).isoformat()
        r = requests.get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/auctions_query",
            params={
                "filter": f"auction_date:gte:{start}",
                "sort": "-auction_date",
                "page[size]": "20",
            },
            timeout=15,
        )
        r.raise_for_status()
        records = r.json().get("data", [])
        results = []
        for rec in records:
            results.append({
                "date": rec.get("auction_date", ""),
                "security": rec.get("security_type", "") + " " + rec.get("security_term", ""),
                "high_yield": rec.get("high_yield", ""),
                "bid_to_cover": rec.get("bid_to_cover_ratio", ""),
                "amount": rec.get("offering_amt", ""),
            })
        return results
    except Exception as e:
        logger.warning(f"Treasury auctions fetch failed: {e}")
        return []


# ─────────────────────────────────────────────
# 3. USASPENDING.GOV — DEFENSE CONTRACTS
# ─────────────────────────────────────────────

DEFENSE_CONTRACTORS = {
    "Lockheed Martin": "LOCKHEED MARTIN",
    "RTX (Raytheon)": "RAYTHEON",
    "Northrop Grumman": "NORTHROP GRUMMAN",
    "General Dynamics": "GENERAL DYNAMICS",
    "Boeing": "BOEING",
    "L3Harris": "L3HARRIS",
    "BAE Systems": "BAE SYSTEMS",
}


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_defense_contracts(days: int = 30) -> pd.DataFrame:
    """Fetch recent DOD contract awards from USASpending.gov."""
    try:
        start = (date.today() - timedelta(days=days)).isoformat()
        r = requests.post(
            "https://api.usaspending.gov/api/v2/search/spending_by_award/",
            json={
                "filters": {
                    "time_period": [{"start_date": start, "end_date": date.today().isoformat()}],
                    "agencies": [{"type": "awarding", "tier": "toptier", "name": "Department of Defense"}],
                    "award_type_codes": ["A", "B", "C", "D"],
                },
                "fields": ["Award ID", "Recipient Name", "Award Amount", "Start Date",
                           "Awarding Agency", "Awarding Sub Agency", "Description"],
                "page": 1,
                "limit": 100,
                "sort": "Award Amount",
                "order": "desc",
            },
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        if not results:
            return pd.DataFrame()

        rows = []
        for award in results:
            rows.append({
                "date": award.get("Start Date", ""),
                "recipient": award.get("Recipient Name", ""),
                "amount": award.get("Award Amount", 0),
                "agency": award.get("Awarding Agency", ""),
                "sub_agency": award.get("Awarding Sub Agency", ""),
                "description": (award.get("Description", "") or "")[:200],
            })
        return pd.DataFrame(rows)
    except Exception as e:
        logger.warning(f"USASpending defense contracts fetch failed: {e}")
        return pd.DataFrame()


def get_defense_contract_summary(days: int = 30) -> dict:
    """Summarize defense contract awards for AI context injection."""
    df = fetch_defense_contracts(days)
    if df.empty:
        return {}

    total = df["amount"].sum()

    # Match to known contractors
    contractor_totals = {}
    for label, search_term in DEFENSE_CONTRACTORS.items():
        mask = df["recipient"].str.upper().str.contains(search_term, na=False)
        if mask.any():
            contractor_totals[label] = round(df.loc[mask, "amount"].sum())

    # Sub-agency breakdown
    agency_totals = {}
    if "sub_agency" in df.columns:
        for agency, group in df.groupby("sub_agency"):
            if agency and group["amount"].sum() > 1_000_000:
                agency_totals[agency] = round(group["amount"].sum())

    # Top 5 individual awards
    top_awards = []
    for _, row in df.head(5).iterrows():
        top_awards.append({
            "recipient": row.get("recipient", ""),
            "amount": row.get("amount", 0),
            "description": row.get("description", ""),
        })

    return {
        "total_awarded": round(total),
        "days": days,
        "n_awards": len(df),
        "by_contractor": contractor_totals,
        "by_agency": agency_totals,
        "top_awards": top_awards,
    }
