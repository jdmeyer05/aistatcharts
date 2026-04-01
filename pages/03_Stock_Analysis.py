import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.data_engine import polygon_history, polygon_ticker_details, polygon_snapshot, polygon_financials, fetch_insider_transactions, fetch_analyst_recommendations
from src.edgar import calculate_financial_ratios, get_ratio_history, fetch_recent_8k, score_insider_transactions
import logging
import json
from datetime import datetime, timedelta
import re
from src.layout import setup_page, card_header, error_boundary, get_active_ticker, set_active_ticker, fun_loader
from src.styles import COLORS

setup_page("03_Stock_Analysis")
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
from src.api_keys import get_secret as _get_key

grok_key = _get_key("GROK_API_KEY")


@st.cache_data(ttl=900, show_spinner=False)
def fetch_stock_data(ticker: str) -> dict:
    """Fetch stock data from Polygon API — parallelized."""
    from concurrent.futures import ThreadPoolExecutor
    try:
        # Parallel batch: all independent API calls at once
        with ThreadPoolExecutor(max_workers=6) as pool:
            f_info = pool.submit(polygon_ticker_details, ticker)
            f_snap = pool.submit(polygon_snapshot, ticker)
            f_1y = pool.submit(polygon_history, ticker, 365)
            f_5y = pool.submit(polygon_history, ticker, 1825)
            f_fins = pool.submit(polygon_financials, ticker, "annual", 4)
            f_recs = pool.submit(fetch_analyst_recommendations, ticker)
            f_ins = pool.submit(fetch_insider_transactions, ticker)

        info = f_info.result() or {}
        snap = f_snap.result()
        if snap:
            info["currentPrice"] = snap.get("price", 0)
            info["previousClose"] = snap.get("prev_close", 0)

        fins = f_fins.result()
        income = fins.get("income", pd.DataFrame())
        balance = fins.get("balance", pd.DataFrame())
        cashflow = fins.get("cashflow", pd.DataFrame())

        return {
            "info": info,
            "hist_1y": f_1y.result(),
            "hist_5y": f_5y.result(),
            "income": income,
            "balance": balance,
            "cashflow": cashflow,
            "recommendations": f_recs.result(),
            "insider": f_ins.result(),
            "success": True,
        }
    except Exception as e:
        logger.error(f"Failed to fetch data for {ticker}: {e}")
        return {"success": False, "error": str(e)}


def compute_technicals(hist: pd.DataFrame, hist_full: pd.DataFrame = None) -> dict:
    """Compute technical indicators from price history.

    hist: primary history (1Y) for indicator calculation.
    hist_full: optional longer history (5Y) for timeframe toggle.
    """
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
    rsi = rsi.fillna(50)  # flat price = neutral RSI

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
        # Raw OHLCV for candlestick plotting
        "ohlc": hist[["Open", "High", "Low", "Close", "Volume"]].copy(),
        "ohlc_full": hist_full[["Open", "High", "Low", "Close", "Volume"]].copy() if hist_full is not None and not hist_full.empty else None,
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

    # ── Parallel fetch: earnings, IV, news, short interest, cross-context ──
    from concurrent.futures import ThreadPoolExecutor
    def _fetch_earnings():
        from src.market_data import fetch_analyst_estimates, fetch_earnings_history
        return {"est": fetch_analyst_estimates(ticker), "hist": fetch_earnings_history(ticker)}
    def _fetch_iv():
        from src.metrics_store import percentile_ranks_all, load_history
        return {"pctiles": percentile_ranks_all(ticker), "mhist": load_history(ticker, days=5)}
    def _fetch_news():
        from src.data_engine import fetch_ticker_news, fetch_related_companies
        return {"news": fetch_ticker_news(ticker, limit=5), "peers": fetch_related_companies(ticker)}
    def _fetch_short():
        from src.macro_data import fetch_short_interest
        return fetch_short_interest(ticker)
    def _fetch_xctx():
        from src.cross_context import build_ai_context
        return build_ai_context(ticker=ticker)

    with ThreadPoolExecutor(max_workers=5) as _pool:
        _f_earn = _pool.submit(_fetch_earnings)
        _f_iv = _pool.submit(_fetch_iv)
        _f_news = _pool.submit(_fetch_news)
        _f_short = _pool.submit(_fetch_short)
        _f_xctx = _pool.submit(_fetch_xctx)

    # Earnings data
    try:
        _earn = _f_earn.result()
        _est = _earn.get("est")
        if _est:
            lines.append("")
            lines.append("=" * 50)
            lines.append("EARNINGS & ESTIMATES")
            lines.append("=" * 50)
            if _est.get("forward_eps"):
                lines.append(f"Forward EPS: ${_est['forward_eps']:.2f} | Trailing EPS: ${_est.get('trailing_eps', 0):.2f}")
            if _est.get("eps_est_current_q"):
                lines.append(f"EPS Est (Current Q): ${_est['eps_est_current_q']:.2f} | Next Q: ${_est.get('eps_est_next_q', 0):.2f}")
            if _est.get("rev_est_current_q"):
                lines.append(f"Rev Est (Current Q): ${_est['rev_est_current_q']/1e9:.2f}B" if _est['rev_est_current_q'] > 1e9 else f"Rev Est (Current Q): ${_est['rev_est_current_q']/1e6:.0f}M")
            if _est.get("rev_growth_current_y"):
                lines.append(f"Revenue Growth Est (Current Y): {_est['rev_growth_current_y']*100:.1f}%")

        _eh = _earn.get("hist")
        if _eh is not None and not _eh.empty:
            lines.append("Recent Earnings Surprises:")
            for _, row in _eh.head(4).iterrows():
                _q = row.get("quarter", "")
                _act = row.get("actual", 0)
                _est_v = row.get("estimate", 0)
                _surp = row.get("surprise_pct", 0)
                _beat = "BEAT" if _surp > 0 else "MISS"
                lines.append(f"  {_q}: ${_act:.2f} vs ${_est_v:.2f} ({_beat} {abs(_surp):.1f}%)")
    except Exception:
        pass

    # Options / IV context
    try:
        _iv = _f_iv.result()
        _pctiles = _iv.get("pctiles")
        if _pctiles:
            lines.append("")
            lines.append("=" * 50)
            lines.append("OPTIONS / VOLATILITY CONTEXT")
            lines.append("=" * 50)
            # Try proper historical IV percentile first
            try:
                from src.options_history import get_iv_summary
                # Use ATM IV from metrics store if available, else skip
                _current_atm_iv = None
                _mhist = _iv.get("mhist")
                if _mhist is not None and not _mhist.empty and _mhist.iloc[-1].get("atm_iv"):
                    _current_atm_iv = _mhist.iloc[-1]["atm_iv"]
                _iv_sum = get_iv_summary(ticker, _current_atm_iv or 0.25, days=10) if _current_atm_iv else None
                if _iv_sum:
                    if _iv_sum.get("percentile") is not None:
                        lines.append(f"IV Percentile (10d history): {_iv_sum['percentile']:.0f}th")
                    lines.append(f"IV vs 10d avg: {_iv_sum['vs_avg_pct']:+.0f}% (range {_iv_sum['iv_low']:.1%} - {_iv_sum['iv_high']:.1%})")
                    lines.append(f"Skew trend: {_iv_sum['skew_direction']} (change {_iv_sum['skew_change']:+.3f})")
            except Exception:
                pass
            if _pctiles.get("atm_iv") is not None:
                lines.append(f"ATM IV Percentile (252d metrics): {_pctiles['atm_iv']:.0f}th")
            if _pctiles.get("put_skew") is not None:
                lines.append(f"Put Skew Percentile: {_pctiles['put_skew']:.0f}th")
            if _pctiles.get("vrp") is not None:
                lines.append(f"Vol Risk Premium Percentile: {_pctiles['vrp']:.0f}th")
            if _pctiles.get("iv_hv_ratio") is not None:
                lines.append(f"IV/HV Ratio Percentile: {_pctiles['iv_hv_ratio']:.0f}th")
            if _pctiles.get("hv20") is not None:
                lines.append(f"HV20 Percentile: {_pctiles['hv20']:.0f}th")
        _mhist = _iv.get("mhist")
        if _mhist is not None and not _mhist.empty:
            _latest = _mhist.iloc[-1]
            _parts = []
            if _latest.get("atm_iv"):
                _parts.append(f"ATM IV: {_latest['atm_iv']*100:.1f}%")
            if _latest.get("pc_ratio"):
                _parts.append(f"P/C Ratio: {_latest['pc_ratio']:.2f}")
            if _latest.get("hv20"):
                _parts.append(f"HV20: {_latest['hv20']*100:.1f}%")
            if _latest.get("vrp"):
                _parts.append(f"VRP: {_latest['vrp']*100:.1f}%")
            if _parts:
                lines.append(" | ".join(_parts))
    except Exception:
        pass

    # Recent news headlines
    try:
        _nd = _f_news.result()
        _news = _nd.get("news")
        if _news:
            lines.append("")
            lines.append("=" * 50)
            lines.append("RECENT NEWS (last 5 articles)")
            lines.append("=" * 50)
            for article in _news:
                lines.append(f"- [{article['published'][:10]}] {article['title']}")
        _peers = _nd.get("peers")
        if _peers:
            lines.append(f"\nPEER COMPANIES: {', '.join(_peers[:8])}")
    except Exception:
        pass

    # Short interest data
    try:
        _si = _f_short.result()
        if _si and _si.get("short_ratio"):
            lines.append("")
            lines.append("=" * 50)
            lines.append("SHORT INTEREST")
            lines.append("=" * 50)
            if _si.get("short_ratio"):
                lines.append(f"Days to Cover: {_si['short_ratio']:.1f}")
            if _si.get("short_pct_float"):
                lines.append(f"Short % of Float: {_si['short_pct_float']*100:.1f}%")
            if _si.get("short_shares") and _si.get("short_prior"):
                chg = _si["short_shares"] - _si["short_prior"]
                lines.append(f"Shares Short: {_si['short_shares']:,.0f} (change: {chg:+,.0f})")
    except Exception:
        pass

    # Cross-page context
    try:
        _xctx = _f_xctx.result()
        if _xctx:
            lines.append("")
            lines.append("=" * 50)
            lines.append(_xctx)
            lines.append("=" * 50)
    except Exception:
        pass

    # Inject Iran conflict context for energy/defense/commodity tickers
    _energy_tickers = {"USO", "XLE", "XOP", "OIH", "CL", "BZ", "CVX", "XOM", "COP", "SLB",
                        "HAL", "MPC", "VLO", "PSX", "OXY", "EOG", "PXD", "DVN", "FANG",
                        "UNG", "NG", "GLD", "GC", "SLV", "SI", "DBA", "WEAT",
                        "LMT", "RTX", "NOC", "GD", "BA", "HII", "LHX", "TDG",
                        "ITA", "XAR", "PPA", "DFEN"}
    _ticker_upper = ticker.upper().replace("=F", "")
    if _ticker_upper in _energy_tickers or fundamentals.get("sector") in ("Energy", "Industrials"):
        try:
            from src.db import get_client
            db = get_client()
            if db:
                # Get latest conflict analysis
                _ca = db.table("conflict_analysis").select("situation_summary, escalation_risk")\
                    .eq("region", "iran").order("timestamp", desc=True).limit(1).execute()
                if _ca.data:
                    _esc = _ca.data[0].get("escalation_risk", {})
                    if isinstance(_esc, str):
                        import json as _jc
                        _esc = _jc.loads(_esc)
                    _score = _esc.get("score", "?")
                    _level = _esc.get("level", "?")
                    _summary = _ca.data[0].get("situation_summary", "")

                    lines.append("")
                    lines.append("!" * 60)
                    lines.append("CRITICAL: ACTIVE US-ISRAEL-IRAN WAR (since Feb 28, 2026)")
                    lines.append("!" * 60)
                    lines.append(f"Escalation Score: {_score}/10 ({_level})")
                    lines.append("Key facts (ALL VERIFIED):")
                    lines.append("- Strait of Hormuz is FULLY CLOSED — zero tanker transits since Mar 25")
                    lines.append("- 13.8 mbpd of oil supply at risk (20% of global)")
                    lines.append("- US B-2 strikes on Bandar Abbas, Fordow, Natanz — IRGC Navy destroyed")
                    lines.append("- Iran launched 47 ballistic missiles at Tel Aviv/Haifa on Mar 28")
                    lines.append("- Hezbollah opened northern front — 120 rockets into Galilee")
                    lines.append("- Trump 48-hour ultimatum: reopen Hormuz or destroy all Iranian power plants")
                    lines.append("- UNSC resolution blocked by Russia/China — no diplomatic path")
                    lines.append("- USS Eisenhower carrier damaged — first US carrier combat damage since WWII")
                    if _summary:
                        lines.append(f"\nLatest assessment: {_summary[:600]}")

                    # Add model assessments if available
                    _models = _esc.get("model_assessments", [])
                    if _models:
                        lines.append("\nModel assessments:")
                        for _ma in _models[:3]:
                            lines.append(f"  {_ma.get('model','?')}: {_ma.get('score','?')}/10 — {_ma.get('rationale','')[:150]}")

                    lines.append("")
                    lines.append("THIS IS NOT A HYPOTHETICAL RISK. This is an active war.")
                    lines.append("Your analysis MUST directly address how this war impacts this specific asset.")
                    lines.append("Do NOT use generic language like 'potential supply disruptions' — name the specific impacts.")
        except Exception:
            pass

    # Inject signal engine composite if available
    try:
        from src.signal_engine import compute_composite
        _comp = compute_composite(ticker)
        if _comp and _comp["n_signals"] >= 2:
            lines.append("")
            lines.append("=" * 50)
            lines.append("CROSS-PAGE SIGNAL COMPOSITE")
            lines.append("=" * 50)
            lines.append(f"Direction: {_comp['overall_direction'].upper()} ({_comp['overall_conviction']:.0%} conviction)")
            lines.append(f"Sources: {_comp['n_signals']} ({_comp['signal_agreement']:.0%} agreement)")
            for s in _comp["signals"][:5]:
                lines.append(f"  - {s['source']}: {s['direction']} ({s['conviction']:.0%}) — {s.get('reasoning', '')[:80]}")
    except Exception:
        pass

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
    "gemini": {
        "name": "Gemini 3.1 Pro",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.1-pro-preview",
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

    from src.ai_validation import ACCURACY_CHECK
    user_prompt = f"""{stock_prompt}

{config['extra_instructions']}

{ACCURACY_CHECK}

Produce your complete analysis for {ticker}. Respond with ONLY valid JSON."""

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
            from openai import OpenAI
            client_kwargs = {"api_key": api_key}
            if config["base_url"]:
                client_kwargs["base_url"] = config["base_url"]
            client = OpenAI(**client_kwargs)
            # Standard model call
            model_kwargs = {
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": STOCK_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "max_tokens": 3000,
                "temperature": 0.3,
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

    # Confidence-weighted blending: models with higher self-reported confidence get more weight
    raw_weights = {k: max(1, v.get("confidence", 5)) for k, v in successful.items()}
    w_total = sum(raw_weights.values())
    weights = {k: w / w_total for k, w in raw_weights.items()}

    # Blend scores (confidence-weighted)
    blended_scores = {}
    for dim in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
        blended_scores[dim] = round(
            sum(v.get("scores", {}).get(dim, 5) * weights[k] for k, v in successful.items()), 1)

    composite = round(sum(blended_scores.values()) / len(blended_scores), 1)

    # Blend price targets (confidence-weighted)
    pt_keys = ["bull", "base", "bear", "bull_prob", "base_prob", "bear_prob"]
    blended_pt = {}
    for pk in pt_keys:
        blended_pt[pk] = round(
            sum(v.get("price_targets", {}).get(pk, 0) * weights[k] for k, v in successful.items()), 1)
    # Normalize probabilities
    prob_total = blended_pt.get("bull_prob", 25) + blended_pt.get("base_prob", 50) + blended_pt.get("bear_prob", 25)
    if prob_total > 0:
        for pk in ["bull_prob", "base_prob", "bear_prob"]:
            blended_pt[pk] = round(blended_pt[pk] / prob_total * 100)

    # Blend recommendation (confidence-weighted)
    rec_order = ["Strong Sell", "Sell", "Hold", "Buy", "Strong Buy"]
    rec_scores = {}
    for k, v in successful.items():
        rec = v.get("recommendation", "Hold")
        rec_scores[k] = rec_order.index(rec) if rec in rec_order else 2
    avg_rec_score = sum(rec_scores[k] * weights[k] for k in successful)
    blended_rec = rec_order[round(avg_rec_score)]

    # Blend confidence (average, penalize disagreement)
    confidences = [v.get("confidence", 5) for v in successful.values()]
    avg_conf = sum(confidences) / len(confidences)
    # Score divergence penalty: if models disagree on recommendation, lower confidence
    rec_spread = max(rec_scores.values()) - min(rec_scores.values())
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
st.markdown(f'<div style="font-size:1.6rem;font-weight:800;color:#e6edf3;margin-bottom:2px;">Stock Analysis & Recommendation</div>'
            f'<div style="font-size:0.85rem;color:{COLORS["text_muted"]};margin-bottom:16px;">Multi-model AI equity research with quantitative scoring</div>',
            unsafe_allow_html=True)

# Controls
with st.form("stock_analysis_form", border=True):
    _ctrl1, _ctrl2 = st.columns([2, 1])
    with _ctrl1:
        ticker_input = st.text_input("Ticker", value=get_active_ticker("AAPL"))
    with _ctrl2:
        st.markdown("<br>", unsafe_allow_html=True)
        _is_running = st.session_state.get("_ai_running", False)
        analyze_btn = st.form_submit_button(
            "Running..." if _is_running else "Run Analysis",
            type="primary", use_container_width=True,
            disabled=_is_running,
        )

ticker = ticker_input.strip().upper()
set_active_ticker(ticker)

if analyze_btn or f"stock_analysis_{ticker}" in st.session_state:
    if analyze_btn:
        st.session_state["_ai_running"] = True
        with fun_loader("ai"):
            stock_data = fetch_stock_data(ticker)
            if not stock_data.get("success"):
                st.error(f"Failed to load data for {ticker}: {stock_data.get('error', 'Unknown error')}")
                st.stop()

            fundamentals = compute_fundamentals(
                stock_data["info"], stock_data["income"],
                stock_data["balance"], stock_data["cashflow"]
            )
            technicals = compute_technicals(stock_data["hist_1y"], stock_data["hist_5y"])
            sentiment = fetch_stocktwits_ticker(ticker)

            # Macro context from scenario analysis + conflict data
            macro_ctx = ""
            grok_regime = st.session_state.get("grok_regime_result")
            if grok_regime and grok_regime.get("success"):
                probs = {r["name"]: r["probability"] for r in grok_regime.get("regimes", [])}
                sent = grok_regime.get("sentiment_summary", "")
                macro_ctx = f"Current regime probabilities: {probs}\nMacro sentiment: {sent}"

            # Always inject latest conflict context from Supabase (not session-dependent)
            try:
                from src.db import get_client
                _db = get_client()
                if _db:
                    _ca = _db.table("conflict_analysis").select("situation_summary, escalation_risk")\
                        .eq("region", "iran").order("timestamp", desc=True).limit(1).execute()
                    if _ca.data:
                        import json as _jmc
                        _esc = _ca.data[0].get("escalation_risk", {})
                        if isinstance(_esc, str):
                            _esc = _jmc.loads(_esc)
                        _score = _esc.get("score", "?")
                        macro_ctx += f"\n\nACTIVE WAR: US-Israel-Iran conflict (started Feb 28, 2026). Escalation: {_score}/10."
                        macro_ctx += f"\nStrait of Hormuz: CLOSED. Major oil infrastructure struck."
                        _summ = _ca.data[0].get("situation_summary", "")
                        if _summ:
                            macro_ctx += f"\nLatest intel: {_summ[:400]}"
            except Exception:
                pass

            # Run AI models in parallel
            prompt = build_stock_prompt(ticker, fundamentals, technicals, sentiment, macro_ctx)
            gemini_key = _get_key("GEMINI_API_KEY")
            anthropic_key = _get_key("ANTHROPIC_API_KEY")

            # Check AI quota and allowed models
            from src.auth import check_ai_quota, increment_ai_usage, get_allowed_models, render_upgrade_prompt
            if not check_ai_quota():
                render_upgrade_prompt("AI Stock Analysis (daily limit reached)")
                st.stop()

            allowed_models = get_allowed_models()
            api_keys = {"grok": grok_key, "gemini": gemini_key, "claude": anthropic_key}

            model_results = {}
            active_models = [m for m in allowed_models if api_keys.get(m)]
            if not active_models:
                st.warning("No AI models available. Check your API keys or subscription tier.")
            else:
                # Check AI cache first
                import hashlib
                from src.ai_cache import get_cached_ai, cache_ai_response, build_cache_key
                _stock_ai_key = build_cache_key("stock_analysis", ticker, prompt)
                _cached_stock = get_cached_ai(_stock_ai_key)

                if _cached_stock:
                    import json
                    try:
                        model_results = json.loads(_cached_stock)
                        st.toast("Loaded from AI cache (same fundamentals)")
                    except Exception:
                        _cached_stock = None

                if not _cached_stock:
                    # Run all models in parallel
                    from concurrent.futures import ThreadPoolExecutor, as_completed
                    def _call_model(mk):
                        return mk, run_model_stock_analysis(mk, api_keys[mk], prompt, ticker)
                    with ThreadPoolExecutor(max_workers=len(active_models)) as executor:
                        futures = {executor.submit(_call_model, mk): mk for mk in active_models}
                        for fut in as_completed(futures):
                            try:
                                mk, result = fut.result()
                                model_results[mk] = result
                            except Exception as e:
                                mk = futures[fut]
                                model_results[mk] = {"success": False, "error": str(e), "model_name": MODEL_CONFIGS[mk]["name"]}

                    # Cache the combined results for 2 hours
                    # Round-trip safe: serialize then deserialize to strip non-JSON types
                    try:
                        _serialized = json.loads(json.dumps(model_results, default=str))
                        cache_ai_response(_stock_ai_key, json.dumps(_serialized),
                                           model="multi", source_page="stock_analysis",
                                           ticker=ticker, ttl_hours=2, cost_estimate=0.07)
                    except Exception:
                        pass

                    # Only burn a token on fresh API call
                    _charge_key = f"ai_charged_{ticker}_{hashlib.md5(prompt.encode()).hexdigest()[:12]}"
                    if _charge_key not in st.session_state:
                        increment_ai_usage()
                        st.session_state[_charge_key] = True

            # Blend results
            blended = blend_model_results(model_results)

            # Track prediction for accuracy measurement
            try:
                from src.prediction_tracker import record_prediction
                _pt = blended.get("price_targets", {})
                record_prediction(
                    source="stock_analysis",
                    ticker=ticker,
                    prediction={
                        "direction": blended.get("recommendation", "Hold"),
                        "score": blended.get("composite_score", 0),
                        "target": _pt.get("base") or _pt.get("base_target"),
                        "confidence": blended.get("confidence", 0),
                    },
                    spot=fundamentals.get("current_price", 0),
                    metadata={"n_models": len(model_results)},
                )
            except Exception:
                pass

            # Write signal for unified engine
            try:
                from src.signal_engine import write_signal
                rec = blended.get("recommendation", "Hold").lower()
                sig_dir = "bull" if "buy" in rec else ("bear" if "sell" in rec else "neutral")
                sig_conv = min(1.0, blended.get("confidence", 0) / 10)
                write_signal("stock_analysis", ticker, sig_dir, sig_conv,
                             reasoning=f"{blended.get('recommendation', 'Hold')} — score {blended.get('composite_score', 0):.0f}")
            except Exception:
                pass

            st.session_state[f"stock_analysis_{ticker}"] = {
                "stock_data": stock_data,
                "fundamentals": fundamentals,
                "technicals": technicals,
                "sentiment": sentiment,
                "blended": blended,
                "model_results": model_results,
                "prompt": prompt,
            }
            st.session_state["_ai_running"] = False

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

    # Card style helper
    _card = (f'background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
             f'border-radius:10px;padding:16px 20px;margin-bottom:12px;')
    _card_sm = (f'background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
                f'border-radius:8px;padding:10px 14px;text-align:center;')

    # ═══════════════════════════════════════════
    # HEADER: Company Info + Recommendation
    # ═══════════════════════════════════════════
    with error_boundary("Company Header"):
        price = fundamentals["current_price"]
        _mkt_cap = fundamentals.get("market_cap", 0)
        _mkt_str = f"${_mkt_cap/1e12:.2f}T" if _mkt_cap >= 1e12 else (f"${_mkt_cap/1e9:.1f}B" if _mkt_cap >= 1e9 else f"${_mkt_cap/1e6:.0f}M")

        if grok_result and grok_result.get("success"):
            from html import escape as _esc
            rec = _esc(str(grok_result.get("recommendation", "Hold")))
            conf = grok_result.get("confidence", 5)
            rec_colors = {"Strong Buy": "#00ff96", "Buy": "#00cc66", "Hold": "#ffaa00",
                         "Sell": "#ff6644", "Strong Sell": "#ff4444"}
            _rc = rec_colors.get(rec, "#888")

            st.markdown(
                f'<div style="{_card}display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">'
                f'  <div>'
                f'    <div style="font-size:1.5rem;font-weight:800;color:#e6edf3;">{fundamentals["name"]}</div>'
                f'    <div style="font-size:0.85rem;color:{COLORS["text_muted"]};">{ticker} &middot; {fundamentals["sector"]} &middot; {fundamentals["industry"]} &middot; {_mkt_str}</div>'
                f'  </div>'
                f'  <div style="display:flex;align-items:center;gap:16px;">'
                f'    <div style="text-align:right;">'
                f'      <div style="font-size:1.6rem;font-weight:700;color:#e6edf3;">${price:.2f}</div>'
                f'    </div>'
                f'    <div style="background:{_rc};color:#000;padding:8px 18px;border-radius:8px;text-align:center;">'
                f'      <div style="font-size:1.1rem;font-weight:800;">{rec}</div>'
                f'      <div style="font-size:0.7rem;opacity:0.7;">Confidence {conf}/10</div>'
                f'    </div>'
                f'  </div>'
                f'</div>', unsafe_allow_html=True)

            if rec in ("Strong Buy", "Buy"):
                if st.button(f"Add {ticker} to Position Book", key="add_pos_stock"):
                    try:
                        from src.position_book import add_position
                        add_position(ticker=ticker, type="stock", qty=100,
                                     entry_price=price, source_page="Stock Analysis",
                                     details={"recommendation": rec, "confidence": conf})
                        st.success(f"Added 100 shares of {ticker} @ ${price:.2f}")
                    except Exception as e:
                        st.error(f"Failed: {e}")
        else:
            st.markdown(
                f'<div style="{_card}">'
                f'  <div style="font-size:1.5rem;font-weight:800;color:#e6edf3;">{fundamentals["name"]} ({ticker})</div>'
                f'  <div style="font-size:0.85rem;color:{COLORS["text_muted"]};">{fundamentals["sector"]} &middot; {fundamentals["industry"]} &middot; {_mkt_str}</div>'
                f'  <div style="font-size:1.6rem;font-weight:700;color:#e6edf3;margin-top:8px;">${price:.2f}</div>'
                f'</div>', unsafe_allow_html=True)

    # ═══════════════════════════════════════════
    # WALL STREET ANALYST CONSENSUS
    # ═══════════════════════════════════════════
    with error_boundary("Analyst Consensus"):
        try:
            from src.market_data import fetch_analyst_estimates
            _analyst = fetch_analyst_estimates(ticker)
            if _analyst and _analyst.get("num_analysts"):
                st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:10px;">'
                            f'<span style="color:#ffaa00;">Wall Street</span> Analyst Consensus</div>', unsafe_allow_html=True)

                _rec = (_analyst.get("recommendation") or "").replace("_", " ").title()
                _rec_score = _analyst.get("rec_mean_score") or 3.0
                _rec_colors = {"Strong Buy": "#00ff96", "Buy": "#00cc66",
                              "Outperform": "#00cc66", "Hold": "#ffaa00",
                              "Underperform": "#ff6644", "Sell": "#ff4444"}
                _rc = _rec_colors.get(_rec, "#ffaa00")

                _pt_mean = _analyst.get("price_target_mean")
                _pt_low = _analyst.get("price_target_low")
                _pt_high = _analyst.get("price_target_high")
                _cur_price = fundamentals.get("current_price", 0) or 0
                _upside = (_pt_mean / _cur_price - 1) * 100 if _cur_price > 0 and _pt_mean else 0
                _up_color = "#00ff96" if _upside > 0 else "#ff4444"

                _sb = _analyst.get("rec_strong_buy", 0)
                _b = _analyst.get("rec_buy", 0)
                _h = _analyst.get("rec_hold", 0)
                _s = _analyst.get("rec_sell", 0)
                _ss = _analyst.get("rec_strong_sell", 0)
                _total = _sb + _b + _h + _s + _ss
                _bull_pct = (_sb + _b) / _total * 100 if _total > 0 else 0

                _ws_html = f'<div style="{_card}display:flex;gap:10px;flex-wrap:wrap;align-items:center;">'
                # Consensus badge
                _ws_html += (f'<div style="{_card_sm}min-width:110px;border-color:{_rc};">'
                             f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">CONSENSUS</div>'
                             f'<div style="font-size:1.1rem;font-weight:800;color:{_rc};">{_rec}</div>'
                             f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};">{_rec_score:.1f}/5 &middot; {_analyst["num_analysts"]} analysts</div></div>')
                # Target
                if _pt_mean:
                    _ws_html += (f'<div style="{_card_sm}min-width:90px;">'
                                 f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">TARGET</div>'
                                 f'<div style="font-size:1.1rem;font-weight:700;color:#e6edf3;">${_pt_mean:.0f}</div>'
                                 f'<div style="font-size:0.75rem;color:{_up_color};font-weight:600;">{_upside:+.0f}%</div></div>')
                # Range
                if _pt_low and _pt_high:
                    _ws_html += (f'<div style="{_card_sm}min-width:110px;">'
                                 f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">RANGE</div>'
                                 f'<div style="font-size:1.1rem;font-weight:700;color:#e6edf3;">${_pt_low:.0f} &ndash; ${_pt_high:.0f}</div></div>')
                # Bulls / Bears
                if _total > 0:
                    _ws_html += (f'<div style="{_card_sm}min-width:80px;">'
                                 f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">BULLS</div>'
                                 f'<div style="font-size:1.1rem;font-weight:700;color:#00ff96;">{_sb + _b}/{_total}</div>'
                                 f'<div style="font-size:0.75rem;color:#00ff96;">{_bull_pct:.0f}%</div></div>')
                    _ws_html += (f'<div style="{_card_sm}min-width:80px;">'
                                 f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">BEARS</div>'
                                 f'<div style="font-size:1.1rem;font-weight:700;color:#ff4444;">{_s + _ss}/{_total}</div></div>')
                _ws_html += '</div>'
                st.markdown(_ws_html, unsafe_allow_html=True)

                # Recent upgrades/downgrades
                _ud = _analyst.get("upgrades_downgrades", [])
                if _ud:
                    with st.expander(f"Recent Analyst Actions ({len(_ud)})", expanded=False):
                        for _action in _ud[:8]:
                            _firm = _action.get("Firm", "")
                            _grade = _action.get("ToGrade", "")
                            _act_type = _action.get("Action", "")
                            _pt_cur = _action.get("currentPriceTarget", 0)
                            _date = str(_action.get("GradeDate", ""))[:10]
                            _act_color = "#00ff96" if _act_type in ("up", "init") else ("#ff4444" if _act_type == "down" else "#888")
                            _pt_str = f" &rarr; ${_pt_cur:.0f}" if _pt_cur else ""
                            st.markdown(
                                f'<div style="padding:3px 0;font-size:0.85rem;border-bottom:1px solid {COLORS["card_border"]};">'
                                f'<span style="color:{_act_color};font-weight:600;">{_firm}</span> '
                                f'<span style="color:#ccc;">{_grade}{_pt_str}</span> '
                                f'<span style="color:{COLORS["text_muted"]};font-size:0.75rem;">{_date}</span></div>',
                                unsafe_allow_html=True)

                # Write analyst signal to signal engine
                try:
                    from src.signal_engine import write_signal
                    if _rec_score and _rec_score <= 2.5:
                        _a_dir = "bull"
                    elif _rec_score and _rec_score >= 3.5:
                        _a_dir = "bear"
                    else:
                        _a_dir = "neutral"
                    _a_conv = max(0.0, min(1.0, (5 - float(_rec_score)) / 4))  # 1=1.0, 5=0.0
                    write_signal("analyst_consensus", ticker, _a_dir, round(_a_conv, 2),
                                 reasoning=f"{_rec} ({_rec_score:.1f}/5), {_analyst['num_analysts']} analysts, target ${_pt_mean:.0f}" if _pt_mean else f"{_rec}")
                except Exception:
                    pass
        except Exception:
            pass

    # Executive summary — blended from all models
    if grok_result and grok_result.get("success"):
        individual = grok_result.get("model_results", {})
        agreement = grok_result.get("agreement", "")
        # Clean markdown artifacts from agreement string
        _clean_agreement = agreement.replace("**", "")

        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:10px;">Executive Summary</div>',
                    unsafe_allow_html=True)

        if _clean_agreement:
            st.markdown(f'<div style="font-size:0.9rem;color:#ccc;margin-bottom:12px;">'
                        f'<strong style="color:#e6edf3;">Model Consensus:</strong> {_clean_agreement}</div>',
                        unsafe_allow_html=True)

        # Per-model summaries as expandable cards with model color accent
        _has_summaries = False
        for k, v in individual.items():
            if v.get("success") and v.get("summary"):
                _has_summaries = True
                _mname = v.get("model_name", k)
                _mcolor = MODEL_CONFIGS.get(k, {}).get("color", "#888")
                _mrec = v.get("recommendation", "Hold")
                _mconf = v.get("confidence", 5)
                _msummary = v.get("summary", "")
                with st.expander(f"{_mname} — {_mrec} (Confidence {_mconf}/10)", expanded=False):
                    st.markdown(f'<div style="font-size:0.88rem;color:#ccc;line-height:1.5;border-left:3px solid {_mcolor};padding-left:12px;">'
                                f'{_msummary}</div>', unsafe_allow_html=True)

        if not _has_summaries and grok_result.get("summary"):
            st.markdown(f'<div style="{_card}font-size:0.88rem;color:#ccc;line-height:1.5;">{grok_result["summary"]}</div>',
                        unsafe_allow_html=True)

    # ═══════════════════════════════════════════
    # SCORECARD
    # ═══════════════════════════════════════════
    if grok_result and grok_result.get("success"):
        with error_boundary("Scorecard"):
            st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:10px;">Multi-Dimensional Scorecard</div>', unsafe_allow_html=True)
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
                _is_comp = label == "Composite"
                _bw = "2px" if _is_comp else "1px"
                _bg = f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},0.08)" if _is_comp else COLORS["card_bg"]
                with col:
                    st.markdown(
                        f'<div style="text-align:center;padding:10px 6px;border:{_bw} solid {color};'
                        f'border-radius:8px;background:{_bg};">'
                        f'<div style="font-size:1.8rem;font-weight:800;color:{color};">{score:.0f}</div>'
                        f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{label}</div></div>',
                        unsafe_allow_html=True,
                    )

            # Radar chart — overlay each model's scores + blended consensus
            categories = ["Technical", "Fundamental", "Sentiment", "Macro", "Valuation"]
            fig_radar = go.Figure()

            # Individual model traces (translucent)
            _individual = grok_result.get("model_results", {})
            for _mk, _mv in _individual.items():
                if _mv.get("success") and _mv.get("scores"):
                    _mscores = _mv["scores"]
                    _mvals = [_mscores.get(c.lower(), 5) for c in categories]
                    _mvals.append(_mvals[0])
                    _mcolor = MODEL_CONFIGS.get(_mk, {}).get("color", "#888")
                    fig_radar.add_trace(go.Scatterpolar(
                        r=_mvals, theta=categories + [categories[0]],
                        fill="toself", fillcolor=f"rgba({int(_mcolor[1:3],16)},{int(_mcolor[3:5],16)},{int(_mcolor[5:7],16)},0.05)",
                        line=dict(color=_mcolor, width=1, dash="dot"),
                        name=_mv.get("model_name", _mk),
                    ))

            # Blended consensus trace (solid, on top)
            values = [scores.get(c.lower(), 5) for c in categories]
            values.append(values[0])
            fig_radar.add_trace(go.Scatterpolar(
                r=values, theta=categories + [categories[0]],
                fill="toself", fillcolor="rgba(0,209,255,0.15)",
                line=dict(color=COLORS["accent"], width=2.5),
                marker=dict(size=6),
                name="Consensus",
            ))

            fig_radar.update_layout(
                polar=dict(
                    radialaxis=dict(visible=True, range=[0, 10], gridcolor="#30363d"),
                    angularaxis=dict(gridcolor="#30363d"),
                    bgcolor="#161b22",
                ),
                template="plotly_dark", height=380, margin=dict(t=30, b=30, l=60, r=60),
                legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5),
            )
            st.plotly_chart(fig_radar, use_container_width=True)

    # ═══════════════════════════════════════════
    # PRICE TARGETS
    # ═══════════════════════════════════════════
    if grok_result and grok_result.get("success"):
        with error_boundary("Price Targets"):
            st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:10px;">'
                        f'<span style="color:#00d1ff;">AI</span> Price Targets (12-Month)</div>', unsafe_allow_html=True)
            pt = grok_result.get("price_targets", {})
            price = fundamentals["current_price"]
            _bear = pt.get('bear') or 0
            _base = pt.get('base') or 0
            _bull = pt.get('bull') or 0

            _pt_html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;">'
            for _ptl, _ptv, _ptc, _ptchg in [
                ("Current", price, "#e6edf3", ""),
                ("Bear", _bear, "#ff4444", f"{((_bear/price)-1)*100:+.1f}%" if _bear and price else ""),
                ("Base", _base, "#00d1ff", f"{((_base/price)-1)*100:+.1f}%" if _base and price else ""),
                ("Bull", _bull, "#00ff96", f"{((_bull/price)-1)*100:+.1f}%" if _bull and price else ""),
            ]:
                _chg_html = f'<div style="font-size:0.7rem;color:{_ptc};">{_ptchg}</div>' if _ptchg else ""
                _pt_html += (f'<div style="{_card_sm}flex:1;min-width:100px;">'
                             f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{_ptl}</div>'
                             f'<div style="font-size:1.2rem;font-weight:700;color:{_ptc};">${_ptv:.2f}</div>'
                             f'{_chg_html}</div>')
            _pt_html += '</div>'
            st.markdown(_pt_html, unsafe_allow_html=True)
            st.caption(f"Probability: Bear {pt.get('bear_prob', 25)}% · "
                      f"Base {pt.get('base_prob', 50)}% · Bull {pt.get('bull_prob', 25)}%")

            # Probability-weighted price distribution
            bear_p = pt.get("bear", price * 0.8) or price * 0.8
            base_p = pt.get("base", price) or price
            bull_p = pt.get("bull", price * 1.2) or price * 1.2
            bear_prob = pt.get("bear_prob", 25)
            base_prob = pt.get("base_prob", 50)
            bull_prob = pt.get("bull_prob", 25)

            # Build smooth distribution from three scenario peaks
            from scipy.stats import norm
            _x_range = np.linspace(bear_p * 0.9, bull_p * 1.1, 300)
            _spread = max(0.01, (bull_p - bear_p) / 6)  # width of each scenario bell, floor at 0.01
            _dist = (bear_prob / 100 * norm.pdf(_x_range, bear_p, _spread) +
                     base_prob / 100 * norm.pdf(_x_range, base_p, max(0.01, _spread * 0.7)) +
                     bull_prob / 100 * norm.pdf(_x_range, bull_p, _spread))
            _dist_max = _dist.max()
            _dist = _dist / _dist_max if _dist_max > 0 else _dist  # normalize peak to 1

            fig_pt = go.Figure()
            # Shaded regions
            _bear_mask = _x_range <= (bear_p + base_p) / 2
            _bull_mask = _x_range >= (base_p + bull_p) / 2
            _base_mask = ~_bear_mask & ~_bull_mask

            fig_pt.add_trace(go.Scatter(
                x=_x_range[_bear_mask], y=_dist[_bear_mask],
                fill="tozeroy", fillcolor="rgba(255,68,68,0.2)",
                line=dict(color="#ff4444", width=0), showlegend=False))
            fig_pt.add_trace(go.Scatter(
                x=_x_range[_base_mask], y=_dist[_base_mask],
                fill="tozeroy", fillcolor="rgba(0,209,255,0.2)",
                line=dict(color="#00d1ff", width=0), showlegend=False))
            fig_pt.add_trace(go.Scatter(
                x=_x_range[_bull_mask], y=_dist[_bull_mask],
                fill="tozeroy", fillcolor="rgba(0,255,150,0.2)",
                line=dict(color="#00ff96", width=0), showlegend=False))
            # Full distribution outline
            fig_pt.add_trace(go.Scatter(
                x=_x_range, y=_dist, mode="lines",
                line=dict(color="white", width=1.5), showlegend=False))

            # Scenario markers
            for _sp, _sl, _sc, _sprob in [
                (bear_p, "Bear", "#ff4444", bear_prob),
                (base_p, "Base", "#00d1ff", base_prob),
                (bull_p, "Bull", "#00ff96", bull_prob),
            ]:
                fig_pt.add_vline(x=_sp, line_dash="dot", line_color=_sc, line_width=1)
                fig_pt.add_annotation(x=_sp, y=1.05, text=f"${_sp:.0f}<br>{_sprob}%",
                                      showarrow=False, font=dict(color=_sc, size=11))

            # Current price
            fig_pt.add_vline(x=price, line_dash="dash", line_color="white", line_width=2)
            fig_pt.add_annotation(x=price, y=1.15, text=f"<b>Current ${price:.2f}</b>",
                                  showarrow=False, font=dict(color="white", size=11))

            fig_pt.update_layout(
                template="plotly_dark", height=200, margin=dict(t=40, b=10, l=0, r=0),
                xaxis_title="Price ($)", yaxis=dict(visible=False), showlegend=False,
            )
            st.plotly_chart(fig_pt, use_container_width=True)

    # ═══════════════════════════════════════════
    # TECHNICAL CHART
    # ═══════════════════════════════════════════
    if technicals:
        with error_boundary("Technical Analysis"):
            st.markdown("### Technical Analysis")

            # Timeframe selector
            _tf_col1, _tf_col2 = st.columns([3, 1])
            with _tf_col2:
                _timeframe = st.radio("Timeframe", ["3M", "1Y", "5Y"], index=1, horizontal=True, key="tech_tf")

            # Select OHLC data based on timeframe
            ohlc = technicals["ohlc"]
            if _timeframe == "3M":
                _cutoff = ohlc.index.max() - pd.Timedelta(days=90)
                ohlc = ohlc[ohlc.index >= _cutoff]
            elif _timeframe == "5Y" and technicals.get("ohlc_full") is not None:
                ohlc = technicals["ohlc_full"]

            fig_tech = make_subplots(rows=4, cols=1, shared_xaxes=True,
                                    row_heights=[0.5, 0.15, 0.15, 0.2],
                                    vertical_spacing=0.02)

            # Candlestick chart
            fig_tech.add_trace(go.Candlestick(
                x=ohlc.index, open=ohlc["Open"], high=ohlc["High"],
                low=ohlc["Low"], close=ohlc["Close"],
                increasing_line_color="#00ff96", decreasing_line_color="#ff4444",
                increasing_fillcolor="#00ff96", decreasing_fillcolor="#ff4444",
                name="Price", showlegend=False,
            ), row=1, col=1)

            # EMAs + Bollinger (only on 1Y/3M where indicators are computed)
            close = technicals["close"]
            if _timeframe != "5Y":
                _ema_idx = close.index
                fig_tech.add_trace(go.Scatter(x=_ema_idx, y=technicals["ema_20_series"],
                                             mode="lines", line=dict(color="#00d1ff", width=1), name="EMA 20"), row=1, col=1)
                fig_tech.add_trace(go.Scatter(x=_ema_idx, y=technicals["ema_50_series"],
                                             mode="lines", line=dict(color="#ffaa00", width=1), name="EMA 50"), row=1, col=1)
                if len(technicals["ema_200_series"]) > 0:
                    fig_tech.add_trace(go.Scatter(x=_ema_idx, y=technicals["ema_200_series"],
                                                 mode="lines", line=dict(color="#ff4444", width=1), name="EMA 200"), row=1, col=1)
                fig_tech.add_trace(go.Scatter(x=_ema_idx, y=technicals["bb_upper_series"],
                                             mode="lines", line=dict(color="#555", width=0.5, dash="dot"), name="BB Upper", showlegend=False), row=1, col=1)
                fig_tech.add_trace(go.Scatter(x=_ema_idx, y=technicals["bb_lower_series"],
                                             mode="lines", line=dict(color="#555", width=0.5, dash="dot"), name="BB Lower",
                                             fill="tonexty", fillcolor="rgba(85,85,85,0.1)", showlegend=False), row=1, col=1)

            # Volume bars (colored by price direction)
            _vol_colors = ["#00ff96" if c >= o else "#ff4444"
                           for c, o in zip(ohlc["Close"], ohlc["Open"])]
            fig_tech.add_trace(go.Bar(x=ohlc.index, y=ohlc["Volume"], marker_color=_vol_colors,
                                     opacity=0.5, name="Volume", showlegend=False), row=2, col=1)

            # RSI
            if _timeframe != "5Y":
                rsi = technicals["rsi_series"]
                fig_tech.add_trace(go.Scatter(x=rsi.index, y=rsi, mode="lines",
                                             line=dict(color="#ad7fff", width=1.5), name="RSI"), row=3, col=1)
                fig_tech.add_hline(y=70, line_dash="dot", line_color="#ff4444", row=3, col=1)
                fig_tech.add_hline(y=30, line_dash="dot", line_color="#00ff96", row=3, col=1)

                # MACD
                macd_h = technicals["macd_hist_series"]
                colors = [COLORS["success"] if v >= 0 else COLORS["danger"] for v in macd_h]
                fig_tech.add_trace(go.Bar(x=macd_h.index, y=macd_h, marker_color=colors,
                                         name="MACD Hist", showlegend=False), row=4, col=1)
                fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["macd_line_series"],
                                             mode="lines", line=dict(color="#00d1ff", width=1), name="MACD"), row=4, col=1)
                fig_tech.add_trace(go.Scatter(x=close.index, y=technicals["macd_signal_series"],
                                             mode="lines", line=dict(color="#ffaa00", width=1), name="Signal"), row=4, col=1)

            fig_tech.update_layout(
                template="plotly_dark", height=700, margin=dict(t=10, b=0, l=0, r=0),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                xaxis_rangeslider_visible=False,
                xaxis2_rangeslider_visible=False,
                xaxis3_rangeslider_visible=False,
                xaxis4_rangeslider_visible=False,
            )
            fig_tech.update_yaxes(title_text="Volume", row=2, col=1)
            fig_tech.update_yaxes(title_text="RSI", row=3, col=1)
            fig_tech.update_yaxes(title_text="MACD", row=4, col=1)
            st.plotly_chart(fig_tech, use_container_width=True)

            # Technical metrics row — styled cards
            _rsi_v = technicals['rsi']
            _rsi_c = "#ff4444" if _rsi_v > 70 else "#00ff96" if _rsi_v < 30 else "#e6edf3"
            _macd_v = "Bullish" if technicals["macd_bullish"] else "Bearish"
            _macd_c = "#00ff96" if technicals["macd_bullish"] else "#ff4444"
            _trend_v = technicals['trend_signals']
            _trend_c = "#00ff96" if _trend_v >= 3 else "#ff4444" if _trend_v <= 1 else "#ffaa00"
            _tech_html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px;">'
            for _tl, _tv, _tc in [
                ("RSI (14)", f"{_rsi_v:.1f}", _rsi_c),
                ("MACD", _macd_v, _macd_c),
                ("BB %B", f"{technicals['bb_pct']:.2f}", "#e6edf3"),
                ("ATR", f"{technicals['atr_pct']:.1f}%", "#e6edf3"),
                ("Trend", f"{_trend_v}/4", _trend_c),
            ]:
                _tech_html += (f'<div style="{_card_sm}flex:1;min-width:90px;">'
                               f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{_tl}</div>'
                               f'<div style="font-size:1.1rem;font-weight:700;color:{_tc};">{_tv}</div></div>')
            _tech_html += '</div>'
            st.markdown(_tech_html, unsafe_allow_html=True)

    # ═══════════════════════════════════════════
    # FUNDAMENTALS
    # ═══════════════════════════════════════════
    with error_boundary("Fundamentals"):
        st.markdown("### Fundamental Profile")
        f = fundamentals

        # Row 1: Valuation metrics
        _fund_items_1 = [
            ("P/E", f"{f['pe']:.1f}" if f['pe'] else "N/A"),
            ("P/S", f"{f['ps']:.1f}" if f['ps'] else "N/A"),
            ("P/B", f"{f['pb']:.1f}" if f['pb'] else "N/A"),
            ("D/E", f"{f['de']:.0f}%" if f['de'] else "N/A"),
            ("FCF Yield", f"{f['fcf_yield']:.1f}%"),
        ]
        _fund_items_2 = [
            ("Rev Growth", f"{f['rev_growth']*100:+.1f}%" if f['rev_growth'] else "N/A"),
            ("EPS Growth", f"{f['earnings_growth']*100:+.1f}%" if f['earnings_growth'] else "N/A"),
            ("Margin", f"{f['margin']*100:.1f}%" if f['margin'] else "N/A"),
            ("Short %", f"{f['short_pct']*100:.1f}%" if f['short_pct'] else "N/A"),
            ("Div Yield", f"{f['dividend_yield']*100:.1f}%" if f['dividend_yield'] else "N/A"),
        ]
        _fund_html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;">'
        for _label, _val in _fund_items_1:
            _fund_html += (f'<div style="{_card_sm}flex:1;min-width:100px;">'
                           f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{_label}</div>'
                           f'<div style="font-size:1.1rem;font-weight:700;color:#e6edf3;">{_val}</div></div>')
        _fund_html += '</div><div style="display:flex;gap:8px;flex-wrap:wrap;">'
        for _label, _val in _fund_items_2:
            _is_pos = _val.startswith("+") if _val != "N/A" else False
            _is_neg = _val.startswith("-") if _val != "N/A" else False
            _vc = "#00ff96" if _is_pos else ("#ff4444" if _is_neg else "#e6edf3")
            _fund_html += (f'<div style="{_card_sm}flex:1;min-width:100px;">'
                           f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{_label}</div>'
                           f'<div style="font-size:1.1rem;font-weight:700;color:{_vc};">{_val}</div></div>')
        _fund_html += '</div>'
        st.markdown(_fund_html, unsafe_allow_html=True)

    # ═══════════════════════════════════════════
    # DETAILED AI ANALYSIS
    # ═══════════════════════════════════════════
    if grok_result and grok_result.get("success"):
        with error_boundary("AI Analysis"):
            st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;">AI Detailed Analysis</div>'
                        f'<div style="font-size:0.78rem;color:{COLORS["text_muted"]};margin-bottom:10px;">Multi-model AI assessment &mdash; expand each dimension for detail</div>',
                        unsafe_allow_html=True)
            analysis = grok_result.get("analysis", {})

            for dimension in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
                text = analysis.get(dimension, "")
                if text:
                    score = grok_result.get("scores", {}).get(dimension, 5)
                    color = COLORS["success"] if score >= 7 else COLORS["warning"] if score >= 4 else COLORS["danger"]
                    with st.expander(f"{dimension.title()} — {score}/10", expanded=False):
                        st.markdown(text)

            # Risks and Catalysts side by side
            rc = st.columns(2)
            with rc[0]:
                _risks_html = f'<div style="font-size:0.85rem;font-weight:700;color:#ff4444;margin-bottom:6px;">KEY RISKS</div>'
                for risk in grok_result.get("risks", []):
                    _risks_html += f'<div style="padding:4px 0;font-size:0.85rem;color:#ccc;border-bottom:1px solid {COLORS["card_border"]};">&#x2022; {risk}</div>'
                st.markdown(f'<div style="{_card}">{_risks_html}</div>', unsafe_allow_html=True)
            with rc[1]:
                _cats_html = f'<div style="font-size:0.85rem;font-weight:700;color:#00ff96;margin-bottom:6px;">KEY CATALYSTS</div>'
                for cat in grok_result.get("catalysts", []):
                    _cats_html += f'<div style="padding:4px 0;font-size:0.85rem;color:#ccc;border-bottom:1px solid {COLORS["card_border"]};">&#x2022; {cat}</div>'
                st.markdown(f'<div style="{_card}">{_cats_html}</div>', unsafe_allow_html=True)


    # ═══════════════════════════════════════════
    # DEEP DIVE TABS
    # ═══════════════════════════════════════════
    st.divider()
    successful_models = {k: v for k, v in model_results.items() if v.get("success")}
    failed_models = {k: v for k, v in model_results.items() if not v.get("success")}

    _tab_sentiment, _tab_models, _tab_edgar, _tab_financials, _tab_peers = st.tabs([
        "Sentiment & News", "Model Comparison", "EDGAR / Insider", "Financials", "Peer Comparison"
    ])

    # ── Tab 1: Sentiment & News ──
    with _tab_sentiment:
        with error_boundary("Sentiment"):
            s = sentiment
            _sent_c1, _sent_c2 = st.columns([1, 2])
            with _sent_c1:
                _bull_r = s["bull_ratio"]
                _gauge_color = "#00ff96" if _bull_r > 60 else "#ff4444" if _bull_r < 40 else "#ffaa00"
                _signal_text = "Bullish" if _bull_r > 60 else "Bearish" if _bull_r < 40 else "Neutral"
                fig_gauge = go.Figure(go.Indicator(
                    mode="gauge+number", value=_bull_r,
                    number=dict(suffix="%", font=dict(size=28)),
                    title=dict(text=f"StockTwits: {_signal_text}", font=dict(size=14)),
                    gauge=dict(
                        axis=dict(range=[0, 100], tickwidth=1), bar=dict(color=_gauge_color),
                        steps=[dict(range=[0, 40], color="rgba(255,68,68,0.15)"),
                               dict(range=[40, 60], color="rgba(255,170,0,0.15)"),
                               dict(range=[60, 100], color="rgba(0,255,150,0.15)")],
                        threshold=dict(line=dict(color="white", width=2), value=_bull_r)),
                ))
                fig_gauge.update_layout(template="plotly_dark", height=200, margin=dict(t=40, b=0, l=30, r=30))
                st.plotly_chart(fig_gauge, use_container_width=True, config={"displayModeBar": False})
                st.caption(f"{s['bull']} bullish / {s['bear']} bearish of {s['total']} posts")
            with _sent_c2:
                _posts = s.get("recent_posts", [])
                if _posts:
                    st.markdown("**Recent Posts**")
                    for _p in _posts[:5]:
                        _ps_label = _p.get("sentiment", "\u2014")
                        _ps_color = "#00ff96" if _ps_label == "Bullish" else "#ff4444" if _ps_label == "Bearish" else "#888"
                        _ps_body = _p.get("body", "")[:140]
                        st.markdown(
                            f'<div style="padding:4px 8px;margin-bottom:3px;border-left:2px solid {_ps_color};'
                            f'background:rgba(255,255,255,0.02);border-radius:0 4px 4px 0;font-size:0.8rem;">'
                            f'<span style="color:{_ps_color};font-weight:600;">{_ps_label}</span> '
                            f'<span style="color:#ccc;">{_ps_body}</span></div>', unsafe_allow_html=True)
                else:
                    st.caption("No recent StockTwits posts found.")
            try:
                from src.data_engine import fetch_ticker_news
                _news_display = fetch_ticker_news(ticker, limit=5)
                if _news_display:
                    st.markdown("**Recent News**")
                    for _art in _news_display:
                        st.markdown(
                            f'<span style="color:#888;font-size:0.78rem;">{_art.get("published","")[:10]}</span> \u2014 '
                            f'<span style="color:#ccc;">{_art.get("title","")}</span>', unsafe_allow_html=True)
            except Exception:
                pass

    # ── Tab 2: Model Comparison ──
    with _tab_models:
        with error_boundary("Model Comparison"):
            if len(successful_models) > 1:
                score_rows = []
                for dim in ["technical", "fundamental", "sentiment", "macro", "valuation", "composite_score"]:
                    row = {"Dimension": dim.replace("_", " ").title()}
                    for k, v in successful_models.items():
                        name = v.get("model_name", k)
                        row[name] = v.get("composite_score", "\u2014") if dim == "composite_score" else v.get("scores", {}).get(dim, "\u2014")
                    row["Consensus"] = grok_result.get("composite_score", "\u2014") if dim == "composite_score" else grok_result.get("scores", {}).get(dim, "\u2014")
                    score_rows.append(row)
                st.dataframe(pd.DataFrame(score_rows).set_index("Dimension"), use_container_width=True)

            _n_cols = len(successful_models) + (1 if len(successful_models) > 1 else 0) + len(failed_models)
            if _n_cols > 0:
                model_cols = st.columns(max(1, _n_cols))
                _col_idx = 0
                for k, v in successful_models.items():
                    with model_cols[_col_idx]:
                        color = MODEL_CONFIGS.get(k, {}).get("color", "#888")
                        st.markdown(f'<div style="border-left:3px solid {color};padding-left:10px;"><strong>{v.get("model_name", k)}</strong></div>', unsafe_allow_html=True)
                        st.metric("Recommendation", v.get("recommendation", "Hold"))
                        st.metric("Confidence", f"{v.get('confidence', 5)}/10")
                        pt = v.get("price_targets", {})
                        if pt:
                            st.caption(f"Targets: ${pt.get('bear') or 0:.0f} / ${pt.get('base') or 0:.0f} / ${pt.get('bull') or 0:.0f}")
                    _col_idx += 1

                if len(successful_models) > 1:
                    with model_cols[_col_idx]:
                        st.markdown(f'<div style="border-left:3px solid {COLORS["accent"]};padding-left:10px;"><strong>Consensus</strong></div>', unsafe_allow_html=True)
                        st.metric("Recommendation", grok_result.get("recommendation", "Hold"))
                        st.metric("Confidence", f"{grok_result.get('confidence', 5)}/10")
                        pt = grok_result.get("price_targets", {})
                        if pt:
                            st.caption(f"Targets: ${pt.get('bear') or 0:.0f} / ${pt.get('base') or 0:.0f} / ${pt.get('bull') or 0:.0f}")
                    _col_idx += 1

                for k, v in failed_models.items():
                    with model_cols[min(_col_idx, len(model_cols) - 1)]:
                        _fname = MODEL_CONFIGS.get(k, {}).get("name", k)
                        _fcolor = MODEL_CONFIGS.get(k, {}).get("color", "#888")
                        st.markdown(f'<div style="border-left:3px solid {_fcolor};padding-left:10px;"><strong>{_fname}</strong></div>', unsafe_allow_html=True)
                        st.error(f"Failed: {v.get('error', 'Unknown')[:80]}")
                        if st.button(f"Retry {_fname}", key=f"retry_{k}"):
                            _rkey = _get_key(MODEL_CONFIGS.get(k, {}).get("key_name", ""))
                            _cached_prompt = cached.get("prompt", "")
                            if _rkey and _cached_prompt:
                                try:
                                    run_model_stock_analysis.clear()
                                except Exception:
                                    pass
                                _retry = run_model_stock_analysis(k, _rkey, _cached_prompt, ticker)
                                model_results[k] = _retry
                                cached_state = st.session_state.get(f"stock_analysis_{ticker}", {})
                                cached_state["model_results"] = model_results
                                cached_state["blended"] = blend_model_results(model_results)
                                st.session_state[f"stock_analysis_{ticker}"] = cached_state
                                st.rerun()
                            else:
                                st.warning("Re-run full analysis to retry this model.")
                    _col_idx += 1
            else:
                st.info("No model results available.")

    # ── Tab 3: EDGAR / Insider ──
    with _tab_edgar:
        with error_boundary("Insider Score"):
            insider_data = stock_data.get("insider") or pd.DataFrame()
            if isinstance(insider_data, pd.DataFrame) and not insider_data.empty:
                insider_score = score_insider_transactions(insider_data)
                sc = insider_score["score"]
                sig = insider_score["signal"]
                bd = insider_score["breakdown"]
                sc_color = "#00ff96" if sc >= 60 else "#ff4444" if sc <= 40 else "#ffaa00"
                st.markdown("#### Insider Activity Score")
                _is1, _is2 = st.columns([1, 3])
                with _is1:
                    st.markdown(
                        f'<div style="text-align:center;padding:16px;border:2px solid {sc_color};border-radius:10px;">'
                        f'<div style="font-size:36px;font-weight:bold;color:{sc_color};">{sc}</div>'
                        f'<div style="color:{sc_color};font-weight:600;font-size:14px;">{sig}</div>'
                        f'<div style="color:#888;font-size:11px;">Insider Score (0-100)</div></div>', unsafe_allow_html=True)
                with _is2:
                    st.markdown(f"- Buys: {bd.get('buys', 0)} | Sells: {bd.get('sells', 0)}")
                    if bd.get("csuite_buys"):
                        st.markdown(f"- C-Suite buys: {bd['csuite_buys']} (strong signal)")
                    if bd.get("cluster_buy"):
                        st.markdown("- Cluster buying detected (3+ insiders within 7 days)")
                    if bd.get("large_buys"):
                        st.markdown(f"- Large buys (>$100K): {bd['large_buys']}")
                    if len(insider_data) > 0:
                        display_cols = [c for c in ["Date", "Insider", "Title", "Transaction", "Shares", "Value"] if c in insider_data.columns]
                        if display_cols:
                            st.dataframe(insider_data[display_cols].head(10), use_container_width=True, hide_index=True)
                st.divider()
        with error_boundary("8-K Events"):
            events_8k = fetch_recent_8k(ticker, days=90)
            if events_8k:
                st.markdown("#### Recent Material Events (8-K)")
                for evt in events_8k[:8]:
                    st.markdown(
                        f'<div style="padding:6px 10px;border-left:2px solid #00d1ff;margin-bottom:4px;'
                        f'background:rgba(255,255,255,0.02);border-radius:0 4px 4px 0;">'
                        f'<span style="color:#888;font-size:0.78rem;">{evt.get("filed","")}</span> &nbsp;'
                        f'<span style="color:#00d1ff;font-weight:600;">{evt.get("form","8-K")}</span> &nbsp;'
                        f'<span style="color:#ccc;">{evt.get("company","")}</span></div>', unsafe_allow_html=True)

    # ── Tab 4: Financials ──
    with _tab_financials:
        with error_boundary("Financial Ratios"):
            ratios = calculate_financial_ratios(ticker)
            if ratios:
                _r1, _r2, _r3, _r4, _r5, _r6 = st.columns(6)
                for col, label, val, suffix in [
                    (_r1, "Net Margin", ratios.get("net_margin"), "%"),
                    (_r2, "Op. Margin", ratios.get("operating_margin"), "%"),
                    (_r3, "ROE", ratios.get("roe"), "%"),
                    (_r4, "ROA", ratios.get("roa"), "%"),
                    (_r5, "D/E", ratios.get("debt_to_equity"), "x"),
                    (_r6, "Current", ratios.get("current_ratio"), "x"),
                ]:
                    col.metric(label, f"{val}{suffix}" if val is not None else "N/A")

                rev_hist = get_ratio_history(ticker, "Revenues")
                if rev_hist.empty:
                    rev_hist = get_ratio_history(ticker, "RevenueFromContractWithCustomerExcludingAssessedTax")
                ni_hist = get_ratio_history(ticker, "NetIncomeLoss")
                op_hist = get_ratio_history(ticker, "OperatingIncomeLoss")
                eps_hist_xbrl = get_ratio_history(ticker, "EarningsPerShareDiluted")

                if not rev_hist.empty or not ni_hist.empty:
                    _rc1, _rc2 = st.columns(2)
                    if not rev_hist.empty:
                        with _rc1:
                            fig = go.Figure()
                            fig.add_trace(go.Bar(x=rev_hist["end"], y=rev_hist["val"] / 1e6, marker_color="#00d1ff"))
                            fig.update_layout(template="plotly_dark", height=250, title="Revenue ($M)", margin=dict(l=0, r=0, t=30, b=0))
                            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                    if not ni_hist.empty:
                        with _rc2:
                            fig = go.Figure()
                            fig.add_trace(go.Bar(x=ni_hist["end"], y=ni_hist["val"] / 1e6,
                                                 marker_color=["#00ff96" if v >= 0 else "#ff4444" for v in ni_hist["val"]]))
                            fig.update_layout(template="plotly_dark", height=250, title="Net Income ($M)", margin=dict(l=0, r=0, t=30, b=0))
                            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})

                _show_margin = not op_hist.empty and not rev_hist.empty
                _show_eps = not eps_hist_xbrl.empty
                if _show_margin or _show_eps:
                    _rc3, _rc4 = st.columns(2)
                    if _show_margin:
                        with _rc3:
                            _merged = pd.merge(rev_hist[["end", "val"]], op_hist[["end", "val"]], on="end", suffixes=("_rev", "_op"))
                            if not _merged.empty:
                                _merged["margin"] = (_merged["val_op"] / _merged["val_rev"].replace(0, np.nan) * 100)
                                fig = go.Figure()
                                fig.add_trace(go.Scatter(x=_merged["end"], y=_merged["margin"], mode="lines+markers",
                                                         line=dict(color="#ffaa00", width=2), marker=dict(size=8)))
                                fig.update_layout(template="plotly_dark", height=250, title="Operating Margin (%)", margin=dict(l=0, r=0, t=30, b=0))
                                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                    if _show_eps:
                        with _rc4:
                            fig = go.Figure()
                            fig.add_trace(go.Bar(x=eps_hist_xbrl["end"], y=eps_hist_xbrl["val"],
                                                 marker_color=["#00ff96" if v >= 0 else "#ff4444" for v in eps_hist_xbrl["val"]]))
                            fig.update_layout(template="plotly_dark", height=250, title="Diluted EPS ($)", margin=dict(l=0, r=0, t=30, b=0))
                            st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
            else:
                st.caption("No XBRL financial data available for this ticker.")

    # ── Tab 5: Peer Comparison ──
    with _tab_peers:
        with error_boundary("Peer Comparison"):
            try:
                from src.data_engine import fetch_related_companies
                _peers = fetch_related_companies(ticker)
                if _peers and len(_peers) >= 2:
                    _peer_list = [ticker] + [p for p in _peers[:5] if p != ticker]
                    _peer_rows = []
                    for _pt in _peer_list:
                        try:
                            _pi = polygon_ticker_details(_pt) or {}
                            _ps = polygon_snapshot(_pt)
                            _price = _ps.get("price", 0) if _ps else 0
                            _pe = _pi.get("trailingPE") or _pi.get("forwardPE") or 0
                            _pb = _pi.get("priceToBook") or 0
                            _mc = _pi.get("marketCap") or 0
                            _rg = _pi.get("revenueGrowth") or 0
                            _pm = _pi.get("profitMargins") or 0
                            _peer_rows.append({
                                "Ticker": _pt,
                                "Price": f"${_price:.2f}" if _price else "\u2014",
                                "Mkt Cap": f"${_mc/1e9:.1f}B" if _mc > 1e9 else (f"${_mc/1e6:.0f}M" if _mc else "\u2014"),
                                "P/E": f"{_pe:.1f}" if _pe else "\u2014",
                                "P/B": f"{_pb:.1f}" if _pb else "\u2014",
                                "Rev Growth": f"{_rg*100:+.1f}%" if _rg else "\u2014",
                                "Margin": f"{_pm*100:.1f}%" if _pm else "\u2014",
                            })
                        except Exception:
                            continue
                    if len(_peer_rows) > 1:
                        _pdf = pd.DataFrame(_peer_rows)
                        st.dataframe(_pdf.set_index("Ticker").style.apply(
                            lambda row: ["font-weight: bold; color: #00d1ff" if row.name == ticker else "" for _ in row],
                            axis=1), use_container_width=True)
                    else:
                        st.caption("Could not load peer data.")
                else:
                    st.caption("No peer companies found.")
            except Exception:
                st.caption("Peer comparison unavailable.")

    # ── Export + Disclaimer ──
    with error_boundary("Export"):
        if grok_result and grok_result.get("success"):
            st.divider()
            _exp = [f"# {fundamentals['name']} ({ticker}) \u2014 AI Stock Analysis Report",
                    f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    f"Models: {', '.join(v.get('model_name', k) for k, v in (model_results or {}).items() if v.get('success'))}", "",
                    f"## Recommendation: {grok_result.get('recommendation', 'N/A')}",
                    f"Confidence: {grok_result.get('confidence', 'N/A')}/10"]
            if grok_result.get("agreement"):
                _exp.append(f"Model Agreement: {grok_result['agreement']}")
            _exp.append("\n## Scores")
            for _dim in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
                _exp.append(f"- {_dim.title()}: {grok_result.get('scores', {}).get(_dim, 'N/A')}/10")
            _exp.append(f"- Composite: {grok_result.get('composite_score', 'N/A')}/10")
            _ept = grok_result.get("price_targets", {})
            _exp += ["", "## Price Targets", f"- Current: ${fundamentals['current_price']:.2f}",
                     f"- Bear: ${_ept.get('bear', 0):.2f} ({_ept.get('bear_prob', 25)}%)",
                     f"- Base: ${_ept.get('base', 0):.2f} ({_ept.get('base_prob', 50)}%)",
                     f"- Bull: ${_ept.get('bull', 0):.2f} ({_ept.get('bull_prob', 25)}%)", "", "## Analysis"]
            for _dim in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
                _text = grok_result.get("analysis", {}).get(_dim, "")
                if _text:
                    _exp += [f"### {_dim.title()}", _text, ""]
            _exp.append("## Key Risks")
            for _r in grok_result.get("risks", []):
                _exp.append(f"- {_r}")
            _exp.append("\n## Key Catalysts")
            for _c in grok_result.get("catalysts", []):
                _exp.append(f"- {_c}")
            _exp.append("\n---\n*AI-generated analysis. Not financial advice.*")
            st.download_button("Download Report", data="\n".join(_exp),
                file_name=f"{ticker}_analysis_{datetime.now().strftime('%Y%m%d')}.md",
                mime="text/markdown", key="download_report")

    n_models = len(successful_models) if successful_models else 0
    model_names = ", ".join(v.get("model_name", k) for k, v in successful_models.items()) if successful_models else "none"
    st.caption(f"**Disclaimer:** This analysis is AI-generated using {model_names} with live market data, "
              f"StockTwits sentiment, and social media search. "
              f"{'Scores are blended across ' + str(n_models) + ' models. ' if n_models > 1 else ''}"
              f"Not financial advice. Always conduct your own due diligence.")

else:
    st.info("Enter a ticker and click **Run Analysis** to begin.")
