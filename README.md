# AI Statcharts

Quantitative analysis platform with AI-powered conflict intelligence, macro scenario analysis, and multi-model consensus trading tools.

## Quick Start

```bash
cd C:\Users\jdmey\aistatcharts
python -m streamlit run app.py
```

Opens at **http://localhost:8501** (or next available port).

## Platform Overview

- **47 pages** of quantitative analysis tools + automated hourly background worker (incl. 11 sector pages, 6 quant research pages, power trading strategies, meta portfolio analysis, calendar spreads, vol surface with Gemini AI trade ideas, portfolio Greeks with delta hedging, universe portfolio, market expectations engine)
- **4 AI models**: Grok 4, Gemini 2.5 Pro, Claude Sonnet/Opus, Gemini 2.5 Flash (chat)
- **Unified Signal Engine** -- aggregates signals from 8+ analysis pages into weighted composite scores per ticker
- **Historical Metrics Store** -- daily vol/options snapshots with 252-day percentile ranks
- **Position Lifecycle Manager** -- Greek tracking, P&L attribution (delta/gamma/theta/vega), alerts, trade journal
- **18-table Supabase backend** -- signals, metrics, positions, predictions, chat history, API cache, AI response cache, price history, user preferences, conflict timeline, AI usage tracking
- **API response caching** -- Polygon responses cached in Supabase (~100ms vs ~1.5s on cache hit)
- **AI response caching** -- Gemini/Grok/Claude responses cached by input hash, shared across users
- **Hourly background worker** -- GitHub Actions cron updates conflict analysis, briefings, timeline, metrics 24/7
- **User preferences** -- active ticker, watchlist, heatmap defaults persist across sessions via Supabase
- **Wall Street analyst consensus** -- yfinance ratings, price targets, upgrades/downgrades fed into Signal Engine
- **Landing page dashboard** -- market pulse bar + futures bar (auto-refresh), signal composites, vol regime, position book, heatmap, AI intelligence, prediction accuracy, 12 feature cards
- **Top nav header** -- logo, dropdown navigation, Settings popover (account, usage, market status)
- **Fun loader** -- animated spinner with quips, progress bar, milestone status, countdown ETA
- **Open Beta** -- no login required, all features unlocked, optional account creation via Settings > Log In
- **Session persistence** -- cookie-based auth recovery, auto-reload on stale mobile connections
- **Tier-based analyst chat** -- Gemini 2.5 Flash (all tiers) in inline expander, chat history persisted to Supabase
- **Subscription tiers** -- Free, Pro ($12), Premium ($29), Platinum ($79) *(currently disabled — open beta)*
- **Token system** -- buy analysis tokens ($8/50, $25/200, $50/500) *(currently disabled — open beta)*
- **Supabase auth** -- login, registration, "remember me", session timeout warning *(optional during open beta)*
- **Stripe integration** -- subscription billing with lookup_key tier mapping *(disabled during open beta)*
- **Mobile-optimized** -- 44px touch targets, responsive breakpoints, pull-to-refresh, auto-reload
- **No sidebar** -- all controls inline via columns/expanders, sidebar fully hidden via config

## Subscription Tiers (Currently Disabled — Open Beta)

> **All users currently have full Platinum-level access without login.** Auth, tier restrictions, payment UI, and token purchases are disabled while the platform is in open beta. To re-enable: search `OPEN BETA` in `src/auth.py` and `app.py` to find the early-return lines to remove, then restore the original `app.py` login flow from git history.

| | Free | Pro ($12/mo) | Premium ($29/mo) | Platinum ($79/mo) |
|---|---|---|---|---|
| **Pages** | 18 | All 23 | All 23 | All 23 |
| **AI Analyses/day** | 0 | 5 | 20 | 50 |
| **AI Models** | None | 3 (Grok 4, Gemini 3.1 Pro, Claude Sonnet) | 3 | 3 + Claude Opus upgrade |
| **RL Trading** | No | No | Yes | Yes |
| **Analyst Chat** | Gemini Flash (5/day) | Gemini Flash (unlimited) | Gemini Flash (unlimited) | Gemini Flash (unlimited) |
| **Bonus Tokens** | Buy tokens for AI analyses beyond daily limit -- never expire |

## Key Pages

| Page | Description |
|------|-------------|
| **Summary** | Landing dashboard: market heatmap (Sectors/Indices/Fixed Income/Commodities/Mega Caps with stock-level drill-in), AI intelligence cards, watchlist |
| **Scenario Analysis** | 6-tab macro engine: Grok AI regime analysis, FRED data, portfolio impact modeling, stress tests |
| **Stock Analysis** | 3-model AI consensus scorecard + SEC EDGAR insider scoring, 8-K events, XBRL financial ratios |
| **RL Trading** | Dueling DQN ensemble with 31 features, walk-forward validation, Monte Carlo robustness, feature redundancy detection |
| **Iran Conflict** | 3-model AI war analysis (search-grounded), live situation briefing, Grok infrastructure monitoring, trending tweets, conflict timeline with ultimatum countdown |
| **Fed & Macro Drivers** | 8-tab page: signal matrix (aggregate score, Taylor Rule, FOMC countdown), driver trends, Fed policy (dot plot, SEP), FOMC statement diff (word-level with Gemini AI interpretation), inflation deep dive (CPI decomposition, sticky vs flexible), labor market (NFP, JOLTS, Beveridge), yield curve & financial conditions (NFCI, Sahm Rule), market sentiment (StockTwits, Polymarket) |
| **Smart Money** | 13F institutional holdings, congressional trades, activist investors (13D), 8-K event search |
| **Economic Calendar** | Today's events + countdown, week view, yield curve, inflation, labor, earnings, auctions |
| **Algo Backtester** | 13 strategies, 9-tab analysis: equity curve, drawdown, trade log, monthly heatmap, return distribution, position chart, walk-forward (9 window combos), regime analysis, strategy comparison. López de Prado methods: Deflated Sharpe, PBO (CPCV), Triple Barrier exits, bet sizing, fractional differentiation, sequential bootstrap. |
| **Monte Carlo** | Student-t (fat tails), empirical block bootstrap, and GBM simulation. Warns when normal assumptions don't fit. |
| **Power Analytics** | Institutional-grade ERCOT power market analysis: duck curve (historical overlay, flexibility metrics, over-gen risk, storage arbitrage, forecast vs actual, multi-ISO comparison), implied heat rates (hourly curve, System Lambda, heat rate vs load scatter, duration curve), spark spreads (VOM-adjusted margins, hourly profitability, DAM vs RT, System Lambda vs fuel cost, duration curve), generation stack (gas fleet disaggregation, inframarginal rent, fuel mix area chart, belly vs peak, reserve margin). Data: ERCOT Public API (NP6-345, NP4-737, NP4-732, NP6-905, NP4-190, NP6-322, NP4-523), EIA Hourly Grid Monitor, yfinance NG/CL futures, ERCOT dashboard real-time. |
| **Sector Analysis (11)** | All 11 SPDR sectors (XLE-XLRE): 8-tab template with revenue, CapEx, valuation, alpha signals, risk, guidance, macro overlay, pairs correlation. Shared via `src/sector_analysis.py`. |
| **Correlation** | Cross-asset correlation matrix, regime correlations (calm/normal/stress), rolling correlation, hierarchical clustering, breakdown alerts, PCA factor structure |
| **Quant Lab** | Lopez de Prado methods: fractional differentiation, CUSUM filter, SADF bubble detection, triple barrier labeling, meta-labeling, sequential bootstrap, feature importance (MDI/MDA/SFI/SHAP), HRP, microstructure (VPIN/Kyle's Lambda/Amihud), entropy |
| **Factor Decomposition** | Fama-French 5-factor + momentum decomposition: factor returns, exposure betas, alpha attribution waterfall, rolling style drift detection, risk decomposition pie |
| **Portfolio Optimizer** | 9 allocation methods head-to-head: tangency, robust Sharpe, min variance, risk parity, max diversification, HRP (Ward), HERC (CVaR), HCAA (1/N), Black-Litterman. Ledoit-Wolf covariance denoising. Walk-forward OOS backtest. Dendrogram visualization. |
| **Signal Scanner** | 8-tab institutional scanner: momentum (12-1, acceleration, risk-adjusted), mean reversion (RSI/BB/Z-score, confluence), value & quality (PE/PB/ROE/margins), earnings & sentiment (EPS revisions, insider buying), regime & microstructure (VPIN, entropy), factor correlation (redundancy, eigenvalue decomposition), composite ranking (configurable weights). |
| **Meta Analysis** | 9-tab cross-method portfolio comparison engine: walk-forward equity curves, allocations with rebalance history, forward return estimates (analyst/EPS/valuation/macro), performance ranking, institutional analytics (net-of-cost, regime analysis, capture ratios, stress tests, capacity), de Prado statistical tests (DSR, PBO, sequential bootstrap, min track record), drawdown duration, rolling analysis, universe grid with hierarchical two-layer allocation. SPY benchmark. CSV export. |
| **Power Strategies** | Spark spreads, heat rate trades, peak/off-peak, RT vs DAM arb, renewable curtailment, congestion analysis. Live intraday charts. **Similar Day Price Forecast v4**: 5-node weather matching (Houston/Dallas/SA/Austin/CC), hourly temperature curve correlation, ERCOT load shape matching, bootstrap 80%/95% CI, spike-robust estimation, rolling marginal heat rate regression, hub basis adjustment, DAM-RT basis forecast, reserve margin alerts, block product P&L scenarios, 3-day heatmap, rolling 7/30-day MAPE tracker. De Prado-style strategy backtester with walk-forward, sequential bootstrap, DSR. |
| **Calendar Spreads** | 8-tab calendar spread suite: builder, term structure, scanner with VIX regime, P&L simulator, roll optimizer, risk analysis, backtest, AI assessment |
| **Vol Surface** | 9-tab: 3D surface, IV skew, term structure, dislocations, skew metrics, gamma scalping (with P&L backtest), surface animation (real historical data), surface comparison (date/call-put/cross-ticker), Gemini 2.5 Pro AI trade ideas |
| **Portfolio Greeks** | Position entry (imports from Position Book), aggregate dollar Greeks, risk scenario heatmap, Greeks by expiration, Greeks over time, delta hedging calculator with gamma scalping P&L projection |
| **Track Record** | 5-tab institutional accountability dashboard: platform scorecard (rolling accuracy, calibration curve, win/loss streaks), tool breakdown (confusion matrix, precision/recall/F1, return distributions, best/worst calls), signal engine performance (conviction vs accuracy, source weights), position P&L (win rate, profit factor, closed position analytics), full prediction log with filters |
| **Universe Portfolio** | 15-group universe scan, cross-group correlation, hierarchical two-layer allocation, walk-forward backtest, statistical tests |
| **Market Expectations** | Cross-asset options intelligence: vol dashboard, term structure comparison, skew landscape, PCA, implied correlation, VRP, trade synthesis |
| **+ 11 more** | Historical analysis, options (3 pages), ML predictor, screener, VaR, oil, natgas, ERCOT (2), futures |

## AI Models

### Iran Conflict Analysis (3 models in parallel)

| Model | ID | Role |
|-------|-----|------|
| **Grok 4** | `grok-4.20-0309-reasoning` | Real-time visual OSINT with **live web search** enabled: war maps, satellite imagery, X/Twitter breaking news, narrative shifts |
| **Gemini 3.1 Pro** | `gemini-3.1-pro-preview` | Quantitative engine with **Google Search grounding**: facility-by-facility supply model, oil price math, economic impact |
| **Claude Sonnet** | `claude-sonnet-4-6` | Bayesian reasoning (no search — analyzes provided data only): scenario trees, ceasefire decomposition, red-teaming, confidence intervals |
| **Claude Opus** *(Platinum)* | `claude-opus-4-6` | Upgraded Claude with deeper reasoning |

### Independent Grok Calls (auto-refresh, no button needed)

| Function | Model | Refresh | Purpose |
|----------|-------|---------|---------|
| Infrastructure Status | Grok 4 reasoning | 30 min | Facility status from 30+ verified X accounts (own tab) |
| Live Tweets | Grok 4 fast | 10 min | Breaking news feed (verified sources only) |
| Situation Briefing | Grok 4 fast | 15 min | 4-hour war correspondent dispatch (above tabs) |
| Breaking News Brief | Grok 4 fast | 15 min | 6-hour summary fed to all models as context |
| Timeline Auto-Update | Grok 4 fast | 1 hr | New conflict events appended |

### Data Enrichment (fed into AI models)

- Polymarket prediction odds (ceasefire, oil, escalation contracts)
- Oil futures term structure (backwardation/contango signal)
- LNG/natgas prices (TTF, Henry Hub)
- Brent, WTI, VIX, Gold, DXY, Henry Hub live prices (Polygon + yfinance fallback with sanity bounds)
- SEC EDGAR 8-K defense sector filings
- Post-processing layer: replaces hallucinated prices with real API data, clamps disruption/escalation to sane bounds

## Data Sources

| Source | Type | Legal Status |
|--------|------|-------------|
| **Polygon (Massive API)** | Market prices, options, financials, insider txns | Paid (Stocks Starter) |
| **Finnhub** | Analyst recommendations | Free tier, commercial OK |
| **SEC EDGAR** | 13F holdings, insider txns, 8-K events, XBRL financials | Public domain |
| **GDELT** | Media intensity, tone, bulk conflict events | Open data |
| **ACLED** | Armed conflict events (11 ME countries) | Academic/commercial |
| **FRED** | 24 economic indicators | Public domain |
| **EIA** | Oil/gas prices, storage, Hourly Grid Monitor (rto/fuel-type-data, region-data) | Public domain |
| **ERCOT Public API** | Actual load, wind/solar gen+forecast, RT/DAM SPP, System Lambda, ancillary services | Free (subscription key) |
| **ERCOT Dashboard** | Real-time fuel mix (5-min), supply/demand, system-wide prices | Public |
| **Polymarket** | Prediction market odds | Public API |
| **StockTwits** | Social feed (official accounts only) | Public API |
| **MarineTraffic** | Vessel tracking / AIS data (via Grok X search) | X/Twitter |
| **yfinance** | Fallback price data when Polygon returns stale/bad data | Free |
| **CBOE (via yfinance)** | VIX term structure (VIX9D/VIX/VIX3M/VIX6M/VIX1Y), SKEW index | Free |
| **CFTC** | Commitments of Traders — managed money positioning | Public API |
| **OECD** | Composite Leading Indicators (6 countries) | Public SDMX API |
| **BIS** | Credit-to-GDP gap (financial crisis predictor) | Public API |
| **Treasury.gov** | Auction results (bid-to-cover, yields) | Public API |

## Quantitative Methods (López de Prado Framework)

The backtester and simulation pages implement institutional-grade statistical rigor from *Advances in Financial Machine Learning*:

| Method | Page | What It Does |
|--------|------|-------------|
| **Deflated Sharpe Ratio** | Algo Backtester | Adjusts observed Sharpe for multiple testing bias (number of parameter combos tried) |
| **Probability of Backtest Overfitting (PBO)** | Algo Backtester | CPCV with purging + embargo -- measures probability best IS strategy underperforms OOS |
| **Walk-Forward (9 combos)** | Algo Backtester | Tests all 3x3 train/test window combinations, Sharpe heatmap, aggregate robustness verdict |
| **Triple Barrier Method** | Algo Backtester | Profit-taking, stop-loss, time-expiry exits (ATR-based) replace indefinite holds |
| **Meta-Labeling / Bet Sizing** | Algo Backtester | Scales position size by signal confidence (0-1) instead of binary ±1 |
| **Fractional Differentiation** | Algo Backtester | Preserves long memory while achieving stationarity (auto-finds minimum d via ADF) |
| **Sequential Bootstrap** | Algo Backtester | Block bootstrap preserving serial dependence -- honest p-values for autocorrelated returns |
| **Regime Analysis** | Algo Backtester | Performance bucketed by volatility (low/med/high) and trend (bull/bear/sideways) regime |
| **Student-t Simulation** | Monte Carlo | Fat-tailed distribution fitted to historical returns -- captures crash/rally risk GBM misses |
| **Empirical Block Bootstrap** | Monte Carlo | Samples contiguous blocks from actual return history -- preserves autocorrelation and distribution |
| **Feature Redundancy Detection** | RL Trading | Flags correlated feature pairs (|r| > 0.8) that inflate overfitting risk |
| **Merton Jump-Diffusion** | Options Lab | Poisson jump process for OTM pricing where standard BS understates tail risk |

**Global disclaimer** on all pages with trading signals: backtested results don't guarantee future returns, not financial advice.

## Security

### Auth Isolation (Multi-User)
Streamlit on Cloud Run shares a single server process across all users. The Supabase Python client stores auth sessions in-memory on the server, meaning `supabase.auth.get_session()` returns whichever user authenticated last -- not the current visitor.

**Mitigations:**
- **Cookie-only auth recovery** -- sessions are recovered exclusively via per-browser `sb_refresh` cookies, never from the shared server-side session
- **Password change re-auth** -- `update_user()` re-authenticates via the user's own cookie before updating
- **No shared sign_out** -- logout clears the browser cookie and session state only, without calling `sign_out()` on the shared client
- **Cookie sanitization** -- refresh tokens are stripped of non-alphanumeric characters before injection into JavaScript
- **XSRF protection enabled** -- Streamlit's built-in cross-site request forgery protection is on in production

### Webhook Security
- Stripe webhook signature verification is **required** -- requests are rejected if `STRIPE_WEBHOOK_SECRET` is not configured
- Token purchases use atomic Supabase RPC (`increment_tokens`) to prevent race conditions

### XSS Hardening
- AI model output rendered in `unsafe_allow_html` contexts is escaped via `html.escape()` to prevent injection

### Supabase SQL Functions
The following function must exist in Supabase for atomic token operations:
```sql
CREATE OR REPLACE FUNCTION increment_tokens(p_email TEXT, p_amount INT)
RETURNS VOID AS $$
BEGIN
  INSERT INTO user_tokens (email, balance, updated_at)
  VALUES (p_email, p_amount, NOW())
  ON CONFLICT (email)
  DO UPDATE SET balance = user_tokens.balance + p_amount, updated_at = NOW();
END;
$$ LANGUAGE plpgsql;
```

## Tech Stack

- **Frontend:** Streamlit
- **Auth:** Supabase
- **Payments:** Stripe
- **Data viz:** Plotly (uirevision for stable charts)
- **ML:** scikit-learn, scipy (RL: pure numpy DQN)
- **AI:** Anthropic SDK, Google GenAI SDK, OpenAI SDK (for Grok x.ai)
- **Market Data:** Polygon API + yfinance fallback (sanity-bounded)
- **OSINT:** SEC EDGAR, GDELT, ACLED

## Project Structure

```
app.py                    Entry point (redirects to Summary; login disabled for open beta)
worker.py                 Hourly background worker (conflict analysis, metrics, cleanup)
webhook_server.py         Stripe webhook handler (Flask, port 5000)
.github/workflows/
  hourly-worker.yml       GitHub Actions cron (hourly market, 4h off-hours)
Dockerfile                Cloud Run deployment (4 CPU, 4GB recommended)
static/
  logo.png                Platform logo (base64-encoded into header)
src/
  api_keys.py             Centralized API key retrieval (single source of truth)
  auth.py                 Auth, tiers, tokens, Stripe, session timeout, cookies
  layout.py               setup_page(), header, nav, Settings popover, footer
  styles.py               Global CSS, 5-layer background, responsive breakpoints, Plotly defaults
  chatbot.py              Tier-based analyst chat (Gemini 2.5 Flash, inline expander)
  edgar.py                SEC EDGAR: 13F, insider scoring, 8-K, XBRL ratios, 13D activist
  gdelt_events.py         GDELT bulk event download & processing
  data_engine.py          Polygon API (snapshots, batch snapshots, history, options chains)
  market_data.py          Yahoo Finance, FRED, StockTwits, Polymarket, CFTC COT, commodity futures
  sector_analysis.py      Shared 8-tab sector analysis template (SectorConfig + render_sector_page)
  portfolio_models.py     Factor betas, regime estimation, stressed correlations, blend estimates
  options_models.py       BS-Merton Jump Diffusion pricing
  eia_helpers.py          EIA API v2 (supply data, Henry Hub, hourly grid monitor)
  ercot_api.py            ERCOT Public API (authenticated + dashboard endpoints)
  simulation.py           Stochastic price simulation (Random Forest, seasonal Monte Carlo)
  json_repair.py          Multi-strategy JSON repair for LLM output
  analysis_history.py     AI analysis history persistence (load/save/staleness)
  cross_context.py        Cross-page intelligence sharing (write/read/build_ai_context)
  signal_engine.py        Unified signal aggregation (8 pages → weighted composite scores)
  metrics_store.py        Historical metrics store (daily snapshots, percentile ranks)
  position_book.py        Position lifecycle (Greeks, P&L attribution, alerts, journal)
  prediction_tracker.py   Prediction accuracy tracking (T+30/60/90 evaluation)
  api_cache.py            Supabase-backed API response caching layer
  db.py                   Shared Supabase client accessor with user ID resolution
  ai_cache.py             AI response caching (eliminates redundant Gemini/Grok/Claude calls)
  user_prefs.py           Persistent user preferences via Supabase (ticker, watchlist, settings)
  macro_data.py           Extended macro sources (VIX term structure, SKEW, Fed balance sheet, CFTC COT, OECD CLI, BIS credit gap)
  economic_calendar.py    Centralized FOMC dates and macro event detection
  quant_features.py       Shared quant functions (frac diff, CUSUM, triple barrier, HRP, VPIN, entropy)
  gov_data.py             CFTC COT, Treasury yields/auctions, defense contracts
pages/
  01_Summary.py           Landing dashboard (batch-loaded heatmap, AI intelligence cards)
  02_Scenario_Analysis.py Macro scenario engine (7 tabs)
  03_Stock_Analysis.py    AI stock analysis + EDGAR insider/8-K/XBRL
  04_RL_Trading.py        Reinforcement learning trading
  05-13                   Historical, options (3), ML predictor, screener, backtester, Monte Carlo, VaR
  14-17                   Oil, NatGas, ERCOT Power, ERCOT Capacity
  18_Economic_Calendar.py FRED releases, yield curve, earnings, auctions
  19_Iran_Conflict.py     AI-powered conflict intelligence (3 models)
  20_Futures.py           Multi-asset futures dashboard
  21_Fed_Macro_Drivers.py Fed policy dashboard (8 tabs)
  22_Smart_Money.py       13F holdings, congressional trades, activist investors
  23_Power_Analytics.py   Duck curve, heat rates, spark spreads, stack analysis
  24-34                   Sector analysis (all 11 SPDR sectors: XLE-XLRE)
  35_Correlation.py       Cross-asset correlation, regime analysis, PCA
  36_Quant_Lab.py         De Prado methods (8 tabs: frac diff, CUSUM, triple barrier, HRP, etc.)
  37_Factor_Decomposition.py  Fama-French 5-factor + momentum decomposition
  38_Portfolio_Optimizer.py    6 allocation methods + Black-Litterman
  39_Signal_Scanner.py    Systematic signal scanner (momentum, mean reversion, composite)
  40_Power_Strategies.py  Power trading strategies (10 tabs: charts, spark, heat rate, peak/off-peak, RT/DAM, curtailment, congestion, similar day forecast v4, backtest, meta)
  41_Meta_Analysis.py     9-tab cross-method portfolio comparison engine with hierarchical allocation
  42_Calendar_Spreads.py  8-tab calendar spread suite with scanner, backtest, AI assessment
  43_Vol_Surface.py       9-tab vol surface: 3D, skew, term structure, dislocations, metrics, gamma scalp, animation, comparison, Gemini AI trade ideas
  44_Portfolio_Greeks.py   Position-level Greeks with delta hedging calculator
  45_Universe_Portfolio.py 7-tab multi-group portfolio construction engine
  46_Market_Expectations.py 8-tab cross-asset options intelligence with trade synthesis
  47_Track_Record.py      5-tab institutional track record (scorecard, tool breakdown, signals, positions, log)
  99_Login.py             Standalone login/register page (accessible via Settings popover)
data/
  gdelt_events/           Cached GDELT daily event files (gitignored)
  signals/                Signal engine JSON fallback (gitignored)
  metrics_history/        Per-ticker metrics JSON fallback (gitignored)
  positions/              Position book JSON fallback (gitignored)
  predictions/            Prediction tracker JSON fallback (gitignored)
  iv_surface_cache/       IV surface daily snapshots (gitignored)
.streamlit/
  config.toml             Theme, toolbar, static serving, sidebar disabled
  secrets.toml            API keys (gitignored)
```

## Environment Setup

All keys in `.streamlit/secrets.toml` (gitignored):

```
SUPABASE_URL, SUPABASE_KEY     Auth & database
STRIPE_SECRET_KEY               Stripe billing
STRIPE_WEBHOOK_SECRET           Webhook signature verification
GROK_API_KEY                    Grok 4 (xAI)
GEMINI_API_KEY                  Gemini 3.1 Pro + 2.5 Flash
ANTHROPIC_API_KEY               Claude Sonnet / Opus
FRED_API_KEY                    Economic data
EIA_API_KEY                     Energy data
MASSIVE_API_KEY                 Polygon market data
FINNHUB_API_KEY                 Analyst recommendations
ACLED_EMAIL, ACLED_PASSWORD     Armed conflict data (optional)
LOCAL_DEV = "true"              Skip auth locally
```

## Cloud Run Deployment

```bash
gcloud run deploy aistatcharts \
  --source=. \
  --cpu=4 \
  --memory=4Gi \
  --concurrency=80 \
  --min-instances=1 \
  --max-instances=10 \
  --region=us-central1
```

## Stripe Setup

Payment links in `src/auth.py` -> `STRIPE_LINKS`. Tier detection: price metadata `tier` -> lookup_key -> product name.

| Product | Price | Type |
|---------|-------|------|
| Pro | $12/mo | Recurring |
| Premium | $29/mo | Recurring |
| Platinum | $79/mo | Recurring |
| Starter Tokens (50) | $8 | One-time |
| Power Tokens (200) | $25 | One-time |
| Elite Tokens (500) | $50 | One-time |

**Webhook:** dashboard.stripe.com/webhooks -> endpoint `/stripe/webhook` -> events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed`

**Supabase tables (18):**
- `subscriptions` -- email, plan_type, status, stripe_customer_id, stripe_price_id
- `user_tokens` -- email, balance
- `payment_failures` -- email, invoice_id, failed_at, resolved
- `signals` -- unified signal engine (direction, conviction, vol_view per source/ticker)
- `metrics_history` -- daily vol/options snapshots (ATM IV, skew, VRP, HV20/60)
- `positions` -- position book with JSONB Greeks, alerts, journal
- `pnl_history` -- daily P&L attribution (delta/gamma/theta/vega decomposition)
- `predictions` -- prediction tracker (T+30/60/90 outcome evaluation)
- `iv_surface_snapshots` -- daily IV chain cache for surface animation
- `conflict_analysis` -- Iran conflict analysis history + infrastructure state
- `ai_usage` -- persistent daily AI/chat usage counters (prevents refresh bypass)
- `chat_history` -- persistent chat conversation log
- `source_credibility` -- news/intel source reliability scores
- `api_cache` -- Polygon API response cache (TTL-based, ~100ms reads)
- `ai_response_cache` -- AI model response cache with TTL (Gemini/Grok/Claude + Similar Day forecasts)
- `price_history` -- daily OHLCV cache (fetch once from Polygon, append daily)
- `user_preferences` -- persistent user settings (active ticker, watchlist, theme, heatmap defaults)
- `conflict_timeline` -- Grok-discovered conflict events with auto-update

**Supabase views:**
- `signal_composites` -- real-time weighted signal aggregation in SQL
- `metrics_percentiles` -- pre-computed 252-day percentile ranks (materialized)

**Supabase RPC functions (6):**
- `increment_tokens` -- atomic token balance update
- `increment_ai_usage` -- atomic daily usage counter
- `get_metrics_coverage` -- ticker summary for metrics store
- `cleanup_expired_cache` -- prune expired API cache entries
- `cleanup_old_signals` -- prune signals >7 days, chat >90 days
- `refresh_percentiles` -- refresh materialized view
