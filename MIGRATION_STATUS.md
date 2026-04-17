# Streamlit → Next.js Migration — Status & Handoff

**Last updated:** 2026-04-16
**Session that left this note:** Completed 3 of 8 thin-page migrations plus an
auth kill-switch. See `CHANGELOG.md` entry dated 2026-04-16 for the full
shipped-work summary.

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

All three pass `npx tsc --noEmit` and `npx next build` cleanly. Each backend
endpoint was tested live with curl before shipping.

## Not yet done — priority order

Go in this order unless the user overrides. Sized by remaining gap:

| # | Page | Streamlit LOC | Current NJS | Coverage | Status |
|---|---|---|---|---|---|
| 4 | **Meta Analysis** (`pages/41_Meta_Analysis.py`) | 3,327 | 131 LOC (3 tabs) | **~33%** | Pending |
| 5 | **Scenario Analysis** (`pages/02_Scenario_Analysis.py`) — flagship | 2,331 | 176 LOC (4 tabs) | ~25% | Pending |
| 6 | **Quant Lab** (`pages/36_Quant_Lab.py`) | 1,869 | 120 LOC (4 tabs) | ~50% | Pending |
| 7 | **Fed Macro Drivers** (`pages/21_Fed_Macro_Drivers.py`) | 1,513 | 239 LOC (5 tabs) | ~30% | Pending |
| 8 | **Calendar Spread polish** (`pages/42_Calendar_Spreads.py`) | 2,585 | 525 LOC (8 tabs) | 85% | Small cleanup |

Note: the user said "biggest gaps first." Under that rule, Meta Analysis is
next (33% coverage, biggest LOC gap). If you want the flagship polished
first, Scenario Analysis has the highest user-visibility.

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

**Phase 2 pending:** frontend login UI + wire JWT into `apiFetch` + replace
the fake `auth-gate.tsx` (client-side password in a public `NEXT_PUBLIC_`
var — not real security). This is a separate project from the migration but
needed before the site can be made public.

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
