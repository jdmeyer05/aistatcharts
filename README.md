# AI Statcharts

Quantitative analysis platform with AI-powered conflict intelligence, macro scenario analysis, and multi-model consensus trading tools.

## Quick Start

```bash
cd C:\Users\jdmey\aistatcharts
python -m streamlit run app.py
```

Opens at **http://localhost:8501** (or next available port).

## Platform Overview

- **21 pages** of quantitative analysis tools
- **4 AI models**: Grok 3, Gemini 3 Pro, Claude Sonnet, GPT-5 (Platinum)
- **Landing page dashboard** — stat bar, relative performance chart, AI intelligence cards, watchlist
- **Top nav header** — logo, dropdown navigation, market status, DELAYED badge, tier/usage badge
- **Scrolling ticker tape** — market prices + StockTwits social feed (1M+ follower accounts only)
- **Fun loader** — animated spinner with funny quips, progress bar, milestone status, and countdown ETA
- **Session persistence** — cookie-based auth recovery, auto-reload on stale mobile connections
- **Tier-based analyst chat** — Gemini Flash (Free/Pro/Premium) or GPT-5 (Platinum)
- **Token system** — buy analysis tokens ($8/50, $25/200, $50/500) when daily limit is reached
- **Subscription tiers** — Free, Pro ($12), Premium ($29), Platinum ($79)
- **Supabase auth** — login, registration, "remember me", session timeout warning
- **Stripe integration** — subscription billing with lookup_key tier mapping
- **Mobile-optimized** — 44px touch targets, responsive breakpoints, pull-to-refresh, auto-reload

## Subscription Tiers

| | Free | Pro ($12/mo) | Premium ($29/mo) | Platinum ($79/mo) |
|---|---|---|---|---|
| **Pages** | 17 | All 21 | All 21 | All 21 |
| **AI Analyses/day** | 0 | 5 | 20 | 50 |
| **AI Models** | None | 3 (Grok, Gemini, Claude) | 3 (Grok, Gemini, Claude) | 4 (+GPT-5) |
| **RL Trading** | No | No | Yes | Yes |
| **Analyst Chat** | Gemini Flash (5/day) | Gemini Flash (unlimited) | Gemini Flash (unlimited) | GPT-5 (unlimited) |
| **Bonus Tokens** | Buy tokens for AI analyses beyond daily limit — never expire |

## Key Pages

| Page | Description |
|------|-------------|
| **Summary** | Landing dashboard: stat bar, market grid, relative performance chart, AI intelligence (regime + conflict + alerts), quick access cards, watchlist |
| **Scenario Analysis** | 6-tab macro engine: Grok AI regime analysis, FRED data, portfolio impact modeling, stress tests |
| **Stock Analysis** | 4-model AI consensus scorecard with blended scores, price targets, and radar chart |
| **RL Trading** | Dueling DQN ensemble with 31 features, walk-forward validation, Monte Carlo robustness |
| **Iran Conflict** | AI war analysis (4-model blend), trending tweets, conflict timeline, infrastructure table, GDELT/ACLED |
| **Fed & Macro Drivers** | 4-tab page: signal matrix, driver trend charts, FOMC dot plot, SEP, Polymarket, StockTwits sentiment |
| **Economic Calendar** | Hero section with today's events + countdown, week view, yield curve, inflation, labor, earnings, auctions |
| **+ 14 more** | Historical analysis, options (3 pages), ML predictor, screener, backtester, Monte Carlo, VaR, oil, natgas, ERCOT (2), futures |

## AI Models

4 models called in parallel:

| Model | Role | Key Capability |
|-------|------|----------------|
| **Grok 3** | Breaking news & viral tweets | Live X/Twitter search with velocity scoring, priority account monitoring, thread detection |
| **Gemini 3 Pro** | Military/strategic + energy/economic | Dual-role: escalation analysis + facility-level disruption math |
| **Claude Sonnet** | Diplomatic/probabilistic | Calibrated uncertainty, ceasefire probability, scenario trees |
| **GPT-5** *(Platinum only)* | Deep strategic synthesis | Challenges assumptions, second/third-order effects |

## UX Features

| Feature | Description |
|---------|-------------|
| **Fun loader** | Animated spinner with cycling quips, milestone status messages, progress bar, countdown ETA |
| **Session persistence** | Cookie-based auth with "remember me" (30-day or session), auto-recovery on mobile wake |
| **Stale data detection** | DELAYED badge when data >15min old, pull-to-refresh banner, auto-reload after 60s idle |
| **Auto-refresh** | Ticker strip refreshes every 5min via `st.fragment`, social feed updates every 10min |
| **Deep linking** | Active ticker follows across all pages, URL `?ticker=AAPL` support |
| **Recent pages** | Quick-access row showing last 5 visited pages |
| **Smart token usage** | AI cache-hit detection prevents burning tokens on duplicate analyses |
| **Error boundaries** | Per-section error handling on all pages — one section failing doesn't crash the page |
| **Loading states** | AI buttons show "Running..." and disable during execution to prevent double-clicks |
| **Social feed** | StockTwits posts from 1M+ follower accounts, profanity filtered, ticker-cleaned |
| **Watchlist** | User-configurable with price threshold alerts, integrated with ticker tape |
| **Footer** | Fixed footer on every page via JS injection into parent document |

## Data Sources

| Source | Type | Cache Strategy |
|--------|------|---------------|
| GDELT Bulk Events | Direct daily CSV download | Parquet cache, background refresh |
| GDELT API | Media intensity/tone | 2hr cache, retry with backoff |
| FRED | Economic indicators (24 series) | 1hr cache |
| EIA | Energy data (WTI, natgas) | 1hr cache |
| yfinance | Market prices | 2min cache, futures use 2d/1h fallback |
| Polymarket | Prediction markets (11 contracts) | 30min cache |
| StockTwits | Social feed (1M+ follower filter) | 10min cache, profanity/quality filter |
| ACLED | Armed conflict events | API + CSV upload fallback |
| Finnhub | Earnings calendar | 1hr cache |

## Tech Stack

- **Frontend:** Streamlit 1.55
- **Auth:** Supabase
- **Payments:** Stripe
- **Data viz:** Plotly
- **ML:** scikit-learn, scipy, PyTorch (RL)
- **AI:** OpenAI SDK, Anthropic SDK, xAI API, Google Gemini API
- **Python:** 3.14

## Project Structure

```
app.py                    Entry point (login + user agreement)
webhook_server.py         Stripe webhook handler (Flask, port 5000)
static/
  logo.png                Platform logo (512x512, transparent)
src/
  auth.py                 Auth, tiers, tokens, Stripe, session timeout, cookies
  layout.py               setup_page(), header, nav, ticker strip, social feed, fun_loader, footer
  styles.py               Global CSS, 5-layer background, responsive breakpoints, color system
  ticker_tape.py           Market data feed with staleness timestamps
  chatbot.py              Tier-based analyst chat (Gemini Flash or GPT-5)
  gdelt_events.py         GDELT bulk event download & processing
  data_engine.py          Market data (Massive API -> yfinance fallback)
  options_models.py       BS-Merton Jump Diffusion pricing
  eia_helpers.py          EIA API wrapper
  simulation.py           Stochastic price simulation
pages/
  01_Summary.py           Landing dashboard with stat bar, markets, relative perf, AI intel
  02_Scenario_Analysis.py Macro scenario engine (6 tabs)
  03_Stock_Analysis.py    AI stock analysis (4-model blend)
  04_RL_Trading.py        Reinforcement learning trading
  05-20                   Analysis tools, options, energy, macro
  21_Fed_Macro_Drivers.py Fed policy dashboard (4 tabs)
data/
  gdelt_events/           Cached GDELT daily event files (gitignored)
.streamlit/
  config.toml             Theme, toolbar, file watcher, static serving
  secrets.toml            API keys (gitignored)
```

## Running

```bash
# Streamlit app
python -m streamlit run app.py

# Webhook server (separate terminal)
python webhook_server.py
```

## Environment Setup

All keys in `.streamlit/secrets.toml` (gitignored):

```
SUPABASE_URL, SUPABASE_KEY     Auth & database
STRIPE_SECRET_KEY               Stripe billing (sk_live_ or sk_test_)
STRIPE_WEBHOOK_SECRET           Webhook signature verification
OPENAI_API_KEY                  GPT-5
GROK_API_KEY                    Grok 3 (xAI)
GEMINI_API_KEY                  Gemini 2.5 Flash + 3 Pro
ANTHROPIC_API_KEY               Claude Sonnet
FRED_API_KEY                    Economic data
EIA_API_KEY                     Energy data
MASSIVE_API_KEY                 Price data (Polygon)
FINNHUB_API_KEY                 Earnings calendar
FMP_API_KEY                     Financial data
ACLED_EMAIL, ACLED_PASSWORD     Armed conflict data (optional)
LOCAL_DEV = "true"              Skip auth locally
```

## Stripe Setup

Payment links in `src/auth.py` -> `STRIPE_LINKS`. Tier detection: price metadata `tier` -> lookup_key -> product name.

| Product | Price | Type | Metadata |
|---------|-------|------|----------|
| Pro | $12/mo | Recurring | `tier: pro` |
| Premium | $29/mo | Recurring | `tier: premium` |
| Platinum | $79/mo | Recurring | `tier: platinum` |
| Starter Tokens (50) | $8 | One-time | -- |
| Power Tokens (200) | $25 | One-time | -- |
| Elite Tokens (500) | $50 | One-time | -- |

**Webhook setup:** dashboard.stripe.com/webhooks -> add endpoint `/stripe/webhook` -> select events: `checkout.session.completed`, `customer.subscription.updated`, `customer.subscription.deleted`, `invoice.payment_failed` -> copy signing secret to `STRIPE_WEBHOOK_SECRET`.

**Supabase tables required:**
- `subscriptions` -- email, plan_type, status, stripe_customer_id, stripe_price_id, updated_at
- `user_tokens` -- email, balance, updated_at
- `payment_failures` -- email, invoice_id, failed_at, resolved
