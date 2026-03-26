"""SEC EDGAR API helpers — public domain data, zero legal risk.
All requests include required User-Agent header per SEC policy."""

import os
import io
import re
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
    Returns DataFrame with columns: start, end, val, form, filed."""
    try:
        data = facts.get("facts", {}).get(taxonomy, {}).get(metric, {}).get("units", {}).get(unit, [])
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        # Keep only annual (10-K) and quarterly (10-Q)
        df = df[df["form"].isin(["10-K", "10-Q"])]
        df["end"] = pd.to_datetime(df["end"])
        if "start" in df.columns:
            df["start"] = pd.to_datetime(df["start"], errors="coerce")
        else:
            df["start"] = pd.NaT
        df = df.sort_values("end").drop_duplicates(subset=["end", "form"], keep="last")
        cols = ["start", "end", "val", "form", "filed"]
        return df[[c for c in cols if c in df.columns]].tail(20)
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

    def _best_revenue():
        """Pick the revenue tag with the most recent filing date."""
        for tag in ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]:
            df = extract_xbrl_metric(facts, tag)
            if not df.empty:
                latest_date = df["end"].iloc[-1]
                # Only accept if data is from the last 2 years
                if latest_date >= pd.Timestamp.now() - pd.DateOffset(years=2):
                    return df["val"].iloc[-1]
        # Fallback: return whichever has the most recent data
        df1 = extract_xbrl_metric(facts, "Revenues")
        df2 = extract_xbrl_metric(facts, "RevenueFromContractWithCustomerExcludingAssessedTax")
        if df1.empty and df2.empty:
            return None
        if df1.empty:
            return df2["val"].iloc[-1]
        if df2.empty:
            return df1["val"].iloc[-1]
        return df1["val"].iloc[-1] if df1["end"].iloc[-1] >= df2["end"].iloc[-1] else df2["val"].iloc[-1]

    revenue = _best_revenue()
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
    import xml.etree.ElementTree as ET

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

        # Use index.json to reliably discover filing documents
        index_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/index.json"
        r2 = requests.get(index_url, headers=_SEC_HEADERS, timeout=10)
        r2.raise_for_status()
        items = r2.json().get("directory", {}).get("item", [])
        xml_names = [
            item["name"] for item in items
            if item["name"].endswith(".xml") and item["name"] != "primary_doc.xml"
        ]

        if not xml_names:
            return pd.DataFrame()

        # Try each XML file until we find the infotable
        holdings = []
        for xml_name in xml_names:
            xml_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/{xml_name}"
            r3 = requests.get(xml_url, headers=_SEC_HEADERS, timeout=15)
            root = ET.fromstring(r3.content)

            # Check if this XML contains infoTable entries
            has_info = any("infoTable" in el.tag for el in root.iter())
            if not has_info:
                continue

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
                            holding["value"] = int(child.text) if child.text else 0
                        elif "shrsOrPrnAmt" in tag:
                            for sub in child:
                                stag = sub.tag.split("}")[-1] if "}" in sub.tag else sub.tag
                                if stag.lower() == "sshprnamt":
                                    holding["shares"] = int(sub.text) if sub.text else 0
                        elif tag == "putCall":
                            holding["put_call"] = child.text
                    if holding.get("company"):
                        holdings.append(holding)
            if holdings:
                break

        if holdings:
            df = pd.DataFrame(holdings)
            df["filing_date"] = filing_date
            if "value" in df.columns:
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
    """Fetch recent 8-K filings for a ticker via submissions API."""
    cik = ticker_to_cik(ticker)
    if not cik:
        return []

    cutoff = (date.today() - timedelta(days=days)).isoformat()
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_SEC_HEADERS, timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        items_list = recent.get("items", [])
        company = data.get("name", ticker.upper())
    except Exception as e:
        logger.warning(f"8-K fetch failed for {ticker}: {e}")
        return []

    results = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        filed = dates[i] if i < len(dates) else ""
        if filed < cutoff:
            break
        items = items_list[i] if i < len(items_list) else ""
        results.append({
            "filed": filed,
            "form": "8-K",
            "company": company,
            "items": items,
            "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=8-K",
        })
        if len(results) >= 20:
            break
    return results


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
def fetch_congressional_trades(year: int | None = None) -> pd.DataFrame:
    """Fetch House member Periodic Transaction Reports from the House Financial
    Disclosure XML feed (official clerk.house.gov data)."""
    import xml.etree.ElementTree as ET

    if year is None:
        year = date.today().year

    results = []
    # Fetch current year, and prior year if current year has few results
    for yr in [year, year - 1]:
        url = f"https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{yr}FD.xml"
        try:
            r = requests.get(url, headers={"User-Agent": _SEC_USER_AGENT}, timeout=15)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content.decode("utf-8-sig"))
            for member in root.findall("Member"):
                filing_type = member.findtext("FilingType", "")
                if filing_type != "P":  # P = Periodic Transaction Report
                    continue
                results.append({
                    "name": f"{member.findtext('First', '')} {member.findtext('Last', '')}".strip(),
                    "state": member.findtext("StateDst", ""),
                    "filed": member.findtext("FilingDate", ""),
                    "year": yr,
                    "doc_id": member.findtext("DocID", ""),
                    "doc_url": f"https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{yr}/{member.findtext('DocID', '')}.pdf",
                })
        except Exception as e:
            logger.warning(f"House disclosure fetch failed for {yr}: {e}")

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df["filed"] = pd.to_datetime(df["filed"], format="mixed", errors="coerce")
    df = df.sort_values("filed", ascending=False).reset_index(drop=True)
    return df


_TICKER_PAT = re.compile(r'\(([A-Z][A-Z0-9\.]{0,5})\)\s*\[(?:ST|OP|CS|OT)\]')
_DATE_PAT = re.compile(r'(\d{2}/\d{2}/\d{4})')
_TXN_TYPE_PAT = re.compile(r'\b(S \(partial\)|S \(full\)|[PSE])\s+\d{2}/')


@st.cache_data(ttl=43200, show_spinner=False)
def parse_ptr_pdf(pdf_url: str) -> list[dict]:
    """Download and parse a House PTR PDF into individual trade records."""
    import pdfplumber

    try:
        r = requests.get(pdf_url, headers={"User-Agent": _SEC_USER_AGENT}, timeout=20)
        if r.status_code != 200:
            return []
        pdf = pdfplumber.open(io.BytesIO(r.content))
    except Exception as e:
        logger.warning(f"PTR PDF download/open failed: {pdf_url}: {e}")
        return []

    full_text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
    lines = full_text.split('\n')
    transactions = []

    for i, line in enumerate(lines):
        m = _TICKER_PAT.search(line)
        if not m:
            continue
        ticker = m.group(1)

        # Search up to 5 lines back for the data line (dates + transaction type)
        data_line = None
        for lookback in range(0, min(6, i + 1)):
            candidate = lines[i - lookback]
            if _DATE_PAT.search(candidate) and _TXN_TYPE_PAT.search(candidate):
                data_line = candidate
                if (i - lookback - 1) >= 0:
                    data_line = lines[i - lookback - 1] + ' ' + data_line
                break

        if not data_line:
            continue

        txn_m = _TXN_TYPE_PAT.search(data_line)
        txn_type = txn_m.group(1) if txn_m else '?'

        dates = _DATE_PAT.findall(data_line)
        txn_date = dates[0] if dates else ''
        notif_date = dates[1] if len(dates) > 1 else ''

        dollars = re.findall(r'\$([\d,]+)', data_line)
        if len(dollars) >= 2:
            amount = f"${dollars[-2]} - ${dollars[-1]}"
        elif len(dollars) == 1:
            amount = f"${dollars[0]}"
        else:
            amount = ''

        transactions.append({
            'ticker': ticker,
            'type': 'Purchase' if txn_type == 'P' else 'Sale',
            'date': txn_date,
            'notification_date': notif_date,
            'amount': amount,
        })

    return transactions


@st.cache_data(ttl=43200, show_spinner="Parsing PTR filings...")
def fetch_parsed_congressional_trades(year: int | None = None, max_filings: int = 50) -> pd.DataFrame:
    """Fetch the PTR index, then parse the most recent PDFs for trade-level data."""
    index_df = fetch_congressional_trades(year)
    if index_df.empty:
        return pd.DataFrame()

    # Take most recent filings up to max_filings
    subset = index_df.head(max_filings)
    all_trades = []

    for _, row in subset.iterrows():
        trades = parse_ptr_pdf(row["doc_url"])
        for t in trades:
            t["member"] = row["name"]
            t["state"] = row["state"]
            t["filed"] = row["filed"]
        all_trades.extend(trades)

    if not all_trades:
        return pd.DataFrame()

    df = pd.DataFrame(all_trades)
    df["date"] = pd.to_datetime(df["date"], format="%m/%d/%Y", errors="coerce")
    return df


# ─────────────────────────────────────────────
# ACTIVIST INVESTORS (13D filings)
# ─────────────────────────────────────────────

# Noise: filers who mass-file 13Ds for municipal/closed-end funds (not activist signals)
_13D_NOISE_FILERS = {
    "BANK OF AMERICA CORP /DE/", "BANK OF AMERICA CORP", "JPMORGAN CHASE & CO",
    "MORGAN STANLEY", "WELLS FARGO & COMPANY", "CITIGROUP INC",
    "GOLDMAN SACHS GROUP INC", "UBS GROUP AG",
}


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_recent_13d(days: int = 90) -> pd.DataFrame:
    """Fetch recent SC 13D filings with structured target/activist extraction."""
    start = (date.today() - timedelta(days=days)).isoformat()

    try:
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"forms": "SC 13D", "startdt": start, "size": "100"},
            headers=_SEC_HEADERS, timeout=15,
        )
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])
    except Exception as e:
        logger.warning(f"13D EFTS search failed: {e}")
        return pd.DataFrame()

    records = []
    for hit in hits:
        src = hit.get("_source", {})
        names = src.get("display_names", [])
        ciks = src.get("ciks", [])
        form = src.get("form", src.get("file_type", ""))
        adsh = src.get("adsh", "")
        filed = src.get("file_date", "")

        target_raw = names[0] if names else ""
        filer_raw = names[1] if len(names) > 1 else ""

        # Extract ticker from target name: "Company Name  (TICK)  (CIK ...)"
        ticker_m = re.search(r'\(([A-Z][A-Z0-9]{0,5})\)', target_raw)
        ticker = ticker_m.group(1) if ticker_m else ""

        # Clean names: strip CIK suffix
        target = re.sub(r'\s*\(CIK\s*\d+\)\s*$', '', target_raw).strip()
        filer = re.sub(r'\s*\(CIK\s*\d+\)\s*$', '', filer_raw).strip()

        # Filter noise filers
        filer_upper = filer.upper()
        if any(noise in filer_upper for noise in _13D_NOISE_FILERS):
            continue

        # Build proper SEC filing URL
        if adsh and ciks:
            cik_clean = ciks[0].lstrip("0")
            acc_clean = adsh.replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_clean}/"
        else:
            url = ""

        records.append({
            "filed": filed,
            "form": form,
            "is_new": "13D/A" not in form,
            "target": target,
            "ticker": ticker,
            "activist": filer,
            "url": url,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df = df.sort_values("filed", ascending=False).reset_index(drop=True)
    return df


# ─────────────────────────────────────────────
# COMPANY GUIDANCE (from 8-K earnings releases)
# ─────────────────────────────────────────────

def _fetch_filing_htm_text(cik: str, adsh: str) -> str:
    """Fetch and combine text from all HTM files in a filing."""
    acc = adsh.replace("-", "")
    try:
        r = requests.get(
            f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/index.json",
            headers=_SEC_HEADERS, timeout=10,
        )
        if r.status_code != 200:
            return ""
        items = r.json().get("directory", {}).get("item", [])
    except Exception:
        return ""

    htm_files = [
        i["name"] for i in items
        if i["name"].endswith((".htm", ".html"))
        and "index" not in i["name"].lower() and "R1" not in i["name"]
    ]

    combined = ""
    for fname in htm_files:
        try:
            r2 = requests.get(
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{fname}",
                headers=_SEC_HEADERS, timeout=15,
            )
            if r2.status_code == 200:
                text = re.sub(r"<[^>]+>", " ", r2.text)
                text = re.sub(r"&[a-zA-Z0-9#]+;", " ", text)
                text = re.sub(r"\s+", " ", text).strip()
                combined += " " + text
        except Exception:
            continue
    return combined


def _parse_dollar(val_str: str, unit_str: str) -> float | None:
    """Convert a dollar string + unit to a float."""
    try:
        val = float(val_str.replace(",", ""))
        unit = unit_str.lower()
        if unit in ("billion", "b"):
            return val * 1e9
        elif unit in ("million", "m"):
            return val * 1e6
        return val
    except (ValueError, AttributeError):
        return None


def extract_guidance_from_text(text: str) -> dict:
    """Extract forward guidance figures from 8-K press release text."""
    result = {
        "raw_outlook": "", "revenue": None, "revenue_high": None,
        "gross_margin": None, "eps": None, "eps_high": None,
        "opex": None, "operating_income": None, "oi_high": None,
        "quarter": "",
    }

    # Find outlook/guidance section
    outlook_text = ""
    outlook_patterns = [
        # Outlook section that contains actual dollar figures (not legal disclaimers)
        r"(?:(?:financial|business)\s+)?(?:outlook|guidance)[:\s]*(.{50,2500}?\$[\d,.]+.{0,1500}?)(?:conference call|safe harbor|cautionary note|forward.looking statement|highlights|about \w{3,}|\Z)",
        r"((?:First|Second|Third|Fourth|Full)[- ](?:Quarter|Year)\s+\d{4}\s+Guidance\s+.{50,1500}?)(?:conference call|safe harbor|\Z)",
        r"((?:For|In)\s+the\s+(?:first|second|third|fourth|full)\s+(?:quarter|year)[^.]*?(?:expects?|anticipates?|projects?)[^.]*?(?:revenue|net sales|earnings)[^.]*\.(?:[^.]*\.){0,8})",
        r"((?:The [Cc]ompany|We)\s+(?:expects?|anticipates?|projects?)\s+[^.]*(?:revenue|net sales|earnings)[^.]*\.(?:[^.]*\.){0,6})",
        # Fallback: outlook without requiring $ (broader match)
        r"(?:(?:financial|business)\s+)?(?:outlook|guidance)[:\s]*(.{50,2500}?)(?:conference call|safe harbor|cautionary note|forward.looking statement|highlights|about \w{3,}|\Z)",
    ]
    for pat in outlook_patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            candidate = m.group(1).strip()
            # Skip if it's just a legal disclaimer (no dollar amounts or percentages)
            if re.search(r'\$[\d,.]+|\d+\.?\d*\s*%', candidate):
                outlook_text = candidate
                break

    if not outlook_text:
        # Last resort: any sentence with revenue + expected
        m2 = re.search(
            r"([^.]*(?:revenue|net sales)[^.]*(?:expect|guid|anticipat|between)[^.]*\.)",
            text, re.IGNORECASE,
        )
        if m2:
            outlook_text = m2.group(1)

    if not outlook_text:
        return result

    result["raw_outlook"] = outlook_text[:1200]

    # Quarter reference
    q_match = re.search(
        r"(?:first|second|third|fourth|Q[1-4]|full[- ]?year)\s+(?:quarter\s+)?(?:of\s+)?(?:fiscal\s+)?(?:year\s+)?(\d{4})",
        outlook_text, re.IGNORECASE,
    )
    if q_match:
        result["quarter"] = q_match.group(0).strip()

    # Revenue — range format: "between $X and $Y billion"
    range_m = re.search(
        r"(?:net sales|revenue|total revenue)[^.]*?between\s+\$([\d,.]+)\s*(billion|million|B|M)\s+and\s+\$([\d,.]+)\s*(billion|million|B|M)",
        outlook_text, re.IGNORECASE,
    )
    if range_m:
        result["revenue"] = _parse_dollar(range_m.group(1), range_m.group(2))
        result["revenue_high"] = _parse_dollar(range_m.group(3), range_m.group(4))
    else:
        # Single figure: "Revenue is expected to be $X billion"
        for pat in [
            r"(?:net sales|revenue|total revenue)[^.]{0,80}?\$([\d,.]+)\s*(billion|million|B|M)",
            r"\$([\d,.]+)\s*(billion|million|B|M)[^.]{0,40}?(?:revenue|net sales)",
            r"(?:expects?|anticipates?)\s+(?:total\s+)?(?:net\s+)?(?:revenue|sales)\s+(?:of\s+)?\$([\d,.]+)\s*(billion|million|B|M)",
        ]:
            rm = re.search(pat, outlook_text, re.IGNORECASE)
            if rm:
                result["revenue"] = _parse_dollar(rm.group(1), rm.group(2))
                break

    # Operating income — range format (handle "$0 and $4.0 billion")
    oi_range = re.search(
        r"[Oo]perating\s+income[^.]*?between\s+\$([\d,.]+)\s*(billion|million|B|M)?\s+and\s+\$([\d,.]+)\s*(billion|million|B|M)",
        outlook_text, re.IGNORECASE,
    )
    if oi_range:
        unit_low = oi_range.group(2) or oi_range.group(4)  # use high's unit if low has none
        result["operating_income"] = _parse_dollar(oi_range.group(1), unit_low)
        result["oi_high"] = _parse_dollar(oi_range.group(3), oi_range.group(4))
    else:
        oi_m = re.search(
            r"[Oo]perating\s+income[^.]*?\$([\d,.]+)\s*(billion|million|B|M)",
            outlook_text, re.IGNORECASE,
        )
        if oi_m:
            result["operating_income"] = _parse_dollar(oi_m.group(1), oi_m.group(2))

    # Gross margin
    for pat in [
        r"[Gg]ross\s+margin[s]?[^.]*?([\d.]+)\s*%",
        r"([\d.]+)\s*%[^.]{0,20}?gross\s+margin",
    ]:
        gm = re.search(pat, outlook_text, re.IGNORECASE)
        if gm:
            val = float(gm.group(1))
            if 10 < val < 100:
                result["gross_margin"] = val
                break

    # EPS — range or single
    eps_range = re.search(
        r"(?:earnings per share|EPS|diluted EPS)[^.]*?between\s+\$([\d.]+)\s+and\s+\$([\d.]+)",
        outlook_text, re.IGNORECASE,
    )
    if eps_range:
        result["eps"] = float(eps_range.group(1))
        result["eps_high"] = float(eps_range.group(2))
    else:
        for pat in [
            r"(?:earnings per share|EPS|diluted EPS|net income per share)[^.]*?\$([\d.]+)",
            r"\$([\d.]+)\s*(?:per (?:diluted )?share|EPS)",
        ]:
            em = re.search(pat, outlook_text, re.IGNORECASE)
            if em:
                val = float(em.group(1))
                if val < 500:
                    result["eps"] = val
                    break

    # Operating expenses
    opex_m = re.search(
        r"[Oo]perating\s+expense[s]?[^.]*?\$([\d,.]+)\s*(billion|million|B|M)",
        outlook_text, re.IGNORECASE,
    )
    if opex_m:
        result["opex"] = _parse_dollar(opex_m.group(1), opex_m.group(2))

    return result


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_earnings_filings(ticker: str, limit: int = 10) -> list[dict]:
    """Find 8-K Item 2.02 (earnings release) filings for a ticker via submissions API."""
    cik = ticker_to_cik(ticker)
    if not cik:
        return []

    try:
        # Use submissions API for reliable recent filings
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=_SEC_HEADERS, timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        items_list = recent.get("items", [])
    except Exception as e:
        logger.warning(f"Earnings filing search failed for {ticker}: {e}")
        return []

    results = []
    for i, form in enumerate(forms):
        if form != "8-K":
            continue
        # Check if this 8-K contains Item 2.02
        items = items_list[i] if i < len(items_list) else ""
        if "2.02" not in items:
            continue
        results.append({
            "filed": dates[i] if i < len(dates) else "",
            "adsh": accessions[i] if i < len(accessions) else "",
            "cik": cik.lstrip("0"),
        })
        if len(results) >= limit:
            break
    return results


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_guidance_history(ticker: str, num_quarters: int = 8) -> pd.DataFrame:
    """Fetch and parse guidance from recent earnings releases for a ticker."""
    filings = fetch_earnings_filings(ticker, limit=num_quarters)
    if not filings:
        return pd.DataFrame()

    records = []
    for f in filings:
        text = _fetch_filing_htm_text(f["cik"], f["adsh"])
        if not text:
            continue
        g = extract_guidance_from_text(text)
        if not g["raw_outlook"] or (not g["revenue"] and not g["eps"] and not g["gross_margin"]):
            continue
        records.append({
            "filed": f["filed"],
            "quarter": g["quarter"],
            "revenue": g["revenue"],
            "revenue_high": g["revenue_high"],
            "gross_margin": g["gross_margin"],
            "eps": g["eps"],
            "eps_high": g["eps_high"],
            "opex": g["opex"],
            "operating_income": g["operating_income"],
            "oi_high": g["oi_high"],
            "outlook": g["raw_outlook"],
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df = df.sort_values("filed").reset_index(drop=True)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_recent_earnings_calendar(days: int = 7) -> pd.DataFrame:
    """Fetch recent 8-K Item 2.02 filings for an earnings calendar view."""
    start = (date.today() - timedelta(days=days)).isoformat()
    try:
        r = requests.get(
            "https://efts.sec.gov/LATEST/search-index",
            params={"forms": "8-K", "startdt": start, "size": "100"},
            headers=_SEC_HEADERS, timeout=15,
        )
        if r.status_code != 200:
            return pd.DataFrame()
        hits = r.json().get("hits", {}).get("hits", [])
    except Exception as e:
        logger.warning(f"Earnings calendar fetch failed: {e}")
        return pd.DataFrame()

    records = []
    for h in hits:
        s = h["_source"]
        if "2.02" not in s.get("items", []):
            continue
        names = s.get("display_names", [])
        company_raw = names[0] if names else ""
        # Extract ticker from company name
        ticker_m = re.search(r'\(([A-Z][A-Z0-9]{0,5})\)', company_raw)
        ticker = ticker_m.group(1) if ticker_m else ""
        company = re.sub(r'\s*\(CIK\s*\d+\)\s*$', '', company_raw).strip()
        records.append({
            "filed": s.get("file_date", ""),
            "company": company,
            "ticker": ticker,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df = df.sort_values("filed", ascending=False).reset_index(drop=True)
    return df


# ─────────────────────────────────────────────
# ENERGY SECTOR ANALYSIS
# ─────────────────────────────────────────────

ENERGY_COMPANIES = {
    "XOM":  "ExxonMobil",
    "CVX":  "Chevron",
    "COP":  "ConocoPhillips",
    "EOG":  "EOG Resources",
    "SLB":  "SLB (Schlumberger)",
    "MPC":  "Marathon Petroleum",
    "OXY":  "Occidental Petroleum",
    "PSX":  "Phillips 66",
    "VLO":  "Valero Energy",
    "DVN":  "Devon Energy",
}

# Snapshot scraped 2026-03-25 from earnings calls + Yahoo Finance.
# Update quarterly after each earnings season.
ENERGY_GUIDANCE_SNAPSHOT = {
    "date": "2026-03-25",
    "data": [
        {"ticker": "XOM", "company": "ExxonMobil", "rev_est_y": 352.0, "rev_growth": "+6%",
         "eps_est_y": 8.09, "eps_est_ny": 8.92, "capex_guidance": 27.0, "capex_note": "FY25 <$27-29B range; FY26 expected similar",
         "production": "4.7M boe/d", "price_target": 155, "rating": "Buy", "fwd_pe": 18.3,
         "outlook": "Highest production in 40+ years. Permian record 1.8M boe/d. $15B cumulative cost savings. 43 consecutive years of dividend increases."},
        {"ticker": "CVX", "company": "Chevron", "rev_est_y": 202.0, "rev_growth": "+7%",
         "eps_est_y": 8.77, "eps_est_ny": 9.77, "capex_guidance": 17.3, "capex_note": "FY25 organic $17-17.5B incl Hess",
         "production": None, "price_target": 196, "rating": "Buy", "fwd_pe": 21.0,
         "outlook": "Q3 organic CapEx $4.4B. Hess integration underway. Golden Pass LNG mechanically complete, first LNG expected early 2026."},
        {"ticker": "COP", "company": "ConocoPhillips", "rev_est_y": 60.0, "rev_growth": "-3%",
         "eps_est_y": 6.37, "eps_est_ny": 7.24, "capex_guidance": None, "capex_note": None,
         "production": None, "price_target": 126, "rating": "Buy", "fwd_pe": 17.8,
         "outlook": "Free cash flow inflection expected to deliver $1B incremental/year 2026-2028. Willow project to add $4B in 2029."},
        {"ticker": "EOG", "company": "EOG Resources", "rev_est_y": 24.0, "rev_growth": "+5%",
         "eps_est_y": 11.08, "eps_est_ny": 11.72, "capex_guidance": None, "capex_note": None,
         "production": None, "price_target": 141, "rating": "Buy", "fwd_pe": 12.3,
         "outlook": "Premium returns strategy. Low-cost operator. No explicit CapEx guidance in press release."},
        {"ticker": "SLB", "company": "SLB (Schlumberger)", "rev_est_y": 37.0, "rev_growth": "+4%",
         "eps_est_y": 2.82, "eps_est_ny": 3.33, "capex_guidance": 2.5, "capex_note": "Capital investments $2.5B; intensity target 5-7%",
         "production": None, "price_target": 55, "rating": "Buy", "fwd_pe": 15.6,
         "outlook": "ChampionX synergies and digital expansion driving margins. Subsea market 20% higher tree award run rate. Capital intensity target 5-7%."},
        {"ticker": "MPC", "company": "Marathon Petroleum", "rev_est_y": 127.0, "rev_growth": "-6%",
         "eps_est_y": 17.61, "eps_est_ny": 15.65, "capex_guidance": None, "capex_note": None,
         "production": None, "price_target": 213, "rating": "Buy", "fwd_pe": 15.4,
         "outlook": "Largest US refiner. No transcript available for automated scraping. Revenue decline reflects lower refining margins."},
        {"ticker": "OXY", "company": "Occidental Petroleum", "rev_est_y": 23.0, "rev_growth": "+5%",
         "eps_est_y": 2.86, "eps_est_ny": 2.69, "capex_guidance": None, "capex_note": None,
         "production": None, "price_target": 58, "rating": "Hold", "fwd_pe": 23.0,
         "outlook": "Buffett-backed. CrownRock acquisition integration. High leverage relative to peers. EPS declining into next year."},
        {"ticker": "PSX", "company": "Phillips 66", "rev_est_y": 141.0, "rev_growth": "+3%",
         "eps_est_y": 13.00, "eps_est_ny": 13.55, "capex_guidance": 2.4, "capex_note": "FY capital budget $2.4B",
         "production": None, "price_target": 165, "rating": "Buy", "fwd_pe": 13.4,
         "outlook": "~$8B operating cash flow expected. ~$4B available for debt reduction and buybacks after CapEx."},
        {"ticker": "VLO", "company": "Valero Energy", "rev_est_y": 116.0, "rev_growth": "-5%",
         "eps_est_y": 16.36, "eps_est_ny": 14.40, "capex_guidance": 1.6, "capex_note": "Q3 CapEx $409M ($364M sustaining); ~$1.6B annualized",
         "production": None, "price_target": 214, "rating": "Buy", "fwd_pe": 16.3,
         "outlook": "Gulf Coast throughput guidance provided quarterly. Primarily sustaining CapEx with modest growth initiatives."},
        {"ticker": "DVN", "company": "Devon Energy", "rev_est_y": 20.0, "rev_growth": "+14%",
         "eps_est_y": 4.00, "eps_est_ny": 4.69, "capex_guidance": None, "capex_note": None,
         "production": "830K boe/d", "price_target": 53, "rating": "Buy", "fwd_pe": 11.0,
         "outlook": "FY2026 production ~830K boe/d. Guidance unchanged despite 10K boe/d weather disruption in Jan. Merger close planned."},
    ],
}


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_sector_financials(companies: dict[str, str]) -> pd.DataFrame:
    """Fetch XBRL financial ratios for a dict of {ticker: name} companies."""
    rows = []
    for ticker, name in companies.items():
        ratios = calculate_financial_ratios(ticker)
        if not ratios:
            continue
        rows.append({
            "ticker": ticker,
            "company": name,
            "revenue": ratios.get("revenue"),
            "net_income": ratios.get("net_income"),
            "net_margin": ratios.get("net_margin"),
            "operating_margin": ratios.get("operating_margin"),
            "roe": ratios.get("roe"),
            "roa": ratios.get("roa"),
            "debt_to_equity": ratios.get("debt_to_equity"),
            "current_ratio": ratios.get("current_ratio"),
            "eps": ratios.get("eps"),
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_energy_financials() -> pd.DataFrame:
    """Fetch XBRL financial ratios for all tracked energy companies."""
    return fetch_sector_financials(ENERGY_COMPANIES)


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_energy_guidance(ticker: str) -> pd.DataFrame:
    """Fetch guidance history for a single energy company."""
    return fetch_guidance_history(ticker, num_quarters=4)


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_sector_analyst_estimates(companies: dict[str, str]) -> pd.DataFrame:
    """Fetch Yahoo Finance analyst estimates for a dict of {ticker: name} companies."""
    from src.market_data import fetch_analyst_estimates

    rows = []
    for ticker, name in companies.items():
        est = fetch_analyst_estimates(ticker)
        if not est:
            continue
        rows.append({
            "ticker": ticker,
            "company": name,
            "rev_est_q": est.get("rev_est_current_q"),
            "rev_est_y": est.get("rev_est_current_y"),
            "rev_growth": est.get("rev_growth_current_y"),
            "eps_est_y": est.get("eps_est_current_y"),
            "eps_est_ny": est.get("eps_est_next_y"),
            "price_target": est.get("price_target_mean"),
            "recommendation": est.get("recommendation"),
            "forward_pe": est.get("forward_pe"),
            "num_analysts": est.get("num_analysts"),
            "capex_guidance": None,
            "production_guidance": None,
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_energy_analyst_estimates() -> pd.DataFrame:
    """Fetch Yahoo Finance analyst estimates for all energy companies (fast, no scraping)."""
    return fetch_sector_analyst_estimates(ENERGY_COMPANIES)


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_energy_forecasts() -> pd.DataFrame:
    """Fetch forward projections for all energy companies.
    Combines Yahoo Finance analyst estimates + earnings call transcript guidance."""
    from src.market_data import fetch_analyst_estimates

    rows = []
    for ticker, name in ENERGY_COMPANIES.items():
        row = {"ticker": ticker, "company": name}

        # Yahoo Finance analyst consensus
        est = fetch_analyst_estimates(ticker)
        if est:
            row["rev_est_q"] = est.get("rev_est_current_q")
            row["rev_est_y"] = est.get("rev_est_current_y")
            row["rev_growth"] = est.get("rev_growth_current_y")
            row["eps_est_y"] = est.get("eps_est_current_y")
            row["eps_est_ny"] = est.get("eps_est_next_y")
            row["price_target"] = est.get("price_target_mean")
            row["recommendation"] = est.get("recommendation")
            row["forward_pe"] = est.get("forward_pe")
            row["num_analysts"] = est.get("num_analysts")

        # Transcript-based CapEx guidance
        try:
            urls = discover_fool_transcript_urls(ticker, limit=2)
            if urls:
                call_df = fetch_transcript_guidance(ticker, urls)
                if not call_df.empty:
                    latest = call_df.iloc[-1]
                    if pd.notna(latest.get("capex")):
                        row["capex_guidance"] = latest["capex"]
                    if pd.notna(latest.get("production")):
                        row["production_guidance"] = latest["production"]
        except Exception:
            pass

        rows.append(row)

    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_sector_margin_history(companies: dict[str, str]) -> pd.DataFrame:
    """Fetch quarterly net income + operating income history for margin analysis (2024+)."""
    rows = []
    for ticker, name in companies.items():
        facts = fetch_company_facts(ticker)
        if not facts:
            continue
        # Pick revenue tag with recent data
        rev = pd.DataFrame()
        for tag in ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]:
            df_tag = extract_xbrl_metric(facts, tag)
            if not df_tag.empty:
                recent = df_tag[df_tag["end"] >= "2024-01-01"]
                if len(recent) > len(rev):
                    rev = recent
        ni = extract_xbrl_metric(facts, "NetIncomeLoss")
        oi = extract_xbrl_metric(facts, "OperatingIncomeLoss")

        for df_metric, col_name in [(rev, "revenue"), (ni, "net_income"), (oi, "operating_income")]:
            if df_metric.empty:
                continue
            df_f = df_metric[df_metric["end"] >= "2024-01-01"].sort_values("end").drop_duplicates("end", keep="last")
            for _, r in df_f.iterrows():
                rows.append({"ticker": ticker, "date": r["end"], "metric": col_name, "value": r["val"]})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.pivot_table(index=["ticker", "date"], columns="metric", values="value").reset_index()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_energy_margin_history() -> pd.DataFrame:
    """Fetch quarterly net income + operating income history for margin analysis (2024+)."""
    return fetch_sector_margin_history(ENERGY_COMPANIES)


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_sector_cashflow(tickers: list[str]) -> pd.DataFrame:
    """Fetch operating cash flow for earnings quality analysis."""
    from src.market_data import fetch_energy_valuation_data
    val = fetch_energy_valuation_data(tickers)
    if val.empty:
        return pd.DataFrame()
    return val[["ticker", "operating_cf", "fcf", "market_cap"]].copy()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_energy_cashflow() -> pd.DataFrame:
    """Fetch operating cash flow for earnings quality analysis."""
    return fetch_sector_cashflow(list(ENERGY_COMPANIES.keys()))


def _filter_quarterly_periods(df: pd.DataFrame) -> pd.DataFrame:
    """Filter XBRL data to single-quarter periods only (not cumulative YTD).
    Uses start/end dates to detect quarterly vs multi-quarter entries."""
    if df.empty or "start" not in df.columns:
        return df
    df = df.copy()
    df["period_days"] = (df["end"] - df["start"]).dt.days
    # Single quarter = roughly 80-100 days. Allow up to 120 for fiscal quirks.
    quarterly = df[df["period_days"].between(60, 120)]
    return quarterly if not quarterly.empty else df


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_sector_revenue_history(companies: dict[str, str]) -> pd.DataFrame:
    """Fetch quarterly revenue history for companies from XBRL (2024+).
    Filters to single-quarter periods to avoid cumulative YTD values."""
    rows = []
    for ticker, name in companies.items():
        facts = fetch_company_facts(ticker)
        if not facts:
            continue
        # Pick whichever revenue tag has more 2024+ quarterly data
        best_df = pd.DataFrame()
        for tag in ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]:
            df = extract_xbrl_metric(facts, tag)
            if df.empty:
                continue
            recent = df[df["end"] >= "2024-01-01"]
            # Filter to single-quarter periods
            recent = _filter_quarterly_periods(recent)
            if len(recent) > len(best_df):
                best_df = recent
        if best_df.empty:
            continue
        best_df = best_df.sort_values("end").drop_duplicates(subset=["end"], keep="last")
        for _, r in best_df.iterrows():
            rows.append({
                "ticker": ticker,
                "company": name,
                "date": r["end"],
                "revenue": r["val"],
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_energy_revenue_history() -> pd.DataFrame:
    """Fetch quarterly revenue history for all energy companies from XBRL (2024+)."""
    return fetch_sector_revenue_history(ENERGY_COMPANIES)


_CAPEX_TAGS = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireOilAndGasPropertyAndEquipment",
    "PaymentsToAcquireOilAndGasProperty",
    "PaymentsToAcquireProductiveAssets",
    "CapitalExpendituresIncurredButNotYetPaid",
]


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_sector_capex(companies: dict[str, str]) -> pd.DataFrame:
    """Fetch latest CapEx for companies from XBRL."""
    rows = []
    for ticker, name in companies.items():
        facts = fetch_company_facts(ticker)
        if not facts:
            continue
        best_val, best_form = None, None
        for tag in _CAPEX_TAGS:
            df = extract_xbrl_metric(facts, tag)
            if df.empty:
                continue
            df = df[df["end"] >= "2024-01-01"]
            if not df.empty:
                latest = df.iloc[-1]
                if best_val is None or latest["val"] > best_val:
                    best_val = latest["val"]
                    best_form = latest["form"]
        if best_val is not None:
            rows.append({
                "ticker": ticker,
                "company": name,
                "capex": best_val,
                "period": best_form,
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_energy_capex() -> pd.DataFrame:
    """Fetch latest CapEx for all energy companies from XBRL."""
    return fetch_sector_capex(ENERGY_COMPANIES)


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_sector_capex_history(companies: dict[str, str]) -> pd.DataFrame:
    """Fetch quarterly CapEx history for companies (2024+)."""
    rows = []
    for ticker, name in companies.items():
        facts = fetch_company_facts(ticker)
        if not facts:
            continue
        best_df = pd.DataFrame()
        for tag in _CAPEX_TAGS:
            df = extract_xbrl_metric(facts, tag)
            if df.empty:
                continue
            df = df[df["end"] >= "2024-01-01"]
            if len(df) > len(best_df):
                best_df = df
        if not best_df.empty:
            best_df = best_df.sort_values("end").drop_duplicates(subset=["end"], keep="last")
            for _, r in best_df.iterrows():
                rows.append({
                    "ticker": ticker,
                    "company": name,
                    "date": r["end"],
                    "capex": r["val"],
                    "form": r["form"],
                })
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_energy_capex_history() -> pd.DataFrame:
    """Fetch quarterly CapEx history for all energy companies (2024+)."""
    return fetch_sector_capex_history(ENERGY_COMPANIES)


# ─────────────────────────────────────────────
# EARNINGS CALL TRANSCRIPTS (Motley Fool)
# ─────────────────────────────────────────────

_FOOL_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _fetch_fool_transcript_text(url: str) -> str:
    """Fetch and extract cleaned transcript text from a Motley Fool URL."""
    try:
        r = requests.get(url, headers={"User-Agent": _FOOL_UA}, timeout=20)
        if r.status_code != 200:
            return ""
    except Exception:
        return ""

    html = r.text
    html = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', '', html, flags=re.DOTALL)

    # Find the largest text block (the transcript body)
    blocks = re.split(r'<(?:div|section|article)[^>]*>', html)
    best = ""
    for b in blocks:
        text = re.sub(r'<[^>]+>', ' ', b)
        text = re.sub(r'&[a-zA-Z0-9#]+;', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        if len(text) > len(best):
            best = text
    return best


def extract_guidance_from_call(text: str) -> dict:
    """Extract forward guidance from an earnings call transcript."""
    result = {
        "raw_outlook": "", "revenue": None, "revenue_high": None,
        "gross_margin": None, "eps": None, "eps_high": None,
        "opex": None, "operating_income": None, "oi_high": None,
        "quarter": "", "source": "earnings_call",
        "capex": None, "production": None, "production_unit": None,
        "revenue_growth_low": None, "revenue_growth_high": None,
    }

    # Find guidance/outlook passages — multiple patterns for different styles
    patterns = [
        # Standard: revenue/EPS guidance
        r"((?:guidance|outlook)[^.]*(?:revenue|sales|earnings|margin|EPS|operating|\$)[^.]*\.(?:[^.]*(?:revenue|margin|expect|anticipat|between|\$)[^.]*\.){0,10})",
        r"((?:For the |In the )?(?:first|second|third|fourth|next|full|fiscal)\s+(?:quarter|year|half)[^.]*(?:expect|anticipat|project|guid)[^.]*(?:revenue|sales|margin|\$)[^.]*\.(?:[^.]*\.){0,8})",
        r"((?:We expect|We anticipate|We project|The company expects|Management expects)[^.]*(?:revenue|sales|earnings|margin)[^.]*\.(?:[^.]*\.){0,6})",
        # Energy-specific: production, CapEx, cost savings
        r"((?:guidance|outlook|Takeaway)[^.]*(?:production|CapEx|capital expenditure|capital spending|barrels|cost saving)[^.]*\.(?:[^.]*(?:production|CapEx|capital|barrels|billion|million|percent|expect|plan)[^.]*\.){0,12})",
        r"((?:production)[^.]*(?:expect|target|plan|anticipat|project)[^.]*(?:barrels|million|billion)[^.]*\.(?:[^.]*\.){0,6})",
        r"((?:capital expenditure|CapEx|capital spending|capital budget)[^.]*\$[\d,.]+\s*(?:billion|million)[^.]*\.(?:[^.]*\.){0,6})",
        r"((?:Management expects|expects?)\s+[^.]*(?:production|CapEx|capital|Permian|Guyana)[^.]*\.(?:[^.]*\.){0,8})",
    ]

    outlook_text = ""
    for pat in patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        if matches:
            candidate = max(matches, key=len)
            if len(candidate) > len(outlook_text):
                outlook_text = candidate

    if not outlook_text:
        return result

    result["raw_outlook"] = outlook_text[:1200]

    # Quarter
    q_match = re.search(
        r"(?:first|second|third|fourth|Q[1-4]|full[- ]?year|fiscal|March|June|September|December)\s+(?:quarter\s+)?(?:of\s+)?(?:fiscal\s+)?(?:year\s+)?(\d{4})",
        outlook_text, re.IGNORECASE,
    )
    if q_match:
        result["quarter"] = q_match.group(0).strip()

    # Revenue — range
    range_m = re.search(
        r"(?:net sales|revenue|total revenue)[^.]*?between\s+\$([\d,.]+)\s*(billion|million)\s+and\s+\$([\d,.]+)\s*(billion|million)",
        outlook_text, re.IGNORECASE,
    )
    if range_m:
        result["revenue"] = _parse_dollar(range_m.group(1), range_m.group(2))
        result["revenue_high"] = _parse_dollar(range_m.group(3), range_m.group(4))
    else:
        # Revenue with growth rate: "revenue to grow X%-Y% year over year"
        growth_m = re.search(
            r"revenue[^.]*?grow\s+([\d]+)\s*%\s*(?:to|-)\s*([\d]+)\s*%",
            outlook_text, re.IGNORECASE,
        )
        if growth_m:
            result["revenue_growth_low"] = float(growth_m.group(1))
            result["revenue_growth_high"] = float(growth_m.group(2))
        else:
            # Single figure
            for pat in [
                r"(?:net sales|revenue|total revenue)[^.]{0,80}?\$([\d,.]+)\s*(billion|million)",
                r"\$([\d,.]+)\s*(billion|million)[^.]{0,40}(?:revenue|net sales|in sales)",
                r"(?:expect|anticipat)[^.]*?revenue[^.]*?\$([\d,.]+)\s*(billion|million)",
            ]:
                rm = re.search(pat, outlook_text, re.IGNORECASE)
                if rm:
                    result["revenue"] = _parse_dollar(rm.group(1), rm.group(2))
                    break

    # Operating income — range
    oi_range = re.search(
        r"[Oo]perating\s+income[^.]*?between\s+\$([\d,.]+)\s*(billion|million)?\s+and\s+\$([\d,.]+)\s*(billion|million)",
        outlook_text, re.IGNORECASE,
    )
    if oi_range:
        unit_low = oi_range.group(2) or oi_range.group(4)
        result["operating_income"] = _parse_dollar(oi_range.group(1), unit_low)
        result["oi_high"] = _parse_dollar(oi_range.group(3), oi_range.group(4))

    # Gross margin — range: "X% to Y%" or single
    gm_range = re.search(
        r"[Gg]ross\s+margin[^.]*?([\d.]+)\s*%\s*(?:to|-)\s*([\d.]+)\s*%",
        outlook_text,
    )
    if gm_range:
        result["gross_margin"] = float(gm_range.group(1))
    else:
        gm = re.search(r"[Gg]ross\s+margin[s]?[^.]*?([\d.]+)\s*%", outlook_text)
        if gm:
            val = float(gm.group(1))
            if 10 < val < 100:
                result["gross_margin"] = val

    # EPS
    eps_m = re.search(r"(?:earnings per share|EPS|diluted)[^.]*?\$([\d.]+)", outlook_text, re.IGNORECASE)
    if eps_m:
        result["eps"] = float(eps_m.group(1))

    # ── Energy-specific: CapEx ──
    capex_m = re.search(
        r"(?:capital expenditure|CapEx|capital spending|capital budget)[^.]*?\$([\d,.]+)\s*(billion|million)",
        outlook_text, re.IGNORECASE,
    )
    if capex_m:
        result["capex"] = _parse_dollar(capex_m.group(1), capex_m.group(2))

    # ── Energy-specific: Production ──
    prod_m = re.search(
        r"(?:production)[^.]*?([\d,.]+)\s*(?:million)?\s*(?:oil equivalent\s+)?(?:barrels per day|boe(?:pd)?|bbl)",
        outlook_text, re.IGNORECASE,
    )
    if prod_m:
        result["production"] = float(prod_m.group(1).replace(",", ""))
        result["production_unit"] = "barrels/day"

    return result


# Known Motley Fool URL slugs for major companies
_FOOL_SLUGS = {
    "XOM": ["exxon-mobil"], "CVX": ["chevron"], "COP": ["conocophillips"],
    "EOG": ["eog-resources"], "SLB": ["schlumberger", "slb"], "MPC": ["marathon-petroleum"],
    "OXY": ["occidental-petroleum"], "PSX": ["phillips-66"], "VLO": ["valero-energy"],
    "DVN": ["devon-energy"], "AAPL": ["apple"], "NVDA": ["nvidia"],
    "MSFT": ["microsoft"], "AMZN": ["amazon", "amazon-com", "amazoncom"],
    "GOOGL": ["alphabet"], "META": ["meta-platforms"], "TSLA": ["tesla"],
    "CRM": ["salesforce"], "NFLX": ["netflix"], "AMD": ["advanced-micro-devices"],
    "INTC": ["intel"], "COST": ["costco-wholesale"], "JPM": ["jpmorgan-chase"],
    "BAC": ["bank-of-america"], "WMT": ["walmart"],
}

# Fiscal quarter labels vary by company fiscal year
_FISCAL_QUARTERS = {
    # Companies with non-calendar fiscal years (FY ends ≠ Dec)
    "AAPL": [(10, "q4"), (1, "q1"), (5, "q2"), (8, "q3")],  # FY ends Sep
    "NVDA": [(2, "q4"), (5, "q1"), (8, "q2"), (11, "q3")],   # FY ends Jan
    "MSFT": [(1, "q2"), (4, "q3"), (7, "q4"), (10, "q1")],   # FY ends Jun
    "CRM": [(2, "q4"), (5, "q1"), (8, "q2"), (11, "q3")],    # FY ends Jan
    "COST": [(12, "q1"), (3, "q2"), (5, "q3"), (9, "q4")],   # FY ends Aug
}


def _guess_quarter_labels(ticker: str, filing_month: int, filing_year: int) -> list[str]:
    """Generate likely quarter labels for a Fool transcript URL."""
    labels = []
    if ticker in _FISCAL_QUARTERS:
        for month, qlabel in _FISCAL_QUARTERS[ticker]:
            if abs(month - filing_month) <= 1 or abs(month - filing_month) >= 11:
                labels.append(f"{qlabel}-{filing_year}")
                labels.append(f"{qlabel}-{filing_year + 1}")
                labels.append(f"{qlabel}-{filing_year - 1}")
    # Default calendar-year quarters
    cal_q = {1: 4, 2: 4, 3: 4, 4: 1, 5: 1, 6: 2, 7: 2, 8: 3, 9: 3, 10: 3, 11: 3, 12: 4}
    q = cal_q.get(filing_month, 4)
    cal_year = filing_year if filing_month >= 3 else filing_year - 1
    labels.extend([f"q{q}-{cal_year}", f"q{q}-{cal_year + 1}", f"q{q}-{cal_year - 1}"])
    return list(dict.fromkeys(labels))  # dedupe


@st.cache_data(ttl=86400, show_spinner=False)
def discover_fool_transcript_urls(ticker: str, filing_dates: list[str] | None = None, limit: int = 6) -> list[str]:
    """Auto-discover Motley Fool transcript URLs by guessing from 8-K filing dates."""
    ticker_upper = ticker.upper()
    ticker_lower = ticker.lower()
    slugs = _FOOL_SLUGS.get(ticker_upper, [ticker_lower])
    suffixes = ["earnings-call-transcript", "earnings-transcript"]

    # Get filing dates from EDGAR if not provided
    if not filing_dates:
        filings = fetch_earnings_filings(ticker_upper, limit=limit)
        filing_dates = [f["filed"] for f in filings]

    found = []
    for filed in filing_dates:
        try:
            d = pd.to_datetime(filed)
        except Exception:
            continue

        labels = _guess_quarter_labels(ticker_upper, d.month, d.year)

        hit = False
        for day_offset in range(-1, 3):
            if hit:
                break
            dd = d + pd.Timedelta(days=day_offset)
            date_path = f"{dd.year}/{dd.month:02d}/{dd.day:02d}"
            for slug in slugs:
                if hit:
                    break
                for qlabel in labels:
                    if hit:
                        break
                    for suffix in suffixes:
                        url = f"https://www.fool.com/earnings/call-transcripts/{date_path}/{slug}-{ticker_lower}-{qlabel}-{suffix}/"
                        try:
                            r = requests.head(url, headers={"User-Agent": _FOOL_UA},
                                              timeout=5, allow_redirects=True)
                            if r.status_code == 200:
                                found.append(url)
                                hit = True
                                break
                        except Exception:
                            continue

        if len(found) >= limit:
            break

    return found


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_transcript_guidance(ticker: str, transcript_urls: list[str]) -> pd.DataFrame:
    """Fetch and parse guidance from Motley Fool earnings call transcripts."""
    records = []
    for url in transcript_urls:
        text = _fetch_fool_transcript_text(url)
        if not text or len(text) < 1000:
            continue
        g = extract_guidance_from_call(text)
        if not g["raw_outlook"]:
            continue
        # Extract filing date from URL: /YYYY/MM/DD/
        date_m = re.search(r'/(\d{4}/\d{2}/\d{2})/', url)
        filed = date_m.group(1).replace("/", "-") if date_m else ""
        has_data = any([
            g["revenue"], g["gross_margin"], g["eps"],
            g.get("revenue_growth_low"), g["operating_income"],
            g.get("capex"), g.get("production"),
        ])
        if not has_data:
            continue
        records.append({
            "filed": filed,
            "quarter": g["quarter"],
            "revenue": g["revenue"],
            "revenue_high": g["revenue_high"],
            "gross_margin": g["gross_margin"],
            "eps": g["eps"],
            "eps_high": g.get("eps_high"),
            "opex": g["opex"],
            "operating_income": g["operating_income"],
            "oi_high": g.get("oi_high"),
            "revenue_growth_low": g.get("revenue_growth_low"),
            "revenue_growth_high": g.get("revenue_growth_high"),
            "capex": g.get("capex"),
            "production": g.get("production"),
            "production_unit": g.get("production_unit"),
            "outlook": g["raw_outlook"],
            "source": "earnings_call",
            "url": url,
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    df["filed"] = pd.to_datetime(df["filed"], errors="coerce")
    df = df.sort_values("filed").reset_index(drop=True)
    return df
