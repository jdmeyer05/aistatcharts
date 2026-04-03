"""Options analytics endpoints — pricing, Greeks, IV, metrics, surface snapshots, AI trade ideas."""

import logging
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, field_validator
from typing import Optional
from api.deps import get_current_user

logger = logging.getLogger(__name__)
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


# ─── Surface Snapshots (for animation & comparison) ──────────


@router.get("/surface-snapshots/{ticker}")
async def get_surface_snapshots(
    ticker: str,
    days: int = Query(10, ge=1, le=30),
    user: str = Depends(get_current_user),
):
    """Load historical IV surface snapshots from Supabase or local cache."""
    import json, os, glob
    from datetime import date, timedelta

    ticker = ticker.upper()
    snapshots = []

    # Try Supabase first — column is "date" (matches Streamlit schema)
    try:
        from src.db import get_client
        db = get_client()
        if db:
            cutoff = (date.today() - timedelta(days=days)).isoformat()
            result = db.table("iv_surface_snapshots")\
                .select("date, spot, data")\
                .eq("ticker", ticker)\
                .gte("date", cutoff)\
                .order("date", desc=True)\
                .limit(days)\
                .execute()
            if result.data:
                for row in result.data:
                    data = row["data"]
                    if isinstance(data, str):
                        try:
                            data = json.loads(data)
                        except (json.JSONDecodeError, TypeError):
                            data = []
                    elif not isinstance(data, (dict, list)):
                        data = []
                    snapshots.append({
                        "date": row["date"],
                        "spot": row["spot"],
                        "data": data,
                    })
    except Exception as e:
        logger.debug(f"Supabase snapshot load failed: {e}")

    # Fallback to local JSON cache
    if not snapshots:
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                 "data", "iv_surface_cache")
        if os.path.isdir(cache_dir):
            pattern = os.path.join(cache_dir, f"{ticker}_*.json")
            files = sorted(glob.glob(pattern), reverse=True)[:days]
            for fpath in files:
                try:
                    with open(fpath, "r") as f:
                        obj = json.load(f)
                    snapshots.append({
                        "date": obj.get("date", os.path.basename(fpath).split("_", 1)[1].replace(".json", "")),
                        "spot": obj.get("spot", 0),
                        "data": obj.get("data", []),
                    })
                except Exception:
                    continue

    snapshots.sort(key=lambda s: s["date"])
    return {"ticker": ticker, "count": len(snapshots), "snapshots": snapshots}


class SnapshotSaveInput(BaseModel):
    spot: float
    data: list  # [{strike, dte, iv, delta, gamma, type, exp}, ...]


@router.post("/surface-snapshots/{ticker}")
async def save_surface_snapshot(
    ticker: str,
    inp: SnapshotSaveInput,
    user: str = Depends(get_current_user),
):
    """Save today's IV surface snapshot for historical replay."""
    import json, os
    from datetime import date

    ticker = ticker.upper()
    today = date.today().isoformat()

    # Save to Supabase — matches Streamlit schema: column "date", conflict on "user_id,ticker,date"
    saved_db = False
    try:
        from src.db import get_client
        db = get_client()
        if db:
            db.table("iv_surface_snapshots").upsert({
                "user_id": user,
                "ticker": ticker,
                "date": today,
                "spot": inp.spot,
                "data": json.dumps(inp.data) if not isinstance(inp.data, str) else inp.data,
            }, on_conflict="user_id,ticker,date").execute()
            saved_db = True
    except Exception as e:
        logger.debug(f"Supabase snapshot save failed: {e}")

    # Also save to local JSON cache
    try:
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                                 "data", "iv_surface_cache")
        os.makedirs(cache_dir, exist_ok=True)
        fpath = os.path.join(cache_dir, f"{ticker}_{today}.json")
        with open(fpath, "w") as f:
            json.dump({"date": today, "spot": inp.spot, "data": inp.data}, f)
    except Exception:
        pass

    return {"status": "ok", "saved_db": saved_db, "date": today}


# ─── AI Trade Ideas (Gemini proxy) ───────────────────────────


class TradeIdeasInput(BaseModel):
    ticker: str
    context: str
    style: str = "full_scan"
    account_size: Optional[float] = None
    refine_prompt: Optional[str] = None
    previous_response: Optional[str] = None

    @field_validator("context")
    @classmethod
    def validate_context(cls, v: str) -> str:
        if len(v) > 15000:
            raise ValueError("Context too long (max 15000 chars)")
        return v

    @field_validator("refine_prompt")
    @classmethod
    def validate_refine(cls, v: Optional[str]) -> Optional[str]:
        if v and len(v) > 2000:
            raise ValueError("Refine prompt too long (max 2000 chars)")
        return v


@router.post("/ai-trade-ideas")
async def ai_trade_ideas(
    inp: TradeIdeasInput,
    user: str = Depends(get_current_user),
):
    """Generate AI-powered trade ideas from vol surface data using Gemini."""
    from src.ai_cache import get_cached_ai, cache_ai_response, build_cache_key_from_metrics
    from src.ai_validation import ACCURACY_CHECK, VOL_SURFACE_EXPERT_CONTEXT
    from src.api_keys import get_secret
    import re

    ticker = inp.ticker.upper()

    # Parse key metrics from context for cache key
    spot_match = re.search(r"Spot:\s*\$?([\d.]+)", inp.context)
    iv_match = re.search(r"ATM IV.*?:\s*([\d.]+)%", inp.context)
    skew_match = re.search(r"25[Δd] Put Skew.*?:\s*([\d.]+)", inp.context)
    vrp_match = re.search(r"VRP.*?:\s*([+-]?[\d.]+)%", inp.context)

    spot_val = float(spot_match.group(1)) if spot_match else 0
    iv_val = float(iv_match.group(1)) / 100 if iv_match else 0
    skew_val = float(skew_match.group(1)) if skew_match else 0
    vrp_val = float(vrp_match.group(1)) / 100 if vrp_match else 0

    # Check cache (skip if refining)
    cache_key = None
    if not inp.refine_prompt:
        cache_key = build_cache_key_from_metrics(
            f"vol_surface_{inp.style}", ticker,
            spot=spot_val, iv=iv_val, skew=skew_val, vrp=vrp_val
        )
        cached = get_cached_ai(cache_key)
        if cached:
            return {"content": cached, "cached": True, "cost": 0}

    # Build system prompt
    style_instructions = {
        "full_scan": "Give 3-5 trades spanning income, directional, and volatility categories.",
        "income": "Focus on income/theta strategies: credit spreads, iron condors, covered calls, cash-secured puts.",
        "directional": "Focus on directional bets: debit spreads, ratio spreads, risk reversals, outright calls/puts.",
        "volatility": "Focus on volatility trades: straddles, strangles, calendars, vol spreads, butterflies.",
        "hedging": "Focus on hedging: put spreads, collars, tail-risk hedges, portfolio protection.",
    }

    system_prompt = f"""You are a senior options market maker with 20 years of institutional experience.
Analyze this volatility surface and generate specific, actionable trade ideas.

{VOL_SURFACE_EXPERT_CONTEXT}

ANALYSIS FOCUS: {style_instructions.get(inp.style, style_instructions['full_scan'])}

{'ACCOUNT SIZE: $' + f'{inp.account_size:,.0f}. Size positions to risk 2-5% of account per trade.' if inp.account_size else ''}

OUTPUT FORMAT (strict markdown):
## Surface Assessment
2-3 sentences on current vol regime and key observations.

## Trade 1: [Strategy Name]
**Strategy** | **Conviction** (1-10) | **R:R Grade** (A-F)

#### Legs
| Action | Type | Strike | Expiration | Est. Price |
|--------|------|--------|------------|------------|

#### P&L Profile
| Net Credit/Debit | Max Profit | Max Loss | Breakevens | Prob of Profit |
|------------------|------------|----------|------------|----------------|

#### The Edge
Why this trade works given the surface data. Reference specific dislocations, skew, VRP.

#### Risk Flags
Key risks and when to exit.

(Repeat for each trade)

## Portfolio Note
Net delta, gamma, theta, vega if running all trades together.

RULES:
- Only suggest strikes with MEDIUM+ open interest
- Flag unlimited-loss positions prominently
- Reference dislocations (sell rich, buy cheap)
- Use butterfly/risk reversal for tail risk insights
- Flag earnings if within 30 days
- Prices must be realistic given IV data
- Be direct, no disclaimers
- Include Prob of Profit via delta proxy

{ACCURACY_CHECK}"""

    # Call Gemini 3.1 Pro — matches Streamlit pattern: contents=string, types.GenerateContentConfig
    try:
        api_key = get_secret("GEMINI_API_KEY")
        if not api_key:
            return {"content": "Gemini API key not configured.", "cached": False, "cost": 0}

        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)

        if inp.refine_prompt and inp.previous_response:
            # Multi-turn: include previous analysis as context
            full_prompt = (
                f"{system_prompt}\n\n{inp.context}\n\n"
                f"PREVIOUS ANALYSIS:\n{inp.previous_response[:3000]}\n\n"
                f"USER REFINEMENT REQUEST: {inp.refine_prompt}"
            )
        else:
            # Single turn: system prompt + surface context
            full_prompt = f"{system_prompt}\n\n{inp.context}"

        response = client.models.generate_content(
            model="gemini-3.1-pro-preview",
            contents=full_prompt,
            config=types.GenerateContentConfig(max_output_tokens=20000, temperature=0.4),
        )
        content = response.text or ""
        cost = 0.05

        # Cache the response
        if cache_key and content:
            cache_ai_response(
                cache_key, content, model="gemini-3.1-pro-preview",
                source_page="vol_surface", ticker=ticker,
                ttl_hours=2, cost_estimate=cost,
                prompt_summary=f"Trade ideas ({inp.style}) for {ticker}",
            )

        return {"content": content, "cached": False, "cost": cost}

    except Exception as e:  # noqa: E722
        error_msg = str(e).lower()
        if "api_key" in error_msg or "authentication" in error_msg:
            content = "Invalid Gemini API key. Check GEMINI_API_KEY configuration."
        elif "quota" in error_msg or "rate" in error_msg or "429" in error_msg:
            content = "Rate limit exceeded. Try again in 30 seconds."
        elif "404" in error_msg or "not found" in error_msg:
            content = "Model gemini-3.1-pro-preview not available. Check API access."
        else:
            content = f"AI generation failed: {str(e)}"
            logger.error(f"AI trade ideas error for {ticker}: {e}")
        return {"content": content, "cached": False, "cost": 0}


# ─── Vol Landscape (cross-asset scan) ────────────────────────


@router.get("/vol-landscape")
async def vol_landscape_scan(
    user: str = Depends(get_current_user),
):
    """Scan 20 ETFs for cross-asset vol metrics: IV, HV, skew, term structure, VRP, correlations.

    Heavy endpoint (~10-30s first call). Results cached 10 min via bundle cache.
    """
    import json
    import numpy as np
    import pandas as pd

    # Check cache
    try:
        from api.routes.energy import _get_bundle_cache, _set_bundle_cache
        cached = _get_bundle_cache("vol_landscape_scan", ttl_minutes=10)
        if cached:
            return cached
    except Exception:
        pass

    from src.cross_asset_vol import (
        ALL_TICKERS, SCAN_UNIVERSE, get_rfr,
        load_universe_data, compute_cross_asset_metrics,
        interpolate_smile, compute_implied_correlation,
        detect_divergences, compute_correlation_matrix,
        fetch_earnings_dates, compute_benchmark_context,
    )

    rfr = get_rfr()
    ticker_data = load_universe_data(ALL_TICKERS, rfr)

    if len(ticker_data) < 3:
        return {"error": "Could not load enough data", "metrics": [], "count": 0}

    mdf = compute_cross_asset_metrics(ticker_data, rfr)
    if mdf.empty:
        return {"error": "No metrics computed", "metrics": [], "count": 0}

    # Implied correlation — guard against missing SPY or empty sector list
    impl_corr = None
    sector_tickers = [tk for tk in ticker_data if tk in SCAN_UNIVERSE.get("Sectors", {})]
    if "SPY" in mdf["Ticker"].values:
        spy_iv = float(mdf.loc[mdf["Ticker"] == "SPY", "Front_IV"].values[0])
        sector_ivs = [float(mdf.loc[mdf["Ticker"] == tk, "Front_IV"].values[0]) for tk in sector_tickers if tk in mdf["Ticker"].values]
        if spy_iv > 0 and len(sector_ivs) >= 2:
            impl_corr = compute_implied_correlation(spy_iv, sector_ivs)

    # Divergences
    divergences = detect_divergences(mdf)

    # Smile data for heatmap
    moneyness_pts = [0.90, 0.95, 0.98, 1.00, 1.02, 1.05, 1.10]
    smile_data = []
    for tk in mdf["Ticker"].tolist():
        td = ticker_data.get(tk)
        if not td or not td.get("chains") or not td.get("expirations"):
            continue
        try:
            smile = interpolate_smile(td["chains"][td["expirations"][0]], td["spot"], moneyness_pts)
            if smile:
                row = {str(m): (smile.get(m) or 0) * 100 for m in moneyness_pts}
                row["ticker"] = tk
                smile_data.append(row)
        except Exception:
            continue

    # Term structure data
    ts_data = []
    for tk in mdf["Ticker"].tolist():
        td = ticker_data.get(tk)
        if not td or not td.get("chains") or not td.get("expirations"):
            continue
        from src.cross_asset_vol import atm_iv
        row_ivs = []
        for exp in td["expirations"][:5]:
            chain = td["chains"].get(exp)
            if chain is not None:
                iv = atm_iv(chain, td["spot"])
                dte = max((pd.to_datetime(exp) - pd.Timestamp.now()).days, 1)
                row_ivs.append({"dte": dte, "iv": round(iv * 100, 2)})
        if row_ivs:
            ts_data.append({"ticker": tk, "term_structure": row_ivs})

    # Earnings
    try:
        earnings = fetch_earnings_dates(list(ticker_data.keys()))
    except Exception as e:
        logger.warning(f"Earnings fetch failed: {e}")
        earnings = {}

    # Convert mdf to records, handling NaN
    metrics_records = []
    for _, row in mdf.iterrows():
        rec = {}
        for col in mdf.columns:
            val = row[col]
            if isinstance(val, (np.floating, float)):
                rec[col] = None if (np.isnan(val) or np.isinf(val)) else round(float(val), 6)
            elif isinstance(val, (np.integer, int)):
                rec[col] = int(val)
            else:
                rec[col] = str(val) if val is not None else None
        metrics_records.append(rec)

    # Regime classification
    _avg_iv_raw = mdf["Front_IV"].mean()
    avg_iv = float(_avg_iv_raw) if not pd.isna(_avg_iv_raw) else 0.0
    _avg_ivhv_raw = mdf["IV_HV"].dropna().mean()
    avg_ivhv = float(_avg_ivhv_raw) if not pd.isna(_avg_ivhv_raw) else 1.0
    _avg_skew_raw = mdf["Put_Skew"].mean()
    avg_skew = float(_avg_skew_raw) if not pd.isna(_avg_skew_raw) else 1.0
    n_inverted = int((mdf["TS_Slope"] < 0).sum()) if "TS_Slope" in mdf.columns else 0
    n_steep = int((mdf["Put_Skew"] > 1.10).sum()) if "Put_Skew" in mdf.columns else 0

    if avg_ivhv > 1.2:
        regime = "Elevated Vol — Rich Premiums"
        regime_action = "Sell premium. Iron condors, credit spreads."
    elif avg_ivhv < 0.85:
        regime = "Low Vol — Cheap Protection"
        regime_action = "Buy protection. Long puts, tail hedges."
    elif n_inverted >= 3:
        regime = "Event-Driven — Near-Term Fear"
        regime_action = "Calendar spreads. Sell front, buy back."
    elif n_steep > len(mdf) * 0.5:
        regime = "Broad Fear — Steep Skew"
        regime_action = "Sell overpriced put wings."
    else:
        regime = "Normal Conditions"
        regime_action = "No broad signal. Single-name relative value."

    result = {
        "count": len(metrics_records),
        "metrics": metrics_records,
        "smile_data": smile_data,
        "ts_data": ts_data,
        "impl_corr": round(impl_corr, 4) if impl_corr is not None else None,
        "divergences": divergences[:5] if divergences else [],
        "earnings": {k: v for k, v in (earnings or {}).items()},
        "regime": regime,
        "regime_action": regime_action,
        "summary": {
            "avg_iv": round(avg_iv * 100, 2),
            "avg_ivhv": round(avg_ivhv, 3),
            "avg_skew": round(avg_skew, 3),
            "n_inverted": n_inverted,
            "n_steep_skew": n_steep,
            "n_tickers": len(mdf),
        },
    }

    # Cache
    try:
        from api.routes.energy import _set_bundle_cache
        _set_bundle_cache("vol_landscape_scan", result, ttl_minutes=10)
    except Exception:
        pass

    return result
