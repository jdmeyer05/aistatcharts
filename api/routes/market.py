"""Market data endpoints — prices, snapshots, options chains."""

from fastapi import APIRouter, Depends, Query
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
