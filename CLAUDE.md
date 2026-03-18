# AI Statcharts — Claude Quick-Start Guide

## Spin Up Local Server

```bash
cd C:\Users\jdmey\aistatcharts
python -m streamlit run app.py
```

Opens at **http://localhost:8501**. Auth is bypassed locally when `LOCAL_DEV = "true"` (see `src/auth.py`).

**Python 3.14 note:** `supabase` has a transitive dep (`pyiceberg`) that fails to build without MSVC. Workaround: install `storage3==2.19.0 --no-deps` then install remaining deps normally. All other packages install fine.

**Production (Docker):**
```bash
docker build -t aistatcharts . && docker run -p 8080:8080 aistatcharts
```

---

## What This App Does

Institutional-grade **quantitative trading & analysis platform** built with Streamlit. Features: advanced charting, options analytics, ML forecasts, backtesting, macro/energy monitoring, AI-powered scenario analysis with live data feeds, and an AI chatbot sidebar.

---

## Project Structure

```
app.py                        → Entry point: Supabase login, routes to Summary
Dockerfile                    → Python 3.11-slim, Cloud Run on port 8080
requirements.txt              → All pip dependencies
.streamlit/config.toml        → Dark theme (cyan primary)
.streamlit/secrets.toml       → API keys (see below)
src/grok_regime_history.json  → Hourly Grok analysis history (auto-generated)
```

### src/ — Core Modules

| File | Purpose |
|------|---------|
| `auth.py` | Supabase client init, session recovery, local dev bypass |
| `chatbot.py` | OpenAI sidebar chatbot (system prompt: quant analyst), 50-msg cap |
| `data_engine.py` | Market data fetcher: Massive API → yfinance fallback, ticker normalization, options chains |
| `eia_helpers.py` | EIA API v2 wrapper for energy timeseries, WoW change calc |
| `simulation.py` | Stochastic Recursive Random Forest: 30-day forward price paths with tree variance injection |
| `grok_regime_history.json` | Persistent store for hourly Grok regime analyses (timestamps, probabilities, sentiment, asset estimates) |

### pages/ — App Pages

| # | File | What It Does |
|---|------|-------------|
| 01 | `Summary.py` | Dashboard: sparklines for QQQ, BTC, Oil, NatGas with 3-month trends |
| 02 | `Scenario_Analysis.py` | **Flagship page** — see detailed breakdown below |
| 03 | `Historical_Analysis.py` | Multi-year price history, seasonal decomposition, volatility, drawdown |
| 04 | `Options_Analysis.py` | IV skew, open interest walls, Greek exposures (equities only) |
| 05 | `Options_Flow.py` | Unusual activity scanner, put/call ratios, GEX (Polygon API) |
| 06 | `Options_Lab.py` | Vol surface across all expirations, earnings analyzer, multi-leg strategy modeler |
| 07 | `ML_Stock_Predictor.py` | 30-day Random Forest forecast with probabilistic projections (uses `simulation.py`) |
| 08 | `Tech_Screener.py` | EMA, RSI, MACD, Bollinger Bands in 2x2 grid layout |
| 09 | `Algo_Backtester.py` | 13 strategies (SMA, MACD, RSI, ATR, etc.), vectorized backtest, Sharpe/drawdown stats |
| 10 | `Monte_Carlo.py` | GBM simulation: configurable paths/days, percentile bands, terminal distribution |
| 11 | `Power_Risk_VaR.py` | Multi-asset portfolio VaR/CVaR at 95%/99%, correlation heatmaps |
| 12 | `Oil_Fundamentals.py` | EIA crude: inventories, production, Cushing, refinery util, 5-year seasonal avg |
| 13 | `NatGas_Fundamentals.py` | EIA storage by region, Henry Hub spot, consumption, 5-year avg |
| 14 | `ERCOT_Power.py` | Real-time TX grid: fuel mix, supply vs demand, load forecast (15-min refresh) |
| 15 | `ERCOT_Capacity.py` | Planned generation additions by fuel type from ERCOT interconnection queue |
| 16 | `Economic_Calendar.py` | FRED releases (CPI, NFP, GDP, FOMC), yield curve (1M–30Y) |
| 17 | `Iran_Conflict.py` | GDELT media intensity, oil price correlation, defense/energy sector impact |
| 18 | `Futures.py` | Multi-asset snapshot (indices, energy, metals, rates, ag, FX), term structure, correlations |

---

## Scenario Analysis Engine (Page 02) — Deep Dive

This is the most complex page. It has 7 tabs and integrates 7 live data sources through a Grok AI analysis pipeline.

### Tab Order
1. **Macro Portfolio Scenarios** — AI-powered regime analysis + portfolio impact
2. **Fed & Macro Drivers** — Live FRED indicators, FOMC dot plot, Polymarket, StockTwits
3. **Historical Stress Tests** — Replay 8 historical crises against your portfolio
4. **Custom What-If** — Slider-based asset shocks with macro presets
5. **Bull / Base / Bear** — GBM forward projections with adjustable assumptions
6. **Event-Driven** — Catalyst modeler (FOMC, earnings, CPI, geopolitical) with probability weighting
7. **Model Diagnostics** — Factor betas, R², residuals, correlations, stress tests

### 7-Layer Grok Analysis Pipeline
Grok 3 (xAI) is called hourly and ingests all 7 layers in a single prompt:

| # | Layer | Source | Update Freq | How |
|---|-------|--------|-------------|-----|
| 1 | Hard economic data | FRED API | Live (1hr cache) | 21 series: CPI, Core PCE, unemployment, NFP, Fed Funds, 2s10s, 10Y, 2Y, retail sales, sentiment, industrial production, GDP, housing starts, dollar, initial claims, Sahm Rule, NFCI, VIX, HY spreads, 5Y breakeven, 10Y breakeven |
| 2 | FOMC dot plots + SEP | Last 3 meetings | Hardcoded (4x/yr) | March 2026, December 2025, September 2025. Individual projections + medians + hawkish/dovish shift analysis |
| 3 | Beige Books | Last 3 releases | Hardcoded (8x/yr) | March 2026, January 2026, November 2025. District-level anecdotal economic conditions |
| 4 | Leading indicators | FRED (GDPNow) + Grok search | Live + search | GDPNow from FRED. ISM PMI, CME FedWatch, LEI via Grok's real-time search |
| 5 | Retail sentiment | StockTwits API | Live (30min cache) | Bull/bear ratios for SPY, QQQ, TLT, USO, GLD, DIA, IWM, VIX. Uses `curl_cffi` to bypass Cloudflare |
| 6 | Prediction markets | Polymarket Gamma API | Live (30min cache) | Recession probability, Fed rate cuts, inflation level, Iran outcomes. No auth needed |
| 7 | X/Twitter sentiment | Grok real-time search | Per hourly call | Grok searches X for Fed commentary, recession fears, inflation expectations, geopolitical developments |

### Hourly Auto-Poll System
- Results saved to `src/grok_regime_history.json` with timestamps
- On page load: checks last result age. If >1hr, calls Grok with full 7-layer prompt
- If <1hr, loads cached result instantly (no API call)
- History enables: probability-over-time chart (line + stacked area), sentiment log, change tracking
- Grok receives its own prior analyses (last 24 entries) to track shifts and avoid manufacturing changes
- "Force Refresh" button visible only to admin (jdmeyer05@gmail.com)

### Two-Layer Portfolio Impact Model
Returns are estimated for each user ticker under each macro regime:

**Layer 1 — Data-Driven Factor Model:**
- 7 factors: VIX, 10Y yield, HY credit spreads, 5Y breakeven inflation, dollar index, crude oil, VIX×HY interaction
- Exponentially-weighted OLS (halflife=60 trading days)
- Block bootstrap from regime-like periods (high-VIX vs low-VIX historical blocks)
- Stressed residual std for downside regimes (recession, crisis, stagflation)
- Student-t (df=5) confidence intervals for fat tails
- Rolling beta stability check (first-half vs second-half correlation)

**Layer 2 — Grok AI Estimates:**
- User's actual ticker list sent to Grok
- Grok estimates per-ticker returns based on sector exposure and historical analogs
- Returns per-regime `asset_estimates` dict

**Blending:**
- R²-adaptive + stability-adjusted weights
- High R² + stable betas → up to 80% data weight
- Low R² + unstable betas → up to 70% AI weight
- Fallback chain: blended → data-only → AI-only → hardcoded defaults

**Portfolio Features:**
- Horizon selector: 3m / 6m / 12m (scales factor moves, CIs, and AI estimates)
- 10,000 Monte Carlo simulations with regime-weighted Student-t draws
- 95% VaR, CVaR (Expected Shortfall), probability of loss
- Sector concentration detection
- Stressed vs normal correlation matrices

### 6 Macro Regimes (probability-calibrated to March 2026)
Current base probabilities reflect: Iran/Hormuz oil shock, Fed hold at 3.50-3.75%, Feb NFP -92K, tariffs at 10.5% effective rate.

| Regime | Base Prob | Key Driver |
|--------|-----------|-----------|
| Stagflation | 30% | Oil >$100 + tariffs keep inflation sticky while growth stalls |
| Recession | 25% | Negative NFP + oil drag + tariff squeeze |
| Soft Landing | 15% | Requires rapid de-escalation + oil normalization |
| Financial Crisis | 10% | Prolonged Hormuz closure → sovereign/credit contagion |
| Re-Acceleration | 10% | Quick war end + SCOTUS strikes tariffs |
| Goldilocks | 10% | Everything breaks right simultaneously |

### Model Diagnostics Tab
- Factor beta heatmap (7 factors × N tickers)
- R² and beta stability bar charts
- Residual distribution histograms with kurtosis/skew
- Actual vs predicted scatter plots
- Stressed vs normal volatility comparison
- Factor correlation matrix (multicollinearity check)
- Normal vs stressed asset correlation heatmaps
- Sector concentration flags

---

## API Keys & Secrets

All in `.streamlit/secrets.toml`:

| Key | Service | Used By |
|-----|---------|---------|
| `SUPABASE_URL` + `SUPABASE_KEY` | Auth & database | `auth.py` |
| `OPENAI_API_KEY` | Sidebar chatbot | `chatbot.py` |
| `GROK_API_KEY` | Scenario analysis (xAI) | `02_Scenario_Analysis.py` |
| `MASSIVE_API_KEY` | Price data (Polygon) | `data_engine.py` |
| `FRED_API_KEY` | Economic indicators | `02_Scenario_Analysis.py`, `15_Economic_Calendar.py` |
| `EIA_API_KEY` | Energy data | `eia_helpers.py` |
| `FINNHUB_API_KEY` | Earnings calendar | `15_Economic_Calendar.py` |
| `FMP_API_KEY` | Financial data | Various |
| `LOCAL_DEV` | Skip auth locally | `auth.py` |

No auth needed for: Polymarket (public API), StockTwits (public API via curl_cffi), yfinance.

---

## Key Patterns

- **Auth:** Supabase with session persistence; returns `None` locally to bypass
- **Caching:** `@st.cache_data(ttl=...)` — 15min to 1hr TTLs
- **Data fallback:** Massive API → yfinance for price data
- **Admin gates:** Force refresh button gated to `jdmeyer05@gmail.com` and `local-dev@preview`
- **StockTwits:** Requires `curl_cffi` (not `requests`) to bypass Cloudflare. Already installed via yfinance.
- **Grok history:** JSON file at `src/grok_regime_history.json`. Contains timestamped entries with regime probabilities, sentiment summaries, change summaries, and asset estimates.

---

## Hardcoded Data That Needs Manual Updates

These change infrequently but are NOT auto-updating:

| Data | Location in `02_Scenario_Analysis.py` | Update When |
|------|---------------------------------------|-------------|
| FOMC dot plots (3 meetings) | `build_fred_summary()` | After each FOMC with projections (4x/yr) |
| FOMC SEP economic projections | `build_fred_summary()` | Same as above |
| Beige Book summaries (3 releases) | `build_fred_summary()` | After each Beige Book (8x/yr) |
| Historical stress test drawdowns | `HISTORICAL_SCENARIOS` dict | Rarely — only if correcting data |
| Macro regime descriptions/rationale | `MACRO_REGIMES` dict | When macro conditions shift materially |
| Regime factor moves | `REGIME_FACTOR_MOVES` dict | When recalibrating regime assumptions |
| Sector ticker mapping | `SECTOR_MAP` dict | When adding new tickers |
