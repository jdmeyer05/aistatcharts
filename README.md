# AI Statcharts

Quantitative analysis platform with AI-powered conflict intelligence, macro scenario analysis, and multi-model consensus trading tools.

## Quick Start

```bash
cd C:\Users\jdmey\aistatcharts
python -m streamlit run app.py
```

Opens at **http://localhost:8501** (or next available port).

## Platform Overview

- **20 pages** of quantitative analysis tools
- **3 AI models** (4 for Platinum): Grok 3, Gemini 3 Pro, Claude Sonnet (+GPT-5)
- **Top nav header** — brand bar with dropdown navigation, market status, tier/usage badge
- **Scrolling market strip** — S&P 500, WTI Crude, VIX, Gold, DXY, BTC, Fed rate, Iran war escalation score
- **Tier-based analyst chat** — Gemini Flash (Free/Pro/Premium) or GPT-5 (Platinum)
- **Token system** — buy analysis tokens ($8/50, $25/200, $50/500) when daily limit is reached
- **Subscription tiers** — Free, Pro ($12), Premium ($29), Platinum ($79)
- **Supabase auth** — login, registration, user agreement, tier management
- **Stripe integration** — subscription billing with lookup_key tier mapping
- **Mobile-friendly** — touch scroll protection, responsive breakpoints

## Subscription Tiers

| | Free | Pro ($12/mo) | Premium ($29/mo) | Platinum ($79/mo) |
|---|---|---|---|---|
| **Pages** | 17 | All 20 | All 20 | All 20 |
| **AI Analyses/day** | 0 | 5 | 20 | 50 |
| **AI Models** | None | 3 (Grok, Gemini, Claude) | 3 (Grok, Gemini, Claude) | 4 (+GPT-5) |
| **RL Trading** | No | No | Yes | Yes |
| **Analyst Chat** | Gemini Flash (5/day) | Gemini Flash (unlimited) | Gemini Flash (unlimited) | GPT-5 (unlimited) |
| **Bonus Tokens** | Buy tokens for AI analyses beyond daily limit — never expire |

## Key Pages

| Page | Description |
|------|-------------|
| **Scenario Analysis** | 7-tab macro engine: Grok AI regime analysis, FRED data, FOMC dot plots, Polymarket, portfolio impact modeling |
| **Stock Analysis** | 4-model AI consensus scorecard with blended scores, price targets, and radar chart |
| **RL Trading** | Dueling DQN ensemble with 31 features, walk-forward validation, Monte Carlo robustness |
| **Iran Conflict** | AI war analysis (4-model blend), conflict timeline, infrastructure map, GDELT bulk events, ACLED integration |
| **+ 16 more** | Historical analysis, options (3 pages), ML predictor, screener, backtester, Monte Carlo, VaR, oil, natgas, ERCOT (2), economic calendar, futures |

## AI Models

3 models called in parallel (4 for Platinum tier):

| Model | Role | Key Capability |
|-------|------|----------------|
| **Grok 3** | Breaking news & sentiment | Live X/Twitter search, infrastructure monitoring |
| **Gemini 3 Pro** | Military/strategic + energy/economic | Dual-role: escalation analysis + facility-level disruption math |
| **Claude Sonnet** | Diplomatic/probabilistic | Calibrated uncertainty, ceasefire probability, scenario trees |
| **GPT-5** *(Platinum only)* | Deep strategic synthesis | Challenges assumptions, second/third-order effects |

## Data Sources

| Source | Type | Rate Limit Strategy |
|--------|------|-------------------|
| GDELT Bulk Events | Direct daily CSV download | No limits — file download |
| GDELT API | Media intensity/tone | 2hr cache, retry with backoff |
| FRED | Economic indicators | 1hr cache |
| EIA | Energy data (WTI, natgas) | 1hr cache |
| yfinance | Market prices | 2-5min cache |
| Polymarket | Prediction markets | 30min cache |
| StockTwits | Retail sentiment | 30min cache (curl_cffi) |
| ACLED | Armed conflict events | API + CSV upload fallback |

## Tech Stack

- **Frontend:** Streamlit
- **Auth:** Supabase
- **Payments:** Stripe
- **Data viz:** Plotly
- **ML:** scikit-learn, scipy, PyTorch (RL)
- **AI:** OpenAI SDK, Anthropic SDK, xAI API, Google Gemini API
- **Python:** 3.14

## Project Structure

```
app.py                    Entry point (login + user agreement)
src/
  auth.py                 Supabase auth, tier system (Free/Pro/Premium/Platinum), Stripe mapping, page gating
  layout.py               setup_page(), header bar, nav dropdowns, market ticker strip
  styles.py               Global CSS, 5-layer background, responsive breakpoints, color system
  ticker_tape.py          Market data feed (^GSPC, CL=F, GC=F, ^VIX, etc.)
  chatbot.py              Tier-based analyst chat (Gemini Flash or GPT-5)
  gdelt_events.py         GDELT bulk event download & processing
  data_engine.py          Market data (Massive API -> yfinance fallback)
  options_models.py       BS-Merton Jump Diffusion pricing for missing data
  eia_helpers.py           EIA API wrapper
  simulation.py           Stochastic price simulation
pages/
  01-20                   All application pages
data/
  gdelt_events/           Cached GDELT daily event files (gitignored)
```

## Environment Setup

All API keys in `.streamlit/secrets.toml` (gitignored):

```
SUPABASE_URL, SUPABASE_KEY     Auth & database
STRIPE_SECRET_KEY               Stripe billing
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

All payment links are configured in `src/auth.py` → `STRIPE_LINKS`. Tier detection reads price metadata `tier` field, then lookup_key, then product name as fallback.

| Product | Price | Type | Metadata |
|---------|-------|------|----------|
| Pro | $12/mo | Recurring | `tier: pro` |
| Premium | $29/mo | Recurring | `tier: premium` |
| Platinum | $79/mo | Recurring | `tier: platinum` |
| Starter Tokens (50) | $8 | One-time | — |
| Power Tokens (200) | $25 | One-time | — |
| Elite Tokens (500) | $50 | One-time | — |

Customer portal enabled for subscription management. `STRIPE_SECRET_KEY` required in secrets.
