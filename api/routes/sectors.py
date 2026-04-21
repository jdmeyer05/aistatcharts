"""Sector analysis endpoints — 11 SPDR sectors (Energy/XLE, Financials/XLF, etc).

One dynamic page on the frontend hits these endpoints per selected sector ETF.
Data shapes mirror what src/sector_analysis.py renders into the Streamlit 8-tab UI.
"""
from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_current_user
from src._cache_util import result_cached as _result_cached

logger = logging.getLogger(__name__)
router = APIRouter()


# ═══════════════════════════════════════════════
# STATIC CONFIG DATA — 11 SPDR SECTORS
# ═══════════════════════════════════════════════

SECTOR_CONFIGS: dict[str, dict] = {
    "XLE": {
        "etf": "XLE",
        "label": "Energy (XLE)",
        "title": "Energy Sector Analysis",
        "subtitle": "Top 10 US oil & gas companies — financials, CapEx, valuation, guidance, and market positioning.",
        "companies": {
            "XOM": "ExxonMobil", "CVX": "Chevron", "COP": "ConocoPhillips", "EOG": "EOG Resources",
            "SLB": "SLB (Schlumberger)", "MPC": "Marathon Petroleum", "OXY": "Occidental Petroleum",
            "PSX": "Phillips 66", "VLO": "Valero Energy", "DVN": "Devon Energy",
        },
        "subsectors": {
            "Integrated": ["XOM", "CVX"],
            "E&P": ["COP", "EOG", "OXY", "DVN"],
            "Refining": ["MPC", "PSX", "VLO"],
            "Services": ["SLB"],
        },
        "macro_overlay": {"fred_series": "DCOILWTICO", "label": "WTI Crude ($/bbl)"},
        "factor_proxies": ["SPY", "USO", "UUP", "TLT"],
        "cot_commodities": [["Crude Oil", "crude_oil"], ["Natural Gas", "natural_gas"]],
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "XOM", "company": "ExxonMobil", "rev_est_y": 352.0, "rev_growth": "+6%", "eps_est_y": 8.09, "eps_est_ny": 8.92, "capex_guidance": 27.0, "capex_note": "FY25 <$27-29B range; FY26 expected similar", "production": "4.7M boe/d", "price_target": 155, "rating": "Buy", "fwd_pe": 18.3, "outlook": "Highest production in 40+ years. Permian record 1.8M boe/d. $15B cumulative cost savings. 43 consecutive years of dividend increases."},
                {"ticker": "CVX", "company": "Chevron", "rev_est_y": 202.0, "rev_growth": "+7%", "eps_est_y": 8.77, "eps_est_ny": 9.77, "capex_guidance": 17.3, "capex_note": "FY25 organic $17-17.5B incl Hess", "production": None, "price_target": 196, "rating": "Buy", "fwd_pe": 21.0, "outlook": "Q3 organic CapEx $4.4B. Hess integration underway. Golden Pass LNG mechanically complete, first LNG expected early 2026."},
                {"ticker": "COP", "company": "ConocoPhillips", "rev_est_y": 60.0, "rev_growth": "-3%", "eps_est_y": 6.37, "eps_est_ny": 7.24, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 126, "rating": "Buy", "fwd_pe": 17.8, "outlook": "Free cash flow inflection expected to deliver $1B incremental/year 2026-2028. Willow project to add $4B in 2029."},
                {"ticker": "EOG", "company": "EOG Resources", "rev_est_y": 24.0, "rev_growth": "+5%", "eps_est_y": 11.08, "eps_est_ny": 11.72, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 141, "rating": "Buy", "fwd_pe": 12.3, "outlook": "Premium returns strategy. Low-cost operator. No explicit CapEx guidance in press release."},
                {"ticker": "SLB", "company": "SLB (Schlumberger)", "rev_est_y": 37.0, "rev_growth": "+4%", "eps_est_y": 2.82, "eps_est_ny": 3.33, "capex_guidance": 2.5, "capex_note": "Capital investments $2.5B; intensity target 5-7%", "production": None, "price_target": 55, "rating": "Buy", "fwd_pe": 15.6, "outlook": "ChampionX synergies and digital expansion driving margins. Subsea market 20% higher tree award run rate. Capital intensity target 5-7%."},
                {"ticker": "MPC", "company": "Marathon Petroleum", "rev_est_y": 127.0, "rev_growth": "-6%", "eps_est_y": 17.61, "eps_est_ny": 15.65, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 213, "rating": "Buy", "fwd_pe": 15.4, "outlook": "Largest US refiner. Revenue decline reflects lower refining margins."},
                {"ticker": "OXY", "company": "Occidental Petroleum", "rev_est_y": 23.0, "rev_growth": "+5%", "eps_est_y": 2.86, "eps_est_ny": 2.69, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 58, "rating": "Hold", "fwd_pe": 23.0, "outlook": "Buffett-backed. CrownRock acquisition integration. High leverage relative to peers. EPS declining into next year."},
                {"ticker": "PSX", "company": "Phillips 66", "rev_est_y": 141.0, "rev_growth": "+3%", "eps_est_y": 13.00, "eps_est_ny": 13.55, "capex_guidance": 2.4, "capex_note": "FY capital budget $2.4B", "production": None, "price_target": 165, "rating": "Buy", "fwd_pe": 13.4, "outlook": "~$8B operating cash flow expected. ~$4B available for debt reduction and buybacks after CapEx."},
                {"ticker": "VLO", "company": "Valero Energy", "rev_est_y": 116.0, "rev_growth": "-5%", "eps_est_y": 16.36, "eps_est_ny": 14.40, "capex_guidance": 1.6, "capex_note": "Q3 CapEx $409M ($364M sustaining); ~$1.6B annualized", "production": None, "price_target": 214, "rating": "Buy", "fwd_pe": 16.3, "outlook": "Gulf Coast throughput guidance provided quarterly. Primarily sustaining CapEx with modest growth initiatives."},
                {"ticker": "DVN", "company": "Devon Energy", "rev_est_y": 20.0, "rev_growth": "+14%", "eps_est_y": 4.00, "eps_est_ny": 4.69, "capex_guidance": None, "capex_note": None, "production": "830K boe/d", "price_target": 53, "rating": "Buy", "fwd_pe": 11.0, "outlook": "FY2026 production ~830K boe/d. Guidance unchanged despite 10K boe/d weather disruption in Jan. Merger close planned."},
            ],
        },
    },
    "XLF": {
        "etf": "XLF",
        "label": "Financials (XLF)",
        "title": "Financials Sector Analysis",
        "subtitle": "Top 10 US financial companies — banking, asset management, insurance, and payments.",
        "companies": {
            "JPM": "JPMorgan Chase", "BAC": "Bank of America", "WFC": "Wells Fargo",
            "GS": "Goldman Sachs", "MS": "Morgan Stanley", "BLK": "BlackRock",
            "SCHW": "Charles Schwab", "C": "Citigroup", "AXP": "American Express",
            "MMC": "Marsh McLennan",
        },
        "subsectors": {
            "Banks": ["JPM", "BAC", "WFC", "C"],
            "Investment Banks": ["GS", "MS"],
            "Asset Management": ["BLK", "SCHW"],
            "Insurance & Payments": ["AXP", "MMC"],
        },
        "macro_overlay": {"fred_series": "DFF", "label": "Fed Funds Rate (%)"},
        "factor_proxies": ["SPY", "XLF", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "JPM", "company": "JPMorgan Chase", "rev_est_y": 178.0, "rev_growth": "+8%", "eps_est_y": 18.50, "eps_est_ny": 19.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 265, "rating": "Buy", "fwd_pe": 13.5, "outlook": "Record revenue in CIB and AWM. NII guidance ~$94B for FY26. Credit costs normalizing. 10% dividend increase announced."},
                {"ticker": "BAC", "company": "Bank of America", "rev_est_y": 105.0, "rev_growth": "+6%", "eps_est_y": 3.85, "eps_est_ny": 4.20, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 52, "rating": "Buy", "fwd_pe": 12.0, "outlook": "NII inflection underway as fixed-rate assets reprice higher. Digital engagement record 48M active users. Expense discipline on track."},
                {"ticker": "WFC", "company": "Wells Fargo", "rev_est_y": 82.0, "rev_growth": "+4%", "eps_est_y": 5.40, "eps_est_ny": 6.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 78, "rating": "Hold", "fwd_pe": 11.5, "outlook": "Asset cap removal remains key catalyst. Efficiency ratio improving toward 60% target. Fee income diversification ongoing."},
                {"ticker": "GS", "company": "Goldman Sachs", "rev_est_y": 55.0, "rev_growth": "+12%", "eps_est_y": 42.00, "eps_est_ny": 46.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 620, "rating": "Buy", "fwd_pe": 14.0, "outlook": "Investment banking recovery driving upside. AWM now >$3T AUS. Exited consumer lending. Capital markets activity rebounding."},
                {"ticker": "MS", "company": "Morgan Stanley", "rev_est_y": 62.0, "rev_growth": "+10%", "eps_est_y": 8.50, "eps_est_ny": 9.20, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 130, "rating": "Buy", "fwd_pe": 15.0, "outlook": "Wealth management AUM record $7.2T. Integration of E*Trade client assets progressing. Target 30% pre-tax margin in WM."},
                {"ticker": "BLK", "company": "BlackRock", "rev_est_y": 22.0, "rev_growth": "+14%", "eps_est_y": 45.00, "eps_est_ny": 50.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 1050, "rating": "Buy", "fwd_pe": 22.0, "outlook": "AUM surpassed $11.5T. ETF flows remain dominant. Private markets buildout via GIP acquisition. Technology services (Aladdin) growing double-digits."},
                {"ticker": "SCHW", "company": "Charles Schwab", "rev_est_y": 21.0, "rev_growth": "+9%", "eps_est_y": 4.20, "eps_est_ny": 4.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 88, "rating": "Buy", "fwd_pe": 20.0, "outlook": "Client asset reallocation from cash sorting stabilizing. Ameritrade integration complete. Net new asset growth accelerating."},
                {"ticker": "C", "company": "Citigroup", "rev_est_y": 82.0, "rev_growth": "+5%", "eps_est_y": 7.00, "eps_est_ny": 8.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 85, "rating": "Hold", "fwd_pe": 10.0, "outlook": "Simplification strategy: exited 14 consumer markets. Targeting 11-12% RoTCE medium-term. Consent order remediation ongoing."},
                {"ticker": "AXP", "company": "American Express", "rev_est_y": 68.0, "rev_growth": "+9%", "eps_est_y": 15.00, "eps_est_ny": 16.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 310, "rating": "Buy", "fwd_pe": 19.0, "outlook": "Premium card spend growing. Millennial/Gen Z card acquisitions at record. Revenue growth guidance 8-10% through 2026."},
                {"ticker": "MMC", "company": "Marsh McLennan", "rev_est_y": 25.0, "rev_growth": "+7%", "eps_est_y": 9.50, "eps_est_ny": 10.30, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 235, "rating": "Hold", "fwd_pe": 25.0, "outlook": "Organic revenue growth 5-6%. Insurance broking benefiting from hard market. Consulting (Oliver Wyman, Mercer) stable."},
            ],
        },
    },
    "XLK": {
        "etf": "XLK",
        "label": "Technology (XLK)",
        "title": "Technology Sector Analysis",
        "subtitle": "Top 10 US technology companies — semiconductors, software, cloud, and IT services.",
        "companies": {
            "AAPL": "Apple", "MSFT": "Microsoft", "NVDA": "NVIDIA", "AVGO": "Broadcom",
            "CRM": "Salesforce", "ORCL": "Oracle", "AMD": "AMD", "ADBE": "Adobe",
            "ACN": "Accenture", "CSCO": "Cisco Systems",
        },
        "subsectors": {
            "Semiconductors": ["NVDA", "AVGO", "AMD"],
            "Software": ["MSFT", "CRM", "ORCL", "ADBE"],
            "Hardware": ["AAPL", "CSCO"],
            "IT Services": ["ACN"],
        },
        "macro_overlay": {"fred_series": "DGS10", "label": "10Y Treasury Yield (%)"},
        "factor_proxies": ["SPY", "QQQ", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "AAPL", "company": "Apple", "rev_est_y": 420.0, "rev_growth": "+5%", "eps_est_y": 7.80, "eps_est_ny": 8.40, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 245, "rating": "Buy", "fwd_pe": 30.0, "outlook": "Services revenue >$100B run rate. iPhone cycle stable. India manufacturing ramp. Vision Pro niche but growing."},
                {"ticker": "MSFT", "company": "Microsoft", "rev_est_y": 280.0, "rev_growth": "+14%", "eps_est_y": 14.00, "eps_est_ny": 16.00, "capex_guidance": 80.0, "capex_note": "FY26 CapEx ~$80B (AI infrastructure)", "production": None, "price_target": 510, "rating": "Buy", "fwd_pe": 32.0, "outlook": "Azure AI revenue tripling YoY. Copilot adoption accelerating across M365. LinkedIn and Gaming stable contributors."},
                {"ticker": "NVDA", "company": "NVIDIA", "rev_est_y": 200.0, "rev_growth": "+55%", "eps_est_y": 4.50, "eps_est_ny": 5.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 175, "rating": "Buy", "fwd_pe": 35.0, "outlook": "Blackwell GPU ramp in full production. Data center revenue dominant. Sovereign AI deals expanding TAM."},
                {"ticker": "AVGO", "company": "Broadcom", "rev_est_y": 75.0, "rev_growth": "+20%", "eps_est_y": 6.50, "eps_est_ny": 7.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 250, "rating": "Buy", "fwd_pe": 30.0, "outlook": "VMware integration boosting margins. Custom AI accelerator (XPU) wins with 3 hyperscalers. Networking + broadband stable."},
                {"ticker": "CRM", "company": "Salesforce", "rev_est_y": 40.0, "rev_growth": "+9%", "eps_est_y": 10.50, "eps_est_ny": 12.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 350, "rating": "Buy", "fwd_pe": 28.0, "outlook": "Agentforce AI platform driving new bookings. Operating margin expanding to 33%+. First dividend initiated."},
                {"ticker": "ORCL", "company": "Oracle", "rev_est_y": 65.0, "rev_growth": "+12%", "eps_est_y": 6.20, "eps_est_ny": 7.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 200, "rating": "Buy", "fwd_pe": 26.0, "outlook": "OCI growing 50%+. Multi-cloud strategy with AWS and Azure. RPO >$130B."},
                {"ticker": "AMD", "company": "AMD", "rev_est_y": 35.0, "rev_growth": "+25%", "eps_est_y": 5.00, "eps_est_ny": 6.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 180, "rating": "Buy", "fwd_pe": 28.0, "outlook": "MI300X AI GPU revenue >$5B. EPYC server CPU share gains continuing. Client PC recovery."},
                {"ticker": "ADBE", "company": "Adobe", "rev_est_y": 24.0, "rev_growth": "+10%", "eps_est_y": 20.00, "eps_est_ny": 22.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 550, "rating": "Hold", "fwd_pe": 25.0, "outlook": "Firefly AI integration across Creative Cloud. Document Cloud growing 20%+. Net new ARR re-accelerating."},
                {"ticker": "ACN", "company": "Accenture", "rev_est_y": 67.0, "rev_growth": "+5%", "eps_est_y": 13.50, "eps_est_ny": 14.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 380, "rating": "Hold", "fwd_pe": 26.0, "outlook": "GenAI bookings >$4B cumulative. Managed services growing. Consulting spending normalizing."},
                {"ticker": "CSCO", "company": "Cisco Systems", "rev_est_y": 58.0, "rev_growth": "+7%", "eps_est_y": 3.70, "eps_est_ny": 3.95, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 68, "rating": "Hold", "fwd_pe": 16.0, "outlook": "Splunk integration driving security/observability bundle. AI networking ramping. Subscription ARR >$30B."},
            ],
        },
    },
    "XLV": {
        "etf": "XLV",
        "label": "Healthcare (XLV)",
        "title": "Healthcare Sector Analysis",
        "subtitle": "Top 10 US healthcare companies — pharma, biotech, medtech, and managed care.",
        "companies": {
            "UNH": "UnitedHealth Group", "LLY": "Eli Lilly", "JNJ": "Johnson & Johnson",
            "ABBV": "AbbVie", "MRK": "Merck", "TMO": "Thermo Fisher",
            "ABT": "Abbott Labs", "AMGN": "Amgen", "DHR": "Danaher", "PFE": "Pfizer",
        },
        "subsectors": {
            "Pharma": ["LLY", "JNJ", "ABBV", "MRK", "PFE"],
            "Biotech": ["AMGN"],
            "MedTech & Life Sciences": ["TMO", "ABT", "DHR"],
            "Managed Care": ["UNH"],
        },
        "macro_overlay": {"fred_series": "CPIMEDSL", "label": "CPI Medical Care (Index)"},
        "factor_proxies": ["SPY", "XLV", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "UNH", "company": "UnitedHealth Group", "rev_est_y": 420.0, "rev_growth": "+8%", "eps_est_y": 29.00, "eps_est_ny": 32.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 600, "rating": "Buy", "fwd_pe": 18.0, "outlook": "Optum Health driving growth. Medicare Advantage enrollment stable."},
                {"ticker": "LLY", "company": "Eli Lilly", "rev_est_y": 58.0, "rev_growth": "+28%", "eps_est_y": 22.00, "eps_est_ny": 28.00, "capex_guidance": 12.0, "capex_note": "FY26 CapEx ~$12B (GLP-1 manufacturing)", "production": None, "price_target": 1000, "rating": "Buy", "fwd_pe": 40.0, "outlook": "Mounjaro/Zepbound demand exceeding supply. Donanemab launched. Revenue could double by 2028."},
                {"ticker": "JNJ", "company": "Johnson & Johnson", "rev_est_y": 92.0, "rev_growth": "+4%", "eps_est_y": 10.50, "eps_est_ny": 11.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 180, "rating": "Hold", "fwd_pe": 15.0, "outlook": "MedTech growing mid-single digits. Innovative medicine portfolio diversifying post-Stelara LOE."},
                {"ticker": "ABBV", "company": "AbbVie", "rev_est_y": 60.0, "rev_growth": "+5%", "eps_est_y": 12.00, "eps_est_ny": 13.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 210, "rating": "Buy", "fwd_pe": 16.0, "outlook": "Skyrizi + Rinvoq replacing Humira revenue. Combined to exceed Humira peak."},
                {"ticker": "MRK", "company": "Merck", "rev_est_y": 65.0, "rev_growth": "+6%", "eps_est_y": 8.50, "eps_est_ny": 9.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 140, "rating": "Buy", "fwd_pe": 14.0, "outlook": "Keytruda franchise >$28B. Subcutaneous Keytruda in development. Winrevair launch strong."},
                {"ticker": "TMO", "company": "Thermo Fisher", "rev_est_y": 44.0, "rev_growth": "+5%", "eps_est_y": 22.50, "eps_est_ny": 25.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 620, "rating": "Buy", "fwd_pe": 26.0, "outlook": "Life sciences demand normalizing. Pharma services (CDMO) growing. Long-term organic growth 7-9%."},
                {"ticker": "ABT", "company": "Abbott Labs", "rev_est_y": 43.0, "rev_growth": "+7%", "eps_est_y": 5.00, "eps_est_ny": 5.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 135, "rating": "Buy", "fwd_pe": 24.0, "outlook": "FreeStyle Libre growing 20%+. Medical devices leading. Double-digit EPS growth trajectory."},
                {"ticker": "AMGN", "company": "Amgen", "rev_est_y": 35.0, "rev_growth": "+9%", "eps_est_y": 20.00, "eps_est_ny": 22.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 320, "rating": "Hold", "fwd_pe": 15.0, "outlook": "Horizon acquisition integrated. MariTide (obesity) Phase 3 pending. Tezepelumab growing."},
                {"ticker": "DHR", "company": "Danaher", "rev_est_y": 24.0, "rev_growth": "+4%", "eps_est_y": 8.00, "eps_est_ny": 9.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 275, "rating": "Hold", "fwd_pe": 30.0, "outlook": "Bioprocessing recovery underway. Diagnostics normalizing. Veralto separation complete."},
                {"ticker": "PFE", "company": "Pfizer", "rev_est_y": 62.0, "rev_growth": "+3%", "eps_est_y": 3.00, "eps_est_ny": 3.30, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 32, "rating": "Hold", "fwd_pe": 11.0, "outlook": "Seagen oncology portfolio ramping. $4B cost savings on track. Dividend yield ~5%."},
            ],
        },
    },
    "XLI": {
        "etf": "XLI",
        "label": "Industrials (XLI)",
        "title": "Industrials Sector Analysis",
        "subtitle": "Top 10 US industrial companies — aerospace, machinery, defense, transports, and business services.",
        "companies": {
            "GE": "GE Aerospace", "CAT": "Caterpillar", "UNP": "Union Pacific",
            "HON": "Honeywell", "RTX": "RTX (Raytheon)", "DE": "Deere & Co",
            "LMT": "Lockheed Martin", "BA": "Boeing", "ETN": "Eaton Corp", "ADP": "ADP",
        },
        "subsectors": {
            "Aerospace & Defense": ["GE", "RTX", "LMT", "BA"],
            "Machinery": ["CAT", "DE", "ETN"],
            "Transport": ["UNP"],
            "Diversified / Services": ["HON", "ADP"],
        },
        "macro_overlay": {"fred_series": "MANEMP", "label": "Manufacturing Employment (Thousands)"},
        "factor_proxies": ["SPY", "XLI", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "GE", "company": "GE Aerospace", "rev_est_y": 40.0, "rev_growth": "+12%", "eps_est_y": 5.50, "eps_est_ny": 6.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 220, "rating": "Buy", "fwd_pe": 35.0, "outlook": "LEAP engine installed base growing. Services revenue accelerating. Operating margin expanding toward 20%+."},
                {"ticker": "CAT", "company": "Caterpillar", "rev_est_y": 67.0, "rev_growth": "+3%", "eps_est_y": 22.00, "eps_est_ny": 23.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 400, "rating": "Buy", "fwd_pe": 17.0, "outlook": "Data center and energy infrastructure driving construction demand. Services revenue >$25B."},
                {"ticker": "UNP", "company": "Union Pacific", "rev_est_y": 25.0, "rev_growth": "+4%", "eps_est_y": 12.00, "eps_est_ny": 13.00, "capex_guidance": 3.6, "capex_note": "FY26 CapEx ~$3.6B", "production": None, "price_target": 260, "rating": "Hold", "fwd_pe": 21.0, "outlook": "PSR driving efficiency. Intermodal volumes recovering. Operating ratio target <58%."},
                {"ticker": "HON", "company": "Honeywell", "rev_est_y": 40.0, "rev_growth": "+5%", "eps_est_y": 10.50, "eps_est_ny": 11.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 230, "rating": "Hold", "fwd_pe": 21.0, "outlook": "Aerospace aftermarket strong. 3-way split announced. Carrier acquisition integration ongoing."},
                {"ticker": "RTX", "company": "RTX (Raytheon)", "rev_est_y": 82.0, "rev_growth": "+7%", "eps_est_y": 6.20, "eps_est_ny": 7.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 140, "rating": "Buy", "fwd_pe": 20.0, "outlook": "Defense backlog >$200B. Pratt & Whitney GTF resolved. Free cash flow improving."},
                {"ticker": "DE", "company": "Deere & Co", "rev_est_y": 42.0, "rev_growth": "-8%", "eps_est_y": 18.00, "eps_est_ny": 21.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 450, "rating": "Hold", "fwd_pe": 22.0, "outlook": "Ag cycle normalizing. Precision ag adoption driving mix-up. Construction equipment resilient."},
                {"ticker": "LMT", "company": "Lockheed Martin", "rev_est_y": 73.0, "rev_growth": "+5%", "eps_est_y": 28.00, "eps_est_ny": 30.00, "capex_guidance": 2.0, "capex_note": "FY26 CapEx ~$2B", "production": None, "price_target": 550, "rating": "Buy", "fwd_pe": 18.0, "outlook": "F-35 deliveries stabilizing. Backlog >$166B. Global rearmament benefiting M&FC."},
                {"ticker": "BA", "company": "Boeing", "rev_est_y": 80.0, "rev_growth": "+15%", "eps_est_y": -2.00, "eps_est_ny": 3.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 200, "rating": "Hold", "fwd_pe": None, "outlook": "737 MAX ramp to 38/month. Quality improvement plan underway. FCF positive expected late 2026."},
                {"ticker": "ETN", "company": "Eaton Corp", "rev_est_y": 26.0, "rev_growth": "+10%", "eps_est_y": 11.50, "eps_est_ny": 13.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 350, "rating": "Buy", "fwd_pe": 30.0, "outlook": "Electrical infrastructure megatrend: data centers, EVs, grid modernization. Record backlog."},
                {"ticker": "ADP", "company": "ADP", "rev_est_y": 20.0, "rev_growth": "+7%", "eps_est_y": 10.50, "eps_est_ny": 11.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 305, "rating": "Hold", "fwd_pe": 28.0, "outlook": "Payroll/HCM leader. Client retention 92%+. Float revenue benefiting from rates."},
            ],
        },
    },
    "XLC": {
        "etf": "XLC",
        "label": "Communication Services (XLC)",
        "title": "Communication Services Sector Analysis",
        "subtitle": "Top 10 US communication companies — social/search, telecom, media, and gaming.",
        "companies": {
            "META": "Meta Platforms", "GOOGL": "Alphabet", "NFLX": "Netflix",
            "T": "AT&T", "CMCSA": "Comcast", "VZ": "Verizon",
            "DIS": "Walt Disney", "TMUS": "T-Mobile US", "EA": "Electronic Arts",
            "CHTR": "Charter Comms",
        },
        "subsectors": {
            "Social & Search": ["META", "GOOGL"],
            "Streaming & Media": ["NFLX", "DIS"],
            "Telecom": ["T", "VZ", "TMUS"],
            "Cable & Gaming": ["CMCSA", "CHTR", "EA"],
        },
        "macro_overlay": {"fred_series": "CPIAUCSL", "label": "CPI All Items (Index)"},
        "factor_proxies": ["SPY", "QQQ", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "META", "company": "Meta Platforms", "rev_est_y": 185.0, "rev_growth": "+18%", "eps_est_y": 25.00, "eps_est_ny": 29.00, "capex_guidance": 65.0, "capex_note": "FY26 CapEx $60-70B (AI)", "production": None, "price_target": 700, "rating": "Buy", "fwd_pe": 24.0, "outlook": "AI-driven ad targeting improving. Reels growing. WhatsApp business scaling. Llama AI investment."},
                {"ticker": "GOOGL", "company": "Alphabet", "rev_est_y": 380.0, "rev_growth": "+12%", "eps_est_y": 9.50, "eps_est_ny": 11.00, "capex_guidance": 75.0, "capex_note": "FY26 CapEx ~$75B", "production": None, "price_target": 210, "rating": "Buy", "fwd_pe": 21.0, "outlook": "Google Cloud >$40B run rate. Search AI Overviews increasing engagement. YouTube >$50B."},
                {"ticker": "NFLX", "company": "Netflix", "rev_est_y": 45.0, "rev_growth": "+14%", "eps_est_y": 25.00, "eps_est_ny": 30.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 1050, "rating": "Buy", "fwd_pe": 38.0, "outlook": "Ad tier 70M+ MAU. Live sports driving engagement. Operating margin >28%."},
                {"ticker": "T", "company": "AT&T", "rev_est_y": 125.0, "rev_growth": "+2%", "eps_est_y": 2.30, "eps_est_ny": 2.50, "capex_guidance": 22.0, "capex_note": "FY26 CapEx ~$22B", "production": None, "price_target": 28, "rating": "Hold", "fwd_pe": 10.0, "outlook": "Fiber subscriber additions accelerating. FCF ~$18B. Dividend yield ~5%."},
                {"ticker": "CMCSA", "company": "Comcast", "rev_est_y": 122.0, "rev_growth": "+3%", "eps_est_y": 4.50, "eps_est_ny": 4.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 48, "rating": "Hold", "fwd_pe": 10.0, "outlook": "Broadband sub losses stabilizing. Peacock losses narrowing. Theme parks strong."},
                {"ticker": "VZ", "company": "Verizon", "rev_est_y": 135.0, "rev_growth": "+1%", "eps_est_y": 4.80, "eps_est_ny": 5.00, "capex_guidance": 18.0, "capex_note": "FY26 CapEx $17.5-18.5B", "production": None, "price_target": 48, "rating": "Hold", "fwd_pe": 9.0, "outlook": "Wireless service revenue growing 3%+. FWA gaining broadband share. Dividend yield >6%."},
                {"ticker": "DIS", "company": "Walt Disney", "rev_est_y": 95.0, "rev_growth": "+5%", "eps_est_y": 5.80, "eps_est_ny": 6.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 130, "rating": "Buy", "fwd_pe": 20.0, "outlook": "Disney+ profitable. Parks record revenue. ESPN DTC launching."},
                {"ticker": "TMUS", "company": "T-Mobile US", "rev_est_y": 84.0, "rev_growth": "+6%", "eps_est_y": 10.50, "eps_est_ny": 12.00, "capex_guidance": 10.0, "capex_note": "FY26 CapEx ~$9-10B", "production": None, "price_target": 240, "rating": "Buy", "fwd_pe": 22.0, "outlook": "Industry-leading postpaid net adds. 5G network advantage. FCF >$18B."},
                {"ticker": "EA", "company": "Electronic Arts", "rev_est_y": 8.0, "rev_growth": "+4%", "eps_est_y": 7.50, "eps_est_ny": 8.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 160, "rating": "Hold", "fwd_pe": 20.0, "outlook": "EA Sports FC growing. Live services 75%+ of revenue. College Football 26 pending."},
                {"ticker": "CHTR", "company": "Charter Comms", "rev_est_y": 56.0, "rev_growth": "+1%", "eps_est_y": 35.00, "eps_est_ny": 40.00, "capex_guidance": 11.0, "capex_note": "FY26 CapEx ~$11B", "production": None, "price_target": 420, "rating": "Hold", "fwd_pe": 11.0, "outlook": "Network evolution buildout ongoing. Mobile line growth strong. Broadband ARPU increasing."},
            ],
        },
    },
    "XLY": {
        "etf": "XLY",
        "label": "Consumer Discretionary (XLY)",
        "title": "Consumer Discretionary Sector Analysis",
        "subtitle": "Top 10 US consumer discretionary companies — retail, e-commerce, restaurants, travel, and autos.",
        "companies": {
            "AMZN": "Amazon", "TSLA": "Tesla", "HD": "Home Depot", "MCD": "McDonald's",
            "NKE": "Nike", "LOW": "Lowe's", "BKNG": "Booking Holdings",
            "SBUX": "Starbucks", "TJX": "TJX Companies", "CMG": "Chipotle",
        },
        "subsectors": {
            "E-Commerce & Tech": ["AMZN", "TSLA"],
            "Home Improvement": ["HD", "LOW"],
            "Restaurants": ["MCD", "SBUX", "CMG"],
            "Retail & Travel": ["NKE", "BKNG", "TJX"],
        },
        "macro_overlay": {"fred_series": "UMCSENT", "label": "Consumer Sentiment (Index)"},
        "factor_proxies": ["SPY", "XLY", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "AMZN", "company": "Amazon", "rev_est_y": 680.0, "rev_growth": "+11%", "eps_est_y": 6.50, "eps_est_ny": 8.00, "capex_guidance": 100.0, "capex_note": "FY26 CapEx ~$100B", "production": None, "price_target": 240, "rating": "Buy", "fwd_pe": 32.0, "outlook": "AWS re-accelerating with AI. Retail margins expanding. Advertising >$60B run rate."},
                {"ticker": "TSLA", "company": "Tesla", "rev_est_y": 115.0, "rev_growth": "+15%", "eps_est_y": 3.00, "eps_est_ny": 4.00, "capex_guidance": 12.0, "capex_note": "FY26 CapEx $11-13B", "production": None, "price_target": 300, "rating": "Hold", "fwd_pe": 80.0, "outlook": "New affordable model ramp. Energy storage growing 100%+. Robotaxi timeline uncertain."},
                {"ticker": "HD", "company": "Home Depot", "rev_est_y": 160.0, "rev_growth": "+3%", "eps_est_y": 16.00, "eps_est_ny": 17.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 420, "rating": "Buy", "fwd_pe": 25.0, "outlook": "Pro customer segment growing. SRS Distribution adding $6.4B revenue."},
                {"ticker": "MCD", "company": "McDonald's", "rev_est_y": 27.0, "rev_growth": "+4%", "eps_est_y": 12.50, "eps_est_ny": 13.50, "capex_guidance": 3.0, "capex_note": "FY26 CapEx ~$3B", "production": None, "price_target": 330, "rating": "Buy", "fwd_pe": 25.0, "outlook": "Same-store sales recovering. Digital orders >40%. Loyalty 175M+ members."},
                {"ticker": "NKE", "company": "Nike", "rev_est_y": 48.0, "rev_growth": "-2%", "eps_est_y": 2.80, "eps_est_ny": 3.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 85, "rating": "Hold", "fwd_pe": 30.0, "outlook": "New CEO turnaround. DTC rebalancing with wholesale. Innovation pipeline."},
                {"ticker": "LOW", "company": "Lowe's", "rev_est_y": 84.0, "rev_growth": "+2%", "eps_est_y": 12.50, "eps_est_ny": 13.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 280, "rating": "Hold", "fwd_pe": 20.0, "outlook": "Total Home strategy targeting Pro growth. Operating margin expansion to 13%+."},
                {"ticker": "BKNG", "company": "Booking Holdings", "rev_est_y": 25.0, "rev_growth": "+10%", "eps_est_y": 195.00, "eps_est_ny": 220.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 5500, "rating": "Buy", "fwd_pe": 25.0, "outlook": "Connected trip strategy. AI trip planner. Room nights growing 8%+."},
                {"ticker": "SBUX", "company": "Starbucks", "rev_est_y": 37.0, "rev_growth": "+3%", "eps_est_y": 3.50, "eps_est_ny": 4.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 110, "rating": "Hold", "fwd_pe": 28.0, "outlook": "New CEO 'Back to Starbucks' strategy. Menu simplification. China partnership shift."},
                {"ticker": "TJX", "company": "TJX Companies", "rev_est_y": 58.0, "rev_growth": "+5%", "eps_est_y": 4.40, "eps_est_ny": 4.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 135, "rating": "Buy", "fwd_pe": 27.0, "outlook": "Off-price gaining share. 1,300+ new store opportunity."},
                {"ticker": "CMG", "company": "Chipotle", "rev_est_y": 12.0, "rev_growth": "+13%", "eps_est_y": 1.40, "eps_est_ny": 1.60, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 65, "rating": "Buy", "fwd_pe": 45.0, "outlook": "New unit growth 8-10%. Chipotlane 80%+ of new builds. Restaurant-level margins 27%+."},
            ],
        },
    },
    "XLP": {
        "etf": "XLP",
        "label": "Consumer Staples (XLP)",
        "title": "Consumer Staples Sector Analysis",
        "subtitle": "Top 10 US consumer staples companies — food, beverage, household, and tobacco.",
        "companies": {
            "PG": "Procter & Gamble", "COST": "Costco", "WMT": "Walmart",
            "KO": "Coca-Cola", "PEP": "PepsiCo", "PM": "Philip Morris",
            "MDLZ": "Mondelez", "MO": "Altria Group", "CL": "Colgate-Palmolive",
            "STZ": "Constellation Brands",
        },
        "subsectors": {
            "Household & Personal": ["PG", "CL"],
            "Retail": ["COST", "WMT"],
            "Beverage": ["KO", "PEP", "STZ"],
            "Food & Tobacco": ["PM", "MDLZ", "MO"],
        },
        "macro_overlay": {"fred_series": "CPIUFDSL", "label": "CPI Food & Beverages (Index)"},
        "factor_proxies": ["SPY", "XLP", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "PG", "company": "Procter & Gamble", "rev_est_y": 86.0, "rev_growth": "+3%", "eps_est_y": 6.90, "eps_est_ny": 7.30, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 185, "rating": "Buy", "fwd_pe": 26.0, "outlook": "Organic growth 4-5%. Pricing power intact. Productivity savings $1.5B+."},
                {"ticker": "COST", "company": "Costco", "rev_est_y": 270.0, "rev_growth": "+7%", "eps_est_y": 17.50, "eps_est_ny": 19.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 1050, "rating": "Hold", "fwd_pe": 52.0, "outlook": "Membership renewal 93%+. E-commerce growing 20%+. Fee increase in effect."},
                {"ticker": "WMT", "company": "Walmart", "rev_est_y": 680.0, "rev_growth": "+5%", "eps_est_y": 2.70, "eps_est_ny": 3.00, "capex_guidance": 23.0, "capex_note": "FY27 CapEx ~$23B", "production": None, "price_target": 105, "rating": "Buy", "fwd_pe": 36.0, "outlook": "Walmart+ growing. Advertising $4B+. Grocery market share gains."},
                {"ticker": "KO", "company": "Coca-Cola", "rev_est_y": 48.0, "rev_growth": "+5%", "eps_est_y": 2.95, "eps_est_ny": 3.15, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 75, "rating": "Buy", "fwd_pe": 24.0, "outlook": "Organic revenue growth 8-10%. Zero-sugar variants driving mix improvement."},
                {"ticker": "PEP", "company": "PepsiCo", "rev_est_y": 94.0, "rev_growth": "+3%", "eps_est_y": 8.30, "eps_est_ny": 8.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 180, "rating": "Hold", "fwd_pe": 20.0, "outlook": "Frito-Lay volumes recovering. Productivity plan $1B+ savings."},
                {"ticker": "PM", "company": "Philip Morris", "rev_est_y": 40.0, "rev_growth": "+8%", "eps_est_y": 7.50, "eps_est_ny": 8.20, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 155, "rating": "Buy", "fwd_pe": 19.0, "outlook": "IQOS growing 15%+. ZYN demand exceeding supply. Smoke-free >40% of revenue."},
                {"ticker": "MDLZ", "company": "Mondelez", "rev_est_y": 37.0, "rev_growth": "+4%", "eps_est_y": 3.80, "eps_est_ny": 4.10, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 80, "rating": "Hold", "fwd_pe": 20.0, "outlook": "Chocolate pricing passing through cocoa inflation. Emerging markets 40%+ of revenue."},
                {"ticker": "MO", "company": "Altria Group", "rev_est_y": 21.0, "rev_growth": "+1%", "eps_est_y": 5.30, "eps_est_ny": 5.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 58, "rating": "Hold", "fwd_pe": 10.0, "outlook": "Marlboro share stable at 42%. NJOY gaining traction. Dividend yield ~7%."},
                {"ticker": "CL", "company": "Colgate-Palmolive", "rev_est_y": 20.0, "rev_growth": "+3%", "eps_est_y": 3.70, "eps_est_ny": 4.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 105, "rating": "Hold", "fwd_pe": 27.0, "outlook": "Oral care global #1. Hill's Pet Nutrition premium growth. Emerging markets 50%+."},
                {"ticker": "STZ", "company": "Constellation Brands", "rev_est_y": 10.5, "rev_growth": "+4%", "eps_est_y": 14.50, "eps_est_ny": 15.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 270, "rating": "Buy", "fwd_pe": 16.0, "outlook": "Modelo Especial #1 US beer. Beer growing mid-single digits."},
            ],
        },
    },
    "XLU": {
        "etf": "XLU",
        "label": "Utilities (XLU)",
        "title": "Utilities Sector Analysis",
        "subtitle": "Top 10 US utility companies — electric, gas, nuclear, and renewable energy.",
        "companies": {
            "NEE": "NextEra Energy", "SO": "Southern Company", "DUK": "Duke Energy",
            "CEG": "Constellation Energy", "SRE": "Sempra", "AEP": "American Electric Power",
            "D": "Dominion Energy", "EXC": "Exelon", "XEL": "Xcel Energy", "PEG": "PSEG",
        },
        "subsectors": {
            "Diversified Electric": ["NEE", "SO", "DUK", "AEP", "D", "XEL"],
            "Nuclear": ["CEG", "PEG"],
            "Regulated": ["EXC", "SRE"],
        },
        "macro_overlay": {"fred_series": "DGS10", "label": "10Y Treasury Yield (%)"},
        "factor_proxies": ["SPY", "XLU", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "NEE", "company": "NextEra Energy", "rev_est_y": 30.0, "rev_growth": "+8%", "eps_est_y": 3.70, "eps_est_ny": 4.10, "capex_guidance": 25.0, "capex_note": "FY26 CapEx ~$25B", "production": None, "price_target": 85, "rating": "Buy", "fwd_pe": 23.0, "outlook": "Largest US renewable generator. FPL rate base growing 9%+. Data center PPA demand accelerating."},
                {"ticker": "SO", "company": "Southern Company", "rev_est_y": 26.0, "rev_growth": "+4%", "eps_est_y": 4.20, "eps_est_ny": 4.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 95, "rating": "Hold", "fwd_pe": 21.0, "outlook": "Vogtle Units 3&4 operational. Southeast load growth strong. EPS growth 5-7%."},
                {"ticker": "DUK", "company": "Duke Energy", "rev_est_y": 30.0, "rev_growth": "+3%", "eps_est_y": 6.20, "eps_est_ny": 6.60, "capex_guidance": 46.0, "capex_note": "5-year CapEx $73B", "production": None, "price_target": 125, "rating": "Hold", "fwd_pe": 19.0, "outlook": "Rate base growth 7-8%. Renewable transition underway. 5-7% EPS growth target."},
                {"ticker": "CEG", "company": "Constellation Energy", "rev_est_y": 25.0, "rev_growth": "+15%", "eps_est_y": 9.50, "eps_est_ny": 11.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 280, "rating": "Buy", "fwd_pe": 25.0, "outlook": "Largest US nuclear fleet. Data center PPAs at premium. TMI restart approved. Calpine merger pending."},
                {"ticker": "SRE", "company": "Sempra", "rev_est_y": 15.0, "rev_growth": "+5%", "eps_est_y": 5.00, "eps_est_ny": 5.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 90, "rating": "Hold", "fwd_pe": 16.0, "outlook": "SDG&E and SoCalGas growing. Oncor benefiting from TX load growth. LNG export development."},
                {"ticker": "AEP", "company": "American Electric Power", "rev_est_y": 20.0, "rev_growth": "+3%", "eps_est_y": 5.80, "eps_est_ny": 6.20, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 105, "rating": "Hold", "fwd_pe": 17.0, "outlook": "Data center load requests >30 GW. Transmission build-out accelerating. Rate base growth 7%+."},
                {"ticker": "D", "company": "Dominion Energy", "rev_est_y": 15.0, "rev_growth": "+2%", "eps_est_y": 3.30, "eps_est_ny": 3.60, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 60, "rating": "Hold", "fwd_pe": 16.0, "outlook": "Virginia utility rate base growing. Coastal Virginia Offshore Wind underway. 5-7% EPS growth."},
                {"ticker": "EXC", "company": "Exelon", "rev_est_y": 23.0, "rev_growth": "+3%", "eps_est_y": 2.60, "eps_est_ny": 2.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 48, "rating": "Hold", "fwd_pe": 17.0, "outlook": "Pure-play regulated utility. Grid modernization investments. EPS growth 5-7%."},
                {"ticker": "XEL", "company": "Xcel Energy", "rev_est_y": 15.0, "rev_growth": "+4%", "eps_est_y": 3.60, "eps_est_ny": 3.85, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 72, "rating": "Hold", "fwd_pe": 19.0, "outlook": "Leading renewable transition. Data center demand in MN and CO. Rate base growth 6-7%."},
                {"ticker": "PEG", "company": "PSEG", "rev_est_y": 11.0, "rev_growth": "+5%", "eps_est_y": 3.80, "eps_est_ny": 4.10, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 90, "rating": "Hold", "fwd_pe": 22.0, "outlook": "Nuclear fleet benefiting from data center PPAs. PSE&G growing. Offshore wind transmission."},
            ],
        },
    },
    "XLB": {
        "etf": "XLB",
        "label": "Materials (XLB)",
        "title": "Materials Sector Analysis",
        "subtitle": "Top 10 US materials companies — chemicals, mining, metals, and construction materials.",
        "companies": {
            "LIN": "Linde", "SHW": "Sherwin-Williams", "APD": "Air Products",
            "ECL": "Ecolab", "FCX": "Freeport-McMoRan", "NUE": "Nucor",
            "NEM": "Newmont", "VMC": "Vulcan Materials", "MLM": "Martin Marietta",
            "DOW": "Dow Inc",
        },
        "subsectors": {
            "Industrial Gases": ["LIN", "APD"],
            "Specialty Chemicals": ["SHW", "ECL", "DOW"],
            "Mining & Metals": ["FCX", "NUE", "NEM"],
            "Construction Materials": ["VMC", "MLM"],
        },
        "macro_overlay": {"fred_series": "PPIACO", "label": "PPI All Commodities (Index)"},
        "factor_proxies": ["SPY", "XLB", "GLD", "UUP"],
        "cot_commodities": [["Gold", "gold"]],
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "LIN", "company": "Linde", "rev_est_y": 34.0, "rev_growth": "+4%", "eps_est_y": 16.00, "eps_est_ny": 17.50, "capex_guidance": 4.0, "capex_note": "FY26 CapEx ~$4B", "production": None, "price_target": 500, "rating": "Buy", "fwd_pe": 30.0, "outlook": "Industrial gas volumes recovering. Clean hydrogen pipeline $7B+. Operating margin 27%+."},
                {"ticker": "SHW", "company": "Sherwin-Williams", "rev_est_y": 24.0, "rev_growth": "+3%", "eps_est_y": 11.50, "eps_est_ny": 13.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 400, "rating": "Buy", "fwd_pe": 32.0, "outlook": "Pro painter segment resilient. New store openings 80-100/year. Raw material costs easing."},
                {"ticker": "APD", "company": "Air Products", "rev_est_y": 12.0, "rev_growth": "+3%", "eps_est_y": 12.50, "eps_est_ny": 13.50, "capex_guidance": 5.0, "capex_note": "FY26 CapEx ~$5B", "production": None, "price_target": 330, "rating": "Hold", "fwd_pe": 24.0, "outlook": "Blue/green hydrogen mega-projects. CEO transition. Capital allocation pivot to core gases."},
                {"ticker": "ECL", "company": "Ecolab", "rev_est_y": 16.0, "rev_growth": "+6%", "eps_est_y": 7.50, "eps_est_ny": 8.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 280, "rating": "Buy", "fwd_pe": 35.0, "outlook": "Water, hygiene, infection prevention. Data center water treatment opportunity."},
                {"ticker": "FCX", "company": "Freeport-McMoRan", "rev_est_y": 26.0, "rev_growth": "+12%", "eps_est_y": 2.50, "eps_est_ny": 3.00, "capex_guidance": 4.0, "capex_note": "FY26 CapEx ~$4B", "production": "4.3B lbs Cu", "price_target": 55, "rating": "Buy", "fwd_pe": 18.0, "outlook": "Copper demand from EVs, data centers, grid. Grasberg ramp-up. Indonesia smelter operational."},
                {"ticker": "NUE", "company": "Nucor", "rev_est_y": 32.0, "rev_growth": "+5%", "eps_est_y": 10.00, "eps_est_ny": 12.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 180, "rating": "Hold", "fwd_pe": 14.0, "outlook": "Lowest-cost US steel producer. Infrastructure spending driving demand."},
                {"ticker": "NEM", "company": "Newmont", "rev_est_y": 20.0, "rev_growth": "+15%", "eps_est_y": 4.00, "eps_est_ny": 4.50, "capex_guidance": 3.0, "capex_note": "FY26 CapEx ~$3B", "production": "6M oz Au", "price_target": 55, "rating": "Buy", "fwd_pe": 14.0, "outlook": "World's largest gold miner. Record gold prices. Newcrest synergies. AISC $1,400/oz."},
                {"ticker": "VMC", "company": "Vulcan Materials", "rev_est_y": 8.5, "rev_growth": "+6%", "eps_est_y": 10.50, "eps_est_ny": 12.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 295, "rating": "Buy", "fwd_pe": 28.0, "outlook": "Aggregates pricing power. Infrastructure bill driving demand. Cash GP per ton expanding."},
                {"ticker": "MLM", "company": "Martin Marietta", "rev_est_y": 7.0, "rev_growth": "+5%", "eps_est_y": 22.00, "eps_est_ny": 25.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 630, "rating": "Buy", "fwd_pe": 27.0, "outlook": "Aggregates volume growth from infrastructure. Pricing discipline. Bolt-on M&A."},
                {"ticker": "DOW", "company": "Dow Inc", "rev_est_y": 44.0, "rev_growth": "+3%", "eps_est_y": 2.50, "eps_est_ny": 3.20, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 42, "rating": "Hold", "fwd_pe": 16.0, "outlook": "Plastics prices normalizing. Path2Zero progressing. Dividend yield ~5%. Cost reduction $1B+."},
            ],
        },
    },
    "XLRE": {
        "etf": "XLRE",
        "label": "Real Estate (XLRE)",
        "title": "Real Estate Sector Analysis",
        "subtitle": "Top 10 US REITs — industrial, data centers, towers, retail, storage, and healthcare.",
        "companies": {
            "PLD": "Prologis", "AMT": "American Tower", "EQIX": "Equinix",
            "SPG": "Simon Property", "PSA": "Public Storage", "O": "Realty Income",
            "WELL": "Welltower", "DLR": "Digital Realty", "VICI": "VICI Properties",
            "CCI": "Crown Castle",
        },
        "subsectors": {
            "Data Centers & Towers": ["EQIX", "DLR", "AMT", "CCI"],
            "Industrial & Logistics": ["PLD"],
            "Retail & Net Lease": ["SPG", "O", "VICI"],
            "Storage & Healthcare": ["PSA", "WELL"],
        },
        "macro_overlay": {"fred_series": "DGS10", "label": "10Y Treasury Yield (%)"},
        "factor_proxies": ["SPY", "XLRE", "TLT", "UUP"],
        "cot_commodities": None,
        "guidance_snapshot": {
            "date": "2026-03-25",
            "data": [
                {"ticker": "PLD", "company": "Prologis", "rev_est_y": 8.5, "rev_growth": "+8%", "eps_est_y": 3.50, "eps_est_ny": 3.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 130, "rating": "Buy", "fwd_pe": 35.0, "outlook": "1.2B sqft logistics portfolio. Data center pipeline $7B+. Embedded rent mark-to-market 30%+."},
                {"ticker": "AMT", "company": "American Tower", "rev_est_y": 11.0, "rev_growth": "+5%", "eps_est_y": 5.00, "eps_est_ny": 5.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 230, "rating": "Hold", "fwd_pe": 40.0, "outlook": "195K+ tower sites. 5G densification driving co-location. CoreSite data center growing."},
                {"ticker": "EQIX", "company": "Equinix", "rev_est_y": 9.0, "rev_growth": "+10%", "eps_est_y": 12.00, "eps_est_ny": 13.50, "capex_guidance": 3.5, "capex_note": "FY26 CapEx ~$3.5B", "production": None, "price_target": 950, "rating": "Buy", "fwd_pe": 72.0, "outlook": "260+ data centers. AI/GPU cluster demand surging. Record leasing $500M+/quarter."},
                {"ticker": "SPG", "company": "Simon Property", "rev_est_y": 6.0, "rev_growth": "+3%", "eps_est_y": 8.50, "eps_est_ny": 9.00, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 180, "rating": "Hold", "fwd_pe": 19.0, "outlook": "Premium mall occupancy 96%+. Tenant sales at records. Mixed-use redevelopment."},
                {"ticker": "PSA", "company": "Public Storage", "rev_est_y": 4.5, "rev_growth": "+2%", "eps_est_y": 11.00, "eps_est_ny": 11.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 340, "rating": "Hold", "fwd_pe": 27.0, "outlook": "3,300+ facilities. New development $2.5B. Industry supply moderating."},
                {"ticker": "O", "company": "Realty Income", "rev_est_y": 5.0, "rev_growth": "+6%", "eps_est_y": 4.20, "eps_est_ny": 4.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 65, "rating": "Hold", "fwd_pe": 15.0, "outlook": "15,400+ net lease properties. Monthly dividend REIT. European expansion. Occupancy 98%+."},
                {"ticker": "WELL", "company": "Welltower", "rev_est_y": 8.0, "rev_growth": "+18%", "eps_est_y": 4.50, "eps_est_ny": 5.20, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 155, "rating": "Buy", "fwd_pe": 32.0, "outlook": "Senior housing occupancy recovery. Aging demographics tailwind. Same-store NOI growth 10%+."},
                {"ticker": "DLR", "company": "Digital Realty", "rev_est_y": 6.0, "rev_growth": "+12%", "eps_est_y": 7.00, "eps_est_ny": 7.80, "capex_guidance": 3.0, "capex_note": "FY26 CapEx ~$3B", "production": None, "price_target": 185, "rating": "Buy", "fwd_pe": 25.0, "outlook": "300+ data centers. AI-ready power demand surging. Record leasing. JV with Blackstone, GIC."},
                {"ticker": "VICI", "company": "VICI Properties", "rev_est_y": 4.0, "rev_growth": "+5%", "eps_est_y": 2.30, "eps_est_ny": 2.50, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 35, "rating": "Hold", "fwd_pe": 14.0, "outlook": "54 gaming properties. Triple-net leases with CPI escalators. 100% rent collection."},
                {"ticker": "CCI", "company": "Crown Castle", "rev_est_y": 6.5, "rev_growth": "-2%", "eps_est_y": 3.50, "eps_est_ny": 3.80, "capex_guidance": None, "capex_note": None, "production": None, "price_target": 110, "rating": "Hold", "fwd_pe": 30.0, "outlook": "40K+ US tower sites. Fiber/small cell under strategic review. Tower business stable."},
            ],
        },
    },
}


# ═══════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════

def _clean(v):
    """Coerce numpy/pandas scalars and NaN/Inf/NaT to JSON-safe Python values."""
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (np.floating, float)):
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (pd.Timestamp,)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, np.ndarray):
        return [_clean(x) for x in v.tolist()]
    return v


def _records(df: pd.DataFrame) -> list[dict]:
    """Convert DataFrame to list of clean records (NaN → None, Timestamps → strings)."""
    if df is None or df.empty:
        return []
    rows = df.to_dict(orient="records")
    return [{k: _clean(v) for k, v in r.items()} for r in rows]


def _get_config(etf: str) -> dict:
    cfg = SECTOR_CONFIGS.get(etf.upper())
    if not cfg:
        raise HTTPException(404, f"Unknown sector ETF: {etf}. Valid: {list(SECTOR_CONFIGS.keys())}")
    return cfg


class EtfBody(BaseModel):
    etf: str


class TickersBody(BaseModel):
    tickers: list[str]


# ═══════════════════════════════════════════════
# GET /api/sectors/configs
# ═══════════════════════════════════════════════

@router.get("/configs")
async def get_configs(user: str = Depends(get_current_user)):
    """Return all 11 SPDR sector configs (companies, subsectors, guidance snapshot, etc.)."""
    return {"sectors": SECTOR_CONFIGS}


# ═══════════════════════════════════════════════
# POST /api/sectors/overview
# ═══════════════════════════════════════════════

@_result_cached("sector_overview")
def _compute_sector_overview(etf: str) -> dict:
    """Supabase-cached (12h) compute for the Overview tab. Fundamentals update
    quarterly so this is conservative."""
    from src.edgar import (
        fetch_sector_financials,
        fetch_sector_analyst_estimates,
        fetch_sector_revenue_history,
        fetch_sector_margin_history,
        fetch_sector_cashflow,
    )
    cfg = _get_config(etf)
    companies = cfg["companies"]
    tickers = list(companies.keys())
    with ThreadPoolExecutor(max_workers=5) as pool:
        futs = {
            "financials": pool.submit(fetch_sector_financials, companies),
            "forecasts": pool.submit(fetch_sector_analyst_estimates, companies),
            "revenue_history": pool.submit(fetch_sector_revenue_history, companies),
            "margin_history": pool.submit(fetch_sector_margin_history, companies),
            "cashflow": pool.submit(fetch_sector_cashflow, tickers),
        }
        results = {k: f.result() for k, f in futs.items()}
    return {
        "etf": cfg["etf"],
        "financials": _records(results["financials"]),
        "forecasts": _records(results["forecasts"]),
        "revenue_history": _records(results["revenue_history"]),
        "margin_history": _records(results["margin_history"]),
        "cashflow": _records(results["cashflow"]),
    }


@router.post("/overview")
async def sector_overview(body: EtfBody, user: str = Depends(get_current_user)):
    """Financials + revenue history + analyst estimates + margin history + cashflow — Tab 1."""
    return _compute_sector_overview(body.etf)


# ═══════════════════════════════════════════════
# POST /api/sectors/capex
# ═══════════════════════════════════════════════

def _quarterize_capex(capex_hist: pd.DataFrame) -> list[dict]:
    """XBRL CapEx is cumulative YTD in 10-Qs; 10-K reports full-year total.
    Convert to quarterly:
    - 10-Q: diff consecutive values within the same fiscal year
    - 10-K: Q4 = 10-K value − last 10-Q cumulative (Q3)
    - 10-K only (no 10-Q): full-year value, labeled 10-K (annual)
    """
    if capex_hist is None or capex_hist.empty:
        return []

    cx = capex_hist.copy()
    cx["date"] = pd.to_datetime(cx["date"])
    cx = cx[cx["date"] >= "2024-01-01"].sort_values(["ticker", "date"])
    cx["year"] = cx["date"].dt.year
    cx["quarter"] = (cx["date"].dt.month - 1) // 3 + 1

    out = []
    for ticker in cx["ticker"].unique():
        sub = cx[cx["ticker"] == ticker].sort_values("date")
        for year in sub["year"].unique():
            yr_data = sub[sub["year"] == year].sort_values("date")
            tenq = yr_data[yr_data["form"] == "10-Q"]
            tenk = yr_data[yr_data["form"] == "10-K"]

            if len(tenq) > 0:
                prev_cum = 0
                for _, row in tenq.iterrows():
                    q_val = row["capex"] - prev_cum
                    prev_cum = row["capex"]
                    out.append({
                        "ticker": row["ticker"],
                        "company": row["company"],
                        "date": row["date"].strftime("%Y-%m-%d"),
                        "q_capex": float(q_val),
                        "form": row["form"],
                        "year": int(year),
                        "quarter": int((row["date"].month - 1) // 3 + 1),
                    })
                if len(tenk) > 0:
                    annual = tenk.iloc[-1]["capex"]
                    last_q_cum = tenq.iloc[-1]["capex"]
                    q4_val = annual - last_q_cum
                    if q4_val > 0:
                        out.append({
                            "ticker": ticker,
                            "company": tenk.iloc[-1]["company"],
                            "date": tenk.iloc[-1]["date"].strftime("%Y-%m-%d"),
                            "q_capex": float(q4_val),
                            "form": "10-K",
                            "year": int(year),
                            "quarter": 4,
                        })
            elif len(tenk) > 0:
                out.append({
                    "ticker": ticker,
                    "company": tenk.iloc[-1]["company"],
                    "date": tenk.iloc[-1]["date"].strftime("%Y-%m-%d"),
                    "q_capex": float(tenk.iloc[-1]["capex"]),
                    "form": "10-K (annual)",
                    "year": int(year),
                    "quarter": 4,
                })
    return out


@_result_cached("sector_capex")
def _compute_sector_capex(etf: str) -> dict:
    from src.edgar import fetch_sector_capex, fetch_sector_capex_history
    cfg = _get_config(etf)
    companies = cfg["companies"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_latest = pool.submit(fetch_sector_capex, companies)
        f_hist = pool.submit(fetch_sector_capex_history, companies)
        latest = f_latest.result()
        hist = f_hist.result()
    return {
        "etf": cfg["etf"],
        "capex_latest": _records(latest),
        "capex_quarterly": _quarterize_capex(hist),
    }


@router.post("/capex")
async def sector_capex(body: EtfBody, user: str = Depends(get_current_user)):
    """Latest CapEx + quarterly CapEx history — Tab 2."""
    return _compute_sector_capex(body.etf)


# ═══════════════════════════════════════════════
# POST /api/sectors/valuation
# ═══════════════════════════════════════════════

@_result_cached("sector_valuation")
def _compute_sector_valuation(etf: str) -> dict:
    from src.market_data import fetch_energy_valuation_data, fetch_momentum_data
    cfg = _get_config(etf)
    tickers = list(cfg["companies"].keys())
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_val = pool.submit(fetch_energy_valuation_data, tickers)
        f_mom = pool.submit(fetch_momentum_data, tickers)
        val = f_val.result()
        mom = f_mom.result()
    return {
        "etf": cfg["etf"],
        "valuation": _records(val),
        "momentum": _records(mom),
    }


@router.post("/valuation")
async def sector_valuation(body: EtfBody, user: str = Depends(get_current_user)):
    """Valuation ratios + momentum — Tabs 3 & 4."""
    return _compute_sector_valuation(body.etf)


# ═══════════════════════════════════════════════
# POST /api/sectors/alpha
# ═══════════════════════════════════════════════

@_result_cached("sector_alpha")
def _compute_sector_alpha(etf: str) -> dict:
    from src.market_data import fetch_eps_revisions, fetch_insider_summary
    cfg = _get_config(etf)
    tickers = list(cfg["companies"].keys())
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_rev = pool.submit(fetch_eps_revisions, tickers)
        f_ins = pool.submit(fetch_insider_summary, tickers)
        rev = f_rev.result()
        ins = f_ins.result()
    return {
        "etf": cfg["etf"],
        "eps_revisions": _records(rev),
        "insider": _records(ins),
    }


@router.post("/alpha")
async def sector_alpha(body: EtfBody, user: str = Depends(get_current_user)):
    """EPS revisions + insider activity — Tab 4 (combined with valuation)."""
    return _compute_sector_alpha(body.etf)


# ═══════════════════════════════════════════════
# POST /api/sectors/prices — shared by Risk + Pairs tabs
# ═══════════════════════════════════════════════

def _price_history_batch(tickers: list[str], period: str = "2y") -> dict[str, list[dict]]:
    """Fetch daily close history in parallel. Thread-safe per yfinance feedback memory."""
    import yfinance as yf

    def _one(tk: str):
        try:
            hist = yf.Ticker(tk).history(period=period)
            if hist.empty:
                return tk, []
            return tk, [
                {"date": idx.strftime("%Y-%m-%d"), "close": float(row["Close"])}
                for idx, row in hist.iterrows()
                if pd.notna(row["Close"])
            ]
        except Exception as e:
            logger.warning(f"price fetch failed for {tk}: {e}")
            return tk, []

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = dict(pool.map(_one, tickers))
    return results


@_result_cached("sector_prices")
def _compute_sector_prices(etf: str) -> dict:
    cfg = _get_config(etf)
    tickers = list(cfg["companies"].keys())
    extra = ["SPY"] + cfg["factor_proxies"]
    all_tickers = list(dict.fromkeys(tickers + extra))
    prices = _price_history_batch(all_tickers, "2y")
    return {
        "etf": cfg["etf"],
        "prices": prices,
    }


@router.post("/prices")
async def sector_prices(body: EtfBody, user: str = Depends(get_current_user)):
    """Price history for sector + SPY + factor proxies — Tabs 5 & 8."""
    return _compute_sector_prices(body.etf)


# ═══════════════════════════════════════════════
# POST /api/sectors/guidance
# ═══════════════════════════════════════════════

def _live_estimates_batch(tickers: list[str]) -> dict[str, dict]:
    """Fetch live analyst estimates from yfinance in parallel."""
    import yfinance as yf

    def _one(tk: str):
        try:
            info = yf.Ticker(tk).info or {}
            return tk, {
                "price_target": info.get("targetMeanPrice"),
                "target_low": info.get("targetLowPrice"),
                "target_high": info.get("targetHighPrice"),
                "fwd_pe": info.get("forwardPE"),
                "trailing_pe": info.get("trailingPE"),
                "rating": (info.get("recommendationKey") or "").replace("_", " ").title() or None,
                "n_analysts": info.get("numberOfAnalystOpinions"),
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
                "fwd_eps": info.get("forwardEps"),
                "trailing_eps": info.get("trailingEps"),
                "rev_growth": info.get("revenueGrowth"),
                "earnings_growth": info.get("earningsGrowth"),
            }
        except Exception as e:
            logger.warning(f"live estimates failed for {tk}: {e}")
            return tk, {}

    with ThreadPoolExecutor(max_workers=10) as pool:
        pairs = list(pool.map(_one, tickers))
    return {tk: {k: _clean(v) for k, v in d.items()} for tk, d in pairs}


@_result_cached("sector_guidance")
def _compute_sector_guidance(etf: str) -> dict:
    from src.market_data import fetch_energy_earnings_surprises
    cfg = _get_config(etf)
    tickers = list(cfg["companies"].keys())
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_live = pool.submit(_live_estimates_batch, tickers)
        f_surp = pool.submit(fetch_energy_earnings_surprises, tickers)
        live = f_live.result()
        surp = f_surp.result()
    return {
        "etf": cfg["etf"],
        "live_estimates": live,
        "earnings_surprises": _records(surp),
    }


@router.post("/guidance")
async def sector_guidance(body: EtfBody, user: str = Depends(get_current_user)):
    """Live analyst estimates + earnings surprise history — Tab 6."""
    return _compute_sector_guidance(body.etf)


# ═══════════════════════════════════════════════
# POST /api/sectors/market
# ═══════════════════════════════════════════════

@_result_cached("sector_market")
def _compute_sector_market(etf: str) -> dict:
    from src.market_data import fetch_fred_series, fetch_cftc_cot
    cfg = _get_config(etf)
    overlay = cfg["macro_overlay"]
    cot_commodities = cfg.get("cot_commodities") or []

    macro = fetch_fred_series(overlay["fred_series"], periods=504)
    macro_records = []
    if not macro.empty:
        macro_records = [
            {"date": r["date"].strftime("%Y-%m-%d"), "value": _clean(r["value"])}
            for _, r in macro.iterrows()
        ]

    # Secondary FRED price series for COT overlay
    fred_price_map = {
        "crude_oil": "DCOILWTICO",
        "natural_gas": "DHHNGSP",
        "gold": "GOLDAMGBD228NLBM",
    }

    cot_data = []
    for display_name, cot_key in cot_commodities:
        cot = fetch_cftc_cot(cot_key, periods=52)
        price_series_id = fred_price_map.get(cot_key)
        price_hist = fetch_fred_series(price_series_id, periods=365) if price_series_id else pd.DataFrame()
        if cot.empty:
            cot_data.append({
                "name": display_name,
                "key": cot_key,
                "rows": [],
                "price_history": [],
            })
            continue
        cot_df = cot.copy()
        cot_df["date"] = pd.to_datetime(cot_df["date"])
        cot_rows = [
            {
                "date": r["date"].strftime("%Y-%m-%d"),
                "spec_long": _clean(r.get("spec_long")),
                "spec_short": _clean(r.get("spec_short")),
                "spec_net": _clean(r.get("spec_net")),
                "comm_long": _clean(r.get("comm_long")),
                "comm_short": _clean(r.get("comm_short")),
                "comm_net": _clean(r.get("comm_net")),
            }
            for _, r in cot_df.iterrows()
        ]
        price_rows = []
        if not price_hist.empty:
            price_rows = [
                {"date": r["date"].strftime("%Y-%m-%d"), "value": _clean(r["value"])}
                for _, r in price_hist.iterrows()
            ]
        cot_data.append({
            "name": display_name,
            "key": cot_key,
            "rows": cot_rows,
            "price_history": price_rows,
        })

    return {
        "etf": cfg["etf"],
        "macro_label": overlay["label"],
        "macro_series_id": overlay["fred_series"],
        "macro_series": macro_records,
        "cot": cot_data,
    }


@router.post("/market")
async def sector_market(body: EtfBody, user: str = Depends(get_current_user)):
    """Macro overlay (FRED) + CFTC COT positioning (if applicable) — Tab 7."""
    return _compute_sector_market(body.etf)
