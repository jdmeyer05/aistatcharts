# Changelog

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
