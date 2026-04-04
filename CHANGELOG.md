# Changelog

## 2026-04-03/04 — Full Trading Platform: Market Scan, Position Monitor, Trade Ideas

### Infrastructure
- **Supabase OHLCV Cache** — 3-tier cache (memory 0ms → Supabase 50ms → yfinance 2-5s). First scan caches 10yr data, subsequent scans instant. Incremental updates fetch only missing days from Polygon.
- **Polygon Live Data** — during market hours, appends today's bar from Polygon API for real-time prices
- **yfinance Thread Safety** — all `yf.download()` replaced with sequential `yf.Ticker(tk).history()` pre-fetch + parallel numpy/talib backtests. Prevents cross-ticker data contamination.
- **Supabase Strategy Params** — 575 Optuna-optimized parameter sets cached across 22 strategies × 99 tickers. Walk-forward OOS validated.

### Optuna Parameter Optimization
- Batch optimizer: POST /batch-optimize runs Bayesian TPE per strategy × ticker
- 22 strategies with tunable parameter spaces (SMA, EMA, MACD, RSI, SAR, Stochastic, ADX, Ichimoku, TEMA, BB, Donchian, OBV, CCI, Williams %R, ATR Trail, Z-Score MR, Golden Cross, composites)
- Walk-forward OOS Sharpe as objective (not in-sample overfitting)
- Results: SPY MACD 0.95 → 2.17 Sharpe, SPY SAR 1.83 → 3.48 with optimized params
- Params cached 30 days in Supabase before re-optimization
- Scan automatically loads cached optimal params

### Trade Ideas Engine
- 22 strategies grouped into 4 scoring families (Trend, Mean Rev, Volume, Composite) + Calendar display-only
- Fresh signal flips (≤10d) with family-weighted confluence — VALIDATED: 2+ families = 5.3× buy-and-hold Sharpe, 3+ = 8.5×
- Backtest-validated ATR stops from MAE/MFE tracking (not rules of thumb)
- Trend strategies: validated stop. Mean reversion: wider stops (2.5-5×ATR)
- Expected value computation with negative EV filtering
- Negative-Sharpe strategies excluded from triggers, confirmations, and dissent
- DSR threshold scales with scan size (0.5 for 20 tickers, 0.15 for 99)
- Minimum 5 trades required for triggers
- Recent 1yr Sharpe degradation detection
- Historical holding period (avg + median from backtest trade loop)
- Delayed entry analysis: backtests entries at 0-5 day delays, classifies urgency (ENTER NOW / WAIT / PATIENT)
- Vol analysis: IV vs RV → options structure (sell if overpriced, buy if cheap, stock if neutral)
- Smart vehicle selection: accounts for IV/RV, hold period, theta decay, account size
- Optimal DTE computation (3.5× median hold period)
- HOLDS THROUGH EARNINGS warning when hold period overlaps earnings
- Short interest from yfinance
- 8 preset ticker groups + ALL (99 unique tickers)
- AI trade analysis via Gemini with news + positions context
- Dynamic holdings preset from Robinhood

### Position Monitor Enhancements
- Monte Carlo simulation (10K paths GBM) with P/L distribution histogram
- Trade outlook: theta vs delta race, recovery days, per-leg ITM probability
- Scenario analysis with corrected gamma sign (short legs = negative gamma)
- Covered call detection (cross-references stock vs short calls)
- Holdings Research: Grok search + yfinance fundamentals with analyst targets, cash runway, earnings moves

### Critical Bug Fixes
- yfinance concurrent download corruption → sequential pre-fetch with retry
- Gamma sign: abs(sign) → sign (iron condor gamma was +479 instead of -1)
- Directional strike checking: calls breached when stock above, puts when below
- Robinhood P&L: negative average_price handling for credit positions
- DSR over-filtering for large scans (99 tickers × 23 strategies)
- 100% win rate on 1 trade eliminated by minimum trades filter
- IV ≈ RV no longer contradicts IVR fallback

---

## 2026-04-03 — Market Scan Overhaul, Position Monitor, Trade Ideas, Robinhood Integration

### New Pages
- **Position Monitor** (`/position-monitor`) — Live Robinhood positions via robin_stocks. Portfolio Greeks (Δ Γ Θ ν), scenario analysis table (P&L at ±1/3/5%), concentration risk, spread management signals (MANAGE/HOLD/CLOSE/EXPIRING), visual strike bars, profit capture bars, Monte Carlo simulation (10K paths GBM), trade outlook (theta vs delta race, recovery days, per-leg ITM probability), holdings research (Grok + yfinance fundamentals), covered call detection.
- **Trade Ideas** (`/trade-ideas`) — 24 strategies grouped into 4 scoring families (Trend, Mean Rev, Volume, Composite). Fresh signal flips (≤10d) with family-weighted confluence. Backtest-validated ATR stops (MAE/MFE tracking, optimal stop multiplier, survival rates). Vol analysis (IV vs RV → options structure), short interest, expected value filter. Preset ticker groups (Blue Chips, Sector Rotation, High Volatility). AI trade analysis via Gemini with news + positions context.

### Market Scan Overhaul
- Grok upgraded from grok-3 (chat completions, no search) to grok-4.20-reasoning (Responses API with web_search + x_search)
- Two-pass pipeline: grok-4-1-fast-reasoning for search → grok-4.20-reasoning for fact-checking
- Story-level dedup: keyword clustering eliminates duplicate coverage
- News categories: trump, iran_oil, macro, earnings, news with color-coded filter tabs
- Polymarket integration: actionability scoring (near-term uncertain > far-out priced-in), hover sparklines via CLOB API
- Trading Thesis: 4-section PM-style note (STANCE → TOP TRADE → RISKS → SIZING) receiving news + positions + strategy signals + Polymarket odds
- Computed 5-Day Outlook: VIX implied range, position risk table with directional strike checking
- Your Book strip with live RH positions + Greeks feeding into AI prompt
- Market hours detection: weekends + NYSE holidays
- Layout: Priority Flow (news first, thesis second, opportunities below)

### Robinhood Integration
- Login via app approval (no TOTP), session cached via robin_stocks
- Live positions: stocks + options with P&L, Greeks per leg
- Portfolio-level Greeks aggregated across all option legs + stock delta
- Covered call detection: cross-references stock holdings vs short calls
- Position data feeds into Market Scan AI thesis + Trade Ideas portfolio awareness

### Confluence Validation
- Backtested multi-family consensus signal across 8 tickers, 5 years
- Results: 1+ family = 1.52 Sharpe, 2+ = 3.52, 3+ = 5.69 (vs 0.67 buy-and-hold)
- Win rate: 55% (1 fam) → 63% (2 fam) → 68% (3 fam)
- Validates the Trade Ideas page's family-weighted approach

### Critical Bug Fixes
- yf.download() thread safety: replaced all 5 concurrent yf.download() calls with sequential yf.Ticker(tk).history() pre-fetch + parallel numpy/talib backtests
- Gamma sign: fixed abs(sign) bug that made all gamma positive (iron condor showed +479 instead of -1)
- Directional strike checking: short calls breached when stock above, short puts when stock below (was using abs distance)
- Robinhood P&L: fixed negative average_price handling for credit positions
- AI note: fixed leaked self-correction text, token truncation, market hours detection

### Dependencies Added
- robin_stocks (Robinhood API)
- pyotp (TOTP generation, installed with robin_stocks)

---

## 2026-04-02/03 — Next.js Platform Overhaul + Strategy Scanner + Market Intelligence

### New Pages
- **Market Scan** (`/daily-briefing`) — 4-source news intelligence pipeline (Polygon + SEC EDGAR + yfinance earnings + Grok live X/Twitter search), claim-level cross-verification, freshness labeling, AI market note (Gemini synthesis of scan + news), trade opportunity scanner, risk budget, sector concentration warnings
- **Strategy Scanner** (`/strategy-scanner`) — 4 modes: Multi-Ticker Scan (24 strategies × N tickers ranked by Deflated Sharpe), Parameter Optimizer (Optuna Bayesian TPE), Combination Testing (AND logic pairs/triples), Deep Scan (multi-timeframe meta-analysis with heatmap, correlation matrix, portfolio recommendation)
- **Live Scan** (`/live-scan`) — Real-time 3D visualization (Three.js react-three-fiber) of strategy scanning via SSE streaming. Instanced mesh columns rise and color as results stream in. Also has particle galaxy (Canvas) and heatmap modes.
- **Vertical Spread Scanner** (`/vertical-spreads`) — All 4 spread types (bull put, bear call, bull call, bear put) with IV skew scoring, expected move check, forward event stress test, historical backtest, Kelly sizing, compare expirations

### Strategy Scanner Engine
- 24 strategies using TA-Lib (C-compiled, matches Bloomberg/TradingView): SMA Cross, EMA Cross, Golden Cross, MACD, Donchian, ATR Trailing, Momentum, Dual Momentum, ADX+DI, Parabolic SAR, Ichimoku Cloud, TEMA Cross, RSI, BB Mean Reversion, BB Breakout, Z-Score MR, Stochastic K/D, CCI, Williams %R, OBV Divergence, Trend+RSI Composite, Trend+BB Composite, Turn-of-Month, Halloween Effect
- Sharpe computed on active days only (prevents sparse signal inflation)
- Excess Sharpe vs buy-and-hold as primary ranking metric
- DSR corrected for total combinations tested (proper multiple testing)
- Walk-forward OOS Sharpe with rolling windows on active days
- Flip cost = 2× (close + reopen), entry/exit = 1×
- yfinance adjusted OHLCV for split/dividend handling
- Intraday support (60min, 15min, 5min via Polygon)
- Optuna Bayesian optimization with walk-forward OOS objective
- Combination scanner: AND logic for signal confluence

### News Intelligence Pipeline
- Phase 1: Structured APIs in parallel (Polygon news with 24h filter + noise/opinion publisher removal, SEC EDGAR 8-K with item code labels, yfinance earnings recency-gated to last 3 days)
- Phase 2: Grok live X/Twitter + web search (explicitly told today's date, training data is stale, specific source handles)
- Phase 3: Claim-level cross-verification (keyword overlap, not just ticker match)
- Phase 4: Freshness labels (live/recent/today/stale), recency-first sorting
- Grok freshness verification: items without temporal words flagged as suspect

### Stock Analysis — Major Rebuild
- New endpoint `stock-data-full`: 10 parallel fetches (technicals via TA-Lib, fundamentals via XBRL + yfinance, StockTwits, insider scoring 0-100, 8-K events, XBRL history charts, analyst consensus)
- New endpoint `stock-ai-analysis`: 3-model parallel (Grok + Gemini + Claude) with structured JSON scoring, confidence-weighted blending, agreement/divergence reporting
- 5-tab frontend: Chart & Technicals (candlestick + EMA/BB + RSI + MACD + 3M/1Y/5Y), AI Analysis (radar chart, price targets, risks/catalysts), Insiders & EDGAR, Financials (XBRL bar charts), Model Comparison

### Iron Condor Scanner — 20 Features Added
- Historical winrate simulation (252-day backtest), alt expirations, forward event stress test (FOMC + earnings), Kelly UI, IVR/VRP/earnings filters, compare expirations tab, DGTV breach warnings, chart overlays (profit target, stop loss, 21 DTE time stop), positive EV filter
- Bug fixes: stress test long leg protection, DGTV limits scaled by contracts, market hours timezone detection

### Algo Backtester — Full Rebuild
- 14 strategies (was 7), 9 tabs (was 5), all de Prado stats (DSR, PBO, walk-forward, regime analysis)
- Strategy comparison tab with risk-return scatter
- Position chart with shaded long overlays
- Monthly returns heatmap, rolling 60-day Sharpe
- Sortino, Calmar, annualized vol, avg winner/loser metrics

### All Options Pages at Parity
- Options Analysis: 9/9 tabs (+Vol Surface 3D, Term Structure, Unusual Activity)
- Options Lab: 4/4 tabs (+Earnings Move Analyzer, Strategy Optimizer)
- Calendar Spreads: 8/8 tabs (+Scanner, Roll Optimizer, Backtest, AI Assessment)
- Portfolio Greeks: 5/5 tabs (+Greeks by Expiration, Greeks Over Time)
- Higher Greeks: 8/8 tabs (+Portfolio Higher Greeks, AI Greek Analyst)

### Vol Surface — Full Parity + Polish
- Surface Reading prose (VRP/Skew/Term Structure interpretation)
- Historical date scrubber on 3D tab
- AI Surface Narrator + AI Evolution Analysis
- Gamma Scalp P&L Backtest
- Event kink detection on term structure
- Actionable dislocation trade ideas (top 5 rich/cheap)
- P&L payoff diagram parsing from AI markdown
- Bug fixes: t.hv20/t.hv60 undefined colors, risk reversal table color inverted

### Platform Changes
- Auth gate: password-protected Next.js site
- Pages removed: RL Trading, Universe Portfolio, Market Expectations, Iran Conflict, Power Analytics (consolidated into ERCOT Power 8 tabs)
- Credit spreads weighted 1.5× over debit in Market Scan (per academic research)
- ERCOT Power consolidated to 8 tabs (added Duck Curve + Generation Stack)
- Dependencies added: optuna, ta-lib, three.js + react-three-fiber + drei

### Bug Fixes (50+)
- Stress test long leg protection for iron condors and verticals
- DGTV limits scaled by contracts
- Market hours timezone detection (Intl.DateTimeFormat)
- Timeframe toggle stale state (React closure issue)
- Prompt MACD "Bearish" when no data
- Single-model AI results missing model_results field
- Insider scoring column name mismatch (Polygon vs scorer)
- XBRL/yfinance merge priority (XBRL wins for percentages)
- Recommendation datetime serialization
- rec_order index overflow clamped
- yfinance 2D array (.ravel() for TA-Lib compatibility)
- Bollinger Bands ddof=1 (sample std)
- Flip cost 2× for position reversals
- Sparse signal Sharpe inflation (active days only)
- DSR skew/kurtosis consistency
- Bull call skew multiplier inverted
- Debit spread stress test P&L formula
- VIX fetched via yfinance (Polygon doesn't carry index tickers)
- SPY/QQQ 0% change fallback to yfinance
- Stale earnings data (trailingEps vs forwardEps → recency-gated earnings_dates)
- News recency parser ("15min ago" failure)
- React Fragment key warnings in combo table
- WebGL null values in 3D surface

---

## 2026-04-01 — Iron Condor Scanner (New Page)

### New Page: 50_Iron_Condor_Scanner
Full institutional-grade short iron condor scanner with 57-ticker universe across indices, sectors, commodities, and individual stocks.

#### Scan & Scoring Engine
- **7-factor composite score**: credit/risk × POP (slippage-adjusted), IVR band (50-75 optimal per quant manual), VRP (IV-HV20), liquidity grade (A-F), earnings penalty, historical managed win rate, theta efficiency ($/day/risk)
- **True IV Percentile**: fetches historical IV from Polygon Options Starter, caches in session_state. Ranks current IV against actual implied vol history, not HV proxy. Saves daily ATM IV to metrics_store for long-term accumulation.
- **Adaptive fill estimates**: fill % from natural-to-mid varies by liquidity grade (A=40%, B=30%, C=20%, D=10%, F=5%). Slippage deducted from credit in the score.
- **57-ticker universe**: SPY/QQQ/IWM/DIA, all 11 SPDR sectors, XBI/SMH/KRE/GDX, GLD/SLV/USO/TLT/HYG/LQD, EEM/EFA/FXI, mega-cap tech, financials, value/stable, energy majors
- **Flexible strike search**: finds nearest available strike at or beyond target wing width (handles $1/$2.50/$5/$10 strike increments)
- **Spot price fallback**: batch snapshot → price history → chain median

#### Trade Analysis (per setup)
- **Profit target / exit management**: configurable %, BS forward pricing for days-to-target, theta decay chart
- **Spread pricing**: natural, mid, and liquidity-adjusted fill estimate per setup
- **Breakevens**: upper/lower with % from spot, on payoff chart
- **Full DGTV Greeks**: net delta/gamma/theta/vega per contract, theta/vega ratio, institutional limit warnings (±0.30Δ, ±0.03Γ, ±0.20ν)
- **Position sizing (Kelly Criterion)**: managed win rate (from backtest or POP+bump), configurable stop multiplier, Kelly fraction, hard cap. Uses historical managed WR when available.
- **30Δ adjustment triggers**: BS binary search for spot price where short legs hit 0.30 delta. Warning zones shaded on payoff chart.
- **21 DTE time stop**: marked on theta decay chart

#### Backtesting & Stress Testing
- **Historical managed exit simulation**: day-by-day walk through 252 days of price history. Three exit paths per trial: profit target hit (theta decay approximation), stop loss hit, held to expiration. Reports managed WR, exp-only WR, early profit count, stopped out count, breached count.
- **Forward event stress test**: FOMC meetings (from economic_calendar.py, regular vs SEP/dot plot) and earnings within DTE window. 1σ/2σ/3σ gap scenarios in both directions. Shows P&L and whether each scenario survives the stop loss.

#### Earnings Intelligence
- Per-ticker earnings date from yfinance (parallel fetch in thread pool)
- Expected move from daily 1σ (IV × √(1/252))
- Strikes-inside-expected-move warning
- Exclude earnings toggle

#### Multi-Expiration Comparison
- Up to 3 alternative expirations with credit, $/day, max risk, POP
- Best value ($/day) and highest absolute credit callout

#### Liquidity Scoring
- Per-leg OI, volume, bid-ask width
- Composite grade A-F with specific thresholds
- Slippage estimate (avg BA × 2)
- Wing width optimization warning (flags <1.5% of underlying)

#### AI Assessment (Grok 4)
- Analyzes all displayed setups (up to 12) with live X/Twitter search
- Per-setup: grade A-F, thesis, key risk, IV view, events, verdict (SELL/WAIT/SKIP)
- **Portfolio recommendation**: best 3-5 diversified picks
- **Correlation warning**: flags concentrated sector exposure
- **Macro environment**: VIX regime, Fed, geopolitical read
- Full quantitative context in prompt: IVR bands, VRP framework, backtest results, Greeks, Kelly, slippage

#### Layout
- Tabbed detail cards (up to 12, one at a time)
- Consolidated warning banner (IVR/liquidity/wings/earnings in one line)
- Two-column card layout (metrics left, charts right)
- Sub-tabs per setup: Management (rules + backtest + stress test), Compare Expirations, Greeks
- Portfolio summary bar (total contracts/credit/risk/earnings/liquidity)
- Sort by 7 fields, filter by POP/liquidity/IVR/VRP/earnings/EV
- Collapsed full results table at bottom
- Contextual explainers throughout

#### Performance
- Batch snapshot for spot prices (1 API call for all tickers)
- Price history + earnings fetched in parallel thread pool (10 workers)
- Chain scanning sequential on main thread (session_state safety)
- True IVR cached in session_state after first fetch
- ATM IV saved to metrics_store for progressive cache building

---

## 2026-03-30/31 — Major Options Infrastructure Overhaul + 3 New Pages

### New Pages
- **48_Vol_Landscape** — Cross-asset volatility surface analysis across 20 ETFs. 5 tabs: Vol Landscape heatmaps (smile + term structure), Market Environment (IV/HV ranking, skew, VRP k-means clustering, implied correlation, sector vs macro comparison), Metrics Table (sortable with change columns), Regime Signals & Alerts (divergence detection across 9 correlated pairs), AI Market Vol Briefing (Gemini 3.1 Pro).
- **49_Higher_Greeks** — 2nd and 3rd order Greeks analysis. 8 tabs: Overview (Greek family tree + calculator), Vanna Profile, Charm & Time Risk (overnight delta drift + hedge recommendation), Gamma Risk Map (speed + zomma heatmap), Vega Convexity (volga/veta + vol shock simulator), Vanna-Volga Pricing (smile premium decomposition + mispricing detection), Portfolio Higher Greeks, AI Greek Analyst.
- **07_Options_Flow** re-enabled — was disabled, now active with 4 tabs for unusual activity, P/C analysis, GEX, block detection.

### New Shared Modules
- **src/cross_asset_vol.py** — SCAN_UNIVERSE (20 ETFs), parallel data loading, cross-asset metrics computation, smile interpolation, implied correlation, divergence detection, metric change tracking.
- **src/options_history.py** — Historical IV from Polygon Options Starter. get_historical_iv(), get_iv_percentile() (proper ranking vs historical IV, not HV proxy), get_skew_trend(), get_iv_summary().
- **src/ai_validation.py** — ACCURACY_CHECK (full 5-point validation for all AI prompts), ACCURACY_CHECK_LIGHT, VOL_SURFACE_EXPERT_CONTEXT (institutional vol analysis framework), HIGHER_GREEKS_EXPERT_CONTEXT (dealer positioning, vanna flows, 0DTE dynamics, gamma scalping optimization).

### Higher-Order Greeks (src/options_models.py)
- **bs_higher_greeks()** — Closed-form BS formulas for 8 higher-order Greeks: vanna, volga, charm, veta, speed, zomma, color, ultima.
- **bs_all_greeks()** — All 12 Greeks in one pass (first + second + third order). For chain-wide batch computation.
- **vanna_volga_decomposition()** — Decomposes option price into BS base + vanna cost + volga cost (smile premium).

### Stock Analysis (Page 03) — Complete Overhaul
- Parallel AI model calls (ThreadPoolExecutor) — ~3x faster
- Candlestick chart with volume bars replacing line chart
- 3M/1Y/5Y timeframe toggle
- Earnings data + IV/options context injected into AI prompt
- Confidence-weighted model blending (not equal-weight)
- Peer comparison table
- Sentiment gauge + StockTwits posts feed
- Per-model radar overlay on scorecard
- Probability-weighted price target distribution curve
- Per-model retry buttons for failed AI calls
- Download report as markdown
- Bottom half reorganized into 5 tabs (Sentiment, Model Comparison, EDGAR, Financials, Peers)
- Wall Street vs AI target labels clearly distinguished
- AI analysis expandable per-dimension (not wall of text)
- Signal engine bug fix: confidence/100 → confidence/10

### Vol Surface (Page 43) — Major Expansion
- 3D Surface: Higher-Order Greeks Snapshot (vanna/charm/speed/zomma) with cross-page link to page 49
- Surface Animation: rebuilt with Polygon Options Starter historical data (5-30 days configurable), heatmap/3D toggle, day-over-day comparison with diff heatmap, AI Surface Evolution Analyst
- AI Surface Narrator on 3D tab with institutional analysis framework
- Gemini Trade Ideas: position sizing, refine follow-up, P&L payoff diagram, CSV download, data freshness indicator, ticker change cleanup, cost estimate, view AI context expander
- Surface data shared with Higher Greeks page via session state
- 0-OI contract filtering on all delta/dislocation computations
- Surface metric accounts for call/put stitching at ATM (no gap)
- Absolute delta for Delta surface view (no sign cliff)

### Polygon Options Starter Integration
- **5 new fields extracted** from v3/snapshot: rho, day_open, day_high, day_low, trade_count
- **fetch_options_trades()** function built for v3/trades (dormant — requires Options Advanced tier)
- **fetch_options_oi_history()** wired into Options Analysis (was dead code)
- **Historical IV surfaces** from daily OHLCV via implied_vol solver
- **Proper IV percentile** on Stock Analysis page (ranks vs historical IV, not HV proxy)
- Rho displayed on Portfolio Greeks (5th Greek)
- Trade count displayed on Vol Surface metrics

### AI Accuracy System
- All 16 AI call sites across 7 files now include accuracy validation prompts
- Full ACCURACY_CHECK (numerical accuracy, internal consistency, stale data, hallucination guard, final pass) on high-stakes calls
- ACCURACY_CHECK_LIGHT on lightweight calls (conflict JSON, chat)
- Domain-specific inline checks on FOMC, scenario analysis, conflict analysis
- Chatbot anti-hallucination instruction

### Gemini Model Upgrade
- All Gemini Pro calls upgraded from gemini-2.5-pro to gemini-3.1-pro-preview across: Vol Surface, Fed Macro, Worker, Stock Analysis
- Display labels updated from "Gemini 2.5 Pro" to "Gemini 3.1 Pro"

### Cross-Page Data Sharing
- Vol Surface ↔ Higher Greeks: shared chain data via session state (instant load if same ticker)
- Vol Landscape ↔ Market Expectations: reuses page 46 ticker data if available
- Cross-page navigation links between Vol Surface and Higher Greeks

### Performance Fixes
- Stock Analysis: fetch_stock_data parallelized (7 API calls concurrent), prompt builder parallelized (5 enrichment fetches concurrent), removed hist_3m (unused API call), single fun_loader instead of nested two
- Auto-load on ticker change removed from 7 pages (caused tab resets)
- Vol Landscape: data freshness indicator, refresh button, earnings cached in session state

### UI/UX Polish
- Global max-width: 1100px on main content (widescreen constraint)
- Stock Analysis: styled card system (header, scorecard, fundamentals, price targets, technical metrics, risks/catalysts)
- Vol Surface: styled metric cards, 3D tab explainer, surface reading cards with actionable analysis
- Calendar Spreads: 5-day historical term structure context, AI markdown rendering fix, stale AI cleared on ticker change, missing get_secret import fix
- Options Analysis: OI premium change table (5-day), rho + trade_count in Greeks heatmap, contract price history chart
- Portfolio Greeks: rho as 5th aggregate Greek metric

### Bug Fixes (30+)
- rec_scores dict max/min on keys → values
- sig_dir "strong" matching "Strong Sell" as bullish
- Price target distribution division by zero guards
- Insider data None guard
- Retry button cache bypass
- Dislocation baseline: per-expiration ATM IV (not flat HV)
- Gamma scalp ATM lookup: reset_index + idxmin (not argsort)
- Earnings fetch: earningsTimestampStart (not nonexistent field)
- Duplicate "GD" in energy ticker set
- Split HTML across st.markdown calls (regime banner)
- Butterfly/Impl_Move falsy-when-zero in AI context
- VRP scatter annotation positioning
- Vol Landscape change tracking: promote-on-scan-only (not every render)
- Stock Analysis current_iv passed as price*0.25 (wrong)
- cross_asset_vol IndentationError after edit
- Calendar Spreads missing get_secret import
- RL Trading SPY relative strength: zeros → actual computation
- Options ticker strike float precision: int() → round()
- OI history showing price as OI (corrected to premium change)
- Options Flow ticker variable scope (ticker → ticker_display)

---

## 2026-03-29 — Platform Trim: 47 → 30 Active Pages

### Sector Consolidation (11 pages → 1 dynamic page)
- New `pages/24_Sector_Analysis.py` with dropdown selector for all 11 SPDR sectors (XLE through XLRE)
- All sector configs (companies, guidance snapshots, subsectors, macro overlays) consolidated into single `SECTORS` dict
- Original 11 individual sector files (25-34) preserved but removed from nav
- `src/auth.py` free tier page list updated

### Power Page Merge (2 pages → 1, 7 tabs)
- Consolidated `23_Power_Analytics.py` (4 tabs) + `40_Power_Strategies.py` (10 tabs) into single 7-tab page:
  1. Duck Curve (from 23) — net load profile, ramp analysis, over-gen risk, storage arb, forecast vs actual, multi-ISO comparison
  2. Spark Spread (from 23) — VOM-adjusted margins, hourly profitability, System Lambda
  3. Stack Analysis (from 23) — merit order dispatch, fuel mix, inframarginal rent
  4. Peak/Off-Peak (from 40) — on-peak vs off-peak spread, calendar arb
  5. RT vs DAM (from 40) — real-time vs day-ahead convergence
  6. Similar Day Forecast (from 40) — weather-matched analog, bootstrap CI, MAPE tracker
  7. Strategy Backtest (from 40) — de Prado walk-forward, sequential bootstrap, DSR
- Dropped: Heat Rate (subsumed by Spark Spread), Live Charts, Renewable Curtailment, Congestion, Meta-Analysis

### Pages Disabled (7) via `DISABLED_PAGES` in `src/layout.py`
- `05_Historical_Analysis` — redundant with Stock Analysis
- `07_Options_Flow` — covered by Options Analysis
- `09_ML_Stock_Predictor` — overlaps with RL Trading + Stock Analysis AI
- `10_Tech_Screener` — Signal Scanner is far superior
- `12_Monte_Carlo` — lightweight niche tool
- `13_Power_Risk_VaR` — very basic VaR
- `40_Power_Strategies` — merged into Power Analytics

### Bug Fixes
- Fixed spark spread fuel cost calculation: removed erroneous `/10` divisor on rolling marginal heat rate (was making fuel costs 10x too low)
- Fixed operator precedence in Similar Day Forecast accuracy tracker (`not x if y else z` → `(not x) if y else z`)
- Removed dead variable `gas_price_float`

### Summary Page Updates
- Replaced 3 feature cards that linked to disabled pages (ML Predictor → Iran Conflict, Options Flow → Fed & Macro, Monte Carlo → Track Record)

### Production Dependency Fixes
- Added `yfinance>=0.2.36` to `requirements.txt` (used in 30+ files, missing from Docker build)
- Added `pdfplumber>=0.10.0` to `requirements.txt` (Congressional trades PDF parsing)
- Added `toml>=0.10.0` to `requirements.txt`
- Added `build-essential` to Dockerfile `apt-get` for C extension compilation
- GitHub Actions worker now installs from `requirements.txt` instead of hardcoded list

---

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
