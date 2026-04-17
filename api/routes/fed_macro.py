"""Fed & Macro Drivers — sentiment, balance sheet, COT, OECD CLI, next FOMC.

FRED series go through the existing `/api/market/fred-batch` endpoint. This
module covers the third-party data that needs server-side API calls.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends

from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


# StockTwits macro symbols (mirrors Streamlit default set)
STOCKTWITS_MACRO_SYMBOLS = ["SPY", "QQQ", "TLT", "USO", "GLD", "DIA", "IWM", "VIX"]

# Polymarket macro slugs (mirrors Streamlit default set)
POLYMARKET_MACRO_SLUGS: Dict[str, str] = {
    "us-recession-by-end-of-2026": "US Recession by End of 2026",
    "will-the-iranian-regime-fall-by-the-end-of-2026": "Iranian Regime Falls by End of 2026",
    "will-the-us-invade-iran-before-2027": "US Invades Iran Before 2027",
    "us-iran-nuclear-deal-before-2027": "US-Iran Nuclear Deal Before 2027",
    "will-bitcoin-hit-1m-before-gta-vi-872": "Bitcoin Hits $1M",
    "will-usdt-market-cap-hit-200b-before-2027": "USDT Market Cap $200B",
    "microstrategy-sells-any-bitcoin-by-december-31-2026": "MicroStrategy Sells BTC by 2027",
    "trump-eliminates-capital-gains-tax-on-crypto-before-2027": "Trump Eliminates Crypto Cap Gains Tax",
    "will-china-invade-taiwan-before-2027": "China Invades Taiwan by 2027",
    "china-x-india-military-clash-by-december-31-2026": "China-India Military Clash by 2027",
    "will-openai-launch-a-new-consumer-hardware-product-by-december-31-2026": "OpenAI Consumer Hardware by 2027",
}


# ═══════════════════════════════════════════════
# SENTIMENT (StockTwits + Polymarket)
# ═══════════════════════════════════════════════

@router.get("/sentiment")
async def sentiment(user: str = Depends(get_current_user)):
    """Return StockTwits retail sentiment + Polymarket prediction odds."""
    from src.market_data import fetch_stocktwits_sentiment, fetch_polymarket_odds

    try:
        st_data = fetch_stocktwits_sentiment(STOCKTWITS_MACRO_SYMBOLS)
    except Exception as e:
        logger.warning(f"StockTwits fetch failed: {e}")
        st_data = []

    try:
        pm_data = fetch_polymarket_odds(POLYMARKET_MACRO_SLUGS)
    except Exception as e:
        logger.warning(f"Polymarket fetch failed: {e}")
        pm_data = []

    return {
        "stocktwits": st_data or [],
        "polymarket": pm_data or [],
    }


# ═══════════════════════════════════════════════
# FED BALANCE SHEET + LIQUIDITY
# ═══════════════════════════════════════════════

@router.get("/balance-sheet")
async def balance_sheet(user: str = Depends(get_current_user)):
    """Fed balance sheet series + net liquidity snapshot."""
    from src.macro_data import fetch_fed_balance_sheet, get_fed_liquidity_snapshot
    import pandas as pd

    try:
        df = fetch_fed_balance_sheet()
    except Exception as e:
        logger.warning(f"fetch_fed_balance_sheet failed: {e}")
        df = None

    try:
        snap = get_fed_liquidity_snapshot() or {}
    except Exception as e:
        logger.warning(f"get_fed_liquidity_snapshot failed: {e}")
        snap = {}

    series: Dict[str, List] = {}
    dates: List[str] = []
    if df is not None and not df.empty:
        dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in df.index]
        for col in df.columns:
            series[str(col)] = [(float(v) if pd.notna(v) else None) for v in df[col].values]

    # Coerce numpy scalars to native Python types (and NaN → None) for JSON
    import math
    def _coerce(v):
        try:
            import numpy as _np
            if isinstance(v, (_np.bool_,)):
                return bool(v)
            if isinstance(v, (_np.integer,)):
                return int(v)
            if isinstance(v, (_np.floating,)):
                v = float(v)
        except Exception:
            pass
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v

    snap_clean = {str(k): _coerce(v) for k, v in snap.items()}

    return {
        "series": series,
        "dates": dates,
        "snapshot": snap_clean,
    }


# ═══════════════════════════════════════════════
# COT POSITIONING
# ═══════════════════════════════════════════════

@router.get("/cot")
async def cot(user: str = Depends(get_current_user)):
    """Hedge fund / managed money positioning from CFTC COT."""
    from src.macro_data import get_cot_positioning_snapshot
    try:
        snap = get_cot_positioning_snapshot()
    except Exception as e:
        logger.warning(f"get_cot_positioning_snapshot failed: {e}")
        snap = None
    return {"positioning": snap or {}}


# ═══════════════════════════════════════════════
# OECD CLI (leading indicators)
# ═══════════════════════════════════════════════

@router.get("/oecd-cli")
async def oecd_cli(user: str = Depends(get_current_user)):
    """OECD Composite Leading Indicators for major economies."""
    from src.macro_data import fetch_oecd_cli
    import pandas as pd
    try:
        df = fetch_oecd_cli(["USA", "GBR", "DEU", "JPN", "CHN", "OECD"])
    except Exception as e:
        logger.warning(f"fetch_oecd_cli failed: {e}")
        df = None

    if df is None or df.empty:
        return {"dates": [], "series": {}}

    dates = [pd.Timestamp(d).strftime("%Y-%m-%d") for d in df.index]
    series: Dict[str, List] = {}
    for col in df.columns:
        series[str(col)] = [(float(v) if pd.notna(v) else None) for v in df[col].values]
    return {"dates": dates, "series": series}


# ═══════════════════════════════════════════════
# NEXT FOMC MEETING
# ═══════════════════════════════════════════════

@router.get("/next-fomc")
async def next_fomc(user: str = Depends(get_current_user)):
    """Return the next FOMC meeting date (ISO string) if known."""
    from src.economic_calendar import get_next_fomc
    try:
        dt = get_next_fomc()
        if dt is None:
            return {"date": None}
        import pandas as pd
        if hasattr(dt, "strftime"):
            return {"date": dt.strftime("%Y-%m-%d")}
        return {"date": pd.Timestamp(dt).strftime("%Y-%m-%d")}
    except Exception as e:
        logger.warning(f"get_next_fomc failed: {e}")
        return {"date": None, "error": str(e)}
