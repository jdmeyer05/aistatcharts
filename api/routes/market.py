"""Market data endpoints — prices, snapshots, options chains."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from api.deps import get_current_user, require_admin

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


@router.get("/ohlcv/{ticker}")
async def ohlcv_history(
    ticker: str,
    days: int = Query(365, ge=1, le=5040),
    interval: str = Query("1d", description="Bar interval: 1m, 5m, 15m, 1h, 1d"),
    user: str = Depends(get_current_user),
):
    """Get OHLCV candlestick data from Polygon. Supports intraday (1m/5m/15m/1h) and daily."""
    import requests
    from datetime import date, datetime, timedelta
    from src.api_keys import get_secret
    from src.data_engine import format_massive_ticker

    api_key = get_secret("MASSIVE_API_KEY")
    if not api_key:
        return {"ticker": ticker, "data": []}

    formatted = format_massive_ticker(ticker.upper())
    end = date.today()

    # Map interval to Polygon multiplier/timespan + sensible day limits
    _INTERVALS = {
        "1m":  (1, "minute", min(days, 1)),     # max 1 day of 1min bars
        "5m":  (5, "minute", min(days, 5)),     # max 5 days
        "15m": (15, "minute", min(days, 10)),   # max 10 days
        "1h":  (1, "hour", min(days, 30)),      # max 30 days
        "1d":  (1, "day", days),
    }
    mult, timespan, effective_days = _INTERVALS.get(interval, (1, "day", days))
    start = end - timedelta(days=effective_days)

    try:
        url = f"https://api.polygon.io/v2/aggs/ticker/{formatted}/range/{mult}/{timespan}/{start.isoformat()}/{end.isoformat()}"
        r = requests.get(url, params={"apiKey": api_key, "sort": "asc", "limit": 50000, "adjusted": "true"}, timeout=15)
        r.raise_for_status()
        results = r.json().get("results", [])
        bars = []
        for bar in results:
            bars.append({
                "time": int(bar["t"] / 1000),
                "open": bar.get("o", 0),
                "high": bar.get("h", 0),
                "low": bar.get("l", 0),
                "close": bar.get("c", 0),
                "volume": bar.get("v", 0),
            })

        # Compute TA-Lib indicators
        indicators = {}
        try:
            import numpy as np
            import talib
            closes = np.array([b["close"] for b in bars], dtype=float)
            highs = np.array([b["high"] for b in bars], dtype=float)
            lows = np.array([b["low"] for b in bars], dtype=float)
            times = [b["time"] for b in bars]

            def _series(values):
                """Convert numpy array to [{time, value}] with NaN filtered."""
                return [{"time": t, "value": round(float(v), 4)}
                        for t, v in zip(times, values) if not np.isnan(v)]

            # EMAs
            indicators["ema9"] = _series(talib.EMA(closes, timeperiod=9))
            indicators["ema21"] = _series(talib.EMA(closes, timeperiod=21))
            indicators["ema50"] = _series(talib.EMA(closes, timeperiod=50))
            indicators["ema200"] = _series(talib.EMA(closes, timeperiod=200))

            # RSI
            indicators["rsi"] = _series(talib.RSI(closes, timeperiod=14))

            # MACD
            macd, signal, hist = talib.MACD(closes, fastperiod=12, slowperiod=26, signalperiod=9)
            indicators["macd"] = _series(macd)
            indicators["macd_signal"] = _series(signal)
            indicators["macd_hist"] = _series(hist)

            # Bollinger Bands
            upper, middle, lower = talib.BBANDS(closes, timeperiod=20, nbdevup=2, nbdevdn=2)
            indicators["bb_upper"] = _series(upper)
            indicators["bb_middle"] = _series(middle)
            indicators["bb_lower"] = _series(lower)

            # VWAP approximation (cumulative typical price × volume / cumulative volume)
            typical = (highs + lows + closes) / 3
            volumes = np.array([b["volume"] for b in bars], dtype=float)
            cum_tpv = np.cumsum(typical * volumes)
            cum_vol = np.cumsum(volumes)
            vwap = np.where(cum_vol > 0, cum_tpv / cum_vol, np.nan)
            indicators["vwap"] = _series(vwap)

        except ImportError:
            pass  # TA-Lib not installed — return bars without indicators
        except Exception:
            pass

        return {"ticker": ticker, "data": bars, "indicators": indicators}
    except Exception as e:
        # Fallback 1: yfinance OHLCV (real candles, free, no API key)
        try:
            import yfinance as yf
            period_map = {1: "5d", 5: "5d", 10: "1mo", 30: "1mo", 90: "3mo",
                          180: "6mo", 365: "1y", 730: "2y", 1825: "5y"}
            yf_period = period_map.get(days, "1y")
            tk = yf.Ticker(ticker.upper())
            df = tk.history(period=yf_period, interval="1d" if interval == "1d" else interval.replace("m", "m").replace("h", "h"))
            if df is not None and not df.empty:
                bars = []
                for dt, row in df.iterrows():
                    ts = int(dt.timestamp())
                    bars.append({
                        "time": ts, "open": float(row["Open"]), "high": float(row["High"]),
                        "low": float(row["Low"]), "close": float(row["Close"]),
                        "volume": float(row.get("Volume", 0)),
                    })
                return {"ticker": ticker, "data": bars}
        except Exception:
            pass

        # Fallback 2: close-only from Supabase cache
        from src.data_engine import fetch_massive_data
        df = fetch_massive_data(ticker.upper(), days)
        if df is not None and not df.empty:
            df = df.reset_index()
            bars = []
            for _, row in df.iterrows():
                dt = row.iloc[0]
                c = float(row["Close"])
                ts = int(dt.timestamp()) if hasattr(dt, 'timestamp') else 0
                bars.append({"time": ts, "open": c, "high": c, "low": c, "close": c, "volume": 0})
            return {"ticker": ticker, "data": bars}
        return {"ticker": ticker, "data": [], "error": str(e)}


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


@router.get("/peers/{ticker}")
async def peer_comparison(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Return a peer-comparison row set for a ticker.
    Uses Polygon's related-companies endpoint + ticker details + batch snapshot.
    """
    from src.data_engine import fetch_related_companies, polygon_batch_snapshot, polygon_ticker_details
    ticker = ticker.upper()

    try:
        peers = fetch_related_companies(ticker) or []
    except Exception:
        peers = []

    candidates = [ticker] + [p for p in peers[:5] if p and p != ticker]
    if len(candidates) < 2:
        return {"ticker": ticker, "peers": []}

    snaps = polygon_batch_snapshot(candidates) or {}
    rows = []
    for tk in candidates:
        try:
            details = polygon_ticker_details(tk) or {}
            snap = snaps.get(tk) or {}
            price = snap.get("price")
            rows.append({
                "ticker": tk,
                "price": float(price) if price else None,
                "change": float(snap.get("change") or 0),
                "market_cap": details.get("marketCap") or details.get("market_cap"),
                "pe": details.get("trailingPE") or details.get("forwardPE"),
                "pb": details.get("priceToBook"),
                "revenue_growth": details.get("revenueGrowth"),
                "profit_margin": details.get("profitMargins"),
                "is_target": tk == ticker,
            })
        except Exception:
            continue
    return {"ticker": ticker, "peers": rows}


@router.get("/macro-dashboard")
async def macro_dashboard(user: str = Depends(get_current_user)):
    """Fetch the FRED macro dashboard — Fed Funds, 10Y, 2Y, yield curve, WTI, nat gas, UNRATE, CPI, GDP."""
    from src.market_data import fetch_fred_macro_dashboard, FRED_SERIES
    import numpy as np
    import pandas as pd
    import math

    bundle = fetch_fred_macro_dashboard()
    if not bundle:
        return {"series": {}, "latest": {}, "labels": FRED_SERIES}

    def _rows(df):
        if df is None or df.empty:
            return []
        out = []
        for _, r in df.iterrows():
            d = r["date"]
            v = r["value"]
            if isinstance(v, (np.floating, float)):
                f = float(v)
                if math.isnan(f) or math.isinf(f):
                    continue
                v = f
            out.append({"date": d.strftime("%Y-%m-%d") if isinstance(d, pd.Timestamp) else str(d), "value": v})
        return out

    series_out = {sid: _rows(df) for sid, df in bundle.items()}
    latest = {}
    for sid, rows in series_out.items():
        if rows:
            latest[sid] = rows[-1]["value"]
    return {"series": series_out, "latest": latest, "labels": FRED_SERIES}


@router.get("/analyst-estimates/{ticker}")
async def analyst_estimates(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Yahoo Finance analyst consensus estimates, price targets, and key stats."""
    from src.market_data import fetch_analyst_estimates
    data = fetch_analyst_estimates(ticker.upper())
    return {"ticker": ticker.upper(), "data": data or {}}


@router.get("/earnings-history/{ticker}")
async def earnings_history(
    ticker: str,
    user: str = Depends(get_current_user),
):
    """Yahoo Finance earnings surprise history (actual vs estimate)."""
    import numpy as np
    import pandas as pd
    import math
    from src.market_data import fetch_earnings_history
    df = fetch_earnings_history(ticker.upper())
    if df is None or df.empty:
        return {"ticker": ticker.upper(), "data": []}
    rows = []
    for _, r in df.iterrows():
        rec = {}
        for col in df.columns:
            val = r[col]
            if isinstance(val, pd.Timestamp):
                rec[col] = val.strftime("%Y-%m-%d")
            elif isinstance(val, (np.floating, float)):
                f = float(val)
                rec[col] = None if (math.isnan(f) or math.isinf(f)) else f
            elif isinstance(val, (np.integer, int)):
                rec[col] = int(val)
            else:
                rec[col] = str(val) if val is not None else None
        rows.append(rec)
    return {"ticker": ticker.upper(), "data": rows}


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
        "name": "Claude Opus",
        "base_url": "anthropic",
        "model": "claude-opus-4-6",
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
        # Strip Grok citation tags and web references before JSON parsing
        cleaned = re.sub(r'<grok:render[^>]*>.*?</grok:render>', '', cleaned)
        cleaned = re.sub(r'\[web:\d+\]', '', cleaned)
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
    """Pass 1: Fast search via grok-4-1-fast-reasoning. Cached 30 min in Supabase."""
    import json, re, logging
    from src.api_keys import get_secret

    _log = logging.getLogger(__name__)

    from datetime import datetime as _dt
    tickers = [t.strip().upper() for t in req.watchlist[:20] if t.strip()]
    today = _dt.utcnow().strftime("%A, %B %d, %Y")
    tickers_str = ", ".join(tickers[:15])

    # Check Supabase cache (30 min TTL)
    try:
        from src.ai_cache import get_cached_ai, cache_ai_response
        import hashlib
        # 30-min bucket: floor minutes to 0 or 30
        _now = _dt.now()
        _bucket = _now.strftime('%Y%m%d_%H') + ("00" if _now.minute < 30 else "30")
        cache_key = hashlib.md5(f"news_search:{_bucket}:{tickers_str}".encode()).hexdigest()
        cached = get_cached_ai(cache_key)
        if cached:
            cached_data = json.loads(cached)
            _log.info(f"News search cache hit: {len(cached_data.get('items', []))} items")
            return cached_data
    except Exception:
        pass

    grok_key = get_secret("GROK_API_KEY")
    if not grok_key:
        return {"success": False, "error": "GROK_API_KEY not configured", "items": []}

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

    for item in items:
        item["confidence"] = "unverified"
        item["verification_note"] = ""

    items = _finalize(items)
    result = {"success": True, "items": items[:40], "sources": _make_sources(items), "total": len(items)}

    # Cache in Supabase for 30 min
    try:
        cache_ai_response(cache_key, json.dumps(result), model="grok-4-1-fast",
                          source_page="news_search", ticker="NEWS",
                          ttl_hours=0.5, prompt_summary="News search results")
    except Exception:
        pass

    return result


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

            # Delayed entry analysis: how does Sharpe change if you enter 1-5 days late?
            delay_sharpes = {}
            for delay in [0, 1, 2, 3, 5]:
                d_pnls = []
                # Reconstruct trades from signal flips
                d_pos = 0
                d_entry_i = 0
                for i in range(1, n):
                    if signals[i] != 0 and d_pos == 0:
                        d_pos = int(signals[i]); d_entry_i = i
                    elif d_pos != 0 and signals[i] != d_pos:
                        delayed_i = d_entry_i + delay
                        if delayed_i < i:
                            pnl = (closes[i] / closes[delayed_i] - 1) * d_pos
                            d_pnls.append(pnl)
                        if signals[i] != 0:
                            d_pos = int(signals[i]); d_entry_i = i
                        else:
                            d_pos = 0
                if len(d_pnls) >= 5:
                    d_arr = np.array(d_pnls)
                    d_std = float(np.std(d_arr, ddof=1))
                    d_sharpe = float(np.mean(d_arr)) / d_std * ann if d_std > 0 else 0
                    delay_sharpes[delay] = round(d_sharpe, 2)

            # Determine entry urgency from delay analysis
            entry_urgency = "neutral"
            if len(delay_sharpes) >= 3:
                s0 = delay_sharpes.get(0, 0)
                s2 = delay_sharpes.get(2, 0)
                s5 = delay_sharpes.get(5, 0)
                if s0 <= 0:
                    entry_urgency = "neutral"  # no edge to begin with
                elif s2 < s0 * 0.5:
                    entry_urgency = "urgent"  # edge halves by day 2
                elif s2 > s0:
                    entry_urgency = "wait"  # improves with delay
                elif s5 > s0 * 0.5:
                    entry_urgency = "patient"  # edge persists through day 5
                else:
                    entry_urgency = "urgent"  # decays but not as fast

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
                "entry_urgency": entry_urgency,
                "delay_sharpes": delay_sharpes,
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
        # Strip Grok citation tags from news
        import re as _re
        cleaned_news = _re.sub(r'\[web:\d+\]', '', req.news_summary)
        cleaned_news = _re.sub(r'<grok:render[^>]*>.*?</grok:render>', '', cleaned_news)
        ctx += f"TODAY'S NEWS (from live search):\n{cleaned_news}\n\n"

    # Add macro context
    try:
        import yfinance as yf
        macro_parts = []
        for sym, label in [("SPY", "SPY"), ("^VIX", "VIX")]:
            hist = yf.Ticker(sym).history(period="5d")
            if hist is not None and len(hist) >= 2:
                p = float(hist["Close"].iloc[-1])
                prev = float(hist["Close"].iloc[-2])
                chg = (p / prev - 1) * 100
                macro_parts.append(f"{label}: {'$' if sym != '^VIX' else ''}{p:.2f} ({chg:+.1f}%)")
        if macro_parts:
            ctx += f"MARKET: {' | '.join(macro_parts)}\n\n"
    except Exception: pass

    from datetime import datetime as _dt
    today = _dt.now().strftime("%A, %B %d, %Y")
    n_ideas = len(req.ideas)

    system = f"""You are a quantitative trading analyst. Today is {today}.

These trade ideas have ALREADY been filtered for positive expected value, 2+ family confirmation, and R:R >= 1.0. They passed the filters — your job is to add context, not second-guess the math.

For EACH of the {n_ideas} trade ideas, write a 2-3 sentence analysis:
1. WHY: Connect the technical signal to today's macro environment and news. Does the news support or contradict the direction? (e.g., "GLD long aligns with Iran escalation driving gold demand")
2. RISK: What specific event or condition could invalidate this trade? Reference earnings dates, news, or portfolio overlap.
3. ACTION: Which options structure to use, citing the IV vs RV relationship. If IV > RV, sell premium. If IV < RV, buy options. If IV/RV data is not available, recommend stock or ATM options and state "vol data unavailable." Be specific.

RULES:
- COMPLETE ALL {n_ideas} IDEAS. Do not stop early or truncate any analysis.
- Do NOT invent data. Only reference numbers from the data below. If a field is missing, say "not available" — do not guess.
- Do NOT say "skip" — these ideas passed the quantitative filters.
- Connect each idea to a specific news headline if relevant.
- If the trader holds an existing position in this ticker, note the overlap.
- One section per idea. Use the ticker + direction as a header (## TICKER DIRECTION)."""

    try:
        api_key = get_secret("GEMINI_API_KEY")
        if not api_key:
            return {"success": False, "error": "Gemini API key not configured"}

        # Use Claude Sonnet for reliability + speed (Gemini thinking models can be slow)
        import anthropic
        claude = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))
        max_tokens = max(2000, n_ideas * 250 + 500)
        response = claude.messages.create(
            model="claude-opus-4-6",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": ctx}],
        )
        text = response.content[0].text.strip() if response.content else ""
        # Strip citation tags
        import re as _re2
        text = _re2.sub(r'\[web:\d+\]', '', text)
        return {"success": True, "analysis": text.strip()}
    except Exception as e:
        return {"success": False, "error": str(e)}


class TradeIdeaQuickRequest(BaseModel):
    ticker: str
    direction: str = "long"
    trigger: str = ""
    signal_days: int = 0
    confluence: int = 0
    total_families: int = 4
    price: float = 0
    stop: float = 0
    target: float = 0
    rr: float = 0
    ev: float = 0
    win_rate: float = 0
    iv: float = 0
    rv: float = 0
    rsi: float = 50
    warnings: list[str] = []
    book_summary: str = ""


@router.post("/trade-idea-quick")
async def trade_idea_quick(req: TradeIdeaQuickRequest, user: str = Depends(get_current_user)):
    """Fast per-idea AI verdict: ENTER / WAIT / SKIP with 2-3 sentence reasoning."""
    from src.api_keys import get_secret
    import anthropic

    ctx = (
        f"TRADE IDEA: {req.ticker} {req.direction.upper()}\n"
        f"  Trigger: {req.trigger} flipped {req.signal_days}d ago | Win rate: {req.win_rate}%\n"
        f"  Confluence: {req.confluence}/{req.total_families} families confirm\n"
        f"  Price: ${req.price:.2f} | Stop: ${req.stop:.2f} | Target: ${req.target:.2f}\n"
        f"  R:R: {req.rr:.1f}:1 | EV: ${req.ev:+.2f}/trade\n"
        f"  RSI: {req.rsi:.0f}"
    )
    if req.iv > 0:
        ctx += f" | IV: {req.iv:.1f}%"
    if req.rv > 0:
        ctx += f" | RV20: {req.rv:.1f}%"
    if req.iv > 0 and req.rv > 0:
        edge = "sell premium" if req.iv > req.rv * 1.05 else "buy options" if req.rv > req.iv * 1.05 else "no vol edge"
        ctx += f" ({edge})"
    if req.warnings:
        ctx += f"\n  Warnings: {'; '.join(req.warnings)}"
    if req.book_summary:
        ctx += f"\n  Portfolio: {req.book_summary}"

    try:
        api_key = get_secret("ANTHROPIC_API_KEY")
        if not api_key:
            return {"success": False, "error": "API key not configured"}

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=250,
            system=(
                "You are a quant trader. Give a clear VERDICT: ENTER, WAIT, or SKIP. "
                "Then 2-3 sentences WHY referencing the data. "
                "If IV > RV, say sell premium (put credit spread for long, call credit spread for short). "
                "If RV > IV, say buy options. If no vol data, say stock or ATM spread. "
                "Be terse and specific. No preamble."
            ),
            messages=[{"role": "user", "content": ctx}],
        )
        text = response.content[0].text.strip()
        verdict = "WAIT"
        for v in ["ENTER", "SKIP", "WAIT"]:
            if v in text[:50].upper():
                verdict = v
                break
        return {"success": True, "ticker": req.ticker, "verdict": verdict, "analysis": text}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}


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


@router.get("/econ-calendar-releases")
async def econ_calendar_releases(user: str = Depends(get_current_user)):
    """FRED release dates for major macro indicators + upcoming FOMC decisions.

    Returns a merged list of events across the next ~3 months, each with
    name, date, impact (High/Medium), category, and source series_id.
    """
    import requests
    from datetime import date
    from src.api_keys import get_secret
    from src.economic_calendar import FOMC_DATES

    key = get_secret("FRED_API_KEY")
    if not key:
        return {"events": []}

    FRED_RELEASES = {
        10: ("CPI", "CPIAUCSL", "High", "Inflation"),
        50: ("Nonfarm Payrolls (NFP)", "PAYEMS", "High", "Employment"),
        53: ("GDP", "GDP", "High", "Growth"),
        21: ("FOMC Minutes/Data Release", "FEDFUNDS", "High", "Fed"),
        9:  ("Retail Sales", "RSAFS", "High", "Consumer"),
        46: ("PPI", "PPIFIS", "High", "Inflation"),
        29: ("PCE Price Index", "PCEPI", "High", "Inflation"),
        61: ("ISM Manufacturing", "MANEMP", "High", "Production"),
        13: ("Industrial Production", "INDPRO", "Medium", "Production"),
        18: ("Housing Starts", "HOUST", "Medium", "Housing"),
        11: ("Employment Cost Index", "ECI", "Medium", "Employment"),
        327: ("Consumer Sentiment (UMich)", "UMCSENT", "Medium", "Consumer"),
        22: ("Existing Home Sales", "EXHOSLUSM495S", "Medium", "Housing"),
        86: ("New Home Sales", "HSN1F", "Medium", "Housing"),
        15: ("Durable Goods Orders", "DGORDER", "Medium", "Production"),
        65: ("Initial Jobless Claims", "ICSA", "Medium", "Employment"),
        20: ("Trade Balance", "BOPGSTB", "Medium", "Trade"),
        31: ("Personal Income", "PI", "Medium", "Consumer"),
        14: ("Capacity Utilization", "TCU", "Medium", "Production"),
        17: ("Building Permits", "PERMIT", "Medium", "Housing"),
        83: ("Consumer Confidence (CB)", "CSCICP03USM665S", "Medium", "Consumer"),
    }
    today_str = date.today().strftime("%Y-%m-%d")
    out: list[dict] = []
    for rid, (name, series, impact, category) in FRED_RELEASES.items():
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/release/dates",
                params={
                    "release_id": rid, "api_key": key, "file_type": "json",
                    "sort_order": "asc", "include_release_dates_with_no_data": "true",
                    "realtime_start": today_str, "limit": 3,
                },
                timeout=10,
            )
            for d in r.json().get("release_dates", []):
                out.append({"date": d["date"], "event": name, "impact": impact,
                            "category": category, "series": series})
        except Exception as e:
            logger.warning("FRED release fetch failed for %s: %s", name, e)

    # Inject FOMC decision dates
    for fomc_date in FOMC_DATES:
        if fomc_date >= today_str:
            out.append({"date": fomc_date, "event": "FOMC Rate Decision",
                        "impact": "High", "category": "Fed", "series": "FEDFUNDS"})

    return {"events": sorted(out, key=lambda e: e["date"])}


@router.get("/earnings-calendar")
async def earnings_calendar(
    from_date: str = Query(..., alias="from", description="YYYY-MM-DD"),
    to_date: str = Query(..., alias="to", description="YYYY-MM-DD"),
    user: str = Depends(get_current_user),
):
    """Upcoming earnings reports via Finnhub. Pass-through with light normalization."""
    import requests
    from src.api_keys import get_secret

    key = get_secret("FINNHUB_API_KEY")
    if not key:
        return {"earnings": []}
    try:
        r = requests.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": from_date, "to": to_date, "token": key},
            timeout=15,
        )
        data = r.json().get("earningsCalendar", [])
        return {"earnings": data}
    except Exception as e:
        logger.error("Finnhub earnings fetch failed: %s", e)
        return {"earnings": []}


@router.get("/treasury-auctions")
async def treasury_auctions(user: str = Depends(get_current_user)):
    """Upcoming Treasury auctions via Treasury's fiscal data API."""
    import requests
    try:
        r = requests.get(
            "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/od/upcoming_auctions",
            params={"sort": "auction_date", "page[size]": 50},
            timeout=15,
        )
        return {"auctions": r.json().get("data", [])}
    except Exception as e:
        logger.error("Treasury auction fetch failed: %s", e)
        return {"auctions": []}


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


# ── News hover analysis (lightweight, cached) ──

class NewsAnalyzeRequest(BaseModel):
    headline: str
    ticker: str = ""
    source: str = ""
    impact: str = ""

@router.post("/news-analyze")
async def news_analyze(req: NewsAnalyzeRequest, user: str = Depends(get_current_user)):
    """Quick 1-2 sentence AI analysis of a news headline. Cached by headline hash."""
    import hashlib
    cache_key = hashlib.md5(f"news_hover:{req.headline[:200]}".encode()).hexdigest()

    # Check cache first
    try:
        from src.ai_cache import get_cached_ai, cache_ai_response
        cached = get_cached_ai(cache_key)
        if cached:
            return {"analysis": cached, "cached": True}
    except Exception:
        pass

    # Call Gemini Flash (fast + cheap)
    try:
        from src.api_keys import get_secret
        key = get_secret("GEMINI_API_KEY")
        if not key:
            return {"analysis": "AI analysis unavailable — no API key.", "cached": False}

        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        prompt = (
            f"You are a senior trader's assistant. In 2 concise sentences, analyze the market impact of this headline.\n\n"
            f"Headline: {req.headline}\n"
            f"{'Ticker: ' + req.ticker if req.ticker else ''}\n"
            f"{'Source: ' + req.source if req.source else ''}\n"
            f"{'Sentiment: ' + req.impact if req.impact else ''}\n\n"
            f"Cover: (1) what it means for the stock/sector, (2) whether to act or ignore. "
            f"Be direct — no hedging, no 'it remains to be seen'."
        )
        resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(max_output_tokens=200, temperature=0.3),
        )
        analysis = resp.text.strip() if resp.text else "Analysis unavailable."

        # Cache for 2 hours
        try:
            cache_ai_response(cache_key, analysis, model="gemini-2.5-flash",
                              source_page="news_hover", ticker=req.ticker or "NEWS",
                              ttl_hours=2, prompt_summary="News hover analysis")
        except Exception:
            pass

        return {"analysis": analysis, "cached": False}
    except Exception as e:
        return {"analysis": f"Analysis failed: {str(e)[:100]}", "cached": False}


# ── Holding Deep Dive — focused position analysis ──

class HoldingDiveRequest(BaseModel):
    ticker: str
    qty: float = 0
    avg_cost: float = 0
    current_price: float = 0
    market_value: float = 0
    pl: float = 0
    pl_pct: float = 0
    entry_date: str = ""

@router.post("/holding-deep-dive")
async def holding_deep_dive(req: HoldingDiveRequest, user: str = Depends(require_admin)):
    """Focused analysis of a single held position. Admin-only — reads Robinhood positions."""
    import concurrent.futures
    from src.api_keys import get_secret

    ticker = req.ticker.upper()
    ctx = {}

    def _technicals():
        try:
            import numpy as np, talib, requests
            from src.data_engine import format_massive_ticker
            from datetime import date, timedelta
            api_key = get_secret("MASSIVE_API_KEY")
            formatted = format_massive_ticker(ticker)
            end = date.today(); start = end - timedelta(days=400)
            r = requests.get(
                f"https://api.polygon.io/v2/aggs/ticker/{formatted}/range/1/day/{start.isoformat()}/{end.isoformat()}",
                params={"apiKey": api_key, "sort": "asc", "limit": 50000, "adjusted": "true"}, timeout=10)
            bars = r.json().get("results", [])
            if not bars: return
            c = np.array([b["c"] for b in bars], dtype=float)
            price = c[-1]
            ema21 = talib.EMA(c, 21)[-1]
            ema50 = talib.EMA(c, 50)[-1]
            rsi = talib.RSI(c, 14)[-1]
            trend = "Bullish" if price > ema21 > ema50 else "Bearish" if price < ema21 < ema50 else "Mixed"
            hi_52w, lo_52w = float(c[-252:].max()), float(c[-252:].min())
            ctx["technicals"] = f"TECHNICALS: ${price:.2f} | Trend: {trend} | RSI: {rsi:.0f} | 52w: ${lo_52w:.2f}-${hi_52w:.2f}"
        except Exception: pass

    def _signals():
        try:
            from src.signal_engine import compute_composite
            comp = compute_composite(ticker)
            if comp:
                ctx["signals"] = f"SIGNAL ENGINE: {comp['overall_direction']} {comp['overall_conviction']:.0%} ({comp['n_signals']} signals)"
        except Exception: pass

    def _vol():
        try:
            from src.metrics_store import get_latest_snapshot, percentile_ranks_all
            snap = get_latest_snapshot(ticker)
            if snap:
                pct = percentile_ranks_all(ticker)
                iv = snap.get("atm_iv", 0)
                vrp = snap.get("vrp", 0)
                ctx["vol"] = f"VOL: IV {iv:.1%} ({pct.get('atm_iv', '?')}th pctile) | VRP {vrp:+.1%}"
        except Exception: pass

    def _insider():
        try:
            from src.data_engine import fetch_insider_transactions
            from src.edgar import score_insider_transactions, fetch_recent_8k
            txns = fetch_insider_transactions(ticker, limit=20)
            if txns is not None and not txns.empty:
                score = score_insider_transactions(txns)
                ctx["insider"] = f"INSIDER: Score {score.get('score', 0)}/100 | Buys: {score.get('buys', 0)} Sells: {score.get('sells', 0)}"
            else:
                ctx["insider"] = "INSIDER: No recent transactions (normal for small/mid-caps)"
            events = fetch_recent_8k(ticker, days=30)
            if events:
                ctx["events"] = "RECENT 8-K: " + " | ".join(f"{e.get('date','?')}: {e.get('description','')[:60]}" for e in events[:3])
            else:
                ctx["events"] = "RECENT 8-K: No material filings in last 30 days (routine — not a red flag)"
        except Exception: pass

    def _fundamentals():
        try:
            import yfinance as yf
            tk = yf.Ticker(ticker)
            info = tk.info or {}
            parts = []
            for key, label in [("targetMeanPrice", "Analyst Target"), ("recommendationKey", "Rating"),
                                ("trailingPE", "P/E"), ("revenueGrowth", "Rev Growth"),
                                ("shortPercentOfFloat", "Short Float")]:
                val = info.get(key)
                if val is not None:
                    if key == "targetMeanPrice": parts.append(f"{label}: ${val:.2f}")
                    elif "Growth" in label or "Float" in label: parts.append(f"{label}: {val*100:.1f}%")
                    else: parts.append(f"{label}: {val}")
            # Earnings
            next_earn = _safe_next_earnings(tk)
            if next_earn:
                parts.append(f"Next earnings: {next_earn}")
            if parts:
                ctx["fundamentals"] = "FUNDAMENTALS: " + " | ".join(parts)
        except Exception: pass

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        futs = [pool.submit(fn) for fn in [_technicals, _signals, _vol, _insider, _fundamentals]]
        concurrent.futures.wait(futs, timeout=12)

    # Build position context — include market value so AI has exact equity numbers
    mkt_val = req.market_value if req.market_value > 0 else req.qty * req.current_price
    position_ctx = (
        f"POSITION: {req.qty:.0f} shares of {ticker} @ ${req.avg_cost:.2f}"
        f" | Current: ${req.current_price:.2f} | Market Value: ${mkt_val:,.0f}"
        f" | P/L: ${req.pl:+,.0f} ({req.pl_pct:+.1f}%)"
        + (f" | Entry: {req.entry_date}" if req.entry_date else "")
    )

    context = position_ctx + "\n\n" + "\n".join(v for v in ctx.values() if v)

    prompt = f"""{context}

Analyze this position. Give a clear VERDICT: HOLD, ADD, TRIM, or CLOSE.
Then explain why in 3-4 sentences referencing the data above.
If TRIM or ADD, suggest sizing. If CLOSE, explain urgency.
When referencing selling proceeds or position value, use the EXACT Market Value from the data above — do NOT estimate or approximate.
End with 2 bullet KEY RISKS specific to this holding.

NOTE: If 8-K event descriptions are missing or show "unknown", this means the SEC filing parser
couldn't extract the item type — it does NOT mean there's a mystery event. Treat missing 8-K
descriptions as "no material events" rather than as a red flag."""

    try:
        api_key = get_secret("ANTHROPIC_API_KEY")
        if not api_key:
            return {"success": False, "error": "API key not configured"}

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=800,
            system="You are a portfolio manager reviewing a client's position. Be direct and specific. Reference the data provided. No hedging — give a clear verdict.",
            messages=[{"role": "user", "content": prompt}],
        )
        analysis = response.content[0].text.strip()

        # Extract verdict from first line
        verdict = "HOLD"
        for v in ["CLOSE", "TRIM", "ADD", "HOLD"]:
            if v in analysis[:100].upper():
                verdict = v
                break

        return {
            "success": True,
            "ticker": ticker,
            "verdict": verdict,
            "analysis": analysis,
            "sources": list(ctx.keys()),
        }
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}


# ── Trade Architect — AI-powered trade structuring ──

class ArchitectMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class TradeArchitectRequest(BaseModel):
    thesis: str = ""
    messages: list[ArchitectMessage] = []
    context: str = ""
    tickers: list[str] = []
    account_size: float = 25000
    deep: bool = False
    risk: str = "moderate"     # conservative / moderate / aggressive
    strategy: str = "auto"     # auto / sell / buy
    direction: str = ""        # bullish / bearish / neutral / "" (auto-detect)

def _detect_direction(thesis: str) -> str:
    """Detect bullish/bearish/neutral from thesis text.
    Options-aware: 'sell puts' / 'put credit spread' = bullish, 'sell calls' / 'bear call' = bearish."""
    t = thesis.lower()

    # Options-specific phrases (highest priority — these are unambiguous)
    bull_phrases = ["sell put", "put credit", "bull put", "cash secured put", "buy call", "call debit", "bull call", "covered call"]
    bear_phrases = ["sell call", "call credit", "bear call", "buy put", "put debit", "bear put",
                    "protective put", "hedge", "protect", "balance", "reduce risk", "tail risk",
                    "diversify", "too long", "overweight", "de-risk"]
    neutral_phrases = ["sell premium", "iron condor", "range bound", "sideways", "theta gang",
                       "income strategy", "straddle", "strangle", "iron butterfly"]

    bull = sum(2 for p in bull_phrases if p in t)
    bear = sum(2 for p in bear_phrases if p in t)
    neutral = sum(2 for p in neutral_phrases if p in t)

    # General directional words (lower weight, after stripping matched phrases)
    t_clean = t
    for p in bull_phrases + bear_phrases + neutral_phrases:
        t_clean = t_clean.replace(p, "")
    neutral += sum(1 for w in ["neutral", "flat", "premium", "theta"] if w in t_clean)
    bull += sum(1 for w in ["bullish","long","buy","upside","rally","run","breakout","support","bounce","recovery","higher","moon","bull"] if w in t_clean)
    bear += sum(1 for w in ["bearish","short","downside","crash","sell","drop","breakdown","fade","lower","tank","dump","bear"] if w in t_clean)

    if neutral > max(bull, bear): return "neutral"
    if bear > bull: return "bearish"
    if bull > 0: return "bullish"
    return "bullish"

def _compute_structured_trades(primary: str, account_size: float,
                                risk: str = "moderate", strategy: str = "auto",
                                thesis: str = "", direction_override: str = "",
                                portfolio_greeks: dict = None) -> list[dict]:
    """Compute structured trades from real market data. Direction-aware, vol-regime-aware,
    portfolio-impact-aware."""
    import numpy as np
    import requests
    from datetime import date, timedelta
    from src.api_keys import get_secret
    from src.data_engine import format_massive_ticker, fetch_options_chain
    import pandas as pd

    direction = direction_override if direction_override in ("bullish", "bearish", "neutral") else (_detect_direction(thesis) if thesis else "bullish")
    is_bull = direction == "bullish"
    is_bear = direction == "bearish"
    is_neutral = direction == "neutral"

    trades = []
    api_key = get_secret("MASSIVE_API_KEY")

    # ── Fetch OHLCV for technicals ──
    price, atr, bars = 0.0, 0.0, []
    hi_20d, lo_20d, ema200 = 0.0, 0.0, 0.0
    try:
        formatted = format_massive_ticker(primary)
        end = date.today(); start = end - timedelta(days=300)
        r = requests.get(
            f"https://api.polygon.io/v2/aggs/ticker/{formatted}/range/1/day/{start.isoformat()}/{end.isoformat()}",
            params={"apiKey": api_key, "sort": "asc", "limit": 50000, "adjusted": "true"}, timeout=10)
        bars = r.json().get("results", [])
        if bars:
            import talib
            h = np.array([b["h"] for b in bars], dtype=float)
            l = np.array([b["l"] for b in bars], dtype=float)
            c = np.array([b["c"] for b in bars], dtype=float)
            price = c[-1]
            atr = float(talib.ATR(h, l, c, 14)[-1])
            ema200 = float(talib.EMA(c, 200)[-1]) if len(c) >= 200 else price * 0.95
            hi_20d = float(h[-20:].max())
            lo_20d = float(l[-20:].min())
    except Exception:
        pass

    if not price:
        return trades

    _RISK_PCT = {"conservative": 0.015, "moderate": 0.03, "aggressive": 0.05}
    max_risk_dollars = account_size * _RISK_PCT.get(risk, 0.03)

    # For hedging theses, detect the position size and allow larger contract counts
    _is_hedge = direction == "bearish" and any(w in (thesis or "").lower() for w in ["hedge", "protect", "cover", "offset"])
    _position_contracts = 0
    if _is_hedge and portfolio_greeks:
        # Use pre-fetched stock positions from endpoint (no extra RH login)
        stock_positions = portfolio_greeks.get("stock_positions", {})
        qty = stock_positions.get(primary, 0)
        if qty > 0:
            _position_contracts = int(qty / 100)

    # ── TRADE 1: STOCK (direction-aware) ──
    if is_bull or is_neutral:
        stop_price = round(max(ema200 - atr * 0.5, price - atr * 2), 2)
        target_price = round(hi_20d if hi_20d > price else price + atr * 3, 2)
        shares = int(max_risk_dollars / max(price - stop_price, 1))
    else:  # bearish — short stock
        stop_price = round(min(hi_20d + atr * 0.5, price + atr * 2), 2)
        target_price = round(lo_20d if lo_20d < price else price - atr * 3, 2)
        shares = int(max_risk_dollars / max(stop_price - price, 1))
    _max_position_pct = {"conservative": 0.2, "moderate": 0.35, "aggressive": 0.5}
    shares = max(1, min(shares, int(account_size * _max_position_pct.get(risk, 0.35) / price)))
    stock_action = "buy" if (is_bull or is_neutral) else "short"
    # If bearish but user holds shares, recommend selling (trim), not shorting additional
    _held_qty = portfolio_greeks.get("stock_positions", {}).get(primary, 0) if portfolio_greeks else 0
    if stock_action == "short" and _held_qty > 0:
        stock_action = "sell"
        if _is_hedge:
            # Explicit hedge request: trim 50% of position (up to 75%)
            trim_target = int(_held_qty * 0.5)
            shares = max(shares, trim_target)
            shares = min(shares, int(_held_qty * 0.75))
        else:
            # Bearish thesis while holding: cap sell at held quantity
            shares = min(shares, int(_held_qty))
        shares = max(1, shares)
    # Compute P/L AFTER final share count is set
    stock_risk = round(shares * abs(price - stop_price), 2)
    stock_profit = round(shares * abs(target_price - price), 2)
    trades.append({
        "type": "stock", "label": f"{'Buy' if stock_action == 'buy' else 'Sell' if stock_action == 'sell' else 'Short'} {shares} Shares",
        "legs": [{"action": stock_action, "instrument": "shares", "ticker": primary, "qty": shares, "price": price}],
        "entry": price, "stop": stop_price, "target": target_price,
        "max_profit": stock_profit, "max_risk": stock_risk,
        "breakeven": price, "pop": None,
        "rr_ratio": round(stock_profit / stock_risk, 2) if stock_risk > 0 else 0,
        "greeks": {"delta": shares if stock_action == "buy" else -shares, "theta": 0, "gamma": 0, "vega": 0},
        "timeframe": "2-3 weeks",
    })

    # ── Fetch options chain ──
    try:
        chain = fetch_options_chain(primary)
        if chain is None or chain.empty:
            return trades
        chain["dte"] = (pd.to_datetime(chain["expiration_date"]) - pd.Timestamp.now()).dt.days

        monthly = chain[(chain["dte"] >= 20) & (chain["dte"] <= 50)]
        if monthly.empty: monthly = chain[chain["dte"] >= 7]
        if monthly.empty: monthly = chain[chain["dte"] >= 1]  # last resort: any expiration
        if monthly.empty: return trades
        dte_by_exp = monthly.groupby("expiration_date")["dte"].first().dropna()
        if dte_by_exp.empty: return trades
        exp = dte_by_exp.idxmin()
        ec = monthly[monthly["expiration_date"] == exp]
        dte = int(ec["dte"].iloc[0])
        calls = ec[ec["contract_type"] == "call"].sort_values("strike_price")
        puts = ec[ec["contract_type"] == "put"].sort_values("strike_price")
        if calls.empty or puts.empty: return trades

        def mid(row):
            b, a = float(row.get("bid", 0) or 0), float(row.get("ask", 0) or 0)
            return round((b + a) / 2, 2) if b > 0 and a > 0 else round(float(row.get("last_price", 0) or 0), 2)

        def greek(row, field):
            v = row.get(field, 0)
            try: return float(v) if v and not np.isnan(float(v)) else 0.0
            except: return 0.0

        # Vol regime
        atm_c = calls.iloc[(calls["strike_price"] - price).abs().argmin()]
        atm_p = puts.iloc[(puts["strike_price"] - price).abs().argmin()]
        _civ = float(atm_c.get("implied_volatility", 0) or 0)
        _piv = float(atm_p.get("implied_volatility", 0) or 0)
        atm_iv = (_civ + _piv) / 2 if (_civ + _piv) > 0 else 0.3
        hv20 = float(np.std(np.diff(np.log(np.array([b["c"] for b in bars[-22:]], dtype=float)))) * np.sqrt(252)) if len(bars) >= 22 else atm_iv
        vol_rich = atm_iv > hv20 * 1.05
        sell_premium = vol_rich if strategy == "auto" else (strategy == "sell")

        # Earnings check
        earnings_in_window = False
        _next_earn_str = None
        try:
            import yfinance as yf
            _next_earn_str = _safe_next_earnings(yf.Ticker(primary))
            if _next_earn_str:
                next_earn = pd.to_datetime(_next_earn_str)
                if (next_earn - pd.Timestamp.now()).days <= dte:
                    earnings_in_window = True
        except Exception: pass

        target_width = max(5, round(price * 0.01))

        def find_otm(opts, side, target_delta=0.25):
            """Find OTM option near target delta."""
            otm = opts[(opts["strike_price"] < price) if side == "put" else (opts["strike_price"] > price)]
            if otm.empty: return None
            otm = otm.copy()
            otm["_ad"] = otm["delta"].abs()
            near = otm[(otm["_ad"] >= 0.10) & (otm["_ad"] <= 0.40)]
            if not near.empty:
                return near.iloc[(near["_ad"] - target_delta).abs().argmin()]
            return otm.iloc[-1] if side == "put" else otm.iloc[0]

        def find_wing(opts, anchor_strike, side):
            """Find long wing at target_width from anchor."""
            if side == "put":
                cands = opts[opts["strike_price"] <= anchor_strike - target_width + 1]
                if cands.empty: cands = opts[opts["strike_price"] < anchor_strike - 1]
            else:
                cands = opts[opts["strike_price"] >= anchor_strike + target_width - 1]
                if cands.empty: cands = opts[opts["strike_price"] > anchor_strike + 1]
            if cands.empty: return None
            target = anchor_strike - target_width if side == "put" else anchor_strike + target_width
            return cands.iloc[(cands["strike_price"] - target).abs().argmin()]

        def build_spread(short_row, long_row, spread_type, s_inst, l_inst):
            """Build a vertical spread trade dict."""
            ss, ls = float(short_row["strike_price"]), float(long_row["strike_price"])
            width = abs(ss - ls)
            cr = round(mid(short_row) - mid(long_row), 2)
            is_credit = cr > 0
            if is_credit:
                mp = round(cr * 100, 0); mr = round((width - cr) * 100, 0)
            else:
                mr = round(abs(cr) * 100, 0); mp = round((width - abs(cr)) * 100, 0)
            if mr <= 0 or mp <= 0: return None
            _max_c = max(10, _position_contracts) if _is_hedge and _position_contracts > 0 else 10
            contracts = max(1, min(int(max_risk_dollars / max(mr, 1)), _max_c))
            if _is_hedge and _position_contracts > 0:
                contracts = max(contracts, _position_contracts)
            sd = abs(greek(short_row, "delta"))
            pop = round((1 - sd) * 100, 1) if is_credit else round(sd * 100 * 0.7, 1)
            # Breakeven
            if spread_type.startswith("Bull Put"):
                be = round(ss - cr, 2)
            elif spread_type.startswith("Bear Call"):
                be = round(ss + cr, 2)
            elif spread_type.startswith("Bull Call"):
                be = round(ls + abs(cr), 2)
            else:
                be = round(ls - abs(cr), 2)
            lo_s, hi_s = min(ss, ls), max(ss, ls)
            return {
                "type": "options", "label": f"{spread_type} ${lo_s:g}/${hi_s:g}",
                "legs": [
                    {"action": "sell", "instrument": s_inst, "ticker": primary, "strike": ss, "exp": best_exp, "qty": contracts, "price": mid(short_row)},
                    {"action": "buy", "instrument": l_inst, "ticker": primary, "strike": ls, "exp": best_exp, "qty": contracts, "price": mid(long_row)},
                ],
                "entry": cr, "stop": None, "target": None,
                "max_profit": mp * contracts, "max_risk": mr * contracts,
                "breakeven": be, "pop": pop,
                "rr_ratio": round(mp / mr, 2) if mr > 0 else 0,
                "greeks": {
                    "delta": round((greek(short_row, "delta") * -1 + greek(long_row, "delta")) * contracts * 100, 1),
                    "theta": round((greek(short_row, "theta") * -1 + greek(long_row, "theta")) * contracts * 100, 2),
                    "gamma": round((greek(short_row, "gamma") * -1 + greek(long_row, "gamma")) * contracts * 100, 3),
                    "vega": round((greek(short_row, "vega") * -1 + greek(long_row, "vega")) * contracts * 100, 2),
                },
                "timeframe": f"{best_dte}d (exp {best_exp})" + (" ⚠ EARNINGS" if earnings_in_window else ""),
                "contracts": contracts, "width": width,
                "short_strike": ss, "long_strike": ls,
                "short_oi": int(short_row.get("open_interest", 0) or 0),
                "long_oi": int(long_row.get("open_interest", 0) or 0),
            }

        # ── Earnings-aware expiration selection ──
        # If earnings are within the current expiration, try to find a pre-earnings exp
        best_exp, best_dte, best_ec, best_calls, best_puts = exp, dte, ec, calls, puts
        if earnings_in_window:
            all_exps = sorted(chain["expiration_date"].unique())
            for alt_exp in all_exps:
                alt_ec = chain[chain["expiration_date"] == alt_exp]
                alt_dte = int(alt_ec["dte"].iloc[0])
                if 7 <= alt_dte < dte:  # shorter exp that avoids earnings
                    try:
                        # Reuse earnings date from earlier check (no redundant yfinance call)
                        next_earn_days = (pd.to_datetime(_next_earn_str) - pd.Timestamp.now()).days if _next_earn_str else 999
                        if alt_dte < next_earn_days:
                            best_exp = alt_exp
                            best_dte = alt_dte
                            best_ec = alt_ec
                            best_calls = alt_ec[alt_ec["contract_type"] == "call"].sort_values("strike_price")
                            best_puts = alt_ec[alt_ec["contract_type"] == "put"].sort_values("strike_price")
                            earnings_in_window = False  # found a clean exp
                            break
                    except Exception: pass

        # ── TRADE 2: OPTIONS — optimizer (test multiple deltas × widths, pick best score) ──
        opt_trade = None
        best_score = -1

        if is_neutral:
            # Iron condor: test multiple delta targets
            for d_target in [0.15, 0.20, 0.25]:
                sp = find_otm(best_puts, "put", d_target)
                sc = find_otm(best_calls, "call", d_target)
                if sp is None or sc is None: continue
                for tw in [target_width, target_width * 0.6, target_width * 1.5]:
                    tw = max(1, round(tw))
                    lp = find_wing(best_puts, float(sp["strike_price"]), "put")
                    lc = find_wing(best_calls, float(sc["strike_price"]), "call")
                    if lp is None or lc is None: continue
                    sp_s, sc_s = float(sp["strike_price"]), float(sc["strike_price"])
                    lp_s, lc_s = float(lp["strike_price"]), float(lc["strike_price"])
                    cr = round(mid(sp) + mid(sc) - mid(lp) - mid(lc), 2)
                    put_w = sp_s - lp_s; call_w = lc_s - sc_s
                    mr = round((max(put_w, call_w) - cr) * 100, 0)
                    mp = round(cr * 100, 0)
                    if mr <= 0 or mp <= 0 or cr <= 0.05: continue
                    pop = round((1 - abs(greek(sp, "delta")) - abs(greek(sc, "delta"))) * 100, 1)
                    if pop < 30 or pop > 95: continue  # skip degenerate setups
                    score = min(mp / mr, 5.0) * (pop / 100) if mr > 0 else 0
                    if score > best_score:
                        best_score = score
                        _max_c = max(10, _position_contracts) if _is_hedge and _position_contracts > 0 else 10
                        contracts = max(1, min(int(max_risk_dollars / max(mr, 1)), _max_c))
                        if _is_hedge and _position_contracts > 0:
                            contracts = max(contracts, _position_contracts)
                        opt_trade = {
                            "type": "options", "label": f"Iron Condor ${lp_s:g}/${sp_s:g}/${sc_s:g}/${lc_s:g}",
                            "legs": [
                                {"action": "sell", "instrument": "put", "ticker": primary, "strike": sp_s, "exp": best_exp, "qty": contracts, "price": mid(sp)},
                                {"action": "buy", "instrument": "put", "ticker": primary, "strike": lp_s, "exp": best_exp, "qty": contracts, "price": mid(lp)},
                                {"action": "sell", "instrument": "call", "ticker": primary, "strike": sc_s, "exp": best_exp, "qty": contracts, "price": mid(sc)},
                                {"action": "buy", "instrument": "call", "ticker": primary, "strike": lc_s, "exp": best_exp, "qty": contracts, "price": mid(lc)},
                            ],
                            "entry": cr, "stop": None, "target": None,
                            "max_profit": mp * contracts, "max_risk": mr * contracts,
                            "breakeven": round(sp_s - cr, 2), "breakeven_upper": round(sc_s + cr, 2), "pop": pop,
                            "rr_ratio": round(mp / mr, 2),
                            "greeks": {
                                "delta": round((-greek(sp, "delta") + greek(lp, "delta") - greek(sc, "delta") + greek(lc, "delta")) * contracts * 100, 1),
                                "theta": round((-greek(sp, "theta") + greek(lp, "theta") - greek(sc, "theta") + greek(lc, "theta")) * contracts * 100, 2),
                                "gamma": round((-greek(sp, "gamma") + greek(lp, "gamma") - greek(sc, "gamma") + greek(lc, "gamma")) * contracts * 100, 3),
                                "vega": round((-greek(sp, "vega") + greek(lp, "vega") - greek(sc, "vega") + greek(lc, "vega")) * contracts * 100, 2),
                            },
                            "timeframe": f"{best_dte}d (exp {best_exp})" + (" ⚠ EARNINGS" if earnings_in_window else ""),
                            "contracts": contracts,
                        }
        else:
            # Vertical spreads: test delta targets × widths, pick best
            _DELTAS = [0.15, 0.20, 0.25, 0.30, 0.35]
            _WIDTHS = [max(1, round(price * p)) for p in [0.005, 0.01, 0.015, 0.025]]

            for d_target in _DELTAS:
                for tw_test in _WIDTHS:
                    if is_bull and sell_premium:
                        short_row = find_otm(best_puts, "put", d_target)
                        if short_row is None: continue
                        ss = float(short_row["strike_price"])
                        lp_cands = best_puts[best_puts["strike_price"] <= ss - tw_test + 1]
                        if lp_cands.empty: continue
                        long_row = lp_cands.iloc[(lp_cands["strike_price"] - (ss - tw_test)).abs().argmin()]
                        spread = build_spread(short_row, long_row, "Bull Put Spread", "put", "put")
                    elif is_bull and not sell_premium:
                        long_row = find_otm(best_calls, "call", d_target)
                        if long_row is None: continue
                        ls = float(long_row["strike_price"])
                        sc_cands = best_calls[best_calls["strike_price"] >= ls + tw_test - 1]
                        if sc_cands.empty: continue
                        short_row = sc_cands.iloc[(sc_cands["strike_price"] - (ls + tw_test)).abs().argmin()]
                        spread = build_spread(short_row, long_row, "Bull Call Spread", "call", "call")
                    elif is_bear and sell_premium:
                        short_row = find_otm(best_calls, "call", d_target)
                        if short_row is None: continue
                        ss = float(short_row["strike_price"])
                        lc_cands = best_calls[best_calls["strike_price"] >= ss + tw_test - 1]
                        if lc_cands.empty: continue
                        long_row = lc_cands.iloc[(lc_cands["strike_price"] - (ss + tw_test)).abs().argmin()]
                        spread = build_spread(short_row, long_row, "Bear Call Spread", "call", "call")
                    else:  # bearish debit
                        long_row = find_otm(best_puts, "put", d_target)
                        if long_row is None: continue
                        ls = float(long_row["strike_price"])
                        sp_cands = best_puts[best_puts["strike_price"] <= ls - tw_test + 1]
                        if sp_cands.empty: continue
                        short_row = sp_cands.iloc[(sp_cands["strike_price"] - (ls - tw_test)).abs().argmin()]
                        spread = build_spread(short_row, long_row, "Bear Put Spread", "put", "put")

                    if spread is None: continue
                    # Score: (credit_or_profit / risk) × POP
                    s_pop = spread.get("pop", 50) / 100
                    s_rr = spread.get("rr_ratio", 0)
                    # Score: R:R × POP, but penalize extremes (POP < 30% or > 95% are degenerate)
                    if s_pop < 0.30 or s_pop > 0.95: continue
                    score = min(s_rr, 5.0) * s_pop  # cap R:R contribution to avoid penny-picking
                    if score > best_score:
                        best_score = score
                        spread["timeframe"] = f"{best_dte}d (exp {best_exp})" + (" ⚠ EARNINGS" if earnings_in_window else "")
                        opt_trade = spread

        if opt_trade:
            # ── Historical backtest on the recommended spread ──
            if bars and len(bars) >= best_dte + 30 and opt_trade.get("legs"):
                try:
                    closes = np.array([b["c"] for b in bars], dtype=float)
                    legs = opt_trade["legs"]
                    # Extract strike distances as % of spot
                    short_legs = [l for l in legs if l["action"] == "sell" and l["instrument"] != "shares"]
                    if short_legs:
                        s_strike = short_legs[0]["strike"]
                        dist_pct = abs(price - s_strike) / price
                        credit_pct = abs(opt_trade["entry"]) / price if opt_trade["entry"] else 0
                        # Simulate: for each historical entry, did price stay within the distance?
                        wins, total = 0, 0
                        for i in range(len(closes) - best_dte):
                            entry_p = closes[i]
                            window = closes[i+1:i+best_dte+1]
                            if len(window) < 2: continue
                            total += 1
                            # For credit spreads: win if price stays beyond short strike distance
                            if is_bull or is_neutral:
                                if window.min() > entry_p * (1 - dist_pct): wins += 1
                            else:
                                if window.max() < entry_p * (1 + dist_pct): wins += 1
                        if total >= 20:
                            opt_trade["hist_winrate"] = round(wins / total * 100, 1)
                            opt_trade["hist_trials"] = total
                except Exception: pass
            trades.append(opt_trade)

        # ── TRADE 3: COMBINATION — defined-risk only (no naked options) ──
        # Isolated try/except so a bug here doesn't kill vol suggestion / portfolio impact
        try:
          # Choose structure based on direction + what makes sense for the account:
          # Bullish: wider bull put spread (different width than trade 2) or long call + put hedge
          # Bearish: wider bear call spread or long put + call hedge
          # Neutral: wider iron condor (different deltas than trade 2)
          combo_trade = None
          if opt_trade:
              # Build an alternative spread at a different delta/width than the optimizer picked
              alt_delta = 0.35 if sell_premium else 0.30  # further OTM or closer to ATM
              alt_width = round(target_width * 1.5)
              if is_bull:
                  if sell_premium:
                      sr = find_otm(best_puts, "put", alt_delta)
                      if sr:
                          lr_c = best_puts[best_puts["strike_price"] <= float(sr["strike_price"]) - alt_width + 1]
                          if not lr_c.empty:
                              lr = lr_c.iloc[(lr_c["strike_price"] - (float(sr["strike_price"]) - alt_width)).abs().argmin()]
                              combo_trade = build_spread(sr, lr, "Wide Bull Put Spread", "put", "put")
                              if combo_trade: combo_trade["type"] = "combination"
                  else:
                      # Long call (defined risk, leveraged upside)
                      lc_row = find_otm(best_calls, "call", 0.40)
                      if lc_row:
                          lc_price = mid(lc_row)
                          lc_strike = float(lc_row["strike_price"])
                          if lc_price > 0:
                              contracts = max(1, min(int(max_risk_dollars / (lc_price * 100)), 5))
                              combo_trade = {
                                  "type": "combination", "label": f"Long ${lc_strike:g} Call",
                                  "legs": [{"action": "buy", "instrument": "call", "ticker": primary, "strike": lc_strike,
                                            "exp": best_exp, "qty": contracts, "price": lc_price}],
                                  "entry": lc_price, "stop": None, "target": None,
                                  "max_profit": round(price * 0.1 * 100 * contracts, 0),
                                  "max_risk": round(lc_price * 100 * contracts, 0),
                                  "breakeven": round(lc_strike + lc_price, 2),
                                  "pop": round(abs(greek(lc_row, "delta")) * max(0.4, 1 - lc_price / price) * 100, 1),
                                  "rr_ratio": round(price * 0.1 / lc_price, 2) if lc_price > 0 else 0,
                                  "greeks": {
                                      "delta": round(greek(lc_row, "delta") * contracts * 100, 1),
                                      "theta": round(greek(lc_row, "theta") * contracts * 100, 2),
                                      "gamma": round(greek(lc_row, "gamma") * contracts * 100, 3),
                                      "vega": round(greek(lc_row, "vega") * contracts * 100, 2),
                                  },
                                  "timeframe": f"{best_dte}d (exp {best_exp})" + (" ⚠ EARNINGS" if earnings_in_window else ""),
                                  "contracts": contracts,
                              }
              elif is_bear:
                  if sell_premium:
                      sr = find_otm(best_calls, "call", alt_delta)
                      if sr:
                          lr_c = best_calls[best_calls["strike_price"] >= float(sr["strike_price"]) + alt_width - 1]
                          if not lr_c.empty:
                              lr = lr_c.iloc[(lr_c["strike_price"] - (float(sr["strike_price"]) + alt_width)).abs().argmin()]
                              combo_trade = build_spread(sr, lr, "Wide Bear Call Spread", "call", "call")
                              if combo_trade: combo_trade["type"] = "combination"
                  else:
                      lp_row = find_otm(best_puts, "put", 0.40)
                      if lp_row:
                          lp_price = mid(lp_row)
                          lp_strike = float(lp_row["strike_price"])
                          if lp_price > 0:
                              contracts = max(1, min(int(max_risk_dollars / (lp_price * 100)), 5))
                              combo_trade = {
                                  "type": "combination", "label": f"Long ${lp_strike:g} Put",
                                  "legs": [{"action": "buy", "instrument": "put", "ticker": primary, "strike": lp_strike,
                                            "exp": best_exp, "qty": contracts, "price": lp_price}],
                                  "entry": lp_price, "stop": None, "target": None,
                                  "max_profit": round(lp_strike * 0.1 * 100 * contracts, 0),
                                  "max_risk": round(lp_price * 100 * contracts, 0),
                                  "breakeven": round(lp_strike - lp_price, 2),
                                  "pop": round(abs(greek(lp_row, "delta")) * max(0.4, 1 - lp_price / price) * 100, 1),
                                  "rr_ratio": round(lp_strike * 0.1 / lp_price, 2) if lp_price > 0 else 0,
                                  "greeks": {
                                      "delta": round(greek(lp_row, "delta") * contracts * 100, 1),
                                      "theta": round(greek(lp_row, "theta") * contracts * 100, 2),
                                      "gamma": round(greek(lp_row, "gamma") * contracts * 100, 3),
                                      "vega": round(greek(lp_row, "vega") * contracts * 100, 2),
                                  },
                                  "timeframe": f"{best_dte}d (exp {best_exp})" + (" ⚠ EARNINGS" if earnings_in_window else ""),
                                  "contracts": contracts,
                              }
          # Fallback combo: if the wide spread didn't work, try a long option (always defined risk)
          if not combo_trade and not is_neutral:
              if is_bull:
                  lc_row = find_otm(best_calls, "call", 0.40)
                  if lc_row:
                      lc_price = mid(lc_row)
                      lc_strike = float(lc_row["strike_price"])
                      if lc_price > 0:
                          contracts = max(1, min(int(max_risk_dollars / (lc_price * 100)), 5))
                          combo_trade = {
                              "type": "combination", "label": f"Long ${lc_strike:g} Call",
                              "legs": [{"action": "buy", "instrument": "call", "ticker": primary, "strike": lc_strike,
                                        "exp": best_exp, "qty": contracts, "price": lc_price}],
                              "entry": lc_price, "stop": None, "target": None,
                              "max_profit": round(price * 0.1 * 100 * contracts, 0),
                              "max_risk": round(lc_price * 100 * contracts, 0),
                              "breakeven": round(lc_strike + lc_price, 2),
                              "pop": round(abs(greek(lc_row, "delta")) * max(0.4, 1 - lc_price / price) * 100, 1),
                              "rr_ratio": round(price * 0.1 / lc_price, 2) if lc_price > 0 else 0,
                              "greeks": {
                                  "delta": round(greek(lc_row, "delta") * contracts * 100, 1),
                                  "theta": round(greek(lc_row, "theta") * contracts * 100, 2),
                                  "gamma": round(greek(lc_row, "gamma") * contracts * 100, 3),
                                  "vega": round(greek(lc_row, "vega") * contracts * 100, 2),
                              },
                              "timeframe": f"{best_dte}d (exp {best_exp})",
                              "contracts": contracts,
                          }
              else:
                  lp_row = find_otm(best_puts, "put", 0.40)
                  if lp_row:
                      lp_price = mid(lp_row)
                      lp_strike = float(lp_row["strike_price"])
                      if lp_price > 0:
                          contracts = max(1, min(int(max_risk_dollars / (lp_price * 100)), 5))
                          combo_trade = {
                              "type": "combination", "label": f"Long ${lp_strike:g} Put",
                              "legs": [{"action": "buy", "instrument": "put", "ticker": primary, "strike": lp_strike,
                                        "exp": best_exp, "qty": contracts, "price": lp_price}],
                              "entry": lp_price, "stop": None, "target": None,
                              "max_profit": round(lp_strike * 0.1 * 100 * contracts, 0),
                              "max_risk": round(lp_price * 100 * contracts, 0),
                              "breakeven": round(lp_strike - lp_price, 2),
                              "pop": round(abs(greek(lp_row, "delta")) * max(0.4, 1 - lp_price / price) * 100, 1),
                              "rr_ratio": round(lp_strike * 0.1 / lp_price, 2) if lp_price > 0 else 0,
                              "greeks": {
                                  "delta": round(greek(lp_row, "delta") * contracts * 100, 1),
                                  "theta": round(greek(lp_row, "theta") * contracts * 100, 2),
                                  "gamma": round(greek(lp_row, "gamma") * contracts * 100, 3),
                                  "vega": round(greek(lp_row, "vega") * contracts * 100, 2),
                              },
                              "timeframe": f"{best_dte}d (exp {best_exp})",
                              "contracts": contracts,
                          }
        except Exception:
            pass  # combo builder failed — stock + options trades still valid
        if combo_trade:
            trades.append(combo_trade)

        # ── Vol suggestion (from trade ideas page logic) ──
        vol_suggestion = ""
        if atm_iv and hv20:
            iv_pct = atm_iv * 100; rv_pct = hv20 * 100
            if atm_iv > hv20 * 1.05:
                vol_suggestion = f"IV {iv_pct:.0f}% > RV {rv_pct:.0f}% → vol rich, favor selling premium"
            elif hv20 > atm_iv * 1.05:
                vol_suggestion = f"IV {iv_pct:.0f}% < RV {rv_pct:.0f}% → vol cheap, favor buying premium"
            else:
                vol_suggestion = f"IV {iv_pct:.0f}% ≈ RV {rv_pct:.0f}% → neutral, either structure viable"
        for t in trades:
            t["vol_suggestion"] = vol_suggestion
            t["direction"] = direction

        # ── Portfolio impact (before/after Greeks) ──
        pg = portfolio_greeks or {}
        port_delta = pg.get("delta", 0)
        port_theta = pg.get("theta", 0)
        port_equity = pg.get("equity", account_size)
        for t in trades:
            t["portfolio_equity"] = port_equity
            t["risk_pct_of_account"] = round(t["max_risk"] / port_equity * 100, 1) if port_equity > 0 else 0
            td = t["greeks"]["delta"]
            tt = t["greeks"]["theta"]
            t["portfolio_delta_before"] = round(port_delta, 1)
            t["portfolio_delta_after"] = round(port_delta + td, 1)
            t["portfolio_theta_before"] = round(port_theta, 1)
            t["portfolio_theta_after"] = round(port_theta + tt, 1)

            # Account fit score: penalize trades that increase directional concentration
            # Score: 100 = perfect fit, 0 = terrible (doubles existing exposure)
            fit = 100
            if port_delta != 0 and td != 0:
                same_direction = (port_delta > 0 and td > 0) or (port_delta < 0 and td < 0)
                if same_direction:
                    concentration = abs(td) / max(abs(port_delta), 1) * 100
                    fit -= min(concentration * 2, 40)  # up to -40 for adding to concentrated direction
                else:
                    fit += 10  # bonus for hedging
            # Penalize oversized risk (cap penalty at 50 — don't zero out undefined-risk trades)
            risk_pct = t["risk_pct_of_account"]
            if risk_pct > 5: fit -= min((risk_pct - 5) * 2, 50)
            t["account_fit"] = max(0, min(100, round(fit)))

    except Exception:
        pass

    # ── Portfolio impact (runs on ALL trades, even if options chain failed) ──
    pg = portfolio_greeks or {}
    port_delta = pg.get("delta", 0)
    port_theta = pg.get("theta", 0)
    port_equity = pg.get("equity", account_size)
    for t in trades:
        if "risk_pct_of_account" not in t:
            t["portfolio_equity"] = port_equity
            t["risk_pct_of_account"] = round(t["max_risk"] / port_equity * 100, 1) if port_equity > 0 else 0
            td = t["greeks"]["delta"]
            tt = t["greeks"]["theta"]
            t["portfolio_delta_before"] = round(port_delta, 1)
            t["portfolio_delta_after"] = round(port_delta + td, 1)
            t["portfolio_theta_before"] = round(port_theta, 1)
            t["portfolio_theta_after"] = round(port_theta + tt, 1)
            fit = 100
            if port_delta != 0 and td != 0:
                same_direction = (port_delta > 0 and td > 0) or (port_delta < 0 and td < 0)
                if same_direction:
                    concentration = abs(td) / max(abs(port_delta), 1) * 100
                    fit -= min(concentration * 2, 40)
                else:
                    fit += 10
            risk_pct = t["risk_pct_of_account"]
            if risk_pct > 5: fit -= min((risk_pct - 5) * 2, 50)
            t["account_fit"] = max(0, min(100, round(fit)))

    # ── Signal engine composite ──
    try:
        from src.signal_engine import compute_composite
        comp = compute_composite(primary)
        if comp:
            signal_text = f"{comp['overall_direction']} {comp['overall_conviction']:.0%} ({comp['n_signals']} signals)"
            for t in trades:
                t["signal_consensus"] = signal_text
    except Exception: pass

    return trades


_SECTOR_MAP: dict[str, list[str]] = {
    "agriculture": ["MOO", "CF", "MOS", "DE", "ADM", "CORN", "WEAT"],
    "farming": ["MOO", "CF", "MOS", "DE", "ADM"],
    "fertilizer": ["CF", "MOS", "NTR"],
    "energy": ["XLE", "XOP", "USO", "CVX", "XOM", "SLB", "CL"],
    "oil": ["USO", "XLE", "XOP", "CVX", "XOM", "CL"],
    "natural gas": ["UNG", "AR", "EQT", "SWN", "NG"],
    "gold": ["GLD", "GDX", "NEM", "GOLD", "AEM"],
    "silver": ["SLV", "PAAS", "AG"],
    "bitcoin": ["BTC", "MSTR", "COIN", "MARA", "RIOT"],
    "crypto": ["BTC", "ETH", "COIN", "MSTR", "MARA"],
    "ai": ["NVDA", "AMD", "SMCI", "AVGO", "MSFT", "GOOGL", "SMH"],
    "semiconductor": ["SMH", "NVDA", "AMD", "AVGO", "INTC", "TSM", "QCOM"],
    "chip": ["SMH", "NVDA", "AMD", "AVGO", "INTC", "TSM"],
    "quantum": ["RGTI", "IONQ", "QUBT", "QBTS"],
    "defense": ["LMT", "RTX", "NOC", "GD", "BA", "ITA"],
    "military": ["LMT", "RTX", "NOC", "GD", "ITA"],
    "bank": ["XLF", "JPM", "BAC", "GS", "MS", "C", "WFC"],
    "financial": ["XLF", "JPM", "GS", "MS", "BRK.B", "V", "MA"],
    "healthcare": ["XLV", "UNH", "JNJ", "PFE", "MRK", "ABBV", "LLY"],
    "biotech": ["XBI", "MRNA", "REGN", "VRTX", "AMGN"],
    "real estate": ["XLRE", "VNQ", "AMT", "PLD", "SPG"],
    "utilities": ["XLU", "NEE", "DUK", "SO", "AEP"],
    "consumer": ["XLY", "AMZN", "TSLA", "HD", "MCD", "NKE"],
    "retail": ["XRT", "WMT", "COST", "TGT", "AMZN"],
    "tech": ["XLK", "QQQ", "AAPL", "MSFT", "GOOGL", "META"],
    "cloud": ["SNOW", "NET", "DDOG", "CRWD", "ZS"],
    "cyber": ["CRWD", "PANW", "ZS", "FTNT", "HACK"],
    "industrial": ["XLI", "CAT", "HON", "UPS", "GE"],
    "uranium": ["URA", "CCJ", "UUUU", "DNN"],
    "clean energy": ["ICLN", "TAN", "ENPH", "FSLR", "RUN"],
    "cannabis": ["MSOS", "TLRY", "CGC"],
    "china": ["FXI", "KWEB", "BABA", "JD", "PDD"],
    "emerging": ["EEM", "VWO", "IEMG"],
    "bond": ["TLT", "IEF", "LQD", "HYG", "AGG"],
    "treasury": ["TLT", "IEF", "SHY", "TIP"],
}

def _extract_tickers(text: str) -> list[str]:
    """Extract tickers from text. Handles sector/theme keywords + explicit tickers."""
    import re
    t = text.lower()

    # Check sector/theme keywords first
    sector_tickers: list[str] = []
    for keyword, tickers in _SECTOR_MAP.items():
        if keyword in t:
            sector_tickers.extend(tickers)

    # Also look for explicit tickers
    _KNOWN = {"SPY","QQQ","AAPL","MSFT","NVDA","TSLA","AMD","AMZN","META","GOOGL","NFLX",
               "GLD","TLT","XLE","XLF","XLK","RGTI","USO","BA","JPM","GS","MA","DIS",
               "COST","WMT","HD","UNH","JNJ","PFE","MRK","ABBV","LLY","SMCI","COIN","MSTR",
               "PLTR","SOFI","ARM","SNOW","NET","CRWD","PANW","ZS","DDOG","SHOP","SQ","ROKU",
               "IWM","DIA","EEM","EFA","HYG","LQD","SMH","XBI","ARKK","VIX","UVXY",
               "QUBT","QBTS","IONQ","UAMY","XOP","XLU","XLV","XLI","XLB","XLC","XLY","XLP",
               "XLRE","TNA","SOXL","TQQQ","SQQQ","SPXU","BTC","ETH",
               "MOO","CF","MOS","DE","ADM","CORN","WEAT","NTR",
               "CVX","XOM","SLB","UNG","AR","EQT","SLV","PAAS","AG",
               "MARA","RIOT","AVGO","INTC","TSM","QCOM",
               "LMT","RTX","NOC","GD","ITA","BAC","MS","WFC","BRK.B",
               "MRNA","REGN","VRTX","AMGN","VNQ","AMT","PLD","SPG",
               "NEE","DUK","SO","AEP","MCD","NKE","TGT","XRT",
               "CAT","HON","UPS","GE","CCJ","UUUU","DNN",
               "ICLN","TAN","ENPH","FSLR","FTNT","HACK",
               "MSOS","TLRY","CGC","FXI","KWEB","BABA","JD","PDD",
               "VWO","IEMG","IEF","SHY","TIP","AGG"}
    explicit = []
    for c in re.findall(r'\b([A-Z]{1,5})\b', text.upper()):
        if c in _KNOWN: explicit.append(c)

    # Combine: explicit tickers first, then sector suggestions
    seen, out = set(), []
    for tk in explicit + sector_tickers:
        if tk not in seen:
            out.append(tk); seen.add(tk)
            if len(out) >= 5: break

    return out


_ARCHITECT_SYSTEM = """You are an elite institutional trade structurer at a top-tier prop desk.
The user gives you a trading thesis. Your job: find the BEST way to express that thesis
using ALL available market data. You have real-time technicals, a live options chain with
actual bids/asks, vol regime data, current portfolio positions, news, signal engine
consensus, insider activity, fundamentals, macro calendar, peer comparisons, pre-computed
scanner results, and Black-Scholes Greeks.

This is a CONVERSATION. The user may ask follow-up questions to refine the trade.
Adjust your recommendations based on their feedback while keeping the original data context.

OUTPUT FORMAT for initial analysis (follow EXACTLY — the frontend parses these headers):

**THESIS ASSESSMENT**
1-2 sentences: is this thesis well-timed? What does data support or contradict?

**TRADE 1: STOCK**
Best pure stock expression.
- Action: Buy/Sell/Short [X] shares of [TICKER] at $[price]
- Stop: $[level] ([X]% risk, based on ATR/support)
- Target: $[level] ([X]% upside, based on resistance/technicals)
- Timeframe: [days/weeks]
- Risk: $[max dollar loss] ([X]% of account)

**TRADE 2: OPTIONS**
Best pure options expression. Use REAL strikes from the chain.
- Structure: [exact legs with strikes, expiration, bid-ask midpoint prices]
- Max profit: $[amount] | Max risk: $[amount]
- Breakeven: $[level] | POP: [X]%
- Greeks: Δ=[X] Θ=$[X]/day
- Why this structure: [vol regime, skew, term structure justify the choice]

**TRADE 3: COMBINATION**
Best stock + options combo (covered call, protective put, collar, etc).
- Exact structure with shares + options legs
- Net cost basis / breakeven
- Why the combo beats pure stock or pure options

**BEST TRADE**
Which of the three is the SINGLE best expression and why. 1-2 sentences.

**KEY RISKS**
2-3 specific risks flagged by the data.

**EDGE**
What gives this trade an edge vs random entry.

For FOLLOW-UP responses: respond naturally. If the user asks to adjust, give the revised
trade with the same specificity. You don't need all headers for follow-ups — just address
what changed.

RULES:
- Use REAL strikes and prices from the options chain data
- Account for existing positions — flag doubling exposure
- If vol is rich (VRP > 2%), favor selling premium. If cheap, favor buying.
- Never risk more than 5% of account on one trade
- Be SPECIFIC — exact strikes, exact prices, exact quantities
- If scanner results are provided, reference them (they're pre-optimized)"""


# Server-side context cache: {ticker: (context_str, sources, timestamp)}
_architect_cache: dict[str, tuple[str, list[str], float]] = {}
_ARCHITECT_CACHE_TTL = 300  # 5 min

def _safe_next_earnings(ticker_obj) -> str | None:
    """Safely extract next earnings date from yfinance Ticker.calendar (handles dict or DataFrame)."""
    try:
        cal = ticker_obj.calendar
        if cal is None:
            return None
        if isinstance(cal, dict):
            # Newer yfinance: dict with 'Earnings Date' key
            ed = cal.get("Earnings Date") or cal.get("earnings_date")
            if ed:
                return str(ed[0]) if isinstance(ed, (list, tuple)) else str(ed)
            return None
        # Older yfinance: DataFrame
        if hasattr(cal, "empty") and not cal.empty and len(cal.columns) > 0:
            return str(cal.iloc[0, 0])
    except Exception:
        pass
    return None

def _gather_architect_context(primary: str, tickers: list[str], account_size: float) -> tuple[str, list[str]]:
    """Gather all 14+ data sources for the Trade Architect. Returns (context_str, source_list).
    Results are cached per-ticker for 5 minutes."""
    import time
    cache_key = f"{primary}:{','.join(sorted(tickers))}"
    cached = _architect_cache.get(cache_key)
    if cached and (time.time() - cached[2]) < _ARCHITECT_CACHE_TTL:
        return cached[0], cached[1]

    import concurrent.futures
    import numpy as np
    import pandas as pd
    import requests
    from datetime import date, datetime, timedelta
    from src.api_keys import get_secret
    from src.data_engine import format_massive_ticker

    ctx = {}
    api_key = get_secret("MASSIVE_API_KEY")

    # ── Gather ALL platform data in parallel ──
    ctx = {}

    def _technicals():
        try:
            formatted = format_massive_ticker(primary)
            end = date.today(); start = end - timedelta(days=365)
            api_key = get_secret("MASSIVE_API_KEY")
            r = requests.get(
                f"https://api.polygon.io/v2/aggs/ticker/{formatted}/range/1/day/{start.isoformat()}/{end.isoformat()}",
                params={"apiKey": api_key, "sort": "asc", "limit": 50000, "adjusted": "true"}, timeout=15)
            results = r.json().get("results", [])
            if not results: return

            import talib
            h = np.array([b["h"] for b in results], dtype=float)
            l = np.array([b["l"] for b in results], dtype=float)
            c = np.array([b["c"] for b in results], dtype=float)
            v = np.array([b["v"] for b in results], dtype=float)
            price = c[-1]

            ema9, ema21, ema50 = talib.EMA(c, 9)[-1], talib.EMA(c, 21)[-1], talib.EMA(c, 50)[-1]
            ema200 = talib.EMA(c, 200)[-1] if len(c) >= 200 else None
            rsi = talib.RSI(c, 14)[-1]
            _, _, macd_hist = talib.MACD(c)
            atr = talib.ATR(h, l, c, 14)[-1]
            hv20 = float(np.std(np.diff(np.log(c[-21:]))) * np.sqrt(252) * 100) if len(c) >= 21 else 0
            hv60 = float(np.std(np.diff(np.log(c[-61:]))) * np.sqrt(252) * 100) if len(c) >= 61 else hv20
            avg_vol_20 = float(v[-20:].mean())
            # Support/resistance from recent pivots
            hi_52w, lo_52w = float(c[-252:].max()), float(c[-252:].min())
            hi_20d, lo_20d = float(h[-20:].max()), float(l[-20:].min())

            trend = "Bullish" if price > ema21 > ema50 else "Bearish" if price < ema21 < ema50 else "Mixed"
            ctx["technicals"] = (
                f"TECHNICALS ({primary} ${price:.2f}):\n"
                f"  Trend: {trend} | EMA9={ema9:.2f} EMA21={ema21:.2f} EMA50={ema50:.2f}"
                + (f" EMA200={ema200:.2f}" if ema200 else "") + "\n"
                f"  RSI(14)={rsi:.1f} | MACD hist={macd_hist[-1]:.3f} | ATR(14)=${atr:.2f}\n"
                f"  HV20={hv20:.1f}% HV60={hv60:.1f}% | Avg vol 20d: {avg_vol_20/1e6:.1f}M\n"
                f"  52w: ${lo_52w:.2f}-${hi_52w:.2f} | 20d: ${lo_20d:.2f}-${hi_20d:.2f}\n"
                f"  Key levels: support ~${lo_20d:.2f}, resistance ~${hi_20d:.2f}"
            )
            ctx["_price"] = price
            ctx["_atr"] = atr
        except Exception as e:
            ctx["technicals"] = f"Technicals unavailable: {e}"

    def _options_chain():
        try:
            from src.data_engine import fetch_options_chain
            chain = fetch_options_chain(primary)
            if chain is None or chain.empty: return
            chain["dte"] = (pd.to_datetime(chain["expiration_date"]) - pd.Timestamp.now()).dt.days

            # All available expirations
            exps = sorted(chain["expiration_date"].unique())[:8]
            dte_list = [(e, int(chain[chain["expiration_date"] == e]["dte"].iloc[0])) for e in exps]
            exp_str = ", ".join(f"{e} ({d}d)" for e, d in dte_list)

            # Nearest monthly chain
            monthly = chain[(chain["dte"] >= 20) & (chain["dte"] <= 50)]
            if monthly.empty: monthly = chain[chain["dte"] >= 7]
            if monthly.empty: return
            exp = monthly.groupby("expiration_date")["dte"].first().idxmin()
            ec = monthly[monthly["expiration_date"] == exp]
            dte = int(ec["dte"].iloc[0])
            calls = ec[ec["contract_type"] == "call"].sort_values("strike_price")
            puts = ec[ec["contract_type"] == "put"].sort_values("strike_price")
            if calls.empty or puts.empty: return

            spot_approx = float(calls["strike_price"].median())
            # ATM IV
            atm_c = calls.iloc[(calls["strike_price"] - spot_approx).abs().argmin()]
            atm_p = puts.iloc[(puts["strike_price"] - spot_approx).abs().argmin()]
            _c_iv = float(atm_c.get("implied_volatility", 0) or 0)
            _p_iv = float(atm_p.get("implied_volatility", 0) or 0)
            atm_iv = (_c_iv + _p_iv) / 2 * 100 if (_c_iv + _p_iv) > 0 else 0

            # Build strike table: 10 strikes nearest ATM with bid/ask/IV/delta/OI
            def _strike_row(row, side):
                return (f"  {side} ${row['strike_price']:.0f}: "
                        f"bid ${row.get('bid', 0) or 0:.2f} ask ${row.get('ask', 0) or 0:.2f} "
                        f"IV={float(row.get('implied_volatility', 0) or 0)*100:.1f}% "
                        f"Δ={float(row.get('delta', 0) or 0):.2f} "
                        f"OI={int(row.get('open_interest', 0) or 0)}")

            # 5 nearest ATM calls + 5 nearest ATM puts
            near_calls = calls.iloc[(calls["strike_price"] - spot_approx).abs().argsort()[:7]]
            near_puts = puts.iloc[(puts["strike_price"] - spot_approx).abs().argsort()[:7]]

            strike_table = "\n".join(
                [_strike_row(row, "C") for _, row in near_calls.iterrows()] +
                [_strike_row(row, "P") for _, row in near_puts.iterrows()]
            )

            # Put skew
            otm_puts = puts[puts["strike_price"] < spot_approx * 0.95]
            put_skew = float(otm_puts["implied_volatility"].mean() / (atm_iv / 100)) if not otm_puts.empty and atm_iv > 0 else 1.0

            ctx["options"] = (
                f"OPTIONS CHAIN ({primary}, nearest monthly exp {exp} = {dte}d):\n"
                f"  ATM IV: {atm_iv:.1f}% | Put skew: {put_skew:.2f}x\n"
                f"  Expirations available: {exp_str}\n"
                f"  Calls OI: {int(calls['open_interest'].sum())} | Puts OI: {int(puts['open_interest'].sum())}\n"
                f"  --- Strike Table (nearest ATM, exp {exp}) ---\n{strike_table}"
            )
        except Exception as e:
            ctx["options"] = f"Options unavailable: {e}"

    def _vol_regime():
        try:
            from src.metrics_store import get_latest_snapshot, percentile_ranks_all
            snap = get_latest_snapshot(primary)
            if not snap: return
            pct = percentile_ranks_all(primary)
            iv = snap.get("atm_iv", 0); vrp = snap.get("vrp", 0); skew = snap.get("put_skew", 0)
            hv20 = snap.get("hv20", 0)
            regime = "Rich vol — favor selling premium" if vrp and vrp > 0.02 else \
                     "Cheap vol — favor buying premium" if vrp and vrp < -0.02 else "Neutral VRP"
            ctx["vol"] = (
                f"VOL REGIME ({primary}):\n"
                f"  ATM IV: {iv:.1%} ({pct.get('atm_iv', '?')}th pctile) | HV20: {hv20:.1%}\n"
                f"  VRP: {vrp:+.1%} ({pct.get('vrp', '?')}th pctile) | Skew: {skew:.2f}x\n"
                f"  Regime: {regime}"
            )
        except Exception: pass

    def _positions():
        try:
            import robin_stocks.robinhood as rh
            rh_user, rh_pass = get_secret("ROBINHOOD_USERNAME"), get_secret("ROBINHOOD_PASSWORD")
            if not rh_user or not rh_pass: return
            rh.login(rh_user, rh_pass, store_session=True)
            parts = []
            for pos in rh.account.get_open_stock_positions():
                tk = rh.stocks.get_symbol_by_url(pos.get("instrument", ""))
                qty = float(pos.get("quantity", 0)); avg = float(pos.get("average_buy_price", 0))
                if tk and qty > 0:
                    parts.append(f"  {tk}: {qty:.0f} shares @ ${avg:.2f}")
            for pos in rh.options.get_open_option_positions():
                tk = pos.get("chain_symbol", "")
                if not tk: continue
                qty = float(pos.get("quantity", 0)); strike = float(pos.get("strike_price", 0))
                opt_type = pos.get("option_type", "?"); exp = pos.get("expiration_date", "?")
                avg = float(pos.get("average_price", 0)) / 100
                parts.append(f"  {tk}: {'long' if qty > 0 else 'short'} {abs(qty):.0f}× ${strike} {opt_type} exp {exp} @ ${avg:.2f}")
            if parts:
                ctx["positions"] = "CURRENT POSITIONS (all):\n" + "\n".join(parts)
            profile = rh.profiles.load_portfolio_profile()
            ctx["portfolio"] = f"PORTFOLIO: ${float(profile.get('equity', 0) or 0):,.0f} equity"
        except Exception: pass

    def _signals():
        try:
            from src.signal_engine import compute_composite, get_top_trade_ideas
            comp = compute_composite(primary)
            ideas = get_top_trade_ideas(10)
            parts = []
            if comp:
                parts.append(f"  {primary}: {comp['overall_direction']} {comp['overall_conviction']:.0%} ({comp['n_signals']} sources)")
            for t in ideas:
                if t["ticker"] != primary and t["ticker"] in tickers:
                    parts.append(f"  {t['ticker']}: {t['overall_direction']} {t['overall_conviction']:.0%} ({t['n_signals']} signals)")
            if parts:
                ctx["signals"] = "SIGNAL ENGINE CONSENSUS:\n" + "\n".join(parts)
        except Exception: pass

    def _cross_context():
        try:
            from src.cross_context import build_ai_context
            cross = build_ai_context(primary)
            if cross and len(cross) > 50:
                ctx["cross_intel"] = f"CROSS-PAGE INTELLIGENCE ({primary}):\n{cross[:1200]}"
        except Exception: pass

    def _fundamentals():
        try:
            import yfinance as yf
            tk = yf.Ticker(primary)
            info = tk.info or {}
            parts = []
            for key, label in [("marketCap", "Mkt Cap"), ("trailingPE", "P/E"), ("forwardPE", "Fwd P/E"),
                                ("priceToBook", "P/B"), ("revenueGrowth", "Rev Growth"), ("profitMargins", "Margin"),
                                ("targetMeanPrice", "Analyst Target"), ("recommendationKey", "Rating"),
                                ("shortPercentOfFloat", "Short Float")]:
                val = info.get(key)
                if val is not None:
                    if key == "marketCap":
                        parts.append(f"  {label}: ${val/1e9:.1f}B")
                    elif "Growth" in label or "Margin" in label or "Float" in label:
                        parts.append(f"  {label}: {val*100:.1f}%")
                    elif key == "targetMeanPrice":
                        parts.append(f"  {label}: ${val:.2f}")
                    else:
                        parts.append(f"  {label}: {val}")
            # Earnings
            next_earn = _safe_next_earnings(tk)
            if next_earn:
                parts.append(f"  Next earnings: {next_earn}")
            # Analyst recommendations
            recs = tk.recommendations
            if recs is not None and not recs.empty:
                latest = recs.tail(3)
                for _, row in latest.iterrows():
                    parts.append(f"  Analyst: {row.get('Firm', '?')} → {row.get('To Grade', '?')}")
            if parts:
                ctx["fundamentals"] = f"FUNDAMENTALS ({primary}):\n" + "\n".join(parts)
        except Exception: pass

    def _insider_edgar():
        try:
            from src.edgar import fetch_recent_8k, score_insider_transactions
            from src.data_engine import fetch_insider_transactions
            txns = fetch_insider_transactions(primary, limit=20)
            if txns is not None and not txns.empty:
                score = score_insider_transactions(txns)
                ctx["insider"] = (
                    f"INSIDER ACTIVITY ({primary}):\n"
                    f"  Score: {score.get('score', 0)}/100 | Buys: {score.get('buys', 0)} Sells: {score.get('sells', 0)}\n"
                    f"  Net: {'Bullish bias' if score.get('score', 0) > 60 else 'Bearish bias' if score.get('score', 0) < 40 else 'Neutral'}"
                )
            events = fetch_recent_8k(primary, days=30)
            if events:
                ctx["edgar_8k"] = f"RECENT 8-K FILINGS ({primary}, last 30d):\n" + "\n".join(
                    f"  {e.get('date', '?')}: {e.get('items', '?')} — {e.get('description', '')[:80]}" for e in events[:5]
                )
        except Exception: pass

    def _macro_events():
        try:
            from src.economic_calendar import find_events_near_date, get_next_fomc
            events = find_events_near_date(date.today().isoformat(), window_days=14)
            fomc = get_next_fomc()
            parts = []
            if fomc:
                days = (pd.to_datetime(fomc).date() - date.today()).days
                parts.append(f"  FOMC: {fomc} ({days}d away)")
            for e in (events or [])[:8]:
                parts.append(f"  {e.get('date', '?')}: {e['name']} ({e.get('days_away', '?')}d)")
            if parts:
                ctx["macro_events"] = "UPCOMING MACRO EVENTS (next 14d):\n" + "\n".join(parts)
        except Exception: pass

    def _market_macro():
        """Broad market context: SPY, VIX regime, term structure, treasuries."""
        try:
            import yfinance as yf
            parts = []
            # SPY + QQQ via .history() (fast, reliable)
            for sym, label in [("SPY", "SPY"), ("QQQ", "QQQ")]:
                hist = yf.Ticker(sym).history(period="5d")
                if hist is not None and len(hist) >= 2:
                    price = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2])
                    chg = (price / prev - 1) * 100
                    ret_5d = (price / float(hist["Close"].iloc[0]) - 1) * 100
                    parts.append(f"  {label}: ${price:.2f} ({chg:+.2f}% today, {ret_5d:+.1f}% 5d)")
            # VIX via .history() (NOT .info which is slow/unreliable for indices)
            vix_hist = yf.Ticker("^VIX").history(period="2d")
            vix_price = float(vix_hist["Close"].iloc[-1]) if vix_hist is not None and len(vix_hist) >= 1 else 0
            if vix_price:
                regime = "Low" if vix_price < 15 else "Normal" if vix_price < 20 else "Elevated" if vix_price < 30 else "High" if vix_price < 40 else "Extreme"
                parts.append(f"  VIX: {vix_price:.1f} ({regime})")
                # Term structure
                try:
                    vix3m_hist = yf.Ticker("^VIX3M").history(period="2d")
                    vix3m_price = float(vix3m_hist["Close"].iloc[-1]) if vix3m_hist is not None and len(vix3m_hist) >= 1 else 0
                    if vix3m_price and vix_price:
                        ratio = vix_price / vix3m_price
                        structure = "Backwardation (fear)" if ratio > 1.05 else "Contango (normal)" if ratio < 0.95 else "Flat"
                        parts.append(f"  VIX term structure: {structure} (VIX/VIX3M = {ratio:.2f})")
                except Exception: pass
            # 10Y Treasury yield via .history()
            try:
                tnx_hist = yf.Ticker("^TNX").history(period="2d")
                yield_10y = float(tnx_hist["Close"].iloc[-1]) if tnx_hist is not None and len(tnx_hist) >= 1 else 0
                if yield_10y:
                    parts.append(f"  10Y yield: {yield_10y:.2f}%")
            except Exception: pass
            if parts:
                ctx["market_macro"] = "MARKET ENVIRONMENT:\n" + "\n".join(parts)
        except Exception: pass

    def _news():
        try:
            from src.ai_cache import get_cached_ai
            for offset in range(3):
                dt = datetime.now() - timedelta(hours=offset)
                cached = get_cached_ai(f"market_news_{dt.strftime('%Y%m%d_%H')}")
                if cached:
                    ctx["news"] = f"RECENT NEWS ({offset}h ago):\n{cached[:1000]}"
                    break
        except Exception: pass

    def _track_record():
        try:
            from src.prediction_tracker import get_track_record
            tr = get_track_record("signal_scanner")
            if tr and tr.get("evaluated", 0) > 5:
                ctx["track_record"] = (
                    f"SIGNAL TRACK RECORD:\n"
                    f"  Scanner accuracy: {tr.get('accuracy', 0)*100:.0f}% ({tr['evaluated']} evaluated)"
                )
        except Exception: pass

    def _peers():
        try:
            from src.data_engine import fetch_related_companies, polygon_batch_snapshot, fetch_massive_data
            import numpy as _np
            # Relative strength vs SPY
            parts = []
            try:
                spy_df = fetch_massive_data("SPY", 30)
                tk_df = fetch_massive_data(primary, 30)
                if spy_df is not None and tk_df is not None and not spy_df.empty and not tk_df.empty:
                    spy_ret = (spy_df["Close"].iloc[-1] / spy_df["Close"].iloc[0] - 1) * 100
                    tk_ret = (tk_df["Close"].iloc[-1] / tk_df["Close"].iloc[0] - 1) * 100
                    rel = tk_ret - spy_ret
                    parts.append(f"  vs SPY (30d): {primary} {tk_ret:+.1f}% vs SPY {spy_ret:+.1f}% → {'Outperforming' if rel > 1 else 'Underperforming' if rel < -1 else 'In line'} ({rel:+.1f}%)")
            except Exception: pass

            # Peer tickers
            peers = fetch_related_companies(primary)
            if peers and len(peers) >= 2:
                peer_list = [p for p in peers[:4] if p != primary]
                snaps = polygon_batch_snapshot([primary] + peer_list)
                for tk in peer_list:
                    s = snaps.get(tk)
                    if s:
                        parts.append(f"  {tk}: ${s['price']:.2f} ({s.get('change', 0):+.1f}%)")

            # Sector earnings calendar
            try:
                import yfinance as yf
                check_tickers = [primary] + (tickers[1:4] if len(tickers) > 1 else [])
                for ctk in check_tickers:
                    next_earn = _safe_next_earnings(yf.Ticker(ctk))
                    if next_earn:
                        parts.append(f"  {ctk} earnings: {next_earn}")
            except Exception: pass

            if parts:
                ctx["peers"] = "RELATIVE STRENGTH + PEERS:\n" + "\n".join(parts)
        except Exception: pass

    def _vol_surface_ideas():
        """Pull pre-computed Gemini vol surface trade ideas from cache."""
        try:
            from src.ai_cache import get_cached_ai, build_cache_key_from_metrics
            from src.metrics_store import get_latest_snapshot
            snap = get_latest_snapshot(primary)
            if not snap: return
            # Check if cached trade ideas exist for current vol state
            cache_key = build_cache_key_from_metrics(
                "vol_surface_full_scan", primary,
                spot=ctx.get("_price", 0), iv=snap.get("atm_iv", 0),
                skew=snap.get("put_skew", 0), vrp=snap.get("vrp", 0)
            )
            cached = get_cached_ai(cache_key)
            if cached:
                # Truncate to key trades only (skip preamble)
                ctx["vol_surface_ideas"] = f"PRE-COMPUTED VOL SURFACE TRADE IDEAS ({primary}, Gemini):\n{cached[:1500]}"
        except Exception: pass

    def _options_greeks_context():
        """Compute BS Greeks for ATM options to give Claude concrete numbers."""
        try:
            from src.options_models import bs_greeks, bs_higher_greeks
            price = ctx.get("_price", 0)
            if not price: return
            from src.metrics_store import get_latest_snapshot
            snap = get_latest_snapshot(primary)
            iv = snap.get("atm_iv", 0.3) if snap else 0.3
            # Compute Greeks for ATM call + put at ~30 DTE
            T = 30 / 365
            r = 0.045
            call_g = bs_greeks(price, price, T, r, iv, "call")
            put_g = bs_greeks(price, price, T, r, iv, "put")
            higher = bs_higher_greeks(price, price, T, r, iv, "call")
            ctx["atm_greeks"] = (
                f"ATM GREEKS ({primary}, ~30 DTE, IV={iv:.1%}):\n"
                f"  Call: Δ={call_g['delta']:.3f} Γ={call_g['gamma']:.4f} Θ=${call_g['theta']:.2f}/day V={call_g['vega']:.2f}\n"
                f"  Put:  Δ={put_g['delta']:.3f} Γ={put_g['gamma']:.4f} Θ=${put_g['theta']:.2f}/day V={put_g['vega']:.2f}\n"
                f"  Higher: Vanna={higher.get('vanna', 0):.4f} Charm={higher.get('charm', 0):.4f} Vomma={higher.get('vomma', 0):.4f}"
            )
        except Exception: pass

    # ── Run all fetchers in parallel (12s timeout — skip stragglers) ──
    with concurrent.futures.ThreadPoolExecutor(max_workers=15) as pool:
        futs = [pool.submit(fn) for fn in [
            _technicals, _options_chain, _vol_regime, _positions, _signals,
            _cross_context, _fundamentals, _insider_edgar, _macro_events,
            _market_macro, _news, _track_record, _peers, _vol_surface_ideas,
            _options_greeks_context,
        ]]
        concurrent.futures.wait(futs, timeout=12)

    context = "\n\n".join(v for k, v in ctx.items() if v and not k.startswith("_"))
    sources = [k for k in ctx if not k.startswith("_") and ctx[k]]

    # Cache for reuse
    import time
    _architect_cache[cache_key] = (context, sources, time.time())
    # Evict old entries
    now = time.time()
    for k in list(_architect_cache):
        if now - _architect_cache[k][2] > _ARCHITECT_CACHE_TTL * 2:
            del _architect_cache[k]

    return context, sources

@router.post("/trade-architect")
async def trade_architect(req: TradeArchitectRequest, user: str = Depends(require_admin)):
    """Conversational AI Trade Architect. Admin-only — reads Robinhood positions for context."""
    from src.api_keys import get_secret

    # Determine the user message
    user_msg = req.thesis.strip() if req.thesis.strip() else (
        req.messages[-1].content if req.messages and req.messages[-1].role == "user" else ""
    )
    if not user_msg:
        return {"success": False, "error": "No message provided."}

    # Extract tickers
    tickers = [t.upper() for t in req.tickers if t.strip()]
    if not tickers:
        all_text = user_msg + " " + " ".join(m.content for m in req.messages)
        tickers = _extract_tickers(all_text)
    if not tickers:
        tickers = ["SPY"]
    primary = tickers[0]

    # First call: gather context + compute trades. Follow-ups: reuse.
    if req.context:
        context = req.context
        sources = []
    else:
        context, sources = _gather_architect_context(primary, tickers, req.account_size)

    # Compute structured trades from real chain data
    # Fetch portfolio Greeks for impact analysis
    _port_greeks = {}
    try:
        import robin_stocks.robinhood as rh
        from src.api_keys import get_secret as _gs
        _ru, _rp = _gs("ROBINHOOD_USERNAME"), _gs("ROBINHOOD_PASSWORD")
        if _ru and _rp:
            rh.login(_ru, _rp, store_session=True)
            _profile = rh.profiles.load_portfolio_profile()
            _port_greeks["equity"] = float(_profile.get("equity", 0) or 0)
            # Aggregate option Greeks from open positions
            _total_d, _total_t, _total_g, _total_v = 0.0, 0.0, 0.0, 0.0
            _stock_positions = {}
            for pos in rh.account.get_open_stock_positions():
                _qty = float(pos.get("quantity", 0))
                _total_d += _qty  # stock delta = qty
                _tk = rh.stocks.get_symbol_by_url(pos.get("instrument", ""))
                if _tk: _stock_positions[_tk.upper()] = _qty
            _port_greeks["stock_positions"] = _stock_positions
            # Options Greeks: fetch live market data in parallel (RH positions lack Greeks)
            _opt_positions = rh.options.get_open_option_positions()
            def _fetch_opt_greeks(pos):
                _qty = float(pos.get("quantity", 0))
                _sign = -1 if pos.get("type", "") == "short" else 1
                try:
                    _mark = rh.options.get_option_market_data_by_id(pos.get("option_id", ""))
                    if isinstance(_mark, list): _mark = _mark[0] if _mark else {}
                    return (
                        float(_mark.get("delta", 0) or 0) * 100 * _qty * _sign,
                        float(_mark.get("theta", 0) or 0) * 100 * _qty * _sign,
                        float(_mark.get("gamma", 0) or 0) * 100 * _qty * _sign,
                        float(_mark.get("vega", 0) or 0) * 100 * _qty * _sign,
                    )
                except Exception: return (0, 0, 0, 0)
            from concurrent.futures import ThreadPoolExecutor as _TPE
            with _TPE(max_workers=min(len(_opt_positions), 10)) as _pool:
                for _d, _t, _g, _v in _pool.map(_fetch_opt_greeks, _opt_positions):
                    _total_d += _d; _total_t += _t; _total_g += _g; _total_v += _v
            _port_greeks.update({"delta": _total_d, "theta": _total_t, "gamma": _total_g, "vega": _total_v})
    except Exception:
        pass

    structured_trades = _compute_structured_trades(
        primary, req.account_size, req.risk, req.strategy, user_msg,
        direction_override=req.direction, portfolio_greeks=_port_greeks
    )

    # Format trades for Claude context
    trades_text = ""
    if structured_trades:
        trades_text = "\n\nPRE-COMPUTED TRADES (from real chain data — use these exact numbers):\n"
        for t in structured_trades:
            trades_text += f"\n{t['type'].upper()}: {t['label']}\n"
            for leg in t["legs"]:
                trades_text += f"  {leg['action']} {leg['qty']}× {leg.get('ticker', primary)} "
                if leg["instrument"] == "shares":
                    trades_text += f"shares @ ${leg['price']:.2f}\n"
                else:
                    trades_text += f"${leg.get('strike', 0):.0f} {leg['instrument']} exp {leg.get('exp', '?')} @ ${leg['price']:.2f}\n"
            trades_text += f"  Max profit: ${t['max_profit']:,.0f} | Max risk: ${t['max_risk']:,.0f}"
            if t.get("breakeven"): trades_text += f" | BE: ${t['breakeven']:.2f}"
            if t.get("pop"): trades_text += f" | POP: {t['pop']}%"
            trades_text += f" | R:R: {t['rr_ratio']}x"
            if t.get("risk_pct_of_account"): trades_text += f" | Account risk: {t['risk_pct_of_account']:.1f}%"
            trades_text += "\n"
            g = t.get("greeks", {})
            trades_text += f"  Greeks: Δ={g.get('delta', 0):.1f} Θ=${g.get('theta', 0):.2f}/day"
            if t.get("short_oi") is not None:
                trades_text += f" | OI: short={t['short_oi']} long={t.get('long_oi', 0)}"
            trades_text += "\n"
            if t.get("portfolio_delta_before") is not None:
                trades_text += f"  Portfolio impact: delta {t['portfolio_delta_before']:.0f} → {t['portfolio_delta_after']:.0f} | theta {t['portfolio_theta_before']:.1f} → {t['portfolio_theta_after']:.1f}\n"
            if t.get("account_fit") is not None:
                trades_text += f"  Account fit score: {t['account_fit']}/100"
                if t['account_fit'] < 50: trades_text += " (POOR — increases concentration)"
                elif t['account_fit'] >= 80: trades_text += " (GOOD)"
                trades_text += "\n"
            if t.get("signal_consensus"):
                trades_text += f"  Signal engine: {t['signal_consensus']}\n"
            if t.get("vol_suggestion"):
                trades_text += f"  Vol regime: {t['vol_suggestion']}\n"

    # Build Claude messages
    claude_messages = []
    first_user = f"""TICKERS: {', '.join(tickers)}
ACCOUNT SIZE: ${req.account_size:,.0f}

{context}{trades_text}

USER THESIS: {user_msg}

The user sees the structured trade cards with all numbers. Your job:
1. 3-5 sentences assessing the thesis (data supports? contradicts? relative strength vs SPY?)
2. Factor in the MARKET ENVIRONMENT and UPCOMING MACRO EVENTS — if VIX is elevated, FOMC is imminent, or CPI/NFP is within the trade window, explicitly address how this affects the trade (e.g. "FOMC in 3d adds event risk — consider waiting or widening stops")
3. Name which trade is BEST and why in 1-2 sentences
4. EXIT STRATEGY: profit target % (e.g. "close at 50% of max profit"), stop loss, max days to hold. ALWAYS include this.
5. 2-3 bullet KEY RISKS (include macro event risk if any events fall within the trade timeframe)
6. If OI on legs is thin (under 100), warn about liquidity/slippage
Be terse — the numbers are already displayed, don't repeat them."""

    if req.messages:
        # Conversation mode: rebuild history with context in first message
        for i, m in enumerate(req.messages):
            if i == 0 and m.role == "user":
                # Inject context into first user message
                claude_messages.append({
                    "role": "user",
                    "content": f"TICKERS: {', '.join(tickers)}\nACCOUNT SIZE: ${req.account_size:,.0f}\n\n{context}\n\nUSER THESIS: {m.content}",
                })
            else:
                claude_messages.append({"role": m.role, "content": m.content})
        # Add latest message if not already in history
        if req.thesis.strip() and (not req.messages or req.messages[-1].content != req.thesis.strip()):
            claude_messages.append({"role": "user", "content": req.thesis.strip()})
    else:
        claude_messages = [{"role": "user", "content": first_user}]

    # Call Claude Opus
    try:
        api_key = get_secret("ANTHROPIC_API_KEY")
        if not api_key:
            return {"success": False, "error": "Anthropic API key not configured."}

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-opus-4-6" if req.deep else "claude-sonnet-4-6",
            max_tokens=3000,
            system=_ARCHITECT_SYSTEM,
            messages=claude_messages,
        )
        analysis = response.content[0].text.strip()

        return {
            "success": True,
            "analysis": analysis,
            "trades": structured_trades,
            "tickers": tickers,
            "context": context,
            "context_sources": sources,
        }
    except Exception as e:
        # Fallback to Gemini
        try:
            gemini_key = get_secret("GEMINI_API_KEY")
            if gemini_key:
                from google import genai
                from google.genai import types
                client = genai.Client(api_key=gemini_key)
                # Gemini: flatten to single prompt
                flat = _ARCHITECT_SYSTEM + "\n\n" + "\n\n".join(
                    f"{'USER' if m['role'] == 'user' else 'ASSISTANT'}: {m['content']}"
                    for m in claude_messages
                )
                resp = client.models.generate_content(
                    model="gemini-3.1-pro-preview",
                    contents=flat,
                    config=types.GenerateContentConfig(max_output_tokens=3000, temperature=0.2),
                )
                return {
                    "success": True,
                    "analysis": resp.text.strip(),
                    "trades": structured_trades,
                    "tickers": tickers,
                    "context": context,
                    "context_sources": sources,
                    "model": "gemini-3.1-pro",
                }
        except Exception:
            pass
        return {"success": False, "error": f"AI call failed: {str(e)[:200]}"}
