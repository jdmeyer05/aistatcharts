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

Institutional-grade **quantitative trading & analysis platform** built with Streamlit. Features: AI-powered conflict intelligence (4 models), macro scenario analysis, individual stock scorecards, RL trading strategy optimizer, options analytics, backtesting, energy/macro monitoring, and subscription-gated access.

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
src/iran_conflict_history.json → AI conflict analysis history (auto-generated, gitignored)
data/gdelt_events/            → Cached GDELT daily event files (gitignored)
data/acled_events.csv         → Cached ACLED conflict events (gitignored)
```

### src/ — Core Modules

| File | Purpose |
|------|---------|
| `auth.py` | Supabase auth, session recovery, **subscription tier system** (free/pro/premium/platinum), page gating, AI quota enforcement, Stripe integration |
| `layout.py` | `setup_page()` universal page setup, **threat dashboard banner** (S&P 500, WTI Crude, VIX, Gold, DXY, Fed rate, Iran war day count, AI escalation score), sidebar branding with SVG logo + tier badge, data freshness status bar, background task notifications, error boundaries |
| `styles.py` | Centralized color system (10 semantic colors), global CSS injection (cards, backgrounds, borders, 5-layer background system, metric styling, sidebar input borders) |
| `ticker_tape.py` | Scrolling market ticker (10 assets: ^GSPC, QQQ, DIA, IWM, TLT, GLD, CL=F, BTC, DXY, VIX), time-synced animation, raw data API for threat dashboard |
| `gdelt_events.py` | GDELT bulk event download & processing — downloads daily CSVs from data.gdeltproject.org, filters to Iran-region conflict events (11 countries, CAMEO conflict codes), caches to parquet |
| `chatbot.py` | Tier-based analyst chat — Gemini Flash (Free/Pro) or GPT-5 (Premium/Platinum), daily limits for free tier, 50-msg history cap |
| `data_engine.py` | Market data fetcher: Massive API → yfinance fallback, ticker normalization, options chains |
| `eia_helpers.py` | EIA API v2 wrapper for energy timeseries |
| `simulation.py` | Stochastic Recursive Random Forest: 30-day forward price paths |

### pages/ — 20 App Pages

| # | File | What It Does |
|---|------|-------------|
| 01 | `Summary.py` | Dashboard: 12 candlestick charts (indices, commodities, rates), Grok macro pulse, AI alerts, portfolio snapshot, account management |
| 02 | `Scenario_Analysis.py` | **Flagship** — 7-tab macro scenario engine (see deep dive below) |
| 03 | `Stock_Analysis.py` | 4-model AI stock scorecard (Grok + GPT-5 + Gemini 3 Pro + Claude), blended consensus, radar chart, price targets |
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
| 19 | `Iran_Conflict.py` | **AI-Powered Conflict Intelligence** (see deep dive below) |
| 20 | `Futures.py` | Multi-asset futures snapshot |

---

## Subscription Tier System

Defined in `src/auth.py`. Enforced by `setup_page()` in `src/layout.py`. Stripe mapping via `STRIPE_TIER_MAP`.

| Tier | Pages | AI Models | Daily Analyses | RL Trading | Analyst Chat | Price |
|------|-------|-----------|---------------|------------|-------------|-------|
| **Free** | 17 (no 02, 03, 04) | None | 0 | No | Gemini Flash (5/day) | $0 |
| **Pro** | All 20 | 3 (Grok, Gemini, Claude) | 5/day | No | Gemini Flash (unlimited) | $12/mo |
| **Premium** | All 20 | 3 (Grok, Gemini, Claude) | 50/day | Yes | GPT-5 (unlimited) | $29/mo |
| **Platinum** | All 20 | 4 (+GPT-5) | Unlimited | Yes | GPT-5 (unlimited) | $79/mo |

Admin emails (`jdmeyer05@gmail.com`, `local-dev@preview`) always get Platinum.

### Analyst Chat (`src/chatbot.py`)
Tier-based sidebar chat with model and rate limit per tier. Configured in `CHAT_TIERS` dict. GPT-5 requires `max_completion_tokens` (not `max_tokens`) and no custom `temperature`.

### Stripe Integration
`verify_subscription()` reads `lookup_key` from active Stripe subscription price → maps via `STRIPE_TIER_MAP` → stores tier in Supabase. Supports monthly/yearly variants (`pro_monthly`, `premium_yearly`, etc.).

---

## Iran Conflict Intelligence (Page 19) — Deep Dive

### 8 Tabs
1. **AI War Analysis** — 4-model blend with specialized roles, domain-weighted scoring, disagreement detection
2. **Conflict Timeline** — Interactive timeline with oil price overlay, conflict clock, infrastructure map, supply disruption waterfall
3. **ACLED Events** — Armed conflict event data (API + CSV upload fallback)
4. **Media Intensity** — GDELT API + bulk event data (direct download, no rate limits)
5. **Topic Tracker** — Sub-topic media intensity
6. **Oil Price Correlation** — Rolling correlation, scatter plots
7. **Market Impact** — Defense & energy sector performance
8. **Sentiment Analysis** — GDELT media tone

### AI Analysis Pipeline (3 models standard, +GPT-5 for Platinum)

Each model has a specialized analytical lens. Per-model prompts are slimmed to only include relevant data sections.

| Model | Role | Domain Weights | Cost |
|-------|------|---------------|------|
| **Grok 3** | Breaking news & infrastructure monitoring (live X/Twitter) | Escalation: 1.3x | ~$0.03 |
| **Gemini 3 Pro** | Military/strategic + energy/economic (dual role) | Escalation: 1.1x, Oil: 1.4x | ~$0.01 |
| **Claude Sonnet** | Diplomatic & probabilistic reasoning | Ceasefire: 1.4x | ~$0.03 |
| **GPT-5** *(Platinum only)* | Deep strategic synthesis, challenges assumptions | Escalation: 1.2x | ~$0.13 |

Standard query cost: ~$0.07. Platinum query cost: ~$0.20. GPT-5 added at runtime via `GPT5_CONFIG` + `active_configs` dict.

### Reliability Features
- **Calibration anchors** — escalation scores mapped to historical events (10=Cuban Missile Crisis → 1=post-conflict)
- **Citation requirements** — every claim must reference 2+ specific data points
- **Anti-drift instructions** — each run is independent, no trend continuation
- **Live data injection** — GDELT intensity, oil prices, ACLED events, facility disruption breakdown all fed into prompt
- **Infrastructure monitoring** — Grok specifically searches X/Twitter for each facility status
- **Disagreement detection** — flags when models diverge >2pts on escalation, >15pp on ceasefire, >$10 on oil

### Data Sources (Iran Conflict)
- **GDELT Bulk Events** — daily CSV downloads from data.gdeltproject.org, filtered to 11 countries + conflict CAMEO codes, cached to parquet
- **GDELT API** — media intensity timelines + tone (2hr cache, retry with backoff, consolidated queries)
- **ACLED** — armed conflict events (OAuth API + CSV upload fallback)
- **EIA** — WTI spot price
- **Infrastructure targets** — 8 key facilities with lat/lon for mapping, linked to disruption breakdown

### Single Source of Truth
`DISRUPTION_BREAKDOWN` constant feeds: waterfall chart, conflict clock, AI prompt, AI tab display. Currently -12.45 mbpd net.

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

### Anti-Drift Fix
Prior history fed to Grok is limited to last 1-2 entries + base probabilities as anchor. System prompt explicitly instructs against trend continuation.

### Two-Layer Portfolio Impact Model
- **Layer 1:** Exponentially-weighted 7-factor OLS (VIX, 10Y, HY spreads, breakeven, dollar, oil, VIX×HY interaction) + block bootstrap + Student-t CIs
- **Layer 2:** Grok AI per-ticker estimates
- **Blending:** R²-adaptive + stability-adjusted weights
- **Monte Carlo:** 10K draws with regime-weighted Student-t for full distribution
- **Horizon selector:** 3m / 6m / 12m

---

## Stock Analysis (Page 03) — 4-Model Consensus

Calls Grok 3, GPT-5, Gemini 3 Pro, and Claude Sonnet in parallel with identical prompts containing fundamentals, technicals, StockTwits sentiment, and macro context. Blends scores, price targets, and recommendations. Shows individual model views side-by-side.

**GPT-5 note:** Uses `max_completion_tokens` (not `max_tokens`) and does not support custom `temperature`.

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
- **Threat dashboard banner:** Persistent top bar with live market KPIs (S&P 500, WTI, VIX, Gold, DXY), Fed rate, Iran war day count + AI escalation score
- **5-layer background:** Gradient mesh + grid lines + noise texture + topographic contours + vignette
- **Card styling:** Semi-transparent backgrounds with backdrop blur
- **Borders:** All charts, tables, expanders, alert boxes, sidebar inputs have consistent card_border
- **Metrics:** Card-styled with border and background
- **Sidebar:** SVG logo, tier badge, bordered inputs, scrollable, "app" nav link hidden
- **Ticker tape:** 10 assets (^GSPC, CL=F, ^VIX, GLD, etc.), time-synced CSS animation across page navigation
- **Status bar:** Data freshness dots (green/yellow/red) + market open/closed status

---

## API Keys & Secrets

All in `.streamlit/secrets.toml`:

| Key | Service | Used By |
|-----|---------|---------|
| `SUPABASE_URL` + `SUPABASE_KEY` | Auth & database | `auth.py` |
| `OPENAI_API_KEY` | GPT-5 | Stock Analysis, Iran Conflict, chatbot |
| `GROK_API_KEY` | Grok 3 (xAI) | Scenario Analysis, Stock Analysis, Iran Conflict, RL Assessment |
| `GEMINI_API_KEY` | Gemini 3 Pro | Stock Analysis, Iran Conflict |
| `ANTHROPIC_API_KEY` | Claude Sonnet | Stock Analysis, Iran Conflict |
| `MASSIVE_API_KEY` | Price data (Polygon) | `data_engine.py` |
| `FRED_API_KEY` | Economic indicators | Scenario Analysis, Econ Calendar |
| `EIA_API_KEY` | Energy data | Oil/NatGas pages, Iran Conflict |
| `FINNHUB_API_KEY` | Earnings calendar | Econ Calendar |
| `FMP_API_KEY` | Financial data | Various |
| `ACLED_EMAIL` + `ACLED_PASSWORD` | Armed conflict data | Iran Conflict (optional — CSV upload fallback) |
| `LOCAL_DEV` | Skip auth locally | `auth.py` |

No auth needed: Polymarket (public), StockTwits (curl_cffi), yfinance, GDELT bulk downloads.

---

## Key Patterns

- **Auth + Tiers:** Supabase auth → `get_user_tier()` → `check_page_access()` → `render_upgrade_prompt()` or allow
- **Page setup:** Every page calls `setup_page("XX_Name")` which handles: config, auth, CSS, threat dashboard, sidebar, ticker tape, status bar, tier gating
- **Caching:** `@st.cache_data(ttl=...)` — 5min for prices, 30min for sentiment/Polymarket, 1hr for FRED/AI analysis, 2hr for GDELT
- **Data fallback:** Massive API → yfinance
- **StockTwits:** Requires `curl_cffi` to bypass Cloudflare
- **Admin gates:** Force refresh, platinum tier for `jdmeyer05@gmail.com`
- **GPT-5 compatibility:** Uses `max_completion_tokens` instead of `max_tokens`, no custom `temperature`
- **Grok history:** JSON at `src/grok_regime_history.json` (gitignored)
- **Conflict history:** JSON at `src/iran_conflict_history.json` (gitignored)
- **GDELT bulk data:** Parquet at `data/gdelt_events/iran_conflict_events.parquet` (gitignored)
- **Background tasks:** RL training via `threading.Thread`, notifications via `st.session_state` checked in `render_background_notifications()`
- **Anti-drift:** AI analysis prompts include base probabilities + only last 1-2 prior results, not full history

---

## Hardcoded Data Needing Manual Updates

| Data | Location | Update When |
|------|----------|-------------|
| FOMC dot plots (3 meetings) | `02_Scenario_Analysis.py: build_fred_summary()` | After FOMC with projections (4x/yr) |
| Beige Book summaries (3) | Same | After each Beige Book (8x/yr) |
| Historical stress test drawdowns | `02: HISTORICAL_SCENARIOS` | Rarely |
| Macro regime definitions | `02: MACRO_REGIMES` | When conditions shift materially |
| Fed rate in threat banner | `src/layout.py: render_threat_dashboard()` | After each FOMC decision |
| Conflict timeline events | `19: CONFLICT_TIMELINE_EVENTS` | As events unfold |
| Infrastructure targets | `19: INFRASTRUCTURE_TARGETS` | When facility status changes |
| Supply disruption breakdown | `19: DISRUPTION_BREAKDOWN` | When facilities go online/offline |
| Conflict phases | `19: CONFLICT_PHASES` | As conflict enters new phases |
