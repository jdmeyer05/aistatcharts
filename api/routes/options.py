"""Options analytics endpoints — pricing, Greeks, IV, metrics."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from api.deps import get_current_user

router = APIRouter()


class PricingInput(BaseModel):
    spot: float
    strike: float
    time_years: float
    rate: float = 0.045
    vol: float
    opt_type: str = "call"  # call or put


@router.post("/price")
async def price_option(inp: PricingInput):
    """Black-Scholes option price."""
    from src.options_models import black_scholes
    price = black_scholes(inp.spot, inp.strike, inp.time_years, inp.rate, inp.vol, inp.opt_type)
    return {"price": round(price, 4)}


@router.post("/greeks")
async def option_greeks(inp: PricingInput):
    """First-order Greeks (delta, gamma, theta, vega, rho)."""
    from src.options_models import bs_greeks
    return bs_greeks(inp.spot, inp.strike, inp.time_years, inp.rate, inp.vol, inp.opt_type)


@router.post("/higher-greeks")
async def higher_greeks(inp: PricingInput):
    """Second/third-order Greeks (vanna, volga, charm, veta, speed, zomma, color, ultima)."""
    from src.options_models import bs_higher_greeks
    return bs_higher_greeks(inp.spot, inp.strike, inp.time_years, inp.rate, inp.vol, inp.opt_type)


@router.post("/implied-vol")
async def implied_vol(
    spot: float, strike: float, time_years: float, market_price: float,
    rate: float = 0.045, opt_type: str = "call",
):
    """Newton-Raphson IV solver."""
    from src.options_models import implied_vol
    iv = implied_vol(market_price, spot, strike, time_years, rate, opt_type)
    return {"implied_vol": round(iv, 6) if iv else None}


@router.get("/metrics/{ticker}")
async def ticker_metrics(
    ticker: str,
    days: int = Query(252, ge=1, le=504),
    user: str = Depends(get_current_user),
):
    """Get historical metrics (ATM IV, HV20, VRP, skew) and percentile ranks."""
    from src.metrics_store import load_history, percentile_ranks_all, get_latest_snapshot
    latest = get_latest_snapshot(ticker.upper())
    pctiles = percentile_ranks_all(ticker.upper())
    history = load_history(ticker.upper(), days)
    return {
        "ticker": ticker,
        "latest": latest,
        "percentiles": pctiles,
        "history_count": len(history) if not history.empty else 0,
    }
