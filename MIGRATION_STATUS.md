# Streamlit → Next.js Migration — Status & Handoff

**Last updated:** 2026-04-16 (session 2)
**Session that left this note:** All 8 migration tasks now complete.
Completed Meta Analysis (#4), Scenario Analysis (#5 — flagship), Quant Lab
(#6), Fed Macro Drivers (#7), AND Calendar Spread polish (#8) in this
session. New backends landed under `api/routes/meta_analysis.py`,
`api/routes/scenario.py`, `api/routes/quant_lab.py`, and
`api/routes/fed_macro.py`. Previous session completed the first 3
thin-page migrations plus the auth kill-switch.

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

All four pass `npx tsc --noEmit` and `npx next build` cleanly. Each backend
endpoint was tested live with curl before shipping.

## Not yet done — priority order

Go in this order unless the user overrides. Sized by remaining gap:

No pending migration tasks — all 8 pages shipped. Residual gaps are documented
in the Notes section below.

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
  `NEXT_PUBLIC_SUPABASE_ANON_KEY` added. The `NEXT_PUBLIC_SITE_PASSWORD`
  legacy env is still present but unused; safe to delete after verification.

**Required on the Supabase side**: user account for `jdmeyer05@gmail.com` must
exist in the Supabase Auth dashboard (create via Supabase UI or CLI), and the
backend still needs `SUPABASE_JWT_SECRET` + `ADMIN_EMAILS=jdmeyer05@gmail.com`
set in `.streamlit/secrets.toml` (or env) for admin-gated endpoints
(Robinhood, holding-deep-dive, trade-architect) to work.

**Deprecation note**: Next.js 16 renamed `middleware.ts` → `proxy.ts` and the
exported function from `middleware` → `proxy`. The old name still works in
16.x but emits a warning and is scheduled for removal. Using the new name.

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
