import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import yfinance as yf
import requests
import os
import logging
import json
from openai import OpenAI
from datetime import datetime, timedelta
import re
from src.layout import setup_page, card_header, error_boundary
from src.styles import COLORS

setup_page("03_Stock_Analysis")
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def _get_key(name: str):
    key = os.environ.get(name)
    if not key:
        try:
            key = st.secrets[name]
        except Exception:
            pass
    return key

grok_key = _get_key("GROK_API_KEY")
fred_key = _get_key("FRED_API_KEY")


@st.cache_data(ttl=900, show_spinner=False)
def fetch_stock_data(ticker: str) -> dict:
    """Fetch comprehensive stock data from yfinance."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.info or {}
        hist_1y = tk.history(period="1y")
        hist_3m = tk.history(period="3mo")
        hist_5y = tk.history(period="5y")

        # Financials
        try:
            income = tk.income_stmt
        except Exception:
            income = pd.DataFrame()
        try:
            balance = tk.balance_sheet
        except Exception:
            balance = pd.DataFrame()
        try:
            cashflow = tk.cashflow
        except Exception:
            cashflow = pd.DataFrame()

        # Analyst data
        try:
            recommendations = tk.recommendations
        except Exception:
            recommendations = pd.DataFrame()

        # Insider transactions
        try:
            insider = tk.insider_transactions
        except Exception:
            insider = pd.DataFrame()

        return {
            "info": info,
            "hist_1y": hist_1y,
            "hist_3m": hist_3m,
            "hist_5y": hist_5y,
            "income": income,
            "balance": balance,
            "cashflow": cashflow,
            "recommendations": recommendations,
            "insider": insider,
            "success": True,
        }
    except Exception as e:
        logger.error(f"Failed to fetch data for {ticker}: {e}")
        return {"success": False, "error": str(e)}


def compute_technicals(hist: pd.DataFrame) -> dict:
    """Compute technical indicators from price history."""
    if hist.empty or len(hist) < 20:
        return {}

    close = hist["Close"]
    high = hist["High"]
    low = hist["Low"]
    volume = hist["Volume"]

    # EMAs
    ema_20 = close.ewm(span=20).mean()
    ema_50 = close.ewm(span=50).mean()
    ema_200 = close.ewm(span=200).mean() if len(close) >= 200 else pd.Series(dtype=float)

    # RSI (14-day)
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # MACD
    ema_12 = close.ewm(span=12).mean()
    ema_26 = close.ewm(span=26).mean()
    macd_line = ema_12 - ema_26
    signal_line = macd_line.ewm(span=9).mean()
    macd_hist = macd_line - signal_line

    # Bollinger Bands
    sma_20 = close.rolling(20).mean()
    std_20 = close.rolling(20).std()
    bb_upper = sma_20 + 2 * std_20
    bb_lower = sma_20 - 2 * std_20

    # ATR (14-day)
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()

    # Volume
    avg_vol_20 = volume.rolling(20).mean()
    vol_ratio = volume / avg_vol_20.replace(0, np.nan)

    current = close.iloc[-1]

    # Trend score
    trend_signals = 0
    if len(ema_200) > 0 and current > ema_200.iloc[-1]:
        trend_signals += 1
    if current > ema_50.iloc[-1]:
        trend_signals += 1
    if current > ema_20.iloc[-1]:
        trend_signals += 1
    if ema_20.iloc[-1] > ema_50.iloc[-1]:
        trend_signals += 1

    # Momentum score
    current_rsi = rsi.iloc[-1] if not rsi.empty else 50
    macd_bullish = macd_hist.iloc[-1] > 0 if not macd_hist.empty else False

    return {
        "current_price": current,
        "ema_20": ema_20.iloc[-1],
        "ema_50": ema_50.iloc[-1],
        "ema_200": ema_200.iloc[-1] if len(ema_200) > 0 else None,
        "rsi": current_rsi,
        "macd_line": macd_line.iloc[-1],
        "macd_signal": signal_line.iloc[-1],
        "macd_hist": macd_hist.iloc[-1],
        "macd_bullish": macd_bullish,
        "bb_upper": bb_upper.iloc[-1],
        "bb_lower": bb_lower.iloc[-1],
        "bb_pct": (current - bb_lower.iloc[-1]) / (bb_upper.iloc[-1] - bb_lower.iloc[-1]) if (bb_upper.iloc[-1] - bb_lower.iloc[-1]) > 0 else 0.5,
        "atr": atr.iloc[-1],
        "atr_pct": atr.iloc[-1] / current * 100,
        "vol_ratio": vol_ratio.iloc[-1],
        "trend_signals": trend_signals,
        # Raw series for plotting
        "close": close,
        "ema_20_series": ema_20,
        "ema_50_series": ema_50,
        "ema_200_series": ema_200,
        "rsi_series": rsi,
        "macd_line_series": macd_line,
        "macd_signal_series": signal_line,
        "macd_hist_series": macd_hist,
        "bb_upper_series": bb_upper,
        "bb_lower_series": bb_lower,
    }


def compute_fundamentals(info: dict, income: pd.DataFrame, balance: pd.DataFrame, cashflow: pd.DataFrame) -> dict:
    """Extract and score fundamental metrics."""
    def safe_get(d, *keys, default=None):
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return default

    pe = safe_get(info, "trailingPE", "forwardPE", default=0)
    ps = safe_get(info, "priceToSalesTrailing12Months", default=0)
    pb = safe_get(info, "priceToBook", default=0)
    de = safe_get(info, "debtToEquity", default=0)
    roe = safe_get(info, "returnOnEquity", default=0)
    margin = safe_get(info, "profitMargins", default=0)
    rev_growth = safe_get(info, "revenueGrowth", default=0)
    earnings_growth = safe_get(info, "earningsGrowth", default=0)
    fcf = safe_get(info, "freeCashflow", default=0)
    market_cap = safe_get(info, "marketCap", default=0)
    dividend_yield = safe_get(info, "dividendYield", default=0)
    beta = safe_get(info, "beta", default=1.0)
    short_pct = safe_get(info, "shortPercentOfFloat", default=0)
    target_mean = safe_get(info, "targetMeanPrice", default=0)
    target_low = safe_get(info, "targetLowPrice", default=0)
    target_high = safe_get(info, "targetHighPrice", default=0)
    current_price = safe_get(info, "currentPrice", "regularMarketPrice", default=0)
    rec = safe_get(info, "recommendationKey", default="none")
    sector = safe_get(info, "sector", default="Unknown")
    industry = safe_get(info, "industry", default="Unknown")
    name = safe_get(info, "longName", "shortName", default="Unknown")

    # FCF yield
    fcf_yield = (fcf / market_cap * 100) if market_cap > 0 and fcf else 0

    # Analyst upside
    upside = ((target_mean / current_price) - 1) * 100 if current_price > 0 and target_mean > 0 else 0

    return {
        "name": name, "sector": sector, "industry": industry,
        "market_cap": market_cap, "current_price": current_price,
        "pe": pe, "ps": ps, "pb": pb, "de": de,
        "roe": roe, "margin": margin,
        "rev_growth": rev_growth, "earnings_growth": earnings_growth,
        "fcf": fcf, "fcf_yield": fcf_yield,
        "dividend_yield": dividend_yield, "beta": beta,
        "short_pct": short_pct,
        "target_mean": target_mean, "target_low": target_low, "target_high": target_high,
        "upside": upside, "rec": rec,
    }


@st.cache_data(ttl=1800, show_spinner=False)
def fetch_stocktwits_ticker(ticker: str) -> dict:
    """Fetch StockTwits sentiment for a specific ticker."""
    try:
        from curl_cffi import requests as cffi_requests
        r = cffi_requests.get(
            f"https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json?limit=30",
            impersonate="chrome", timeout=10,
        )
        data = r.json()
        msgs = data.get("messages", [])
        bull = sum(1 for m in msgs if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bullish")
        bear = sum(1 for m in msgs if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bearish")
        tagged = bull + bear
        recent_posts = []
        for m in msgs[:5]:
            sent = (m.get("entities", {}).get("sentiment") or {}).get("basic", "—")
            body = m.get("body", "")[:150]
            recent_posts.append({"sentiment": sent, "body": body})
        return {
            "bull": bull, "bear": bear, "total": len(msgs), "tagged": tagged,
            "bull_ratio": (bull / tagged * 100) if tagged > 0 else 50,
            "recent_posts": recent_posts,
        }
    except Exception:
        return {"bull": 0, "bear": 0, "total": 0, "tagged": 0, "bull_ratio": 50, "recent_posts": []}


def build_stock_prompt(ticker: str, fundamentals: dict, technicals: dict,
                       sentiment: dict, macro_context: str) -> str:
    """Build a structured prompt with all stock data for Grok."""
    lines = []
    lines.append(f"STOCK ANALYSIS REQUEST: {ticker} ({fundamentals.get('name', ticker)})")
    lines.append(f"Sector: {fundamentals.get('sector', '?')} | Industry: {fundamentals.get('industry', '?')}")
    lines.append("")

    # Fundamentals
    lines.append("=" * 50)
    lines.append("FUNDAMENTALS")
    lines.append("=" * 50)
    f = fundamentals
    lines.append(f"Market Cap: ${f['market_cap']/1e9:.1f}B" if f['market_cap'] > 1e9 else f"Market Cap: ${f['market_cap']/1e6:.0f}M")
    lines.append(f"Current Price: ${f['current_price']:.2f}")
    lines.append(f"P/E: {f['pe']:.1f} | P/S: {f['ps']:.1f} | P/B: {f['pb']:.1f}")
    lines.append(f"Debt/Equity: {f['de']:.0f}% | ROE: {f['roe']*100:.1f}%" if f['roe'] else f"Debt/Equity: {f['de']:.0f}%")
    lines.append(f"Profit Margin: {f['margin']*100:.1f}%" if f['margin'] else "Profit Margin: N/A")
    lines.append(f"Revenue Growth: {f['rev_growth']*100:.1f}% | Earnings Growth: {f['earnings_growth']*100:.1f}%" if f['earnings_growth'] else "")
    lines.append(f"FCF Yield: {f['fcf_yield']:.1f}% | Dividend Yield: {f['dividend_yield']*100:.1f}%" if f['dividend_yield'] else f"FCF Yield: {f['fcf_yield']:.1f}%")
    lines.append(f"Beta: {f['beta']:.2f} | Short % of Float: {f['short_pct']*100:.1f}%" if f['short_pct'] else f"Beta: {f['beta']:.2f}")
    lines.append(f"Analyst Target: ${f['target_low']:.0f} / ${f['target_mean']:.0f} / ${f['target_high']:.0f} (Low/Mean/High)")
    lines.append(f"Analyst Consensus: {f['rec']} | Upside to Mean Target: {f['upside']:+.1f}%")

    # Technicals
    lines.append("")
    lines.append("=" * 50)
    lines.append("TECHNICALS")
    lines.append("=" * 50)
    t = technicals
    if t:
        lines.append(f"Price: ${t['current_price']:.2f}")
        lines.append(f"EMA 20: ${t['ema_20']:.2f} | EMA 50: ${t['ema_50']:.2f}" +
                    (f" | EMA 200: ${t['ema_200']:.2f}" if t['ema_200'] else ""))
        lines.append(f"RSI(14): {t['rsi']:.1f} ({'Overbought' if t['rsi'] > 70 else 'Oversold' if t['rsi'] < 30 else 'Neutral'})")
        lines.append(f"MACD: {'Bullish' if t['macd_bullish'] else 'Bearish'} (hist={t['macd_hist']:.3f})")
        lines.append(f"Bollinger %B: {t['bb_pct']:.2f} (0=lower band, 1=upper band)")
        lines.append(f"ATR: ${t['atr']:.2f} ({t['atr_pct']:.1f}% of price)")
        lines.append(f"Volume Ratio: {t['vol_ratio']:.1f}x 20-day avg")
        lines.append(f"Trend Signals: {t['trend_signals']}/4 bullish (price vs EMAs + EMA alignment)")

    # Sentiment
    lines.append("")
    lines.append("=" * 50)
    lines.append("SENTIMENT (StockTwits)")
    lines.append("=" * 50)
    s = sentiment
    lines.append(f"Bull/Bear: {s['bull']}/{s['bear']} of {s['total']} posts ({s['bull_ratio']:.0f}% bullish)")
    if s['recent_posts']:
        lines.append("Recent posts:")
        for p in s['recent_posts'][:3]:
            lines.append(f"  [{p['sentiment']}] {p['body'][:100]}")

    # Macro context
    if macro_context:
        lines.append("")
        lines.append("=" * 50)
        lines.append("MACRO CONTEXT (from Scenario Analysis)")
        lines.append("=" * 50)
        lines.append(macro_context)

    return "\n".join(lines)


STOCK_SYSTEM_PROMPT = """You are a senior equity research analyst at a top-tier investment bank.
You produce rigorous, institutional-grade stock analysis. You are given comprehensive data
about a stock including fundamentals, technicals, sentiment, and macro context.

Your job is to produce a COMPLETE analysis with the following structure:

1. SCORES: Rate each dimension 1-10 (10 = strongest bull case):
   - technical_score: Based on trend, momentum, volume, support/resistance
   - fundamental_score: Based on valuation, growth, profitability, balance sheet
   - sentiment_score: Based on StockTwits data provided + any social/news sentiment you know
   - macro_score: How well positioned for current macro regime
   - valuation_score: Is the stock cheap or expensive relative to fair value

2. RECOMMENDATION: One of: "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"

3. PRICE TARGETS (12-month):
   - bull_target: Best case price
   - base_target: Most likely price
   - bear_target: Worst case price
   - bull_probability: % chance of bull case
   - base_probability: % chance of base case
   - bear_probability: % chance of bear case (must sum to 100)

4. ANALYSIS: Detailed rationale for each score (2-3 sentences each)

5. KEY RISKS: Top 3 risks to the thesis
6. KEY CATALYSTS: Top 3 potential catalysts
7. CONFIDENCE: 1-10 how confident you are (based on data quality and signal agreement)

Respond with ONLY valid JSON in this format:
{
  "scores": {"technical": N, "fundamental": N, "sentiment": N, "macro": N, "valuation": N},
  "composite_score": N,
  "recommendation": "...",
  "price_targets": {"bull": N, "base": N, "bear": N, "bull_prob": N, "base_prob": N, "bear_prob": N},
  "analysis": {"technical": "...", "fundamental": "...", "sentiment": "...", "macro": "...", "valuation": "..."},
  "risks": ["...", "...", "..."],
  "catalysts": ["...", "...", "..."],
  "confidence": N,
  "summary": "3-4 sentence executive summary that MUST reference: (1) the key fundamental driver, (2) the technical setup, (3) retail sentiment from StockTwits (cite the bull/bear ratio), and (4) any material macro or news risk",
  "sentiment_pulse": "1-2 sentences on what social media and StockTwits are saying about this stock right now — cite specific bull/bear ratio and notable posts"
}"""

# Model configurations: (name, base_url, model_id, api_key_name, has_search)
MODEL_CONFIGS = {
    "grok": {
        "name": "Grok 3",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-3",
        "key_name": "GROK_API_KEY",
        "extra_instructions": "IMPORTANT: Search X/Twitter for the latest sentiment, news, and analyst commentary on this ticker. Factor in any breaking news, earnings reactions, or insider activity.",
        "color": "#ff4444",
    },
    "openai": {
        "name": "GPT-5",
        "base_url": None,  # default OpenAI
        "model": "gpt-5",
        "key_name": "OPENAI_API_KEY",
        "extra_instructions": "Use your training data and knowledge to assess the latest market conditions, analyst consensus, and any recent news for this ticker.",
        "color": "#00cc66",
    },
    "gemini": {
        "name": "Gemini 3 Pro",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3-pro-preview",
        "key_name": "GEMINI_API_KEY",
        "extra_instructions": "Use your knowledge to assess the latest market conditions, analyst consensus, and any recent news for this ticker.",
        "color": "#4285f4",
    },
    "claude": {
        "name": "Claude Sonnet",
        "base_url": "anthropic",  # special flag — uses anthropic SDK
        "model": "claude-sonnet-4-20250514",
        "key_name": "ANTHROPIC_API_KEY",
        "extra_instructions": "Use your knowledge to assess the latest market conditions, analyst consensus, and any recent news for this ticker.",
        "color": "#d4a574",
    },
}


@st.cache_data(ttl=1800, show_spinner=False)
def run_model_stock_analysis(model_key: str, api_key: str, stock_prompt: str, ticker: str) -> dict:
    """Call a single AI model for stock analysis. Handles OpenAI-compatible APIs and Claude."""
    config = MODEL_CONFIGS[model_key]

    user_prompt = f"""{stock_prompt}

{config['extra_instructions']}
Produce your complete analysis for {ticker}. JSON only."""

    try:
        if config["base_url"] == "anthropic":
            # Claude uses its own SDK
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=config["model"],
                max_tokens=3000,
                temperature=0.3,
                system=STOCK_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
        else:
            # OpenAI-compatible APIs (OpenAI, Grok, Gemini)
            client_kwargs = {"api_key": api_key}
            if config["base_url"]:
                client_kwargs["base_url"] = config["base_url"]
            client = OpenAI(**client_kwargs)
            # GPT-5 uses max_completion_tokens and doesn't support temperature
            is_gpt5 = "gpt-5" in config["model"]
            model_kwargs = {
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": STOCK_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "max_completion_tokens" if is_gpt5 else "max_tokens": 3000,
                **({"temperature": 0.3} if not is_gpt5 else {}),
            }
            try:
                response = client.chat.completions.create(
                    **model_kwargs,
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = client.chat.completions.create(**model_kwargs)
            raw = response.choices[0].message.content

        # Strip markdown code blocks (some models wrap JSON in ```json ... ```)
        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)
        result["success"] = True
        result["model_name"] = config["name"]
        return result
    except Exception as e:
        logger.error(f"{config['name']} stock analysis failed for {ticker}: {e}")
        return {"success": False, "error": str(e), "model_name": config["name"]}


def blend_model_results(results: dict) -> dict:
    """Blend multiple model outputs into a consensus view."""
    successful = {k: v for k, v in results.items() if v.get("success")}
    if not successful:
        return {"success": False, "error": "All models failed"}
    if len(successful) == 1:
        only = list(successful.values())[0]
        only["blend_note"] = f"Single model: {only.get('model_name', '?')}"
        return only

    n = len(successful)

    # Blend scores (simple average)
    blended_scores = {}
    for dim in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
        vals = [v.get("scores", {}).get(dim, 5) for v in successful.values()]
        blended_scores[dim] = round(sum(vals) / len(vals), 1)

    composite = round(sum(blended_scores.values()) / len(blended_scores), 1)

    # Blend price targets (average)
    pt_keys = ["bull", "base", "bear", "bull_prob", "base_prob", "bear_prob"]
    blended_pt = {}
    for k in pt_keys:
        vals = [v.get("price_targets", {}).get(k, 0) for v in successful.values()]
        blended_pt[k] = round(sum(vals) / len(vals), 1)
    # Normalize probabilities
    prob_total = blended_pt.get("bull_prob", 25) + blended_pt.get("base_prob", 50) + blended_pt.get("bear_prob", 25)
    if prob_total > 0:
        for pk in ["bull_prob", "base_prob", "bear_prob"]:
            blended_pt[pk] = round(blended_pt[pk] / prob_total * 100)

    # Blend recommendation (majority vote, fallback to most conservative)
    rec_order = ["Strong Sell", "Sell", "Hold", "Buy", "Strong Buy"]
    rec_scores = []
    for v in successful.values():
        rec = v.get("recommendation", "Hold")
        rec_scores.append(rec_order.index(rec) if rec in rec_order else 2)
    avg_rec_score = sum(rec_scores) / len(rec_scores)
    blended_rec = rec_order[round(avg_rec_score)]

    # Blend confidence (average, penalize disagreement)
    confidences = [v.get("confidence", 5) for v in successful.values()]
    avg_conf = sum(confidences) / len(confidences)
    # Score divergence penalty: if models disagree on recommendation, lower confidence
    rec_spread = max(rec_scores) - min(rec_scores)
    conf_penalty = min(2, rec_spread * 0.5)
    blended_conf = max(1, round(avg_conf - conf_penalty))

    # Merge analysis text (concatenate with attribution)
    blended_analysis = {}
    for dim in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
        parts = []
        for k, v in successful.items():
            name = v.get("model_name", k)
            text = v.get("analysis", {}).get(dim, "")
            if text:
                parts.append(f"**{name}:** {text}")
        blended_analysis[dim] = " ".join(parts)

    # Merge risks and catalysts (deduplicate-ish by taking all unique)
    all_risks = []
    all_catalysts = []
    for v in successful.values():
        all_risks.extend(v.get("risks", []))
        all_catalysts.extend(v.get("catalysts", []))
    # Simple dedup: take first 5 unique-ish (by first 30 chars)
    seen_r, seen_c = set(), set()
    unique_risks, unique_catalysts = [], []
    for r in all_risks:
        key = r[:30].lower()
        if key not in seen_r:
            seen_r.add(key)
            unique_risks.append(r)
    for c in all_catalysts:
        key = c[:30].lower()
        if key not in seen_c:
            seen_c.add(key)
            unique_catalysts.append(c)

    # Blend summaries
    summaries = [v.get("summary", "") for v in successful.values() if v.get("summary")]
    pulses = [v.get("sentiment_pulse", "") for v in successful.values() if v.get("sentiment_pulse")]

    # Agreement / disagreement note
    model_recs = {v.get("model_name", k): v.get("recommendation", "Hold") for k, v in successful.items()}
    if len(set(model_recs.values())) == 1:
        agreement = f"All {n} models agree: **{blended_rec}**"
    else:
        rec_list = ", ".join(f"{name}: {rec}" for name, rec in model_recs.items())
        agreement = f"Models diverge — {rec_list}. Blended: **{blended_rec}**"

    return {
        "success": True,
        "scores": blended_scores,
        "composite_score": composite,
        "recommendation": blended_rec,
        "price_targets": blended_pt,
        "analysis": blended_analysis,
        "risks": unique_risks[:5],
        "catalysts": unique_catalysts[:5],
        "confidence": blended_conf,
        "summary": summaries[0] if summaries else "",
        "sentiment_pulse": pulses[0] if pulses else "",
        "agreement": agreement,
        "model_results": {k: v for k, v in results.items()},
        "blend_note": f"Consensus of {n} models",
    }


# ─────────────────────────────────────────────
# PAGE
# ─────────────────────────────────────────────
st.title("🔍 Stock Analysis & Recommendation")
st.markdown("AI-powered institutional-grade equity research. Multi-dimensional scoring with quantitative backing.")

# Sidebar
with st.sidebar:
    st.header("Analysis Parameters")
    ticker_input = st.text_input("Ticker", value="AAPL")
    analyze_btn = st.button("Run Analysis", type="primary", use_container_width=True)

ticker = ticker_input.strip().upper()

if analyze_btn or f"stock_analysis_{ticker}" in st.session_state:
    if analyze_btn:
        with st.spinner(f"Fetching data for {ticker}..."):
            stock_data = fetch_stock_data(ticker)
            if not stock_data.get("success"):
                st.error(f"Failed to load data for {ticker}: {stock_data.get('error', 'Unknown error')}")
                st.stop()

            fundamentals = compute_fundamentals(
                stock_data["info"], stock_data["income"],
                stock_data["balance"], stock_data["cashflow"]
            )
            technicals = compute_technicals(stock_data["hist_1y"])
            sentiment = fetch_stocktwits_ticker(ticker)

            # Macro context from scenario analysis if available
            macro_ctx = ""
            grok_regime = st.session_state.get("grok_regime_result")
            if grok_regime and grok_regime.get("success"):
                probs = {r["name"]: r["probability"] for r in grok_regime.get("regimes", [])}
                sent = grok_regime.get("sentiment_summary", "")
                macro_ctx = f"Current regime probabilities: {probs}\nMacro sentiment: {sent}"

            # Run AI models in parallel
            prompt = build_stock_prompt(ticker, fundamentals, technicals, sentiment, macro_ctx)
            openai_key = _get_key("OPENAI_API_KEY")
            gemini_key = _get_key("GEMINI_API_KEY")
            anthropic_key = _get_key("ANTHROPIC_API_KEY")

            # Check AI quota and allowed models
            from src.auth import check_ai_quota, increment_ai_usage, get_allowed_models, render_upgrade_prompt
            if not check_ai_quota():
                render_upgrade_prompt("AI Stock Analysis (daily limit reached)")
                st.stop()

            allowed_models = get_allowed_models()
            api_keys = {"grok": grok_key, "openai": openai_key, "gemini": gemini_key, "claude": anthropic_key}

            model_results = {}
            active_models = [m for m in allowed_models if api_keys.get(m)]
            if not active_models:
                st.warning("No AI models available. Check your API keys or subscription tier.")
            else:
                model_names = ", ".join(MODEL_CONFIGS[m]["name"] for m in active_models)
                with st.spinner(f"Running AI analysis ({model_names})..."):
                    for model_key in active_models:
                        key = api_keys[model_key]
                        if key:
                            model_results[model_key] = run_model_stock_analysis(model_key, key, prompt, ticker)

                increment_ai_usage()

            # Blend results
            blended = blend_model_results(model_results)

            st.session_state[f"stock_analysis_{ticker}"] = {
                "stock_data": stock_data,
                "fundamentals": fundamentals,
                "technicals": technicals,
                "sentiment": sentiment,
                "blended": blended,
                "model_results": model_results,
            }

    cached = st.session_state.get(f"stock_analysis_{ticker}")
    if not cached:
        st.info("Click **Run Analysis** to begin.")
        st.stop()

    stock_data = cached["stock_data"]
    fundamentals = cached["fundamentals"]
    technicals = cached["technicals"]
    sentiment = cached["sentiment"]
    grok_result = cached["blended"]
    model_results = cached.get("model_results", {})

    # ═══════════════════════════════════════════
    # HEADER: Company Info + Recommendation
    # ═══════════════════════════════════════════
    with error_boundary("Company Header"):
        h1, h2, h3 = st.columns([2, 1, 1])
        with h1:
            st.markdown(f"## {fundamentals['name']} ({ticker})")
            st.caption(f"{fundamentals['sector']} · {fundamentals['industry']}")
        with h2:
            price = fundamentals["current_price"]
            st.metric("Price", f"${price:.2f}")
        with h3:
            if grok_result and grok_result.get("success"):
                rec = grok_result.get("recommendation", "Hold")
                rec_colors = {"Strong Buy": "#00ff96", "Buy": "#00cc66", "Hold": "#ffaa00",
                             "Sell": "#ff6644", "Strong Sell": "#ff4444"}
                st.markdown(f'<div style="background:{rec_colors.get(rec, "#888")};color:#000;'
                           f'padding:12px;border-radius:8px;text-align:center;font-weight:700;font-size:1.2rem;">'
                           f'{rec}</div>', unsafe_allow_html=True)
                conf = grok_result.get("confidence", 5)
                st.caption(f"Confidence: {conf}/10")

    # Executive summary — blended from all models
    if grok_result and grok_result.get("success"):
        # Collect all model summaries
        all_summaries = []
        all_pulses = []
        individual = grok_result.get("model_results", {})
        for k, v in individual.items():
            if v.get("success"):
                name = v.get("model_name", k)
                s = v.get("summary", "")
                p = v.get("sentiment_pulse", "")
                if s:
                    all_summaries.append(f"**{name}:** {s}")
                if p:
                    all_pulses.append(f"**{name}:** {p}")

        # Show blended summary block
        agreement = grok_result.get("agreement", "")
        blend_note = grok_result.get("blend_note", "")

        st.markdown("### Executive Summary")
        if agreement:
            st.markdown(f"**Model Consensus:** {agreement}")

        if all_summaries:
            for s in all_summaries:
                st.markdown(s)
        elif grok_result.get("summary"):
            st.info(grok_result["summary"])

        if all_pulses:
            st.divider()
            st.caption("**Sentiment Pulse**")
            for p in all_pulses:
                st.caption(p)

    # ═══════════════════════════════════════════
    # SCORECARD
    # ═══════════════════════════════════════════
    if grok_result and grok_result.get("success"):
        with error_boundary("Scorecard"):
            st.markdown("### Multi-Dimensional Scorecard")
            scores = grok_result.get("scores", {})
            composite = grok_result.get("composite_score", 5)

            score_cols = st.columns(6)
            score_items = [
                ("Technical", scores.get("technical", 5)),
                ("Fundamental", scores.get("fundamental", 5)),
                ("Sentiment", scores.get("sentiment", 5)),
                ("Macro", scores.get("macro", 5)),
                ("Valuation", scores.get("valuation", 5)),
                ("Composite", composite),
            ]
            for col, (label, score) in zip(score_cols, score_items):
                score = float(score)
                color = COLORS["success"] if score >= 7 else COLORS["warning"] if score >= 4 else COLORS["danger"]
                with col:
                    st.markdown(
                        f'<div style="text-align:center;padding:8px;border:2px solid {color};border-radius:8px;">'
                        f'<div style="font-size:1.8rem;font-weight:700;color:{color}">{score:.0f}</div>'
                        f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]}">{label}</div></div>',
                        unsafe_allow_html=True,
                    )

            # Radar chart
            categories = ["Technical", "Fundamental", "Sentiment", "Macro", "Valuation"]
            values = [scores.get(c.lower(), 5) for c in categories]
            values.append(values[0])  # close the polygon

            fig_radar = go.Figure()
            fig_radar.add_trace(go.Scatterpolar(
                r=values, theta=categories + [categories[0]],
                fill="toself", fillcolor="rgba(0,209,255,0.15)",
                line=dict(color=COLORS["accent"], width=2),
                marker=dict(size=6),
            ))
            fig_radar.update_layout(
                polar=dict(
                    radialaxis=dict(visible=True, range=[0, 10], gridcolor="#30363d"),
                    angularaxis=dict(gridcolor="#30363d"),
                    bgcolor="#161b22",
                ),
                template="plotly_dark", height=350, margin=dict(t=30, b=30, l=60, r=60),
                showlegend=False,
            )
            st.plotly_chart(fig_radar, use_container_width=True)

    # ═══════════════════════════════════════════
    # PRICE TARGETS
    # ═══════════════════════════════════════════
    if grok_result and grok_result.get("success"):
        with error_boundary("Price Targets"):
            st.markdown("### Price Targets (12-Month)")
            pt = grok_result.get("price_targets", {})
            price = fundamentals["current_price"]

            pt_cols = st.columns(4)
            pt_cols[0].metric("Current", f"${price:.2f}")
            pt_cols[1].metric("Bear Target", f"${pt.get('bear', 0):.2f}",
                             f"{((pt.get('bear', price)/price)-1)*100:+.1f}%",
                             delta_color="inverse")
            pt_cols[2].metric("Base Target", f"${pt.get('base', 0):.2f}",
                             f"{((pt.get('base', price)/price)-1)*100:+.1f}%")
            pt_cols[3].metric("Bull Target", f"${pt.get('bull', 0):.2f}",
                             f"{((pt.get('bull', price)/price)-1)*100:+.1f}%")

            st.caption(f"Probability: Bear {pt.get('bear_prob', 25)}% · "
                      f"Base {pt.get('base_prob', 50)}% · Bull {pt.get('bull_prob', 25)}%")

            # Price target range visualization
            fig_pt = go.Figure()
            bear_p = pt.get("bear", price * 0.8)
            base_p = pt.get("base", price)
            bull_p = pt.get("bull", price * 1.2)

            fig_pt.add_trace(go.Scatter(
                x=[bear_p, base_p, bull_p], y=["Target", "Target", "Target"],
                mode="markers+text",
                marker=dict(size=[20, 25, 20], color=[COLORS["danger"], COLORS["accent"], COLORS["success"]]),
                text=[f"${bear_p:.0f}", f"${base_p:.0f}", f"${bull_p:.0f}"],
                textposition="top center",
            ))
            fig_pt.add_vline(x=price, line_dash="dash", line_color="white",
                            annotation_text=f"Current ${price:.2f}")
            fig_pt.update_layout(
                template="plotly_dark", height=120, margin=dict(t=30, b=10, l=0, r=0),
                xaxis_title="Price ($)", yaxis=dict(visible=False), showlegend=False,
            )
            st.plotly_chart(fig_pt, use_container_width=True)

    # ═══════════════════════════════════════════
    # TECHNICAL CHART
    # ═══════════════════════════════════════════
    if technicals:
        with error_boundary("Technical Analysis"):
            st.markdown("### Technical Analysis")

            fig_tech = make_subplots(rows=3, cols=1, shared_xaxes=True,
                                    row_heights=[0.6, 0.2, 0.2],
                                    vertical_spacing=0.03)

            close = technicals["close"]
            # Price + EMAs + Bollinger
            fig_tech.add_trace(go.Scatter(x=close.index, y=close, mode="lines",
                                         line=dict(color="white", width=1.5), name="Price"), row=1, col=1)
            fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["ema_20_series"],
                                         mode="lines", line=dict(color="#00d1ff", width=1), name="EMA 20"), row=1, col=1)
            fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["ema_50_series"],
                                         mode="lines", line=dict(color="#ffaa00", width=1), name="EMA 50"), row=1, col=1)
            if len(technicals["ema_200_series"]) > 0:
                fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["ema_200_series"],
                                             mode="lines", line=dict(color="#ff4444", width=1), name="EMA 200"), row=1, col=1)
            fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["bb_upper_series"],
                                         mode="lines", line=dict(color="#555", width=0.5, dash="dot"), name="BB Upper", showlegend=False), row=1, col=1)
            fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["bb_lower_series"],
                                         mode="lines", line=dict(color="#555", width=0.5, dash="dot"), name="BB Lower",
                                         fill="tonexty", fillcolor="rgba(85,85,85,0.1)", showlegend=False), row=1, col=1)

            # RSI
            rsi = technicals["rsi_series"]
            fig_tech.add_trace(go.Scatter(x=rsi.index, y=rsi, mode="lines",
                                         line=dict(color="#ad7fff", width=1.5), name="RSI"), row=2, col=1)
            fig_tech.add_hline(y=70, line_dash="dot", line_color="#ff4444", row=2, col=1)
            fig_tech.add_hline(y=30, line_dash="dot", line_color="#00ff96", row=2, col=1)

            # MACD
            macd_h = technicals["macd_hist_series"]
            colors = [COLORS["success"] if v >= 0 else COLORS["danger"] for v in macd_h]
            fig_tech.add_trace(go.Bar(x=macd_h.index, y=macd_h, marker_color=colors,
                                     name="MACD Hist", showlegend=False), row=3, col=1)
            fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["macd_line_series"],
                                         mode="lines", line=dict(color="#00d1ff", width=1), name="MACD"), row=3, col=1)
            fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["macd_signal_series"],
                                         mode="lines", line=dict(color="#ffaa00", width=1), name="Signal"), row=3, col=1)

            fig_tech.update_layout(template="plotly_dark", height=600, margin=dict(t=10, b=0, l=0, r=0),
                                  legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
            st.plotly_chart(fig_tech, use_container_width=True)

            # Technical metrics row
            tc = st.columns(5)
            tc[0].metric("RSI", f"{technicals['rsi']:.1f}")
            tc[1].metric("MACD", "Bullish" if technicals["macd_bullish"] else "Bearish")
            tc[2].metric("Bollinger %B", f"{technicals['bb_pct']:.2f}")
            tc[3].metric("ATR", f"{technicals['atr_pct']:.1f}%")
            tc[4].metric("Trend", f"{technicals['trend_signals']}/4")

    # ═══════════════════════════════════════════
    # FUNDAMENTALS
    # ═══════════════════════════════════════════
    with error_boundary("Fundamentals"):
        st.markdown("### Fundamental Profile")
        f = fundamentals

        fc = st.columns(5)
        fc[0].metric("P/E", f"{f['pe']:.1f}" if f['pe'] else "N/A")
        fc[1].metric("P/S", f"{f['ps']:.1f}" if f['ps'] else "N/A")
        fc[2].metric("Debt/Equity", f"{f['de']:.0f}%")
        fc[3].metric("FCF Yield", f"{f['fcf_yield']:.1f}%")
        fc[4].metric("Beta", f"{f['beta']:.2f}")

        fc2 = st.columns(5)
        fc2[0].metric("Rev Growth", f"{f['rev_growth']*100:+.1f}%" if f['rev_growth'] else "N/A")
        fc2[1].metric("Earnings Growth", f"{f['earnings_growth']*100:+.1f}%" if f['earnings_growth'] else "N/A")
        fc2[2].metric("Profit Margin", f"{f['margin']*100:.1f}%" if f['margin'] else "N/A")
        fc2[3].metric("Short Interest", f"{f['short_pct']*100:.1f}%" if f['short_pct'] else "N/A")
        fc2[4].metric("Div Yield", f"{f['dividend_yield']*100:.1f}%" if f['dividend_yield'] else "N/A")

        # Analyst targets
        if f["target_mean"] > 0:
            st.caption(f"Analyst Targets: ${f['target_low']:.0f} (Low) / "
                      f"${f['target_mean']:.0f} (Mean) / ${f['target_high']:.0f} (High) — "
                      f"Upside: {f['upside']:+.1f}% | Consensus: {f['rec']}")

    # ═══════════════════════════════════════════
    # DETAILED AI ANALYSIS
    # ═══════════════════════════════════════════
    if grok_result and grok_result.get("success"):
        with error_boundary("AI Analysis"):
            st.markdown("### Detailed Analysis")
            analysis = grok_result.get("analysis", {})

            for dimension in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
                text = analysis.get(dimension, "")
                if text:
                    score = grok_result.get("scores", {}).get(dimension, 5)
                    color = COLORS["success"] if score >= 7 else COLORS["warning"] if score >= 4 else COLORS["danger"]
                    st.markdown(f'**{dimension.title()}** — <span style="color:{color};font-weight:700;">{score}/10</span>',
                                unsafe_allow_html=True)
                    st.caption(text)

            st.divider()

            # Risks and Catalysts side by side
            rc = st.columns(2)
            with rc[0]:
                st.markdown("**Key Risks**")
                for risk in grok_result.get("risks", []):
                    st.markdown(f"- {risk}")
            with rc[1]:
                st.markdown("**Key Catalysts**")
                for cat in grok_result.get("catalysts", []):
                    st.markdown(f"- {cat}")

    # ═══════════════════════════════════════════
    # SENTIMENT
    # ═══════════════════════════════════════════
    with error_boundary("Sentiment"):
        st.markdown("### Sentiment")
        s = sentiment
        sc = st.columns(3)
        sc[0].metric("StockTwits Bull Ratio", f"{s['bull_ratio']:.0f}%",
                    f"{s['bull']}B / {s['bear']}Be")
        sc[1].metric("Posts Analyzed", s["total"])
        sc[2].metric("Signal", "Bullish" if s["bull_ratio"] > 60 else "Bearish" if s["bull_ratio"] < 40 else "Neutral")

    # ═══════════════════════════════════════════
    # INDIVIDUAL MODEL COMPARISON
    # ═══════════════════════════════════════════
    successful_models = {k: v for k, v in model_results.items() if v.get("success")}
    if len(successful_models) > 1:
        with error_boundary("Model Comparison"):
            st.divider()
            st.markdown("### Individual Model Views")
            st.caption("Side-by-side comparison of each AI model's independent assessment.")

            # Score comparison table
            score_rows = []
            for dim in ["technical", "fundamental", "sentiment", "macro", "valuation", "composite_score"]:
                row = {"Dimension": dim.replace("_", " ").title()}
                for k, v in successful_models.items():
                    name = v.get("model_name", k)
                    if dim == "composite_score":
                        row[name] = v.get("composite_score", "—")
                    else:
                        row[name] = v.get("scores", {}).get(dim, "—")
                # Add blended
                if dim == "composite_score":
                    row["Consensus"] = grok_result.get("composite_score", "—")
                else:
                    row["Consensus"] = grok_result.get("scores", {}).get(dim, "—")
                score_rows.append(row)

            st.dataframe(pd.DataFrame(score_rows).set_index("Dimension"), use_container_width=True)

            # Recommendation + targets comparison
            model_cols = st.columns(len(successful_models) + 1)
            for idx, (k, v) in enumerate(successful_models.items()):
                with model_cols[idx]:
                    name = v.get("model_name", k)
                    rec = v.get("recommendation", "Hold")
                    conf = v.get("confidence", 5)
                    pt = v.get("price_targets", {})
                    color = MODEL_CONFIGS.get(k, {}).get("color", "#888")

                    st.markdown(f'<div style="border-left:3px solid {color};padding-left:10px;">'
                               f'<strong>{name}</strong></div>', unsafe_allow_html=True)
                    st.metric("Recommendation", rec)
                    st.metric("Confidence", f"{conf}/10")
                    if pt:
                        st.caption(f"Targets: ${pt.get('bear',0):.0f} / ${pt.get('base',0):.0f} / ${pt.get('bull',0):.0f}")

            # Consensus column
            with model_cols[-1]:
                st.markdown(f'<div style="border-left:3px solid {COLORS["accent"]};padding-left:10px;">'
                           f'<strong>Consensus</strong></div>', unsafe_allow_html=True)
                st.metric("Recommendation", grok_result.get("recommendation", "Hold"))
                st.metric("Confidence", f"{grok_result.get('confidence', 5)}/10")
                pt = grok_result.get("price_targets", {})
                if pt:
                    st.caption(f"Targets: ${pt.get('bear',0):.0f} / ${pt.get('base',0):.0f} / ${pt.get('bull',0):.0f}")

    st.divider()
    n_models = len(successful_models) if successful_models else 0
    model_names = ", ".join(v.get("model_name", k) for k, v in successful_models.items()) if successful_models else "none"
    st.caption(f"**Disclaimer:** This analysis is AI-generated using {model_names} with live market data, "
              f"StockTwits sentiment, and social media search. "
              f"{'Scores are blended across ' + str(n_models) + ' models. ' if n_models > 1 else ''}"
              f"Not financial advice. Always conduct your own due diligence.")

else:
    st.info("Enter a ticker in the sidebar and click **Run Analysis** to begin.")
