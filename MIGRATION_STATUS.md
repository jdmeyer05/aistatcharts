# Streamlit → Next.js Migration — Status & Handoff

**Last updated:** 2026-04-17 (session 3)
**Session that left this note:** Sector Analysis full port shipped — all 11
SPDR sectors, 8 tabs, dynamic page via ETF selector. New backend
`api/routes/sectors.py` (~850 LOC including static configs). Frontend
`frontend/app/sector-analysis/page.tsx` rewritten (~2100 LOC). Added types +
fetchers for 7 new endpoints in `frontend/lib/api.ts`. Passed `npx tsc
--noEmit` + `npx next build`. All 6 POST endpoints smoke-tested live on
`:8001` with real data (financials, valuation, capex, guidance, prices,
market). Previous session shipped the 8 migration tasks plus Phase 2 auth.

This document is the entry point for the next session. Read it first.

## Overall Goal

Replace the Streamlit app with the Next.js app (`frontend/`) as the live site.
The Next.js side was behind on ~8 pages — each shipped as a thin stub when
the corresponding Streamlit page was substantial. The user wants "as close to
100% parity as we can get," page by page, biggest gaps first.

## Completed (do not redo)

| # | Page | Streamlit LOC | Next.js now | Commits |
|---|---|---|---|---|
| 1 | ERCOT Capacity | 653 | 6 tabs, full port | see history |
| 2 | Economic Calendar | 1,412 | 9 tabs, full port | see history |
| 3 | Signal Scanner | 1,577 | 8 tabs, full port | see history |
| 4 | Meta Analysis | 3,327 | 9 tabs, full port (~1,450 LOC) | uncommitted |
| 5 | Scenario Analysis | 2,331 | 8 tabs, full port (~1,200 LOC) | uncommitted |
| 6 | Quant Lab | 1,869 | 8 tabs, full port (~1,050 LOC) | uncommitted |
| 7 | Fed Macro Drivers | 1,513 | 8 tabs, full port (~1,250 LOC) | uncommitted |
| 8 | Calendar Spread polish | 2,585 | 525 → 926 LOC, gaps closed | uncommitted |
| 9 | Sector Analysis (full port) | 2,337 (24+sector_analysis) | 154 → ~2,100 LOC, 8 tabs, 11 ETFs | uncommitted |
| 10 | Smart Money (full port) | 813 | 160 → ~1,250 LOC, 6 tabs | uncommitted |
| 11 | Correlation parity | 868 | 272 → ~970 LOC, 6 tabs (added Clustering, Breakdown Alerts, PCA) | uncommitted |
| 12 | Factors parity | 718 | 275 → ~570 LOC, 5 tabs (expanded Timing, added Risk Decomposition) | uncommitted |
| 13 | Portfolio-optimizer parity | 1,425 | 288 → ~890 LOC, 5 tabs (added Walk-Forward + Black-Litterman) | uncommitted |
| 14 | Stock-analysis parity | 1,835 | 695 → ~760 LOC, 6 tabs (added Peer Comparison) | uncommitted |
| 15 | Track-record parity | 865 | 227 → ~390 LOC, 5 tabs (added Signal Engine) | uncommitted |

All four pass `npx tsc --noEmit` and `npx next build` cleanly. Each backend
endpoint was tested live with curl before shipping.

## Not yet done — priority order

Go in this order unless the user overrides. Sized by remaining gap:

No pending migration tasks — all 8 pages shipped. Residual gaps are documented
in the Notes section below.

---

## Sector Analysis full port (shipped 2026-04-17)

All 8 tabs ported. Backend endpoints live at `api/routes/sectors.py`:
- `GET /api/sectors/configs` — all 11 sector configs (Energy/XLE, Financials/XLF,
  Tech/XLK, Healthcare/XLV, Industrials/XLI, Comms/XLC, Cons Disc/XLY, Cons
  Staples/XLP, Utilities/XLU, Materials/XLB, Real Estate/XLRE). Returns
  companies dict, subsectors grouping, guidance_snapshot (hardcoded), macro
  overlay, factor proxies, CFTC COT commodities. Static data.
- `POST /api/sectors/overview` body `{etf}` — financials + analyst forecasts +
  revenue history + margin history + cashflow, parallelized with
  ThreadPoolExecutor.
- `POST /api/sectors/capex` — latest CapEx + quarterly-converted history
  (Python port of the Streamlit 10-Q cumulative diff + 10-K → Q4 logic).
- `POST /api/sectors/valuation` — valuation ratios + momentum (1M/3M/6M/12M).
- `POST /api/sectors/alpha` — EPS revisions + insider activity.
- `POST /api/sectors/prices` — 2Y daily close for sector tickers + SPY +
  factor proxies (shared by Risk + Pairs tabs).
- `POST /api/sectors/guidance` — live analyst estimates (yfinance) + earnings
  surprise history.
- `POST /api/sectors/market` — FRED macro series + CFTC COT positioning with
  secondary FRED price series for overlay.

Frontend `frontend/app/sector-analysis/page.tsx` uses `useQueries` to fire all
7 endpoints in parallel, gated by a Load button. Tab list matches Streamlit
exactly. Sector switch clears loaded state so the user has to re-click Load.

**Client-side math (all in TS, no backend work):**
- QoQ/YoY revenue growth, revenue volatility (CV), operating leverage (median
  ΔOI/ΔRev), earnings quality (OpCF/NI), composite scorecard ranking
- Max drawdown, VaR (hist + parametric), annualized vol, Sharpe/Sortino
- Sector-vs-SPY equal-weighted cumulative performance, sub-sector decomposition
- Factor regression via OLS (Gauss-Jordan solver in `solveLinear`)
- Correlation matrix, rolling correlations (21D + 63D), spread z-score
- COT positioning percentile, 4W MA, WoW changes, spec/comm divergence

**Deviations from Streamlit:**
- Skipped the full N×N pairs scatter matrix (10×10 subplots = slow); kept
  correlation heatmap + pair deep-dive with normalized prices, spread z-score,
  rolling correlation, and return distribution.
- Guidance data snapshots (11 sectors × 10 companies = ~110 rows of hand-curated
  price targets, ratings, outlook blurbs) are duplicated in
  `api/routes/sectors.py`. They also live in `pages/24_Sector_Analysis.py` and
  (for Energy) `src/edgar.py`. If snapshots get updated, need to update both
  places — consider extracting to `src/sector_configs.py` as a future refactor.

**Uvicorn restart needed:** the user's uvicorn on `:8000` was started before
`api/routes/sectors.py` was added, so the new routes won't resolve until they
restart the server. Smoke-tested on `:8001` before shipping.

Note on per-sector pages (`pages/25_Financials_Sector.py` through
`pages/34_RealEstate_Sector.py`): these thin wrappers still exist on the
Streamlit side and reuse `src/sector_analysis.py::render_sector_page`. The
Next.js side replaces all 11 with a single dynamic page + sector picker. No
Next.js routes needed per sector.

## Original open port request (now fulfilled)

**User request (2026-04-17):** the current Next.js `/sector-analysis` page
(154 LOC, 3 tabs: Performance, Relative Strength, Correlation) is roughly
10-15% of the Streamlit version. User wants a **full port** to match.

**Streamlit source to mirror:**
- `pages/24_Sector_Analysis.py` (387 LOC) — hub with 11 sector configs
  (Energy, Financials, Technology, Healthcare, Industrials, Consumer Disc,
  Consumer Staples, Utilities, Materials, Real Estate, Comms). Each
  `SectorConfig` carries: companies dict (`ticker → name`), `subsectors`
  groupings, `guidance_snapshot` (pre-loaded analyst data: rev/EPS
  estimates, price targets, ratings, fwd P/E, outlook blurbs),
  `macro_overlay` (FRED series + label, e.g. WTI for Energy, Fed Funds for
  Financials), `factor_proxies` (list of ETFs for regression), and
  `cot_commodities` (CFTC COT commodity keys for sectors where it applies).
- `pages/25_Financials_Sector.py` through `34_RealEstate_Sector.py` —
  thin wrappers that call `render_sector_page(SECTORS[name])`.
- `src/sector_analysis.py` (1,950 LOC) — the real work. 8 tabs:
  1. **Overview & Revenue** — revenue ranking, net margin bar, ROE bar,
     quarterly revenue trend (abs or indexed Q1 2024=100) with analyst
     estimate projections as starred markers, QoQ/YoY growth bars,
     revenue volatility, margin trend, operating leverage, earnings
     quality (OpCF/NI), composite ranking scorecard, financial ratios
     table.
  2. **CapEx Analysis** — latest-quarter CapEx, capital intensity
     (CapEx/Revenue), CapEx trend (abs or indexed) with guidance projection
     markers, YoY CapEx change, stacked sector CapEx, per-filing detail
     table with form type (10-Q vs 10-K) and cumulative-to-quarterly
     conversion.
  3. **Valuation & Returns** — valuation summary (P/E, P/S, P/B, EV/EBITDA)
     via `fetch_valuation_data`.
  4. **Alpha Signals** — relative value map (valuation × momentum scatter),
     price momentum bars, analyst estimate revision tracker
     (`fetch_eps_revisions`), insider activity summary
     (`fetch_insider_summary`).
  5. **Risk Analytics** — max drawdown, VaR, risk-adjusted returns,
     sector vs SPY, sub-sector decomposition, factor exposure regression
     against `cfg.factor_proxies`.
  6. **Guidance & Estimates** — earnings surprise heatmap
     (`fetch_earnings_surprises`), live analyst estimates table, the
     static guidance_snapshot (price targets, ratings, outlook blurbs)
     rendered as per-company cards or table.
  7. **Market & Positioning** — revenue vs macro overlay line chart
     (FRED series from `cfg.macro_overlay`), CFTC COT positioning chart
     for sectors with `cot_commodities` set.
  8. **Pairs & Correlation** — cross-company correlation matrix +
     pair-trading analysis.

**Backend reuse (already in `src/`):**
- `src/edgar.py`: `fetch_sector_financials`, `fetch_sector_analyst_estimates`,
  `fetch_sector_revenue_history`, `fetch_sector_capex`,
  `fetch_sector_capex_history`, `fetch_sector_margin_history`,
  `fetch_sector_cashflow`
- `src/market_data.py`: `fetch_energy_valuation_data` (used for all sectors,
  aliased `fetch_valuation_data`), `fetch_energy_earnings_surprises`
  (aliased `fetch_earnings_surprises`), `fetch_energy_price_history`
  (aliased `fetch_price_history`), `fetch_fred_series`, `fetch_cftc_cot`,
  `fetch_momentum_data`, `fetch_eps_revisions`, `fetch_insider_summary`
- The guidance snapshots are static Python dicts embedded in
  `pages/24_Sector_Analysis.py` (lines 11-400ish). Next.js will need these
  ported either (a) inline in a TS const, or (b) served by a new
  `/api/sectors/config` endpoint.

**Recommended plan for new session:**

1. **Build `api/routes/sectors.py`** with:
   - `GET /api/sectors/configs` — returns all 11 sector configs (companies,
     subsectors, guidance snapshot, macro overlay, factor proxies, COT
     commodities). Static data.
   - `POST /api/sectors/{etf}/overview` — financials, revenue history,
     analyst estimates, margin history, cashflow (combined).
   - `POST /api/sectors/{etf}/capex` — capex history with 10-Q/10-K
     quarterly conversion baked in (port the Python logic around
     `sector_analysis.py` line 580-650).
   - `POST /api/sectors/{etf}/valuation` — price history + valuation data
     + momentum data.
   - `POST /api/sectors/{etf}/guidance` — earnings surprises + live
     estimates + insider summary + EPS revisions.
   - `POST /api/sectors/{etf}/market` — FRED series for overlay + COT if
     applicable + revenue history for overlay.

2. **Rewrite `frontend/app/sector-analysis/page.tsx`** with 8 tabs matching
   the Streamlit layout exactly. Guidance snapshots can be served from
   the config endpoint and rendered as a table.

3. **Follow the migration recipe** in the top of this doc — reuse
   chart-theme helpers (especially `heatmapTrace`, `heatmapHeight`,
   `CHART_HEIGHT`), the `Metric` component, standard `space-y-4` patterns,
   and the 307-redirect-safe `apiFetch` auth header.

4. **Per-sector pages (`25_Financials` through `34_RealEstate`)** don't
   need their own routes in Next.js — a single dynamic `/sector-analysis`
   page with a sector picker (already there) handles all 11 via the
   chosen config. Streamlit's one-per-file pattern was a quirk of the
   multi-page framework, not a real requirement.

5. **Scope estimate:** ~600-800 LOC backend + ~1,500-1,800 LOC frontend.
   Comparable to the Meta Analysis port (#4).

**Verification hooks:**
- `npx tsc --noEmit`, `npx next build`, curl each new endpoint with
  `{tickers: [...]}` / `{sector: "XLE"}` payloads.
- Live compare to Streamlit on `localhost:8501` → 24 Sector Analysis
  → pick same ETF → diff tab-by-tab.

Note on Meta Analysis (now shipped): the Forecasts tab (tab 3) currently shows
an informational placeholder — the forward-estimates workflow (analyst targets
+ EPS revisions + valuation + macro overlay) is still Streamlit-only. If you
want it in the Next.js build, add a `/api/meta/forecasts` POST that wraps
`_fetch_forecasts`, `_fetch_macro_context`, and `_build_forecast_returns` from
`pages/41_Meta_Analysis.py:489-635`, then feed the components into the tab.

Tab 9 also omits two Streamlit-only extras that require long-running grid
work: (a) cross-group correlation heatmap of best-method OOS returns, and
(b) the two-layer hierarchical allocation flow with Fama-French factor
attribution. The universe grid itself, top-15 combos, best-method-per-universe,
and consistency bar are all ported.

Notes on Scenario Analysis (now shipped):

- **Scaling bug fixed**. `src/portfolio_models.py::estimate_regime_returns` has
  a legacy double-scaling bug: it multiplies by `horizon_days` on top of the
  already horizon-scaled input, producing per-regime returns ~252× too large.
  The new `/api/scenario/portfolio-impact` endpoint sidesteps that by computing
  the point estimate inline (`sum(beta × scaled_move) × 100`). If the
  Streamlit page gets revisited, fix the helper by either passing
  `horizon_days=1` or removing the internal `* horizon_days` multiplication.
- **Grok AI regime analysis is read-only**. `/api/scenario/grok-latest` surfaces
  the most recent cached result from `src/grok_regime_history.json`. A fresh
  Grok call is still Streamlit-only (runs hourly inside `pages/02_*.py`). To
  port, add a `POST /api/scenario/grok-refresh` that wraps `_call_grok_api`
  from the Streamlit page — gate with `require_admin` (cost: ~$0.03/call).
- **StockTwits + Polymarket**: only feed into the Grok prompt, not the UI.
  Nothing to port unless Grok refresh is added.
- **Fed & Macro Drivers tab** shows only the dual-mandate scorecard (5 metrics)
  plus a link to the dedicated `/fed-macro` page, mirroring the Streamlit flow.
- **Live API restart needed**: the user's uvicorn on `:8000` was started before
  `api/routes/scenario.py` was added, so the scenario routes won't resolve
  until they restart the server. The new routes were smoke-tested on `:8001`.

Notes on Quant Lab (now shipped):

- **Server endpoints** (`api/routes/quant_lab.py`): `POST /api/quant-lab/analyze`
  runs the heavy Python lifting (ADF scan for fractional differencing, SADF
  bubble detection, Chow breakpoint F-stats, Random-Forest MDI+MDA feature
  importance). `POST /api/quant-lab/hrp` runs static + walk-forward
  hierarchical risk parity with weight evolution.
- **SHAP skipped**. The Streamlit tab 5 computes SHAP dependency plots; not
  worth the extra backend complexity and SHAP Python dependency. Users still
  see normalized MDI + MDA + OOS accuracy, which is the core signal.
- **Transfer entropy skipped**. Streamlit tab 8 includes cross-asset transfer
  entropy (requires fetching a second ticker). Ported inline entropy
  (Shannon, plug-in, Lempel-Ziv, conditional + transition matrix + timeframe
  comparison) — the bits that only need the primary ticker.
- **Client-side math**: CUSUM filter, triple-barrier labeling + meta-label
  sizing + equity curve, ATR, sample-uniqueness bootstrap (standard +
  sequential), Amihud / VPIN / Kyle's Lambda / Corwin-Schultz, and all
  entropy computations run purely in the browser. The Streamlit backend
  handles ADF/SADF/Chow/feature importance/HRP only.
- **Fractional diff ADF scan can be sparse**. At high d, the truncated
  weights series is short enough that `_frac_diff` drops below 30 usable
  obs; those rows are filtered. Not a bug — consistent with AFML recipe.

Notes on Fed Macro Drivers (now shipped):

- **Server endpoints** (`api/routes/fed_macro.py`): `/sentiment` (StockTwits
  + Polymarket), `/balance-sheet` (Fed balance sheet + liquidity snapshot),
  `/cot` (CFTC managed money positioning), `/oecd-cli` (leading indicators),
  `/next-fomc` (ISO date of next meeting). FRED driver data reuses the
  existing `/api/market/fred-batch` endpoint — no new route needed.
- **Static data in the frontend**: 8 FOMC statements, March 2026 + December
  2025 dot plots with medians, SEP projections, reaction function table, and
  hawkish/dovish word lists are all hardcoded in
  `frontend/app/fed-macro/page.tsx`. These are snapshot data that don't
  change between Fed meetings; update them when the next SEP drops.
- **Word diff done client-side**. Implemented a small Myers-style LCS diff
  in TypeScript — no external diff library needed.
- **Gemini FOMC AI analysis skipped**. The Streamlit page offers a Gemini
  call that interprets FOMC language changes. To port: add
  `POST /api/fed-macro/fomc-diff-ai` wrapping `genai.Client` with
  `ACCURACY_CHECK` prompt + `ai_cache` key; gate with admin auth (cost:
  ~$0.03/call).
- **Balance sheet NaN/numpy sanitizer**. `src/macro_data.py::get_fed_liquidity_snapshot`
  can return numpy scalars and NaN values; `/balance-sheet` coerces these
  via an inline `_coerce` helper before JSON serialization. If new fields
  are added to the snapshot dict, make sure they still flow through.

Notes on Calendar Spread polish (now shipped):

- **Client-side additions** (no new backend needed): added missing
  Streamlit features to `frontend/app/calendar-spread/page.tsx` using
  client-side math — `bsGreeks` and `spreadGreeks` helpers compute
  delta/gamma/vega/theta directly.
- **Term Structure tab**: added Calendar IV Differential bar chart for
  adjacent expiration pairs and an `IvVsRvSection` component that fetches
  1-year price history on demand and ranks each expiration&apos;s ATM IV
  against the 20D realized-vol distribution.
- **P&amp;L Simulator tab**: added Daily Theta P&amp;L curve, Greeks
  Evolution (2×2 grid of delta/gamma/vega/theta over time), IV Scenario
  table, and Term Structure Tilt table.
- **Risk Analysis tab**: added Gamma Risk Near Front Expiry (delta/gamma
  over DTE), Pin Risk zone computation (extrinsic-value based), Tail Risk
  Scenarios table (−3σ to +3σ with leverage-effect β=−0.4 IV adjustment),
  and Reg-T margin requirement note.
- **Skipped from Streamlit** (low priority for the polish scope): earnings
  date overlay, diagonal roll analysis, early assignment checks (needs
  `yfinance.dividends`), watchlist alerts section, and the scanner
  score-validation panel. Port these if needed later — all require
  per-ticker yfinance calls that would add latency.

## Pattern That Works (use this)

Each migration followed the same recipe — use it again:

1. **Read the Streamlit source top-to-bottom** — catalog tabs and the data
   each one needs. Don't trust coverage numbers; count tabs and features.
2. **Identify backend endpoints needed.** Check if they already exist under
   `api/routes/*.py`. If not, add them. Reuse `src/*.py` helpers wherever
   possible — Streamlit and FastAPI both pull from the same `src/` modules.
3. **Add types + fetchers to `frontend/lib/api.ts`.** Put related types near
   each other in the file, not at the end.
4. **Rewrite the page.** Always `"use client"`. Use `useQuery` for reads,
   `useMutation` for user-triggered actions, `useQueries` for parallel
   per-item fetches. Import `Plot` dynamically with `ssr: false`.
5. **Verify:** `npx tsc --noEmit` → `npx next build` → `curl` the new
   endpoint(s) → manually smoke-test the page.

## Gotchas & Decisions From This Session

These apply to future migrations — save time.

**React / Next.js:**
- `frontend/AGENTS.md` warns "This is NOT the Next.js you know" — Next.js 16.2
  has breaking changes from training data. Before writing *new* Next.js APIs
  (router, caching, server components, route handlers), check
  `node_modules/next/dist/docs/`. None of the 3 migrations needed this — all
  used client components with TanStack Query.
- `useMemo` / `useState` / `useQuery` must come **before** any conditional
  `return` in a component. I hit this once in `EarningsTab` of the Economic
  Calendar page — Next.js build can silently accept it but React hooks rules
  are strict. Always put hooks at the top.
- `Metric` component (in `frontend/components/ui/metric.tsx`) takes `delta`,
  not `hint`. It also takes `deltaType: "gain" | "loss" | "neutral"`.

**Styling:**
- `getChartTheme(isDark)` + `getBaseLayout(t)` from `@/lib/chart-theme` — use
  for every Plotly chart so light/dark mode works.
- Fuel colors, factor colors, etc. — duplicate the colors inline rather than
  centralizing; palettes are small and each page's colors are contextual.

**Types:**
- Avoid `[key: string]: unknown` index signatures — they poison every access
  as `unknown`. This is the root cause of the big TS-error wave from the
  power-cut session.
- Supabase JSON responses use `date`/`value` as plain fields; use
  `parseFredBatch` pattern (in economic-calendar) to convert.

**Backend:**
- Use `get_current_user` on public endpoints, `require_admin` on anything
  that touches personal data (Robinhood, etc.). Never leave admin-sensitive
  endpoints without a gate.
- `fetchFredBatch` (already exists) handles most macro data. Don't add new
  FRED endpoints unless you need release-calendar metadata.
- yfinance is slow and flaky — always parallelize with ThreadPoolExecutor
  when fetching multiple tickers' `info`. See `signal-bundle` endpoint for a
  reference implementation.

**Math / Stats:**
- Annualized return: `Math.pow(terminal/initial, 252/(n_periods))`, where
  `n_periods = eq.length - 1`, not `eq.length`.
- Cross-sectional percentile rank: use `pctRank(values)` helper pattern from
  signal-scanner page (handles nulls cleanly).
- Correlation on already-percentile-ranked values ≈ Spearman on raw — saves
  a rank pass.
- Eigenvalues of symmetric matrices: use the `jacobiEigen()` helper in
  signal-scanner page. Pure TS, no numeric library needed.

**Bugs inherited from Streamlit that you should fix rather than mirror:**
- ERCOT `discover_months` used `timedelta(days=30 * months_ago)` which drifts
  across year boundaries → fixed with calendar-month decrement + dedupe.
- Signal Scanner mean-reversion score was inverted → already fixed in commit
  `9e57a4d`.
- Meta Analysis annualization off-by-one → already fixed in commit `9e57a4d`.

## Useful File Paths

- Streamlit pages: `pages/*.py`
- Next.js pages: `frontend/app/*/page.tsx`
- Frontend API client + types: `frontend/lib/api.ts`
- Frontend chart theme: `frontend/lib/chart-theme.ts`
- FastAPI routes: `api/routes/*.py`
- FastAPI deps (auth etc.): `api/deps.py`
- Shared Python modules (used by both Streamlit and FastAPI): `src/*.py`
- Project-wide behavioral guide: `CLAUDE.md` (includes behavioral guidelines
  at the bottom — follow them)

## Running Things Locally

```bash
# Three servers for development (keep them all running):
python -m streamlit run app.py                           # Streamlit :8501
python -m uvicorn api.main:app --port 8000 --host 0.0.0.0   # FastAPI :8000
cd frontend && npm run dev                                # Next.js :3000
```

For Robinhood access locally without the Phase 2 login UI, use:
```bash
LOCAL_DEV=true python -m uvicorn api.main:app --port 8000 --host 0.0.0.0
```

## Deployment Situation (relevant context)

- **Vercel** hosts only the Next.js frontend. The project is currently
  paused per user request (end of 2026-04-16 session) — no `NEXT_PUBLIC_API_URL`
  was ever set, so API calls default to `localhost:8000` and the deployed
  site doesn't work for real users. The user elected to keep Vercel paused
  until the full migration + auth + real FastAPI deployment are ready.
- **Cloud Run** (`aistatcharts-83677860965.us-east1.run.app`) serves only
  Streamlit on port 8080. FastAPI on port 8000 inside the same container is
  not reachable externally.
- **Redeploying the Cloud Run Streamlit service picks up backend changes
  only for Streamlit's internal use** — it doesn't help the Next.js site.

## Auth Kill-Switch (Phase 1, shipped)

`require_admin` dependency in `api/deps.py` now blocks these endpoints from
non-admin callers:
- `GET /api/positions/robinhood`
- `POST /api/market/holding-deep-dive`
- `POST /api/market/trade-architect`

Requires `SUPABASE_JWT_SECRET` + `ADMIN_EMAILS` in `.streamlit/secrets.toml`
or env. If neither is set, endpoints return **503** to everyone (fail closed).

**Phase 2 shipped (2026-04-17).** Full Supabase auth wired up end-to-end:

- `frontend/lib/supabase.ts` — `supabaseBrowser()` factory using `@supabase/ssr`.
- `frontend/proxy.ts` — Next 16 proxy (née middleware) that refreshes the
  session cookie, redirects unauthenticated traffic to `/login?next=…`, and
  bounces authenticated users away from `/login`.
- `frontend/app/login/page.tsx` — email+password sign in, with a magic-link
  fallback button. Wrapped in `<Suspense>` because `useSearchParams()` needs
  it in Next 16.
- `frontend/components/auth-gate.tsx` — rewritten as a thin session-context
  provider (`useSessionUser()`) instead of the old fake password gate. Routes
  are protected by the proxy now, not this component.
- `frontend/components/layout/app-chrome.tsx` — hides the Header+main
  wrapper on `/login` without restructuring 35 page directories into a
  route group.
- `frontend/components/layout/header.tsx` — added `UserMenu` dropdown (email
  + sign-out button) next to the theme toggle.
- `frontend/lib/api.ts::apiFetch` — now pulls the current Supabase access
  token from the browser client and attaches it as `Authorization: Bearer`
  on every backend call. Works with `api/deps.py::get_current_user` which
  already decodes the HS256 JWT.
- `.env.local` / `.env.production` — `NEXT_PUBLIC_SUPABASE_URL` +
  `NEXT_PUBLIC_SUPABASE_ANON_KEY` added. Legacy `NEXT_PUBLIC_SITE_PASSWORD`
  removed 2026-04-23.

**Required on the Supabase side**: user account for `jdmeyer05@gmail.com` must
exist in the Supabase Auth dashboard (create via Supabase UI or CLI), and the
backend still needs `SUPABASE_JWT_SECRET` + `ADMIN_EMAILS=jdmeyer05@gmail.com`
set in `.streamlit/secrets.toml` (or env) for admin-gated endpoints
(Robinhood, holding-deep-dive, trade-architect) to work.

**Deprecation note**: Next.js 16 renamed `middleware.ts` → `proxy.ts` and the
exported function from `middleware` → `proxy`. The old name still works in
16.x but emits a warning and is scheduled for removal. Using the new name.

### First-time Supabase user + end-to-end login test

The JWT plumbing is done but the Supabase project has no SMTP configured,
so magic-link email delivery fails with `500 unexpected_failure`. Two paths
to get the first user logged in:

**Option A — create the user manually** (fastest, no email needed):
1. Open https://supabase.com/dashboard/project/diyhmmpegkxlwwhmqkyo/auth/users
2. Click **Add user → Create new user**, enter `jdmeyer05@gmail.com` and a
   password, toggle **Auto-confirm user** ON so no email verification is
   required.
3. Hit `http://localhost:3000/login`, sign in with that email+password.

**Option B — configure SMTP then use magic links**:
1. Supabase dashboard → Project Settings → Auth → SMTP Settings. Either
   provide a custom SMTP (Resend, SendGrid, Postmark) or use the built-in
   service (rate-limited to 4/hr in dev).
2. After enabling, the "Email me a magic link instead" button on `/login`
   works — no password needed.

### Verifying the full round-trip

Once signed in locally:

```bash
# Restart uvicorn so it picks up the new routes added this session.
cd C:/Users/jdmey/aistatcharts
LOCAL_DEV=true python -m uvicorn api.main:app --port 8000 --host 0.0.0.0
```

Then in the browser hit `/meta-analysis` → run a backtest. In DevTools →
Network, confirm the request to `/api/meta/backtest` carries an
`Authorization: Bearer <jwt>` header. The token decodes on the server
via `api/deps.py::get_current_user` and `user` is set to the signed-in
email (check the uvicorn logs).

Admin-gated endpoints (`/api/positions/robinhood`, `/api/market/holding-deep-dive`,
`/api/market/trade-architect`) require `ADMIN_EMAILS=jdmeyer05@gmail.com`
in secrets. Without `LOCAL_DEV=true`, non-admin callers get 403.

## How to Resume

1. Read this file first (this is the handoff).
2. Run `TaskList` to see which task is currently `in_progress`.
3. If no in-progress task, pick up #4 (Meta Analysis) unless the user says
   otherwise.
4. Read the target Streamlit page fully before writing anything.
5. Follow the recipe under "Pattern That Works" above.
6. Don't commit between tasks — the user prefers to commit in logical
   batches (or at session end).
7. Run `tsc --noEmit` + `next build` + smoke-test the endpoint before
   declaring a task done.
