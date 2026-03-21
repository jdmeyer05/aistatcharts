"""SEC EDGAR API helpers — public domain data, zero legal risk.
All requests include required User-Agent header per SEC policy."""

import os
import logging
import requests
import pandas as pd
import streamlit as st
from datetime import date, timedelta

logger = logging.getLogger(__name__)

# SEC requires identification
_SEC_USER_AGENT = "AIStatcharts/2.1 (jdmeyer05@gmail.com)"
_SEC_HEADERS = {"User-Agent": _SEC_USER_AGENT, "Accept": "application/json"}

# Rate limit: 10 req/sec — we rely on Streamlit caching to stay well under this


# ─────────────────────────────────────────────
# CIK LOOKUP (ticker → CIK mapping)
# ─────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def _load_cik_map() -> dict:
    """Load SEC ticker → CIK mapping. Cached 24 hours."""
    try:
        r = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=_SEC_HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        # Build ticker → CIK map
        return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}
    except Exception as e:
        logger.error(f"Failed to load CIK map: {e}")
        return {}


def ticker_to_cik(ticker: str) -> str | None:
    """Convert a stock ticker to SEC CIK number."""
    cik_map = _load_cik_map()
    return cik_map.get(ticker.upper())


# ─────────────────────────────────────────────
# XBRL COMPANY FACTS (structured financials)
# ─────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_company_facts(ticker: str) -> dict:
    """Fetch all XBRL financial facts for a company from SEC."""
    cik = ticker_to_cik(ticker)
    if not cik:
        return {}
    try:
        r = requests.get(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
            headers=_SEC_HEADERS, timeout=15,
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning(f"XBRL company facts failed for {ticker} (CIK {cik}): {e}")
        return {}


def extract_xbrl_metric(facts: dict, metric: str, taxonomy: str = "us-gaap", unit: str = "USD") -> pd.DataFrame:
    """Extract a specific metric from XBRL company facts.
    Returns DataFrame with columns: end, val, form, filed."""
    try:
        data = facts.get("facts", {}).get(taxonomy, {}).get(metric, {}).get("units", {}).get(unit, [])
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        # Keep only annual (10-K) and quarterly (10-Q)
        df = df[df["form"].isin(["10-K", "10-Q"])]
        df["end"] = pd.to_datetime(df["end"])
        df = df.sort_values("end").drop_duplicates(subset=["end", "form"], keep="last")
        return df[["end", "val", "form", "filed"]].tail(20)
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=86400, show_spinner=False)
def calculate_financial_ratios(ticker: str) -> dict:
    """Calculate key financial ratios from XBRL data."""
    facts = fetch_company_facts(ticker)
    if not facts:
        return {}

    def _latest(metric, tax="us-gaap", unit="USD"):
        df = extract_xbrl_metric(facts, metric, tax, unit)
        if df.empty:
            return None
        return df["val"].iloc[-1]

    def _latest_shares(metric):
        df = extract_xbrl_metric(facts, metric, "us-gaap", "shares")
        if df.empty:
            return None
        return df["val"].iloc[-1]

    revenue = _latest("Revenues") or _latest("RevenueFromContractWithCustomerExcludingAssessedTax")
    net_income = _latest("NetIncomeLoss")
    total_assets = _latest("Assets")
    total_liabilities = _latest("Liabilities")
    total_equity = _latest("StockholdersEquity")
    current_assets = _latest("AssetsCurrent")
    current_liabilities = _latest("LiabilitiesCurrent")
    long_term_debt = _latest("LongTermDebt") or _latest("LongTermDebtNoncurrent")
    operating_income = _latest("OperatingIncomeLoss")
    shares = _latest_shares("CommonStockSharesOutstanding") or _latest_shares("EntityCommonStockSharesOutstanding")
    eps = _latest("EarningsPerShareBasic", unit="USD/shares")

    ratios = {}
    if net_income and revenue and revenue != 0:
        ratios["net_margin"] = round(net_income / revenue * 100, 2)
    if operating_income and revenue and revenue != 0:
        ratios["operating_margin"] = round(operating_income / revenue * 100, 2)
    if net_income and total_equity and total_equity != 0:
        ratios["roe"] = round(net_income / total_equity * 100, 2)
    if net_income and total_assets and total_assets != 0:
        ratios["roa"] = round(net_income / total_assets * 100, 2)
    if total_liabilities and total_equity and total_equity != 0:
        ratios["debt_to_equity"] = round(total_liabilities / total_equity, 2)
    if current_assets and current_liabilities and current_liabilities != 0:
        ratios["current_ratio"] = round(current_assets / current_liabilities, 2)
    if long_term_debt and total_equity and total_equity != 0:
        ratios["lt_debt_to_equity"] = round(long_term_debt / total_equity, 2)
    if revenue:
        ratios["revenue"] = revenue
    if net_income:
        ratios["net_income"] = net_income
    if eps:
        ratios["eps"] = eps
    if shares:
        ratios["shares_outstanding"] = shares

    return ratios


@st.cache_data(ttl=86400, show_spinner=False)
def get_ratio_history(ticker: str, metric: str, periods: int = 8) -> pd.DataFrame:
    """Get historical values for a specific XBRL metric."""
    facts = fetch_company_facts(ticker)
    if not facts:
        return pd.DataFrame()
    df = extract_xbrl_metric(facts, metric)
    if df.empty:
        return pd.DataFrame()
    # Keep only 10-Q for quarterly view
    quarterly = df[df["form"] == "10-Q"].tail(periods)
    if len(quarterly) < 2:
        quarterly = df.tail(periods)
    return quarterly


# ─────────────────────────────────────────────
# EFTS FULL-TEXT SEARCH (filings search)
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def search_filings(query: str = "", forms: str = "", ticker: str = "",
                   start_date: str = "", end_date: str = "", limit: int = 20) -> list:
    """Search SEC EDGAR filings via EFTS full-text search."""
    params = {"q": query, "from": "0", "size": str(limit)}
    if forms:
        params["forms"] = forms
    if start_date:
        params["startdt"] = start_date
    if end_date:
        params["enddt"] = end_date

    # If ticker provided, resolve to entity name for better search
    if ticker and not query:
        params["q"] = ticker.upper()

    try:
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params=params, headers=_SEC_HEADERS, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        hits = data.get("hits", {}).get("hits", [])
        results = []
        for hit in hits:
            src = hit.get("_source", {})
            results.append({
                "filed": src.get("file_date", ""),
                "form": src.get("form_type", ""),
                "company": src.get("display_names", [""])[0] if src.get("display_names") else "",
                "description": src.get("display_date_filed", ""),
                "url": f"https://www.sec.gov/Archives/edgar/data/{src.get('entity_id', '')}/{src.get('file_num', '')}",
            })
        return results
    except Exception as e:
        logger.warning(f"EDGAR search failed: {e}")
        return []


# ─────────────────────────────────────────────
# 13F INSTITUTIONAL HOLDINGS
# ─────────────────────────────────────────────

# Major funds to track — name: CIK
TRACKED_FUNDS = {
    "Berkshire Hathaway": "0001067983",
    "Bridgewater Associates": "0001350694",
    "Citadel Advisors": "0001423053",
    "Renaissance Technologies": "0001037389",
    "Two Sigma": "0001179392",
    "Tiger Global": "0001167483",
    "DE Shaw": "0001009207",
    "Point72": "0001603466",
    "Millennium Management": "0001273087",
    "Appaloosa Management": "0001656456",
    "Pershing Square": "0001336528",
    "Elliott Management": "0001048445",
    "Third Point": "0001040273",
    "Soros Fund Management": "0001029160",
    "Baupost Group": "0001061768",
}


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_13f_holdings(fund_cik: str) -> pd.DataFrame:
    """Fetch latest 13F holdings for a specific fund from SEC EDGAR."""
    try:
        # Get recent 13F filings
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{fund_cik}.json",
            headers=_SEC_HEADERS, timeout=15,
        )
        r.raise_for_status()
        data = r.json()

        # Find the most recent 13F-HR filing
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])

        filing_accession = None
        filing_date = None
        for i, form in enumerate(forms):
            if "13F" in form:
                filing_accession = accessions[i].replace("-", "")
                filing_date = dates[i]
                break

        if not filing_accession:
            return pd.DataFrame()

        # Fetch the 13F XML/table
        cik_clean = fund_cik.lstrip("0")
        infotable_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{filing_accession}"

        # Try to find the infotable XML
        r2 = requests.get(
            f"https://data.sec.gov/Archives/edgar/data/{cik_clean}/{accessions[0].replace('-', '')}/",
            headers=_SEC_HEADERS, timeout=10,
        )

        # Parse the filing index to find the infotable
        # Simplified: use the submissions API for holdings data
        return pd.DataFrame({"_filing_date": [filing_date], "_accession": [filing_accession]})

    except Exception as e:
        logger.warning(f"13F fetch failed for CIK {fund_cik}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_13f_from_xml(fund_cik: str) -> pd.DataFrame:
    """Fetch and parse 13F holdings from the actual XML infotable."""
    try:
        cik_clean = fund_cik.lstrip("0")
        # Get filing index
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{fund_cik}.json",
            headers=_SEC_HEADERS, timeout=15,
        )
        r.raise_for_status()
        data = r.json()

        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        dates = recent.get("filingDate", [])

        # Find latest 13F
        acc = None
        filing_date = None
        for i, form in enumerate(forms):
            if "13F" in form:
                acc = accessions[i]
                filing_date = dates[i]
                break
        if not acc:
            return pd.DataFrame()

        acc_clean = acc.replace("-", "")

        # Get filing directory to find infotable
        r2 = requests.get(
            f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/",
            headers=_SEC_HEADERS, timeout=10,
        )
        # Find the infotable XML file
        import re
        xml_files = re.findall(r'href="([^"]*infotable[^"]*\.xml)"', r2.text, re.IGNORECASE)
        if not xml_files:
            # Try any XML file
            xml_files = re.findall(r'href="([^"]*\.xml)"', r2.text)

        if not xml_files:
            return pd.DataFrame()

        xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/{xml_files[0]}"
        r3 = requests.get(xml_url, headers=_SEC_HEADERS, timeout=15)

        # Parse XML
        import xml.etree.ElementTree as ET
        root = ET.fromstring(r3.content)
        ns = {"ns": root.tag.split("}")[0].strip("{")} if "}" in root.tag else {}

        holdings = []
        for entry in root.iter():
            if "infoTable" in entry.tag:
                holding = {}
                for child in entry:
                    tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                    if tag == "nameOfIssuer":
                        holding["company"] = child.text
                    elif tag == "titleOfClass":
                        holding["class"] = child.text
                    elif tag == "cusip":
                        holding["cusip"] = child.text
                    elif tag == "value":
                        holding["value_thousands"] = int(child.text) if child.text else 0
                    elif tag == "sshPrnamt" or tag == "Prnamt":
                        for sub in child:
                            stag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                            if stag == "sshPrnamt":
                                holding["shares"] = int(sub.text) if sub.text else 0
                    elif "shrsOrPrnAmt" in tag:
                        for sub in child:
                            stag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                            if "sshPrnamt" in stag.lower():
                                holding["shares"] = int(sub.text) if sub.text else 0
                    elif tag == "putCall":
                        holding["put_call"] = child.text
                if holding.get("company"):
                    holdings.append(holding)

        if holdings:
            df = pd.DataFrame(holdings)
            df["filing_date"] = filing_date
            if "value_thousands" in df.columns:
                df["value"] = df["value_thousands"] * 1000
                df = df.sort_values("value", ascending=False)
            return df
        return pd.DataFrame()

    except Exception as e:
        logger.warning(f"13F XML parse failed for CIK {fund_cik}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────
# 8-K EVENT DETECTION
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_recent_8k(ticker: str, days: int = 90) -> list:
    """Fetch recent 8-K filings (material events) for a ticker."""
    start = (date.today() - timedelta(days=days)).isoformat()
    end = date.today().isoformat()
    return search_filings(query=ticker.upper(), forms="8-K", start_date=start, end_date=end, limit=15)


# ─────────────────────────────────────────────
# INSIDER TRANSACTION SCORING
# ─────────────────────────────────────────────

def score_insider_transactions(transactions: pd.DataFrame) -> dict:
    """Score insider transactions for signal strength.
    Returns composite score (0-100) and breakdown."""
    if transactions.empty:
        return {"score": 50, "signal": "Neutral", "breakdown": {}}

    score = 50  # neutral baseline
    breakdown = {}

    # Count buys vs sells
    if "Transaction" in transactions.columns:
        buys = transactions[transactions["Transaction"].str.contains("Purchase|Buy|P-Purchase", case=False, na=False)]
        sells = transactions[transactions["Transaction"].str.contains("Sale|Sell|S-Sale", case=False, na=False)]
    else:
        buys = pd.DataFrame()
        sells = pd.DataFrame()

    n_buys = len(buys)
    n_sells = len(sells)
    breakdown["buys"] = n_buys
    breakdown["sells"] = n_sells

    # Net direction
    if n_buys > n_sells:
        score += min(20, (n_buys - n_sells) * 5)
    elif n_sells > n_buys:
        score -= min(20, (n_sells - n_buys) * 3)

    # C-suite buys are stronger signals
    if "Title" in transactions.columns and not buys.empty:
        csuite_buys = buys[buys["Title"].str.contains("CEO|CFO|COO|President|Chief", case=False, na=False)]
        if len(csuite_buys) > 0:
            score += len(csuite_buys) * 8
            breakdown["csuite_buys"] = len(csuite_buys)

    # Cluster buying (3+ buys within 7 days)
    if "Date" in transactions.columns and not buys.empty:
        try:
            buy_dates = pd.to_datetime(buys["Date"])
            if len(buy_dates) >= 3:
                date_range = (buy_dates.max() - buy_dates.min()).days
                if date_range <= 7:
                    score += 15
                    breakdown["cluster_buy"] = True
        except Exception:
            pass

    # Large transactions
    if "Value" in transactions.columns and not buys.empty:
        large_buys = buys[buys["Value"] > 100000]
        if len(large_buys) > 0:
            score += min(15, len(large_buys) * 5)
            breakdown["large_buys"] = len(large_buys)

    # Clamp score
    score = max(0, min(100, score))

    # Signal label
    if score >= 75:
        signal = "Strong Buy Signal"
    elif score >= 60:
        signal = "Bullish"
    elif score >= 40:
        signal = "Neutral"
    elif score >= 25:
        signal = "Bearish"
    else:
        signal = "Strong Sell Signal"

    breakdown["raw_score"] = score
    return {"score": score, "signal": signal, "breakdown": breakdown}


# ─────────────────────────────────────────────
# CONGRESSIONAL TRADING
# ─────────────────────────────────────────────

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_congressional_trades() -> pd.DataFrame:
    """Search EDGAR for filings that may relate to congressional disclosures.
    Note: Congressional trades are primarily filed via Senate eFD and House disclosure
    portals, not EDGAR. This is a best-effort keyword search."""
    try:
        # Search for any filings mentioning Congress-related terms
        results = search_filings(
            query="united states senate OR house of representatives OR member of congress",
            forms="3,4,5",
            start_date=(date.today() - timedelta(days=90)).isoformat(),
            limit=50,
        )
        if results:
            return pd.DataFrame(results)
    except Exception:
        pass
    return pd.DataFrame()


# ─────────────────────────────────────────────
# ACTIVIST INVESTORS (13D filings)
# ─────────────────────────────────────────────

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_recent_13d(days: int = 90) -> list:
    """Fetch recent 13D filings (activist positions >5% ownership)."""
    start = (date.today() - timedelta(days=days)).isoformat()
    return search_filings(forms="SC 13D,SC 13D/A", start_date=start, limit=30)
