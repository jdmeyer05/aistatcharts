"""Market data endpoints — prices, snapshots, options chains."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from api.deps import get_current_user

router = APIRouter()


@router.get("/snapshot")
async def batch_snapshot(
    tickers: str = Query(..., description="Comma-separated tickers"),
    user: str = Depends(get_current_user),
):
    """Get current prices for multiple tickers."""
    from src.data_engine import polygon_batch_snapshot
    symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    return polygon_batch_snapshot(symbols)


@router.get("/history/{ticker}")
async def price_history(
    ticker: str,
    days: int = Query(252, ge=1, le=5040),
    user: str = Depends(get_current_user),
):
    """Get daily OHLCV price history."""
    from src.data_engine import fetch_massive_data
    df = fetch_massive_data(ticker.upper(), days)
    if df is None or df.empty:
        return {"ticker": ticker, "data": []}
    df = df.reset_index()
    df.columns = [str(c) for c in df.columns]
    return {"ticker": ticker, "data": df.to_dict(orient="records")}


@router.get("/history-batch")
async def price_history_batch(
    tickers: str = Query(..., description="Comma-separated tickers"),
    days: int = Query(252, ge=1, le=1000),
    user: str = Depends(get_current_user),
):
    """Get daily close prices for multiple tickers. Returns {ticker: [{Date, Close}]}."""
    from concurrent.futures import ThreadPoolExecutor
    from src.data_engine import fetch_massive_data

    symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()][:50]  # cap at 50

    def fetch_one(tk: str):
        df = fetch_massive_data(tk, days)
        if df is None or df.empty:
            return tk, []
        df = df.reset_index()
        return tk, [{"Date": str(d), "Close": float(c)} for d, c in zip(df.iloc[:, 0], df["Close"])]

    result = {}
    with ThreadPoolExecutor(max_workers=10) as pool:
        for tk, data in pool.map(lambda s: fetch_one(s), symbols):
            result[tk] = data

    return result


@router.get("/chain/{ticker}")
async def options_chain(
    ticker: str,
    expiration: str = Query(None, description="Specific expiration date (YYYY-MM-DD)"),
    user: str = Depends(get_current_user),
):
    """Get full options chain with Greeks."""
    from src.data_engine import fetch_options_chain
    df = fetch_options_chain(ticker.upper(), expiration)
    if df is None or df.empty:
        return {"ticker": ticker, "data": []}
    return {"ticker": ticker, "count": len(df), "data": df.to_dict(orient="records")}


@router.get("/news")
async def market_news(user: str = Depends(get_current_user)):
    """Get latest cached market news scan."""
    from src.ai_cache import get_cached_ai
    from datetime import datetime, timedelta
    now = datetime.now()
    # Try current hour, then previous
    for offset in [0, 1]:
        t = now - timedelta(hours=offset)
        key = f"market_news_{t.strftime('%Y%m%d_%H')}"
        content = get_cached_ai(key)
        if content:
            return {"content": content, "age_hours": offset}
    return {"content": None, "age_hours": None}


@router.get("/heatmap")
async def heatmap_data(
    group: str = Query("sectors", description="sectors, indices, fixed_income, commodities, mega_caps"),
    user: str = Depends(get_current_user),
):
    """Get heatmap data — tickers with daily returns for a group."""
    from src.data_engine import polygon_batch_snapshot

    GROUPS = {
        "sectors": [("XLK", "Tech"), ("XLF", "Financials"), ("XLE", "Energy"), ("XLV", "Health"),
                    ("XLY", "Cons Disc"), ("XLP", "Cons Staples"), ("XLI", "Industrials"),
                    ("XLB", "Materials"), ("XLU", "Utilities"), ("XLRE", "Real Estate"), ("XLC", "Comms")],
        "indices": [("SPY", "S&P 500"), ("QQQ", "Nasdaq"), ("DIA", "Dow"), ("IWM", "Russell"),
                    ("EFA", "Int'l Dev"), ("EEM", "Emerging"), ("VGK", "Europe"), ("EWJ", "Japan"), ("FXI", "China")],
        "fixed_income": [("AGG", "US Agg"), ("TLT", "20Y+"), ("IEF", "7-10Y"), ("SHY", "1-3Y"),
                         ("TIP", "TIPS"), ("LQD", "IG Corp"), ("HYG", "HY Corp"), ("EMB", "EM Debt")],
        "commodities": [("GLD", "Gold"), ("SLV", "Silver"), ("USO", "Crude"), ("UNG", "NatGas"),
                        ("CPER", "Copper"), ("DBA", "Agriculture"), ("URA", "Uranium")],
        "mega_caps": [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("NVDA", "Nvidia"), ("AMZN", "Amazon"),
                      ("GOOGL", "Google"), ("META", "Meta"), ("TSLA", "Tesla"), ("BRK-B", "Berkshire"),
                      ("JPM", "JPMorgan"), ("V", "Visa")],
    }

    tickers_info = GROUPS.get(group, GROUPS["sectors"])
    symbols = [t for t, _ in tickers_info]
    snaps = polygon_batch_snapshot(symbols)

    items = []
    for sym, label in tickers_info:
        snap = snaps.get(sym)
        if snap and snap.get("price"):
            items.append({"symbol": sym, "label": label, "price": snap["price"], "change": snap.get("change", 0)})
    return {"group": group, "items": items}


@router.get("/events")
async def upcoming_events(user: str = Depends(get_current_user)):
    """Get upcoming macro events and FOMC dates."""
    from datetime import date
    import pandas as pd
    try:
        from src.economic_calendar import find_events_near_date, get_upcoming_fomc, FOMC_SEP_DATES
        today = date.today()
        events = find_events_near_date(today.strftime("%Y-%m-%d"), window_days=14) or []
        fomc_dates = get_upcoming_fomc(3) or []

        items = []
        for ev in events:
            days = ev.get("days_away", 0)
            if days < 0:
                continue
            items.append({"name": ev["name"], "date": ev.get("date", ""), "days_away": days})

        event_dates = {e.get("date", "") for e in events}
        for fd in fomc_dates:
            if fd not in event_dates:
                fd_dt = pd.to_datetime(fd).date()
                days = (fd_dt - today).days
                if days > 0:
                    is_sep = fd in FOMC_SEP_DATES
                    items.append({
                        "name": "FOMC + SEP/Dots" if is_sep else "FOMC Meeting",
                        "date": fd, "days_away": days,
                    })

        items.sort(key=lambda x: x["days_away"])
        return {"events": items[:8]}
    except Exception:
        return {"events": []}


@router.get("/fred/{series_id}")
async def fred_series(
    series_id: str,
    periods: int = Query(60, ge=5, le=500),
    user: str = Depends(get_current_user),
):
    """Fetch a FRED economic data series."""
    from src.market_data import fetch_fred_series
    df = fetch_fred_series(series_id.upper(), periods)
    if df is None or df.empty:
        return {"series_id": series_id, "data": []}
    df = df.reset_index(drop=True)
    if "date" in df.columns:
        df["date"] = df["date"].astype(str)
    elif "period" in df.columns:
        df["period"] = df["period"].astype(str)
    return {"series_id": series_id, "data": df.to_dict(orient="records")}


@router.get("/stock-data/{ticker}")
async def stock_data_bundle(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Fetch comprehensive stock data: price, details, financials, insiders, analysts."""
    from concurrent.futures import ThreadPoolExecutor
    from src.data_engine import polygon_history, polygon_ticker_details, polygon_snapshot, polygon_financials, fetch_insider_transactions, fetch_analyst_recommendations

    ticker = ticker.upper()

    def safe(fn, *args):
        try: return fn(*args)
        except: return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        f_info = pool.submit(safe, polygon_ticker_details, ticker)
        f_snap = pool.submit(safe, polygon_snapshot, ticker)
        f_1y = pool.submit(safe, polygon_history, ticker, 365)
        f_recs = pool.submit(safe, fetch_analyst_recommendations, ticker)
        f_ins = pool.submit(safe, fetch_insider_transactions, ticker)

    info = f_info.result() or {}
    snap = f_snap.result()
    hist = f_1y.result()
    recs = f_recs.result()
    insiders = f_ins.result()

    price = snap.get("price", 0) if snap else 0
    prev = snap.get("prev_close", price) if snap else price

    hist_records = []
    if hist is not None and not hist.empty:
        hist = hist.reset_index()
        hist.columns = [str(c) for c in hist.columns]
        hist_records = hist.to_dict(orient="records")

    rec_records = []
    if recs is not None and not recs.empty:
        rec_records = recs.head(20).to_dict(orient="records")

    ins_records = []
    if insiders is not None and not insiders.empty:
        insiders = insiders.head(20)
        for col in insiders.columns:
            if insiders[col].dtype == "datetime64[ns]":
                insiders[col] = insiders[col].astype(str)
        ins_records = insiders.to_dict(orient="records")

    return {
        "ticker": ticker,
        "price": price,
        "prev_close": prev,
        "change": price - prev,
        "change_pct": (price - prev) / prev * 100 if prev else 0,
        "info": {k: v for k, v in info.items() if isinstance(v, (str, int, float, bool, type(None)))},
        "history": hist_records,
        "recommendations": rec_records,
        "insiders": ins_records,
    }


@router.get("/stock-data-full/{ticker}")
async def stock_data_full(
    ticker: str,
    days: int = Query(365, ge=60, le=1825),
    user: str = Depends(get_current_user),
):
    """Comprehensive stock data: price, technicals, fundamentals, sentiment, EDGAR, analysts.

    Used by the Stock Analysis page for full feature parity with Streamlit.
    """
    import numpy as np
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from src.data_engine import (polygon_history, polygon_ticker_details,
                                 polygon_snapshot, fetch_insider_transactions,
                                 fetch_analyst_recommendations)
    from src.edgar import (calculate_financial_ratios, fetch_recent_8k,
                           score_insider_transactions, fetch_company_facts,
                           extract_xbrl_metric)

    ticker = ticker.upper()

    def safe(fn, *args):
        try:
            return fn(*args)
        except Exception:
            return None

    # ── Parallel data fetches ──
    with ThreadPoolExecutor(max_workers=10) as pool:
        f_info = pool.submit(safe, polygon_ticker_details, ticker)
        f_snap = pool.submit(safe, polygon_snapshot, ticker)
        f_hist = pool.submit(safe, polygon_history, ticker, days)
        f_recs = pool.submit(safe, fetch_analyst_recommendations, ticker)
        f_ins = pool.submit(safe, fetch_insider_transactions, ticker)
        f_ratios = pool.submit(safe, calculate_financial_ratios, ticker)
        f_8k = pool.submit(safe, fetch_recent_8k, ticker, 90)
        # StockTwits needs curl_cffi — import inside thread
        def _fetch_st():
            try:
                from src.market_data import fetch_stocktwits_sentiment
                r = fetch_stocktwits_sentiment([ticker])
                return r[0] if r else None
            except Exception:
                return None
        f_st = pool.submit(_fetch_st)
        # XBRL history for charts
        def _fetch_xbrl_history():
            try:
                facts = fetch_company_facts(ticker)
                if not facts:
                    return {}
                result = {}
                for metric, key in [("Revenues", "revenue"), ("NetIncomeLoss", "net_income"),
                                     ("OperatingIncomeLoss", "operating_income"),
                                     ("EarningsPerShareBasic", "eps")]:
                    unit = "USD/shares" if metric == "EarningsPerShareBasic" else "USD"
                    df = extract_xbrl_metric(facts, metric, "us-gaap", unit)
                    if not df.empty:
                        # Prefer 10-K annual data, fallback to all
                        annual = df[df["form"] == "10-K"].tail(8)
                        if len(annual) < 2:
                            annual = df.tail(8)
                        result[key] = [{"period": r["end"].isoformat() if hasattr(r["end"], "isoformat") else str(r["end"]),
                                         "value": float(r["val"])} for _, r in annual.iterrows()]
                return result
            except Exception:
                return {}
        f_xbrl = pool.submit(_fetch_xbrl_history)
        # Peer companies via yfinance
        def _fetch_peers():
            try:
                import yfinance as yf
                info = yf.Ticker(ticker).info or {}
                peers = info.get("companyOfficers", None)
                # yfinance doesn't have great peer data — use sector + industry matching
                # For now, just return related tickers from the info
                related = []
                # Try to get peer tickers
                sector = info.get("sector", "")
                industry = info.get("industry", "")
                return {"sector": sector, "industry": industry,
                        "pe": info.get("trailingPE"), "ps": info.get("priceToSalesTrailing12Months"),
                        "pb": info.get("priceToBook"), "de": info.get("debtToEquity"),
                        "roe": info.get("returnOnEquity"), "margin": info.get("profitMargins"),
                        "div_yield": info.get("dividendYield"),
                        "beta": info.get("beta"), "short_pct": info.get("shortPercentOfFloat"),
                        "forward_pe": info.get("forwardPE"),
                        "rev_growth": info.get("revenueGrowth"),
                        "earnings_growth": info.get("earningsGrowth"),
                        "fcf_yield": None}  # compute below if possible
            except Exception:
                return {}
        f_peers = pool.submit(_fetch_peers)

    info = f_info.result() or {}
    snap = f_snap.result()
    hist_df = f_hist.result()
    recs = f_recs.result()
    insiders = f_ins.result()
    xbrl_ratios = f_ratios.result() or {}
    events_8k = f_8k.result() or []
    stocktwits = f_st.result()
    xbrl_history = f_xbrl.result() or {}
    yf_fundamentals = f_peers.result() or {}

    price = snap.get("price", 0) if snap else 0
    prev = snap.get("prev_close", price) if snap else price

    # ── Technical Indicators ──
    technicals = {}
    hist_records = []
    if hist_df is not None and not hist_df.empty:
        df = hist_df.copy()
        if "Close" in df.columns and len(df) > 20:
            c = df["Close"]
            # EMAs
            technicals["ema20"] = round(float(c.ewm(span=20).mean().iloc[-1]), 2)
            technicals["ema50"] = round(float(c.ewm(span=50).mean().iloc[-1]), 2) if len(c) >= 50 else None
            technicals["ema200"] = round(float(c.ewm(span=200).mean().iloc[-1]), 2) if len(c) >= 200 else None

            # RSI(14)
            delta = c.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = 100 - (100 / (1 + rs))
            technicals["rsi"] = round(float(rsi.iloc[-1]), 1) if not np.isnan(rsi.iloc[-1]) else None

            # MACD(12,26,9)
            ema12 = c.ewm(span=12).mean()
            ema26 = c.ewm(span=26).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9).mean()
            macd_hist = macd_line - signal_line
            technicals["macd"] = round(float(macd_line.iloc[-1]), 3)
            technicals["macd_signal"] = round(float(signal_line.iloc[-1]), 3)
            technicals["macd_hist"] = round(float(macd_hist.iloc[-1]), 3)
            technicals["macd_bullish"] = float(macd_line.iloc[-1]) > float(signal_line.iloc[-1])

            # Bollinger Bands(20,2)
            sma20 = c.rolling(20).mean()
            std20 = c.rolling(20).std()
            bb_upper = sma20 + 2 * std20
            bb_lower = sma20 - 2 * std20
            bb_pctb = (c - bb_lower) / (bb_upper - bb_lower)
            technicals["bb_upper"] = round(float(bb_upper.iloc[-1]), 2) if not np.isnan(bb_upper.iloc[-1]) else None
            technicals["bb_lower"] = round(float(bb_lower.iloc[-1]), 2) if not np.isnan(bb_lower.iloc[-1]) else None
            technicals["bb_pctb"] = round(float(bb_pctb.iloc[-1]), 2) if not np.isnan(bb_pctb.iloc[-1]) else None

            # ATR(14)
            if all(col in df.columns for col in ["High", "Low", "Close"]):
                h = df["High"]
                l = df["Low"]
                tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
                atr = tr.rolling(14).mean()
                technicals["atr"] = round(float(atr.iloc[-1]), 2) if not np.isnan(atr.iloc[-1]) else None
                technicals["atr_pct"] = round(float(atr.iloc[-1] / c.iloc[-1] * 100), 2) if c.iloc[-1] > 0 else None

            # Volume ratio
            if "Volume" in df.columns:
                vol_ma = df["Volume"].rolling(20).mean()
                technicals["volume_ratio"] = round(float(df["Volume"].iloc[-1] / vol_ma.iloc[-1]), 2) if vol_ma.iloc[-1] > 0 else None

            # Trend score (0-4)
            score = 0
            if c.iloc[-1] > technicals.get("ema20", 0): score += 1
            if technicals.get("ema50") and c.iloc[-1] > technicals["ema50"]: score += 1
            if technicals.get("ema200") and c.iloc[-1] > technicals["ema200"]: score += 1
            if technicals.get("ema50") and technicals.get("ema200") and technicals["ema50"] > technicals["ema200"]: score += 1
            technicals["trend_score"] = score

            # Add EMA/BB series to history for chart overlays
            df["ema20"] = c.ewm(span=20).mean()
            if len(c) >= 50:
                df["ema50"] = c.ewm(span=50).mean()
            if len(c) >= 200:
                df["ema200"] = c.ewm(span=200).mean()
            df["bb_upper"] = sma20 + 2 * std20
            df["bb_lower"] = sma20 - 2 * std20
            df["rsi"] = rsi
            df["macd_line"] = macd_line
            df["macd_signal_line"] = signal_line
            df["macd_hist"] = macd_hist

        df = df.reset_index()
        df.columns = [str(c) for c in df.columns]
        # Replace NaN with None for JSON serialization
        df = df.where(df.notna(), None)
        hist_records = df.to_dict(orient="records")

    # ── Analyst data ──
    rec_records = []
    analyst_summary = {}
    if recs is not None and not recs.empty:
        recs_out = recs.head(20).copy()
        for col in recs_out.columns:
            if recs_out[col].dtype == "datetime64[ns]":
                recs_out[col] = recs_out[col].astype(str)
        rec_records = recs_out.to_dict(orient="records")
        # Compute consensus summary
        if "rating" in recs.columns or "action" in recs.columns:
            col = "rating" if "rating" in recs.columns else "action"
            ratings = recs[col].dropna().str.lower()
            buys = ratings.str.contains("buy|overweight|outperform").sum()
            holds = ratings.str.contains("hold|neutral|equal|peer|market perform|sector perform").sum()
            sells = ratings.str.contains("sell|underweight|underperform").sum()
            total = buys + holds + sells
            analyst_summary = {
                "buys": int(buys), "holds": int(holds), "sells": int(sells),
                "total": int(total),
                "consensus": "Buy" if buys > holds + sells else "Sell" if sells > buys + holds else "Hold",
            }
        if "target_price" in recs.columns:
            targets = recs["target_price"].dropna()
            if len(targets) > 0:
                analyst_summary["target_mean"] = round(float(targets.mean()), 2)
                analyst_summary["target_low"] = round(float(targets.min()), 2)
                analyst_summary["target_high"] = round(float(targets.max()), 2)
                if price > 0:
                    analyst_summary["upside_pct"] = round((float(targets.mean()) - price) / price * 100, 1)

    # ── Insider scoring ──
    ins_records = []
    insider_score = {"score": 50, "signal": "Neutral", "breakdown": {}}
    if insiders is not None and not insiders.empty:
        for col in insiders.columns:
            if insiders[col].dtype == "datetime64[ns]":
                insiders[col] = insiders[col].astype(str)
        ins_records = insiders.head(20).to_dict(orient="records")
        try:
            # Normalize column names for score_insider_transactions
            # Polygon returns: transaction_type, title, filing_date, value
            # Scorer expects: Transaction, Title, Date, Value
            score_df = insiders.copy()
            col_map = {"transaction_type": "Transaction", "filing_date": "Date"}
            for old_name, new_name in col_map.items():
                if old_name in score_df.columns and new_name not in score_df.columns:
                    score_df = score_df.rename(columns={old_name: new_name})
            # Title and Value might already be correct or need capitalization
            if "title" in score_df.columns and "Title" not in score_df.columns:
                score_df = score_df.rename(columns={"title": "Title"})
            if "value" in score_df.columns and "Value" not in score_df.columns:
                score_df = score_df.rename(columns={"value": "Value"})
            insider_score = score_insider_transactions(score_df)
        except Exception:
            pass

    return {
        "ticker": ticker,
        "price": price,
        "prev_close": prev,
        "change": round(price - prev, 2),
        "change_pct": round((price - prev) / prev * 100, 2) if prev else 0,
        "info": {k: v for k, v in info.items() if isinstance(v, (str, int, float, bool, type(None)))},
        "history": hist_records,
        "technicals": technicals,
        "fundamentals": {**{k: v for k, v in yf_fundamentals.items() if v is not None}, **xbrl_ratios},
        "xbrl_history": xbrl_history,
        "recommendations": rec_records,
        "analyst_summary": analyst_summary,
        "insiders": ins_records,
        "insider_score": insider_score,
        "events_8k": events_8k[:8],
        "stocktwits": stocktwits,
    }


class StockAIRequest(BaseModel):
    ticker: str
    stock_prompt: str  # pre-built context string from frontend


STOCK_SYSTEM_PROMPT = """You are a senior equity research analyst at a top-tier investment bank. You are given comprehensive data
about a stock including fundamentals, technicals, sentiment, and macro context.

Your job is to produce a COMPLETE analysis with the following structure:

1. SCORES: Rate each dimension 1-10 (10 = strongest bull case):
   - technical: Based on trend, momentum, volume, support/resistance
   - fundamental: Based on valuation, growth, profitability, balance sheet
   - sentiment: Based on StockTwits data provided + any social/news sentiment you know
   - macro: How well positioned for current macro regime
   - valuation: Is the stock cheap or expensive relative to fair value

2. RECOMMENDATION: One of: "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"

3. PRICE TARGETS (12-month):
   - bull, base, bear prices
   - bull_prob, base_prob, bear_prob (must sum to 100)

4. ANALYSIS: Detailed rationale for each score (2-3 sentences each)

5. KEY RISKS: Top 3 risks
6. KEY CATALYSTS: Top 3 catalysts
7. CONFIDENCE: 1-10 how confident you are
8. SUMMARY: 3-4 sentence executive summary referencing: fundamental driver, technical setup, StockTwits sentiment, macro risk
9. SENTIMENT_PULSE: 1-2 sentences on social/StockTwits sentiment

Respond with ONLY valid JSON:
{
  "scores": {"technical": N, "fundamental": N, "sentiment": N, "macro": N, "valuation": N},
  "composite_score": N,
  "recommendation": "...",
  "price_targets": {"bull": N, "base": N, "bear": N, "bull_prob": N, "base_prob": N, "bear_prob": N},
  "analysis": {"technical": "...", "fundamental": "...", "sentiment": "...", "macro": "...", "valuation": "..."},
  "risks": ["...", "...", "..."],
  "catalysts": ["...", "...", "..."],
  "confidence": N,
  "summary": "...",
  "sentiment_pulse": "..."
}"""

STOCK_MODEL_CONFIGS = {
    "grok": {
        "name": "Grok 4.20",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.20-reasoning",
        "key_name": "GROK_API_KEY",
        "extra": "IMPORTANT: Search X/Twitter for the latest sentiment, news, and analyst commentary on this ticker.",
        "color": "#ff4444",
    },
    "gemini": {
        "name": "Gemini 3.1 Pro",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3.1-pro-preview",
        "key_name": "GEMINI_API_KEY",
        "extra": "Use your knowledge to assess the latest market conditions, analyst consensus, and any recent news.",
        "color": "#4285f4",
    },
    "claude": {
        "name": "Claude Sonnet",
        "base_url": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "key_name": "ANTHROPIC_API_KEY",
        "extra": "Use your knowledge to assess the latest market conditions, analyst consensus, and any recent news.",
        "color": "#d4a574",
    },
}


def _call_stock_model(model_key: str, stock_prompt: str, ticker: str) -> dict:
    """Call a single AI model for stock analysis."""
    import re, json, logging
    from src.api_keys import get_secret

    config = STOCK_MODEL_CONFIGS[model_key]
    api_key = get_secret(config["key_name"])
    if not api_key:
        return {"success": False, "error": f"{config['key_name']} not configured", "model_name": config["name"], "color": config["color"]}

    user_prompt = f"{stock_prompt}\n\n{config['extra']}\n\nProduce your complete analysis for {ticker}. Respond with ONLY valid JSON."

    try:
        if config["base_url"] == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=config["model"], max_tokens=3000, temperature=0.3,
                system=STOCK_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
        else:
            from openai import OpenAI
            client_kwargs = {"api_key": api_key}
            if config["base_url"]:
                client_kwargs["base_url"] = config["base_url"]
            client = OpenAI(**client_kwargs)
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
                response = client.chat.completions.create(**model_kwargs, response_format={"type": "json_object"})
            except Exception:
                response = client.chat.completions.create(**model_kwargs)
            raw = response.choices[0].message.content

        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)
        result["success"] = True
        result["model_name"] = config["name"]
        result["color"] = config["color"]
        return result
    except Exception as e:
        logging.getLogger(__name__).error(f"{config['name']} stock analysis failed for {ticker}: {e}")
        return {"success": False, "error": str(e)[:200], "model_name": config["name"], "color": config["color"]}


def _blend_stock_results(results: dict) -> dict:
    """Blend multiple model outputs into a consensus view with confidence-weighted averaging."""
    successful = {k: v for k, v in results.items() if v.get("success")}
    if not successful:
        return {"success": False, "error": "All models failed"}
    if len(successful) == 1:
        only = list(successful.values())[0]
        only["blend_note"] = f"Single model: {only.get('model_name', '?')}"
        only["model_results"] = {k: v for k, v in results.items()}
        only["agreement"] = f"Single model: **{only.get('recommendation', 'N/A')}** ({only.get('model_name', '?')})"
        return only

    n = len(successful)
    raw_weights = {k: max(1, v.get("confidence", 5)) for k, v in successful.items()}
    w_total = sum(raw_weights.values())
    weights = {k: w / w_total for k, w in raw_weights.items()}

    # Blend scores
    blended_scores = {}
    for dim in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
        blended_scores[dim] = round(
            sum(v.get("scores", {}).get(dim, 5) * weights[k] for k, v in successful.items()), 1)
    composite = round(sum(blended_scores.values()) / len(blended_scores), 1)

    # Blend price targets
    pt_keys = ["bull", "base", "bear", "bull_prob", "base_prob", "bear_prob"]
    blended_pt = {}
    for pk in pt_keys:
        blended_pt[pk] = round(
            sum(v.get("price_targets", {}).get(pk, 0) * weights[k] for k, v in successful.items()), 1)
    prob_total = blended_pt.get("bull_prob", 25) + blended_pt.get("base_prob", 50) + blended_pt.get("bear_prob", 25)
    if prob_total > 0:
        for pk in ["bull_prob", "base_prob", "bear_prob"]:
            blended_pt[pk] = round(blended_pt[pk] / prob_total * 100)

    # Blend recommendation
    rec_order = ["Strong Sell", "Sell", "Hold", "Buy", "Strong Buy"]
    rec_scores = {}
    for k, v in successful.items():
        rec = v.get("recommendation", "Hold")
        rec_scores[k] = rec_order.index(rec) if rec in rec_order else 2
    avg_rec = sum(rec_scores[k] * weights[k] for k in successful)
    blended_rec = rec_order[min(4, max(0, round(avg_rec)))]

    # Confidence with divergence penalty
    confidences = [v.get("confidence", 5) for v in successful.values()]
    avg_conf = sum(confidences) / len(confidences)
    rec_spread = max(rec_scores.values()) - min(rec_scores.values())
    blended_conf = max(1, round(avg_conf - min(2, rec_spread * 0.5)))

    # Merge analysis text
    blended_analysis = {}
    for dim in ["technical", "fundamental", "sentiment", "macro", "valuation"]:
        parts = []
        for k, v in successful.items():
            name = v.get("model_name", k)
            text = v.get("analysis", {}).get(dim, "")
            if text:
                parts.append(f"**{name}:** {text}")
        blended_analysis[dim] = " ".join(parts)

    # Merge risks/catalysts (dedupe by first 30 chars)
    all_risks, all_catalysts = [], []
    for v in successful.values():
        all_risks.extend(v.get("risks", []))
        all_catalysts.extend(v.get("catalysts", []))
    seen_r, seen_c = set(), set()
    unique_risks, unique_catalysts = [], []
    for r in all_risks:
        key = r[:30].lower()
        if key not in seen_r:
            seen_r.add(key)
            unique_risks.append(r)
    for c_item in all_catalysts:
        key = c_item[:30].lower()
        if key not in seen_c:
            seen_c.add(key)
            unique_catalysts.append(c_item)

    # Agreement note
    model_recs = {v.get("model_name", k): v.get("recommendation", "Hold") for k, v in successful.items()}
    if len(set(model_recs.values())) == 1:
        agreement = f"All {n} models agree: **{blended_rec}**"
    else:
        rec_list = ", ".join(f"{name}: {rec}" for name, rec in model_recs.items())
        agreement = f"Models diverge — {rec_list}. Blended: **{blended_rec}**"

    summaries = [v.get("summary", "") for v in successful.values() if v.get("summary")]
    pulses = [v.get("sentiment_pulse", "") for v in successful.values() if v.get("sentiment_pulse")]

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


@router.post("/stock-ai-analysis")
async def stock_ai_analysis(req: StockAIRequest, user: str = Depends(get_current_user)):
    """Run 3-model parallel stock analysis (Grok + Gemini + Claude).

    Returns blended consensus + per-model results.
    Calls take ~10-30 seconds depending on model response times.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ticker = req.ticker.upper()
    results = {}

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_call_stock_model, key, req.stock_prompt, ticker): key
                   for key in STOCK_MODEL_CONFIGS}
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                results[key] = fut.result()
            except Exception as e:
                results[key] = {"success": False, "error": str(e), "model_name": STOCK_MODEL_CONFIGS[key]["name"],
                                "color": STOCK_MODEL_CONFIGS[key]["color"]}

    blended = _blend_stock_results(results)
    return blended


class BacktestStatsRequest(BaseModel):
    returns: list[float]        # daily log returns
    trades: list[dict] = []     # [{entry_idx, exit_idx, pnl_pct, side}]
    n_strategies_tested: int = 1  # for DSR multiple testing correction
    walk_forward: bool = True
    n_bootstrap: int = 1000


@router.post("/backtest-stats")
async def backtest_stats(req: BacktestStatsRequest, user: str = Depends(get_current_user)):
    """Compute de Prado statistical tests on backtest results.

    Returns DSR, PBO estimate, sequential bootstrap p-value,
    walk-forward Sharpe across 9 train/test combos, and regime analysis.
    """
    import numpy as np
    from scipy import stats as sp_stats

    rets = np.array(req.returns)
    n = len(rets)
    if n < 60:
        return {"error": "Need at least 60 returns", "success": False}

    ann = np.sqrt(252)

    # ── Basic stats ──
    sharpe = float(rets.mean() / rets.std() * ann) if rets.std() > 0 else 0
    skew = float(sp_stats.skew(rets))
    kurt = float(sp_stats.kurtosis(rets))

    # ── Deflated Sharpe Ratio (DSR) ──
    # Adjusts observed Sharpe for number of strategies tested (multiple testing)
    # DSR = P(SR* > 0 | skew, kurtosis, n_trials)
    N = req.n_strategies_tested
    if N > 1 and rets.std() > 0:
        # Expected max Sharpe from N iid trials (Euler-Mascheroni approximation)
        e_max_sr = sp_stats.norm.ppf(1 - 1 / N) * (1 - 0.5772 / np.log(N)) if N > 1 else 0
        # Variance of Sharpe estimator (Lo 2002, adjusted for non-normality)
        var_sr = (1 + 0.5 * sharpe**2 - skew * sharpe + (kurt / 4) * sharpe**2) / (n - 1)
        se_sr = np.sqrt(max(var_sr, 1e-10))
        dsr = float(sp_stats.norm.cdf((sharpe - e_max_sr) / se_sr))
    else:
        dsr = float(sp_stats.norm.cdf(sharpe / (1 / np.sqrt(n - 1)))) if n > 1 else 0.5

    # ── PBO Estimate (simplified CPCV approximation) ──
    # Split data into S subsets, test all train/test combos
    S = min(10, max(4, n // 100))
    fold_size = n // S
    oos_sharpes = []
    for i in range(S):
        test_start = i * fold_size
        test_end = min(test_start + fold_size, n)
        test_rets = rets[test_start:test_end]
        if len(test_rets) > 10 and test_rets.std() > 0:
            oos_sharpes.append(float(test_rets.mean() / test_rets.std() * ann))
    pbo = round(sum(1 for s in oos_sharpes if s < 0) / max(len(oos_sharpes), 1), 2) if oos_sharpes else None

    # ── Sequential Bootstrap p-value ──
    boot_sharpes = []
    for _ in range(req.n_bootstrap):
        # Block bootstrap (block size ~sqrt(n))
        block = max(2, int(np.sqrt(n)))
        starts = np.random.randint(0, max(1, n - block), size=n // block + 1)
        boot_rets = np.concatenate([rets[s:s + block] for s in starts])[:n]
        if len(boot_rets) > 10 and boot_rets.std() > 0:
            boot_sharpes.append(float(boot_rets.mean() / boot_rets.std() * ann))
    boot_p = round(sum(1 for bs in boot_sharpes if bs >= sharpe) / max(len(boot_sharpes), 1), 4) if boot_sharpes else None

    # ── Walk-Forward (9 train/test combos: 3 train × 3 test windows) ──
    wf_results = []
    if req.walk_forward and n >= 504:  # need at least 2 years
        train_fracs = [0.5, 0.6, 0.7]
        test_fracs = [0.15, 0.20, 0.30]
        for tf in train_fracs:
            for testf in test_fracs:
                if tf + testf > 0.95:
                    continue
                train_n = int(n * tf)
                test_n = int(n * testf)
                step = test_n
                fold_sharpes = []
                i = 0
                while i + train_n + test_n <= n:
                    test_slice = rets[i + train_n: i + train_n + test_n]
                    if len(test_slice) > 5 and test_slice.std() > 0:
                        fold_sharpes.append(float(test_slice.mean() / test_slice.std() * ann))
                    i += step
                if fold_sharpes:
                    wf_results.append({
                        "train_pct": int(tf * 100),
                        "test_pct": int(testf * 100),
                        "n_folds": len(fold_sharpes),
                        "avg_sharpe": round(sum(fold_sharpes) / len(fold_sharpes), 3),
                        "min_sharpe": round(min(fold_sharpes), 3),
                        "max_sharpe": round(max(fold_sharpes), 3),
                        "pct_positive": round(sum(1 for s in fold_sharpes if s > 0) / len(fold_sharpes) * 100, 0),
                    })

    # ── Regime Analysis ──
    # Classify each day into vol regime + trend regime
    regimes = {}
    if n >= 60:
        vol_20 = np.array([rets[max(0, i - 20):i].std() * ann if i >= 20 else np.nan for i in range(n)])
        vol_q = np.nanpercentile(vol_20, [33, 66])
        vol_labels = np.where(vol_20 < vol_q[0], "low", np.where(vol_20 < vol_q[1], "med", "high"))

        cum_rets = np.cumsum(rets)
        sma_50 = np.array([cum_rets[max(0, i - 50):i].mean() if i >= 50 else np.nan for i in range(n)])
        trend_labels = np.where(cum_rets > sma_50, "bull", np.where(cum_rets < sma_50 - 0.02, "bear", "sideways"))

        for vol_reg in ["low", "med", "high"]:
            mask = vol_labels == vol_reg
            r = rets[mask]
            if len(r) > 10 and r.std() > 0:
                regimes[f"vol_{vol_reg}"] = {
                    "n_days": int(mask.sum()),
                    "sharpe": round(float(r.mean() / r.std() * ann), 3),
                    "avg_return": round(float(r.mean() * 252 * 100), 2),
                    "volatility": round(float(r.std() * ann * 100), 1),
                }
        for trend_reg in ["bull", "bear", "sideways"]:
            mask = trend_labels == trend_reg
            r = rets[mask]
            if len(r) > 10 and r.std() > 0:
                regimes[f"trend_{trend_reg}"] = {
                    "n_days": int(mask.sum()),
                    "sharpe": round(float(r.mean() / r.std() * ann), 3),
                    "avg_return": round(float(r.mean() * 252 * 100), 2),
                    "volatility": round(float(r.std() * ann * 100), 1),
                }

    return {
        "success": True,
        "sharpe": round(sharpe, 3),
        "dsr": round(dsr, 4),
        "dsr_verdict": "Significant" if dsr > 0.95 else "Marginal" if dsr > 0.85 else "Not Significant",
        "pbo": pbo,
        "pbo_verdict": "Low Risk" if pbo is not None and pbo < 0.3 else "Moderate" if pbo is not None and pbo < 0.5 else "High Risk" if pbo is not None else None,
        "bootstrap_p": boot_p,
        "bootstrap_verdict": "Significant" if boot_p is not None and boot_p < 0.05 else "Not Significant" if boot_p is not None else None,
        "walk_forward": wf_results,
        "regimes": regimes,
        "n_returns": n,
        "skew": round(skew, 3),
        "kurtosis": round(kurt, 3),
    }


class DailyBriefingRequest(BaseModel):
    watchlist: list[str] = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "AMZN", "META", "GOOGL"]
    account_size: int = 25000
    scan_spreads: bool = True
    scan_condors: bool = True


@router.post("/daily-briefing")
async def daily_briefing(req: DailyBriefingRequest, user: str = Depends(get_current_user)):
    """Full morning scan: market context, opportunities, positions, earnings, risk budget."""
    import numpy as np
    import pandas as pd
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime as dt
    from src.data_engine import polygon_batch_snapshot
    from src.economic_calendar import get_upcoming_fomc, FOMC_SEP_DATES

    tickers = [t.strip().upper() for t in req.watchlist if t.strip()]

    # ── 1. Market Context ──
    context_tickers = ["SPY", "QQQ", "IWM", "GLD", "TLT"]
    all_tickers = list(set(tickers + context_tickers))
    snapshots = polygon_batch_snapshot(all_tickers)

    # VIX via yfinance
    vix_price = 0
    vix_data = {}
    try:
        import yfinance as yf
        vix_tk = yf.Ticker("^VIX")
        vix_info = vix_tk.info or {}
        vix_price = vix_info.get("regularMarketPrice") or vix_info.get("previousClose") or 0
        # VIX term structure (contango/backwardation)
        try:
            vix3m = yf.Ticker("^VIX3M").info or {}
            vix3m_price = vix3m.get("regularMarketPrice") or vix3m.get("previousClose") or 0
            if vix_price > 0 and vix3m_price > 0:
                vix_data["vix3m"] = round(vix3m_price, 2)
                vix_data["term_ratio"] = round(vix3m_price / vix_price, 2)
                vix_data["term_structure"] = "Contango" if vix3m_price > vix_price * 1.02 else "Backwardation" if vix3m_price < vix_price * 0.98 else "Flat"
        except Exception:
            pass
    except Exception:
        pass

    def _safe_change(snap):
        price = snap.get("price", 0)
        prev = snap.get("prev_close", 0) or 0
        if not price or not prev or prev <= 0:
            return round(price, 2) if price else 0, 0.0
        change = round((price - prev) / prev * 100, 2)
        if change == 0 and price > 0:
            return round(price, 2), 0.0  # genuinely flat or stale
        return round(price, 2), change

    spy_price, spy_change = _safe_change(snapshots.get("SPY", {}))
    qqq_price, qqq_change = _safe_change(snapshots.get("QQQ", {}))

    # yfinance fallback for SPY/QQQ when Polygon returns 0% (after hours / stale)
    if spy_change == 0 or spy_price == 0:
        try:
            spy_yf = yf.Ticker("SPY").info or {}
            spy_price = round(spy_yf.get("regularMarketPrice") or spy_yf.get("previousClose") or spy_price, 2)
            spy_prev = spy_yf.get("regularMarketPreviousClose") or spy_yf.get("previousClose") or 0
            if spy_price > 0 and spy_prev > 0:
                spy_change = round((spy_price - spy_prev) / spy_prev * 100, 2)
        except Exception:
            pass
    if qqq_change == 0 or qqq_price == 0:
        try:
            qqq_yf = yf.Ticker("QQQ").info or {}
            qqq_price = round(qqq_yf.get("regularMarketPrice") or qqq_yf.get("previousClose") or qqq_price, 2)
            qqq_prev = qqq_yf.get("regularMarketPreviousClose") or qqq_yf.get("previousClose") or 0
            if qqq_price > 0 and qqq_prev > 0:
                qqq_change = round((qqq_price - qqq_prev) / qqq_prev * 100, 2)
        except Exception:
            pass
    vix_regime = "Low" if vix_price < 15 else "Normal" if vix_price < 20 else "Elevated" if vix_price < 30 else "High" if vix_price < 40 else "Extreme"

    # FOMC + events
    fomc_dates = get_upcoming_fomc(3)
    fomc_events = []
    for fd in fomc_dates:
        days_away = (pd.to_datetime(fd) - pd.Timestamp.now()).days
        if 0 <= days_away <= 30:
            is_sep = fd in FOMC_SEP_DATES
            fomc_events.append({"date": fd, "days_away": days_away, "type": "FOMC + SEP/Dot Plot" if is_sep else "FOMC"})

    market_context = {
        "spy": {"price": spy_price, "change_pct": spy_change},
        "vix": {"price": round(vix_price, 2), "regime": vix_regime, **vix_data},
        "qqq": {"price": qqq_price, "change_pct": qqq_change},
        "fomc_events": fomc_events,
        "timestamp": dt.now().isoformat(),
    }

    # ── 2. Watchlist + Earnings ──
    import yfinance as yf

    def _fetch_earnings(tk):
        try:
            info = yf.Ticker(tk).info or {}
            ts = info.get("earningsTimestampStart")
            if ts and ts > 0:
                ed = dt.utcfromtimestamp(ts).date()
                days = (ed - dt.now().date()).days
                if 0 < days <= 14:
                    return {"date": ed.isoformat(), "days": days}
        except Exception:
            pass
        return None

    # Parallel earnings fetch
    earnings_map = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        earn_futs = {ex.submit(_fetch_earnings, tk): tk for tk in tickers}
        for fut in as_completed(earn_futs):
            tk = earn_futs[fut]
            try:
                result = fut.result()
                if result:
                    earnings_map[tk] = result
            except Exception:
                pass

    watchlist_data = []
    for tk in tickers:
        snap = snapshots.get(tk, {})
        price, change_pct = _safe_change(snap)
        # yfinance fallback for 0% change
        if change_pct == 0 and price > 0:
            try:
                tk_yf = yf.Ticker(tk).info or {}
                yf_price = tk_yf.get("regularMarketPrice") or 0
                yf_prev = tk_yf.get("regularMarketPreviousClose") or tk_yf.get("previousClose") or 0
                if yf_price > 0 and yf_prev > 0:
                    price = round(yf_price, 2)
                    change_pct = round((yf_price - yf_prev) / yf_prev * 100, 2)
            except Exception:
                pass
        earn = earnings_map.get(tk)
        watchlist_data.append({
            "ticker": tk, "price": price, "change_pct": change_pct,
            "earnings": earn,
        })

    # ── 3. Scan for opportunities ──
    opportunities = []

    def _run_vertical_scan():
        try:
            from api.routes.scanner import VSScanRequest, scan_vertical_spreads
            scan_req = VSScanRequest(
                tickers=tickers[:12], spread_types=["bull_put", "bear_call"],
                dte_min=14, dte_max=60, short_delta=0.25, width=5,
                profit_target_pct=50, stop_multiplier=1.5,
                account_size=req.account_size, max_risk_pct=5.0, kelly_fraction=0.5,
            )
            result = scan_vertical_spreads(scan_req, user=user)
            return result.get("results", []) if isinstance(result, dict) else []
        except Exception:
            return []

    def _run_condor_scan():
        try:
            from api.routes.scanner import ICScanRequest, scan_iron_condors
            scan_req = ICScanRequest(
                tickers=tickers[:12], dte_min=21, dte_max=60,
                short_delta=0.20, wing_width=10, profit_target_pct=50,
                stop_multiplier=1.5, account_size=req.account_size, max_risk_pct=5.0,
            )
            result = scan_iron_condors(scan_req, user=user)
            return result.get("results", []) if isinstance(result, dict) else []
        except Exception:
            return []

    spread_results, condor_results = [], []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {}
        if req.scan_spreads:
            futs[pool.submit(_run_vertical_scan)] = "spreads"
        if req.scan_condors:
            futs[pool.submit(_run_condor_scan)] = "condors"
        for fut in as_completed(futs):
            kind = futs[fut]
            try:
                res = fut.result()
                if kind == "spreads": spread_results = res
                else: condor_results = res
            except Exception:
                pass

    # Merge opportunities with sector tagging
    SECTOR_MAP = {"AAPL": "Tech", "MSFT": "Tech", "NVDA": "Tech", "AMD": "Tech", "AMZN": "Tech",
                  "META": "Tech", "GOOGL": "Tech", "NFLX": "Tech", "TSLA": "Auto",
                  "SPY": "Index", "QQQ": "Index", "IWM": "Index", "DIA": "Index",
                  "GLD": "Commodity", "SMH": "Semi", "XLF": "Financials", "TLT": "Bonds",
                  "EEM": "EM", "JPM": "Financials", "BA": "Industrials"}

    # Build price lookup from watchlist
    price_map = {w["ticker"]: w["price"] for w in watchlist_data if w.get("price")}

    for r in spread_results[:15]:
        tk = r.get("ticker", "")
        opportunities.append({
            "type": "vertical", "label": r.get("spread_label", "Vertical"),
            "ticker": tk, "sector": SECTOR_MAP.get(tk, "Other"),
            "score": r.get("adj_score", 0), "pop": r.get("pop", 0),
            "premium": r.get("fill_estimate", 0), "max_risk": r.get("max_risk", 0),
            "max_profit": r.get("max_profit", 0), "rr_ratio": r.get("rr_ratio", 0),
            "contracts": r.get("contracts", 0),
            "strikes": f"${r.get('long_strike', 0):.0f}/${r.get('short_strike', 0):.0f}",
            "long_strike": r.get("long_strike", 0), "short_strike": r.get("short_strike", 0),
            "stock_price": price_map.get(tk, 0),
            "expiration": r.get("expiration", ""), "dte": r.get("dte", 0),
            "ivr": r.get("ivr"), "ivr_band": r.get("ivr_band", "N/A"),
            "liq_grade": r.get("liq_grade", "?"),
            "earnings_before": r.get("earnings_before", False),
            "inside_exp_move": r.get("inside_exp_move", False),
            "managed_wr": r.get("managed_wr", 0), "kelly_adj": r.get("kelly_adj", 0),
        })
    for r in condor_results[:10]:
        tk = r.get("ticker", "")
        opportunities.append({
            "type": "condor", "label": "Iron Condor",
            "ticker": tk, "sector": SECTOR_MAP.get(tk, "Other"),
            "score": r.get("adj_score", 0), "pop": r.get("pop", 0),
            "premium": r.get("fill_estimate", 0), "max_risk": r.get("max_risk", 0),
            "max_profit": r.get("fill_estimate", 0),
            "rr_ratio": round(r.get("fill_estimate", 0) / max(r.get("max_risk", 1), 1), 2),
            "contracts": r.get("contracts", 0),
            "strikes": f"${r.get('short_put', 0):.0f}P/${r.get('short_call', 0):.0f}C",
            "short_put": r.get("short_put", 0), "long_put": r.get("long_put", 0),
            "short_call": r.get("short_call", 0), "long_call": r.get("long_call", 0),
            "stock_price": price_map.get(tk, 0),
            "expiration": r.get("expiration", ""), "dte": r.get("dte", 0),
            "ivr": r.get("ivr"), "ivr_band": r.get("ivr_band", "N/A"),
            "liq_grade": r.get("liq_grade", "?"),
            "earnings_before": r.get("earnings_before", False), "inside_exp_move": False,
            "managed_wr": r.get("managed_wr", 0), "kelly_adj": r.get("kelly_adj", 0),
        })

    # Weight credit spreads 2x over debit (per academic research:
    # option sellers avg +20% per trade, buyers avg -3.95%)
    for opp in opportunities:
        if opp["type"] == "condor" or (opp["type"] == "vertical" and "Credit" in opp.get("label", "")):
            opp["score"] *= 1.5  # credit premium boost
    opportunities.sort(key=lambda x: x["score"], reverse=True)
    opportunities = opportunities[:15]

    # ── 4. Risk Budget ──
    total_risk_deployed = sum(o["max_risk"] * max(o["contracts"], 1) for o in opportunities[:5])
    pct_deployed = round(total_risk_deployed / max(req.account_size, 1) * 100, 1)
    risk_budget = {
        "account_size": req.account_size,
        "top5_risk": round(total_risk_deployed, 0),
        "pct_of_account": pct_deployed,
        "remaining": req.account_size - total_risk_deployed,
        "verdict": "Conservative" if pct_deployed < 15 else "Moderate" if pct_deployed < 30 else "Aggressive" if pct_deployed < 50 else "Overleveraged",
    }

    # ── 5. Concentration + Correlation ──
    ticker_counts = {}
    sector_counts = {}
    for opp in opportunities:
        tk = opp["ticker"]
        sec = opp.get("sector", "Other")
        ticker_counts[tk] = ticker_counts.get(tk, 0) + 1
        sector_counts[sec] = sector_counts.get(sec, 0) + 1

    warnings = []
    for tk, count in ticker_counts.items():
        if count > 2:
            warnings.append(f"{tk}: {count} setups — concentrated")
    for sec, count in sector_counts.items():
        if count > 3 and sec != "Index":
            warnings.append(f"{sec} sector: {count} setups — correlated risk")

    # ── 6. Computed Outlook ──
    import math
    spy_price = market_context.get("spy", {}).get("price", 0) or 0
    vix_val = market_context.get("vix", {}).get("price", 0) or 0
    # 5-day implied move: VIX/100 / sqrt(252) * sqrt(5) * SPY
    if spy_price > 0 and vix_val > 0:
        daily_vol = vix_val / 100 / math.sqrt(252)
        five_day_vol = daily_vol * math.sqrt(5)
        implied_move_pct = round(five_day_vol * 100, 2)
        implied_move_dollar = round(spy_price * five_day_vol, 2)
        implied_low = round(spy_price - implied_move_dollar, 2)
        implied_high = round(spy_price + implied_move_dollar, 2)
    else:
        implied_move_pct = 0
        implied_low = implied_high = implied_move_dollar = 0

    # Position exposure analysis
    top5 = opportunities[:5]
    exposure_notes = []
    # Sector correlation
    for sec, count in sector_counts.items():
        if count >= 3 and sec != "Index":
            tickers_in_sec = [o["ticker"] for o in top5 if o.get("sector") == sec]
            if tickers_in_sec:
                exposure_notes.append({
                    "type": "correlated",
                    "note": f"{count} {sec} setups correlated — if sector drops, {', '.join(tickers_in_sec[:3])} all lose",
                })
    # Earnings risk
    for o in top5:
        if o.get("earnings_before"):
            exposure_notes.append({
                "type": "earnings",
                "note": f"{o['ticker']} {o['label']} has earnings before expiry — IV crush risk",
            })
    # Directional exposure
    bull_count = sum(1 for o in top5 if "Bull" in o.get("label", ""))
    bear_count = sum(1 for o in top5 if "Bear" in o.get("label", ""))
    condor_count = sum(1 for o in top5 if o.get("type") == "condor")
    if bull_count >= 3:
        exposure_notes.append({"type": "directional", "note": f"{bull_count} bullish setups — vulnerable to broad selloff"})
    if bear_count >= 3:
        exposure_notes.append({"type": "directional", "note": f"{bear_count} bearish setups — vulnerable to rally"})
    if condor_count >= 2:
        exposure_notes.append({"type": "neutral", "note": f"{condor_count} condors profit if range-bound — vulnerable to breakout"})

    outlook = {
        "spy_price": spy_price,
        "vix": vix_val,
        "implied_move_pct": implied_move_pct,
        "implied_move_dollar": implied_move_dollar,
        "implied_low": implied_low,
        "implied_high": implied_high,
        "earnings": [{"ticker": tk, **edata} for tk, edata in earnings_map.items()],
        "fomc_events": market_context.get("fomc_events", []),
        "exposure_notes": exposure_notes,
    }

    return {
        "market_context": market_context,
        "watchlist": watchlist_data,
        "earnings_this_week": [{"ticker": tk, **edata} for tk, edata in earnings_map.items()],
        "opportunities": opportunities,
        "risk_budget": risk_budget,
        "warnings": warnings,
        "sector_exposure": sector_counts,
        "scan_stats": {
            "spreads_found": len(spread_results),
            "condors_found": len(condor_results),
            "top_shown": len(opportunities),
        },
        "outlook": outlook,
    }


class NewsIntelRequest(BaseModel):
    watchlist: list[str] = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]


def _grok_helpers(grok_key: str):
    """Shared helpers for Grok Responses API calls."""
    import httpx, re, json

    def _grok_request(model: str, instructions: str, prompt: str, timeout: float = 120.0) -> str:
        import time
        last_err = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(3)  # brief pause before retry
                resp = httpx.post(
                    "https://api.x.ai/v1/responses",
                    headers={"Authorization": f"Bearer {grok_key}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "instructions": instructions,
                        "input": [{"role": "user", "content": prompt}],
                        "tools": [{"type": "web_search"}, {"type": "x_search"}],
                        "temperature": 0.1,
                    },
                    timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                for out_item in data.get("output", []):
                    if out_item.get("type") == "message":
                        for content in out_item.get("content", []):
                            if content.get("type") == "output_text":
                                return content.get("text", "")
                return ""
            except Exception as e:
                last_err = e
                continue
        raise last_err  # all retries failed

    def _extract_json_array(text: str) -> list:
        cleaned = text.strip()
        cleaned = re.sub(r"^```json?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
        start = cleaned.find("[")
        if start == -1:
            return []
        depth = 0
        end = start
        for i in range(start, len(cleaned)):
            if cleaned[i] == "[": depth += 1
            elif cleaned[i] == "]": depth -= 1
            if depth == 0:
                end = i + 1
                break
        return json.loads(cleaned[start:end])

    def _dedup_by_story(items: list) -> list:
        """Cluster items about the same story, keep the best one per cluster."""
        if not items:
            return items

        CONFIDENCE_RANK = {"verified": 3, "likely": 2, "unverified": 1}
        SOURCE_RANK = {"Reuters": 5, "Bloomberg": 5, "WSJ": 5, "CNBC": 4, "AP": 4,
                       "MarketWatch": 3, "Yahoo Finance": 3, "Barron's": 3}

        def _keywords(headline: str) -> set:
            stop = {"the","and","for","that","with","from","this","has","are","was","its",
                    "new","says","amid","after","over","into","than","about","just","will",
                    "could","would","may","also","but","not","more","most","than","out","all"}
            return set(w.lower() for w in re.sub(r"[^\w\s]", "", headline).split()
                       if len(w) > 3 and w.lower() not in stop)

        def _score(item: dict) -> float:
            conf = CONFIDENCE_RANK.get(item.get("confidence", ""), 0)
            src = SOURCE_RANK.get(item.get("source", ""), 1)
            has_url = 1 if item.get("url") else 0
            return conf * 10 + src + has_url

        # Cluster by keyword overlap within same category
        clusters: list[list[dict]] = []
        cluster_kws: list[set] = []  # union of all keywords in each cluster
        for item in items:
            kw = _keywords(item.get("headline", ""))
            placed = False
            for ci, cluster in enumerate(clusters):
                # Compare against the union of ALL headlines in the cluster
                if (item.get("category") == cluster[0].get("category")
                        and len(kw & cluster_kws[ci]) >= 2):
                    cluster.append(item)
                    cluster_kws[ci] |= kw  # grow the cluster's keyword pool
                    placed = True
                    break
            if not placed:
                clusters.append([item])
                cluster_kws.append(kw.copy())

        # Pick the best item from each cluster
        deduped = []
        for cluster in clusters:
            best = max(cluster, key=_score)
            # Mention how many sources confirmed if cluster > 1
            if len(cluster) > 1:
                sources = set(c.get("source", "") for c in cluster if c.get("source"))
                if sources and best.get("verification_note"):
                    pass  # verification_note already mentions sources
                elif sources:
                    best["verification_note"] = f"Confirmed by {len(sources)} sources: {', '.join(sorted(sources)[:4])}"
            deduped.append(best)
        return deduped

    def _finalize(items: list) -> list:
        CONFIDENCE_ORDER = {"verified": 0, "likely": 1, "unverified": 2}
        CATEGORY_ORDER = {"trump": 0, "iran_oil": 1, "macro": 2, "earnings": 3, "news": 4}
        for item in items:
            item["source_type"] = "grok_live"
            if not item.get("url"): item["url"] = ""
            if not item.get("confidence"): item["confidence"] = "unverified"
            if not item.get("category"): item["category"] = "news"
        items = _dedup_by_story(items)
        items.sort(key=lambda x: (
            CATEGORY_ORDER.get(x.get("category", "news"), 9),
            CONFIDENCE_ORDER.get(x.get("confidence", "unverified"), 9),
        ))
        return items

    def _make_sources(items: list) -> dict:
        return {
            "grok_verified": sum(1 for i in items if i.get("confidence") == "verified"),
            "grok_likely": sum(1 for i in items if i.get("confidence") == "likely"),
            "grok_unverified": sum(1 for i in items if i.get("confidence") == "unverified"),
            "trump": sum(1 for i in items if i.get("category") == "trump"),
        }

    return _grok_request, _extract_json_array, _finalize, _make_sources


def _news_search_prompt(today: str, tickers_str: str) -> tuple[str, str]:
    """Return (instructions, prompt) for Pass 1 search."""
    instructions = (
        f"You are a real-time financial news scanner. TODAY IS: {today}. "
        "Use web_search and x_search tools aggressively to find LIVE breaking news. "
        "Do NOT rely on training data — SEARCH first, then report what you found."
    )
    prompt = f"""Search the web AND X/Twitter RIGHT NOW for market-moving news on: {tickers_str}

TODAY IS {today}. Only return news from the last 24 hours.

SEARCH FOR:
1. Web: Reuters, Bloomberg, CNBC, WSJ, MarketWatch, Yahoo Finance, Barron's
2. X/Twitter: @DeItaone, @zaborhedge, @LiveSquawk, @unusual_whales, @FirstSquawk, company IR accounts
3. Pre/post-market movers gapping >2% and WHY
4. Macro data releases today (CPI, jobs, PMI, Fed speakers)
5. Earnings reported last night or this morning
6. **CRITICAL — Trump / White House**: Search @POTUS, @realDonaldTrump, @WhiteHouse, Truth Social posts, and news about ANY Trump statements, executive orders, tariff announcements, trade policy, sanctions, geopolitical threats, or regulatory actions from the last 24 hours. Trump posts move markets — capture ALL of them. Return at LEAST 4 Trump items if any exist.
7. **CRITICAL — Iran conflict & oil supply**: Search for Iran war developments, US military strikes on Iran, Iranian retaliation, Strait of Hormuz, OPEC response, oil production disruptions, oil price moves, crude supply/demand impacts, tanker traffic, refinery impacts, defense contractor news. Include oil price levels (WTI, Brent) if mentioned. Return at LEAST 3 Iran/oil items.

MINIMUM TARGETS: At least 5 Trump items, at least 4 Iran/oil items, at least 8 other news/macro/earnings items. Cast a wide net — more is better, dedup happens downstream.

DO NOT RETURN opinions, predictions, or generic "market is up" filler.

For each item:
- ticker: affected ticker(s), or "MACRO" if it affects the broad market, or "OIL" / "USO" / "XLE" for oil/energy
- headline: one factual sentence
- source: specific outlet name or @handle
- impact: "bull" / "bear" / "neutral"
- time: relative (e.g. "2h ago", "pre-market", "last night AH")
- url: source URL if found, empty string if not
- category: "trump" if Trump/White House/tariffs/trade policy, "iran_oil" if Iran conflict/military/oil supply/crude prices/OPEC/Strait of Hormuz, "macro" for economic data/Fed, "earnings" for earnings, "news" for everything else

Return ONLY a JSON array. Return 20-40 items. More is better — duplicates will be removed downstream. Return [] if nothing found.
[{{"ticker":"OIL","headline":"...","source":"Reuters","impact":"bear","time":"2h ago","url":"","category":"iran_oil"}}]"""
    return instructions, prompt


@router.post("/news-intel-search")
async def news_intel_search(req: NewsIntelRequest, user: str = Depends(get_current_user)):
    """Pass 1: Fast search via grok-4-1-fast-reasoning. Returns unverified items immediately."""
    import json, re, logging
    from src.api_keys import get_secret

    _log = logging.getLogger(__name__)
    grok_key = get_secret("GROK_API_KEY")
    if not grok_key:
        return {"success": False, "error": "GROK_API_KEY not configured", "items": []}

    from datetime import datetime as _dt
    tickers = [t.strip().upper() for t in req.watchlist[:20] if t.strip()]
    today = _dt.utcnow().strftime("%A, %B %d, %Y")
    tickers_str = ", ".join(tickers[:15])

    _grok_request, _extract_json_array, _finalize, _make_sources = _grok_helpers(grok_key)

    try:
        _log.info("Grok Pass 1 (fast): searching web + X...")
        instructions, prompt = _news_search_prompt(today, tickers_str)
        raw = _grok_request("grok-4-1-fast-reasoning", instructions, prompt, timeout=90.0)
        items = _extract_json_array(raw)
        _log.info(f"Grok Pass 1: found {len(items)} items")
    except Exception as e:
        _log.warning(f"Grok Pass 1 failed: {e}")
        return {"success": False, "error": f"Grok search failed: {e}", "items": []}

    if not items:
        return {"success": True, "items": [], "sources": _make_sources([]), "total": 0}

    # Mark all as unverified
    for item in items:
        item["confidence"] = "unverified"
        item["verification_note"] = ""

    items = _finalize(items)
    return {"success": True, "items": items[:40], "sources": _make_sources(items), "total": len(items)}


class NewsVerifyRequest(BaseModel):
    items: list[dict] = []


@router.post("/news-intel-verify")
async def news_intel_verify(req: NewsVerifyRequest, user: str = Depends(get_current_user)):
    """Pass 2: Fact-check items via grok-4.20-reasoning. Returns verified items."""
    import json, re, logging
    from src.api_keys import get_secret

    _log = logging.getLogger(__name__)
    grok_key = get_secret("GROK_API_KEY")
    if not grok_key:
        return {"success": False, "error": "GROK_API_KEY not configured", "items": []}

    from datetime import datetime as _dt
    today = _dt.utcnow().strftime("%A, %B %d, %Y")

    _grok_request, _extract_json_array, _finalize, _make_sources = _grok_helpers(grok_key)

    pass1_items = req.items
    if not pass1_items:
        return {"success": True, "items": [], "sources": _make_sources([]), "total": 0}

    try:
        _log.info("Grok Pass 2 (reasoning): fact-checking...")
        claims_json = json.dumps(pass1_items, indent=1)
        raw = _grok_request(
            "grok-4.20-reasoning",
            f"You are a financial news fact-checker. TODAY IS: {today}. "
            "Use web_search and x_search to VERIFY each claim below. "
            "For each item, search for independent confirmation. Be strict.",
            f"""Fact-check each news item below by searching the web and X for independent confirmation.

CLAIMS TO VERIFY:
{claims_json}

For EACH item, search for the specific claim. Then return the same array with TWO added fields:
- confidence: "verified" (found 2+ independent sources confirming), "likely" (found 1 confirming source), "unverified" (could not find confirmation), "false" (found contradicting evidence)
- verification_note: one sentence explaining what you found or didn't find

Remove any item where confidence is "false".
Keep the original fields (ticker, headline, source, impact, time, url, category) unchanged.
Return ONLY a JSON array.""",
            timeout=120.0,
        )
        items = _extract_json_array(raw)
        _log.info(f"Grok Pass 2: {len(items)} items after verification")
    except Exception as e:
        _log.warning(f"Grok Pass 2 failed: {e} — returning unverified")
        items = pass1_items
        for item in items:
            item["confidence"] = "unverified"
            item["verification_note"] = "Fact-check pass failed — unverified"

    items = _finalize(items)
    return {"success": True, "items": items[:40], "sources": _make_sources(items), "total": len(items)}


@router.post("/news-intel")
async def news_intel(req: NewsIntelRequest, user: str = Depends(get_current_user)):
    """Legacy: runs both passes sequentially. Use /news-intel-search + /news-intel-verify for streaming UX."""
    search_result = await news_intel_search(req, user)
    if not search_result.get("success") or not search_result.get("items"):
        return search_result
    verify_req = NewsVerifyRequest(items=search_result["items"])
    return await news_intel_verify(verify_req, user)


class MarketNoteRequest(BaseModel):
    briefing_data: dict = {}
    news_items: list[dict] = []
    polymarket: list[dict] = []
    book_summary: str = ""
    signal_summary: str = ""


@router.post("/morning-note")
async def morning_note(req: MarketNoteRequest, user: str = Depends(get_current_user)):
    """Generate AI market note from scan data + news intelligence + prediction markets."""
    from src.api_keys import get_secret

    data = req.briefing_data
    mc = data.get("market_context", {})
    opps = data.get("opportunities", [])
    earn = data.get("earnings_this_week", [])
    rb = data.get("risk_budget", {})
    warns = data.get("warnings", [])
    news = req.news_items
    poly = req.polymarket

    def _pct(v): return f"{v:+.2f}%" if isinstance(v, (int, float)) else "N/A"

    ctx = f"""DATA (use ONLY these numbers):

SPY ${mc.get('spy', {}).get('price', 0) or 0} ({_pct(mc.get('spy', {}).get('change_pct'))}) | QQQ ${mc.get('qqq', {}).get('price', 0) or 0} ({_pct(mc.get('qqq', {}).get('change_pct'))})
VIX {mc.get('vix', {}).get('price', 0) or 0} {mc.get('vix', {}).get('regime', 'N/A')} | Term: {mc.get('vix', {}).get('term_structure', 'N/A')} ({mc.get('vix', {}).get('term_ratio', 'N/A')}x)
FOMC: {', '.join(f"{e['type']} {e['days_away']}d" for e in mc.get('fomc_events', [])) or 'None within 30d'}
Earnings: {', '.join(f"{e['ticker']} {e['days']}d" for e in earn) or 'None'}
"""

    if poly:
        ctx += "\nPREDICTION MARKETS (Polymarket — real money odds, sorted by near-term actionability):\n"
        for p in poly[:6]:
            outcomes = p.get("outcomes", [])
            odds = ", ".join(
                f"{o['label']}: {o['yes_pct']}%" + (f" ({o['days_out']}d out)" if o.get('days_out', 999) < 180 else "")
                for o in outcomes[:3]
            )
            ctx += f"  {p.get('title','?')}: {odds}\n"
        ctx += "  NOTE: Near-term uncertain odds (20-80%, <60 days) are most actionable. Far-out >90% odds are priced in.\n"

    if news:
        trump_news = [n for n in news if n.get("category") == "trump"]
        iran_news = [n for n in news if n.get("category") == "iran_oil"]
        other_news = [n for n in news if n.get("category") not in ("trump", "iran_oil")]
        if trump_news:
            ctx += "\nTRUMP/WHITE HOUSE NEWS:\n"
            for item in trump_news[:5]:
                conf = item.get("confidence", "unverified")
                ctx += f"  [{item.get('ticker','?')}] {item.get('headline','')} — {item.get('source','')} ({item.get('time','')}) [{item.get('impact','neutral')}] [{conf}]\n"
        if iran_news:
            ctx += "\nIRAN CONFLICT & OIL SUPPLY NEWS:\n"
            for item in iran_news[:5]:
                conf = item.get("confidence", "unverified")
                ctx += f"  [{item.get('ticker','?')}] {item.get('headline','')} — {item.get('source','')} ({item.get('time','')}) [{item.get('impact','neutral')}] [{conf}]\n"
        if other_news:
            ctx += "\nOTHER NEWS:\n"
            for item in other_news[:8]:
                conf = item.get("confidence", "unverified")
                ctx += f"  [{item.get('ticker','?')}] {item.get('headline','')} — {item.get('source','')} ({item.get('time','')}) [{item.get('impact','neutral')}] [{conf}]\n"

    if opps:
        ctx += f"\nTOP {min(len(opps), 5)} SETUPS:\n"
        for i, o in enumerate(opps[:5]):
            flags = ""
            if o.get('earnings_before'): flags += " ⚠EARN"
            if o.get('inside_exp_move'): flags += " ⚠INSIDE_EM"
            kelly = o.get('kelly_adj')
            kelly_str = f"{kelly:.1f}%" if isinstance(kelly, (int, float)) else "?"
            ctx += f"  {i+1}. {o.get('ticker','?')} {o.get('label','?')} {o.get('strikes','?')} {o.get('dte','?')}d POP:{o.get('pop','?')}% R:R:{o.get('rr_ratio','?')}x IVR:{o.get('ivr', '?')} ({o.get('ivr_band','?')}) Liq:{o.get('liq_grade','?')} Kelly:{kelly_str} → {o.get('contracts',0)}×{flags}\n"

    ctx += f"\nRISK: ${rb.get('account_size', 0)} acct | deployed ${rb.get('top5_risk', 0)} ({rb.get('pct_of_account', 0)}%) | {rb.get('verdict', '?')}"
    if warns:
        ctx += f"\nWARNINGS: {'; '.join(warns)}"
    ctx += f"\nSECTORS: {', '.join(f'{s}:{c}' for s, c in data.get('sector_exposure', {}).items()) or 'N/A'}\n"

    from datetime import datetime as _dt, date as _date
    now_dt = _dt.now()
    today = now_dt.strftime("%A, %B %d, %Y")

    # Market hours check — weekends + NYSE holidays
    NYSE_HOLIDAYS_2026 = {
        _date(2026, 1, 1), _date(2026, 1, 19), _date(2026, 2, 16),
        _date(2026, 4, 3),  # Good Friday
        _date(2026, 5, 25), _date(2026, 7, 3), _date(2026, 9, 7),
        _date(2026, 11, 26), _date(2026, 12, 25),
    }
    today_date = now_dt.date()
    if today_date.weekday() >= 5:
        market_status = "MARKETS CLOSED (weekend)"
    elif today_date in NYSE_HOLIDAYS_2026:
        market_status = "MARKETS CLOSED (holiday)"
    else:
        market_status = "Markets open"

    # Build the top trade context with more detail
    top_opp = opps[0] if opps else None
    top_trade_ctx = ""
    if top_opp:
        top_trade_ctx = f"""
TOP RANKED TRADE (score {top_opp.get('score', 0):.3f}):
  {top_opp.get('ticker','?')} {top_opp.get('label','?')} {top_opp.get('strikes','?')} {top_opp.get('dte','?')}d
  POP {top_opp.get('pop','?')}% | R:R {top_opp.get('rr_ratio','?')}x | IVR {top_opp.get('ivr','?')} ({top_opp.get('ivr_band','?')}) | Kelly {top_opp.get('kelly_adj',0):.1f}% | Liq {top_opp.get('liq_grade','?')}
  Premium ${top_opp.get('premium',0)} | Max Risk ${top_opp.get('max_risk',0)} | Max Profit ${top_opp.get('max_profit',0)}
  {'⚠ EARNINGS BEFORE EXPIRY' if top_opp.get('earnings_before') else ''}{'⚠ INSIDE EXPECTED MOVE' if top_opp.get('inside_exp_move') else ''}
"""
    ctx += top_trade_ctx

    # Live book from Robinhood
    book = req.book_summary
    if book:
        ctx += f"\nYOUR LIVE POSITIONS (from Robinhood):\n{book}\n"
        ctx += "IMPORTANT: If any position is breached or at max loss, address it FIRST in your note. Management of existing positions takes priority over new trades.\n"

    signals = req.signal_summary
    if signals:
        ctx += f"\nTECHNICAL SIGNAL CONSENSUS (24 strategies, family-weighted, fresh flips only):\n{signals}\n"
        ctx += "These are the top trade ideas from the strategy scanner. Each has 2+ independent strategy families confirming. Reference them in TOP TRADE if they align with the market thesis.\n"
        ctx += "IMPORTANT: Cross-check signals against news. If a signal says BUY but news is bearish, FLAG the contradiction. If signal + news agree, that's high conviction.\n"

    system = f"""You are a portfolio manager writing a private morning note to yourself before market open. Today is {today}. {market_status}.

You trade options spreads (credit and debit verticals, iron condors). You sell premium when VIX is 20-30 and IVR is elevated. You size positions using half-Kelly. You respect earnings risk and sector concentration.

Your training data is STALE. ONLY use the data below. Do not invent numbers, tickers, or events.

{"Markets are CLOSED today. State this in STANCE. Say 'No trades today' in TOP TRADE. Say 'No deployment' in SIZING. In OUTLOOK, frame as 'when markets reopen' — the forward view still matters." if "CLOSED" in market_status else ""}

OUTPUT EXACTLY 4 SECTIONS. Be direct. Take a stance. No hedging, no "on the other hand."

**STANCE**
One sentence. Are you bullish, bearish, or sitting out today? Why?
Synthesize: VIX regime + term structure + today's news + prediction market odds + YOUR LIVE BOOK status → net directional bias.
If you have positions that are breached or at max loss, your stance should reflect that reality.

**TOP TRADE**
If you have live positions that need management (breached strikes, at max loss, expiring soon), address those FIRST — "close X", "roll Y", "hold Z". Existing position management beats new trades.
Then, if appropriate, your #1 new pick from the scan:
- What it is (ticker, strategy, strikes, DTE)
- Why THIS one (IVR, POP, Kelly edge)
- How today's news supports or threatens it
- If it overlaps with an existing position, note it

**RISKS**
What could blow up — both your EXISTING positions and any new trades from the scan.
- Flag any live position where the short strike is within 3% of stock price
- Earnings exposure on any position or scan ticker
- Sector concentration across your ENTIRE book (live + new)
- Geopolitical: Iran/oil (cite Polymarket odds), tariffs

**SIZING**
One sentence. Account for what's ALREADY deployed in your live book.
Reference: account equity, existing exposure, remaining buying power.

RULES:
- Write like you're talking to yourself. No filler. No "it's worth noting." No disclaimers.
- Every claim must reference specific data from below — a number, a ticker, a Polymarket odd.
- If you can't support a statement with data provided, don't make it.
- Do NOT repeat the news feed. The news is already on screen. Your job is to interpret it for trading.
- Be wrong confidently rather than right vaguely. A trader needs a clear signal, not a balanced essay."""

    try:
        api_key = get_secret("GEMINI_API_KEY")
        if not api_key:
            return {"content": "Gemini API key not configured.", "success": False}

        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=f"{system}\n\n{ctx}",
            config=types.GenerateContentConfig(max_output_tokens=4000, temperature=0.15),
        )
        text = response.text or ""
        # Strip leaked thinking / self-correction artifacts
        import re as _re
        text = _re.sub(r"\*Self-[Cc]orrection[^*]*\*:?[^\n]*\n?", "", text)
        text = _re.sub(r"\*?(?:Let me|Wait,|Actually,|Hmm,|Note to self)[^\n]*\n?", "", text, flags=_re.IGNORECASE)
        text = _re.sub(r"^\s*(?:Okay|Alright|Sure|Here'?s)[^\n]*\n", "", text.strip())
        # Ensure it starts with the first section header
        for header in ["**STANCE**", "**STANCE", "**TOP TRADE**", "**TOP TRADE"]:
            idx = text.find(header)
            if idx > 0:
                text = text[idx:]
                break
        return {"content": text.strip(), "success": True}
    except Exception as e:
        return {"content": f"AI generation failed: {str(e)}", "success": False}


class StrategyScanRequest(BaseModel):
    tickers: list[str] = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "AMZN", "META", "GOOGL"]
    strategies: list[str] = ["sma_cross", "ema_cross", "macd", "rsi_ob_os", "mean_rev", "momentum", "donchian", "atr_trail", "trend_mr_composite", "trend_bb_composite", "adx_di", "stochastic", "parabolic_sar", "cci", "williams_r", "ichimoku", "tema_cross"]
    lookback_days: int = 1260
    timeframe: str = "daily"  # "daily", "60min", "15min", "5min"
    min_dsr: float = 0.0
    commission_bps: int = 5
    slippage_bps: int = 5


@router.post("/strategy-scan")
async def strategy_scan(req: StrategyScanRequest, user: str = Depends(get_current_user)):
    """Scan all ticker × strategy combinations. Rank by Deflated Sharpe.

    Uses yfinance adjusted OHLCV (handles splits + dividends).
    DSR corrected for n_tested = tickers × strategies.
    Walk-forward with rolling windows. Proper cost modeling.
    """
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from scipy import stats as sp_stats
    import yfinance as yf

    tickers = [t.strip().upper() for t in req.tickers if t.strip()]
    strategies = req.strategies
    n_tested = len(tickers) * len(strategies)
    cost_1x = (req.commission_bps + req.slippage_bps) / 10000
    is_intraday = req.timeframe != "daily"

    # Annualization factor: sqrt(bars_per_year)
    # Daily: 252 trading days. 60min: 252 × 6.5 = 1638. 15min: 252 × 26 = 6552. 5min: 252 × 78 = 19656.
    BARS_PER_YEAR = {"daily": 252, "60min": 1638, "15min": 6552, "5min": 19656}
    bars_yr = BARS_PER_YEAR.get(req.timeframe, 252)
    ann = np.sqrt(bars_yr)

    # ── TA-Lib indicators (C-compiled, matches Bloomberg/TradingView exactly) ──
    import talib

    def _sma(c, p): return talib.SMA(c, timeperiod=p)
    def _ema(c, p): return talib.EMA(c, timeperiod=p)
    def _rsi(c, p=14): return talib.RSI(c, timeperiod=p)

    def _generate_signals(closes, highs, lows, strategy, volumes=None, params_override=None):
        n = len(closes)
        signals = np.zeros(n)
        p = params_override or {}  # shorthand

        if strategy == "sma_cross":
            fast, slow = _sma(closes, p.get("fast", 50)), _sma(closes, p.get("slow", 200))
            for i in range(200, n):
                if not np.isnan(fast[i]) and not np.isnan(slow[i]):
                    signals[i] = 1 if fast[i] > slow[i] else -1

        elif strategy == "ema_cross":
            fast, slow = _ema(closes, p.get("fast", 12)), _ema(closes, p.get("slow", 26))
            warmup = p.get("slow", 26) + 1
            for i in range(warmup, n):
                signals[i] = 1 if fast[i] > slow[i] else -1

        elif strategy == "golden_cross":
            fast, slow = _sma(closes, 50), _sma(closes, 200)
            for i in range(200, n):
                if not np.isnan(fast[i]) and not np.isnan(slow[i]):
                    signals[i] = 1 if closes[i] > slow[i] and fast[i] > slow[i] else -1

        elif strategy == "macd":
            mf, ms, msig = p.get("fast", 12), p.get("slow", 26), p.get("signal", 9)
            e_f, e_s = _ema(closes, mf), _ema(closes, ms)
            macd_line = e_f - e_s
            sig_line = _ema(macd_line, msig)
            warmup = ms + msig + 1
            for i in range(warmup, n):
                if not np.isnan(macd_line[i]) and not np.isnan(sig_line[i]):
                    signals[i] = 1 if macd_line[i] > sig_line[i] else -1

        elif strategy == "rsi_ob_os":
            rsi_period = p.get("period", 14)
            r = talib.RSI(closes, timeperiod=rsi_period)
            ob, os_ = p.get("overbought", 70), p.get("oversold", 30)
            for i in range(rsi_period + 1, n):
                if np.isnan(r[i]): continue
                if r[i] < os_: signals[i] = 1
                elif r[i] > ob: signals[i] = -1
                else: signals[i] = signals[i - 1]

        elif strategy in ("mean_rev", "bb_breakout"):
            bb_upper, bb_mid, bb_lower = talib.BBANDS(closes, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
            for i in range(20, n):
                if np.isnan(bb_upper[i]) or np.isnan(bb_lower[i]): continue
                if strategy == "mean_rev":
                    if closes[i] < bb_lower[i]: signals[i] = 1
                    elif closes[i] > bb_upper[i]: signals[i] = -1
                    else: signals[i] = signals[i - 1]
                else:  # bb_breakout
                    if closes[i] > bb_upper[i]: signals[i] = 1
                    elif closes[i] < bb_lower[i]: signals[i] = -1
                    else: signals[i] = signals[i - 1]

        elif strategy == "donchian":
            for i in range(20, n):
                window = closes[i - 20:i + 1]  # FIXED: inclusive current bar (21 bars)
                hi, lo = float(np.max(window[:-1])), float(np.min(window[:-1]))  # breakout of prior 20
                if closes[i] > hi: signals[i] = 1
                elif closes[i] < lo: signals[i] = -1
                else: signals[i] = signals[i - 1]

        elif strategy == "momentum":
            # Z-score normalized momentum (FIXED: not raw)
            for i in range(252, n):
                mom12 = closes[i] / closes[i - 252] - 1
                mom1 = closes[i] / closes[i - 21] - 1
                net_mom = mom12 - mom1
                # Rolling z-score of net momentum over 63 days
                if i >= 252 + 63:
                    hist_mom = np.array([closes[j] / closes[j - 252] - 1 - (closes[j] / closes[j - 21] - 1) for j in range(i - 62, i + 1)])
                    z = (net_mom - np.mean(hist_mom)) / max(np.std(hist_mom, ddof=1), 1e-6)
                    signals[i] = 1 if z > 0.5 else (-1 if z < -0.5 else 0)
                else:
                    signals[i] = 1 if net_mom > 0 else -1

        elif strategy == "dual_mom":
            for i in range(252, n):
                abs_mom = closes[i] / closes[i - 252] - 1
                s200 = np.mean(closes[max(0, i - 199):i + 1])
                signals[i] = 1 if abs_mom > 0 and closes[i] > s200 else (-1 if abs_mom < -0.05 else 0)

        elif strategy == "zscore_mr":
            s50 = _sma(closes, 50)
            for i in range(50, n):
                if np.isnan(s50[i]): continue
                sl = closes[i - 49:i + 1]
                std = float(np.std(sl, ddof=1))  # FIXED: sample std
                z = (closes[i] - s50[i]) / std if std > 0 else 0
                if z < -2: signals[i] = 1
                elif z > 2: signals[i] = -1
                elif abs(z) < 0.5: signals[i] = 0
                else: signals[i] = signals[i - 1]

        elif strategy == "atr_trail":
            # Long-only ATR trailing stop (TA-Lib ATR)
            atr_arr = talib.ATR(highs, lows, closes, timeperiod=14)
            pos = 0
            stop = 0.0
            for i in range(15, n):
                atr = float(atr_arr[i]) if not np.isnan(atr_arr[i]) else 0
                if atr <= 0: continue
                if pos == 0 and i >= 50:
                    s50 = np.mean(closes[i - 49:i + 1])
                    if closes[i] > s50:
                        pos = 1; stop = closes[i] - 3 * atr
                elif pos == 1:
                    stop = max(stop, closes[i] - 3 * atr)
                    if closes[i] < stop: pos = 0
                signals[i] = pos

        elif strategy == "trend_mr_composite":
            # THE OPTIMAL ARCHITECTURE (per academic research):
            # Long-term trend filter (SMA 200) + short-term mean reversion entry (RSI < 30)
            # Only buy when macro trend is UP and short-term is oversold
            # Only short when macro trend is DOWN and short-term is overbought
            s200 = _sma(closes, 200)
            r = _rsi(closes, 14)
            for i in range(200, n):
                if np.isnan(s200[i]) or np.isnan(r[i]): continue
                if closes[i] > s200[i]:  # uptrend
                    if r[i] < 30: signals[i] = 1       # oversold in uptrend = BUY
                    elif r[i] > 70: signals[i] = 0     # overbought = take profit
                    else: signals[i] = signals[i - 1]
                else:  # downtrend
                    if r[i] > 70: signals[i] = -1      # overbought in downtrend = SHORT
                    elif r[i] < 30: signals[i] = 0     # oversold = cover
                    else: signals[i] = signals[i - 1]

        elif strategy == "trend_bb_composite":
            # Trend filter (SMA 200) + Bollinger Band entry (TA-Lib)
            s200 = _sma(closes, 200)
            bb_up, bb_mid, bb_lo = talib.BBANDS(closes, timeperiod=20, nbdevup=2, nbdevdn=2, matype=0)
            for i in range(200, n):
                if np.isnan(s200[i]) or np.isnan(bb_up[i]): continue
                upper, lower = bb_up[i], bb_lo[i]
                if closes[i] > s200[i]:  # uptrend
                    if closes[i] <= lower: signals[i] = 1
                    elif closes[i] >= upper: signals[i] = 0
                    else: signals[i] = signals[i - 1]
                else:  # downtrend
                    if closes[i] >= upper: signals[i] = -1
                    elif closes[i] <= lower: signals[i] = 0
                    else: signals[i] = signals[i - 1]

        elif strategy == "calendar_tom":
            # Turn-of-Month: long last 4 trading days + first 2 of next month
            # Academic: statistically significant since 1950
            import pandas as pd
            # Need dates — estimate from bar index (daily only)
            for i in range(1, n):
                # Approximate day-of-month from bar index
                # For daily data, bar i corresponds roughly to trading day
                # TOM = last 4 + first 2 = ~6 trading days around month end
                # Simple proxy: long when i % 21 >= 17 or i % 21 <= 1
                day_in_month = i % 21  # ~21 trading days per month
                if day_in_month >= 17 or day_in_month <= 1:
                    signals[i] = 1  # long during TOM window
                else:
                    signals[i] = 0  # flat outside

        elif strategy == "halloween":
            # Halloween Effect: long Nov-Apr, flat May-Oct
            # Academic: Nov-Apr vastly outperforms May-Oct since 1950
            # Proxy for daily data: long first ~126 days of year + last ~42 days
            for i in range(1, n):
                # Approximate month from bar position in year
                day_in_year = i % 252
                # Nov-Apr ≈ bars 0-84 (Jan-Apr) and 210-252 (Nov-Dec)
                if day_in_year <= 84 or day_in_year >= 210:
                    signals[i] = 1
                else:
                    signals[i] = 0

        elif strategy == "adx_di":
            # Wilder's ADX + Directional Movement System
            # Long when +DI > -DI and ADX > 25 (strong trend). Short when -DI > +DI and ADX > 25.
            # Flat when ADX < 20 (no trend — avoids chop)
            adx = talib.ADX(highs, lows, closes, timeperiod=14)
            plus_di = talib.PLUS_DI(highs, lows, closes, timeperiod=14)
            minus_di = talib.MINUS_DI(highs, lows, closes, timeperiod=14)
            for i in range(15, n):
                if np.isnan(adx[i]): continue
                if adx[i] >= 25:  # strong trend
                    signals[i] = 1 if plus_di[i] > minus_di[i] else -1
                elif adx[i] < 20:  # no trend — stay flat
                    signals[i] = 0
                else:
                    signals[i] = signals[i - 1]

        elif strategy == "stochastic":
            # Stochastic K/D crossover mean reversion
            # Buy when K crosses above D below 20 (oversold). Sell when K crosses below D above 80.
            slowk, slowd = talib.STOCH(highs, lows, closes, fastk_period=14, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
            for i in range(15, n):
                if np.isnan(slowk[i]) or np.isnan(slowd[i]): continue
                if slowk[i] < 20 and slowk[i] > slowd[i]: signals[i] = 1
                elif slowk[i] > 80 and slowk[i] < slowd[i]: signals[i] = -1
                else: signals[i] = signals[i - 1]

        elif strategy == "parabolic_sar":
            sar = talib.SAR(highs, lows, acceleration=p.get("accel", 0.02), maximum=p.get("max_accel", 0.2))
            for i in range(2, n):
                if np.isnan(sar[i]): continue
                signals[i] = 1 if closes[i] > sar[i] else -1

        elif strategy == "cci":
            cci_period = p.get("period", 20)
            cci_ob = p.get("overbought", 100)
            cci_os = p.get("oversold", -100)
            cci = talib.CCI(highs, lows, closes, timeperiod=cci_period)
            for i in range(cci_period + 1, n):
                if np.isnan(cci[i]): continue
                if cci[i] < cci_os: signals[i] = 1
                elif cci[i] > cci_ob: signals[i] = -1
                elif abs(cci[i]) < 50: signals[i] = 0
                else: signals[i] = signals[i - 1]

        elif strategy == "williams_r":
            wr_period = p.get("period", 14)
            wr_ob = p.get("overbought", -20)
            wr_os = p.get("oversold", -80)
            willr = talib.WILLR(highs, lows, closes, timeperiod=wr_period)
            for i in range(wr_period + 1, n):
                if np.isnan(willr[i]): continue
                if willr[i] < wr_os: signals[i] = 1
                elif willr[i] > wr_ob: signals[i] = -1
                else: signals[i] = signals[i - 1]

        elif strategy == "obv_divergence":
            # On Balance Volume divergence detection
            # Long when OBV trending up + price dipping (accumulation)
            # Short when OBV trending down + price rising (distribution)
            vol = volumes if volumes is not None else np.ones(n)
            obv = talib.OBV(closes, vol)
            obv_sma = talib.SMA(obv, timeperiod=20)
            price_sma = _sma(closes, 20)
            for i in range(50, n):
                if np.isnan(obv_sma[i]) or np.isnan(price_sma[i]): continue
                obv_trend = obv[i] > obv_sma[i]
                price_trend = closes[i] > price_sma[i]
                if obv_trend and not price_trend: signals[i] = 1    # accumulation
                elif not obv_trend and price_trend: signals[i] = -1  # distribution
                else: signals[i] = signals[i - 1]

        elif strategy == "ichimoku":
            # Ichimoku Cloud — price vs cloud for trend, Tenkan/Kijun cross for entry
            # Tenkan-sen (conversion) = (9-high + 9-low) / 2
            # Kijun-sen (base) = (26-high + 26-low) / 2
            # Senkou A (cloud top) = (Tenkan + Kijun) / 2 shifted 26 ahead
            # Senkou B (cloud bottom) = (52-high + 52-low) / 2 shifted 26 ahead
            tenkan = (talib.MAX(highs, 9) + talib.MIN(lows, 9)) / 2
            kijun = (talib.MAX(highs, 26) + talib.MIN(lows, 26)) / 2
            senkou_a = (tenkan + kijun) / 2  # normally shifted 26 but we use current for signal
            senkou_b = (talib.MAX(highs, 52) + talib.MIN(lows, 52)) / 2
            for i in range(52, n):
                if np.isnan(senkou_a[i]) or np.isnan(senkou_b[i]): continue
                cloud_top = max(senkou_a[i], senkou_b[i])
                cloud_bot = min(senkou_a[i], senkou_b[i])
                if closes[i] > cloud_top and tenkan[i] > kijun[i]:
                    signals[i] = 1   # above cloud + bullish cross
                elif closes[i] < cloud_bot and tenkan[i] < kijun[i]:
                    signals[i] = -1  # below cloud + bearish cross
                else:
                    signals[i] = signals[i - 1]

        elif strategy == "tema_cross":
            # Triple EMA crossover — less lag than standard EMA
            tema_fast = talib.TEMA(closes, timeperiod=12)
            tema_slow = talib.TEMA(closes, timeperiod=26)
            for i in range(26, n):
                if np.isnan(tema_fast[i]) or np.isnan(tema_slow[i]): continue
                signals[i] = 1 if tema_fast[i] > tema_slow[i] else -1

        return signals

    def _fetch_intraday_polygon(tk, timeframe, lookback_days):
        """Fetch intraday OHLCV from Polygon API."""
        import requests
        from datetime import datetime, timedelta
        from src.api_keys import get_secret
        api_key = get_secret("MASSIVE_API_KEY")
        if not api_key:
            return None

        multiplier_map = {"60min": (1, "hour"), "15min": (15, "minute"), "5min": (5, "minute")}
        mult, span = multiplier_map.get(timeframe, (1, "hour"))

        # Polygon allows max ~2 years of intraday. Limit lookback for rate limits.
        max_days = min(lookback_days, 60 if span == "minute" and mult <= 5 else 180)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")

        url = f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/{mult}/{span}/{start}/{end}"
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code != 200:
                return None
            data = r.json()
            results = data.get("results", [])
            if not results or len(results) < 50:
                return None
            import pandas as pd
            df = pd.DataFrame(results)
            df = df.rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
            return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            return None

    def _backtest_combo(tk, strategy):
        """Run full backtest using pre-fetched OHLCV data. No yfinance calls inside threads."""
        try:
            import pandas as pd

            df = _ohlcv_cache.get(tk)
            if df is None or len(df) < 100:
                return None

            # OHLCV validation
            closes = df["Close"].values.astype(float).ravel()
            highs = df["High"].values.astype(float).ravel()
            lows = df["Low"].values.astype(float).ravel()
            n = len(closes)

            # Data quality checks
            # Flag days where High < Low (bad data)
            bad_bars = np.sum(highs < lows)
            if bad_bars > n * 0.01:  # >1% bad bars = bad data
                return None
            # Fix minor OHLC inconsistencies
            highs = np.maximum(highs, closes)
            lows = np.minimum(lows, closes)
            # Check for extreme moves (>30% daily = likely split artifact even with adjusted data)
            daily_moves = np.abs(np.diff(closes) / closes[:-1])
            if np.sum(daily_moves > 0.30) > 3:
                return None  # too many extreme moves, data quality suspect

            volumes = df["Volume"].values.astype(float).ravel() if "Volume" in df.columns else None
            # Load optimized params from Supabase cache (if available)
            from src.strategy_optimizer import get_cached_params
            opt_params = get_cached_params(tk, strategy)
            signals = _generate_signals(closes, highs, lows, strategy, volumes, params_override=opt_params)

            # Daily returns
            daily_rets = np.zeros(n)
            for i in range(1, n):
                if signals[i] != 0:
                    daily_rets[i] = signals[i] * (closes[i] / closes[i - 1] - 1)

            # FIXED: Proper cost modeling — flip costs 2x, entry/exit costs 1x
            for i in range(1, n):
                if signals[i] != signals[i - 1]:
                    if signals[i] != 0 and signals[i - 1] != 0:
                        daily_rets[i] -= 2 * cost_1x  # flip: close + reopen
                    elif signals[i] != 0 or signals[i - 1] != 0:
                        daily_rets[i] -= cost_1x  # entry or exit

            # ── Strategy metrics (on ACTIVE days only — avoids sparse signal inflation) ──
            warmup = max(200, int(n * 0.1))
            all_rets = daily_rets[warmup:]
            active_mask = signals[warmup:] != 0
            active_rets = all_rets[active_mask]
            if len(active_rets) < 20:
                return None

            # Strategy Sharpe on ACTIVE days only (when actually in a position)
            mean_r = float(np.mean(active_rets))
            std_r = float(np.std(active_rets, ddof=1))
            sharpe = mean_r / std_r * ann if std_r > 0 else 0
            skew = float(sp_stats.skew(active_rets)) if len(active_rets) > 30 else 0
            kurt = float(sp_stats.kurtosis(active_rets)) if len(active_rets) > 30 else 0

            # ── Buy-and-hold benchmark (same period) ──
            bh_rets = np.zeros(n)
            for i in range(1, n):
                bh_rets[i] = closes[i] / closes[i - 1] - 1
            bh_rets_period = bh_rets[warmup:]
            bh_mean = float(np.mean(bh_rets_period))
            bh_std = float(np.std(bh_rets_period, ddof=1))
            bh_sharpe = bh_mean / bh_std * ann if bh_std > 0 else 0
            bh_eq = np.cumprod(1 + bh_rets)
            bh_total_ret = float(bh_eq[-1] - 1) * 100
            bh_cagr = (float(bh_eq[-1]) ** (1 / max(n / bars_yr, 0.01)) - 1) * 100

            # ── Strategy equity curve ──
            eq = np.cumprod(1 + daily_rets)
            total_ret = float(eq[-1] - 1) * 100
            years = n / bars_yr
            cagr = (float(eq[-1]) ** (1 / max(years, 0.01)) - 1) * 100

            # Excess metrics (strategy - buy-and-hold)
            excess_sharpe = round(sharpe - bh_sharpe, 3)
            excess_cagr = round(cagr - bh_cagr, 1)
            excess_ret = round(total_ret - bh_total_ret, 1)

            # Drawdown
            peak = np.maximum.accumulate(eq)
            max_dd = float(np.min((eq / peak - 1) * 100))

            # Time in market
            pct_active = round(float(np.sum(active_mask)) / max(len(active_mask), 1) * 100, 0)

            # Win rate + trade-level ATR stop analysis
            atr_arr = talib.ATR(highs, lows, closes, timeperiod=14)
            trades, wins, pos, entry_px = 0, 0, 0, 0.0
            # ATR stop simulation: track trades with 1.5x, 2x, 2.5x ATR stops
            stop_results = {1.5: {"stopped": 0, "survived": 0, "win_survived": 0},
                           2.0: {"stopped": 0, "survived": 0, "win_survived": 0},
                           2.5: {"stopped": 0, "survived": 0, "win_survived": 0}}
            mfe_list = []  # max favorable excursion per trade (in ATR multiples)
            mae_list = []  # max adverse excursion per trade (in ATR multiples)
            hold_days = []  # holding period per trade in bars

            for i in range(1, n):
                if signals[i] != 0 and pos == 0:
                    pos = int(signals[i]); entry_px = closes[i]
                elif pos != 0 and signals[i] != pos:
                    pnl = (closes[i] / entry_px - 1) * pos
                    trades += 1
                    if pnl > 2 * cost_1x: wins += 1

                    # Compute MAE/MFE for this trade (find entry index)
                    entry_idx = i - 1
                    while entry_idx > 0 and signals[entry_idx] == pos:
                        entry_idx -= 1
                    entry_idx += 1
                    hold_days.append(i - entry_idx)
                    entry_atr = float(atr_arr[entry_idx]) if not np.isnan(atr_arr[entry_idx]) else 0
                    if entry_atr > 0:
                        trade_highs = highs[entry_idx:i+1]
                        trade_lows = lows[entry_idx:i+1]
                        if pos == 1:  # long
                            mfe = float(np.max(trade_highs) - entry_px) / entry_atr
                            mae = float(entry_px - np.min(trade_lows)) / entry_atr
                        else:  # short
                            mfe = float(entry_px - np.min(trade_lows)) / entry_atr
                            mae = float(np.max(trade_highs) - entry_px) / entry_atr
                        mfe_list.append(round(mfe, 2))
                        mae_list.append(round(mae, 2))

                        # Test each stop level
                        for mult, sr in stop_results.items():
                            if mae >= mult:
                                sr["stopped"] += 1
                            else:
                                sr["survived"] += 1
                                if pnl > 2 * cost_1x:
                                    sr["win_survived"] += 1

                    if signals[i] != 0:
                        pos = int(signals[i]); entry_px = closes[i]
                    else:
                        pos = 0
            win_rate = round(wins / max(trades, 1) * 100, 1)

            # Compute optimal stop from data
            best_stop_mult = 2.0
            best_stop_ev = -999
            for mult, sr in stop_results.items():
                total = sr["stopped"] + sr["survived"]
                if total < 5:
                    continue
                survival_rate = sr["survived"] / total
                win_rate_stopped = sr["win_survived"] / max(sr["survived"], 1)
                # EV with this stop: wins × avg_target - losses × stop_level
                # Use 1.5× stop as target proxy (gives 1.5:1 R:R at mult stop)
                ev = win_rate_stopped * 1.5 * mult - (1 - win_rate_stopped) * mult
                if ev > best_stop_ev:
                    best_stop_ev = ev
                    best_stop_mult = mult

            avg_mae = round(float(np.mean(mae_list)), 2) if mae_list else 0
            avg_mfe = round(float(np.mean(mfe_list)), 2) if mfe_list else 0
            avg_hold = round(float(np.mean(hold_days))) if hold_days else 0
            median_hold = round(float(np.median(hold_days))) if hold_days else 0
            stop_2x_survival = round(stop_results[2.0]["survived"] / max(stop_results[2.0]["survived"] + stop_results[2.0]["stopped"], 1) * 100, 0)

            # ── DSR (on active returns — no sparse inflation) ──
            n_obs = len(active_rets)
            var_sr = (1 + 0.5 * sharpe**2 - skew * sharpe + (kurt / 4) * sharpe**2) / max(n_obs - 1, 1)
            se_sr = np.sqrt(max(var_sr, 1e-10))
            if n_tested > 1:
                e_max_sr = float(sp_stats.norm.ppf(1 - 1 / n_tested)) * (1 - 0.5772 / max(np.log(n_tested), 1))
            else:
                e_max_sr = 0
            dsr = float(sp_stats.norm.cdf((sharpe - e_max_sr) / se_sr)) if se_sr > 0 else 0.5

            # ── Walk-forward (rolling windows, no data leakage) ──
            wf_sharpes = []
            min_bars_for_wf = 504 if not is_intraday else 500  # ~2yr daily or ~500 bars intraday
            test_size = max(n // 5, 63 if not is_intraday else 100)
            train_size = max(int(n * 0.6), 252 if not is_intraday else 300)
            if n >= min_bars_for_wf:
                start = 0
                while start + train_size + test_size <= n:
                    test_start = start + train_size
                    test_end = test_start + test_size
                    test_rets = daily_rets[test_start:test_end]
                    test_signals = signals[test_start:test_end]
                    test_active = test_rets[test_signals != 0]
                    if len(test_active) >= 10:
                        tm = float(np.mean(test_active))
                        ts = float(np.std(test_active, ddof=1))
                        wf_sharpes.append(tm / ts * ann if ts > 0 else 0)
                    start += test_size

            avg_wf = round(float(np.mean(wf_sharpes)), 3) if wf_sharpes else None
            pct_wf_pos = round(sum(1 for s in wf_sharpes if s > 0) / max(len(wf_sharpes), 1) * 100, 0) if wf_sharpes else None

            # ── Recent performance (last 252 days) — detect degradation ──
            recent_sharpe = None
            if n > 504:  # need at least 2yr to have a "recent" vs "historical" split
                recent_rets = daily_rets[-252:]
                recent_sigs = signals[-252:]
                recent_active = recent_rets[recent_sigs != 0]
                if len(recent_active) >= 10:
                    rm = float(np.mean(recent_active))
                    rs = float(np.std(recent_active, ddof=1))
                    recent_sharpe = round(rm / rs * ann if rs > 0 else 0, 3)

            # ── Current signal + duration ──
            current_signal = "Long" if signals[-1] == 1 else "Short" if signals[-1] == -1 else "Flat"
            signal_days = 0
            for i in range(n - 1, -1, -1):
                if signals[i] == signals[-1]: signal_days += 1
                else: break

            # Long-only flag
            is_long_only = strategy == "atr_trail"

            # Price levels for trade ideas
            atr_arr = talib.ATR(highs, lows, closes, timeperiod=14)
            atr_14 = round(float(atr_arr[-1]), 2) if not np.isnan(atr_arr[-1]) else 0
            current_price = round(float(closes[-1]), 2)
            high_20d = round(float(np.max(highs[-20:])), 2) if n >= 20 else current_price
            low_20d = round(float(np.min(lows[-20:])), 2) if n >= 20 else current_price
            # RSI for context
            rsi_arr = talib.RSI(closes, timeperiod=14)
            current_rsi = round(float(rsi_arr[-1]), 1) if not np.isnan(rsi_arr[-1]) else 50

            return {
                "ticker": tk, "strategy": strategy,
                "sharpe": round(sharpe, 3), "dsr": round(dsr, 4), "dsr_pct": round(dsr * 100, 1),
                "cagr": round(cagr, 1), "max_dd": round(max_dd, 1), "total_ret": round(total_ret, 1),
                "win_rate": win_rate, "trades": trades,
                "bh_sharpe": round(bh_sharpe, 3), "bh_cagr": round(bh_cagr, 1), "bh_total_ret": round(bh_total_ret, 1),
                "excess_sharpe": excess_sharpe, "excess_cagr": excess_cagr, "excess_ret": excess_ret,
                "pct_active": pct_active,
                "avg_wf_sharpe": avg_wf, "pct_wf_positive": pct_wf_pos,
                "n_wf_folds": len(wf_sharpes),
                "current_signal": current_signal, "signal_days": signal_days,
                "long_only": is_long_only, "n_days": n,
                "skew": round(skew, 2), "kurtosis": round(kurt, 2),
                # Recent performance
                "recent_sharpe": recent_sharpe,
                # Price levels
                "current_price": current_price, "atr_14": atr_14,
                "high_20d": high_20d, "low_20d": low_20d,
                "rsi": current_rsi,
                # Validated stop analysis
                "best_stop_atr": best_stop_mult,
                "avg_mae_atr": avg_mae,
                "avg_mfe_atr": avg_mfe,
                "stop_2x_survival": stop_2x_survival,
                "avg_hold_days": avg_hold,
                "median_hold_days": median_hold,
            }
        except Exception:
            return None

    # ── Pre-fetch ticker data (Supabase cache + yfinance/Polygon) ──
    import logging as _scan_log
    _scan_log.getLogger(__name__).info(f"Pre-fetching OHLCV for {len(tickers)} tickers...")
    _ohlcv_cache: dict = {}
    if is_intraday:
        for tk in tickers:
            _ohlcv_cache[tk] = _fetch_intraday_polygon(tk, req.timeframe, req.lookback_days)
    else:
        from src.ohlcv_cache import fetch_ohlcv
        import time as _pftime
        def _fetch_cached(tk):
            try:
                return tk, fetch_ohlcv(tk, req.lookback_days)
            except Exception:
                return tk, None
        # Pass 1: fetch with 3 workers
        with ThreadPoolExecutor(max_workers=3) as prefetch_pool:
            for tk, df in prefetch_pool.map(_fetch_cached, tickers):
                _ohlcv_cache[tk] = df
        # Pass 2: retry any failures sequentially (rate limit recovery)
        failed = [tk for tk, df in _ohlcv_cache.items() if df is None or len(df) < 50]
        if failed:
            _scan_log.getLogger(__name__).info(f"Retrying {len(failed)} failed tickers...")
            _pftime.sleep(2)
            for tk in failed:
                try:
                    _ohlcv_cache[tk] = fetch_ohlcv(tk, req.lookback_days)
                except Exception:
                    pass
    loaded = sum(1 for v in _ohlcv_cache.values() if v is not None and len(v) > 0)
    _scan_log.getLogger(__name__).info(f"Pre-fetch complete. {loaded}/{len(tickers)} tickers loaded.")

    # ── Run all combinations in parallel (no yfinance calls inside threads) ──
    results = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = {pool.submit(_backtest_combo, tk, strat): (tk, strat) for tk in tickers for strat in strategies}
        for fut in as_completed(futs):
            try:
                result = fut.result()
                if result and (req.min_dsr <= 0 or result["dsr"] >= req.min_dsr):
                    results.append(result)
            except Exception:
                pass

    results.sort(key=lambda r: r["dsr"], reverse=True)

    active_signals = [r for r in results if r["current_signal"] != "Flat"]
    significant = [r for r in results if r["dsr"] >= 0.95]

    return {
        "results": results,
        "n_tested": n_tested,
        "n_significant": len(significant),
        "n_active_signals": len(active_signals),
        "active_signals": [r for r in results[:50] if r["current_signal"] != "Flat"],
    }


class BatchOptimizeRequest(BaseModel):
    tickers: list[str] = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]
    strategies: list[str] = []  # empty = all optimizable
    n_trials: int = 50
    lookback_days: int = 2520


@router.post("/batch-optimize")
async def batch_optimize(req: BatchOptimizeRequest, user: str = Depends(get_current_user)):
    """Run Optuna optimization for multiple ticker × strategy combos. Stores results in Supabase."""
    import logging
    _log = logging.getLogger(__name__)

    from src.strategy_optimizer import PARAM_SPACES, optimize_strategy, save_optimized_params, get_cached_params
    from src.ohlcv_cache import fetch_ohlcv

    tickers = [t.strip().upper() for t in req.tickers[:30] if t.strip()]
    strategies = req.strategies or list(PARAM_SPACES.keys())
    strategies = [s for s in strategies if s in PARAM_SPACES]

    _log.info(f"Batch optimize: {len(tickers)} tickers × {len(strategies)} strategies = {len(tickers) * len(strategies)} combos")

    results = []
    skipped = 0
    failed = 0

    for tk in tickers:
        df = fetch_ohlcv(tk, req.lookback_days)
        if df is None or len(df) < 252:
            failed += 1
            continue

        closes = df["Close"].values.astype(float).ravel()
        highs = df["High"].values.astype(float).ravel()
        lows = df["Low"].values.astype(float).ravel()
        volumes = df["Volume"].values.astype(float).ravel() if "Volume" in df.columns else None
        import numpy as np
        highs = np.maximum(highs, closes)
        lows = np.minimum(lows, closes)

        for strat in strategies:
            # Skip if already optimized recently
            cached = get_cached_params(tk, strat)
            if cached is not None:
                skipped += 1
                continue

            try:
                result = optimize_strategy(tk, strat, closes, highs, lows, volumes, n_trials=req.n_trials)
                if result:
                    save_optimized_params(tk, strat, result["params"], result)
                    results.append({
                        "ticker": tk, "strategy": strat,
                        "params": result["params"],
                        "wf_sharpe": result["wf_sharpe"],
                        "sharpe": result["sharpe"],
                        "trades": result["trades"],
                        "win_rate": result["win_rate"],
                    })
                    _log.info(f"  {tk} {strat}: WF Sharpe {result['wf_sharpe']:.3f}, {result['trades']} trades")
                else:
                    failed += 1
            except Exception as e:
                _log.warning(f"  {tk} {strat} failed: {e}")
                failed += 1

    return {
        "success": True,
        "optimized": len(results),
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


class ConfluenceValidationRequest(BaseModel):
    tickers: list[str] = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA", "GLD"]
    lookback_days: int = 1260


@router.post("/confluence-validation")
async def confluence_validation(req: ConfluenceValidationRequest, user: str = Depends(get_current_user)):
    """Backtest the multi-family confluence signal to validate it improves over individual strategies."""
    import numpy as np, yfinance as yf, talib, logging
    from scipy import stats as sp_stats

    _log = logging.getLogger(__name__)
    tickers = [t.strip().upper() for t in req.tickers[:10] if t.strip()]

    FAMILIES = {
        "trend": ["sma_cross", "ema_cross", "golden_cross", "macd", "donchian", "atr_trail", "momentum", "adx_di", "parabolic_sar", "ichimoku", "tema_cross"],
        "mean_rev": ["rsi_ob_os", "mean_rev", "bb_breakout", "zscore_mr", "stochastic", "cci", "williams_r"],
        "volume": ["obv_divergence"],
        "composite": ["trend_mr_composite", "trend_bb_composite"],
    }
    ALL_STRATS = [s for fam in FAMILIES.values() for s in fam]

    def _sma(c, p): return talib.SMA(c, timeperiod=p)
    def _ema(c, p): return talib.EMA(c, timeperiod=p)
    def _rsi(c, p=14): return talib.RSI(c, timeperiod=p)

    # Import signal generator from scan (reuse existing code)
    # We need access to _generate_signals — it's defined inside strategy_scan
    # Simplest: inline a lightweight version for the strategies we need
    from api.routes.market import router  # self-reference won't work, inline it

    period_map = {252: "1y", 504: "2y", 756: "3y", 1260: "5y", 2520: "10y"}
    period = period_map.get(req.lookback_days, "5y")
    ann = np.sqrt(252)

    results_per_ticker = []

    for tk in tickers:
        try:
            df = yf.Ticker(tk).history(period=period, auto_adjust=True)
            if df is None or len(df) < 252:
                continue

            closes = df["Close"].values.astype(float).ravel()
            highs = df["High"].values.astype(float).ravel()
            lows = df["Low"].values.astype(float).ravel()
            volumes = df["Volume"].values.astype(float).ravel() if "Volume" in df.columns else None
            n = len(closes)
            highs = np.maximum(highs, closes)
            lows = np.minimum(lows, closes)

            # Generate signals for ALL strategies
            strategy_signals = {}
            for strat in ALL_STRATS:
                try:
                    sig = np.zeros(n)
                    if strat == "sma_cross":
                        f, s = _sma(closes, 50), _sma(closes, 200)
                        for i in range(200, n):
                            if not np.isnan(f[i]) and not np.isnan(s[i]):
                                sig[i] = 1 if f[i] > s[i] else -1
                    elif strat == "ema_cross":
                        f, s = _ema(closes, 12), _ema(closes, 26)
                        for i in range(26, n):
                            if not np.isnan(f[i]) and not np.isnan(s[i]):
                                sig[i] = 1 if f[i] > s[i] else -1
                    elif strat == "macd":
                        m, ms, _ = talib.MACD(closes)
                        for i in range(35, n):
                            if not np.isnan(m[i]) and not np.isnan(ms[i]):
                                sig[i] = 1 if m[i] > ms[i] else -1
                    elif strat == "rsi_ob_os":
                        r = _rsi(closes)
                        pos = 0
                        for i in range(15, n):
                            if not np.isnan(r[i]):
                                if r[i] < 30: pos = 1
                                elif r[i] > 70: pos = -1
                                sig[i] = pos
                    elif strat == "momentum":
                        for i in range(126, n):
                            sig[i] = 1 if closes[i] > closes[i-126] else -1
                    elif strat == "adx_di":
                        adx = talib.ADX(highs, lows, closes)
                        pdi = talib.PLUS_DI(highs, lows, closes)
                        mdi = talib.MINUS_DI(highs, lows, closes)
                        for i in range(30, n):
                            if not np.isnan(adx[i]) and adx[i] > 20:
                                sig[i] = 1 if pdi[i] > mdi[i] else -1
                    elif strat == "stochastic":
                        k, d = talib.STOCH(highs, lows, closes)
                        pos = 0
                        for i in range(20, n):
                            if not np.isnan(k[i]):
                                if k[i] < 20: pos = 1
                                elif k[i] > 80: pos = -1
                                sig[i] = pos
                    elif strat == "parabolic_sar":
                        sar = talib.SAR(highs, lows)
                        for i in range(5, n):
                            if not np.isnan(sar[i]):
                                sig[i] = 1 if closes[i] > sar[i] else -1
                    elif strat == "ichimoku":
                        th = talib.MAX(highs, 9)
                        tl = talib.MIN(lows, 9)
                        kh = talib.MAX(highs, 26)
                        kl = talib.MIN(lows, 26)
                        for i in range(52, n):
                            tenkan = (th[i] + tl[i]) / 2
                            kijun = (kh[i] + kl[i]) / 2
                            sig[i] = 1 if closes[i] > kijun and tenkan > kijun else (-1 if closes[i] < kijun and tenkan < kijun else 0)
                    elif strat == "obv_divergence":
                        if volumes is not None:
                            obv = np.zeros(n)
                            for i in range(1, n):
                                obv[i] = obv[i-1] + (volumes[i] if closes[i] > closes[i-1] else (-volumes[i] if closes[i] < closes[i-1] else 0))
                            obv_sma = _sma(obv, 20)
                            for i in range(50, n):
                                if not np.isnan(obv_sma[i]):
                                    sig[i] = 1 if obv[i] > obv_sma[i] else -1
                    elif strat == "trend_mr_composite":
                        sma200 = _sma(closes, 200)
                        r = _rsi(closes)
                        for i in range(200, n):
                            if not np.isnan(sma200[i]) and not np.isnan(r[i]):
                                if closes[i] > sma200[i] and r[i] < 40: sig[i] = 1
                                elif closes[i] < sma200[i] and r[i] > 60: sig[i] = -1
                    elif strat == "trend_bb_composite":
                        sma200 = _sma(closes, 200)
                        upper, _, lower = talib.BBANDS(closes, timeperiod=20)
                        for i in range(200, n):
                            if not np.isnan(sma200[i]) and not np.isnan(lower[i]):
                                if closes[i] > sma200[i] and closes[i] < lower[i]: sig[i] = 1
                                elif closes[i] < sma200[i] and closes[i] > upper[i]: sig[i] = -1
                    else:
                        continue  # skip strategies not implemented here
                    strategy_signals[strat] = sig
                except Exception:
                    continue

            if len(strategy_signals) < 5:
                continue

            # Compute family-level daily signals
            warmup = 252
            daily_rets = np.diff(closes) / closes[:-1]
            daily_rets = np.insert(daily_rets, 0, 0)

            # For each day, determine family direction
            family_bull = np.zeros((n, len(FAMILIES)))
            family_bear = np.zeros((n, len(FAMILIES)))
            for fi, (fam_name, strats) in enumerate(FAMILIES.items()):
                for strat in strats:
                    if strat not in strategy_signals:
                        continue
                    sig = strategy_signals[strat]
                    family_bull[:, fi] += (sig == 1).astype(float)
                    family_bear[:, fi] += (sig == -1).astype(float)

            # Family direction: bullish if more bull than bear
            fam_dir = np.zeros((n, len(FAMILIES)))
            for fi in range(len(FAMILIES)):
                fam_dir[:, fi] = np.where(family_bull[:, fi] > family_bear[:, fi], 1,
                                 np.where(family_bear[:, fi] > family_bull[:, fi], -1, 0))

            # Confluence: count how many families agree
            bull_fams = (fam_dir == 1).sum(axis=1)
            bear_fams = (fam_dir == -1).sum(axis=1)

            # Test different confluence levels
            confluence_results = {}
            for min_fam in [1, 2, 3, 4]:
                # Signal: long when bull_fams >= min_fam, short when bear_fams >= min_fam
                conf_sig = np.zeros(n)
                for i in range(warmup, n):
                    if bull_fams[i] >= min_fam:
                        conf_sig[i] = 1
                    elif bear_fams[i] >= min_fam:
                        conf_sig[i] = -1

                # Returns when signal is active
                conf_rets = conf_sig[warmup:] * daily_rets[warmup:]
                active = conf_sig[warmup:] != 0

                if np.sum(active) < 20:
                    confluence_results[min_fam] = None
                    continue

                active_rets = conf_rets[active]
                mean_r = float(np.mean(active_rets))
                std_r = float(np.std(active_rets, ddof=1))
                sharpe = mean_r / std_r * ann if std_r > 0 else 0

                # Win rate (daily)
                daily_wins = np.sum(active_rets > 0)
                daily_total = len(active_rets)
                daily_wr = daily_wins / daily_total * 100

                # Equity curve for drawdown
                eq = np.cumprod(1 + conf_rets)
                peak = np.maximum.accumulate(eq)
                max_dd = float(np.min((eq / peak - 1) * 100))

                # CAGR
                years = len(conf_rets) / 252
                cagr = (float(eq[-1]) ** (1 / max(years, 0.01)) - 1) * 100

                # Time in market
                pct_active = float(np.sum(active)) / len(active) * 100

                confluence_results[min_fam] = {
                    "sharpe": round(sharpe, 3),
                    "cagr": round(cagr, 1),
                    "max_dd": round(max_dd, 1),
                    "win_rate_daily": round(daily_wr, 1),
                    "pct_active": round(pct_active, 0),
                    "n_days_active": int(np.sum(active)),
                }

            # Buy and hold benchmark
            bh_rets = daily_rets[warmup:]
            bh_mean = float(np.mean(bh_rets))
            bh_std = float(np.std(bh_rets, ddof=1))
            bh_sharpe = bh_mean / bh_std * ann if bh_std > 0 else 0
            bh_eq = np.cumprod(1 + bh_rets)
            bh_cagr = (float(bh_eq[-1]) ** (1 / max(len(bh_rets) / 252, 0.01)) - 1) * 100

            results_per_ticker.append({
                "ticker": tk,
                "n_strategies": len(strategy_signals),
                "n_days": n,
                "buy_hold": {"sharpe": round(bh_sharpe, 3), "cagr": round(bh_cagr, 1)},
                "confluence": {str(k): v for k, v in confluence_results.items()},
            })

        except Exception as e:
            _log.warning(f"Confluence validation failed for {tk}: {e}")
            continue

    # Aggregate across all tickers
    agg = {}
    for min_fam in [1, 2, 3, 4]:
        sharpes = [r["confluence"][str(min_fam)]["sharpe"] for r in results_per_ticker
                   if r["confluence"].get(str(min_fam))]
        cagrs = [r["confluence"][str(min_fam)]["cagr"] for r in results_per_ticker
                 if r["confluence"].get(str(min_fam))]
        wrs = [r["confluence"][str(min_fam)]["win_rate_daily"] for r in results_per_ticker
               if r["confluence"].get(str(min_fam))]
        if sharpes:
            agg[str(min_fam)] = {
                "avg_sharpe": round(float(np.mean(sharpes)), 3),
                "avg_cagr": round(float(np.mean(cagrs)), 1),
                "avg_win_rate": round(float(np.mean(wrs)), 1),
                "n_tickers": len(sharpes),
            }

    bh_sharpes = [r["buy_hold"]["sharpe"] for r in results_per_ticker]
    bh_avg = round(float(np.mean(bh_sharpes)), 3) if bh_sharpes else 0

    return {
        "success": True,
        "tickers": results_per_ticker,
        "aggregate": agg,
        "buy_hold_avg_sharpe": bh_avg,
        "n_tickers": len(results_per_ticker),
    }


class OptimizeRequest(BaseModel):
    ticker: str = "SPY"
    strategies: list[str] = ["sma_cross", "ema_cross", "macd", "rsi_ob_os", "mean_rev", "donchian", "bb_breakout", "momentum"]
    lookback_days: int = 1260
    timeframe: str = "daily"
    n_trials: int = 100
    commission_bps: int = 5
    slippage_bps: int = 5


@router.post("/optimize-strategy")
async def optimize_strategy(req: OptimizeRequest, user: str = Depends(get_current_user)):
    """Bayesian hyperparameter optimization using Optuna.

    Optimizes strategy parameters for a single ticker. Objective = walk-forward
    OOS Sharpe (not in-sample). DSR applied to final result with n_tested = n_trials.
    Returns best parameters, performance, parameter importance.
    """
    import numpy as np
    import optuna
    from scipy import stats as sp_stats
    import yfinance as yf
    import logging

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    tk = req.ticker.upper()
    cost_1x = (req.commission_bps + req.slippage_bps) / 10000
    is_intraday = req.timeframe != "daily"
    BARS_PER_YEAR = {"daily": 252, "60min": 1638, "15min": 6552, "5min": 19656}
    bars_yr = BARS_PER_YEAR.get(req.timeframe, 252)
    ann = np.sqrt(bars_yr)

    # ── Fetch data once ──
    if is_intraday:
        from datetime import datetime, timedelta
        import requests as req_http
        from src.api_keys import get_secret
        api_key = get_secret("MASSIVE_API_KEY")
        mult_map = {"60min": (1, "hour"), "15min": (15, "minute"), "5min": (5, "minute")}
        mult, span = mult_map.get(req.timeframe, (1, "hour"))
        max_days = min(req.lookback_days, 60 if span == "minute" and mult <= 5 else 180)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
        url = f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/{mult}/{span}/{start}/{end}"
        r = req_http.get(url, params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}, timeout=30)
        import pandas as pd
        results_raw = r.json().get("results", [])
        if len(results_raw) < 100:
            return {"error": f"Not enough intraday data for {tk}", "success": False}
        df = pd.DataFrame(results_raw).rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    else:
        period_map = {252: "1y", 504: "2y", 756: "3y", 1260: "5y", 2520: "10y"}
        period = period_map.get(req.lookback_days, "5y")
        df = yf.Ticker(tk).history(period=period, auto_adjust=True)
        if df is None or len(df) < 200:
            return {"error": f"Not enough data for {tk}", "success": False}

    closes = df["Close"].values.astype(float).ravel()
    highs = df["High"].values.astype(float).ravel()
    lows = df["Low"].values.astype(float).ravel()
    n = len(closes)

    # Data validation
    highs = np.maximum(highs, closes)
    lows = np.minimum(lows, closes)

    # ── TA-Lib indicators (same as strategy scanner) ──
    import talib

    def _sma(c, p): return talib.SMA(c, timeperiod=p)
    def _ema(c, p): return talib.EMA(c, timeperiod=p)
    def _rsi(c, p): return talib.RSI(c, timeperiod=p)

    def _generate_parameterized_signals(strategy, params):
        """Generate signals with variable parameters from Optuna."""
        signals = np.zeros(n)

        if strategy == "sma_cross":
            fast_p, slow_p = params["fast"], params["slow"]
            if fast_p >= slow_p: return signals
            fast, slow = _sma(closes, fast_p), _sma(closes, slow_p)
            for i in range(slow_p, n):
                if not np.isnan(fast[i]) and not np.isnan(slow[i]):
                    signals[i] = 1 if fast[i] > slow[i] else -1

        elif strategy == "ema_cross":
            fast_p, slow_p = params["fast"], params["slow"]
            if fast_p >= slow_p: return signals
            fast, slow = _ema(closes, fast_p), _ema(closes, slow_p)
            for i in range(slow_p, n):
                signals[i] = 1 if fast[i] > slow[i] else -1

        elif strategy == "macd":
            fast_p, slow_p, sig_p = params["fast"], params["slow"], params["signal"]
            if fast_p >= slow_p: return signals
            e_fast, e_slow = _ema(closes, fast_p), _ema(closes, slow_p)
            macd_line = e_fast - e_slow
            sig_line = _ema(macd_line, sig_p)
            warmup = slow_p + sig_p
            for i in range(warmup, n):
                signals[i] = 1 if macd_line[i] > sig_line[i] else -1

        elif strategy == "rsi_ob_os":
            period = params["period"]
            ob, os_level = params["overbought"], params["oversold"]
            r = _rsi(closes, period)
            for i in range(period + 1, n):
                if np.isnan(r[i]): continue
                if r[i] < os_level: signals[i] = 1
                elif r[i] > ob: signals[i] = -1
                else: signals[i] = signals[i-1]

        elif strategy in ("mean_rev", "bb_breakout"):
            period = params["period"]
            num_std = params["num_std"]
            bb_up, bb_mid, bb_lo = talib.BBANDS(closes, timeperiod=period, nbdevup=num_std, nbdevdn=num_std, matype=0)
            for i in range(period, n):
                if np.isnan(bb_up[i]): continue
                if strategy == "mean_rev":
                    if closes[i] < bb_lo[i]: signals[i] = 1
                    elif closes[i] > bb_up[i]: signals[i] = -1
                    else: signals[i] = signals[i-1]
                else:
                    if closes[i] > bb_up[i]: signals[i] = 1
                    elif closes[i] < bb_lo[i]: signals[i] = -1
                    else: signals[i] = signals[i-1]

        elif strategy == "donchian":
            period = params["period"]
            for i in range(period, n):
                window = closes[i - period:i]
                if closes[i] > np.max(window): signals[i] = 1
                elif closes[i] < np.min(window): signals[i] = -1
                else: signals[i] = signals[i-1]

        elif strategy == "momentum":
            lookback = params["lookback"]
            skip = params["skip"]
            for i in range(lookback, n):
                mom = closes[i] / closes[i - lookback] - 1
                mom_skip = closes[i] / closes[i - skip] - 1 if i >= skip else 0
                signals[i] = 1 if (mom - mom_skip) > 0 else -1

        return signals

    def _compute_wf_sharpe(signals):
        """Compute walk-forward OOS Sharpe — the actual objective."""
        daily_rets = np.zeros(n)
        for i in range(1, n):
            if signals[i] != 0:
                daily_rets[i] = signals[i] * (closes[i] / closes[i-1] - 1)
        for i in range(1, n):
            if signals[i] != signals[i-1]:
                if signals[i] != 0 and signals[i-1] != 0:
                    daily_rets[i] -= 2 * cost_1x
                elif signals[i] != 0 or signals[i-1] != 0:
                    daily_rets[i] -= cost_1x

        # Walk-forward: 60% train, 20% test, rolling
        wf_sharpes = []
        test_size = max(n // 5, 50)
        train_size = max(int(n * 0.6), 200)
        start = 0
        while start + train_size + test_size <= n:
            test_start = start + train_size
            test_end = test_start + test_size
            test_rets = daily_rets[test_start:test_end]
            if len(test_rets) >= 20:
                tm = float(np.mean(test_rets))
                ts = float(np.std(test_rets, ddof=1))
                wf_sharpes.append(tm / ts * ann if ts > 0 else 0)
            start += test_size

        if not wf_sharpes:
            # Fallback: OOS on last 30%
            oos_start = int(n * 0.7)
            oos_rets = daily_rets[oos_start:]
            if len(oos_rets) < 20: return -999
            m = float(np.mean(oos_rets))
            s = float(np.std(oos_rets, ddof=1))
            return m / s * ann if s > 0 else 0

        return float(np.mean(wf_sharpes))

    # ── Define Optuna search spaces per strategy ──
    def _get_search_space(trial, strategy):
        if strategy == "sma_cross":
            return {"fast": trial.suggest_int("fast", 10, 100, step=5), "slow": trial.suggest_int("slow", 50, 300, step=10)}
        elif strategy == "ema_cross":
            return {"fast": trial.suggest_int("fast", 5, 50, step=1), "slow": trial.suggest_int("slow", 15, 100, step=5)}
        elif strategy == "macd":
            return {"fast": trial.suggest_int("fast", 6, 20), "slow": trial.suggest_int("slow", 18, 40), "signal": trial.suggest_int("signal", 5, 15)}
        elif strategy == "rsi_ob_os":
            return {"period": trial.suggest_int("period", 7, 21), "oversold": trial.suggest_int("oversold", 20, 40), "overbought": trial.suggest_int("overbought", 60, 80)}
        elif strategy in ("mean_rev", "bb_breakout"):
            return {"period": trial.suggest_int("period", 10, 40), "num_std": trial.suggest_float("num_std", 1.5, 3.0, step=0.25)}
        elif strategy == "donchian":
            return {"period": trial.suggest_int("period", 10, 50, step=5)}
        elif strategy == "momentum":
            return {"lookback": trial.suggest_int("lookback", 63, 252, step=21), "skip": trial.suggest_int("skip", 5, 42, step=7)}
        # Composite + calendar strategies use fixed params — no optimization needed
        return {}

    # ── Run Optuna for each strategy ──
    all_results = []
    total_trials = 0

    for strategy in req.strategies:
        try:
            study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))

            def objective(trial):
                params = _get_search_space(trial, strategy)
                signals = _generate_parameterized_signals(strategy, params)
                active_bars = np.sum(signals != 0)
                if active_bars < 30:
                    return -999  # not enough trades
                wf_sharpe = _compute_wf_sharpe(signals)
                return wf_sharpe

            study.optimize(objective, n_trials=req.n_trials, show_progress_bar=False)
            total_trials += len(study.trials)

            best = study.best_trial
            if best.value <= -900:
                continue

            # Recompute full metrics for the best parameters
            best_params = _get_search_space(best, strategy)
            # Re-extract params from trial (they're stored in best.params)
            best_signals = _generate_parameterized_signals(strategy, best.params)

            daily_rets = np.zeros(n)
            for i in range(1, n):
                if best_signals[i] != 0:
                    daily_rets[i] = best_signals[i] * (closes[i] / closes[i-1] - 1)
            for i in range(1, n):
                if best_signals[i] != best_signals[i-1]:
                    if best_signals[i] != 0 and best_signals[i-1] != 0:
                        daily_rets[i] -= 2 * cost_1x
                    elif best_signals[i] != 0 or best_signals[i-1] != 0:
                        daily_rets[i] -= cost_1x

            warmup = max(200, int(n * 0.1))
            all_rets = daily_rets[warmup:]
            if len(all_rets) < 30: continue

            mean_r = float(np.mean(all_rets))
            std_r = float(np.std(all_rets, ddof=1))
            sharpe = mean_r / std_r * ann if std_r > 0 else 0
            skew = float(sp_stats.skew(all_rets))
            kurt = float(sp_stats.kurtosis(all_rets))

            eq = np.cumprod(1 + daily_rets)
            total_ret = float(eq[-1] - 1) * 100
            years = n / 252
            cagr = (float(eq[-1]) ** (1 / years) - 1) * 100 if years > 0 else 0
            peak = np.maximum.accumulate(eq)
            max_dd = float(np.min((eq / peak - 1) * 100))

            trades, wins, pos, entry_px = 0, 0, 0, 0.0
            for i in range(1, n):
                if best_signals[i] != 0 and pos == 0:
                    pos = int(best_signals[i]); entry_px = closes[i]
                elif pos != 0 and best_signals[i] != pos:
                    pnl = (closes[i] / entry_px - 1) * pos
                    trades += 1
                    if pnl > 2 * cost_1x: wins += 1
                    if best_signals[i] != 0: pos = int(best_signals[i]); entry_px = closes[i]
                    else: pos = 0
            win_rate = round(wins / max(trades, 1) * 100, 1)

            # DSR with n_tested = total_trials (across all strategies)
            n_obs = len(all_rets)
            var_sr = (1 + 0.5 * sharpe**2 - skew * sharpe + (kurt / 4) * sharpe**2) / max(n_obs - 1, 1)
            se_sr = np.sqrt(max(var_sr, 1e-10))
            effective_n = total_trials  # will be updated after all strategies run
            e_max_sr = float(sp_stats.norm.ppf(1 - 1 / max(effective_n, 2))) * (1 - 0.5772 / max(np.log(max(effective_n, 2)), 1))
            dsr = float(sp_stats.norm.cdf((sharpe - e_max_sr) / se_sr))

            current_signal = "Long" if best_signals[-1] == 1 else "Short" if best_signals[-1] == -1 else "Flat"
            signal_days = 0
            for i in range(n - 1, -1, -1):
                if best_signals[i] == best_signals[-1]: signal_days += 1
                else: break

            # Parameter importance
            try:
                importance = optuna.importance.get_param_importances(study)
                param_importance = {k: round(v, 3) for k, v in importance.items()}
            except Exception:
                param_importance = {}

            all_results.append({
                "strategy": strategy,
                "best_params": {k: round(v, 4) if isinstance(v, float) else v for k, v in best.params.items()},
                "wf_sharpe": round(best.value, 3),
                "sharpe": round(sharpe, 3),
                "dsr": round(dsr, 4),
                "dsr_pct": round(dsr * 100, 1),
                "cagr": round(cagr, 1),
                "max_dd": round(max_dd, 1),
                "total_ret": round(total_ret, 1),
                "win_rate": win_rate,
                "trades": trades,
                "current_signal": current_signal,
                "signal_days": signal_days,
                "n_trials": len(study.trials),
                "param_importance": param_importance,
            })

        except Exception:
            continue

    # Recompute DSR with final total_trials count
    for r in all_results:
        n_obs_approx = n - max(200, int(n * 0.1))
        s = r["sharpe"]
        sk = sp_stats.skew(np.zeros(10))  # placeholder — use stored values
        # Quick recompute with correct n_tested
        var_sr = (1 + 0.5 * s**2) / max(n_obs_approx - 1, 1)
        se_sr = np.sqrt(max(var_sr, 1e-10))
        e_max = float(sp_stats.norm.ppf(1 - 1 / max(total_trials, 2))) * (1 - 0.5772 / max(np.log(max(total_trials, 2)), 1))
        r["dsr"] = round(float(sp_stats.norm.cdf((s - e_max) / se_sr)), 4)
        r["dsr_pct"] = round(r["dsr"] * 100, 1)
        r["n_tested_total"] = total_trials

    all_results.sort(key=lambda r: r["dsr"], reverse=True)

    return {
        "ticker": tk,
        "timeframe": req.timeframe,
        "total_trials": total_trials,
        "strategies_tested": len(req.strategies),
        "results": all_results,
        "success": True,
    }


from fastapi.responses import StreamingResponse
import json as _json
import asyncio


@router.get("/strategy-scan-stream")
async def strategy_scan_stream(
    tickers: str = "SPY,QQQ,AAPL,MSFT,NVDA,TSLA,AMD,AMZN,META,GOOGL",
    strategies: str = "sma_cross,ema_cross,macd,rsi_ob_os,mean_rev,adx_di,ichimoku,tema_cross,stochastic,parabolic_sar",
    timeframe: str = "daily",
    commission_bps: int = 5,
    slippage_bps: int = 5,
):
    """SSE endpoint: streams scan results one at a time as each combo completes."""
    import numpy as np
    import talib
    from scipy import stats as sp_stats
    import yfinance as yf

    ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
    strat_list = [s.strip() for s in strategies.split(",") if s.strip()]
    n_tested = len(ticker_list) * len(strat_list)
    cost_1x = (commission_bps + slippage_bps) / 10000
    BARS_PER_YEAR = {"daily": 252, "60min": 1638, "15min": 6552, "5min": 19656}
    bars_yr = BARS_PER_YEAR.get(timeframe, 252)
    ann = np.sqrt(bars_yr)

    def _sma(c, p): return talib.SMA(c, timeperiod=p)
    def _ema(c, p): return talib.EMA(c, timeperiod=p)
    def _rsi(c, p=14): return talib.RSI(c, timeperiod=p)

    # Expected max Sharpe for DSR
    if n_tested > 1:
        e_max_sr = float(sp_stats.norm.ppf(1 - 1 / n_tested)) * (1 - 0.5772 / max(np.log(n_tested), 1))
    else:
        e_max_sr = 0

    async def event_stream():
        import random

        # Send initial metadata
        yield f"data: {_json.dumps({'type': 'init', 'tickers': ticker_list, 'strategies': strat_list, 'n_tested': n_tested})}\n\n"

        # Build all combos and shuffle for visual randomness
        all_combos = [(tk, strat) for tk in ticker_list for strat in strat_list]
        random.shuffle(all_combos)

        # Cache fetched data per ticker (download once, reuse)
        data_cache: dict = {}

        completed = 0
        for tk, strat in all_combos:
            # Fetch data (cached per ticker)
            if tk not in data_cache:
                try:
                    period_map = {252: "1y", 504: "2y", 756: "3y", 1260: "5y"}
                    df = yf.Ticker(tk).history(period=period_map.get(1260, "5y"), auto_adjust=True)
                    if df is None or len(df) < 200:
                        data_cache[tk] = None
                    else:
                        data_cache[tk] = df
                except Exception:
                    data_cache[tk] = None

            df = data_cache.get(tk)
            if df is None:
                completed += 1
                yield f"data: {_json.dumps({'type': 'skip', 'ticker': tk, 'strategy': strat, 'completed': completed})}\n\n"
                await asyncio.sleep(0)
                continue

            closes = df["Close"].values.astype(float).ravel()
            highs = df["High"].values.astype(float).ravel()
            lows = df["Low"].values.astype(float).ravel()
            n = len(closes)
            warmup = max(200, int(n * 0.1))

            # B&H for this ticker
            bh_rets = np.diff(closes) / closes[:-1]
            bh_rets_full = np.concatenate([[0], bh_rets])
            bh_m = float(np.mean(bh_rets_full[warmup:]))
            bh_s = float(np.std(bh_rets_full[warmup:], ddof=1))
            bh_sharpe = bh_m / bh_s * ann if bh_s > 0 else 0

            completed += 1
            try:
                # Generate signals (simplified inline)
                signals = np.zeros(n)
                if strat == "sma_cross":
                    f, s = _sma(closes, 50), _sma(closes, 200)
                    for i in range(200, n):
                        if not np.isnan(f[i]) and not np.isnan(s[i]): signals[i] = 1 if f[i] > s[i] else -1
                elif strat == "ema_cross":
                    f, s = _ema(closes, 12), _ema(closes, 26)
                    for i in range(26, n): signals[i] = 1 if f[i] > s[i] else -1
                elif strat == "macd":
                    ef, es = _ema(closes, 12), _ema(closes, 26); ml = ef - es; sl = _ema(ml, 9)
                    for i in range(34, n): signals[i] = 1 if ml[i] > sl[i] else -1
                elif strat == "rsi_ob_os":
                    r = _rsi(closes, 14)
                    for i in range(15, n):
                        if np.isnan(r[i]): continue
                        if r[i] < 30: signals[i] = 1
                        elif r[i] > 70: signals[i] = -1
                        else: signals[i] = signals[i-1]
                elif strat == "mean_rev":
                    bu, bm, bl = talib.BBANDS(closes, 20, 2, 2, 0)
                    for i in range(20, n):
                        if np.isnan(bu[i]): continue
                        if closes[i] < bl[i]: signals[i] = 1
                        elif closes[i] > bu[i]: signals[i] = -1
                        else: signals[i] = signals[i-1]
                elif strat == "adx_di":
                    adx = talib.ADX(highs, lows, closes, 14); pdi = talib.PLUS_DI(highs, lows, closes, 14); mdi = talib.MINUS_DI(highs, lows, closes, 14)
                    for i in range(15, n):
                        if np.isnan(adx[i]): continue
                        if adx[i] >= 25: signals[i] = 1 if pdi[i] > mdi[i] else -1
                        elif adx[i] < 20: signals[i] = 0
                        else: signals[i] = signals[i-1]
                elif strat == "ichimoku":
                    tk_s = (talib.MAX(highs, 9) + talib.MIN(lows, 9)) / 2; kj = (talib.MAX(highs, 26) + talib.MIN(lows, 26)) / 2
                    sa = (tk_s + kj) / 2; sb = (talib.MAX(highs, 52) + talib.MIN(lows, 52)) / 2
                    for i in range(52, n):
                        if np.isnan(sa[i]): continue
                        if closes[i] > max(sa[i], sb[i]) and tk_s[i] > kj[i]: signals[i] = 1
                        elif closes[i] < min(sa[i], sb[i]) and tk_s[i] < kj[i]: signals[i] = -1
                        else: signals[i] = signals[i-1]
                elif strat == "tema_cross":
                    tf = talib.TEMA(closes, 12); ts = talib.TEMA(closes, 26)
                    for i in range(26, n):
                        if not np.isnan(tf[i]) and not np.isnan(ts[i]): signals[i] = 1 if tf[i] > ts[i] else -1
                elif strat == "stochastic":
                    sk, sd = talib.STOCH(highs, lows, closes, 14, 3, 0, 3, 0)
                    for i in range(15, n):
                        if np.isnan(sk[i]): continue
                        if sk[i] < 20 and sk[i] > sd[i]: signals[i] = 1
                        elif sk[i] > 80 and sk[i] < sd[i]: signals[i] = -1
                        else: signals[i] = signals[i-1]
                elif strat == "parabolic_sar":
                    sar = talib.SAR(highs, lows, 0.02, 0.2)
                    for i in range(2, n):
                        if not np.isnan(sar[i]): signals[i] = 1 if closes[i] > sar[i] else -1
                elif strat == "momentum":
                    for i in range(252, n):
                        m12 = closes[i] / closes[i-252] - 1; m1 = closes[i] / closes[i-21] - 1
                        signals[i] = 1 if (m12 - m1) > 0 else -1
                elif strat == "donchian":
                    for i in range(20, n):
                        w = closes[i-20:i]
                        if closes[i] > np.max(w): signals[i] = 1
                        elif closes[i] < np.min(w): signals[i] = -1
                        else: signals[i] = signals[i-1]
                else:
                    signals = np.zeros(n)

                # Backtest
                dr = np.zeros(n)
                for i in range(1, n):
                    if signals[i] != 0: dr[i] = signals[i] * (closes[i] / closes[i-1] - 1)
                for i in range(1, n):
                    if signals[i] != signals[i-1]:
                        if signals[i] != 0 and signals[i-1] != 0: dr[i] -= 2 * cost_1x
                        elif signals[i] != 0 or signals[i-1] != 0: dr[i] -= cost_1x

                active_mask = signals[warmup:] != 0
                active_rets = dr[warmup:][active_mask]
                if len(active_rets) < 20:
                    yield f"data: {_json.dumps({'type': 'skip', 'ticker': tk, 'strategy': strat, 'completed': completed})}\n\n"
                    continue

                m = float(np.mean(active_rets)); s = float(np.std(active_rets, ddof=1))
                sharpe = m / s * ann if s > 0 else 0
                var_sr = (1 + 0.5 * sharpe**2) / max(len(active_rets) - 1, 1)
                se_sr = np.sqrt(max(var_sr, 1e-10))
                dsr = float(sp_stats.norm.cdf((sharpe - e_max_sr) / se_sr))

                eq = np.cumprod(1 + dr)
                cagr = (float(eq[-1]) ** (1 / max(n / bars_yr, 0.01)) - 1) * 100
                sig = "Long" if signals[-1] == 1 else "Short" if signals[-1] == -1 else "Flat"

                result = {
                    "type": "result", "ticker": tk, "strategy": strat,
                    "sharpe": round(sharpe, 3), "excess_sharpe": round(sharpe - bh_sharpe, 3),
                    "dsr": round(dsr, 4), "cagr": round(cagr, 1),
                    "signal": sig, "completed": completed,
                }
                yield f"data: {_json.dumps(result)}\n\n"

            except Exception:
                yield f"data: {_json.dumps({'type': 'error', 'ticker': tk, 'strategy': strat, 'completed': completed})}\n\n"

            await asyncio.sleep(0)  # yield control to event loop

        yield f"data: {_json.dumps({'type': 'done', 'completed': completed})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class ComboScanRequest(BaseModel):
    ticker: str = "SPY"
    strategies: list[str] = ["sma_cross", "ema_cross", "macd", "rsi_ob_os", "adx_di", "ichimoku", "tema_cross", "stochastic"]
    lookback_days: int = 1260
    timeframe: str = "daily"
    commission_bps: int = 5
    slippage_bps: int = 5
    max_combo_size: int = 2  # 2 = pairs, 3 = triples


@router.post("/combo-scan")
async def combo_scan(req: ComboScanRequest, user: str = Depends(get_current_user)):
    """Test all pairs (and optionally triples) of strategies using AND logic.

    AND logic: enter long only when ALL strategies in the combo agree on long.
    Compares each combo vs individual strategies vs buy-and-hold.
    """
    import numpy as np
    import talib
    from itertools import combinations
    from scipy import stats as sp_stats
    import yfinance as yf

    tk = req.ticker.upper()
    cost_1x = (req.commission_bps + req.slippage_bps) / 10000
    is_intraday = req.timeframe != "daily"
    BARS_PER_YEAR = {"daily": 252, "60min": 1638, "15min": 6552, "5min": 19656}
    bars_yr = BARS_PER_YEAR.get(req.timeframe, 252)
    ann = np.sqrt(bars_yr)

    # Fetch data
    if is_intraday:
        from datetime import datetime, timedelta
        import requests as req_http
        from src.api_keys import get_secret
        api_key = get_secret("MASSIVE_API_KEY")
        mult_map = {"60min": (1, "hour"), "15min": (15, "minute"), "5min": (5, "minute")}
        mult, span = mult_map.get(req.timeframe, (1, "hour"))
        max_days = min(req.lookback_days, 60 if span == "minute" and mult <= 5 else 180)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
        url = f"https://api.polygon.io/v2/aggs/ticker/{tk}/range/{mult}/{span}/{start}/{end}"
        r = req_http.get(url, params={"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}, timeout=30)
        import pandas as pd
        raw = r.json().get("results", [])
        if len(raw) < 100: return {"error": "Not enough data", "success": False}
        df = pd.DataFrame(raw).rename(columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"})
    else:
        period_map = {252: "1y", 504: "2y", 756: "3y", 1260: "5y", 2520: "10y"}
        period = period_map.get(req.lookback_days, "5y")
        df = yf.Ticker(tk).history(period=period, auto_adjust=True)
        if df is None or len(df) < 200: return {"error": "Not enough data", "success": False}

    closes = df["Close"].values.astype(float).ravel()
    highs = df["High"].values.astype(float).ravel()
    lows = df["Low"].values.astype(float).ravel()
    volumes = df["Volume"].values.astype(float).ravel() if "Volume" in df.columns else None
    n = len(closes)
    warmup = max(200, int(n * 0.1))

    # TA-Lib helpers
    def _sma(c, p): return talib.SMA(c, timeperiod=p)
    def _ema(c, p): return talib.EMA(c, timeperiod=p)
    def _rsi(c, p=14): return talib.RSI(c, timeperiod=p)

    # Generate signals for ALL strategies (reuse _generate_signals from strategy_scan)
    # Inline a simplified version to avoid circular dependency
    all_signals = {}
    for strat in req.strategies:
        signals = np.zeros(n)
        try:
            if strat == "sma_cross":
                f, s = _sma(closes, 50), _sma(closes, 200)
                for i in range(200, n):
                    if not np.isnan(f[i]) and not np.isnan(s[i]): signals[i] = 1 if f[i] > s[i] else -1
            elif strat == "ema_cross":
                f, s = _ema(closes, 12), _ema(closes, 26)
                for i in range(26, n): signals[i] = 1 if f[i] > s[i] else -1
            elif strat == "macd":
                ef, es = _ema(closes, 12), _ema(closes, 26)
                ml = ef - es; sl = _ema(ml, 9)
                for i in range(34, n): signals[i] = 1 if ml[i] > sl[i] else -1
            elif strat == "rsi_ob_os":
                r = _rsi(closes, 14)
                for i in range(15, n):
                    if np.isnan(r[i]): continue
                    if r[i] < 30: signals[i] = 1
                    elif r[i] > 70: signals[i] = -1
                    else: signals[i] = signals[i-1]
            elif strat == "adx_di":
                adx = talib.ADX(highs, lows, closes, 14)
                pdi = talib.PLUS_DI(highs, lows, closes, 14)
                mdi = talib.MINUS_DI(highs, lows, closes, 14)
                for i in range(15, n):
                    if np.isnan(adx[i]): continue
                    if adx[i] >= 25: signals[i] = 1 if pdi[i] > mdi[i] else -1
                    elif adx[i] < 20: signals[i] = 0
                    else: signals[i] = signals[i-1]
            elif strat == "ichimoku":
                tk_s = (talib.MAX(highs, 9) + talib.MIN(lows, 9)) / 2
                kj = (talib.MAX(highs, 26) + talib.MIN(lows, 26)) / 2
                sa = (tk_s + kj) / 2; sb = (talib.MAX(highs, 52) + talib.MIN(lows, 52)) / 2
                for i in range(52, n):
                    if np.isnan(sa[i]): continue
                    ct = max(sa[i], sb[i]); cb = min(sa[i], sb[i])
                    if closes[i] > ct and tk_s[i] > kj[i]: signals[i] = 1
                    elif closes[i] < cb and tk_s[i] < kj[i]: signals[i] = -1
                    else: signals[i] = signals[i-1]
            elif strat == "tema_cross":
                tf = talib.TEMA(closes, 12); ts = talib.TEMA(closes, 26)
                for i in range(26, n):
                    if not np.isnan(tf[i]) and not np.isnan(ts[i]): signals[i] = 1 if tf[i] > ts[i] else -1
            elif strat == "stochastic":
                sk, sd = talib.STOCH(highs, lows, closes, 14, 3, 0, 3, 0)
                for i in range(15, n):
                    if np.isnan(sk[i]): continue
                    if sk[i] < 20 and sk[i] > sd[i]: signals[i] = 1
                    elif sk[i] > 80 and sk[i] < sd[i]: signals[i] = -1
                    else: signals[i] = signals[i-1]
            elif strat == "parabolic_sar":
                sar = talib.SAR(highs, lows, 0.02, 0.2)
                for i in range(2, n):
                    if not np.isnan(sar[i]): signals[i] = 1 if closes[i] > sar[i] else -1
            elif strat == "mean_rev":
                bu, bm, bl = talib.BBANDS(closes, 20, 2, 2, 0)
                for i in range(20, n):
                    if np.isnan(bu[i]): continue
                    if closes[i] < bl[i]: signals[i] = 1
                    elif closes[i] > bu[i]: signals[i] = -1
                    else: signals[i] = signals[i-1]
            elif strat == "donchian":
                for i in range(20, n):
                    w = closes[i-20:i]
                    if closes[i] > np.max(w): signals[i] = 1
                    elif closes[i] < np.min(w): signals[i] = -1
                    else: signals[i] = signals[i-1]
            elif strat == "momentum":
                for i in range(252, n):
                    m12 = closes[i] / closes[i-252] - 1
                    m1 = closes[i] / closes[i-21] - 1
                    signals[i] = 1 if (m12 - m1) > 0 else -1
            elif strat == "williams_r":
                wr = talib.WILLR(highs, lows, closes, 14)
                for i in range(14, n):
                    if np.isnan(wr[i]): continue
                    if wr[i] < -80: signals[i] = 1
                    elif wr[i] > -20: signals[i] = -1
                    else: signals[i] = signals[i-1]
            elif strat == "cci":
                cc = talib.CCI(highs, lows, closes, 20)
                for i in range(20, n):
                    if np.isnan(cc[i]): continue
                    if cc[i] < -100: signals[i] = 1
                    elif cc[i] > 100: signals[i] = -1
                    elif abs(cc[i]) < 50: signals[i] = 0
                    else: signals[i] = signals[i-1]
        except Exception:
            pass
        all_signals[strat] = signals

    # B&H equity (computed once, shared)
    bh_dr = np.zeros(n)
    for i in range(1, n): bh_dr[i] = closes[i] / closes[i-1] - 1
    bh_eq = np.cumprod(1 + bh_dr)
    bh_p = bh_dr[warmup:]
    bh_sharpe_global = float(np.mean(bh_p)) / float(np.std(bh_p, ddof=1)) * ann if np.std(bh_p) > 0 else 0

    # Subsample indices for chart data (every Nth bar to limit payload)
    step = max(1, n // 200)
    chart_indices = list(range(0, n, step))

    def _backtest_signals(combo_signals, include_charts=False):
        """Backtest a signal array and return metrics + optional chart data."""
        dr = np.zeros(n)
        for i in range(1, n):
            if combo_signals[i] != 0:
                dr[i] = combo_signals[i] * (closes[i] / closes[i-1] - 1)
        for i in range(1, n):
            if combo_signals[i] != combo_signals[i-1]:
                if combo_signals[i] != 0 and combo_signals[i-1] != 0: dr[i] -= 2 * cost_1x
                elif combo_signals[i] != 0 or combo_signals[i-1] != 0: dr[i] -= cost_1x

        active_mask = combo_signals[warmup:] != 0
        active_rets = dr[warmup:][active_mask]
        if len(active_rets) < 15: return None

        m = float(np.mean(active_rets)); s = float(np.std(active_rets, ddof=1))
        sharpe = m / s * ann if s > 0 else 0

        eq = np.cumprod(1 + dr)
        total_ret = float(eq[-1] - 1) * 100
        years = n / bars_yr
        cagr = (float(eq[-1]) ** (1 / max(years, 0.01)) - 1) * 100
        peak = np.maximum.accumulate(eq); max_dd = float(np.min((eq / peak - 1) * 100))
        dd = (eq / peak - 1) * 100

        pct_active = round(float(np.sum(active_mask)) / max(len(active_mask), 1) * 100, 0)
        trades = int(np.sum(np.diff(combo_signals) != 0))
        cur = "Long" if combo_signals[-1] == 1 else "Short" if combo_signals[-1] == -1 else "Flat"

        result = {
            "sharpe": round(sharpe, 3), "bh_sharpe": round(bh_sharpe_global, 3),
            "excess_sharpe": round(sharpe - bh_sharpe_global, 3),
            "cagr": round(cagr, 1), "total_ret": round(total_ret, 1), "max_dd": round(max_dd, 1),
            "pct_active": pct_active, "trades": trades, "current_signal": cur,
        }

        if include_charts:
            # Subsampled chart data (~200 points)
            result["chart"] = {
                "equity": [round(float(eq[i]) * 100 - 100, 1) for i in chart_indices],
                "bh_equity": [round(float(bh_eq[i]) * 100 - 100, 1) for i in chart_indices],
                "drawdown": [round(float(dd[i]), 1) for i in chart_indices],
                "signals": [int(combo_signals[i]) for i in chart_indices],
                "x_indices": chart_indices,
            }

        return result

    # ── Individual strategy results ──
    individual_results = {}
    for strat, sigs in all_signals.items():
        r = _backtest_signals(sigs)
        if r: individual_results[strat] = r

    # ── Combination results (AND logic) ──
    combo_results = []
    n_combos = 0
    for size in range(2, min(req.max_combo_size + 1, 4)):
        for combo in combinations(req.strategies, size):
            n_combos += 1
            # AND logic: all must agree
            combo_sigs = np.zeros(n)
            for i in range(warmup, n):
                votes = [all_signals[s][i] for s in combo]
                if all(v == 1 for v in votes): combo_sigs[i] = 1
                elif all(v == -1 for v in votes): combo_sigs[i] = -1
                # else flat (disagreement)

            r = _backtest_signals(combo_sigs)
            if r:
                combo_results.append({
                    "combo": list(combo), "size": size,
                    "logic": "AND", **r,
                })

    # Sort by excess Sharpe
    combo_results.sort(key=lambda x: x["excess_sharpe"], reverse=True)

    # Add chart data to top 10 combos (re-run with include_charts=True)
    for cr in combo_results[:10]:
        combo_sigs = np.zeros(n)
        for i in range(warmup, n):
            votes = [all_signals[s][i] for s in cr["combo"]]
            if all(v == 1 for v in votes): combo_sigs[i] = 1
            elif all(v == -1 for v in votes): combo_sigs[i] = -1
        r_with_charts = _backtest_signals(combo_sigs, include_charts=True)
        if r_with_charts and "chart" in r_with_charts:
            cr["chart"] = r_with_charts["chart"]

    # Also add charts for individual strategies
    for strat, sigs in all_signals.items():
        if strat in individual_results:
            r_with_charts = _backtest_signals(sigs, include_charts=True)
            if r_with_charts and "chart" in r_with_charts:
                individual_results[strat]["chart"] = r_with_charts["chart"]

    # DSR for combos (n_tested = total combos + individuals)
    total_tested = n_combos + len(req.strategies)
    if total_tested > 1:
        e_max = float(sp_stats.norm.ppf(1 - 1 / total_tested)) * (1 - 0.5772 / max(np.log(total_tested), 1))
    else:
        e_max = 0
    for r in combo_results:
        s = r["sharpe"]
        n_obs = max(int(n * r["pct_active"] / 100), 20)
        var_sr = (1 + 0.5 * s**2) / max(n_obs - 1, 1)
        se = np.sqrt(max(var_sr, 1e-10))
        r["dsr"] = round(float(sp_stats.norm.cdf((s - e_max) / se)), 4)
        r["dsr_pct"] = round(r["dsr"] * 100, 1)

    return {
        "success": True,
        "ticker": tk, "timeframe": req.timeframe,
        "n_strategies": len(req.strategies),
        "n_combos_tested": n_combos,
        "individual": individual_results,
        "combos": combo_results[:30],
        "best_combo": combo_results[0] if combo_results else None,
        "best_individual": max(individual_results.items(), key=lambda x: x[1]["excess_sharpe"])[0] if individual_results else None,
    }


class DeepScanRequest(BaseModel):
    tickers: list[str] = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMD", "AMZN", "META", "GOOGL"]
    strategies: list[str] = ["sma_cross", "ema_cross", "macd", "rsi_ob_os", "mean_rev", "momentum", "donchian",
                              "adx_di", "stochastic", "parabolic_sar", "ichimoku", "tema_cross",
                              "trend_mr_composite", "trend_bb_composite"]
    timeframes: list[str] = ["daily", "60min"]
    commission_bps: int = 5
    slippage_bps: int = 5


@router.post("/deep-scan")
async def deep_scan(req: DeepScanRequest, user: str = Depends(get_current_user)):
    """Run strategy scan across ALL timeframes, aggregate, and analyze.

    Returns per-combo results + meta-analysis: strategy rankings, ticker rankings,
    timeframe rankings, heatmap data, correlation matrix, portfolio recommendation.
    """
    import numpy as np
    from concurrent.futures import ThreadPoolExecutor, as_completed

    all_results = []
    total_tested = 0

    # Run scan for each timeframe sequentially (each scan is already parallel internally)
    for tf in req.timeframes:
        try:
            scan_req = StrategyScanRequest(
                tickers=req.tickers, strategies=req.strategies,
                lookback_days=1260 if tf == "daily" else 60,
                timeframe=tf, min_dsr=0.0,
                commission_bps=req.commission_bps, slippage_bps=req.slippage_bps,
            )
            result = await strategy_scan(scan_req, user=user)
            for r in result.get("results", []):
                r["timeframe"] = tf
                all_results.append(r)
            total_tested += result.get("n_tested", 0)
        except Exception:
            pass

    if not all_results:
        return {"error": "No results from any timeframe", "success": False}

    # Recompute DSR with TOTAL n_tested across all timeframes (correct for full multiple testing)
    from scipy import stats as sp_stats
    true_n_tested = len(req.tickers) * len(req.strategies) * len(req.timeframes)
    if true_n_tested > 1:
        e_max_sr = float(sp_stats.norm.ppf(1 - 1 / true_n_tested)) * (1 - 0.5772 / max(np.log(true_n_tested), 1))
    else:
        e_max_sr = 0
    for r in all_results:
        s = r["sharpe"]
        sk = r.get("skew", 0)
        kt = r.get("kurtosis", 0)
        n_obs = max(r.get("n_days", 252) - 200, 60)
        var_sr = (1 + 0.5 * s**2 - sk * s + (kt / 4) * s**2) / max(n_obs - 1, 1)
        se_sr = np.sqrt(max(var_sr, 1e-10))
        r["dsr"] = round(float(sp_stats.norm.cdf((s - e_max_sr) / se_sr)), 4)
        r["dsr_pct"] = round(r["dsr"] * 100, 1)
    total_tested = true_n_tested

    # ── Meta-Analysis ──

    # 1. Strategy rankings (avg DSR across all tickers/timeframes)
    strategy_stats = {}
    for r in all_results:
        s = r["strategy"]
        if s not in strategy_stats:
            strategy_stats[s] = {"dsrs": [], "sharpes": [], "win_rates": [], "active_signals": 0}
        strategy_stats[s]["dsrs"].append(r["dsr"])
        strategy_stats[s]["sharpes"].append(r["sharpe"])
        strategy_stats[s]["win_rates"].append(r["win_rate"])
        if r["current_signal"] != "Flat":
            strategy_stats[s]["active_signals"] += 1

    strategy_rankings = []
    for s, st in strategy_stats.items():
        strategy_rankings.append({
            "strategy": s,
            "avg_dsr": round(float(np.mean(st["dsrs"])), 4),
            "median_dsr": round(float(np.median(st["dsrs"])), 4),
            "avg_sharpe": round(float(np.mean(st["sharpes"])), 3),
            "avg_win_rate": round(float(np.mean(st["win_rates"])), 1),
            "n_significant": sum(1 for d in st["dsrs"] if d >= 0.95),
            "n_tested": len(st["dsrs"]),
            "pct_significant": round(sum(1 for d in st["dsrs"] if d >= 0.95) / max(len(st["dsrs"]), 1) * 100, 0),
            "active_signals": st["active_signals"],
        })
    strategy_rankings.sort(key=lambda x: x["avg_dsr"], reverse=True)

    # 2. Ticker rankings (avg DSR across all strategies/timeframes)
    ticker_stats = {}
    for r in all_results:
        tk = r["ticker"]
        if tk not in ticker_stats:
            ticker_stats[tk] = {"dsrs": [], "sharpes": [], "best_strategy": None, "best_dsr": 0}
        ticker_stats[tk]["dsrs"].append(r["dsr"])
        ticker_stats[tk]["sharpes"].append(r["sharpe"])
        if r["dsr"] > ticker_stats[tk]["best_dsr"]:
            ticker_stats[tk]["best_dsr"] = r["dsr"]
            ticker_stats[tk]["best_strategy"] = f"{r['strategy']} ({r.get('timeframe', 'daily')})"

    ticker_rankings = []
    for tk, st in ticker_stats.items():
        ticker_rankings.append({
            "ticker": tk,
            "avg_dsr": round(float(np.mean(st["dsrs"])), 4),
            "avg_sharpe": round(float(np.mean(st["sharpes"])), 3),
            "n_significant": sum(1 for d in st["dsrs"] if d >= 0.95),
            "best_strategy": st["best_strategy"],
            "best_dsr": round(st["best_dsr"], 4),
        })
    ticker_rankings.sort(key=lambda x: x["avg_dsr"], reverse=True)

    # 3. Timeframe rankings
    tf_stats = {}
    for r in all_results:
        tf = r.get("timeframe", "daily")
        if tf not in tf_stats:
            tf_stats[tf] = {"dsrs": [], "sharpes": [], "n_sig": 0}
        tf_stats[tf]["dsrs"].append(r["dsr"])
        tf_stats[tf]["sharpes"].append(r["sharpe"])
        if r["dsr"] >= 0.95:
            tf_stats[tf]["n_sig"] += 1

    timeframe_rankings = []
    for tf, st in tf_stats.items():
        timeframe_rankings.append({
            "timeframe": tf,
            "avg_dsr": round(float(np.mean(st["dsrs"])), 4),
            "avg_sharpe": round(float(np.mean(st["sharpes"])), 3),
            "n_significant": st["n_sig"],
            "n_tested": len(st["dsrs"]),
        })
    timeframe_rankings.sort(key=lambda x: x["avg_dsr"], reverse=True)

    # 4. Heatmap: Strategy × Ticker (best DSR across timeframes)
    heatmap = {}
    for r in all_results:
        key = (r["strategy"], r["ticker"])
        if key not in heatmap or r["dsr"] > heatmap[key]["dsr"]:
            heatmap[key] = {"dsr": r["dsr"], "timeframe": r.get("timeframe", "daily"), "signal": r["current_signal"]}

    heatmap_data = [{"strategy": k[0], "ticker": k[1], "dsr": round(v["dsr"], 3), "timeframe": v["timeframe"], "signal": v["signal"]}
                    for k, v in heatmap.items()]

    # 5. Top combos (significant + active signal)
    significant_active = [r for r in all_results if r["dsr"] >= 0.85 and r["current_signal"] != "Flat"]
    significant_active.sort(key=lambda x: x["dsr"], reverse=True)

    # 6. Strategy correlation (do strategies give the same signals?)
    # Group by ticker, compare signal arrays conceptually via agreement rate
    strategy_signals = {}
    for r in all_results:
        s = r["strategy"]
        if s not in strategy_signals:
            strategy_signals[s] = []
        strategy_signals[s].append(1 if r["current_signal"] == "Long" else -1 if r["current_signal"] == "Short" else 0)

    strat_names = list(strategy_signals.keys())
    corr_matrix = []
    for s1 in strat_names:
        row = []
        for s2 in strat_names:
            arr1 = np.array(strategy_signals[s1])
            arr2 = np.array(strategy_signals[s2])
            min_len = min(len(arr1), len(arr2))
            if min_len > 0:
                agreement = float(np.mean(arr1[:min_len] == arr2[:min_len]))
            else:
                agreement = 0.5
            row.append(round(agreement, 2))
        corr_matrix.append(row)

    # 7. Portfolio recommendation: top 3-5 uncorrelated strategies with active signals
    portfolio = []
    used_strats = set()
    for r in significant_active[:20]:
        s = r["strategy"]
        if s in used_strats:
            continue
        # Check correlation with already selected strategies
        correlated = False
        if s in strat_names:
            s_idx = strat_names.index(s)
            for ps in portfolio:
                ps_name = ps["strategy"]
                if ps_name in strat_names:
                    ps_idx = strat_names.index(ps_name)
                    if corr_matrix[s_idx][ps_idx] > 0.7:
                        correlated = True
                        break
        if not correlated:
            portfolio.append({
                "ticker": r["ticker"], "strategy": s, "timeframe": r.get("timeframe", "daily"),
                "signal": r["current_signal"], "signal_days": r["signal_days"],
                "dsr": round(r["dsr"], 4), "sharpe": r["sharpe"],
                "win_rate": r["win_rate"], "cagr": r["cagr"],
            })
            used_strats.add(s)
            if len(portfolio) >= 5:
                break

    return {
        "success": True,
        "total_results": len(all_results),
        "total_tested": total_tested,
        "n_significant": sum(1 for r in all_results if r["dsr"] >= 0.95),
        "n_active": sum(1 for r in all_results if r["current_signal"] != "Flat"),
        "all_results": sorted(all_results, key=lambda r: r["dsr"], reverse=True)[:100],
        "strategy_rankings": strategy_rankings,
        "ticker_rankings": ticker_rankings,
        "timeframe_rankings": timeframe_rankings,
        "heatmap": heatmap_data,
        "significant_active": significant_active[:20],
        "correlation": {"strategies": strat_names, "matrix": corr_matrix},
        "portfolio_recommendation": portfolio,
    }


@router.get("/fama-french")
async def fama_french_factors(
    days: int = Query(252, ge=30, le=2520),
    user: str = Depends(get_current_user),
):
    """Fetch Fama-French 5 factors + Momentum (daily). Cached 24h."""
    import io, zipfile, requests as req

    # Try cache first
    try:
        from api.routes.energy import _get_bundle_cache, _set_bundle_cache
        cached = _get_bundle_cache("fama_french_factors", ttl_minutes=1440)
        if cached:
            # Trim to requested days
            for k in cached:
                if isinstance(cached[k], list):
                    cached[k] = cached[k][-days:]
            return cached
    except Exception:
        pass

    try:
        # FF5
        r = req.get("https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip", timeout=20)
        z = zipfile.ZipFile(io.BytesIO(r.content))
        raw = z.read(z.namelist()[0]).decode("utf-8")
        lines = raw.split("\n")
        start = next((i for i, l in enumerate(lines) if l.strip() and l.strip()[0].isdigit()), None)
        if start is None:
            return {"error": "Failed to parse FF5 data"}

        header = [h.strip() for h in lines[start - 1].split(",")]
        data_lines = [l for l in lines[start:] if l.strip() and l.strip()[0].isdigit() and len(l.split(",")) >= 6]

        records = []
        for l in data_lines[-days:]:
            parts = [p.strip() for p in l.split(",")]
            if len(parts) >= 6:
                try:
                    records.append({
                        "date": f"{parts[0][:4]}-{parts[0][4:6]}-{parts[0][6:8]}",
                        "Mkt-RF": float(parts[1]) / 100,
                        "SMB": float(parts[2]) / 100,
                        "HML": float(parts[3]) / 100,
                        "RMW": float(parts[4]) / 100,
                        "CMA": float(parts[5]) / 100,
                        "RF": float(parts[6]) / 100 if len(parts) > 6 else 0,
                    })
                except (ValueError, IndexError):
                    continue

        result = {"factors": records[-days:], "count": len(records)}

        try:
            from api.routes.energy import _set_bundle_cache
            _set_bundle_cache("fama_french_factors", result, ttl_minutes=1440)
        except Exception:
            pass

        return result
    except Exception as e:
        return {"error": str(e), "factors": [], "count": 0}


@router.get("/fred-batch")
async def fred_batch(
    series: str = Query(..., description="Comma-separated FRED series IDs"),
    periods: int = Query(60, ge=5, le=500),
    user: str = Depends(get_current_user),
):
    """Fetch multiple FRED series in parallel. Returns {series_id: [{date/period, value}]}."""
    from concurrent.futures import ThreadPoolExecutor
    from src.market_data import fetch_fred_series

    ids = [s.strip().upper() for s in series.split(",") if s.strip()][:30]

    def fetch_one(sid: str):
        df = fetch_fred_series(sid, periods)
        if df is None or df.empty:
            return sid, []
        df = df.reset_index(drop=True)
        records = []
        for _, row in df.iterrows():
            r = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, "isoformat"):
                    r[col] = val.isoformat()
                elif hasattr(val, "strftime"):
                    r[col] = str(val)
                else:
                    r[col] = val
            records.append(r)
        return sid, records

    result = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for sid, data in pool.map(lambda s: fetch_one(s), ids):
            result[sid] = data

    return result


class TradeIdeaAnalysisRequest(BaseModel):
    ideas: list[dict] = []
    book_summary: str = ""
    news_summary: str = ""


@router.post("/trade-idea-analysis")
async def trade_idea_analysis(req: TradeIdeaAnalysisRequest, user: str = Depends(get_current_user)):
    """Generate AI analysis for trade ideas using Gemini."""
    from src.api_keys import get_secret

    if not req.ideas:
        return {"success": False, "error": "No ideas to analyze"}

    # Build context
    ctx = "TRADE IDEAS TO ANALYZE:\n\n"
    for i, idea in enumerate(req.ideas):
        ctx += f"#{i+1} {idea.get('ticker','?')} {idea.get('direction','?').upper()}\n"
        t = idea.get("trigger", {})
        ctx += f"  Trigger: {t.get('strategy','')} flipped {t.get('signalDays','')}d ago (DSR {t.get('dsr',0)*100:.0f}%, win {t.get('winRate',0)}%)\n"
        ctx += f"  Confluence: {idea.get('confluenceScore',0)}/{idea.get('totalFamilies',4)} scoring families confirm\n"
        fams = idea.get("familyConfirmations", [])
        ctx += f"  Families: {', '.join(f['label'] + ' ' + str(f['count']) + '/' + str(f['total']) for f in fams)}\n"
        ctx += f"  Price: ${idea.get('price',0):.2f} | Stop: ${idea.get('stop',0):.2f} (-{idea.get('riskPct',0)}%) | Target: ${idea.get('target',0):.2f} (+{idea.get('targetPct',0)}%)\n"
        ctx += f"  R:R: {idea.get('riskReward',0)}:1 | EV: ${idea.get('expectedValue',0):.2f}/trade\n"
        ctx += f"  RSI: {idea.get('rsi',50)} | ATR: ${idea.get('atr',0):.2f}\n"
        vol = idea.get("vol") or {}
        if vol.get("iv"):
            ctx += f"  IV: {vol['iv']}% | RV20: {vol.get('rv_20d','')}% | IVR: {vol.get('ivr','?')}\n"
        if vol.get("avg_earnings_move"):
            ctx += f"  Avg earnings move: ±{vol['avg_earnings_move']}%\n"
        if vol.get("next_earnings_days") is not None:
            ctx += f"  Next earnings: {vol['next_earnings_days']}d\n"
        if idea.get("optionsSuggestion"):
            ctx += f"  Options: {idea['optionsSuggestion']}\n"
        warns = idea.get("warnings", [])
        if warns:
            ctx += f"  Warnings: {'; '.join(warns)}\n"
        ctx += "\n"

    if req.book_summary:
        ctx += f"YOUR CURRENT POSITIONS:\n{req.book_summary}\n\n"

    if req.news_summary:
        ctx += f"TODAY'S NEWS (from live Grok search):\n{req.news_summary}\n\n"

    from datetime import datetime as _dt
    today = _dt.now().strftime("%A, %B %d, %Y")

    system = f"""You are a quantitative trading analyst. Today is {today}.

These trade ideas have ALREADY been filtered for positive expected value, 2+ family confirmation, and R:R >= 1.0. They passed the filters — your job is to add context, not second-guess the math.

For each trade idea, write a 2-3 sentence analysis:
1. WHY: Connect the technical signal to today's news. Does the news support or contradict the direction? (e.g., "GLD long aligns with Iran escalation driving gold demand")
2. RISK: What specific event or condition could invalidate this trade? Reference earnings dates, news, or portfolio overlap.
3. ACTION: Which options structure to use, citing the IV vs RV relationship. If IV > RV, sell premium. If IV < RV, buy options. Be specific with the structure.

RULES:
- Do NOT invent data. Only reference numbers from the data below. If a field is missing, say "not available" — do not guess.
- Do NOT say "skip" — these ideas passed the quantitative filters. Your job is to provide context, not veto.
- Connect each idea to a specific news headline if relevant.
- If the trader holds an existing position in this ticker, note the overlap.
- One section per idea. Use the ticker as a header (## TICKER)."""

    try:
        api_key = get_secret("GEMINI_API_KEY")
        if not api_key:
            return {"success": False, "error": "Gemini API key not configured"}

        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=f"{system}\n\n{ctx}",
            config=types.GenerateContentConfig(max_output_tokens=3000, temperature=0.2),
        )
        text = response.text or ""
        # Strip preamble
        import re as _re
        text = _re.sub(r"^\s*(?:Okay|Here|Sure|Let me)[^\n]*\n", "", text.strip())
        return {"success": True, "analysis": text.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


class VolAnalysisRequest(BaseModel):
    tickers: list[str] = []


@router.post("/vol-analysis")
async def vol_analysis(req: VolAnalysisRequest, user: str = Depends(get_current_user)):
    """Volatility cone, IVR, and historical earnings moves per ticker."""
    import numpy as np, yfinance as yf, logging
    from concurrent.futures import ThreadPoolExecutor
    _log = logging.getLogger(__name__)

    tickers = [t.strip().upper() for t in req.tickers[:20] if t.strip()]

    # Pre-fetch OHLCV via Supabase cache
    from src.ohlcv_cache import fetch_ohlcv as _vol_fetch
    _vol_ohlcv: dict = {}
    for tk in tickers:
        try:
            _vol_ohlcv[tk] = _vol_fetch(tk, 504)  # 2yr for vol cone
        except Exception:
            _vol_ohlcv[tk] = None

    def _analyze(ticker: str) -> dict:
        try:
            ytk = yf.Ticker(ticker)
            df = _vol_ohlcv.get(ticker)
            if df is None or len(df) < 60:
                return {"ticker": ticker}

            closes = df["Close"].values.astype(float).ravel()
            log_rets = np.diff(np.log(closes))

            # Vol cone: realized vol at different lookbacks
            vol_cone = {}
            for window in [10, 20, 30, 60, 90, 180, 252]:
                if len(log_rets) >= window:
                    rv = float(np.std(log_rets[-window:]) * np.sqrt(252) * 100)
                    vol_cone[f"rv_{window}d"] = round(rv, 1)

            # Current 20d realized vol
            rv_20 = float(np.std(log_rets[-20:]) * np.sqrt(252) * 100) if len(log_rets) >= 20 else 0

            # IV from options chain (ATM, nearest expiry)
            iv = None
            ivr = None
            try:
                exps = ytk.options
                if exps:
                    chain = ytk.option_chain(exps[0])
                    info = ytk.info or {}
                    price = float(info.get("regularMarketPrice") or info.get("currentPrice") or closes[-1])
                    # Find ATM call
                    calls = chain.calls
                    if len(calls) > 0:
                        calls_sorted = calls.iloc[(calls["strike"] - price).abs().argsort()[:2]]
                        iv_vals = calls_sorted["impliedVolatility"].dropna().values
                        if len(iv_vals) > 0:
                            iv = round(float(np.mean(iv_vals)) * 100, 1)

                    # IVR: where current IV sits in 1-year range of 20d realized vol
                    if iv and len(log_rets) >= 252:
                        rv_series = []
                        for i in range(20, min(len(log_rets), 252)):
                            rv_series.append(float(np.std(log_rets[i-20:i]) * np.sqrt(252) * 100))
                        if rv_series:
                            rv_min = min(rv_series)
                            rv_max = max(rv_series)
                            rv_range = rv_max - rv_min
                            if rv_range > 0:
                                ivr = round((iv - rv_min) / rv_range * 100, 0)
                                ivr = max(0, min(100, ivr))
            except Exception:
                pass

            # Historical earnings moves + next earnings date
            earnings_moves = []
            next_earnings = None
            next_earnings_days = None
            try:
                edates = ytk.earnings_dates
                if edates is not None and len(edates) > 0:
                    from datetime import datetime as _dt
                    now = _dt.now()
                    for d in edates.index:
                        try:
                            dt = d.tz_localize(None) if d.tzinfo else d
                        except Exception:
                            dt = d.replace(tzinfo=None) if hasattr(d, "replace") else d
                        if dt < now:
                            # Past earnings — compute move
                            try:
                                idx = df.index.get_indexer([dt], method="pad")[0]
                                if 0 < idx < len(closes) - 1:
                                    move_pct = abs(closes[idx + 1] - closes[idx]) / closes[idx] * 100
                                    earnings_moves.append(round(float(move_pct), 1))
                            except Exception:
                                pass
                        else:
                            # Future earnings
                            days = (dt - now).days
                            if next_earnings is None or days < next_earnings_days:
                                next_earnings = dt.strftime("%Y-%m-%d")
                                next_earnings_days = days
            except Exception:
                pass

            # Keep last 8 earnings moves
            earnings_moves = earnings_moves[-8:]
            avg_earnings_move = round(float(np.mean(earnings_moves)), 1) if earnings_moves else None
            max_earnings_move = round(float(np.max(earnings_moves)), 1) if earnings_moves else None

            # Options structure suggestion
            suggestion = None
            if iv is not None and ivr is not None:
                if ivr >= 50:
                    suggestion = "high_iv"
                elif ivr <= 25:
                    suggestion = "low_iv"
                else:
                    suggestion = "neutral_iv"

            # Short interest
            short_pct = None
            short_ratio = None
            try:
                info = ytk.info or {}
                sf = info.get("shortPercentOfFloat")
                if sf is not None:
                    short_pct = round(float(sf) * 100, 1)
                sr = info.get("shortRatio")
                if sr is not None:
                    short_ratio = round(float(sr), 1)
            except Exception:
                pass

            return {
                "ticker": ticker,
                "current_price": round(float(closes[-1]), 2),
                "rv_20d": round(rv_20, 1),
                "iv": iv,
                "iv_percentile": ivr,
                "ivr": ivr,
                "vol_cone": vol_cone,
                "avg_earnings_move": avg_earnings_move,
                "max_earnings_move": max_earnings_move,
                "n_earnings": len(earnings_moves),
                "next_earnings": next_earnings,
                "next_earnings_days": next_earnings_days,
                "suggestion": suggestion,
                "short_pct": short_pct,
                "short_ratio": short_ratio,
            }
        except Exception as e:
            _log.warning(f"Vol analysis failed for {ticker}: {e}")
            return {"ticker": ticker}

    results = []
    with ThreadPoolExecutor(max_workers=min(len(tickers), 8)) as pool:
        results = list(pool.map(_analyze, tickers))

    return {"success": True, "results": {r["ticker"]: r for r in results if r.get("ticker")}}


@router.get("/polymarket")
async def polymarket_odds(user: str = Depends(get_current_user)):
    """Fetch market-relevant prediction odds from Polymarket. No auth required."""
    import httpx, json, logging
    _log = logging.getLogger(__name__)

    # Curated slugs for trading-relevant events
    CURATED_SLUGS = [
        "fed-decision-in-april",
        "fed-decision-in-may",
        "fed-decision-in-june",
        "who-will-be-confirmed-as-fed-chair",
        "us-forces-enter-iran-by",
        "us-x-iran-ceasefire-by",
        "will-the-iranian-regime-fall-by-june-30",
        "us-recession-in-2026",
        "us-recession-in-2025",
    ]

    # Also search top active events for finance keywords
    KEYWORDS = ["fed", "recession", "tariff", "rate cut", "inflation", "gdp",
                "s&p", "trump", "iran", "oil", "economy", "trade war", "china",
                "bitcoin", "treasury", "debt ceiling", "ceasefire", "sanctions"]

    results = []
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Fetch top 100 events by liquidity
            resp = await client.get(
                "https://gamma-api.polymarket.com/events",
                params={"active": "true", "closed": "false", "limit": 100,
                        "order": "liquidity", "ascending": "false"},
            )
            resp.raise_for_status()
            events = resp.json()

            for ev in events:
                title = ev.get("title", "")
                title_lower = title.lower()
                slug = ev.get("slug", "")

                # Include if curated slug or keyword match
                is_relevant = slug in CURATED_SLUGS or any(k in title_lower for k in KEYWORDS)
                if not is_relevant:
                    continue

                markets = ev.get("markets", [])
                outcomes = []
                from datetime import datetime as _dt_pm, timedelta as _td_pm
                now_pm = _dt_pm.utcnow()

                for m in markets[:12]:
                    try:
                        prices = json.loads(m.get("outcomePrices", "[]"))
                        label = m.get("groupItemTitle") or m.get("question", "")[:50]
                        token_ids = json.loads(m.get("clobTokenIds", "[]"))
                        pct = float(prices[0]) * 100 if prices else 0
                        if pct < 2:
                            continue

                        # Parse end date to compute days until resolution
                        end_str = m.get("endDate") or m.get("endDateIso") or ""
                        days_out = 999
                        try:
                            if end_str:
                                end_dt = _dt_pm.fromisoformat(end_str.replace("Z", "+00:00")).replace(tzinfo=None)
                                days_out = max(0, (end_dt - now_pm).days)
                        except Exception:
                            pass

                        # Score: near-term + uncertain (20-80%) is most actionable
                        # Far-out or already >90% / <10% is priced in
                        uncertainty = 50 - abs(pct - 50)  # peaks at 50%, zero at 0/100
                        recency = max(0, 180 - days_out) / 180  # 1.0 = today, 0.0 = 6mo+
                        actionability = uncertainty * recency  # high = near-term + uncertain

                        outcomes.append({
                            "label": label[:40],
                            "yes_pct": round(pct, 1),
                            "token_id": token_ids[0] if token_ids else "",
                            "days_out": days_out,
                            "actionability": round(actionability, 1),
                        })
                    except Exception:
                        continue

                if outcomes:
                    # Sort by actionability (near-term + uncertain first), then by pct
                    outcomes.sort(key=lambda x: (-x["actionability"], -x["yes_pct"]))
                    results.append({
                        "title": title[:60],
                        "slug": slug,
                        "volume_24h": round(ev.get("volume24hr", 0)),
                        "liquidity": round(ev.get("liquidity", 0)),
                        "outcomes": outcomes[:4],
                        "url": f"https://polymarket.com/event/{slug}",
                    })

        # Sort by 24h volume
        results.sort(key=lambda x: -x["volume_24h"])

    except Exception as e:
        _log.warning(f"Polymarket fetch failed: {e}")

    return {"success": True, "markets": results[:12]}


@router.get("/polymarket-history")
async def polymarket_history(token_id: str = Query(...), interval: str = Query("1m"), user: str = Depends(get_current_user)):
    """Fetch price history for a Polymarket token. Returns [{t, p}] for sparklines."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://clob.polymarket.com/prices-history",
                params={"market": token_id, "interval": interval, "fidelity": 360},
            )
            resp.raise_for_status()
            data = resp.json()
            history = data.get("history", [])
            # Convert to percentage for frontend
            points = [{"t": pt["t"], "p": round(float(pt["p"]) * 100, 1)} for pt in history]
            return {"success": True, "points": points}
    except Exception as e:
        return {"success": False, "points": [], "error": str(e)}


@router.get("/risk")
async def risk_snapshot(user: str = Depends(get_current_user)):
    """Get risk dashboard data — conflict, macro regime, vol, strategy."""
    import json, os
    result = {"iran": None, "macro": None, "vol": None, "strategy": None}

    # Iran conflict
    try:
        f = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                         "src", "iran_conflict_history.json")
        if os.path.exists(f):
            with open(f, "r") as fh:
                data = json.load(fh)
            if data:
                bl = data[-1].get("blended", {})
                esc = bl.get("escalation_risk", {})
                result["iran"] = {
                    "score": esc.get("score", 0),
                    "level": esc.get("level", "Unknown"),
                    "oil_range": bl.get("oil_impact", {}).get("price_range"),
                }
    except Exception:
        pass

    # Macro regime
    try:
        f = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                         "src", "grok_regime_history.json")
        if os.path.exists(f):
            with open(f, "r") as fh:
                data = json.load(fh)
            if data:
                regimes = sorted(data[-1].get("regimes", []),
                                 key=lambda r: r.get("probability", 0), reverse=True)
                if regimes:
                    result["macro"] = {
                        "top_regime": regimes[0].get("name"),
                        "top_prob": regimes[0].get("probability"),
                        "regimes": [{"name": r["name"], "probability": r["probability"]}
                                    for r in regimes[:4]],
                    }
    except Exception:
        pass

    # Vol regime (SPY)
    try:
        from src.metrics_store import get_latest_snapshot
        spy = get_latest_snapshot("SPY")
        if spy and spy.get("atm_iv") is not None:
            iv = spy["atm_iv"]
            vrp = spy.get("vrp", 0)
            result["vol"] = {
                "atm_iv": round(iv * 100, 1),
                "level": "High" if iv > 0.25 else "Low" if iv < 0.15 else "Normal",
                "vrp": round(vrp * 100, 1) if vrp else None,
            }
            # Strategy recommendation
            if iv > 0.25 and vrp and vrp > 0.03:
                result["strategy"] = {"rec": "Iron Condors", "reason": "Rich vol + VRP"}
            elif iv < 0.18:
                result["strategy"] = {"rec": "Calendars", "reason": "Low vol, exploit contango"}
            elif vrp and vrp > 0.02:
                result["strategy"] = {"rec": "Iron Condors", "reason": "Positive VRP edge"}
            else:
                result["strategy"] = {"rec": "Both viable", "reason": "Normal conditions"}
    except Exception:
        pass

    return result
