# AI Statcharts

Quantitative analysis platform with AI-powered conflict intelligence, macro scenario analysis, and multi-model consensus trading tools.

## Quick Start

```bash
cd C:\Users\jdmey\aistatcharts
python -m streamlit run app.py
```

Opens at **http://localhost:8501** (or next available port).

## Platform Overview

- **22 pages** of quantitative analysis tools
- **3 AI models**: Grok 4, Gemini 3.1 Pro, Claude Sonnet/Opus
- **Landing page dashboard** -- stat bar, relative performance chart, AI intelligence cards, watchlist
- **Top nav header** -- logo, dropdown navigation, market status, tier/usage badge, Settings popover
- **Scrolling ticker tape** -- Polygon market prices + Grok X/Twitter feed + Polymarket odds + StockTwits backfill
- **Fun loader** -- animated spinner with quips, progress bar, milestone status, countdown ETA
- **Session persistence** -- cookie-based auth recovery, auto-reload on stale mobile connections
- **Tier-based analyst chat** -- Gemini 2.5 Flash (all tiers) in inline expander
- **Token system** -- buy analysis tokens ($8/50, $25/200, $50/500) when daily limit is reached
- **Subscription tiers** -- Free, Pro ($12), Premium ($29), Platinum ($79)
- **Supabase auth** -- login, registration, "remember me", session timeout warning
- **Stripe integration** -- subscription billing with lookup_key tier mapping
- **Mobile-optimized** -- 44px touch targets, responsive breakpoints, pull-to-refresh, auto-reload
- **No sidebar** -- all controls inline via columns/expanders, sidebar fully hidden via config

## Subscription Tiers

| | Free | Pro ($12/mo) | Premium ($29/mo) | Platinum ($79/mo) |
|---|---|---|---|---|
| **Pages** | 17 | All 22 | All 22 | All 22 |
| **AI Analyses/day** | 0 | 5 | 20 | 50 |
| **AI Models** | None | 3 (Grok 4, Gemini 3.1 Pro, Claude Sonnet) | 3 | 3 + Claude Opus upgrade |
| **RL Trading** | No | No | Yes | Yes |
| **Analyst Chat** | Gemini Flash (5/day) | Gemini Flash (unlimited) | Gemini Flash (unlimited) | Gemini Flash (unlimited) |
| **Bonus Tokens** | Buy tokens for AI analyses beyond daily limit -- never expire |

## Key Pages

| Page | Description |
|------|-------------|
| **Summary** | Landing dashboard: stat bar, market grid, relative performance chart, AI intelligence cards, watchlist |
| **Scenario Analysis** | 6-tab macro engine: Grok AI regime analysis, FRED data, portfolio impact modeling, stress tests |
| **Stock Analysis** | 3-model AI consensus scorecard + SEC EDGAR insider scoring, 8-K events, XBRL financial ratios |
| **RL Trading** | Dueling DQN ensemble with 31 features, walk-forward validation, Monte Carlo robustness |
| **Iran Conflict** | 3-model AI war analysis, Grok live infrastructure monitoring, trending tweets, conflict timeline, GDELT/ACLED |
| **Fed & Macro Drivers** | 4-tab page: signal matrix, driver trend charts, FOMC dot plot, SEP, Polymarket |
| **Smart Money** | 13F institutional holdings, congressional trades, activist investors (13D), 8-K event search |
| **Economic Calendar** | Today's events + countdown, week view, yield curve, inflation, labor, earnings, auctions |
| **+ 13 more** | Historical analysis, options (3 pages), ML predictor, screener, backtester, Monte Carlo, VaR, oil, natgas, ERCOT (2), futures |

## AI Models

### Iran Conflict Analysis (3 models in parallel)

| Model | ID | Role |
|-------|-----|------|
| **Grok 4** | `grok-4.20-0309-reasoning` | Real-time visual OSINT: war maps, satellite imagery, X/Twitter breaking news, narrative shifts |
| **Gemini 3.1 Pro** | `gemini-3.1-pro-preview` | Quantitative engine: facility-by-facility supply model, oil price math, economic impact |
| **Claude Sonnet** | `claude-sonnet-4-6` | Bayesian reasoning: scenario trees, ceasefire decomposition, red-teaming, confidence intervals |
| **Claude Opus** *(Platinum)* | `claude-opus-4-6` | Upgraded Claude with deeper reasoning |

### Independent Grok Calls (auto-refresh, no button needed)

| Function | Model | Refresh | Purpose |
|----------|-------|---------|---------|
| Infrastructure Status | Grok 4 reasoning | 30 min | Facility status from 30+ verified X accounts |
| Live Tweets | Grok 4 fast | 10 min | Breaking news feed |
| Breaking News Brief | Grok 4 fast | 15 min | 6-hour summary fed to all models |
| Timeline Auto-Update | Grok 4 fast | 1 hr | New conflict events appended |
| Ticker Scroll Posts | Grok 4 fast | 10 min | Market intelligence for scroll bar |

### Data Enrichment (fed into AI models)

- Polymarket prediction odds (ceasefire, oil, escalation contracts)
- Oil futures term structure (backwardation/contango signal)
- LNG/natgas prices (TTF, Henry Hub)
- Brent, VIX, Gold, DXY live prices
- SEC EDGAR 8-K defense sector filings
- Source credibility scoring (tracks which X accounts are most accurate)
- Historical accuracy weights (models that predicted better get more influence)

## Data Sources

| Source | Type | Legal Status |
|--------|------|-------------|
| **Polygon (Massive API)** | Market prices, options, financials, insider txns | Paid (Stocks Starter) |
| **Finnhub** | Analyst recommendations | Free tier, commercial OK |
| **SEC EDGAR** | 13F holdings, insider txns, 8-K events, XBRL financials | Public domain |
| **GDELT** | Media intensity, tone, bulk conflict events | Open data |
| **ACLED** | Armed conflict events (11 ME countries) | Academic/commercial |
| **FRED** | 24 economic indicators | Public domain |
| **EIA** | Oil/gas prices and storage | Public domain |
| **Polymarket** | Prediction market odds | Public API |
| **StockTwits** | Social feed (1M+ follower filter) | Public API |

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
- **Market Data:** Polygon API (no yfinance dependency)
- **OSINT:** SEC EDGAR, GDELT, ACLED

## Project Structure

```
app.py                    Entry point (login + user agreement)
webhook_server.py         Stripe webhook handler (Flask, port 5000)
Dockerfile                Cloud Run deployment (4 CPU, 4GB recommended)
static/
  logo.png                Platform logo (base64-encoded into header)
src/
  auth.py                 Auth, tiers, tokens, Stripe, session timeout, cookies
  layout.py               setup_page(), header, nav, ticker strip, social feed, cache warming
  styles.py               Global CSS, 5-layer background, responsive breakpoints, Plotly defaults
  ticker_tape.py          Polygon batch snapshots for market ticker
  chatbot.py              Tier-based analyst chat (Gemini 2.5 Flash, inline expander)
  edgar.py                SEC EDGAR: 13F, insider scoring, 8-K, XBRL ratios, 13D activist
  gdelt_events.py         GDELT bulk event download & processing
  data_engine.py          Polygon API (snapshots, history, intraday, financials, ticker details)
  options_models.py       BS-Merton Jump Diffusion pricing
  eia_helpers.py          EIA API wrapper
  simulation.py           Stochastic price simulation
  iran_conflict_history.json  AI analysis history (48 entries, auto-managed)
  iran_infra_state.json   Self-updating infrastructure baseline (Grok-verified)
  source_credibility.json Source reliability scores (auto-updated)
pages/
  01_Summary.py           Landing dashboard
  02_Scenario_Analysis.py Macro scenario engine (6 tabs)
  03_Stock_Analysis.py    AI stock analysis + EDGAR insider/8-K/XBRL
  04_RL_Trading.py        Reinforcement learning trading
  05-21                   Analysis tools, options, energy, macro, futures, Fed
  22_Smart_Money.py       13F holdings, congressional trades, activist investors, 8-K search
data/
  gdelt_events/           Cached GDELT daily event files (gitignored)
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

**Supabase tables:**
- `subscriptions` -- email, plan_type, status, stripe_customer_id, stripe_price_id
- `user_tokens` -- email, balance
- `payment_failures` -- email, invoice_id, failed_at, resolved
