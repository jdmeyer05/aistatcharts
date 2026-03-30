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
| `layout.py` | `setup_page()` universal page setup, header bar with logo (base64), nav dropdowns, Settings popover (account/usage/market status/logout), error boundaries, footer |
| `styles.py` | Centralized color system, global CSS injection, 5-layer background, Plotly `uirevision` defaults, sidebar fully hidden |
| `api_keys.py` | Centralized `get_secret(name)` — single source of truth for all API key retrieval |
| `edgar.py` | SEC EDGAR: CIK lookup, XBRL financials/ratios (generic `fetch_sector_*` functions), 13F, insider scoring, 8-K, 13D |
| `market_data.py` | Yahoo Finance, FRED, StockTwits sentiment, Polymarket odds, CFTC COT, commodity futures, oil term structure |
| `sector_analysis.py` | Shared 8-tab sector template: `SectorConfig` dataclass + `render_sector_page()` for all 11 SPDR sectors |
| `portfolio_models.py` | Factor betas (exp-weighted OLS), regime return estimation, stressed correlations, data/AI blend |
| `data_engine.py` | Polygon API: snapshots, batch snapshots, grouped daily, history, options chains, insider txns |
| `eia_helpers.py` | EIA API v2: energy supply data, Henry Hub (spot + daily), hourly grid monitor |
| `ercot_api.py` | ERCOT Public API (Azure B2C auth) + dashboard API (unauthenticated) |
| `quant_features.py` | Shared quant functions: frac_diff, cusum_filter, triple_barrier_labels, hrp_allocate, compute_vpin, compute_entropy, regime_filter |
| `json_repair.py` | 4-strategy JSON repair for malformed LLM output (sanitize, iterative fix, truncate, extract) |
| `analysis_history.py` | AI analysis history persistence: load/save/staleness for JSON history files |
| `gdelt_events.py` | GDELT bulk event download & processing, conflict event filtering, parquet cache |
| `chatbot.py` | Tier-based analyst chat (Gemini 2.5 Flash), inline expander with form input |
| `gov_data.py` | CFTC COT (multi-contract snapshots for AI context), Treasury yields/auctions, defense contracts |
| `options_models.py` | Black-Scholes, Merton Jump-Diffusion, BS-MJD blended pricing + Greeks + implied_vol solver |
| `simulation.py` | Stochastic Recursive Random Forest: 30-day forward price paths |
| `signal_engine.py` | Unified signal aggregation — 8 pages write signals, weighted composite scoring |
| `metrics_store.py` | Historical metrics store — daily snapshots, percentile ranks, Supabase + JSON fallback |
| `position_book.py` | Position lifecycle — Greeks, P&L attribution, alerts, journal, Supabase + JSON fallback |
| `prediction_tracker.py` | Prediction accuracy — T+30/60/90 evaluation, Supabase + JSON fallback |
| `api_cache.py` | Polygon API response caching in Supabase (~100ms reads vs ~1.5s) |
| `db.py` | Shared Supabase client accessor with user ID resolution |
| `ai_cache.py` | AI response caching — eliminates redundant Gemini/Grok/Claude calls across users |
| `user_prefs.py` | Persistent user preferences via Supabase (active ticker, watchlist, settings) |

### pages/ — 30 Active Pages (7 disabled, 11 sectors consolidated into 1)

| # | File | What It Does |
|---|------|-------------|
| 01 | `Summary.py` | Dashboard: market pulse bar, signal composites, vol regime, position book, heatmap, AI intelligence, prediction accuracy, 12 feature cards, watchlist, account |
| 02 | `Scenario_Analysis.py` | **Flagship** — 7-tab macro scenario engine (see deep dive below) |
| 03 | `Stock_Analysis.py` | 3-model AI stock scorecard (Grok + Gemini + Claude), EDGAR insider scoring, 8-K events, XBRL financial ratios |
| 04 | `RL_Trading.py` | Dueling DQN ensemble with prioritized replay, 31 features, 10-tab analysis including walk-forward, bootstrap significance, Monte Carlo robustness, feature redundancy detection, Grok AI assessment |
| 06 | `Options_Analysis.py` | IV skew, open interest walls, Greek exposures |
| 08 | `Options_Lab.py` | Vol surface, earnings analyzer, multi-leg strategy modeler |
| 11 | `Algo_Backtester.py` | 13 strategies, 9 tabs: equity curve, drawdown, trade log (DSR + PBO + bootstrap), monthly heatmap, return distribution, position chart, walk-forward (9 window combos), regime analysis, strategy comparison. De Prado: Deflated Sharpe, PBO (purged CPCV), Triple Barrier, bet sizing, fracdiff, sequential bootstrap |
| 14 | `Oil_Fundamentals.py` | EIA crude data |
| 15 | `NatGas_Fundamentals.py` | EIA storage & supply |
| 16 | `ERCOT_Power.py` | Real-time TX grid |
| 17 | `ERCOT_Capacity.py` | Generation pipeline |
| 18 | `Economic_Calendar.py` | FRED releases, yield curve |
| 19 | `Iran_Conflict.py` | **AI-Powered Conflict Intelligence** — 3 specialized models, self-updating infrastructure, Polymarket, oil term structure |
| 20 | `Futures.py` | Multi-asset futures snapshot |
| 21 | `Fed_Macro_Drivers.py` | Fed policy dashboard (8 tabs) |
| 22 | `Smart_Money.py` | 13F institutional holdings, congressional trades, activist investors, 8-K events |
| 23 | `Power_Analytics.py` | Consolidated 7-tab power page: duck curve, spark spreads, stack analysis, peak/off-peak, RT/DAM arb, similar day forecast v4, strategy backtest |
| 24 | `Sector_Analysis.py` | Dynamic sector page — all 11 SPDR sectors via dropdown (replaces 24-34) |
| 35 | `Correlation.py` | Cross-asset correlation matrix, regime analysis, hierarchical clustering, PCA, breakdown alerts |
| 36 | `Quant_Lab.py` | De Prado AFML: frac diff, CUSUM, SADF, triple barrier, meta-labeling, sequential bootstrap, MDI/MDA/SFI/SHAP, HRP, microstructure, entropy |
| 37 | `Factor_Decomposition.py` | Fama-French 5+Mom: factor returns, exposure betas, alpha attribution, rolling style drift, risk decomposition |
| 38 | `Portfolio_Optimizer.py` | 9 methods: tangency, robust Sharpe, min-var, risk parity, max diversification, HRP (Ward), HERC (CVaR), HCAA (1/N), Black-Litterman. Ledoit-Wolf denoising. Walk-forward backtest. Dendrogram. |
| 39 | `Signal_Scanner.py` | 8-tab institutional scanner: momentum, mean reversion, value & quality, earnings & sentiment, regime & microstructure (VPIN/entropy), factor correlation, composite ranking |
| 41 | `Meta_Analysis.py` | 9-tab cross-method portfolio engine: walk-forward equity curves, allocations, forward estimates, institutional analytics (net-of-cost, regime, capture ratios, stress, capacity), de Prado stats (DSR, PBO, bootstrap), universe grid, hierarchical two-layer allocation, SPY benchmark, CSV export |
| 42 | `Calendar_Spreads.py` | 8-tab calendar spread suite with scanner, backtest, AI assessment |
| 43 | `Vol_Surface.py` | 9-tab vol surface with Gemini AI trade ideas |
| 44 | `Portfolio_Greeks.py` | Position-level Greeks with delta hedging calculator |
| 45 | `Universe_Portfolio.py` | 7-tab multi-group portfolio construction |
| 46 | `Market_Expectations.py` | 8-tab cross-asset options intelligence |
| 47 | `Track_Record.py` | 5-tab institutional track record |
| 99 | `Login.py` | Standalone login/register page |

**Disabled pages** (code preserved, toggle via `DISABLED_PAGES` in `src/layout.py`):
05 Historical Analysis, 07 Options Flow, 09 ML Predictor, 10 Tech Screener, 12 Monte Carlo, 13 VaR, 40 Power Strategies (merged into 23)

---

## Subscription Tier System

Defined in `src/auth.py`. Enforced by `setup_page()` in `src/layout.py`. Stripe mapping via `STRIPE_TIER_MAP`.

| Tier | Pages | AI Models | Daily Analyses | RL Trading | Analyst Chat | Price |
|------|-------|-----------|---------------|------------|-------------|-------|
| **Free** | 21 (no 02, 03, 04) | None | 0 | No | Gemini Flash (5/day) | $0 |
| **Pro** | All 30 | 3 (Grok 4, Gemini 3.1 Pro, Claude Sonnet) | 5/day | No | Gemini Flash (unlimited) | $12/mo |
| **Premium** | All 30 | 3 (Grok 4, Gemini 3.1 Pro, Claude Sonnet) | 20/day | Yes | Gemini Flash (unlimited) | $29/mo |
| **Platinum** | All 30 | 3 + Claude Opus upgrade | 50/day | Yes | Gemini Flash (unlimited) | $79/mo |

Admin emails (`jdmeyer05@gmail.com`, `local-dev@preview`) always get Platinum.

### Token System (`src/auth.py`)
When users exceed their daily included AI analyses, they can purchase token packs:
- Starter: 50 tokens / $8 ($0.16/token, 56% margin)
- Power: 200 tokens / $25 ($0.125/token, 44% margin)
- Elite: 500 tokens / $50 ($0.10/token, 30% margin)

1 token = 1 AI analysis. Tokens never expire. Free users can buy tokens to access AI without a subscription.
- `check_ai_quota()` checks daily allowance first, then token balance
- `increment_ai_usage()` deducts from daily first, falls back to tokens
- Token balance persisted in Supabase `user_tokens` table (survives sessions)
- Token balance shown in header badge and Summary account section

### Analyst Chat (`src/chatbot.py`)
Tier-based sidebar chat. Free/Pro/Premium use Gemini Flash. Platinum uses GPT-5. Configured in `CHAT_TIERS` dict.

### Stripe Integration
- `verify_subscription()` checks price metadata `tier` → lookup_key → product name
- `get_user_tier()` checks: admin list → session cache → Supabase (webhook) → Stripe API → free
- Payment links configured in `STRIPE_LINKS` dict (all plans + token packs + portal)
- `check_payment_failures()` shows red banner when payment fails

### Webhook Server (`webhook_server.py`)
Flask server (port 5000) handling Stripe webhook events:
- `checkout.session.completed` → activates subscription tier or adds tokens
- `customer.subscription.updated` → tier upgrade/downgrade
- `customer.subscription.deleted` → reverts to free
- `invoice.payment_failed` → flags in Supabase, shows banner to user

Run: `python webhook_server.py` (separate from Streamlit)

### Supabase Tables (18)
- `subscriptions` — user tier tracking (Stripe webhook-driven)
- `user_tokens` — AI analysis credit balance
- `payment_failures` — failed payment flags
- `signals` — unified signal engine (8 pages → composite scores)
- `metrics_history` — daily vol/options snapshots per ticker
- `positions` — position book with JSONB Greeks, alerts, journal
- `pnl_history` — daily P&L attribution (delta/gamma/theta/vega)
- `predictions` — prediction tracker (T+30/60/90 outcomes)
- `iv_surface_snapshots` — daily IV chain cache for animation
- `conflict_analysis` — geopolitical risk analysis history
- `ai_usage` — persistent daily AI/chat usage counters
- `chat_history` — persistent chat conversation log
- `source_credibility` — news source reliability scores
- `api_cache` — Polygon API response cache (TTL-based)
- `ai_response_cache` — AI model response cache (shared across users, TTL-based)
- `price_history` — Daily OHLCV cache (fetch once, append daily)
- `user_preferences` — Persistent user settings (active ticker, watchlist, defaults)
- `conflict_timeline` — Grok-discovered conflict events (auto-updated by worker)

### Supabase Views
- `signal_composites` — real-time weighted signal aggregation
- `metrics_percentiles` — pre-computed 252-day percentile ranks (materialized)

### Supabase RPC Functions (6)
- `increment_tokens` — atomic token balance update
- `increment_ai_usage` — atomic daily usage/chat counter
- `get_metrics_coverage` — ticker summary
- `cleanup_expired_cache` / `cleanup_old_signals` / `cleanup_expired_ai_cache` — data pruning
- `refresh_percentiles` — refresh materialized view

### Background Worker (`worker.py`)
Runs via GitHub Actions cron. Hourly during market hours, every 4h off-hours/weekends.
- `update_situation_briefing()` — Grok searches X/Twitter for latest war developments
- `update_timeline()` — Grok finds new conflict events, persists to `conflict_timeline`
- `update_conflict_analysis()` — 3-model blend (Grok + Gemini + Claude) escalation assessment
- `update_metrics_snapshots()` — Price history + HV20 for 10 key tickers
- `cleanup_caches()` — Purge expired entries from all cache tables

---

## Security Model

**Critical constraint:** Streamlit on Cloud Run shares one process across all users. The Supabase Python client caches auth sessions in a module-level global (`_supabase_client`). This means `supabase.auth.get_session()` returns the **last** user who authenticated on the server, not the current visitor.

### Rules for auth code:
1. **NEVER use `supabase.auth.get_session()`** to identify users — it returns the wrong user in multi-user deployments
2. **ALWAYS recover sessions from the per-browser `sb_refresh` cookie** via `supabase.auth.refresh_session(token)`
3. **NEVER call `supabase.auth.sign_out()`** — it affects the shared server session; instead just clear cookies + session state
4. **ALWAYS re-authenticate via cookie before `supabase.auth.update_user()`** — otherwise you may modify another user's account
5. **ALWAYS sanitize tokens** before interpolating into JavaScript (use `_sanitize_token()` in `src/auth.py`)
6. **ALWAYS escape AI model output** with `html.escape()` before rendering in `unsafe_allow_html=True` contexts
7. **Webhook signature verification is required** — `STRIPE_WEBHOOK_SECRET` must be set in production; unsigned payloads are rejected
8. **Token operations must be atomic** — use `increment_tokens` RPC to avoid race conditions in concurrent webhook handlers

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
