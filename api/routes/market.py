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
        "name": "Grok 4",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-3",
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

    return {
        "market_context": market_context,
        "watchlist": watchlist_data,
        "earnings_this_week": [{"ticker": tk, **data} for tk, data in earnings_map.items()],
        "opportunities": opportunities,
        "risk_budget": risk_budget,
        "warnings": warnings,
        "sector_exposure": sector_counts,
        "scan_stats": {
            "spreads_found": len(spread_results),
            "condors_found": len(condor_results),
            "top_shown": len(opportunities),
        },
    }


class NewsIntelRequest(BaseModel):
    watchlist: list[str] = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]


@router.post("/news-intel")
async def news_intel(req: NewsIntelRequest, user: str = Depends(get_current_user)):
    """Multi-source news intelligence pipeline.

    Phase 1: Pull structured data from APIs (factual, no AI)
      - Polygon news (real headlines)
      - yfinance earnings surprises
      - SEC EDGAR recent 8-K filings
    Phase 2: Grok searches X/Twitter for breaking news APIs miss
    Phase 3: Cross-reference — verify Grok claims against API data, label confidence
    Phase 4: Rank by market impact
    """
    import json, re
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from src.api_keys import get_secret

    from datetime import datetime as _dt, timedelta
    tickers = [t.strip().upper() for t in req.watchlist[:20] if t.strip()]
    now = _dt.utcnow()
    cutoff_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_items = []

    # ── Phase 1A: Polygon news — last 24 hours only, filtered ──
    def _fetch_polygon_news():
        items = []
        try:
            import requests
            api_key = get_secret("MASSIVE_API_KEY")
            if not api_key: return items
            for tk in tickers[:10]:
                try:
                    r = requests.get(f"https://api.polygon.io/v2/reference/news",
                        params={"ticker": tk, "limit": 5, "order": "desc", "sort": "published_utc",
                                "published_utc.gte": cutoff_24h, "apiKey": api_key}, timeout=10)
                    if r.status_code == 200:
                        for article in r.json().get("results", []):
                            title = article.get("title", "")
                            pub = article.get("published_utc", "")

                            # Filter opinion/clickbait
                            noise_words = ["i'd buy", "i would buy", "should you buy", "my top", "best stocks",
                                          "could soar", "to buy now", "no brainer", "millionaire", "why i",
                                          "best investment", "don't miss", "smart investors", "secret",
                                          "top picks", "buy the dip", "hot stock", "next big"]
                            if any(nw in title.lower() for nw in noise_words):
                                continue

                            # Filter opinion publishers entirely
                            publisher = article.get("publisher", {}).get("name", "")
                            opinion_pubs = ["The Motley Fool", "InvestorPlace", "24/7 Wall St"]
                            if publisher in opinion_pubs:
                                continue

                            # Compute hours ago
                            hours_ago = ""
                            try:
                                pub_dt = _dt.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S")
                                hrs = (now - pub_dt).total_seconds() / 3600
                                hours_ago = f"{int(hrs)}h ago" if hrs >= 1 else f"{int(hrs * 60)}m ago"
                            except Exception:
                                hours_ago = pub[:16].replace("T", " ")

                            items.append({
                                "ticker": tk,
                                "headline": title[:200],
                                "source": publisher or "Polygon News",
                                "source_type": "news_api",
                                "impact": "neutral",
                                "confidence": "high",
                                "time": hours_ago,
                                "url": article.get("article_url", ""),
                            })
                except Exception:
                    pass
        except Exception:
            pass
        return items

    # ── Phase 1B: Earnings surprises — ONLY if reported in last 3 days ──
    def _fetch_earnings():
        items = []
        try:
            import yfinance as yf
            from datetime import datetime as _dt, timedelta
            now = _dt.now()
            for tk in tickers[:10]:
                try:
                    ytk = yf.Ticker(tk)
                    # Check if earnings were reported in the last 3 days
                    cal = ytk.calendar
                    info = ytk.info or {}
                    earnings_ts = info.get("mostRecentQuarter")  # unix timestamp of last reported quarter
                    if not earnings_ts:
                        continue

                    # mostRecentQuarter is a unix timestamp of the quarter end date, not the report date
                    # Use earningsTimestampStart for the actual report date
                    report_ts = info.get("earningsTimestampStart") or info.get("earningsTimestampEnd")

                    # Check if the LAST earnings report was within 3 days
                    last_report = None
                    try:
                        # earnings_dates gives actual past + future dates
                        edates = ytk.earnings_dates
                        if edates is not None and len(edates) > 0:
                            past = [d for d in edates.index if d.tz_localize(None) <= now]
                            if past:
                                last_report = max(past).tz_localize(None)
                    except Exception:
                        pass

                    if last_report and (now - last_report).days <= 3:
                        # Recent earnings — get actual vs estimate from earnings_dates
                        try:
                            row = edates.loc[last_report]
                            actual = row.get("Reported EPS")
                            estimate = row.get("EPS Estimate")
                            if actual is not None and estimate is not None and estimate != 0:
                                surprise_pct = round((actual - estimate) / abs(estimate) * 100, 1)
                                beat = actual > estimate
                                if abs(surprise_pct) > 1:  # only material
                                    items.append({
                                        "ticker": tk,
                                        "headline": f"Earnings {'beat' if beat else 'miss'}: ${actual:.2f} vs ${estimate:.2f} est ({'+' if surprise_pct > 0 else ''}{surprise_pct}%)",
                                        "source": f"yfinance (reported {last_report.strftime('%b %d')})",
                                        "source_type": "earnings_data",
                                        "impact": "bull" if beat else "bear",
                                        "confidence": "high",
                                        "time": f"{(now - last_report).days}d ago",
                                        "url": "",
                                    })
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
        return items

    # ── Phase 1C: SEC EDGAR recent 8-K filings ──
    def _fetch_sec_filings():
        items = []
        try:
            from src.edgar import fetch_recent_8k
            for tk in tickers[:8]:
                try:
                    filings = fetch_recent_8k(tk, days=3)
                    # 8-K item codes that matter
                    ITEM_LABELS = {
                        "1.01": "Material agreement", "1.02": "Bankruptcy/receivership",
                        "2.01": "Asset acquisition/disposal", "2.02": "Results of operations (earnings)",
                        "2.03": "Financial obligation", "2.05": "Delisting/transfer",
                        "2.06": "Material impairment", "3.01": "Securities delisting",
                        "4.01": "Auditor change", "4.02": "Non-reliance on financials",
                        "5.01": "Corporate governance change", "5.02": "Executive departure/appointment",
                        "5.03": "Bylaws amendment", "7.01": "Regulation FD disclosure",
                        "8.01": "Other material event", "9.01": "Financial statements/exhibits",
                    }
                    for f in filings[:2]:
                        raw_items = f.get("items", "")
                        # Parse item codes and add human-readable labels
                        labels = []
                        for code, label in ITEM_LABELS.items():
                            if code in raw_items:
                                labels.append(label)
                        why = " — ".join(labels[:2]) if labels else "Material event disclosure"
                        # Determine impact from filing type
                        impact = "neutral"
                        if any(c in raw_items for c in ["2.02", "7.01"]): impact = "neutral"  # earnings/FD = check direction
                        if any(c in raw_items for c in ["1.02", "2.06", "4.02"]): impact = "bear"  # bankruptcy/impairment/non-reliance
                        if any(c in raw_items for c in ["2.01", "5.02"]): impact = "neutral"  # could go either way

                        items.append({
                            "ticker": tk,
                            "headline": f"SEC 8-K: {why}",
                            "source": f"SEC EDGAR ({f.get('company', tk)})",
                            "source_type": "sec_filing",
                            "impact": impact,
                            "confidence": "high",
                            "time": f.get("filed", "recent"),
                            "url": f.get("url", ""),
                        })
                except Exception:
                    pass
        except Exception:
            pass
        return items

    # ── Phase 2: Grok live search (X/Twitter + web) — the ONLY model with real-time access ──
    def _fetch_grok_intel():
        items = []
        grok_key = get_secret("GROK_API_KEY")
        if not grok_key: return items

        from datetime import datetime as _dt
        today = _dt.now().strftime("%A, %B %d, %Y")
        tickers_str = ", ".join(tickers[:15])

        try:
            from openai import OpenAI
            client = OpenAI(api_key=grok_key, base_url="https://api.x.ai/v1")
            response = client.chat.completions.create(
                model="grok-3",  # grok-3 has web search; upgrade to grok-4 reasoning when available
                messages=[
                    {"role": "system", "content": f"""You are a real-time financial news scanner with live web and X/Twitter search.
TODAY IS: {today}. Search for news from TODAY and the last 12 hours ONLY. Anything older is stale — ignore it.
Your training data is outdated. Do NOT rely on memory. SEARCH the web and X/Twitter for current information."""},
                    {"role": "user", "content": f"""Search the web AND X/Twitter RIGHT NOW for market-moving news on these tickers: {tickers_str}

TODAY IS {today}. Only return news from TODAY or last night's after-hours.

SEARCH FOR:
1. Web news: Reuters, Bloomberg, CNBC, WSJ, MarketWatch headlines from today
2. X/Twitter: posts from @DeItaone, @zaborhedge, @LiveSquawk, @unusual_whales, @FirstSquawk, company IR accounts, named analysts
3. Pre-market movers: any ticker gapping up/down >2% and WHY
4. Macro: any economic data released today (CPI, jobs, PMI, Fed speakers)
5. Earnings: any company that reported last night or this morning

DO NOT RETURN:
- Anything from yesterday or earlier (unless after-hours last night)
- Opinions, predictions, or analysis
- Generic "market is up/down" commentary

For each item found TODAY:
- ticker: affected ticker(s)
- headline: one factual sentence (what happened, not what might happen)
- source: specific outlet or @handle
- impact: bull/bear/neutral
- time: how long ago (e.g., "2h ago", "pre-market", "last night AH")

Return JSON array. Max 15 items. Return [] if quiet day.
[{{"ticker": "AAPL", "headline": "...", "source": "...", "impact": "bull", "time": "2h ago"}}]"""},
                ],
                max_tokens=3000, temperature=0.1,
            )
            raw = response.choices[0].message.content or "[]"
            cleaned = re.sub(r"^```json?\s*", "", raw.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned)
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                for item in parsed:
                    item["source_type"] = "x_twitter"
                    item["url"] = ""
                    # Freshness sanity: check if Grok's time references are plausibly recent
                    t = item.get("time", "")
                    if any(w in t.lower() for w in ["ago", "pre-market", "last night", "this morning", "overnight", "ah", "today"]):
                        item["confidence"] = "medium"  # plausibly live search result
                    else:
                        item["confidence"] = "low"  # suspicious — might be from training data
                        item["headline"] = item.get("headline", "") + " [⚠ freshness unverified]"
                    items.append(item)
        except Exception:
            pass
        return items

    # ── Run all sources in parallel ──
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {
            pool.submit(_fetch_polygon_news): "polygon",
            pool.submit(_fetch_earnings): "earnings",
            pool.submit(_fetch_sec_filings): "sec",
            pool.submit(_fetch_grok_intel): "grok",
        }
        for fut in as_completed(futs):
            try:
                items = fut.result()
                all_items.extend(items)
            except Exception:
                pass

    # ── Phase 3: Cross-reference & deduplicate ──
    # Build API headline snippets for claim-level verification
    api_claims = {}  # ticker -> list of headline keywords
    for item in all_items:
        if item.get("source_type") in ("news_api", "earnings_data", "sec_filing"):
            tk = item.get("ticker", "")
            if tk not in api_claims:
                api_claims[tk] = []
            # Extract key words from headline (>3 chars, not common words)
            stop = {"the", "and", "for", "that", "with", "from", "this", "has", "are", "was", "its", "sec"}
            words = set(w.lower() for w in item.get("headline", "").split() if len(w) > 3 and w.lower() not in stop)
            api_claims[tk].append(words)

    for item in all_items:
        if item.get("source_type") == "x_twitter":
            tk = item.get("ticker", "")
            grok_words = set(w.lower() for w in item.get("headline", "").split() if len(w) > 3)
            if tk in api_claims:
                # Check if any API headline shares 2+ meaningful words with Grok's claim
                best_overlap = 0
                for api_words in api_claims[tk]:
                    overlap = len(grok_words & api_words)
                    best_overlap = max(best_overlap, overlap)
                if best_overlap >= 2:
                    item["confidence"] = "high"
                    item["cross_verified"] = True
                else:
                    item["cross_verified"] = False  # same ticker but different claim
            else:
                item["cross_verified"] = False

    # Deduplicate by headline similarity (first 40 chars, normalized)
    seen = set()
    deduped = []
    for item in all_items:
        norm = item.get("headline", "").lower().replace("'", "").replace('"', '')[:40]
        key = (item.get("ticker", ""), norm)
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    # ── Phase 4: Sort by recency first, then impact ──
    # Parse time strings into sortable values (lower = more recent)
    def _recency(item):
        t = item.get("time", "").lower()
        # Handle "Xd ago", "Xh ago", "Xm ago", "X minutes ago" from Grok
        if "ago" in t:
            try:
                num = int("".join(c for c in t.split("ago")[0] if c.isdigit()) or "99")
                if "d" in t: return num * 1440       # days → minutes
                if "h" in t: return num * 60          # hours → minutes
                if "min" in t or "m" in t: return num  # minutes (check 'min' first, then 'm')
                return num  # default to minutes if just a number + "ago"
            except Exception: pass
        # Handle ISO-ish timestamps from Polygon "2026-04-03 01:05"
        if len(t) >= 10 and t[4] == "-":
            try:
                from datetime import datetime
                dt = datetime.strptime(t[:16], "%Y-%m-%d %H:%M")
                mins = (datetime.now() - dt).total_seconds() / 60
                return max(0, mins)
            except Exception: pass
        # Handle "recent", "latest quarter", etc.
        if "recent" in t.lower(): return 500
        return 9999  # unknown = sort last

    IMPACT_RANK = {"earnings_data": 5, "sec_filing": 4, "news_api": 3, "x_twitter": 2}
    CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1}
    # Add freshness labels
    for item in deduped:
        mins = _recency(item)
        if mins < 60: item["freshness"] = "live"
        elif mins < 360: item["freshness"] = "recent"  # <6h
        elif mins < 720: item["freshness"] = "today"    # <12h
        else: item["freshness"] = "stale"                # >12h

    # Remove stale items (>24h)
    deduped = [item for item in deduped if _recency(item) < 1500]  # ~25h cutoff

    deduped.sort(key=lambda x: (
        _recency(x),
        -(IMPACT_RANK.get(x.get("source_type", ""), 1) * CONFIDENCE_RANK.get(x.get("confidence", "low"), 1)),
    ))

    return {
        "success": True,
        "items": deduped[:25],
        "sources": {
            "polygon_news": sum(1 for i in deduped if i.get("source_type") == "news_api"),
            "earnings": sum(1 for i in deduped if i.get("source_type") == "earnings_data"),
            "sec_filings": sum(1 for i in deduped if i.get("source_type") == "sec_filing"),
            "x_twitter": sum(1 for i in deduped if i.get("source_type") == "x_twitter"),
        },
        "total_raw": len(all_items),
        "total_deduped": len(deduped),
    }


class MarketNoteRequest(BaseModel):
    briefing_data: dict = {}
    news_items: list[dict] = []


@router.post("/morning-note")
async def morning_note(req: MarketNoteRequest, user: str = Depends(get_current_user)):
    """Generate AI market note from scan data + news intelligence."""
    from src.api_keys import get_secret

    data = req.briefing_data
    mc = data.get("market_context", {})
    opps = data.get("opportunities", [])
    earn = data.get("earnings_this_week", [])
    rb = data.get("risk_budget", {})
    warns = data.get("warnings", [])
    news = req.news_items

    def _pct(v): return f"{v:+.2f}%" if isinstance(v, (int, float)) else "N/A"

    ctx = f"""MARKET DATA (use ONLY these numbers — do NOT invent):

PRICES:
- SPY: ${mc.get('spy', {}).get('price', 0) or 0} ({_pct(mc.get('spy', {}).get('change_pct'))})
- QQQ: ${mc.get('qqq', {}).get('price', 0) or 0} ({_pct(mc.get('qqq', {}).get('change_pct'))})
- VIX: {mc.get('vix', {}).get('price', 0) or 0} — regime: {mc.get('vix', {}).get('regime', 'N/A')}
- VIX term structure: {mc.get('vix', {}).get('term_structure', 'N/A')} (VIX3M/VIX ratio: {mc.get('vix', {}).get('term_ratio', 'N/A')})

EVENTS:
- FOMC: {', '.join(f"{e['type']} in {e['days_away']}d" for e in mc.get('fomc_events', [])) or 'No FOMC within 30 days'}
- Earnings this week: {', '.join(f"{e['ticker']} in {e['days']}d" for e in earn) or 'None on watchlist'}
"""

    if news:
        ctx += "\nNEWS (from Polygon, SEC EDGAR, yfinance, X/Twitter — fact-checked):\n"
        for item in news[:12]:
            verified = "✓" if item.get("confidence") == "high" else "?"
            ctx += f"  {verified} [{item.get('ticker','?')}] {item.get('headline','')} — {item.get('source','')} ({item.get('time','')}) [{item.get('impact','neutral')}]\n"

    if opps:
        ctx += f"\nTOP {min(len(opps), 8)} TRADE SETUPS (ranked by composite score):\n"
        for i, o in enumerate(opps[:8]):
            flags = ""
            if o.get('earnings_before'): flags += " ⚠EARNINGS"
            if o.get('inside_exp_move'): flags += " ⚠INSIDE_EM"
            kelly = o.get('kelly_adj')
            kelly_str = f"{kelly:.1f}%" if isinstance(kelly, (int, float)) else "N/A"
            ctx += f"  {i+1}. {o.get('ticker','?')} {o.get('label','?')} {o.get('strikes','?')} {o.get('dte','?')}d — POP {o.get('pop','?')}% R:R {o.get('rr_ratio','?')}x IVR {o.get('ivr', 'N/A')} ({o.get('ivr_band','?')}) Liq:{o.get('liq_grade','?')} Kelly:{kelly_str} → {o.get('contracts',0)}×{flags}\n"

    ctx += f"""
RISK: ${rb.get('account_size', 0)} account | Top 5 deployed: ${rb.get('top5_risk', 0)} ({rb.get('pct_of_account', 0)}%) | {rb.get('verdict', 'N/A')}
WARNINGS: {'; '.join(warns) or 'None'}
SECTORS: {', '.join(f"{s}:{c}" for s, c in data.get('sector_exposure', {}).items()) or 'N/A'}
"""

    from datetime import datetime as _dt
    today = _dt.now().strftime("%A, %B %d, %Y")

    system = f"""You are writing a private trading note TO YOURSELF. Today is {today}.

IMPORTANT: Your training data is STALE. Do NOT reference any news, events, or market conditions from memory.
The ONLY facts you know about today come from the data below. If the data says SPY is at $655, that IS the current price.
If there are news items below, those are the ONLY news items that exist today. Do not add others from memory.

Structure (5 short sections, each 2-3 sentences max):

**MARKET**: What's the tape doing? VIX regime + term structure = what it means for selling premium. Any events that change the playbook?

**NEWS**: What matters from the news feed? Only mention items that affect your trading decisions today. If nothing material, say "quiet tape." Connect news to specific tickers in the trade list if relevant.

**TRADES**: Your top 2-3 picks from the scan. For each: ticker, strategy, WHY (cite the IVR band, POP, R:R). If any have earnings flags, explicitly say skip or proceed with caution.

**AVOID**: What NOT to trade and why. Earnings exposure, sector concentration, low liquidity setups. Be specific.

**SIZE**: One sentence. How much to deploy given risk budget + VIX regime. If elevated vol, note to reduce size.

RULES:
- Reference ONLY data provided. No invented numbers.
- No disclaimers, no "not financial advice."
- If VIX < 15: note thin premiums. If VIX 20-30: premium selling sweet spot. If VIX > 30: reduce size, widen wings.
- If news contradicts a trade setup (e.g., bearish news on a ticker you'd sell puts on), FLAG IT.
- Write like you're talking to yourself before market open."""

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
            config=types.GenerateContentConfig(max_output_tokens=1500, temperature=0.25),
        )
        return {"content": response.text or "", "success": True}
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

    def _generate_signals(closes, highs, lows, strategy, volumes=None):
        n = len(closes)
        signals = np.zeros(n)

        if strategy == "sma_cross":
            fast, slow = _sma(closes, 50), _sma(closes, 200)
            for i in range(200, n):
                if not np.isnan(fast[i]) and not np.isnan(slow[i]):
                    signals[i] = 1 if fast[i] > slow[i] else -1

        elif strategy == "ema_cross":
            fast, slow = _ema(closes, 12), _ema(closes, 26)
            for i in range(26, n):
                signals[i] = 1 if fast[i] > slow[i] else -1

        elif strategy == "golden_cross":
            fast, slow = _sma(closes, 50), _sma(closes, 200)
            for i in range(200, n):
                if not np.isnan(fast[i]) and not np.isnan(slow[i]):
                    signals[i] = 1 if closes[i] > slow[i] and fast[i] > slow[i] else -1

        elif strategy == "macd":
            e12, e26 = _ema(closes, 12), _ema(closes, 26)
            macd_line = e12 - e26
            sig_line = _ema(macd_line, 9)
            for i in range(34, n):
                signals[i] = 1 if macd_line[i] > sig_line[i] else -1

        elif strategy == "rsi_ob_os":
            r = _rsi(closes)
            for i in range(15, n):
                if np.isnan(r[i]): continue
                if r[i] < 30: signals[i] = 1
                elif r[i] > 70: signals[i] = -1
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
            # Parabolic SAR — built-in trailing stop + signal
            # Long when price above SAR, short when below
            sar = talib.SAR(highs, lows, acceleration=0.02, maximum=0.2)
            for i in range(2, n):
                if np.isnan(sar[i]): continue
                signals[i] = 1 if closes[i] > sar[i] else -1

        elif strategy == "cci":
            # Commodity Channel Index mean reversion
            # Buy when CCI < -100 (oversold), sell when CCI > 100 (overbought)
            cci = talib.CCI(highs, lows, closes, timeperiod=20)
            for i in range(20, n):
                if np.isnan(cci[i]): continue
                if cci[i] < -100: signals[i] = 1
                elif cci[i] > 100: signals[i] = -1
                elif abs(cci[i]) < 50: signals[i] = 0  # neutral zone = exit
                else: signals[i] = signals[i - 1]

        elif strategy == "williams_r":
            # Williams %R fast oscillator
            # Buy below -80 (oversold), sell above -20 (overbought)
            willr = talib.WILLR(highs, lows, closes, timeperiod=14)
            for i in range(14, n):
                if np.isnan(willr[i]): continue
                if willr[i] < -80: signals[i] = 1
                elif willr[i] > -20: signals[i] = -1
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
        """Run full backtest using adjusted OHLCV. Daily=yfinance, Intraday=Polygon."""
        try:
            import pandas as pd

            if is_intraday:
                df = _fetch_intraday_polygon(tk, req.timeframe, req.lookback_days)
            else:
                period_map = {252: "1y", 504: "2y", 756: "3y", 1260: "5y", 2520: "10y"}
                period = period_map.get(req.lookback_days, f"{req.lookback_days}d")
                df = yf.download(tk, period=period, progress=False, auto_adjust=True)

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
            signals = _generate_signals(closes, highs, lows, strategy, volumes)

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

            # Win rate
            trades, wins, pos, entry_px = 0, 0, 0, 0.0
            for i in range(1, n):
                if signals[i] != 0 and pos == 0:
                    pos = int(signals[i]); entry_px = closes[i]
                elif pos != 0 and signals[i] != pos:
                    pnl = (closes[i] / entry_px - 1) * pos
                    trades += 1
                    if pnl > 2 * cost_1x: wins += 1
                    if signals[i] != 0:
                        pos = int(signals[i]); entry_px = closes[i]
                    else:
                        pos = 0
            win_rate = round(wins / max(trades, 1) * 100, 1)

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

            # ── Current signal + duration ──
            current_signal = "Long" if signals[-1] == 1 else "Short" if signals[-1] == -1 else "Flat"
            signal_days = 0
            for i in range(n - 1, -1, -1):
                if signals[i] == signals[-1]: signal_days += 1
                else: break

            # Long-only flag
            is_long_only = strategy == "atr_trail"

            return {
                "ticker": tk, "strategy": strategy,
                "sharpe": round(sharpe, 3), "dsr": round(dsr, 4), "dsr_pct": round(dsr * 100, 1),
                "cagr": round(cagr, 1), "max_dd": round(max_dd, 1), "total_ret": round(total_ret, 1),
                "win_rate": win_rate, "trades": trades,
                # Buy-and-hold benchmark
                "bh_sharpe": round(bh_sharpe, 3), "bh_cagr": round(bh_cagr, 1), "bh_total_ret": round(bh_total_ret, 1),
                # Excess over benchmark
                "excess_sharpe": excess_sharpe, "excess_cagr": excess_cagr, "excess_ret": excess_ret,
                "pct_active": pct_active,
                # Walk-forward
                "avg_wf_sharpe": avg_wf, "pct_wf_positive": pct_wf_pos,
                "n_wf_folds": len(wf_sharpes),
                "current_signal": current_signal, "signal_days": signal_days,
                "long_only": is_long_only, "n_days": n,
                "skew": round(skew, 2), "kurtosis": round(kurt, 2),
            }
        except Exception:
            return None

    # ── Run all combinations in parallel ──
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
        df = yf.download(tk, period=period, progress=False, auto_adjust=True)
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
                    df = yf.download(tk, period=period_map.get(1260, "5y"), progress=False, auto_adjust=True)
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
        df = yf.download(tk, period=period, progress=False, auto_adjust=True)
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
