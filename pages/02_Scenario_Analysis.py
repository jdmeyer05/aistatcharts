import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots
import requests
import os
import logging
import json
from datetime import datetime, timedelta
from src.data_engine import fetch_massive_data, format_massive_ticker
from src.chatbot import run_sidebar_chatbot
from src.auth import check_auth

logger = logging.getLogger(__name__)

from src.layout import setup_page, page_error_boundary, error_boundary, fun_loader
from src.styles import COLORS
setup_page("02_Scenario_Analysis")

st.title("🔮 Scenario Analysis Engine")
st.markdown("Stress test portfolios against historical shocks, custom what-if scenarios, bull/bear projections, and event-driven catalysts.")

# ─────────────────────────────────────────────
# CONTROLS
# ─────────────────────────────────────────────
_c1, _c2, _c3, _c4, _c5 = st.columns([3, 2, 2, 2, 1])
with _c1:
    raw_tickers = st.text_input("Tickers (comma separated)", "SPY,QQQ,TLT,USO,GLD")
with _c2:
    portfolio_value = st.number_input("Portfolio Value ($)", value=100_000, step=10_000)
with _c3:
    lookback = st.slider("Historical Lookback (Days)", 252, 2520, 756)
with _c4:
    horizon_label = st.selectbox("Scenario Horizon", ["3 Months", "6 Months", "12 Months"], index=2)
    horizon_map = {"3 Months": 63, "6 Months": 126, "12 Months": 252}
    horizon_days = horizon_map[horizon_label]
with _c5:
    st.markdown("<br>", unsafe_allow_html=True)
    _scenario_running = st.session_state.get("_scenario_running", False)
    run_btn = st.button(
        "Running..." if _scenario_running else "Run Analysis",
        type="primary", use_container_width=True,
        disabled=_scenario_running,
    )

# ─────────────────────────────────────────────
# FRED HELPER
# ─────────────────────────────────────────────
from src.api_keys import get_secret as _get_key

fred_key = _get_key("FRED_API_KEY")
grok_key = _get_key("GROK_API_KEY")

from src.market_data import fetch_fred_series as _fetch_fred_canonical

def fetch_fred_series(fred_key: str, series_id: str, limit: int = 60):
    """Wrapper for backward compat — delegates to src.market_data."""
    return _fetch_fred_canonical(series_id, periods=limit)

GROK_HISTORY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "src", "grok_regime_history.json")

# ─────────────────────────────────────────────
# STOCKTWITS SENTIMENT DATA
# ─────────────────────────────────────────────
STOCKTWITS_MACRO_SYMBOLS = ["SPY", "QQQ", "TLT", "USO", "GLD", "DIA", "IWM", "VIX"]

from src.market_data import fetch_stocktwits_sentiment as _fetch_st_canonical


def fetch_stocktwits_sentiment(symbols: list = None) -> list:
    if symbols is None:
        symbols = STOCKTWITS_MACRO_SYMBOLS
    return _fetch_st_canonical(symbols)


def build_stocktwits_summary(st_data: list) -> str:
    """Format StockTwits sentiment for the Grok prompt."""
    if not st_data:
        return ""
    lines = ["", "=" * 60, "STOCKTWITS RETAIL SENTIMENT (live, last 30 posts per symbol)", "=" * 60, ""]
    for item in st_data:
        lines.append(f"- {item['symbol']}: {item['bull_ratio']:.0f}% bullish "
                    f"({item['bullish']}B/{item['bearish']}Be of {item['messages']} posts) — {item['signal']}")
    lines.append("")
    lines.append("This is RETAIL sentiment. Where it diverges from institutional positioning")
    lines.append("(Polymarket, FRED data), it may signal contrarian opportunities or crowded trades.")
    return "\n".join(lines)


# ─────────────────────────────────────────────
# POLYMARKET PREDICTION MARKET DATA
# ─────────────────────────────────────────────
POLYMARKET_SLUGS = {
    "us-recession-by-end-of-2026": "US Recession by End of 2026",
    "how-many-fed-rate-cuts-in-2026": "Fed Rate Cuts in 2026",
    "how-high-will-inflation-get-in-2026": "Inflation Level in 2026",
    "what-will-fed-rate-hit-before-2027": "Fed Rate Bounds Before 2027",
    "will-the-iranian-regime-fall-by-the-end-of-2026": "Iranian Regime Falls Before 2027",
    "will-the-us-invade-iran-before-2027": "US Invades Iran Before 2027",
    "us-iran-nuclear-deal-before-2027": "US-Iran Nuclear Deal Before 2027",
}


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_polymarket_data() -> list:
    """Fetch live prediction market odds from Polymarket for macro-relevant contracts."""
    results = []
    for slug, label in POLYMARKET_SLUGS.items():
        try:
            r = requests.get(f"https://gamma-api.polymarket.com/events?slug={slug}", timeout=10)
            events = r.json()
            if events:
                event = events[0]
                for m in event.get("markets", []):
                    question = m.get("question", "")
                    outcomes = m.get("outcomes", [])
                    prices = m.get("outcomePrices", [])
                    if prices and outcomes:
                        try:
                            price_list = json.loads(prices) if isinstance(prices, str) else prices
                        except Exception:
                            price_list = prices
                        # Get Yes price (probability)
                        yes_prob = float(price_list[0]) * 100 if price_list else 0
                        if yes_prob > 0 and yes_prob < 100:
                            results.append({
                                "category": label,
                                "question": question,
                                "yes_prob": round(yes_prob, 1),
                                "no_prob": round(100 - yes_prob, 1),
                            })
        except Exception as e:
            logger.warning(f"Polymarket fetch failed for {slug}: {e}")
    return results


def build_polymarket_summary(pm_data: list) -> str:
    """Format Polymarket data for the Grok prompt."""
    if not pm_data:
        return ""
    lines = ["", "=" * 60, "POLYMARKET PREDICTION MARKET ODDS (live, real-money bets)", "=" * 60, ""]
    for item in pm_data:
        lines.append(f"- {item['question']}: YES {item['yes_prob']}% / NO {item['no_prob']}%")
    lines.append("")
    lines.append("These are real-money prediction market probabilities. They represent the crowd's")
    lines.append("consensus view. Note where these AGREE or DISAGREE with your own assessment.")
    return "\n".join(lines)


def _call_grok_api(api_key: str, current_data_json: str, regime_names_json: str, ticker_list: list = None) -> dict:
    """Call Grok API with FRED data + live X/Twitter sentiment to get regime probabilities."""
    from openai import OpenAI
    client = OpenAI(base_url="https://api.x.ai/v1", api_key=api_key)

    system_prompt = """You are a senior macroeconomic strategist at a top-tier investment bank.
You combine multiple data layers to assess the probability of macro regimes:

1. HARD DATA: Live US economic indicators from FRED — includes traditional macro (CPI, NFP, GDP),
   plus market-based signals (VIX, HY credit spreads, breakeven inflation rates),
   financial conditions (Chicago Fed NFCI), and the Sahm Rule recession indicator.

2. FOMC DATA (Last 3 meetings): Dot plots, SEP projections, statement language, vote dissents.
   This reveals the Fed's own evolving assessment. Pay close attention to:
   - Dot plot distribution shifts between meetings (hawkish/dovish tilt)
   - SEP revision direction (inflation up? growth down?)
   - Dissent patterns and statement language changes
   - Gap between market pricing (CME FedWatch) and Fed dots

3. BEIGE BOOKS (Last 3): Anecdotal evidence from all 12 Fed districts about real economic
   conditions on the ground — hiring, spending, pricing power, business sentiment.
   This captures what the hard data hasn't yet shown.

4. LEADING INDICATORS: ISM Manufacturing & Services PMI, Atlanta Fed GDPNow real-time GDP
   tracker, CME FedWatch implied rate probabilities, Conference Board LEI signals.
   These LEAD the traditional data by weeks or months.

5. STOCKTWITS RETAIL SENTIMENT: Bull/bear ratios from recent posts on macro ETFs (SPY, QQQ,
   TLT, USO, GLD, etc.). This is RETAIL sentiment — useful as a contrarian indicator when it
   diverges from institutional views. Heavy retail bearishness + institutional bullishness =
   possible contrarian buy signal, and vice versa.

6. POLYMARKET PREDICTION MARKETS: Real-money betting odds on recession, Fed rate cuts,
   inflation levels, and geopolitical outcomes (provided below). These represent the crowd's
   consensus probability — where Polymarket agrees with your assessment, confidence is higher.
   Where it disagrees, flag the divergence and explain why.

6. REAL-TIME SENTIMENT: Search X/Twitter right now for the latest posts about:
   - Fed policy, rate cuts/hikes, FOMC commentary and reactions to the latest dot plot
   - Recession fears or optimism
   - Inflation expectations, oil/energy prices
   - Geopolitical developments (wars, sanctions, trade)
   - Tariff impacts and trade policy
   - Market sentiment (risk-on/risk-off, VIX, equity flows)
   - Notable commentary from economists, fund managers, and Fed officials

Your job: Synthesize ALL seven layers to assign a probability (%) to each of the 6 macro regimes.
The hard data is the foundation. The FOMC data reveals the Fed's reaction function. The Beige
Books capture ground-truth. Leading indicators show where things are headed. And sentiment
captures where the market agrees or disagrees with all of the above.

Rules:
- Probabilities MUST sum to exactly 100
- Reference specific FRED data points (e.g., "unemployment at 4.4%")
- Reference the dot plot distribution and SEP projections (e.g., "14 of 19 members see 0-1 cuts")
- Reference specific sentiment/news themes you found on X (e.g., "trending concern about...")
- Note where market sentiment DIVERGES from the Fed's own projections — this is the most valuable signal
- Be direct and institutional in tone
- Each rationale should be 2-4 sentences blending data + FOMC projections + sentiment
- You will be given BASE PROBABILITIES and your MOST RECENT prior analysis. Use this to:
  - START from the base probabilities as your anchor — these reflect careful calibration
  - Only deviate from base probabilities when you can cite SPECIFIC NEW DATA that justifies the shift
  - Compare to your most recent prior analysis and note what changed and WHY
  - If no material new data has emerged since the last run, your probabilities should be VERY CLOSE to the base rates (within 3-5pp)
  - Do NOT continue a trend just because prior runs showed movement — each run is independent
  - A probability drifting steadily in one direction across runs is a RED FLAG for anchoring bias

Respond with ONLY valid JSON in this exact format:
{"regimes": [{"name": "regime name", "probability": N, "rationale": "..."}],
 "sentiment_summary": "2-3 sentences on what is NEW on X right now. Do NOT repeat the same general narrative from prior assessments. Focus on: new posts in the last hour, any shift in tone, new data releases, breaking news, or notable new commentary. If nothing material has changed, say 'No material change in the last hour — narrative remains [X].' Be specific about what is different.",
 "change_summary": "Compare your probabilities to the PRIOR ANALYSES provided. State the exact pp changes. If probabilities barely moved (<2pp), say 'No material shift — probabilities stable.' Do NOT manufacture changes.",
 "asset_estimates": {"regime_name": {"TICKER1": return_pct, "TICKER2": return_pct}}}

IMPORTANT: If a portfolio ticker list is provided, you MUST include "asset_estimates" in your response.
For each regime, estimate the 12-month total return (%) for EACH ticker based on:
- The asset's sector/industry exposure
- Its historical sensitivity to the macro factors in that regime
- How it performed in past analogous environments
Use your knowledge of each asset. Be specific, not generic."""

    # Build history context
    history_ctx = build_history_context()
    history_block = ""
    if history_ctx:
        history_block = f"""

BASE PROBABILITIES + MOST RECENT PRIOR ANALYSIS (anchor to base rates, only deviate with new data):
{history_ctx}
"""

    user_prompt = f"""HARD DATA + FOMC PROJECTIONS — Current US Economic Indicators (live from FRED)
and latest FOMC dot plot / Summary of Economic Projections:
{current_data_json}

MACRO REGIMES to evaluate:
{regime_names_json}
{history_block}
Now search X/Twitter for the latest macro, Fed, recession, inflation, and geopolitical sentiment.
Pay special attention to market reaction to today's FOMC decision and dot plot.
Compare your assessment to the base probabilities and your most recent prior analysis. Note what changed.
Start from the BASE PROBABILITIES and only adjust if specific new data justifies it. Do not drift.
{f'PORTFOLIO TICKERS to estimate returns for: {", ".join(ticker_list)}. For each regime, estimate 12-month return for each ticker.' if ticker_list else ''}

Before responding: verify all probabilities sum to 100%, all return estimates are internally consistent with the regime described, and all cited data points come from the FRED/market data provided above. Do not invent economic statistics.

Respond with ONLY valid JSON."""

    response = client.chat.completions.create(
        model="grok-3",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=2500,
        temperature=0.3,
    )
    result = json.loads(response.choices[0].message.content)

    # Normalize probabilities to sum to 100
    # Handle case where model returns decimals (0.35) instead of percentages (35)
    regimes = result.get("regimes", [])
    total = sum(r.get("probability", 0) for r in regimes)
    if 0 < total <= 1.1:
        # Model returned fractions — convert to percentages
        for r in regimes:
            r["probability"] = round(r["probability"] * 100)
        total = sum(r.get("probability", 0) for r in regimes)
    if total > 0 and abs(total - 100) > 1:
        for r in regimes:
            r["probability"] = round(r["probability"] * 100 / total)

    # Extract per-regime asset estimates if provided
    asset_estimates = result.get("asset_estimates", {})
    # Also check if estimates are nested inside each regime object
    if not asset_estimates:
        for r in regimes:
            if "asset_estimates" in r:
                asset_estimates[r["name"]] = r.pop("asset_estimates")

    return {
        "regimes": regimes,
        "sentiment_summary": result.get("sentiment_summary", ""),
        "change_summary": result.get("change_summary", ""),
        "asset_estimates": asset_estimates,
        "model": "grok-3",
        "success": True,
    }


from src.analysis_history import load_history as _load_history, get_latest as _get_latest_history


def load_grok_history() -> list:
    return _load_history(GROK_HISTORY_FILE)


def save_grok_result(result: dict) -> None:
    """Append a timestamped Grok result to the history file."""
    from datetime import datetime
    from src.analysis_history import save_history
    history = load_grok_history()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "regimes": result.get("regimes", []),
        "sentiment_summary": result.get("sentiment_summary", ""),
        "change_summary": result.get("change_summary", ""),
        "asset_estimates": result.get("asset_estimates", {}),
    }
    history.append(entry)
    save_history(GROK_HISTORY_FILE, history)

    # Write cross-page context
    try:
        from src.cross_context import write_context
        _regimes = result.get("regimes", [])
        _top = max(_regimes, key=lambda r: r.get("probability", 0)) if _regimes else {}
        write_context("scenario_analysis", {"top_regime": _top})
    except Exception:
        pass

    # Write signal for consensus engine (batch: 1 insert instead of N)
    try:
        from src.signal_engine import write_signals_batch
        _regimes_s = result.get("regimes", [])
        if _regimes_s:
            _top_s = max(_regimes_s, key=lambda r: r.get("probability", 0))
            _rname = _top_s.get("name", "").lower()
            _bullish = {"soft landing", "goldilocks", "expansion", "recovery",
                        "reflation", "growth", "boom", "bull"}
            _bearish = {"recession", "stagflation", "crisis", "hard landing",
                        "contraction", "depression", "bust", "bear"}
            if any(b in _rname for b in _bullish):
                _dir = "bullish"
            elif any(b in _rname for b in _bearish):
                _dir = "bearish"
            else:
                _dir = "neutral"
            _prob = _top_s.get("probability", 50)
            _conv = min(1.0, _prob / 100.0)
            _reason = f"Top regime: {_top_s.get('name', '?')} ({_prob}%)"
            _tickers = ticker_list or ["SPY"]
            write_signals_batch([
                {"source": "scenario_analysis", "ticker": _tk, "direction": _dir,
                 "conviction": _conv, "reasoning": _reason}
                for _tk in _tickers
            ])
    except Exception:
        pass


def get_latest_grok_result() -> tuple:
    return _get_latest_history(GROK_HISTORY_FILE, stale_hours=1.0)


def build_history_context() -> str:
    """Build context from the most recent Grok analysis only (prevents drift).

    Only the last entry is provided so Grok can note what changed since last time,
    without anchoring on a long trend of its own prior outputs.
    The base probabilities are included as the anchor point.
    """
    history = load_grok_history()
    if not history:
        return ""

    # Include base probabilities as the anchor
    base_probs = ", ".join(f"{name}: {data['probability']}%" for name, data in MACRO_REGIMES.items())
    lines = [f"BASE PROBABILITIES (starting anchor): {base_probs}"]

    # Only show the most recent 1-2 entries to prevent feedback-loop drift
    recent = history[-2:]
    for entry in recent:
        ts = entry.get("timestamp", "")
        try:
            ts_fmt = pd.Timestamp(ts).strftime("%b %d %I:%M %p")
        except Exception:
            ts_fmt = ts
        probs = {r["name"]: r.get("probability", 0) for r in entry.get("regimes", [])}
        prob_str = ", ".join(f"{name}: {p}%" for name, p in probs.items())
        sentiment = entry.get("sentiment_summary", "")
        lines.append(f"[{ts_fmt}] {prob_str}")
        if sentiment:
            lines.append(f"  Sentiment: {sentiment}")

    return "\n".join(lines)


def run_grok_if_stale(api_key: str, driver_data: dict, fed_drivers: dict, macro_regimes: dict, fred_api_key: str = None, ticker_list: list = None) -> dict:
    """Check if the latest Grok result is older than 1 hour. If so, run a new analysis."""
    latest, is_stale = get_latest_grok_result()

    if not is_stale and latest:
        return {"regimes": latest["regimes"], "model": "grok-3", "success": True,
                "sentiment_summary": latest.get("sentiment_summary", ""),
                "change_summary": latest.get("change_summary", ""),
                "asset_estimates": latest.get("asset_estimates", {}),
                "timestamp": latest["timestamp"], "from_cache": True}

    # Check Supabase AI cache (shared across users/sessions)
    try:
        from src.ai_cache import get_cached_ai, cache_ai_response, build_cache_key
        _grok_key = build_cache_key("scenario_grok", "MACRO", json.dumps(list(macro_regimes.keys())))
        _cached_grok = get_cached_ai(_grok_key)
        if _cached_grok:
            cached_result = json.loads(_cached_grok)
            cached_result["from_cache"] = True
            return cached_result
    except Exception:
        pass

    # Stale or no history — run fresh analysis
    try:
        fred_summary = build_fred_summary(driver_data, fed_drivers, fred_key_ref=fred_api_key)
        regime_info = json.dumps([
            {"name": name, "description": data["description"]}
            for name, data in macro_regimes.items()
        ], indent=2)
        result = _call_grok_api(api_key, fred_summary, regime_info, ticker_list=ticker_list)
        if result["success"]:
            save_grok_result(result)
            result["timestamp"] = pd.Timestamp.now().isoformat()
            result["from_cache"] = False
            # Cache in Supabase for cross-user sharing (1 hour)
            try:
                _safe = json.loads(json.dumps(result, default=str))
                cache_ai_response(_grok_key, json.dumps(_safe),
                                   model="grok-3", source_page="scenario_analysis",
                                   ticker="MACRO", ttl_hours=1, cost_estimate=0.03)
            except Exception:
                pass
        return result
    except Exception as e:
        logger.error(f"Grok analysis failed: {e}")
        # Fall back to latest cached result if available
        if latest:
            return {"regimes": latest["regimes"], "model": "grok-3", "success": True,
                    "sentiment_summary": latest.get("sentiment_summary", ""),
                    "timestamp": latest["timestamp"], "from_cache": True, "error": str(e)}
        return {"regimes": [], "model": "grok-3", "success": False, "error": str(e)}


def build_fred_summary(driver_data: dict, fed_drivers: dict, fred_key_ref: str = None) -> str:
    """Build a human-readable summary of current FRED data + static Fed docs for the AI prompt."""
    lines = []
    for sid, info in fed_drivers.items():
        if sid not in driver_data or driver_data[sid].empty:
            continue
        df = driver_data[sid]
        latest = df.iloc[-1]["value"]
        latest_date = df.iloc[-1]["date"].strftime("%Y-%m-%d")
        prev = df.iloc[-2]["value"] if len(df) > 1 else latest
        change = latest - prev

        if info["yoy"] and len(df) >= 13:
            yoy = ((df.iloc[-1]["value"] / df.iloc[-13]["value"]) - 1) * 100
            lines.append(f"- {info['name']} ({sid}): {yoy:.1f}% YoY (as of {latest_date}), "
                        f"prior period: {((df.iloc[-2]['value'] / df.iloc[-14]['value']) - 1) * 100:.1f}% YoY"
                        if len(df) >= 14 else
                        f"- {info['name']} ({sid}): {yoy:.1f}% YoY (as of {latest_date})")
        elif info["unit"] == "%":
            lines.append(f"- {info['name']} ({sid}): {latest:.2f}% (as of {latest_date}), change: {change:+.2f}pp")
        elif sid == "PAYEMS":
            lines.append(f"- {info['name']} ({sid}): {latest:,.0f}K total, MoM change: {change:+,.0f}K jobs (as of {latest_date})")
        elif sid == "ICSA":
            lines.append(f"- {info['name']} ({sid}): {latest:,.0f} weekly claims (as of {latest_date}), change: {change:+,.0f}")
        elif info["unit"] in ("K", "$M", "$B"):
            lines.append(f"- {info['name']} ({sid}): {latest:,.0f} {info['unit']} (as of {latest_date}), change: {change:+,.0f}")
        else:
            lines.append(f"- {info['name']} ({sid}): {latest:,.1f} (as of {latest_date}), change: {change:+.1f}")

    # ── FOMC DOT PLOT & SEP (Last 3 meetings) ──
    lines.append("")
    lines.append("=" * 60)
    lines.append("FOMC DOT PLOT & SUMMARY OF ECONOMIC PROJECTIONS (Last 3 meetings)")
    lines.append("=" * 60)

    lines.append("")
    lines.append("MARCH 18, 2026 FOMC (most recent — source: federalreserve.gov):")
    lines.append("Decision: Held rates at 3.50-3.75%. Vote: 11-1.")
    lines.append("Dot plot — fed funds projections:")
    lines.append("  2026: 3.625% (7), 3.375% (7), 3.125% (2), 2.875% (2), 2.625% (1) — Median: 3.4%")
    lines.append("  2027: 3.125% (6), 3.375% (4), 3.625% (3), 2.875% (3), others (3) — Median: 3.1%")
    lines.append("  2028: 3.125% (7), 3.625% (3), 3.375% (3), 2.875% (3), 2.625% (2) — Median: 3.1%")
    lines.append("  Longer Run: 3.000% (5), 3.125% (3), spread from 2.625-3.875 — Median: 3.0%")
    lines.append("KEY SHIFT: 14 of 19 members see 0-1 cuts in 2026 (vs 7 in Dec). Major hawkish tilt.")
    lines.append("SEP medians: GDP 2.4%, UE 4.4%, PCE 2.7%, Core PCE 2.7%")
    lines.append("Inflation projection RAISED to 2.7% from 2.5% in Dec — Iran oil shock.")

    lines.append("")
    lines.append("DECEMBER 10, 2025 FOMC:")
    lines.append("Decision: Cut 25bp to 3.50-3.75%. Dissents: 1 for -50bp, 2 for hold.")
    lines.append("Dot plot 2026: range 2.125-3.875%, median 3.4%. 7 members saw 1-2 cuts, 12 saw >1 cut.")
    lines.append("SEP medians: GDP 2.3%, UE 4.3%, PCE 2.5%, Core PCE 2.5%")
    lines.append("Key: Growth revised UP from Sep, inflation revised DOWN slightly.")

    lines.append("")
    lines.append("SEPTEMBER 17, 2025 FOMC:")
    lines.append("Decision: Cut 25bp to 4.00-4.25%. 1 dissent for -50bp.")
    lines.append("Statement: 'Job gains have slowed, unemployment edged up. Inflation somewhat elevated.'")
    lines.append("SEP medians: GDP 1.6%, UE 4.4%, PCE 2.6%, Fed Funds end-2026: 3.4%")
    lines.append("Key: Downside risks to employment acknowledged. Fed began easing cycle.")

    # ── BEIGE BOOKS (Last 3) ──
    lines.append("")
    lines.append("=" * 60)
    lines.append("FEDERAL RESERVE BEIGE BOOKS (Last 3 releases)")
    lines.append("=" * 60)

    lines.append("")
    lines.append("MARCH 4, 2026 BEIGE BOOK:")
    lines.append("- 'Bifurcated economy': 8 districts slight-modest growth, 3 flat, 1 decline")
    lines.append("- K-shaped: high-income spending strong, bottom 40% pulled back significantly")
    lines.append("- 'Attrition without replacement' — firms shrinking headcount naturally, no mass layoffs")
    lines.append("- Tariff cost pressures across ALL districts, firms reluctant to pass through")
    lines.append("- Manufacturing split: 5 districts growth, 6 contraction")
    lines.append("- Rising energy and insurance costs straining margins")

    lines.append("")
    lines.append("JANUARY 15, 2026 BEIGE BOOK:")
    lines.append("- Growth picked up slightly to modestly in 8 of 12 districts; 3 flat, 1 declined")
    lines.append("- Holiday shopping bolstered consumer activity; high-income strong, low-income hesitant")
    lines.append("- Employment mostly flat; firms using temp workers 'to stay flexible in uncertain times'")
    lines.append("- Wage growth at 'normal/moderate' levels; job switching declined")
    lines.append("- Tariff-related cost pressures consistent across all districts")
    lines.append("- Manufacturing: 5 districts growth, 6 contraction")

    lines.append("")
    lines.append("NOVEMBER 26, 2025 BEIGE BOOK:")
    lines.append("- Activity little changed in most districts; 2 modest decline, 1 modest growth")
    lines.append("- Consumer spending declined further; higher-end resilient")
    lines.append("- Employment edged lower, no major layoffs. Wages rose modestly")
    lines.append("- Prices rose moderately; tariff-induced cost pressures in manufacturing and retail")
    lines.append("- Auto sales flat to down. Housing showed renewed strength")

    # ── LEADING INDICATORS & MARKET SIGNALS ──
    lines.append("")
    lines.append("=" * 60)
    lines.append("LEADING INDICATORS & MARKET-BASED SIGNALS")
    lines.append("=" * 60)

    # GDPNow — live from FRED
    if fred_key_ref:
        gdpnow_df = fetch_fred_series(fred_key_ref, "GDPNOW", limit=5)
        if not gdpnow_df.empty:
            gdn_val = gdpnow_df.iloc[-1]["value"]
            gdn_date = gdpnow_df.iloc[-1]["date"].strftime("%Y-%m-%d")
            gdn_prev = gdpnow_df.iloc[-2]["value"] if len(gdpnow_df) > 1 else gdn_val
            lines.append(f"")
            lines.append(f"ATLANTA FED GDPNow (live, as of {gdn_date}): Q1 GDP estimate = {gdn_val:.1f}%")
            lines.append(f"  Prior estimate: {gdn_prev:.1f}% | Change: {gdn_val - gdn_prev:+.1f}pp")

    # Note: VIX, HY spreads, breakeven inflation, NFCI, Sahm Rule are already in the
    # FRED data section above (pulled live). The following are NOT available via free API,
    # so we instruct Grok to search for them in real-time.
    lines.append("")
    lines.append("IMPORTANT — The following data is NOT provided above. You MUST search X/Twitter")
    lines.append("and your knowledge for the LATEST values. Do not use stale data:")
    lines.append("  - ISM Manufacturing PMI (latest monthly reading and key subindices)")
    lines.append("  - ISM Services PMI (latest monthly reading)")
    lines.append("  - CME FedWatch probabilities (current market-implied rate path for next 3 meetings)")
    lines.append("  - Conference Board Leading Economic Index (LEI) — latest reading and trend")
    lines.append("  - Any breaking news since the data above was collected")

    # ── StockTwits Retail Sentiment ──
    st_data = fetch_stocktwits_sentiment()
    st_summary = build_stocktwits_summary(st_data)
    if st_summary:
        lines.append(st_summary)

    # ── Polymarket Prediction Market Data ──
    pm_data = fetch_polymarket_data()
    pm_summary = build_polymarket_summary(pm_data)
    if pm_summary:
        lines.append(pm_summary)

    # Cross-page context from other analysis pages
    try:
        from src.cross_context import build_ai_context
        _xctx = build_ai_context()
        if _xctx:
            lines.append("")
            lines.append(_xctx)
    except Exception:
        pass

    return "\n".join(lines)


# Key economic drivers that influence Fed decisions
FED_DRIVERS = {
    "CPIAUCSL":  {"name": "CPI (All Items)",       "unit": "index",  "yoy": True,  "color": "#ff4b4b", "category": "Inflation",  "fed_weight": "Primary"},
    "PCEPILFE":  {"name": "Core PCE",               "unit": "index",  "yoy": True,  "color": "#ffaa00", "category": "Inflation",  "fed_weight": "Primary"},
    "UNRATE":    {"name": "Unemployment Rate",       "unit": "%",      "yoy": False, "color": "#00d1ff", "category": "Employment", "fed_weight": "Primary"},
    "PAYEMS":    {"name": "Nonfarm Payrolls",        "unit": "K",      "yoy": False, "color": "#00ff96", "category": "Employment", "fed_weight": "Primary"},
    "FEDFUNDS":  {"name": "Fed Funds Rate",          "unit": "%",      "yoy": False, "color": "#ad7fff", "category": "Fed",        "fed_weight": "Primary"},
    "T10Y2Y":    {"name": "2s10s Yield Spread",      "unit": "%",      "yoy": False, "color": "#ff69b4", "category": "Rates",      "fed_weight": "High"},
    "DGS10":     {"name": "10-Year Treasury Yield",  "unit": "%",      "yoy": False, "color": "#00bcd4", "category": "Rates",      "fed_weight": "High"},
    "DGS2":      {"name": "2-Year Treasury Yield",   "unit": "%",      "yoy": False, "color": "#8bc34a", "category": "Rates",      "fed_weight": "High"},
    "RSAFS":     {"name": "Retail Sales",            "unit": "$M",     "yoy": True,  "color": "#e91e63", "category": "Consumer",   "fed_weight": "Medium"},
    "UMCSENT":   {"name": "Consumer Sentiment",      "unit": "index",  "yoy": False, "color": "#ffc107", "category": "Consumer",   "fed_weight": "Medium"},
    "INDPRO":    {"name": "Industrial Production",   "unit": "index",  "yoy": True,  "color": "#795548", "category": "Production", "fed_weight": "Medium"},
    "GDP":       {"name": "Real GDP",                "unit": "$B",     "yoy": True,  "color": "#607d8b", "category": "Growth",     "fed_weight": "High"},
    "HOUST":     {"name": "Housing Starts",          "unit": "K",      "yoy": False, "color": "#9c27b0", "category": "Housing",    "fed_weight": "Medium"},
    "DTWEXBGS":  {"name": "Trade-Weighted Dollar",   "unit": "index",  "yoy": False, "color": "#4caf50", "category": "FX",         "fed_weight": "Medium"},
    "ICSA":      {"name": "Initial Jobless Claims",  "unit": "",       "yoy": False, "color": "#ff5722", "category": "Employment", "fed_weight": "High"},
    # Tier 2 — Leading indicators
    "SAHMCURRENT": {"name": "Sahm Rule Recession Indicator", "unit": "", "yoy": False, "color": "#d50000", "category": "Recession Signal", "fed_weight": "High"},
    "NFCI":      {"name": "Chicago Fed Financial Conditions", "unit": "index", "yoy": False, "color": "#00897b", "category": "Financial Conditions", "fed_weight": "High"},
    # Tier 3 — Market-based signals
    "VIXCLS":    {"name": "VIX (Fear Index)",       "unit": "",       "yoy": False, "color": "#f44336", "category": "Market Stress", "fed_weight": "Medium"},
    "BAMLH0A0HYM2": {"name": "HY Credit Spread (ICE BofA)", "unit": "%", "yoy": False, "color": "#e65100", "category": "Market Stress", "fed_weight": "High"},
    "T5YIE":     {"name": "5Y Breakeven Inflation", "unit": "%",      "yoy": False, "color": "#ff6f00", "category": "Inflation Expectations", "fed_weight": "High"},
    "T10YIE":    {"name": "10Y Breakeven Inflation", "unit": "%",     "yoy": False, "color": "#ff8f00", "category": "Inflation Expectations", "fed_weight": "Medium"},
}

# ─────────────────────────────────────────────
# MACRO REGIME DEFINITIONS
# ─────────────────────────────────────────────
# Base probabilities calibrated to current conditions as of March 2026.
#
# Current macro backdrop:
#   - Fed held at 3.50-3.75% on Mar 18, 2026; dot plot signals only 1 cut in 2026
#   - US-Israel war on Iran began Feb 28 — Strait of Hormuz effectively closed
#   - Oil spiked above $100/bbl (Brent hit $126 peak), 20% of global supply disrupted
#   - Feb 2026 NFP: -92K (first negative print since COVID), unemployment 4.4%
#   - CPI YoY 2.4% (headline), but Fed projects Core PCE rising to 2.7% due to oil/tariffs
#   - Trump tariffs at 10.5% effective rate (highest since 1943); SCOTUS review pending
#   - GDP consensus: 1.9-2.5% (pre-war), likely lower now
#   - Recession probability: 20-30% per Goldman/RSM (and rising with war)
#
# Key risk: The Iran/Hormuz crisis is a classic stagflationary oil shock —
# it simultaneously pushes inflation higher (supply disruption) AND drags
# growth lower (consumer squeeze, uncertainty). The Fed is trapped: can't cut
# because inflation is above target, can't hike because the economy is weakening.
# This is the dominant macro risk and heavily weights stagflation & recession.
#
# Units for driver_moves:
#   CPI/PCE/Retail/IndProd/GDP: change in YoY rate (percentage points)
#   UNRATE/FEDFUNDS/DGS10/DGS2/T10Y2Y: change in level (percentage points)
#   PAYEMS: MoM job gains level (thousands) — not a delta, the actual monthly number
#   UMCSENT: change in index points
#   HOUST: change in thousands (annualized rate)
#   DTWEXBGS: change in index points
#   ICSA: change in weekly claims level
#
# Asset betas: estimated total return (%) over scenario horizon (~12 months).
# Calibrated against historical analogs for each regime type.

MACRO_REGIMES = {
    "Stagflation": {
        "description": "Iran oil shock + tariffs keep inflation above 3% while growth stalls below 1%. "
                       "Fed paralyzed — can't cut (inflation) or hike (weak growth). 1970s-lite analog.",
        "rationale": "Hormuz closure is a textbook supply-side shock: oil >$100 drives inflation higher while "
                     "squeezing consumers and margins. Tariffs (10.5% effective rate) compound price pressure. "
                     "Fed already projecting Core PCE at 2.7%. Feb NFP -92K shows growth already weakening. "
                     "Historically the most likely outcome of an oil shock hitting a late-cycle economy.",
        "probability": 30,
        "driver_moves": {
            "CPIAUCSL": 1.2, "PCEPILFE": 0.8, "UNRATE": 0.8, "PAYEMS": 30,
            "FEDFUNDS": 0.0, "T10Y2Y": -0.3, "DGS10": 0.5, "DGS2": 0.7,
            "RSAFS": -2.5, "UMCSENT": -15, "INDPRO": -1.5, "GDP": 0.3,
            "HOUST": -120, "DTWEXBGS": -2, "ICSA": 35000,
        },
        "asset_betas": {"SPY": -18, "QQQ": -22, "TLT": -10, "USO": 25, "GLD": 18, "_default": -15},
    },
    "Recession": {
        "description": "Oil shock + tariff drag + weak labor market tip the economy into contraction. "
                       "Feb NFP already -92K. Fed eventually forced to cut despite above-target inflation.",
        "rationale": "Goldman (20%), RSM (30%), Morgan Stanley (15%) recession estimates were set before or early "
                     "in the Hormuz crisis. Feb NFP at -92K is the first negative print since COVID. Unemployment "
                     "rising to 4.4%. Oil shocks preceded the 1973, 1980, 1990, and 2008 recessions. Consumer "
                     "spending already under pressure from tariff-driven price increases.",
        "probability": 25,
        "driver_moves": {
            "CPIAUCSL": -0.5, "PCEPILFE": -0.3, "UNRATE": 1.8, "PAYEMS": -120,
            "FEDFUNDS": -1.5, "T10Y2Y": 0.8, "DGS10": -1.0, "DGS2": -1.8,
            "RSAFS": -6.0, "UMCSENT": -22, "INDPRO": -4.0, "GDP": -1.5,
            "HOUST": -250, "DTWEXBGS": -3, "ICSA": 90000,
        },
        "asset_betas": {"SPY": -25, "QQQ": -32, "TLT": 14, "USO": -20, "GLD": 10, "_default": -22},
    },
    "Soft Landing": {
        "description": "Iran conflict resolves in weeks, oil normalizes below $80, tariff clarity from SCOTUS. "
                       "Fed resumes gradual cutting. Requires multiple tailwinds to materialize.",
        "rationale": "Was the consensus view through 2025, but now requires: (1) rapid Iran de-escalation, "
                     "(2) oil back below $80, (3) SCOTUS tariff clarity, and (4) labor market stabilization. "
                     "Each is possible individually but the conjunction is unlikely. Reduced from ~35% "
                     "pre-war consensus to 15%.",
        "probability": 15,
        "driver_moves": {
            "CPIAUCSL": -0.8, "PCEPILFE": -0.5, "UNRATE": 0.2, "PAYEMS": 140,
            "FEDFUNDS": -0.75, "T10Y2Y": 0.3, "DGS10": -0.3, "DGS2": -0.7,
            "RSAFS": 2.0, "UMCSENT": 8, "INDPRO": 1.0, "GDP": 2.0,
            "HOUST": 40, "DTWEXBGS": -1, "ICSA": 5000,
        },
        "asset_betas": {"SPY": 12, "QQQ": 18, "TLT": 6, "USO": -10, "GLD": -3, "_default": 10},
    },
    "Financial Crisis": {
        "description": "Prolonged Hormuz closure cascades into sovereign debt stress in oil-importing nations, "
                       "shipping/insurance market seizes, credit contagion spreads. Emergency Fed response.",
        "rationale": "Elevated from historical ~3-4% base rate. Hormuz carries 20% of global oil — a months-long "
                     "closure would cripple oil-importing economies (India, Japan, EU). Shipping insurance "
                     "costs already spiking. If credit stress in energy-dependent sovereigns triggers contagion, "
                     "this becomes systemic. Low probability but non-trivial given active war.",
        "probability": 10,
        "driver_moves": {
            "CPIAUCSL": -1.5, "PCEPILFE": -1.2, "UNRATE": 3.0, "PAYEMS": -350,
            "FEDFUNDS": -2.5, "T10Y2Y": 1.2, "DGS10": -1.5, "DGS2": -2.5,
            "RSAFS": -10.0, "UMCSENT": -35, "INDPRO": -7.0, "GDP": -3.5,
            "HOUST": -400, "DTWEXBGS": 5, "ICSA": 150000,
        },
        "asset_betas": {"SPY": -38, "QQQ": -42, "TLT": 18, "USO": -35, "GLD": 15, "_default": -32},
    },
    "Re-Acceleration": {
        "description": "War ends quickly, oil drops sharply, pent-up demand surges, SCOTUS strikes tariffs. "
                       "Growth rebounds but inflation re-ignites — Fed forced to hold or hike.",
        "rationale": "Requires rapid war resolution + tariff rollback creating a demand surge. Possible if "
                     "SCOTUS strikes IEEPA tariffs AND ceasefire is reached. But even then, labor market "
                     "has already weakened (4.4% UE, negative NFP) — reacceleration from this base "
                     "would require a strong positive catalyst. Unlikely but not impossible.",
        "probability": 10,
        "driver_moves": {
            "CPIAUCSL": 0.5, "PCEPILFE": 0.3, "UNRATE": -0.2, "PAYEMS": 220,
            "FEDFUNDS": 0.25, "T10Y2Y": -0.4, "DGS10": 0.4, "DGS2": 0.7,
            "RSAFS": 4.0, "UMCSENT": 8, "INDPRO": 2.5, "GDP": 3.0,
            "HOUST": 70, "DTWEXBGS": 2, "ICSA": -15000,
        },
        "asset_betas": {"SPY": 10, "QQQ": 8, "TLT": -8, "USO": 10, "GLD": -5, "_default": 7},
    },
    "Goldilocks": {
        "description": "Best case: rapid de-escalation, oil normalizes, tariffs rolled back, Fed cuts 2-3x, "
                       "labor market stabilizes. Requires nearly everything to break right.",
        "rationale": "Historical base rate ~15-18% (mid-1960s, 1995-98, 2017). Reduced to 10% because it "
                     "requires simultaneous resolution of war, oil normalization, tariff rollback, AND "
                     "a stabilizing labor market. With this many active headwinds, the probability of "
                     "all clearing within 12 months is low.",
        "probability": 10,
        "driver_moves": {
            "CPIAUCSL": -0.5, "PCEPILFE": -0.4, "UNRATE": 0.0, "PAYEMS": 170,
            "FEDFUNDS": -0.50, "T10Y2Y": 0.2, "DGS10": -0.2, "DGS2": -0.4,
            "RSAFS": 3.0, "UMCSENT": 12, "INDPRO": 2.0, "GDP": 2.5,
            "HOUST": 40, "DTWEXBGS": -1, "ICSA": -5000,
        },
        "asset_betas": {"SPY": 15, "QQQ": 20, "TLT": 5, "USO": -5, "GLD": -2, "_default": 12},
    },
}

# Factor moves per regime: expected total change in each daily FRED factor over scenario horizon
# Includes oil (DCOILWTICO) and VIX×HY interaction term
REGIME_FACTOR_MOVES = {
    "Stagflation":      {"VIXCLS": 12, "DGS10": 0.5,  "BAMLH0A0HYM2": 1.5, "T5YIE": 0.5, "DTWEXBGS": -2, "DCOILWTICO": 25, "VIX_HY": 18},
    "Recession":        {"VIXCLS": 18, "DGS10": -1.0, "BAMLH0A0HYM2": 3.0, "T5YIE": -0.3, "DTWEXBGS": -3, "DCOILWTICO": -20, "VIX_HY": 54},
    "Soft Landing":     {"VIXCLS": -3, "DGS10": -0.3, "BAMLH0A0HYM2": -0.5, "T5YIE": -0.1, "DTWEXBGS": -1, "DCOILWTICO": -10, "VIX_HY": 1.5},
    "Financial Crisis": {"VIXCLS": 35, "DGS10": -1.5, "BAMLH0A0HYM2": 6.0, "T5YIE": -0.5, "DTWEXBGS": 5,  "DCOILWTICO": -30, "VIX_HY": 210},
    "Re-Acceleration":  {"VIXCLS": -2, "DGS10": 0.4,  "BAMLH0A0HYM2": -0.3, "T5YIE": 0.3, "DTWEXBGS": 2,  "DCOILWTICO": 15, "VIX_HY": 0.6},
    "Goldilocks":       {"VIXCLS": -5, "DGS10": -0.2, "BAMLH0A0HYM2": -0.5, "T5YIE": 0.0, "DTWEXBGS": -1, "DCOILWTICO": -5, "VIX_HY": 2.5},
}

# 6 base factors + 1 interaction term
FACTOR_SERIES = ["VIXCLS", "DGS10", "BAMLH0A0HYM2", "T5YIE", "DTWEXBGS", "DCOILWTICO"]

# ─────────────────────────────────────────────
# ENHANCED FACTOR-BETA PORTFOLIO MODEL
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def fetch_factor_data(fred_key: str, days: int) -> pd.DataFrame:
    """Fetch daily FRED factor series, compute daily changes + interaction term."""
    factor_frames = {}
    for sid in FACTOR_SERIES:
        df = fetch_fred_series(fred_key, sid, limit=days)
        if not df.empty:
            df = df.set_index("date")["value"]
            factor_frames[sid] = df
    if not factor_frames:
        return pd.DataFrame()
    factors = pd.DataFrame(factor_frames)
    factors = factors.sort_index().ffill()
    factor_changes = factors.diff().dropna()
    # Add VIX × HY spread interaction term (captures nonlinear crisis dynamics)
    if "VIXCLS" in factor_changes.columns and "BAMLH0A0HYM2" in factor_changes.columns:
        factor_changes["VIX_HY"] = factor_changes["VIXCLS"] * factor_changes["BAMLH0A0HYM2"]
    return factor_changes


from src.portfolio_models import (
    compute_factor_betas,
    estimate_regime_returns,
    compute_stressed_correlations,
    detect_sector_concentration,
    blend_estimates,
    SECTOR_MAP,
)


# ─────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────
ticker_list = [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]

@st.cache_data(ttl=3600)
def load_portfolio(tickers, days):
    data = {}
    failed = []
    for t in tickers:
        df = fetch_massive_data(format_massive_ticker(t), days)
        if df is not None and not df.empty:
            data[t] = df['Close']
        else:
            failed.append(t)
    return data, failed

# Auto-load on first visit or when button clicked
needs_load = run_btn or 'scenario_data' not in st.session_state
if needs_load:
    st.session_state["_scenario_running"] = True
    all_data, failed = load_portfolio(ticker_list, lookback)
    if failed:
        st.warning(f"Could not load: {', '.join(failed)}")
    if not all_data:
        st.error("No data loaded. Check tickers and try again.")
        st.stop()
    portfolio_df = pd.DataFrame(all_data).dropna()
    daily_returns = portfolio_df.pct_change().dropna()
    st.session_state.scenario_data = portfolio_df
    st.session_state.scenario_returns = daily_returns
    st.session_state.scenario_tickers = list(all_data.keys())
    st.session_state.scenario_value = portfolio_value
    st.session_state["_scenario_running"] = False

if 'scenario_data' in st.session_state:
    portfolio_df = st.session_state.scenario_data
    daily_returns = st.session_state.scenario_returns
    tickers = st.session_state.scenario_tickers
    port_val = st.session_state.scenario_value
    weights = np.full(len(tickers), 1.0 / len(tickers))

    # ═══════════════════════════════════════════
    # TAB LAYOUT
    # ═══════════════════════════════════════════
    tab6, tab5, tab1, tab2, tab3, tab4, tab7, tab8 = st.tabs([
        "🌐 Macro Portfolio Scenarios",
        "🏛️ Fed & Macro Drivers",
        "📉 Historical Stress Tests",
        "🎛️ Custom What-If",
        "📊 Bull / Base / Bear",
        "⚡ Event-Driven",
        "🔧 Model Diagnostics",
        "🎯 Regime Track Record",
    ])

    # ═══════════════════════════════════════════
    # TAB 1: HISTORICAL STRESS TESTS
    # ═══════════════════════════════════════════
    with tab1:
        st.subheader("Historical Stress Test Replay")
        st.caption("Apply realized drawdowns from major market crises to your current portfolio.")

        # Historical drawdowns sourced from peak-to-trough actual market data
        # SPY/QQQ: S&P 500 / Nasdaq 100 ETF total return drawdowns
        # TLT: iShares 20+ Year Treasury Bond ETF (launched Jul 2002)
        # USO: United States Oil Fund (launched Apr 2006)
        # GLD: SPDR Gold Shares (launched Nov 2004)
        # Where an ETF didn't exist for a period, the underlying index/commodity move is used
        HISTORICAL_SCENARIOS = {
            "2008 Financial Crisis (Sep-Nov 2008)": {"SPY": -0.46, "QQQ": -0.49, "TLT": 0.33, "USO": -0.68, "GLD": 0.05, "_default": -0.40},
            "COVID Crash (Feb-Mar 2020)": {"SPY": -0.34, "QQQ": -0.28, "TLT": 0.21, "USO": -0.80, "GLD": -0.03, "_default": -0.30},
            "2022 Rate Shock (Jan-Sep 2022)": {"SPY": -0.25, "QQQ": -0.33, "TLT": -0.31, "USO": 0.28, "GLD": -0.09, "_default": -0.20},
            "Dot-Com Bust (Mar 2000-Oct 2002)": {"SPY": -0.49, "QQQ": -0.83, "TLT": 0.20, "USO": 0.0, "GLD": 0.06, "_default": -0.40},
            "2011 Euro Debt Crisis (May-Oct)": {"SPY": -0.19, "QQQ": -0.16, "TLT": 0.28, "USO": -0.20, "GLD": 0.08, "_default": -0.15},
            "2015-16 China/Oil Selloff": {"SPY": -0.13, "QQQ": -0.13, "TLT": 0.05, "USO": -0.55, "GLD": 0.04, "_default": -0.10},
            "2018 Q4 Selloff (Oct-Dec)": {"SPY": -0.20, "QQQ": -0.23, "TLT": 0.06, "USO": -0.40, "GLD": 0.08, "_default": -0.15},
            "Oil Crash (Jun 2014-Feb 2016)": {"SPY": -0.03, "QQQ": 0.02, "TLT": 0.13, "USO": -0.77, "GLD": 0.03, "_default": -0.05},
        }

        selected_scenarios = st.multiselect(
            "Select crisis scenarios to replay",
            list(HISTORICAL_SCENARIOS.keys()),
            default=list(HISTORICAL_SCENARIOS.keys())[:3]
        )

        if selected_scenarios:
            stress_results = []
            for scenario_name in selected_scenarios:
                shocks = HISTORICAL_SCENARIOS[scenario_name]
                pnl = 0
                ticker_impacts = {}
                alloc_per_ticker = port_val / len(tickers)
                for t in tickers:
                    shock = shocks.get(t, shocks["_default"])
                    impact = alloc_per_ticker * shock
                    pnl += impact
                    ticker_impacts[t] = shock * 100

                stress_results.append({
                    "Scenario": scenario_name,
                    "Portfolio P&L ($)": pnl,
                    "Portfolio P&L (%)": (pnl / port_val) * 100,
                    **{f"{t} (%)": ticker_impacts.get(t, 0) for t in tickers}
                })

            stress_df = pd.DataFrame(stress_results)

            # Summary metrics
            worst = stress_df.loc[stress_df["Portfolio P&L ($)"].idxmin()]
            best = stress_df.loc[stress_df["Portfolio P&L ($)"].idxmax()]
            avg_pnl = stress_df["Portfolio P&L ($)"].mean()

            c1, c2, c3 = st.columns(3)
            c1.metric("Worst Case", f"${worst['Portfolio P&L ($)']:,.0f}", f"{worst['Portfolio P&L (%)']:.1f}%", delta_color="inverse")
            c2.metric("Best Case", f"${best['Portfolio P&L ($)']:,.0f}", f"{best['Portfolio P&L (%)']:.1f}%")
            c3.metric("Average Impact", f"${avg_pnl:,.0f}", f"{(avg_pnl/port_val)*100:.1f}%", delta_color="inverse")

            # Waterfall chart
            fig_stress = go.Figure()
            colors = ['#ff4444' if v < 0 else '#00cc66' for v in stress_df["Portfolio P&L ($)"]]
            fig_stress.add_trace(go.Bar(
                x=stress_df["Scenario"],
                y=stress_df["Portfolio P&L ($)"],
                marker_color=colors,
                text=[f"${v:,.0f}" for v in stress_df["Portfolio P&L ($)"]],
                textposition="outside"
            ))
            fig_stress.update_layout(
                template="plotly_dark", height=450, margin=dict(t=30, b=0, l=0, r=0),
                yaxis_title="Portfolio P&L ($)", xaxis_tickangle=-30
            )
            st.plotly_chart(fig_stress, use_container_width=True)

            # Heatmap of per-ticker impacts
            st.caption("Per-Asset Stress Impact (%)")
            heat_cols = [c for c in stress_df.columns if c.endswith("(%)") and c != "Portfolio P&L (%)"]
            heat_data = stress_df[heat_cols].values
            fig_heat = go.Figure(data=go.Heatmap(
                z=heat_data,
                x=[c.replace(" (%)", "") for c in heat_cols],
                y=stress_df["Scenario"],
                colorscale="RdYlGn",
                text=np.round(heat_data, 1),
                texttemplate="%{text}%",
                zmid=0
            ))
            fig_heat.update_layout(template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0))
            st.plotly_chart(fig_heat, use_container_width=True)

    # ═══════════════════════════════════════════
    # TAB 2: CUSTOM WHAT-IF
    # ═══════════════════════════════════════════
    with tab2:
        st.subheader("Custom What-If Scenario Builder")
        st.caption("Define custom percentage moves for each asset and see the portfolio impact.")

        col_left, col_right = st.columns([1, 2])

        with col_left:
            st.markdown("**Define Asset Shocks (%)**")
            custom_shocks = {}
            for t in tickers:
                custom_shocks[t] = st.slider(f"{t}", -80.0, 80.0, 0.0, 1.0, key=f"whatif_{t}")

            st.divider()
            st.markdown("**Macro Presets**")
            preset = st.selectbox("Apply a preset", [
                "— None —",
                "Risk-Off Flight to Safety",
                "Inflation Surge",
                "Rate Cut Rally",
                "Dollar Collapse",
                "Stagflation"
            ])

            PRESETS = {
                "Risk-Off Flight to Safety": {"SPY": -15, "QQQ": -20, "TLT": 10, "USO": -25, "GLD": 8},
                "Inflation Surge": {"SPY": -8, "QQQ": -12, "TLT": -15, "USO": 30, "GLD": 15},
                "Rate Cut Rally": {"SPY": 12, "QQQ": 18, "TLT": 8, "USO": 5, "GLD": 5},
                "Dollar Collapse": {"SPY": -5, "QQQ": -5, "TLT": -10, "USO": 20, "GLD": 25},
                "Stagflation": {"SPY": -15, "QQQ": -18, "TLT": -8, "USO": 15, "GLD": 12},
            }

            if preset != "— None —" and preset in PRESETS:
                for t in tickers:
                    if t in PRESETS[preset]:
                        custom_shocks[t] = PRESETS[preset][t]

        with col_right:
            # Calculate impact
            alloc = port_val / len(tickers)
            impacts = {t: alloc * (custom_shocks[t] / 100) for t in tickers}
            total_pnl = sum(impacts.values())
            new_val = port_val + total_pnl

            m1, m2, m3 = st.columns(3)
            m1.metric("Current Value", f"${port_val:,.0f}")
            m2.metric("Scenario Value", f"${new_val:,.0f}", f"{(total_pnl/port_val)*100:+.1f}%")
            m3.metric("Total P&L", f"${total_pnl:,.0f}", delta_color="inverse" if total_pnl < 0 else "normal")

            # Before/After bar chart
            before = [alloc] * len(tickers)
            after = [alloc + impacts[t] for t in tickers]

            fig_whatif = go.Figure()
            fig_whatif.add_trace(go.Bar(name="Before", x=tickers, y=before, marker_color="#555555"))
            fig_whatif.add_trace(go.Bar(name="After", x=tickers, y=after,
                marker_color=['#ff4444' if impacts[t] < 0 else '#00cc66' for t in tickers]))
            fig_whatif.update_layout(
                template="plotly_dark", barmode="group", height=400,
                margin=dict(t=30, b=0, l=0, r=0), yaxis_title="Allocation ($)"
            )
            st.plotly_chart(fig_whatif, use_container_width=True)

            # Contribution waterfall
            fig_contrib = go.Figure(go.Waterfall(
                x=tickers + ["Total"],
                y=[impacts[t] for t in tickers] + [total_pnl],
                measure=["relative"] * len(tickers) + ["total"],
                connector_line_color="#555555",
                increasing_marker_color="#00cc66",
                decreasing_marker_color="#ff4444",
                totals_marker_color="#00d1ff",
                text=[f"${v:+,.0f}" for v in [impacts[t] for t in tickers] + [total_pnl]],
                textposition="outside"
            ))
            fig_contrib.update_layout(
                template="plotly_dark", height=350, margin=dict(t=30, b=0, l=0, r=0),
                yaxis_title="P&L Contribution ($)"
            )
            st.plotly_chart(fig_contrib, use_container_width=True)

    # ═══════════════════════════════════════════
    # TAB 3: BULL / BASE / BEAR
    # ═══════════════════════════════════════════
    with tab3:
        st.subheader("Multi-Scenario Projection: Bull / Base / Bear")
        st.caption("Forward-looking projections with adjustable return assumptions and GBM simulation.")

        proj_col, chart_col = st.columns([1, 2])

        with proj_col:
            proj_days = st.slider("Projection Horizon (Days)", 30, 504, 252, key="proj_days")
            num_paths = st.selectbox("Simulation Paths", [100, 500, 1000], index=1, key="proj_paths")

            st.markdown("**Annualized Return Assumptions (%)**")
            bull_ret = st.number_input("Bull Case", value=25.0, step=5.0, key="bull_ret")
            base_ret = st.number_input("Base Case", value=10.0, step=5.0, key="base_ret")
            bear_ret = st.number_input("Bear Case", value=-20.0, step=5.0, key="bear_ret")

            proj_ticker = st.selectbox("Primary Ticker", tickers, key="proj_ticker")

        with chart_col:
            close_series = portfolio_df[proj_ticker]
            S0 = close_series.iloc[-1]
            log_rets = np.log(close_series / close_series.shift(1)).dropna()
            hist_vol = log_rets.std() * np.sqrt(252)

            rng = np.random.default_rng(42)
            scenarios = {"Bull": bull_ret, "Base": base_ret, "Bear": bear_ret}
            scenario_colors = {"Bull": "#00cc66", "Base": "#00d1ff", "Bear": "#ff4444"}

            fig_proj = go.Figure()

            # Plot last 60 days of history
            hist_tail = close_series.tail(60)
            x_hist = np.arange(-len(hist_tail) + 1, 1)
            fig_proj.add_trace(go.Scatter(
                x=x_hist, y=hist_tail, mode='lines',
                line=dict(color='white', width=2), name="History"
            ))

            summary_rows = []
            for name, annual_ret in scenarios.items():
                daily_drift = (annual_ret / 100) / 252
                daily_vol = hist_vol / np.sqrt(252)
                drift = daily_drift - 0.5 * daily_vol ** 2

                Z = rng.normal(0, 1, (proj_days, num_paths))
                daily_multipliers = np.exp(drift + daily_vol * Z)
                paths = np.vstack([np.ones(num_paths), np.cumprod(daily_multipliers, axis=0)]) * S0

                mean_path = np.mean(paths, axis=1)
                p10 = np.percentile(paths, 10, axis=1)
                p90 = np.percentile(paths, 90, axis=1)
                x_sim = np.arange(0, len(mean_path))

                # Confidence band
                fig_proj.add_trace(go.Scatter(
                    x=np.concatenate([x_sim, x_sim[::-1]]),
                    y=np.concatenate([p90, p10[::-1]]),
                    fill='toself', fillcolor=scenario_colors[name],
                    opacity=0.08, line=dict(width=0),
                    showlegend=False, hoverinfo='skip'
                ))
                fig_proj.add_trace(go.Scatter(
                    x=x_sim, y=mean_path, mode='lines',
                    line=dict(color=scenario_colors[name], width=2, dash='dash'),
                    name=f"{name} ({annual_ret:+.0f}%)"
                ))

                terminal = paths[-1, :]
                summary_rows.append({
                    "Scenario": name,
                    "Median Price": f"${np.median(terminal):,.2f}",
                    "Mean Price": f"${np.mean(terminal):,.2f}",
                    "10th Pct": f"${np.percentile(terminal, 10):,.2f}",
                    "90th Pct": f"${np.percentile(terminal, 90):,.2f}",
                    "P(Profit)": f"{np.mean(terminal > S0) * 100:.0f}%"
                })

            fig_proj.update_layout(
                template="plotly_dark", height=500, margin=dict(t=30, b=0, l=0, r=0),
                xaxis_title="Trading Days", yaxis_title="Price ($)"
            )
            st.plotly_chart(fig_proj, use_container_width=True)

            st.dataframe(pd.DataFrame(summary_rows).set_index("Scenario"), use_container_width=True)

    # ═══════════════════════════════════════════
    # TAB 4: EVENT-DRIVEN CATALYSTS
    # ═══════════════════════════════════════════
    with tab4:
        st.subheader("Event-Driven Catalyst Modeler")
        st.caption("Model the impact of specific catalysts with probability weighting.")

        evt_ticker = st.selectbox("Ticker to Model", tickers, key="evt_ticker")
        close_series_evt = portfolio_df[evt_ticker]
        S0_evt = close_series_evt.iloc[-1]
        log_rets_evt = np.log(close_series_evt / close_series_evt.shift(1)).dropna()
        daily_vol_evt = log_rets_evt.std()

        st.markdown(f"**{evt_ticker}** current price: **${S0_evt:,.2f}** | Daily vol: **{daily_vol_evt*100:.2f}%** | Annualized vol: **{daily_vol_evt*np.sqrt(252)*100:.1f}%**")

        st.divider()

        CATALYST_PRESETS = {
            "— Custom —": [],
            "FOMC Rate Decision": [
                {"name": "Dovish Cut (-50bp)", "prob": 15, "move": 3.0},
                {"name": "Standard Cut (-25bp)", "prob": 35, "move": 1.5},
                {"name": "Hold (No Change)", "prob": 35, "move": -0.5},
                {"name": "Hawkish Hold", "prob": 15, "move": -2.5},
            ],
            "Earnings Report": [
                {"name": "Blowout Beat", "prob": 15, "move": 8.0},
                {"name": "Modest Beat", "prob": 35, "move": 3.0},
                {"name": "In-Line", "prob": 20, "move": -1.0},
                {"name": "Modest Miss", "prob": 20, "move": -5.0},
                {"name": "Bad Miss + Guide Down", "prob": 10, "move": -12.0},
            ],
            "CPI / Inflation Print": [
                {"name": "Below Consensus (Dovish)", "prob": 25, "move": 2.0},
                {"name": "In-Line", "prob": 40, "move": 0.0},
                {"name": "Hot Print (Hawkish)", "prob": 25, "move": -2.5},
                {"name": "Shock Upside", "prob": 10, "move": -5.0},
            ],
            "Geopolitical Escalation": [
                {"name": "De-escalation", "prob": 20, "move": 3.0},
                {"name": "Status Quo", "prob": 40, "move": 0.0},
                {"name": "Minor Escalation", "prob": 25, "move": -3.0},
                {"name": "Major Escalation", "prob": 15, "move": -8.0},
            ],
        }

        cat_preset = st.selectbox("Catalyst Preset", list(CATALYST_PRESETS.keys()), key="cat_preset")

        st.markdown("**Define Outcome Branches**")

        num_outcomes = st.number_input("Number of Outcomes", 2, 8,
            value=len(CATALYST_PRESETS[cat_preset]) if cat_preset != "— Custom —" else 3, key="n_outcomes")

        outcomes = []
        cols = st.columns(min(int(num_outcomes), 4))
        for i in range(int(num_outcomes)):
            col = cols[i % len(cols)]
            with col:
                preset_data = CATALYST_PRESETS.get(cat_preset, [])
                defaults = preset_data[i] if i < len(preset_data) else {"name": f"Outcome {i+1}", "prob": 0, "move": 0.0}

                name = st.text_input("Label", value=defaults["name"], key=f"evt_name_{i}")
                prob = st.number_input("Probability (%)", 0.0, 100.0, float(defaults["prob"]), 5.0, key=f"evt_prob_{i}")
                move = st.number_input("Expected Move (%)", -50.0, 50.0, float(defaults["move"]), 0.5, key=f"evt_move_{i}")
                outcomes.append({"name": name, "prob": prob / 100, "move": move / 100})

        total_prob = sum(o["prob"] for o in outcomes)
        if abs(total_prob - 1.0) > 0.01:
            st.warning(f"Probabilities sum to {total_prob*100:.0f}% — should be ~100%.")

        if outcomes and total_prob > 0:
            st.divider()

            # Expected value calculation
            ev_move = sum(o["prob"] * o["move"] for o in outcomes)
            ev_price = S0_evt * (1 + ev_move)
            ev_pnl = (port_val / len(tickers)) * ev_move

            e1, e2, e3 = st.columns(3)
            e1.metric("Expected Move", f"{ev_move*100:+.2f}%")
            e2.metric("Expected Price", f"${ev_price:,.2f}")
            e3.metric("Expected P&L (this position)", f"${ev_pnl:+,.0f}")

            # Outcome visualization
            fig_evt = make_subplots(rows=1, cols=2, subplot_titles=["Outcome Distribution", "P&L by Outcome"],
                                    specs=[[{"type": "pie"}, {"type": "bar"}]])

            names = [o["name"] for o in outcomes]
            probs = [o["prob"] for o in outcomes]
            moves = [o["move"] * 100 for o in outcomes]
            pnls = [(port_val / len(tickers)) * o["move"] for o in outcomes]

            fig_evt.add_trace(go.Pie(
                labels=names, values=probs,
                marker=dict(colors=px.colors.qualitative.Set2),
                textinfo="label+percent", hole=0.4
            ), row=1, col=1)

            bar_colors = ['#00cc66' if m > 0 else '#ff4444' for m in moves]
            fig_evt.add_trace(go.Bar(
                x=names, y=pnls, marker_color=bar_colors,
                text=[f"${v:+,.0f}" for v in pnls], textposition="outside",
                showlegend=False
            ), row=1, col=2)

            fig_evt.update_layout(template="plotly_dark", height=400, margin=dict(t=40, b=0, l=0, r=0))
            st.plotly_chart(fig_evt, use_container_width=True)

            # Price outcome ladder
            st.caption("Price Outcome Ladder")
            ladder_data = []
            for o in sorted(outcomes, key=lambda x: x["move"], reverse=True):
                price = S0_evt * (1 + o["move"])
                ladder_data.append({
                    "Outcome": o["name"],
                    "Probability": f"{o['prob']*100:.0f}%",
                    "Move": f"{o['move']*100:+.1f}%",
                    "Target Price": f"${price:,.2f}",
                    "Position P&L": f"${(port_val/len(tickers))*o['move']:+,.0f}"
                })
            st.dataframe(pd.DataFrame(ladder_data).set_index("Outcome"), use_container_width=True)

    # ═══════════════════════════════════════════
    # SHARED: LOAD FRED DATA (used by tabs 5 & 6)
    # ═══════════════════════════════════════════
    driver_data = {}
    if fred_key:
        with fun_loader("data"):
            for sid, info in FED_DRIVERS.items():
                df = fetch_fred_series(fred_key, sid, limit=60)
                if not df.empty:
                    driver_data[sid] = df

    # ═══════════════════════════════════════════
    # SHARED: FACTOR MODEL (used by tabs 6 & 7)
    # ═══════════════════════════════════════════
    factor_betas = {}
    factor_changes = pd.DataFrame()
    corr_matrices = {"normal": None, "stressed": None}
    if fred_key:
        factor_changes = fetch_factor_data(fred_key, lookback)
        if not factor_changes.empty:
            factor_betas = compute_factor_betas(daily_returns, factor_changes)
            if factor_betas:
                corr_matrices = compute_stressed_correlations(daily_returns, factor_changes)

    # ═══════════════════════════════════════════
    # TAB 5: FED & MACRO DRIVERS (now a separate page)
    # ═══════════════════════════════════════════
    with tab5:
        st.subheader("Fed & Macro Drivers")
        st.markdown("This section has been expanded into its own dedicated page with more detail and better layout.")
        if st.button("Open Fed & Macro Drivers", type="primary", use_container_width=True, key="goto_fed_macro"):
            st.switch_page("pages/21_Fed_Macro_Drivers.py")

        # Still show the scorecard summary inline for quick reference
        if not fred_key:
            st.warning("FRED API key not configured.")
        elif driver_data:
            # ── Fed Dual Mandate Scorecard ──
            st.markdown("### Fed Dual Mandate Scorecard")
            st.caption("The Fed's two mandates: **maximum employment** and **price stability** (2% inflation target)")

            sc1, sc2, sc3, sc4, sc5 = st.columns(5)

            # Core PCE YoY
            if "PCEPILFE" in driver_data and len(driver_data["PCEPILFE"]) >= 13:
                df_pce = driver_data["PCEPILFE"]
                pce_yoy = ((df_pce.iloc[-1]["value"] / df_pce.iloc[-13]["value"]) - 1) * 100
                prev_pce = ((df_pce.iloc[-2]["value"] / df_pce.iloc[-14]["value"]) - 1) * 100 if len(df_pce) >= 14 else pce_yoy
                sc1.metric("Core PCE YoY", f"{pce_yoy:.1f}%", f"{pce_yoy - prev_pce:+.1f}%", delta_color="inverse")

            # Unemployment
            if "UNRATE" in driver_data:
                df_ur = driver_data["UNRATE"]
                ur = df_ur.iloc[-1]["value"]
                ur_prev = df_ur.iloc[-2]["value"] if len(df_ur) > 1 else ur
                sc2.metric("Unemployment", f"{ur:.1f}%", f"{ur - ur_prev:+.1f}%", delta_color="inverse")

            # Fed Funds
            if "FEDFUNDS" in driver_data:
                df_ff = driver_data["FEDFUNDS"]
                ff = df_ff.iloc[-1]["value"]
                ff_prev = df_ff.iloc[-2]["value"] if len(df_ff) > 1 else ff
                sc3.metric("Fed Funds Rate", f"{ff:.2f}%", f"{ff - ff_prev:+.2f}%")

            # 2s10s
            if "T10Y2Y" in driver_data:
                df_sp = driver_data["T10Y2Y"]
                spread = df_sp.iloc[-1]["value"]
                sc4.metric("2s10s Spread", f"{spread:.2f}%", "Inverted" if spread < 0 else "Normal",
                          delta_color="inverse" if spread < 0 else "normal")

            # NFP — PAYEMS is total nonfarm payrolls in thousands,
            # so the MoM diff is already in thousands of jobs
            if "PAYEMS" in driver_data and len(driver_data["PAYEMS"]) > 1:
                df_nfp = driver_data["PAYEMS"]
                nfp_change = df_nfp.iloc[-1]["value"] - df_nfp.iloc[-2]["value"]
                sc5.metric("NFP Change (MoM)", f"{nfp_change:+,.0f}K jobs")

            st.caption("Full signal matrix, trend charts, dot plot, and projections available on the dedicated page.")

    # ═══════════════════════════════════════════
    # (removed: signal matrix, sparklines, dot plot, SEP, polymarket, stocktwits, reaction function
    #  — all moved to pages/21_Fed_Macro_Drivers.py)
    _fed_tab_placeholder = None
    if False:
        pass  # old tab5 content removed — now in pages/21_Fed_Macro_Drivers.py
        _dead = driver_data  # reference to keep linter quiet
    # TAB 6: MACRO PORTFOLIO SCENARIOS
    # ═══════════════════════════════════════════
    with tab6:
        st.subheader("Macro-Driven Portfolio Scenario Engine")
        st.caption("Translate macroeconomic regime shifts into portfolio P&L using driver sensitivity analysis.")

        if not fred_key:
            st.warning("FRED API key not configured.")
        else:
            # ── Grok AI Analysis (auto-polls hourly) ──
            grok_result = None
            if grok_key and driver_data:
                st.markdown("### AI-Powered Regime Analysis")

                # Auto-run if stale (>1hr since last), otherwise load cached
                with fun_loader("ai"):
                    grok_result = run_grok_if_stale(grok_key, driver_data, FED_DRIVERS, MACRO_REGIMES, fred_api_key=fred_key, ticker_list=tickers)

                if grok_result and grok_result.get("success"):
                    grok_regimes = {r["name"]: r for r in grok_result.get("regimes", [])}
                    from_cache = grok_result.get("from_cache", False)
                    ts = grok_result.get("timestamp", "")

                    # Status line
                    status = "Loaded from cache" if from_cache else "Fresh analysis"
                    try:
                        ts_display = pd.Timestamp(ts).strftime("%b %d, %Y %I:%M %p")
                    except Exception:
                        ts_display = ts
                    st.caption(f"{status} | Last updated: **{ts_display}** | Auto-refreshes hourly")

                    # X/Twitter sentiment summary with freshness indicator
                    sentiment = grok_result.get("sentiment_summary", "")
                    if sentiment:
                        try:
                            age_min = (datetime.now() - datetime.fromisoformat(ts)).total_seconds() / 60
                            if age_min < 10:
                                fresh_label = "just now"
                            elif age_min < 60:
                                fresh_label = f"{age_min:.0f}m ago"
                            else:
                                fresh_label = f"{age_min/60:.1f}h ago"
                        except Exception:
                            fresh_label = ""
                        st.info(f"**X/Twitter Sentiment Pulse** ({fresh_label}): {sentiment}")

                    # Change summary from prior assessment
                    change_summary = grok_result.get("change_summary", "")
                    if change_summary:
                        st.warning(f"**Change from Prior Assessment:** {change_summary}")

                    # Force refresh button — admin only
                    from src.auth import _admin_emails
                    is_admin = st.session_state.get("user_email", "").lower() in _admin_emails()
                    if is_admin and st.button("Force Refresh Now", key="grok_force_refresh"):
                        try:
                            fred_summary = build_fred_summary(driver_data, FED_DRIVERS, fred_key_ref=fred_key)
                            regime_info = json.dumps([
                                {"name": name, "description": data["description"]}
                                for name, data in MACRO_REGIMES.items()
                            ], indent=2)
                            fresh = _call_grok_api(grok_key, fred_summary, regime_info)
                            if fresh["success"]:
                                save_grok_result(fresh)
                                grok_result = fresh
                                grok_result["timestamp"] = pd.Timestamp.now().isoformat()
                                grok_result["from_cache"] = False
                                grok_regimes = {r["name"]: r for r in grok_result.get("regimes", [])}
                                st.rerun()
                        except Exception as e:
                            st.error(f"Refresh failed: {e}")

                    # Comparison cards — full text visible
                    for regime, data in MACRO_REGIMES.items():
                        ai = grok_regimes.get(regime, {})
                        ai_prob = ai.get("probability", data["probability"])
                        base_prob = data["probability"]
                        diff = ai_prob - base_prob
                        diff_color = "green" if diff > 0 else "red" if diff < 0 else "gray"
                        rationale = ai.get("rationale", "—")

                        st.markdown(
                            f"**{regime}** &nbsp; | &nbsp; "
                            f"Base: {base_prob}% &nbsp; | &nbsp; "
                            f"AI: **{ai_prob}%** &nbsp; | &nbsp; "
                            f"Shift: :{diff_color}[{diff:+d}pp]"
                        )
                        st.caption(rationale)
                        st.divider()

                    # ── Probability History Chart ──
                    history = load_grok_history()
                    if len(history) >= 1:
                        st.markdown("### Regime Probability Over Time")
                        hdr_col1, hdr_col2 = st.columns([3, 1])
                        with hdr_col1:
                            st.caption(f"{len(history)} data points collected")
                        with hdr_col2:
                            chart_mode = st.radio("View", ["Line", "Stacked Area"], horizontal=True,
                                                  key="prob_chart_mode", label_visibility="collapsed")

                        # Build history dataframe
                        hist_rows = []
                        for entry in history:
                            ts_val = entry.get("timestamp", "")
                            for r in entry.get("regimes", []):
                                hist_rows.append({
                                    "timestamp": pd.Timestamp(ts_val),
                                    "regime": r["name"],
                                    "probability": r.get("probability", 0),
                                })

                        if hist_rows:
                            hist_df = pd.DataFrame(hist_rows)
                            # Sort regimes by latest probability descending for stacking order
                            latest_ts = hist_df["timestamp"].max()
                            regime_order = (hist_df[hist_df["timestamp"] == latest_ts]
                                           .sort_values("probability", ascending=False)["regime"].tolist())
                            # Fallback if no latest
                            if not regime_order:
                                regime_order = hist_df["regime"].unique().tolist()

                            regime_colors = {
                                "Stagflation": "#ff4444",
                                "Recession": "#ff8c00",
                                "Soft Landing": "#00cc66",
                                "Financial Crisis": "#ff0066",
                                "Re-Acceleration": "#00d1ff",
                                "Goldilocks": "#aa66ff",
                            }

                            fig_hist = go.Figure()

                            if chart_mode == "Line":
                                for regime_name in regime_order:
                                    rd = hist_df[hist_df["regime"] == regime_name].sort_values("timestamp")
                                    fig_hist.add_trace(go.Scatter(
                                        x=rd["timestamp"], y=rd["probability"],
                                        mode="lines+markers", name=regime_name,
                                        line=dict(color=regime_colors.get(regime_name, "#888"), width=2.5),
                                        marker=dict(size=6),
                                        hovertemplate="%{y:.0f}%<extra>%{fullData.name}</extra>",
                                    ))

                                y_min = max(0, hist_df["probability"].min() - 5)
                                y_max = min(100, hist_df["probability"].max() + 5)
                                fig_hist.update_layout(
                                    yaxis=dict(range=[y_min, y_max], dtick=5, title="Probability (%)"),
                                )
                            else:
                                # Stacked area — regimes stacked to 100%
                                for regime_name in reversed(regime_order):
                                    rd = hist_df[hist_df["regime"] == regime_name].sort_values("timestamp")
                                    fig_hist.add_trace(go.Scatter(
                                        x=rd["timestamp"], y=rd["probability"],
                                        mode="lines", name=regime_name,
                                        line=dict(width=0.5, color=regime_colors.get(regime_name, "#888")),
                                        fillcolor=regime_colors.get(regime_name, "#888"),
                                        stackgroup="one",
                                        hovertemplate="%{y:.0f}%<extra>%{fullData.name}</extra>",
                                    ))

                                fig_hist.update_layout(
                                    yaxis=dict(range=[0, 100], dtick=10, title="Cumulative Probability (%)"),
                                )

                            fig_hist.update_layout(
                                template="plotly_dark", height=420,
                                margin=dict(t=30, b=0, l=0, r=0),
                                hovermode="x unified",
                                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                            )
                            st.plotly_chart(fig_hist, use_container_width=True)

                    # ── Sentiment History Log ──
                    sentiment_entries = [(e.get("timestamp", ""), e.get("sentiment_summary", ""))
                                        for e in reversed(history) if e.get("sentiment_summary")]
                    if sentiment_entries:
                        with st.expander(f"Sentiment History (last {min(3, len(sentiment_entries))})", expanded=False):
                            for ts_str, sent in sentiment_entries[:3]:
                                try:
                                    ts_fmt = pd.Timestamp(ts_str).strftime("%b %d %I:%M %p")
                                except Exception:
                                    ts_fmt = ts_str
                                st.markdown(f"**{ts_fmt}** — {sent}")

                    st.caption("Powered by Grok 3 (xAI) with live X/Twitter search. "
                              "Auto-updates hourly using FRED data + real-time social sentiment. "
                              "AI-generated analysis — not financial advice.")

                elif grok_result:
                    st.warning(f"Grok analysis unavailable: {grok_result.get('error', 'Unknown error')}")

                st.divider()

            # ── Regime Overview ──
            st.markdown("### Macro Regime Definitions")
            st.caption("Probabilities calibrated to current conditions: Iran/Hormuz oil shock, "
                       "Fed on hold at 3.50-3.75%, Feb NFP -92K, tariffs at 10.5% effective rate.")

            for regime, data in MACRO_REGIMES.items():
                with st.expander(f"**{regime}** — {data['probability']}%"):
                    st.markdown(f"**Scenario:** {data['description']}")
                    st.markdown(f"**Probability Rationale:** {data.get('rationale', '—')}")

            st.divider()

            # ── User Probability Adjustment ──
            # If Grok result exists, use AI probabilities as defaults; otherwise use hardcoded base
            grok_probs = {}
            if grok_result and grok_result.get("success"):
                grok_probs = {r["name"]: r.get("probability", 0) for r in grok_result.get("regimes", [])}

            st.markdown("### Adjust Regime Probabilities")
            if grok_probs:
                st.caption("Defaults set to Grok AI estimates. Adjust to your own conviction.")
            else:
                st.caption("Override the base probabilities with your own conviction. Probabilities should sum to ~100%.")

            prob_cols = st.columns(len(MACRO_REGIMES))
            user_probs = {}
            for col, (regime, data) in zip(prob_cols, MACRO_REGIMES.items()):
                default_prob = grok_probs.get(regime, data["probability"])
                with col:
                    user_probs[regime] = col.number_input(
                        regime, min_value=0, max_value=100,
                        value=default_prob, step=5, key=f"macro_prob_{regime}"
                    )

            total_macro_prob = sum(user_probs.values())
            if abs(total_macro_prob - 100) > 1:
                st.warning(f"Probabilities sum to {total_macro_prob}% — should be ~100%.")

            st.divider()

            # ── Driver Shift Comparison ──
            st.markdown("### Economic Driver Shifts by Regime")
            st.caption("How each key economic indicator moves under each macro regime")

            # Build comparison matrix with unit-aware formatting and directional arrows
            driver_shift_rows = []
            # Parallel numeric matrix for styling
            driver_shift_numeric = []
            for sid, info in FED_DRIVERS.items():
                row = {"Driver": info["name"], "Category": info["category"]}
                num_row = {"Driver": info["name"], "Category": info["category"]}
                for regime, rdata in MACRO_REGIMES.items():
                    move = rdata["driver_moves"].get(sid, 0)
                    arrow = "▲" if move > 0 else "▼" if move < 0 else "—"
                    if info["unit"] == "%" or info["yoy"]:
                        row[regime] = f"{arrow} {move:+.1f}%"
                    elif info["unit"] in ("K", "$M"):
                        row[regime] = f"{arrow} {move:+,.0f}"
                    else:
                        row[regime] = f"{arrow} {move:+.1f}"
                    num_row[regime] = move
                driver_shift_rows.append(row)
                driver_shift_numeric.append(num_row)

            display_df = pd.DataFrame(driver_shift_rows).set_index("Driver")
            numeric_df = pd.DataFrame(driver_shift_numeric).set_index("Driver")
            regime_cols = list(MACRO_REGIMES.keys())

            # Color cells: green for positive moves, red for negative, intensity by magnitude
            def _color_cell(val):
                try:
                    num = float(str(val).replace("▲", "").replace("▼", "").replace("—", "0").replace("%", "").replace(",", "").strip())
                except ValueError:
                    return ""
                if num > 0:
                    alpha = min(0.5, abs(num) / 10 * 0.5)
                    return f"background-color: rgba(0, 204, 102, {alpha}); color: #00ff96"
                elif num < 0:
                    alpha = min(0.5, abs(num) / 10 * 0.5)
                    return f"background-color: rgba(255, 68, 68, {alpha}); color: #ff6666"
                return "color: #888"

            styled = display_df.style.applymap(_color_cell, subset=regime_cols)
            st.dataframe(styled, use_container_width=True, height=560)

            st.divider()

            # ── Portfolio Impact by Regime (Two-Layer Model) ──
            st.markdown(f"### Portfolio Impact by Regime — {horizon_label} Horizon")

            alloc_per = port_val / len(tickers)

            # LAYER 1: Data-driven factor betas (computed in shared scope above)
            horizon_scale = horizon_days / 252
            data_estimates = {}
            if factor_betas:
                scaled_factor_moves = {
                    regime: {f: v * horizon_scale for f, v in fmoves.items()}
                    for regime, fmoves in REGIME_FACTOR_MOVES.items()
                }
                data_estimates = estimate_regime_returns(
                    factor_betas, scaled_factor_moves,
                    daily_returns=daily_returns, factor_changes=factor_changes,
                    horizon_days=horizon_days,
                )

            # LAYER 2: Grok AI asset estimates
            # LAYER 2: Grok AI asset estimates (Grok estimates are 12-month; scale to horizon)
            ai_asset_estimates = {}
            if grok_result and grok_result.get("success"):
                raw_ai = grok_result.get("asset_estimates", {})
                # Scale AI estimates from 12-month base to selected horizon
                ai_scale = horizon_days / 252
                for regime, ticker_ests in raw_ai.items():
                    ai_asset_estimates[regime] = {}
                    for t, val in ticker_ests.items():
                        try:
                            ai_asset_estimates[regime][t] = float(val) * ai_scale
                        except (ValueError, TypeError):
                            pass

            # BLEND
            if data_estimates:
                blended = blend_estimates(data_estimates, ai_asset_estimates, factor_betas)
            elif ai_asset_estimates:
                # No factor model — use AI only (already horizon-scaled above)
                blended = {}
                for regime in MACRO_REGIMES:
                    blended[regime] = {}
                    for t in tickers:
                        fallback = MACRO_REGIMES[regime]["asset_betas"].get(t, MACRO_REGIMES[regime]["asset_betas"]["_default"]) * horizon_scale
                        ai_val = ai_asset_estimates.get(regime, {}).get(t, fallback)
                        ci_width = max(5, abs(float(ai_val)) * 0.3)
                        blended[regime][t] = {"point": float(ai_val), "lo": float(ai_val) - ci_width, "hi": float(ai_val) + ci_width,
                                              "r2": 0, "source": "AI-estimated"}
            else:
                # Fallback to hardcoded betas, scaled to horizon
                blended = {}
                for regime, rdata in MACRO_REGIMES.items():
                    blended[regime] = {}
                    betas = rdata["asset_betas"]
                    for t in tickers:
                        val = float(betas.get(t, betas["_default"])) * horizon_scale
                        ci_width = max(5, abs(val) * 0.3)
                        blended[regime][t] = {"point": val, "lo": val - ci_width, "hi": val + ci_width,
                                              "r2": 0, "source": "hardcoded fallback"}

            # Compute portfolio P&L from blended estimates
            regime_results = []
            for regime in MACRO_REGIMES:
                pnl = 0
                pnl_lo = 0
                pnl_hi = 0
                ticker_moves = {}
                for t in tickers:
                    est = blended.get(regime, {}).get(t, {"point": 0, "lo": -5, "hi": 5})
                    move_pct = est["point"]
                    impact = alloc_per * (move_pct / 100)
                    impact_lo = alloc_per * (est["lo"] / 100)
                    impact_hi = alloc_per * (est["hi"] / 100)
                    pnl += impact
                    pnl_lo += impact_lo
                    pnl_hi += impact_hi
                    ticker_moves[t] = est

                regime_results.append({
                    "regime": regime,
                    "pnl": pnl, "pnl_lo": pnl_lo, "pnl_hi": pnl_hi,
                    "pnl_pct": (pnl / port_val) * 100,
                    "prob": user_probs[regime] / 100,
                    "ticker_moves": ticker_moves,
                })

            # Summary metrics
            ev_pnl = sum(r["pnl"] * r["prob"] for r in regime_results)
            ev_lo = sum(r["pnl_lo"] * r["prob"] for r in regime_results)
            ev_hi = sum(r["pnl_hi"] * r["prob"] for r in regime_results)
            worst_regime = min(regime_results, key=lambda r: r["pnl"])
            best_regime = max(regime_results, key=lambda r: r["pnl"])

            rm1, rm2, rm3, rm4 = st.columns(4)
            rm1.metric("Probability-Weighted EV", f"${ev_pnl:+,.0f}", f"{(ev_pnl/port_val)*100:+.1f}%")
            rm2.metric(f"Best: {best_regime['regime']}", f"${best_regime['pnl']:+,.0f}", f"{best_regime['pnl_pct']:+.1f}%")
            rm3.metric(f"Worst: {worst_regime['regime']}", f"${worst_regime['pnl']:+,.0f}", f"{worst_regime['pnl_pct']:+.1f}%",
                      delta_color="inverse")
            rm4.metric("Expected Portfolio Value", f"${port_val + ev_pnl:,.0f}")

            st.caption(f"{horizon_label} horizon | 80% confidence range: ${ev_lo:+,.0f} to ${ev_hi:+,.0f}")

            # Regime P&L bar chart with error bars
            fig_regime_pnl = go.Figure()
            regime_labels = [r["regime"] for r in regime_results]
            regime_pnls = [r["pnl"] for r in regime_results]
            regime_lo = [r["pnl_lo"] for r in regime_results]
            regime_hi = [r["pnl_hi"] for r in regime_results]
            regime_probs = [r["prob"] * 100 for r in regime_results]
            bar_colors = ['#00cc66' if p > 0 else '#ff4444' for p in regime_pnls]

            fig_regime_pnl.add_trace(go.Bar(
                x=regime_labels, y=regime_pnls, marker_color=bar_colors,
                error_y=dict(
                    type="data", symmetric=False,
                    array=[h - p for h, p in zip(regime_hi, regime_pnls)],
                    arrayminus=[p - l for p, l in zip(regime_pnls, regime_lo)],
                    color="rgba(255,255,255,0.4)",
                ),
                text=[f"${v:+,.0f}<br>({p:.0f}%)" for v, p in zip(regime_pnls, regime_probs)],
                textposition="outside", name="P&L"
            ))
            fig_regime_pnl.add_hline(y=ev_pnl, line_dash="dash", line_color="#00d1ff",
                                     annotation_text=f"EV: ${ev_pnl:+,.0f}")
            fig_regime_pnl.update_layout(
                template="plotly_dark", height=450, margin=dict(t=30, b=0, l=0, r=0),
                yaxis_title="Portfolio P&L ($)", xaxis_tickangle=-15
            )
            st.plotly_chart(fig_regime_pnl, use_container_width=True)

            # Per-asset detail table
            st.markdown(f"### Per-Asset Regime Sensitivity — {horizon_label}")
            st.caption(f"Estimated {horizon_label.lower()} returns per ticker per regime with confidence intervals and source")

            for regime in MACRO_REGIMES:
                with st.expander(f"**{regime}**"):
                    detail_rows = []
                    for t in tickers:
                        est = blended.get(regime, {}).get(t, {})
                        detail_rows.append({
                            "Ticker": t,
                            "Est. Return": f"{est.get('point') or 0:+.1f}%",
                            "80% CI": f"{est.get('lo') or 0:+.1f}% to {est.get('hi') or 0:+.1f}%",
                            "Est. P&L": f"${alloc_per * (est.get('point') or 0) / 100:+,.0f}",
                            "R²": f"{est.get('r2') or 0:.2f}" if (est.get('r2') or 0) > 0 else "—",
                            "Source": est.get("source", "—"),
                        })
                    st.dataframe(pd.DataFrame(detail_rows).set_index("Ticker"), use_container_width=True)

            st.divider()

            # ── EV Decomposition ──
            st.markdown("### Expected Value Decomposition by Regime")
            st.caption("How each regime contributes to the probability-weighted expected P&L. "
                       "This is a weighted average — in reality you land in ONE regime, not a blend of all.")

            fig_waterfall = go.Figure()
            ev_contributions = [(r["regime"], r["pnl"] * r["prob"] / port_val * 100) for r in regime_results]
            ev_pnl_pct = ev_pnl / port_val * 100
            ev_contributions.sort(key=lambda x: x[1], reverse=True)

            fig_waterfall.add_trace(go.Waterfall(
                x=[e[0] for e in ev_contributions] + ["Expected Return"],
                y=[e[1] for e in ev_contributions] + [ev_pnl_pct],
                measure=["relative"] * len(ev_contributions) + ["total"],
                connector_line_color="#555555",
                increasing_marker_color="#00cc66",
                decreasing_marker_color="#ff4444",
                totals_marker_color="#00d1ff",
                text=[f"{v:+.1f}%" for _, v in ev_contributions] + [f"{ev_pnl_pct:+.1f}%"],
                textposition="outside"
            ))
            fig_waterfall.update_layout(
                template="plotly_dark", height=400, margin=dict(t=30, b=0, l=0, r=0),
                yaxis_title="EV Contribution (%)"
            )
            st.plotly_chart(fig_waterfall, use_container_width=True)

            # Summary table
            summary_table = []
            for r in regime_results:
                summary_table.append({
                    "Regime": r["regime"],
                    "Probability": f"{r['prob']*100:.0f}%",
                    "P&L (Point)": f"${r['pnl']:+,.0f}",
                    "P&L (80% CI)": f"${r['pnl_lo']:+,.0f} to ${r['pnl_hi']:+,.0f}",
                    "Return": f"{r['pnl_pct']:+.1f}%",
                    "EV Contribution": f"${r['pnl'] * r['prob']:+,.0f}",
                })
            st.dataframe(pd.DataFrame(summary_table).set_index("Regime"), use_container_width=True)

            # ── Monte Carlo Outcome Simulation ──
            st.divider()
            st.markdown(f"### Simulated Outcome Distribution — {horizon_label}")
            st.caption(f"10,000 Monte Carlo draws over a {horizon_label.lower()} horizon: randomly select a regime "
                       f"(weighted by probability), then draw a P&L from that regime's distribution. "
                       f"Shows the true shape of possible outcomes.")

            n_sims = 10000
            rng_mc = np.random.default_rng(42)

            # Build regime parameters for sampling
            regime_names_mc = [r["regime"] for r in regime_results]
            regime_probs_mc = np.array([r["prob"] for r in regime_results])
            regime_probs_mc = regime_probs_mc / regime_probs_mc.sum()  # ensure sums to 1
            regime_points = np.array([r["pnl"] for r in regime_results])
            # Recover sigma from 80% CI (lo to hi spans 2 * 1.476 * sigma for t-dist df=5)
            regime_sigmas = np.array([
                max(1, (r["pnl_hi"] - r["pnl_lo"]) / (2 * 1.476)) for r in regime_results
            ])

            # Step 1: Draw regime index for each simulation
            regime_draws = rng_mc.choice(len(regime_results), size=n_sims, p=regime_probs_mc)

            # Step 2: Draw P&L from each regime's distribution (Student-t, df=5 for fat tails)
            from scipy.stats import t as t_dist
            sim_pnls = np.zeros(n_sims)
            for i in range(n_sims):
                idx = regime_draws[i]
                # Student-t with df=5: fatter tails than normal
                sim_pnls[i] = regime_points[idx] + regime_sigmas[idx] * t_dist.rvs(df=5, random_state=rng_mc)

            # Metrics
            mc_mean = np.mean(sim_pnls)
            mc_median = np.median(sim_pnls)
            mc_var_95 = np.percentile(sim_pnls, 5)  # 5th percentile = 95% VaR
            mc_cvar_95 = np.mean(sim_pnls[sim_pnls <= mc_var_95])  # CVaR = avg of tail
            mc_p10 = np.percentile(sim_pnls, 10)
            mc_p90 = np.percentile(sim_pnls, 90)
            prob_loss = np.mean(sim_pnls < 0) * 100
            prob_gain = np.mean(sim_pnls > 0) * 100

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("Simulated Mean", f"${mc_mean:+,.0f}")
            mc2.metric("95% VaR", f"${mc_var_95:+,.0f}",
                      help="With 95% confidence, losses will not exceed this amount")
            mc3.metric("95% CVaR (Expected Shortfall)", f"${mc_cvar_95:+,.0f}",
                      help="Average loss in the worst 5% of scenarios")
            mc4.metric("Probability of Loss", f"{prob_loss:.0f}%")

            # Histogram
            fig_mc = go.Figure()
            fig_mc.add_trace(go.Histogram(
                x=sim_pnls, nbinsx=80,
                marker_color="#00d1ff", opacity=0.7, name="Simulated P&L",
            ))

            # Reference lines
            fig_mc.add_vline(x=0, line_dash="solid", line_color="white", line_width=1,
                            annotation_text="Breakeven", annotation_position="top")
            fig_mc.add_vline(x=mc_mean, line_dash="dash", line_color="yellow", line_width=2,
                            annotation_text=f"Mean: ${mc_mean:+,.0f}", annotation_position="top left")
            fig_mc.add_vline(x=mc_var_95, line_dash="dash", line_color="#ff4444", line_width=2,
                            annotation_text=f"95% VaR: ${mc_var_95:+,.0f}", annotation_position="top left")

            # Shade loss region
            fig_mc.add_vrect(x0=min(sim_pnls), x1=0, fillcolor="rgba(255, 68, 68, 0.08)", line_width=0)

            fig_mc.update_layout(
                template="plotly_dark", height=420,
                margin=dict(t=30, b=0, l=0, r=0),
                xaxis_title="Portfolio P&L ($)",
                yaxis_title="Frequency",
                bargap=0.02,
                showlegend=False,
            )
            st.plotly_chart(fig_mc, use_container_width=True)

            # Percentile table
            pct_data = {
                "Percentile": ["1st (Catastrophic)", "5th (95% VaR)", "10th", "25th",
                               "50th (Median)", "75th", "90th", "95th", "99th"],
                "P&L": [f"${np.percentile(sim_pnls, p):+,.0f}" for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]],
                "Return": [f"{np.percentile(sim_pnls, p)/port_val*100:+.1f}%" for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]],
                "Portfolio Value": [f"${port_val + np.percentile(sim_pnls, p):,.0f}" for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]],
            }
            st.dataframe(pd.DataFrame(pct_data).set_index("Percentile"), use_container_width=True)

            # Regime contribution breakdown
            st.caption("**Regime Attribution:** Which regime drives which part of the distribution")
            regime_draw_counts = np.bincount(regime_draws, minlength=len(regime_results))
            for i, r in enumerate(regime_results):
                regime_sims = sim_pnls[regime_draws == i]
                if len(regime_sims) > 0:
                    st.caption(
                        f"**{r['regime']}** ({r['prob']*100:.0f}% prob, drawn {regime_draw_counts[i]:,}x) — "
                        f"Mean: ${np.mean(regime_sims):+,.0f}, "
                        f"Range: ${np.min(regime_sims):+,.0f} to ${np.max(regime_sims):+,.0f}"
                    )

            st.divider()
            st.caption(
                "**Methodology:** Portfolio returns estimated using an enhanced two-layer model: "
                "(1) Exponentially-weighted OLS regression (halflife=60d) of each ticker against 7 macro factors "
                "(VIX, 10Y yield, HY spreads, 5Y breakeven inflation, dollar, crude oil, VIX×HY interaction). "
                "Block bootstrap from regime-like historical periods provides non-parametric return estimates "
                "blended 50/50 with factor model. Confidence intervals use Student-t (df=5) for fat tails. "
                "Stressed residual std used for downside regimes (recession, crisis, stagflation). "
                "(2) Grok AI estimates based on each ticker's sector exposure and historical behavior. "
                "Blended using R²-adaptive + beta-stability-adjusted weighting. "
                "See Model Diagnostics tab for full evaluation. This is not financial advice."
            )

    # ═══════════════════════════════════════════
    # TAB 7: MODEL DIAGNOSTICS
    # ═══════════════════════════════════════════
    with tab7:
        st.subheader("Model Diagnostics & Risk Flags")
        st.caption("Evaluate the factor model quality, beta stability, tail risk, and portfolio concentration.")

        if not factor_betas:
            st.info("Run the analysis first to see diagnostics. Factor model requires portfolio data.")
        else:
            avg_r2 = np.mean([v["r2"] for v in factor_betas.values()])
            avg_stability = np.mean([v.get("beta_stability", 1.0) for v in factor_betas.values()])
            n_tickers_modeled = len(factor_betas)

            d1, d2, d3, d4 = st.columns(4)
            d1.metric("Tickers Modeled", f"{n_tickers_modeled}/{len(tickers)}")
            d2.metric("Avg R²", f"{avg_r2:.2f}")
            d3.metric("Avg Beta Stability", f"{avg_stability:.2f}",
                     help="Correlation of betas between first and second half of history. 1.0 = perfectly stable.")
            d4.metric("Factors", "7", help="VIX, 10Y, HY spread, 5Y breakeven, Dollar, Oil, VIX×HY interaction")

            # Per-ticker diagnostics
            diag_rows = []
            for t in tickers:
                fb = factor_betas.get(t, {})
                if fb:
                    diag_rows.append({
                        "Ticker": t,
                        "R²": f"{fb['r2']:.3f}",
                        "Beta Stability": f"{fb.get('beta_stability') or 0:.2f}",
                        "Observations": fb.get("n_obs", 0),
                        "Residual Std (daily)": f"{fb.get('residual_std') or 0:.4f}",
                        "Stressed Std": f"{fb.get('stressed_residual_std') or fb.get('residual_std') or 0:.4f}",
                        "Sector": SECTOR_MAP.get(t, "Unknown"),
                    })
                else:
                    diag_rows.append({
                        "Ticker": t, "R²": "—", "Beta Stability": "—",
                        "Observations": "—", "Residual Std (daily)": "—",
                        "Stressed Std": "—", "Sector": SECTOR_MAP.get(t, "Unknown"),
                    })
            if diag_rows:
                st.dataframe(pd.DataFrame(diag_rows).set_index("Ticker"), use_container_width=True)

            # Flag low-quality tickers
            weak = [t for t, fb in factor_betas.items() if fb["r2"] < 0.05]
            unstable = [t for t, fb in factor_betas.items() if fb.get("beta_stability", 1) < 0.3]
            if weak:
                st.warning(f"Low R² (factor model explains <5% of variance): **{', '.join(weak)}** — "
                          f"estimates for these tickers rely more heavily on AI.")
            if unstable:
                st.warning(f"Unstable betas (shifted significantly over the lookback window): **{', '.join(unstable)}** — "
                          f"factor sensitivities may not be reliable.")

            # Sector concentration
            concentration = detect_sector_concentration(tickers)
            if concentration["warnings"]:
                st.markdown("**Sector Concentration Flags:**")
                for w in concentration["warnings"]:
                    st.warning(w)

            # Stressed correlations
            if corr_matrices["stressed"] is not None and len(tickers) > 1:
                st.markdown("**Correlation Shift: Normal vs Stressed Periods**")
                st.caption("Side-by-side comparison of how asset correlations change during high-VIX periods. "
                          "Higher stressed correlations = diversification breaks down when you need it most.")
                corr_cols = st.columns(2)
                with corr_cols[0]:
                    st.caption("Normal periods")
                    fig_cn = go.Figure(data=go.Heatmap(
                        z=corr_matrices["normal"].values,
                        x=corr_matrices["normal"].columns,
                        y=corr_matrices["normal"].index,
                        colorscale="RdYlGn", zmid=0,
                        text=np.round(corr_matrices["normal"].values, 2),
                        texttemplate="%{text}",
                        zmin=-1, zmax=1,
                    ))
                    fig_cn.update_layout(template="plotly_dark", height=250, margin=dict(t=10, b=0, l=0, r=0))
                    st.plotly_chart(fig_cn, use_container_width=True)
                with corr_cols[1]:
                    st.caption("Stressed periods (high VIX)")
                    fig_cs = go.Figure(data=go.Heatmap(
                        z=corr_matrices["stressed"].values,
                        x=corr_matrices["stressed"].columns,
                        y=corr_matrices["stressed"].index,
                        colorscale="RdYlGn", zmid=0,
                        text=np.round(corr_matrices["stressed"].values, 2),
                        texttemplate="%{text}",
                        zmin=-1, zmax=1,
                    ))
                    fig_cs.update_layout(template="plotly_dark", height=250, margin=dict(t=10, b=0, l=0, r=0))
                    st.plotly_chart(fig_cs, use_container_width=True)

                # Flag correlation breakdown
                avg_normal = corr_matrices["normal"].values[np.triu_indices_from(corr_matrices["normal"].values, k=1)].mean()
                avg_stressed = corr_matrices["stressed"].values[np.triu_indices_from(corr_matrices["stressed"].values, k=1)].mean()
                if avg_stressed > avg_normal + 0.1:
                    st.warning(f"Correlations increase from {avg_normal:.2f} to {avg_stressed:.2f} under stress — "
                              f"diversification benefit weakens in downside regimes. Crisis P&L estimates account for this "
                              f"via stressed residual standard deviation.")

            # ── Additional Diagnostic Charts ──
            if factor_betas and not factor_changes.empty:
                st.divider()
                st.markdown("**Factor Beta Profiles**")
                st.caption("Each ticker's sensitivity to the 7 macro factors. Larger bars = stronger sensitivity.")

                # Factor beta heatmap
                beta_tickers = [t for t in tickers if t in factor_betas]
                if beta_tickers:
                    factor_names = FACTOR_SERIES + (["VIX_HY"] if "VIX_HY" in factor_changes.columns else [])
                    beta_z = []
                    for t in beta_tickers:
                        row = [factor_betas[t]["betas"].get(f, 0) * 10000 for f in factor_names]  # scale to bps
                        beta_z.append(row)

                    fig_beta_heat = go.Figure(data=go.Heatmap(
                        z=beta_z, x=[f.replace("BAMLH0A0HYM2", "HY Spread").replace("DCOILWTICO", "Oil")
                                     .replace("VIXCLS", "VIX").replace("DGS10", "10Y").replace("T5YIE", "5Y BE")
                                     .replace("DTWEXBGS", "Dollar").replace("VIX_HY", "VIX×HY")
                                     for f in factor_names],
                        y=beta_tickers,
                        colorscale="RdBu_r", zmid=0,
                        text=np.round(beta_z, 1), texttemplate="%{text}",
                    ))
                    fig_beta_heat.update_layout(
                        template="plotly_dark", height=max(200, len(beta_tickers) * 40 + 60),
                        margin=dict(t=10, b=0, l=0, r=0),
                        xaxis_title="Factor Sensitivity (bps per unit change)",
                    )
                    st.plotly_chart(fig_beta_heat, use_container_width=True)

                # R² comparison bar chart
                st.markdown("**Model Fit (R²) by Ticker**")
                r2_tickers = [t for t in tickers if t in factor_betas]
                r2_vals = [factor_betas[t]["r2"] for t in r2_tickers]
                stability_vals = [factor_betas[t].get("beta_stability", 0) for t in r2_tickers]

                fig_r2 = make_subplots(rows=1, cols=2, subplot_titles=["R² (Model Fit)", "Beta Stability"])
                r2_colors = ["#00cc66" if v >= 0.1 else "#ffaa00" if v >= 0.05 else "#ff4444" for v in r2_vals]
                fig_r2.add_trace(go.Bar(x=r2_tickers, y=r2_vals, marker_color=r2_colors,
                                       text=[f"{v:.3f}" for v in r2_vals], textposition="outside",
                                       showlegend=False), row=1, col=1)
                stab_colors = ["#00cc66" if v >= 0.5 else "#ffaa00" if v >= 0.3 else "#ff4444" for v in stability_vals]
                fig_r2.add_trace(go.Bar(x=r2_tickers, y=stability_vals, marker_color=stab_colors,
                                       text=[f"{v:.2f}" for v in stability_vals], textposition="outside",
                                       showlegend=False), row=1, col=2)
                fig_r2.update_layout(template="plotly_dark", height=280, margin=dict(t=30, b=0, l=0, r=0))
                st.plotly_chart(fig_r2, use_container_width=True)
                st.caption("R²: Green ≥ 0.10 | Yellow ≥ 0.05 | Red < 0.05. "
                          "Stability: Green ≥ 0.50 | Yellow ≥ 0.30 | Red < 0.30.")

                # Residual distribution per ticker
                st.markdown("**Residual Distribution (Model Error)**")
                st.caption("Fat tails = model underestimates extreme moves. "
                          "Skew = model biased in one direction.")

                common_idx = daily_returns.index.intersection(factor_changes.index)
                if len(common_idx) > 30:
                    Y_diag = daily_returns.loc[common_idx]
                    X_diag = factor_changes.loc[common_idx]
                    X_const = np.column_stack([np.ones(len(X_diag)), X_diag.values])

                    resid_cols = st.columns(min(len(beta_tickers), 3))
                    for idx, t in enumerate(beta_tickers):
                        col = resid_cols[idx % len(resid_cols)]
                        fb = factor_betas[t]
                        y = Y_diag[t].values
                        mask = ~np.isnan(y)
                        if mask.sum() < 30:
                            continue
                        coeffs_arr = np.array([fb["alpha"]] + [fb["betas"].get(f, 0) for f in X_diag.columns])
                        y_pred = X_const[mask] @ coeffs_arr
                        residuals = (y[mask] - y_pred) * 100  # to bps-like scale

                        with col:
                            fig_resid = go.Figure()
                            fig_resid.add_trace(go.Histogram(
                                x=residuals, nbinsx=40,
                                marker_color="#00d1ff",
                                opacity=0.7,
                            ))
                            fig_resid.add_vline(x=0, line_color="white", line_width=1)
                            # Show kurtosis
                            from scipy.stats import kurtosis as calc_kurtosis, skew as calc_skew
                            kurt = calc_kurtosis(residuals)
                            sk = calc_skew(residuals)
                            fig_resid.update_layout(
                                template="plotly_dark", height=180,
                                margin=dict(t=25, b=0, l=0, r=0),
                                title=dict(text=f"{t} (kurt={kurt:.1f}, skew={sk:.2f})", font=dict(size=10)),
                                xaxis=dict(title=""), yaxis=dict(title=""),
                                showlegend=False,
                            )
                            st.plotly_chart(fig_resid, use_container_width=True)

                # Actual vs Predicted scatter per ticker
                st.markdown("**Actual vs Predicted Returns**")
                st.caption("Points near the diagonal = good model fit. Scatter = noise the model can't explain.")

                if len(common_idx) > 30:
                    avp_cols = st.columns(min(len(beta_tickers), 3))
                    for idx, t in enumerate(beta_tickers):
                        col = avp_cols[idx % len(avp_cols)]
                        fb = factor_betas[t]
                        y = Y_diag[t].values
                        mask = ~np.isnan(y)
                        if mask.sum() < 30:
                            continue
                        coeffs_arr = np.array([fb["alpha"]] + [fb["betas"].get(f, 0) for f in X_diag.columns])
                        y_pred = X_const[mask] @ coeffs_arr
                        y_actual = y[mask]

                        with col:
                            fig_avp = go.Figure()
                            fig_avp.add_trace(go.Scatter(
                                x=y_pred * 100, y=y_actual * 100,
                                mode="markers", marker=dict(size=3, color="#00d1ff", opacity=0.4),
                                showlegend=False,
                            ))
                            # Perfect fit line
                            rng_val = max(abs(y_actual.min()), abs(y_actual.max())) * 100
                            fig_avp.add_trace(go.Scatter(
                                x=[-rng_val, rng_val], y=[-rng_val, rng_val],
                                mode="lines", line=dict(color="rgba(255,255,255,0.3)", dash="dash", width=1),
                                showlegend=False,
                            ))
                            fig_avp.update_layout(
                                template="plotly_dark", height=200,
                                margin=dict(t=25, b=0, l=0, r=0),
                                title=dict(text=f"{t} (R²={fb['r2']:.3f})", font=dict(size=10)),
                                xaxis=dict(title="Predicted (%)"), yaxis=dict(title="Actual (%)"),
                            )
                            st.plotly_chart(fig_avp, use_container_width=True)

                # Stressed vs Normal residual std comparison
                st.markdown("**Stressed vs Normal Volatility**")
                st.caption("How much wider residuals get during high-VIX periods. "
                          "Ratio > 1.5 means tail risk is significantly underestimated by normal-period stats.")

                vol_tickers = [t for t in beta_tickers if t in factor_betas]
                normal_stds = [factor_betas[t]["residual_std"] * 100 for t in vol_tickers]
                stressed_stds = [factor_betas[t].get("stressed_residual_std", factor_betas[t]["residual_std"]) * 100 for t in vol_tickers]
                ratios = [s / n if n > 0 else 1 for s, n in zip(stressed_stds, normal_stds)]

                fig_vol = go.Figure()
                fig_vol.add_trace(go.Bar(name="Normal", x=vol_tickers, y=normal_stds, marker_color="#00d1ff"))
                fig_vol.add_trace(go.Bar(name="Stressed", x=vol_tickers, y=stressed_stds, marker_color="#ff4444"))
                fig_vol.update_layout(
                    template="plotly_dark", barmode="group", height=280,
                    margin=dict(t=30, b=0, l=0, r=0),
                    yaxis_title="Daily Residual Std (%)",
                )
                st.plotly_chart(fig_vol, use_container_width=True)

                # Stress ratio annotations
                for t, r in zip(vol_tickers, ratios):
                    if r > 1.5:
                        st.caption(f"**{t}**: Stressed/Normal ratio = {r:.1f}x — significant tail risk amplification")

                # Factor correlation matrix
                st.markdown("**Factor Correlation Matrix**")
                st.caption("High correlation between factors = multicollinearity risk in the regression. "
                          "Watch for |corr| > 0.7.")
                factor_corr = factor_changes.corr()
                factor_labels = [f.replace("BAMLH0A0HYM2", "HY").replace("DCOILWTICO", "Oil")
                                .replace("VIXCLS", "VIX").replace("DGS10", "10Y").replace("T5YIE", "5Y BE")
                                .replace("DTWEXBGS", "USD").replace("VIX_HY", "VIX×HY")
                                for f in factor_corr.columns]
                fig_fc = go.Figure(data=go.Heatmap(
                    z=factor_corr.values, x=factor_labels, y=factor_labels,
                    colorscale="RdBu_r", zmid=0, zmin=-1, zmax=1,
                    text=np.round(factor_corr.values, 2), texttemplate="%{text}",
                ))
                fig_fc.update_layout(template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0))
                st.plotly_chart(fig_fc, use_container_width=True)

                # Flag multicollinearity
                high_corr_pairs = []
                cols_list = list(factor_corr.columns)
                for i in range(len(cols_list)):
                    for j in range(i + 1, len(cols_list)):
                        c = abs(factor_corr.iloc[i, j])
                        if c > 0.7:
                            high_corr_pairs.append((factor_labels[i], factor_labels[j], c))
                if high_corr_pairs:
                    for f1, f2, c in high_corr_pairs:
                        st.warning(f"High correlation: **{f1}** and **{f2}** = {c:.2f} — "
                                  f"may cause unstable beta estimates for these factors.")

    # ─────────────────────────────────────────────
    # TAB 8: REGIME TRACK RECORD
    # ─────────────────────────────────────────────
    with tab8, error_boundary("Regime Track Record"):
        st.subheader("Regime Prediction Track Record")
        with st.expander("What this shows"):
            st.markdown("""
**How accurate have the Grok regime predictions been?**

Each time the macro scenario engine runs, it assigns probabilities to regimes
(Soft Landing, Stagflation, Recession, etc.). This tab compares those predictions
to what the market actually did over the following 30 days.

A regime prediction is "correct" if the highest-probability regime's expected
market direction matched the actual SPY return direction. For example, if Grok
predicted "Soft Landing" at 45% (bullish), and SPY was up 30 days later, that's correct.
""")

        history = load_grok_history()

        if len(history) < 2:
            st.info("Need at least 2 regime analyses to evaluate accuracy. Run more analyses over time.")
        else:
            import yfinance as yf

            # For each historical prediction, check what SPY did afterwards
            eval_rows = []
            _spy_hist = None
            try:
                _spy_hist = yf.download("SPY", period="1y", progress=False)
            except Exception:
                pass

            if _spy_hist is not None and not _spy_hist.empty:
                for entry in history:
                    ts = pd.to_datetime(entry["timestamp"])
                    age_days = (pd.Timestamp.now() - ts).days
                    if age_days < 30:
                        continue  # too recent to evaluate

                    regimes = entry.get("regimes", [])
                    if not regimes:
                        continue

                    # Find top regime
                    top_regime = max(regimes, key=lambda r: r.get("probability", 0))
                    regime_name = top_regime.get("name", "?")
                    regime_prob = top_regime.get("probability", 0)

                    # Determine expected direction from regime name
                    bullish_regimes = {"soft landing", "goldilocks", "expansion", "recovery",
                                       "reflation", "bull", "risk-on"}
                    bearish_regimes = {"recession", "stagflation", "crisis", "hard landing",
                                       "contraction", "bear", "risk-off"}
                    _rname_lower = regime_name.lower()
                    if any(b in _rname_lower for b in bullish_regimes):
                        expected_dir = "Bullish"
                    elif any(b in _rname_lower for b in bearish_regimes):
                        expected_dir = "Bearish"
                    else:
                        expected_dir = "Neutral"

                    # Get SPY return over next 30 days
                    target_date = ts + pd.Timedelta(days=30)
                    _after = _spy_hist[_spy_hist.index >= ts.tz_localize(None) if ts.tzinfo else ts]
                    _after_30 = _after.head(22)  # ~30 calendar days ≈ 22 trading days

                    if len(_after_30) >= 10:
                        spy_start = float(_after_30["Close"].iloc[0])
                        spy_end = float(_after_30["Close"].iloc[-1])
                        spy_ret = (spy_end / spy_start - 1) * 100

                        actual_dir = "Bullish" if spy_ret > 0 else "Bearish"
                        correct = (expected_dir == actual_dir) if expected_dir != "Neutral" else None

                        eval_rows.append({
                            "Date": ts.strftime("%Y-%m-%d %H:%M"),
                            "Top Regime": regime_name,
                            "Probability": f"{regime_prob}%",
                            "Expected": expected_dir,
                            "SPY 30d": f"{spy_ret:+.1f}%",
                            "Actual": actual_dir,
                            "Correct": "Yes" if correct is True else ("No" if correct is False else "—"),
                        })

                if eval_rows:
                    eval_df = pd.DataFrame(eval_rows)

                    # Accuracy metrics
                    _directional = [r for r in eval_rows if r["Correct"] in ("Yes", "No")]
                    _correct = [r for r in _directional if r["Correct"] == "Yes"]
                    accuracy = len(_correct) / len(_directional) if _directional else None

                    ec1, ec2, ec3 = st.columns(3)
                    ec1.metric("Regime Calls Evaluated", len(eval_rows))
                    ec2.metric("Directional Predictions", len(_directional))
                    if accuracy is not None:
                        _acc_color = COLORS["success"] if accuracy > 0.55 else (COLORS["warning"] if accuracy > 0.50 else COLORS["danger"])
                        ec3.metric("30-Day Accuracy", f"{accuracy*100:.0f}%")
                    else:
                        ec3.metric("30-Day Accuracy", "—")

                    st.dataframe(eval_df, use_container_width=True, hide_index=True)

                    if accuracy is not None:
                        if accuracy > 0.6:
                            st.success(f"Regime predictions are {accuracy*100:.0f}% accurate — strong track record.")
                        elif accuracy > 0.5:
                            st.info(f"Regime predictions are {accuracy*100:.0f}% accurate — slightly better than random.")
                        else:
                            st.warning(f"Regime predictions are {accuracy*100:.0f}% accurate — below random. Consider adjusting parameters.")
                else:
                    st.info("No evaluable predictions yet (need predictions older than 30 days with SPY data).")
            else:
                st.warning("Could not fetch SPY history for evaluation.")

    # ─────────────────────────────────────────────
    # CHATBOT
    # ─────────────────────────────────────────────
    context = f"Scenario analysis for portfolio: {', '.join(tickers)}. Portfolio value: ${port_val:,.0f}."
    run_sidebar_chatbot(context)
