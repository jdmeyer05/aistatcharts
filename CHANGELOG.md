# Changelog

## 2026-03-29 (cont.) — Similar Day Forecast v4, Track Record Rewrite, Bug Sweep

### Similar Day Price Forecast v4 (page 40) — 12 Improvements
- **Hourly temperature profile matching** — Correlation-based curve shape similarity (0-1 score) replaces daily-aggregate-only matching. Catches days with similar stats but different intraday patterns.
- **Demand-based matching** — ERCOT historical load profiles via `fetch_load_history()`. "Load Shape" match mode weights load correlation at 60%.
- **Hub basis adjustment** — Computes historical DAM spread vs HUBAVG for non-HUBAVG hubs (congestion premium/discount).
- **Spike-robust estimator** — `_robust_weighted_mean()` uses trimmed mean (drops top/bottom 20%) for hours >$150/MWh or negative. Prevents single ORDC event distortion.
- **Bootstrap 80%/95% confidence intervals** — 500-iteration weighted resampling replaces raw min/max range. Displayed on forecast chart and hourly table.
- **Rolling 7/30-day MAPE tracker** — Daily MAPE saved to Supabase, plotted as rolling averages with Good/Poor threshold lines.
- **Weighted humidity blend** — Now uses 5 weather nodes (Houston, Dallas, San Antonio, Austin, Corpus Christi) with normalized population weights for humidity.
- **DAM-RT basis forecast** — Fetches both DAM and RT prices for similar days, computes hourly RT-DAM spread.
- **Rolling marginal heat rate** — Linear regression of recent power prices on gas prices (up to 60 days). Replaces flat 7.0 HR with market-implied HR (bounded 4.0-15.0).
- **ERCOT reserve margin context** — Pulls `supply-demand` dashboard for capacity, demand, reserves. Alerts at <3 GW (ORDC) and <5 GW.
- **Block-level product view** — 5 blocks (On-Peak, Off-Peak, Super-Peak, Evening Ramp, Overnight) with 80% CI and P&L scenario table.
- **Multi-node weather visualization** — 5 ERCOT demand centers with individual station detail, spatial divergence chart, high-spread alerts.

### Track Record (page 47) — Complete Rewrite (187 → 870 lines, 5 tabs)
- **Platform Scorecard** — Hero accuracy metrics, accuracy-by-tool bar chart, predicted vs actual calibration scatter, rolling 20-prediction accuracy trend (overall + per-tool), win/loss streaks.
- **Tool Breakdown** — Expandable per-source analysis with confusion matrix (TP/FP/TN/FN), precision/recall/F1, return distribution histogram, best & worst calls.
- **Signal Engine** — Source weight visualization, current top trade ideas, conviction vs accuracy binned analysis.
- **Position Performance** — Closed position P&L distribution, win rate, profit factor, avg win/loss, full closed positions table.
- **Prediction Log** — Filterable (tool, status, direction) table of every prediction with outcomes.

### Sector Guidance Staleness Warning
- `src/sector_analysis.py`: Dynamic warning when guidance snapshot is >30 days old (caption) or >90 days old (warning). Applies to all 11 sector pages.

### FOMC Dates Consolidation
- `pages/18_Economic_Calendar.py`: Removed duplicate `FOMC_MEETINGS_2026` list (had incorrect dates), now imports from centralized `src/economic_calendar.py`.

### Bug Fixes
- Fixed `continue` outside loop in `pages/36_Quant_Lab.py` line 730 (replaced with if/else).
- Fixed 6 division-by-zero risks in `pages/40_Power_Strategies.py`: `_robust_weighted_mean`, `_bootstrap_confidence`, weather node blending, forecast weight normalization.
- Fixed KeyError risks in `pages/47_Track_Record.py`: `p["timestamp"]`, `p["source"]`, `pos["ticker"]` → safe `.get()` access.
- Fixed overconfidence detection logic in Track Record (now handles negative predictions correctly).
- Removed dead variable `_streak = 0` in Track Record.

---

## 2026-03-29 — Background Worker, AI Caching, Fed Macro Expansion

### Background Worker (`worker.py`)
- Standalone Python script running via GitHub Actions cron — 24/7, no Streamlit required
- **5 tasks**: situation briefing (Grok), timeline updates, 3-model conflict analysis, metrics snapshots (10 tickers), cache cleanup
- **Schedule**: hourly during market hours (Mon-Fri 9am-5pm ET), every 4 hours off-hours/weekends
- Writes directly to Supabase — data is fresh when users arrive

### AI Response Cache (`src/ai_cache.py`)
- Caches AI model responses keyed by input hash — same data = same analysis, skip the API call
- Vol Surface Gemini trade ideas: cached by metrics hash (spot ±0.5%, IV ±0.5%, skew ±0.02)
- Stock Analysis 3-model blend: cached by prompt hash (2h TTL)
- Scenario Analysis Grok regime: cached by regime names (1h TTL)
- Iran briefing: hourly key shared across all users (30min TTL)
- StockTwits + Polymarket: cached for cross-user sharing (30min TTL)
- **Estimated savings**: ~$0.12/duplicate call avoided, 10-60s saved per cache hit

### Fed Macro Drivers (page 21) — 8 Tabs (was 4)
- **Signal Matrix enhanced**: Aggregate hawkish/dovish score, Taylor Rule calculator (r* + inflation gap + output gap), FOMC countdown with market-implied rate expectations
- **FOMC Statement Diff** (new): Word-level diff of consecutive statements with green/red highlighting, key phrase tracker, Gemini AI interpretation (hawkish/dovish score, trading implications)
- **Inflation Deep Dive** (new): 8 CPI/PCE components charted YoY, current readings table with direction, sticky vs flexible inflation breakdown
- **Labor Market** (new): NFP bars, JOLTS openings + quits, prime-age EPOP, participation rate, wage growth
- **Yield Curve & Financial Conditions** (new): Full Treasury curve (1M-30Y) with historical overlays, 2s10s + 3M-10Y spreads with inversion alerts, Chicago Fed NFCI, Sahm Rule recession indicator

### Wall Street Analyst Consensus (Stock Analysis page)
- New section with consensus rating, price target (mean/high/low), analyst count, bull/bear breakdown
- Recent upgrades/downgrades with firm names, grades, price targets, dates
- Analyst signal wired into Signal Engine at 1.3x weight
- All data from yfinance (free, no API key needed)

### Iran Conflict Context Injection
- Energy/defense/commodity tickers now receive verified war facts in AI prompts: Hormuz closure, missile counts, infrastructure strikes, model assessments
- Data pulled from Supabase `conflict_analysis` table (always current, not session-dependent)
- Explicit instruction: "Do NOT use generic language — name specific impacts"

### User Preferences (`src/user_prefs.py`)
- Active ticker persists across sessions and devices via Supabase `user_preferences` table
- Watchlist survives page refreshes and restarts
- Heatmap list/period defaults remembered
- Recent tickers tracked (last 20)

### Summary Page — Futures Bar
- ES, NQ, Dow, Crude, Gold, Silver, NatGas, 30Y Bond, 10Y Note, Euro FX, Bitcoin
- Auto-refreshes every 2 minutes alongside the equity pulse bar

### Performance Optimizations
- **Price history table**: fetch once from Polygon, append daily — subsequent loads ~100ms vs ~1.5s
- **Options chain cache**: 2-hour TTL in Supabase, shared across all pages hitting same ticker
- **Snapshot cache**: Market pulse, heatmap, watchlist share cached snapshots (3-min TTL)
- **Batch snapshot optimization**: only fetches uncached symbols from Polygon

### Supabase Schema Additions
- `ai_response_cache` — AI model response cache with TTL
- `price_history` — daily OHLCV cache (primary key: ticker + date)
- `user_preferences` — persistent user settings
- `conflict_timeline` — Grok-discovered conflict events

### Bug Fixes
- Fixed Vol Surface Gemini indentation error (try/except inside with block)
- Fixed JSON round-trip safety for AI cache writes (double-serialize to strip non-JSON types)
- Fixed FOMC countdown date type mismatch (string → date conversion)
- Fixed Taylor Rule division by zero on CPI YoY calculation
- Fixed inflation tab division by zero on all YoY calculations
- Removed fabricated price numbers from conflict timeline impact descriptions
- Fixed `polygon_batch_snapshot` fetching all symbols instead of only uncached ones
- Fixed `_last_trading_day()` timezone (local → ET approximation via UTC-5)
- Fixed Stock Analysis `price` variable scope in analyst section
- Fixed `_rec_score` None guard in analyst consensus signal write

---

## 2026-03-28 — Major Platform Upgrade

### New Systems
- **Unified Signal Engine** (`src/signal_engine.py`) — Aggregates structured signals from 8 analysis pages into weighted composite conviction scores. Source weights: ML Predictor 1.5x, RL Trading 1.4x, Signal Scanner 1.3x, Stock Analysis 1.2x, Tech Screener 0.8x.
- **Historical Metrics Store** (`src/metrics_store.py`) — Daily snapshot of ATM IV, put skew, VRP, HV20/60, P/C ratio per ticker. 252-day percentile ranks. Auto-saves from Vol Surface and Market Expectations pages.
- **Position Lifecycle Manager** (upgraded `src/position_book.py`) — Greek tracking via BS pricing, daily P&L attribution decomposed into delta/gamma/theta/vega, alert thresholds, trade journal with entry/exit thesis and tags.
- **API Response Cache** (`src/api_cache.py`) — Supabase-backed caching layer for Polygon API. Cache hit ~100ms vs ~1.5s direct API.
- **Supabase Client** (`src/db.py`) — Shared Supabase accessor with user ID resolution.

### Supabase Migration (14 tables, 2 views, 6 RPC functions)
- All data modules (signals, metrics, positions, predictions, chat history, API cache) now use Supabase as primary storage with local JSON fallback.
- AI usage counters moved from ephemeral session_state to persistent `ai_usage` table — prevents quota bypass on refresh.
- Chat messages persisted to `chat_history` table.
- Iran Conflict analysis, infrastructure state, and source credibility wired to Supabase.
- `signal_composites` SQL view for real-time weighted aggregation.
- `metrics_percentiles` materialized view for fast percentile lookups.
- `api_cache` table with TTL-based expiry and cleanup function.

### New Pages
- **Track Record** (page 47) — Prediction accuracy dashboard evaluating T+30/60/90 outcomes.

### Vol Surface (page 43) — Major Expansion (9 tabs)
- **Surface Animation** — Fetches real historical options prices from Polygon, backs out IV via Newton-Raphson solver, animates day-by-day with Plotly.
- **Surface Comparison** — Three modes: Current vs N Days Ago, Call vs Put IV surfaces, Cross-Ticker comparison. All with difference heatmaps.
- **Gemini Trade Ideas** — Sends full surface profile (regime, percentiles, skew, VRP, term structure, cross-page signals) to Gemini 2.5 Pro for structured trade suggestions with table-formatted legs and P&L.
- **Vol Regime Bar** — Color-coded regime classification (Vol Level, VRP, Skew, Term Structure) visible on all tabs.
- **Percentile Ranks** — 252-day percentiles displayed on regime bar and injected into Gemini prompt.
- Parallelized chain fetching (ThreadPoolExecutor, 5 workers) — initial load 3-5x faster.
- `@st.fragment` on buttons to prevent tab reset during interactions.
- Gamma scalp P&L backtest using actual historical price moves.
- Skew slope quantification in bps per delta step.
- Event detection on term structure (flags DTE with IV >1.5σ above trend).

### Options Flow (page 07) — New Tabs
- **Block Trade Detection** — Institutional-size trade identification, directional bias, moneyness distribution.
- **Historical P/C Ratio Context** — Z-score vs historical averages, sentiment classification, contrarian signals.

### Options Lab (page 08) — New Tab
- **Strategy Optimizer** — Scans live chain for optimal strikes across 6 strategies, ranks by R:R within risk budget.

### Portfolio Greeks (page 44) — New Features
- **Delta Hedging Calculator** — Shares needed to neutralize delta, dynamic hedge schedule, gamma scalping P&L projection.
- **Position Book Import** — Auto-imports open positions from the Position Book.

### Economic Calendar (page 18) — New Tab
- **Surprise Tracker** — Compares actual releases to 3-month moving average consensus, aggregate surprise index, heatmap by indicator.

### Summary Page (page 01) — Complete Rewrite
- **Market Pulse Bar** — SPY, QQQ, IWM, VIX, Gold, Crude, Bonds, Dollar with auto-refresh every 2 min.
- **Three-Column Dashboard** — Signal composites, Vol regime with percentiles, Position book with alerts.
- **Prediction Accuracy** — Track record for each signal source in the AI Intelligence row.
- **12 Feature Cards** — Showcase of all major platform tools with navigation buttons.
- Fixed 0.0% change bug — period returns now skip weekends/holidays.

### Other Page Improvements
- Tech Screener: Universe scan with EMA/RSI/MACD alignment scoring, signal engine integration.
- Monte Carlo: Regime context section with vol regime assessment.
- VaR: Component risk analysis by position, fixed missing COLORS import.
- Correlation: Drawdown performance analysis.
- Scenario Analysis: Regime track record tab, signal engine integration.

### New Utility Modules
- `src/implied_vol()` — Newton-Raphson IV solver in options_models.py.
- `src/economic_calendar.py` — Centralized FOMC dates and macro event detection.
- `src/cross_context.py` — Cross-page intelligence sharing.
- `src/macro_data.py` — Extended macro sources (VIX term structure, SKEW, Fed balance sheet, CFTC COT, OECD CLI, BIS credit gap).
- `fetch_options_surface_history()` — Parallel historical options price fetching for surface animation.

### Bug Fixes (32+)
- Fixed `fetch_options_chain` crash when API returns None.
- Fixed GEX `idxmax()` on empty Series.
- Fixed Economic Calendar negative indexing in surprise calculations.
- Fixed Portfolio Greeks `get_open_positions` → `get_positions`, wrong field names for strike/expiration.
- Fixed `implied_vol` convergence check order and vega collapse handling.
- Fixed VaR page missing COLORS import.
- Fixed cross-ticker comparison NaN propagation in z-axis range.
- Fixed Meta Analysis `list.index()` ValueError.
- Fixed Calendar Spreads division by zero (5 instances).
- Fixed Algo Backtester empty DataFrame IndexError.
- Fixed Power Strategies deprecated `fillna(method="ffill")`.
- Fixed Quant Lab empty slice IndexError.
- Fixed Correlation unguarded `.iloc[-1]` on empty Series.
- Fixed Iran Conflict division by zero and undefined variable in exception handler.
- Fixed Power Analytics division by zero in reserve percentage calculation.
- Fixed Summary page 0.0% change (calendar days → trading days).
- Fixed Supabase `subscriptions` UNIQUE constraint mismatch with auth.py upsert.
- Fixed position_book missing user_id filter on update/delete queries.
