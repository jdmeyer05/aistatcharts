# Sector Analysis Expansion Plan

## Goal
Replicate the Energy Sector page (24_Energy_Sector.py) for each of the 11 SPDR sector ETFs, creating a comprehensive institutional-grade sector analysis suite.

## Target Sectors

| # | ETF | Sector | Page File | Top 10 Source |
|---|-----|--------|-----------|---------------|
| 1 | XLE | Energy | 24_Energy_Sector.py | **DONE** |
| 2 | XLF | Financials | 25_Financials_Sector.py | JPM, BAC, WFC, GS, MS, BLK, SCHW, C, AXP, MMC |
| 3 | XLK | Technology | 26_Tech_Sector.py | AAPL, MSFT, NVDA, AVGO, CRM, ORCL, AMD, ADBE, ACN, CSCO |
| 4 | XLV | Healthcare | 27_Healthcare_Sector.py | UNH, LLY, JNJ, ABBV, MRK, TMO, ABT, AMGN, DHR, PFE |
| 5 | XLI | Industrials | 28_Industrials_Sector.py | GE, CAT, UNP, HON, RTX, DE, LMT, BA, ETN, ADP |
| 6 | XLC | Communication | 29_Comms_Sector.py | META, GOOGL, NFLX, T, CMCSA, VZ, DIS, TMUS, EA, CHTR |
| 7 | XLY | Consumer Disc | 30_ConsDisc_Sector.py | AMZN, TSLA, HD, MCD, NKE, LOW, BKNG, SBUX, TJX, CMG |
| 8 | XLP | Consumer Staples | 31_ConsStaples_Sector.py | PG, COST, WMT, KO, PEP, PM, MDLZ, MO, CL, STZ |
| 9 | XLU | Utilities | 32_Utilities_Sector.py | NEE, SO, DUK, CEG, SRE, AEP, D, EXC, XEL, PEG |
| 10 | XLB | Materials | 33_Materials_Sector.py | LIN, SHW, APD, ECL, FCX, NUE, NEM, VMC, MLM, DOW |
| 11 | XLRE | Real Estate | 34_RealEstate_Sector.py | PLD, AMT, EQIX, SPG, PSA, O, WELL, DLR, VICI, CCI |

## Architecture per Page

Each sector page follows the Energy template with 8 sub-tabs:

### Tab 1: Overview & Revenue
- Revenue/earnings ranking bars
- Profitability (margin + ROE)
- Quarterly revenue trend (absolute/indexed + analyst projections)
- Revenue growth rates (QoQ + YoY)
- Revenue volatility
- Margin trend over time
- Operating leverage
- Earnings quality (OpCF / NI)
- Composite scorecard

### Tab 2: CapEx Analysis
- Latest CapEx ranking + capital intensity
- CapEx trend (absolute/indexed + guidance projections)
- YoY change + stacked sector total
- Detail table with guidance column

### Tab 3: Valuation & Returns
- Forward P/E + EV/EBITDA
- Dividend yield + FCF yield
- Net Debt/EBITDA + Beta
- Summary table

### Tab 4: Alpha Signals
- Relative value scatter (FCF yield vs P/E, color by sub-sector)
- Momentum heatmap (1M/3M/6M/12M)
- Analyst estimate revisions (30-day net)
- Insider activity (90-day net value)

### Tab 5: Risk Analytics
- Max drawdown (individual + comparison)
- Value at Risk (95%/99% historical)
- Sharpe / Sortino ratios
- Sector vs S&P 500 cumulative performance
- Sub-sector decomposition
- Factor exposure (SPY + sector-specific factors)

### Tab 6: Guidance & Estimates
- Static guidance snapshot table (scraped quarterly)
- Company outlook notes
- Earnings surprise heatmap

### Tab 7: Market & Positioning
- Revenue vs macro overlay (sector-specific: oil for energy, rates for financials, etc)
- CFTC COT or equivalent positioning data (where applicable)

### Tab 8: Pairs & Correlation
- N×N pairs plot matrix
- Pair deep dive (scatter, rolling correlation, spread Z-score, distributions)

## Sector-Specific Customizations

Each sector needs its own metrics and context:

| Sector | Key Metrics | Macro Overlay | Sub-sectors |
|--------|-------------|---------------|-------------|
| **Financials** | NIM, loan growth, provision ratio, CET1, ROTCE | Fed funds rate, yield curve slope | Banks, Asset Mgmt, Insurance, Exchanges |
| **Technology** | R&D/Revenue, rule of 40, ARR growth, gross margin | Nasdaq, 10Y yield (growth discount) | Software, Semis, Hardware, IT Services |
| **Healthcare** | Pipeline value, patent cliff, R&D yield | Drug pricing policy, FDA approvals | Pharma, Biotech, MedTech, Insurance |
| **Industrials** | Backlog, book-to-bill, capacity utilization | ISM PMI, freight indices | Aerospace, Machinery, Transports, Defense |
| **Communication** | ARPU, subscriber growth, ad revenue growth | Digital ad spend, cord-cutting rate | Social/Search, Telecom, Media, Gaming |
| **Consumer Disc** | Same-store sales, e-commerce %, consumer confidence | CPI, consumer sentiment | Retail, Auto, Hotels, Restaurants |
| **Consumer Staples** | Organic growth, pricing power, private label share | CPI food/beverage, commodity costs | Food, Beverage, Household, Tobacco |
| **Utilities** | Rate base growth, allowed ROE, renewable mix | 10Y yield (bond proxy), weather | Electric, Gas, Water, Renewable |
| **Materials** | Commodity price sensitivity, volume growth | Copper/gold/steel prices, China PMI | Chemicals, Mining, Paper, Construction |
| **Real Estate** | FFO/AFFO, occupancy, cap rate, NAV premium | 10Y yield (cap rate driver), WFH | Industrial, Data Center, Retail, Residential |

## Implementation Plan

### Phase 1: Template Extraction (1 session)
- Extract shared code from 24_Energy_Sector.py into `src/sector_analysis.py`
- Generic functions: `build_overview_tab()`, `build_capex_tab()`, `build_valuation_tab()`, etc
- Sector config dict: companies, sub-sectors, macro overlays, guidance snapshots
- One function call per page: `render_sector_page(config)`

### Phase 2: Build Financials + Tech (2 sessions)
- Financials: add NIM, CET1, ROTCE from XBRL; overlay yield curve
- Technology: add R&D/revenue, rule of 40; overlay Nasdaq/10Y
- Scrape guidance for 20 companies

### Phase 3: Build Healthcare + Industrials + Communication (2 sessions)
- Healthcare: pipeline/patent metrics harder — may need supplemental data
- Industrials: backlog from XBRL; overlay ISM PMI from FRED
- Communication: ARPU from XBRL; overlay ad spend trends

### Phase 4: Build Consumer Disc + Staples + Utilities + Materials + Real Estate (2 sessions)
- Most follow the standard template closely
- Real Estate: FFO/AFFO instead of EPS; cap rate analysis
- Utilities: bond proxy analysis with yield overlay

### Phase 5: Cross-Sector Dashboard (1 session)
- New page: sector rotation heatmap
- Relative strength across all 11 sectors
- Money flow between sectors
- Factor tilt comparison (which sectors are value vs growth vs momentum)

## Data Requirements

All sectors use the same data stack:
- **SEC EDGAR XBRL** — financials, margins, CapEx (already built)
- **Yahoo Finance** — valuation, estimates, price history, insider trades (already built)
- **Motley Fool** — earnings call transcripts for guidance (already built)
- **FRED** — macro overlays (already built)
- **CFTC** — only relevant for Energy and Materials (already built)

No new API keys or data sources needed.
