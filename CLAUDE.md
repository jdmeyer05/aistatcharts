# AI Statcharts — Claude Quick-Start Guide

## Spin Up Local Server

```bash
cd C:\Users\jdmey\aistatcharts
python -m streamlit run app.py
```

Opens at **http://localhost:8501**. Auth is bypassed locally when `LOCAL_DEV = "true"` (see `src/auth.py`).

**Python 3.14 note:** `supabase` has a transitive dep (`pyiceberg`) that fails to build without MSVC. Workaround: install `storage3==2.19.0 --no-deps` then install remaining deps normally.

**Production (Docker):**
```bash
docker build -t aistatcharts . && docker run -p 8080:8080 aistatcharts
```

---

## What This App Does

Institutional-grade **quantitative trading & analysis platform** built with Streamlit. Features: AI-powered macro scenario analysis (4 models), individual stock scorecards, RL trading strategy optimizer, options analytics, backtesting, energy/macro monitoring, and subscription-gated access.

---

## Project Structure

```
app.py                        → Entry point: Supabase login + user agreement acceptance
Dockerfile                    → Python 3.11-slim, Cloud Run on port 8080
requirements.txt              → All pip dependencies
USER_AGREEMENT.md             → Legal terms of use (referenced at registration)
MARKETING_PLAN.md             → Go-to-market strategy
TWEET_TEMPLATES.txt           → Ready-to-post tweet templates
.streamlit/config.toml        → Dark theme (cyan primary)
.streamlit/secrets.toml       → API keys (see below)
src/grok_regime_history.json  → Hourly Grok analysis history (auto-generated, gitignored)
```

### src/ — Core Modules

| File | Purpose |
|------|---------|
| `auth.py` | Supabase auth, session recovery, **subscription tier system** (free/pro/premium/institutional), page gating, AI quota enforcement, Stripe integration |
| `layout.py` | `setup_page()` universal page setup, sidebar branding with SVG logo + tier badge, data freshness status bar, background task notifications, error boundaries |
| `styles.py` | Centralized color system (10 semantic colors), global CSS injection (cards, backgrounds, borders, 5-layer background system, metric styling) |
| `ticker_tape.py` | Scrolling market ticker (10 assets, time-synced animation for cross-page continuity) |
| `chatbot.py` | OpenAI sidebar chatbot (system prompt: quant analyst), 50-msg cap |
| `data_engine.py` | Market data fetcher: Massive API → yfinance fallback, ticker normalization, options chains |
| `eia_helpers.py` | EIA API v2 wrapper for energy timeseries |
| `simulation.py` | Stochastic Recursive Random Forest: 30-day forward price paths |

### pages/ — 20 App Pages

| # | File | What It Does |
|---|------|-------------|
| 01 | `Summary.py` | Dashboard: 12 candlestick charts (indices, commodities, rates), Grok macro pulse, AI alerts, portfolio snapshot, account management |
| 02 | `Scenario_Analysis.py` | **Flagship** — 7-tab macro scenario engine (see deep dive below) |
| 03 | `Stock_Analysis.py` | 4-model AI stock scorecard (Grok + GPT-4o + Gemini 3 Pro + Claude), blended consensus, radar chart, price targets |
| 04 | `RL_Trading.py` | Dueling DQN ensemble with prioritized replay, 31 features, 10-tab analysis including walk-forward, bootstrap significance, Monte Carlo robustness, Grok AI assessment |
| 05 | `Historical_Analysis.py` | Multi-year price history, seasonal decomposition, volatility, drawdown |
| 06 | `Options_Analysis.py` | IV skew, open interest walls, Greek exposures |
| 07 | `Options_Flow.py` | Unusual activity scanner, put/call ratios, GEX |
| 08 | `Options_Lab.py` | Vol surface, earnings analyzer, multi-leg strategy modeler |
| 09 | `ML_Stock_Predictor.py` | 30-day Random Forest forecast |
| 10 | `Tech_Screener.py` | EMA, RSI, MACD, Bollinger Bands |
| 11 | `Algo_Backtester.py` | 13 strategies, vectorized backtest |
| 12 | `Monte_Carlo.py` | GBM stochastic simulation |
| 13 | `Power_Risk_VaR.py` | Portfolio VaR/CVaR |
| 14 | `Oil_Fundamentals.py` | EIA crude data |
| 15 | `NatGas_Fundamentals.py` | EIA storage & supply |
| 16 | `ERCOT_Power.py` | Real-time TX grid |
| 17 | `ERCOT_Capacity.py` | Generation pipeline |
| 18 | `Economic_Calendar.py` | FRED releases, yield curve |
| 19 | `Iran_Conflict.py` | Geopolitical risk monitor |
| 20 | `Futures.py` | Multi-asset futures snapshot |

---

## Subscription Tier System

Defined in `src/auth.py`. Enforced by `setup_page()` in `src/layout.py`.

| Tier | Pages | AI Models | Daily Analyses | RL Trading | Price |
|------|-------|-----------|---------------|------------|-------|
| **Free** | 17 (no 02, 03, 04) | None | 0 | No | $0 |
| **Pro** | All 20 | GPT-4o only | 5/day | No | $29/mo |
| **Premium** | All 20 | All 4 | 50/day | Yes | $79/mo |
| **Institutional** | All 20 | All 4 | Unlimited | Yes | $249/mo |

Admin emails (`jdmeyer05@gmail.com`, `local-dev@preview`) always get Institutional.

---

## Scenario Analysis Engine (Page 02) — Deep Dive

### 7 Tabs
1. **Macro Portfolio Scenarios** — AI regime analysis + portfolio impact
2. **Fed & Macro Drivers** — Live FRED sparklines, FOMC dot plot, Polymarket, StockTwits
3. **Historical Stress Tests** — 8 historical crises replayed
4. **Custom What-If** — Slider-based asset shocks
5. **Bull / Base / Bear** — GBM projections
6. **Event-Driven** — Catalyst modeler
7. **Model Diagnostics** — Factor betas, residuals, correlations

### 7-Layer Grok Analysis Pipeline (hourly auto-poll)

| # | Layer | Source | Update |
|---|-------|--------|--------|
| 1 | Hard data | FRED (21 series) | Live (1hr cache) |
| 2 | FOMC | Last 3 dot plots + SEP | Hardcoded (4x/yr) |
| 3 | Beige Books | Last 3 releases | Hardcoded (8x/yr) |
| 4 | Leading indicators | GDPNow (FRED) + Grok search | Live + search |
| 5 | Retail sentiment | StockTwits (curl_cffi) | Live (30min) |
| 6 | Prediction markets | Polymarket | Live (30min) |
| 7 | X/Twitter | Grok real-time search | Per hourly call |

### Two-Layer Portfolio Impact Model
- **Layer 1:** Exponentially-weighted 7-factor OLS (VIX, 10Y, HY spreads, breakeven, dollar, oil, VIX×HY interaction) + block bootstrap + Student-t CIs
- **Layer 2:** Grok AI per-ticker estimates
- **Blending:** R²-adaptive + stability-adjusted weights
- **Monte Carlo:** 10K draws with regime-weighted Student-t for full distribution
- **Horizon selector:** 3m / 6m / 12m

---

## Stock Analysis (Page 03) — 4-Model Consensus

Calls Grok 3, GPT-4o, Gemini 3 Pro, and Claude Sonnet in parallel with identical prompts containing fundamentals, technicals, StockTwits sentiment, and macro context. Blends scores, price targets, and recommendations. Shows individual model views side-by-side.

---

## RL Trading (Page 04) — Dueling DQN Ensemble

- **Architecture:** Dueling DQN (value + advantage streams) with Prioritized Experience Replay
- **Features:** 31 inputs (technicals, Fourier cycles, intermarket, relative strength, insider data, earnings proximity, short interest) × 5 stacked timesteps = 155 state dims
- **Risk management:** Stop-loss, max daily loss, commission + spread + slippage + borrow cost
- **Validation:** Walk-forward, bootstrap significance (5K resamples), Monte Carlo robustness (200 sims)
- **Benchmarks:** Buy & hold, SMA crossover, mean reversion, momentum
- **Grok assessment:** Independent qualitative analysis with A-F grading
- **Background training:** Can train in background thread, notification on all pages when done
- **10 tabs:** How It Works, Performance, OOS, Walk-Forward, Statistical Tests, Robustness, AI Assessment, Diagnostics, Trade Analysis, Strategy Insights

---

## Visual Design System

Defined in `src/styles.py`:
- **5-layer background:** Gradient mesh + grid lines + noise texture + topographic contours + vignette
- **Card styling:** Semi-transparent backgrounds with backdrop blur
- **Borders:** All charts, tables, expanders, alert boxes have consistent card_border
- **Metrics:** Card-styled with border and background
- **Sidebar:** SVG logo, tier badge, scrollable, "app" nav link hidden
- **Ticker tape:** 10 assets, time-synced CSS animation across page navigation
- **Status bar:** Data freshness dots (green/yellow/red) + market open/closed status

---

## API Keys & Secrets

All in `.streamlit/secrets.toml`:

| Key | Service | Used By |
|-----|---------|---------|
| `SUPABASE_URL` + `SUPABASE_KEY` | Auth & database | `auth.py` |
| `OPENAI_API_KEY` | GPT-4o + chatbot | Stock Analysis, chatbot |
| `GROK_API_KEY` | Grok 3 (xAI) | Scenario Analysis, Stock Analysis, RL Assessment |
| `GEMINI_API_KEY` | Gemini 3 Pro | Stock Analysis |
| `ANTHROPIC_API_KEY` | Claude Sonnet | Stock Analysis |
| `MASSIVE_API_KEY` | Price data (Polygon) | `data_engine.py` |
| `FRED_API_KEY` | Economic indicators | Scenario Analysis, Econ Calendar |
| `EIA_API_KEY` | Energy data | Oil/NatGas pages |
| `FINNHUB_API_KEY` | Earnings calendar | Econ Calendar |
| `FMP_API_KEY` | Financial data | Various |
| `LOCAL_DEV` | Skip auth locally | `auth.py` |

No auth needed: Polymarket (public), StockTwits (curl_cffi), yfinance.

---

## Key Patterns

- **Auth + Tiers:** Supabase auth → `get_user_tier()` → `check_page_access()` → `render_upgrade_prompt()` or allow
- **Page setup:** Every page calls `setup_page("XX_Name")` which handles: config, auth, CSS, sidebar, ticker tape, status bar, tier gating
- **Caching:** `@st.cache_data(ttl=...)` — 5min for prices, 30min for sentiment/Polymarket, 1hr for FRED
- **Data fallback:** Massive API → yfinance
- **StockTwits:** Requires `curl_cffi` to bypass Cloudflare
- **Admin gates:** Force refresh, institutional tier for `jdmeyer05@gmail.com`
- **Grok history:** JSON at `src/grok_regime_history.json` (gitignored)
- **Background tasks:** RL training via `threading.Thread`, notifications via `st.session_state` checked in `render_background_notifications()`

---

## Hardcoded Data Needing Manual Updates

| Data | Location | Update When |
|------|----------|-------------|
| FOMC dot plots (3 meetings) | `02_Scenario_Analysis.py: build_fred_summary()` | After FOMC with projections (4x/yr) |
| Beige Book summaries (3) | Same | After each Beige Book (8x/yr) |
| Historical stress test drawdowns | `02: HISTORICAL_SCENARIOS` | Rarely |
| Macro regime definitions | `02: MACRO_REGIMES` | When conditions shift materially |
| Regime factor moves | `02: REGIME_FACTOR_MOVES` | When recalibrating |
| Sector ticker mapping | `02: SECTOR_MAP` | When adding tickers |
