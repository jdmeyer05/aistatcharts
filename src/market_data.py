"""Market data helpers — Yahoo Finance, FRED, FINRA, EIA, CFTC.
All free data sources, no paid API keys required (FRED needs free key)."""

import logging
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# YAHOO FINANCE
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_analyst_estimates(ticker: str) -> dict:
    """Fetch analyst consensus estimates, price targets, and key stats."""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        info = t.info or {}

        result = {
            "price_target_mean": info.get("targetMeanPrice"),
            "price_target_high": info.get("targetHighPrice"),
            "price_target_low": info.get("targetLowPrice"),
            "num_analysts": info.get("numberOfAnalystOpinions"),
            "recommendation": info.get("recommendationKey"),
            "forward_eps": info.get("forwardEps"),
            "trailing_eps": info.get("trailingEps"),
            "forward_pe": info.get("forwardPE"),
            "trailing_pe": info.get("trailingPE"),
            "short_pct_float": info.get("shortPercentOfFloat"),
            "insider_pct": info.get("heldPercentInsiders"),
            "institution_pct": info.get("heldPercentInstitutions"),
            "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            "market_cap": info.get("marketCap"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
        }

        # Earnings estimates
        try:
            ee = t.get_earnings_estimate()
            if ee is not None and not ee.empty:
                result["eps_est_current_q"] = ee.iloc[0].get("avg")
                result["eps_est_next_q"] = ee.iloc[1].get("avg") if len(ee) > 1 else None
                result["eps_est_current_y"] = ee.iloc[2].get("avg") if len(ee) > 2 else None
                result["eps_est_next_y"] = ee.iloc[3].get("avg") if len(ee) > 3 else None
        except Exception:
            pass

        # Revenue estimates
        try:
            re_ = t.get_revenue_estimate()
            if re_ is not None and not re_.empty:
                result["rev_est_current_q"] = re_.iloc[0].get("avg")
                result["rev_est_next_q"] = re_.iloc[1].get("avg") if len(re_) > 1 else None
                result["rev_est_current_y"] = re_.iloc[2].get("avg") if len(re_) > 2 else None
                result["rev_growth_current_y"] = re_.iloc[2].get("growth") if len(re_) > 2 else None
        except Exception:
            pass

        return result
    except Exception as e:
        logger.warning(f"Yahoo Finance fetch failed for {ticker}: {e}")
        return {}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_earnings_history(ticker: str) -> pd.DataFrame:
    """Fetch earnings surprise history (actual vs estimate)."""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        eh = t.get_earnings_history()
        if eh is not None and not eh.empty:
            eh = eh.reset_index()
            eh.columns = ["quarter", "actual", "estimate", "difference", "surprise_pct"]
            return eh
    except Exception as e:
        logger.warning(f"Earnings history failed for {ticker}: {e}")
    return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_institutional_holders(ticker: str) -> pd.DataFrame:
    """Fetch top institutional holders."""
    import yfinance as yf

    try:
        t = yf.Ticker(ticker)
        holders = t.institutional_holders
        if holders is not None and not holders.empty:
            return holders
    except Exception as e:
        logger.warning(f"Institutional holders failed for {ticker}: {e}")
    return pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_momentum_data(tickers: list[str]) -> pd.DataFrame:
    """Fetch 1M/3M/6M/12M price momentum for a list of tickers."""
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period="1y")
            if hist.empty or len(hist) < 22:
                continue
            price = hist["Close"].iloc[-1]
            row = {"ticker": ticker, "price": price}
            for label, days in [("1M", 22), ("3M", 66), ("6M", 132), ("12M", 252)]:
                if len(hist) >= days:
                    row[label] = (price / hist["Close"].iloc[-days] - 1) * 100
            rows.append(row)
        except Exception:
            pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_eps_revisions(tickers: list[str]) -> pd.DataFrame:
    """Fetch EPS estimate revision counts (up/down in last 7/30 days)."""
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            rev = yf.Ticker(ticker).get_eps_revisions()
            if rev is not None and not rev.empty:
                # Current year row
                cy = rev.iloc[2] if len(rev) > 2 else rev.iloc[0]
                rows.append({
                    "ticker": ticker,
                    "up_7d": cy.get("upLast7days", 0),
                    "up_30d": cy.get("upLast30days", 0),
                    "down_7d": cy.get("downLast7Days", 0),
                    "down_30d": cy.get("downLast30days", 0),
                    "net_30d": cy.get("upLast30days", 0) - cy.get("downLast30days", 0),
                })
        except Exception:
            pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_insider_summary(tickers: list[str]) -> pd.DataFrame:
    """Fetch insider buy/sell summary for a list of tickers."""
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            txns = t.insider_transactions
            if txns is None or txns.empty:
                continue
            # Last 90 days
            if "Start Date" in txns.columns:
                txns["Start Date"] = pd.to_datetime(txns["Start Date"], errors="coerce")
                cutoff = pd.Timestamp.now() - pd.Timedelta(days=90)
                txns = txns[txns["Start Date"] >= cutoff]
            buys = txns[txns["Text"].str.contains("Purchase|Buy", case=False, na=False)] if "Text" in txns.columns else pd.DataFrame()
            sells = txns[txns["Text"].str.contains("Sale", case=False, na=False)] if "Text" in txns.columns else pd.DataFrame()
            buy_val = buys["Value"].sum() if "Value" in buys.columns and not buys.empty else 0
            sell_val = sells["Value"].sum() if "Value" in sells.columns and not sells.empty else 0
            rows.append({
                "ticker": ticker,
                "buy_count": len(buys),
                "sell_count": len(sells),
                "buy_value": buy_val,
                "sell_value": sell_val,
                "net_value": buy_val - sell_val,
            })
        except Exception:
            pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_energy_valuation_data(tickers: list[str]) -> pd.DataFrame:
    """Fetch valuation, dividend, FCF, and debt data for a list of tickers."""
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info or {}
            mktcap = info.get("marketCap")
            fcf = info.get("freeCashflow")
            rows.append({
                "ticker": ticker,
                "market_cap": mktcap,
                "forward_pe": info.get("forwardPE"),
                "trailing_pe": info.get("trailingPE"),
                "price_to_book": info.get("priceToBook"),
                "ev_ebitda": info.get("enterpriseToEbitda"),
                "dividend_yield": info.get("dividendYield"),
                "dividend_rate": info.get("dividendRate"),
                "payout_ratio": info.get("payoutRatio"),
                "fcf": fcf,
                "fcf_yield": (fcf / mktcap * 100) if fcf and mktcap and mktcap > 0 else None,
                "operating_cf": info.get("operatingCashflow"),
                "total_debt": info.get("totalDebt"),
                "total_cash": info.get("totalCash"),
                "ebitda": info.get("ebitda"),
                "net_debt": (info.get("totalDebt") or 0) - (info.get("totalCash") or 0) if info.get("totalDebt") else None,
                "net_debt_ebitda": ((info.get("totalDebt") or 0) - (info.get("totalCash") or 0)) / info["ebitda"]
                    if info.get("totalDebt") and info.get("ebitda") and info["ebitda"] > 0 else None,
                "beta": info.get("beta"),
                "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
            })
        except Exception as e:
            logger.warning(f"Valuation fetch failed for {ticker}: {e}")
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_energy_earnings_surprises(tickers: list[str]) -> pd.DataFrame:
    """Fetch earnings surprise history for multiple tickers."""
    import yfinance as yf

    rows = []
    for ticker in tickers:
        try:
            eh = yf.Ticker(ticker).get_earnings_history()
            if eh is not None and not eh.empty:
                eh = eh.reset_index()
                for _, r in eh.iterrows():
                    rows.append({
                        "ticker": ticker,
                        "quarter": str(r.iloc[0]),
                        "actual": r.iloc[1],
                        "estimate": r.iloc[2],
                        "surprise_pct": r.iloc[4],
                    })
        except Exception:
            pass
    return pd.DataFrame(rows) if rows else pd.DataFrame()


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_energy_price_history(tickers: list[str], period: str = "2y") -> pd.DataFrame:
    """Fetch price history for correlation analysis."""
    import yfinance as yf

    frames = []
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period=period)
            if not hist.empty:
                s = hist["Close"].rename(ticker)
                frames.append(s)
        except Exception:
            pass
    if frames:
        return pd.concat(frames, axis=1).dropna()
    return pd.DataFrame()


# ─────────────────────────────────────────────
# COMMODITY FUTURES (Yahoo Finance)
# ─────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def fetch_commodity_futures(symbol: str, period: str = "1mo") -> dict | None:
    """Fetch commodity futures price + history from Yahoo Finance.
    Common symbols: NG=F (natgas), CL=F (WTI crude), GC=F (gold), SI=F (silver)."""
    import yfinance as yf
    try:
        hist = yf.Ticker(symbol).history(period=period)
        if hist.empty:
            return None
        return {
            "price": float(hist["Close"].iloc[-1]),
            "date": hist.index[-1],
            "history": hist[["Close"]].rename(columns={"Close": "value"}),
            "source": f"Yahoo Finance ({symbol})",
        }
    except Exception as e:
        logger.warning(f"Commodity futures fetch failed for {symbol}: {e}")
        return None


# ─────────────────────────────────────────────
# STOCKTWITS SENTIMENT
# ─────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_stocktwits_sentiment(symbols: list[str]) -> list:
    """Fetch StockTwits sentiment for a list of symbols using curl_cffi to bypass Cloudflare.
    Returns list of dicts with symbol, messages, bullish, bearish, bull_ratio, signal."""
    try:
        from curl_cffi import requests as cffi_requests
    except ImportError:
        logger.warning("curl_cffi not available, skipping StockTwits")
        return []
    results = []
    for sym in symbols:
        try:
            r = cffi_requests.get(
                f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json?limit=30",
                impersonate="chrome", timeout=10,
            )
            data = r.json()
            msgs = data.get("messages", [])
            bull = sum(1 for m in msgs if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bullish")
            bear = sum(1 for m in msgs if (m.get("entities", {}).get("sentiment") or {}).get("basic") == "Bearish")
            total = len(msgs)
            tagged = bull + bear
            if tagged > 0:
                bull_ratio = bull / tagged * 100
                results.append({
                    "symbol": sym,
                    "messages": total,
                    "bullish": bull,
                    "bearish": bear,
                    "bull_ratio": round(bull_ratio, 0),
                    "signal": "Bullish" if bull_ratio > 60 else "Bearish" if bull_ratio < 40 else "Neutral",
                })
        except Exception as e:
            logger.warning(f"StockTwits fetch failed for {sym}: {e}")
    return results


# ─────────────────────────────────────────────
# POLYMARKET PREDICTION MARKETS
# ─────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_polymarket_odds(slugs: dict[str, str]) -> list:
    """Fetch Polymarket prediction odds for a dict of {slug: label} contracts.
    Returns list of dicts with question, yes_prob, slug."""
    import requests as _req
    import json as _json
    results = []
    for slug, label in slugs.items():
        try:
            r = _req.get(f"https://gamma-api.polymarket.com/markets?slug={slug}", timeout=8)
            data = r.json()
            if data and isinstance(data, list) and data[0].get("outcomePrices"):
                prices = _json.loads(data[0]["outcomePrices"])
                yes_prob = round(float(prices[0]) * 100, 1)
                results.append({"question": label, "yes_prob": yes_prob, "slug": slug})
        except Exception:
            pass
    return results


# ─────────────────────────────────────────────
# OIL TERM STRUCTURE
# ─────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def fetch_oil_term_structure(contracts: list[str] = None, labels: list[str] = None) -> dict:
    """Fetch oil futures term structure for backwardation/contango signal.
    Returns {prices: {label: price}, spread, structure}."""
    from src.data_engine import polygon_snapshot
    if contracts is None:
        contracts = ["CL=F", "CLK26.NYM", "CLM26.NYM", "CLN26.NYM", "CLQ26.NYM", "CLU26.NYM"]
    if labels is None:
        labels = ["Front Month", "May 26", "Jun 26", "Jul 26", "Aug 26", "Sep 26"]
    try:
        prices = {}
        for sym, label in zip(contracts, labels):
            snap = polygon_snapshot(sym)
            if snap and snap.get("price"):
                prices[label] = round(float(snap["price"]), 2)
        if len(prices) >= 2:
            front = list(prices.values())[0]
            back = list(prices.values())[-1]
            spread = round(front - back, 2)
            structure = "backwardation" if spread > 0 else "contango"
            return {"prices": prices, "spread": spread, "structure": structure}
    except Exception as e:
        logger.warning(f"Oil term structure fetch failed: {e}")
    return {}


# ─────────────────────────────────────────────
# ENERGY SPOT PRICES
# ─────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def fetch_energy_spot_prices(symbols: dict[str, tuple[str, str]] = None) -> dict:
    """Fetch spot prices for energy commodities via Polygon snapshots.
    symbols: {ticker: (key, display_name)} — defaults to TTF + Henry Hub."""
    from src.data_engine import polygon_snapshot
    if symbols is None:
        symbols = {"TTF=F": ("ttf", "TTF (European Gas)"), "NG=F": ("henry_hub", "Henry Hub (US Gas)")}
    result = {}
    for sym, (key, name) in symbols.items():
        try:
            snap = polygon_snapshot(sym)
            if snap and snap.get("price") and snap.get("prev_close"):
                price = snap["price"]
                prev = snap["prev_close"]
                chg = (price / prev - 1) * 100 if prev > 0 else 0
                result[key] = {"price": round(price, 2), "change": round(chg, 2), "name": name}
        except Exception:
            pass
    return result


# ─────────────────────────────────────────────
# FRED — MACRO INDICATORS
# ─────────────────────────────────────────────

FRED_SERIES = {
    "DFF": "Fed Funds Rate",
    "DGS10": "10-Year Treasury",
    "DGS2": "2-Year Treasury",
    "T10Y2Y": "10Y-2Y Spread (Yield Curve)",
    "CPIAUCSL": "CPI (All Urban)",
    "UNRATE": "Unemployment Rate",
    "DCOILWTICO": "WTI Crude Oil Price",
    "DCOILBRENTEU": "Brent Crude Oil Price",
    "DHHNGSP": "Henry Hub Natural Gas Price",
    "GDP": "Real GDP",
}


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_fred_series(series_id: str, periods: int = 252) -> pd.DataFrame:
    """Fetch a FRED time series."""
    import requests
    from src.api_keys import get_secret

    key = get_secret("FRED_API_KEY")
    if not key:
        return pd.DataFrame()

    try:
        r = requests.get(
            "https://api.stlouisfed.org/fred/series/observations",
            params={
                "series_id": series_id, "api_key": key,
                "file_type": "json", "sort_order": "desc",
                "limit": str(periods),
            },
            timeout=15,
        )
        if r.status_code != 200:
            return pd.DataFrame()

        obs = r.json().get("observations", [])
        if not obs:
            return pd.DataFrame()

        df = pd.DataFrame(obs)
        df = df[df["value"] != "."]
        df["date"] = pd.to_datetime(df["date"])
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df[["date", "value"]].dropna().sort_values("date")
        return df
    except Exception as e:
        logger.warning(f"FRED fetch failed for {series_id}: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=43200, show_spinner=False)
def fetch_fred_macro_dashboard() -> dict[str, pd.DataFrame]:
    """Fetch key macro indicators from FRED."""
    from src.api_keys import get_secret
    key = get_secret("FRED_API_KEY")
    if not key:
        return {}

    result = {}
    for series_id in ["DFF", "DGS10", "DGS2", "T10Y2Y", "DCOILWTICO", "DHHNGSP", "UNRATE"]:
        df = fetch_fred_series(series_id, periods=252)
        if not df.empty:
            result[series_id] = df
    return result


# ─────────────────────────────────────────────
# EIA — ENERGY DATA
# ─────────────────────────────────────────────

@st.cache_data(ttl=43200, show_spinner=False)
def fetch_eia_series(series_id: str, periods: int = 104) -> pd.DataFrame:
    """Fetch an EIA time series (v2 API)."""
    import requests
    from src.api_keys import get_secret

    key = get_secret("EIA_API_KEY")
    if not key:
        return pd.DataFrame()

    try:
        r = requests.get(
            f"https://api.eia.gov/v2/seriesid/{series_id}",
            params={"api_key": key, "length": str(periods)},
            timeout=15,
        )
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json().get("response", {}).get("data", [])
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data)
        if "period" in df.columns and "value" in df.columns:
            df["date"] = pd.to_datetime(df["period"], errors="coerce")
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df[["date", "value"]].dropna().sort_values("date")
            return df
    except Exception as e:
        logger.warning(f"EIA fetch failed for {series_id}: {e}")
    return pd.DataFrame()


EIA_SERIES = {
    "PET.WCESTUS1.W": "US Crude Oil Inventories (Weekly)",
    "PET.WCRFPUS2.W": "US Crude Oil Production (Weekly)",
    "NG.NW2_EPG0_SWO_R48_BCF.W": "US Natural Gas Storage (Weekly)",
}


# ─────────────────────────────────────────────
# CFTC — COMMITMENTS OF TRADERS
# ─────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def fetch_cftc_cot(commodity: str = "crude_oil", periods: int = 52) -> pd.DataFrame:
    """Fetch CFTC COT time series for a single commodity (52-week chart data).
    Downloads annual CFTC ZIP and filters by commodity.
    For multi-contract AI summaries, use src.gov_data.get_cot_summary() instead."""
    import requests

    # CFTC contract codes
    codes = {
        "crude_oil": "067651",
        "natural_gas": "023651",
        "gold": "088691",
        "sp500": "13874A",
    }
    code = codes.get(commodity, commodity)

    # Try CFTC annual CSV (most recent year)
    try:
        from datetime import date
        year = date.today().year
        url = f"https://www.cftc.gov/files/dea/history/deacot{year}.zip"
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            import io, zipfile
            z = zipfile.ZipFile(io.BytesIO(r.content))
            csv_name = z.namelist()[0]
            df = pd.read_csv(z.open(csv_name))
            # Filter to our commodity
            # Normalize columns (preserve hyphens in date col names)
            df.columns = [c.strip().replace(" ", "_") for c in df.columns]
            name_col = next((c for c in df.columns if "Market" in c and "Name" in c), None)
            if name_col:
                name_map = {
                    "crude_oil": "CRUDE OIL, LIGHT SWEET",
                    "natural_gas": "^HENRY HUB - NEW YORK",
                    "gold": "GOLD.*COMEX",
                }
                name = name_map.get(commodity, commodity.upper())
                df = df[df[name_col].str.contains(name, case=False, na=False)]

            if not df.empty:
                # Handle both underscore and space column names
                df.columns = [c.strip().replace(" ", "_") for c in df.columns]
                date_col = next((c for c in df.columns if "YYYY-MM-DD" in c), None)
                if not date_col:
                    date_col = next((c for c in df.columns if "YYMMDD" in c), None)
                if date_col:
                    df["date"] = pd.to_datetime(df[date_col], errors="coerce")
                else:
                    return pd.DataFrame()
                # Map columns flexibly
                out = df[["date"]].copy()
                import re as _re
                for pattern, new_name in [
                    (r"^Noncommercial.*Long.*\(All\)", "spec_long"),
                    (r"^Noncommercial.*Short.*\(All\)", "spec_short"),
                    (r"^Commercial.*Long.*\(All\)", "comm_long"),
                    (r"^Commercial.*Short.*\(All\)", "comm_short"),
                ]:
                    matched = [c for c in df.columns if _re.search(pattern, c, _re.IGNORECASE)]
                    if matched:
                        out[new_name] = pd.to_numeric(df[matched[0]], errors="coerce")
                out = out.dropna(subset=["date"]).sort_values("date").tail(periods)
                if "spec_long" in out.columns and "spec_short" in out.columns:
                    out["spec_net"] = out["spec_long"] - out["spec_short"]
                if "comm_long" in out.columns and "comm_short" in out.columns:
                    out["comm_net"] = out["comm_long"] - out["comm_short"]
                return out.reset_index(drop=True)
    except Exception as e:
        logger.warning(f"CFTC COT fetch failed for {commodity}: {e}")

    return pd.DataFrame()
