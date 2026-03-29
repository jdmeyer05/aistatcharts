"""Unified Signal Engine — aggregates structured signals from every analysis page.

Each page writes a signal: {source, ticker, direction, conviction, vol_view, reasoning}.
The engine combines them with source-specific weighting into a composite conviction score.

Storage: Supabase `signals` table, with JSON file fallback.
"""

import json
import logging
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

import streamlit as st

logger = logging.getLogger(__name__)

# Local fallback
SIGNALS_DIR = Path(__file__).parent.parent / "data" / "signals"
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
SIGNALS_FILE = SIGNALS_DIR / "signals.json"
MAX_SIGNALS = 2000

SOURCE_WEIGHTS = {
    "ml_predictor": 1.5, "rl_trading": 1.4, "signal_scanner": 1.3,
    "stock_analysis": 1.2, "scenario_analysis": 1.1, "vol_surface": 1.1,
    "options_flow": 1.0, "market_expectations": 1.0, "correlation": 0.9,
    "tech_screener": 0.8,
}

VALID_DIRECTIONS = {"bull", "bear", "neutral"}
VALID_VOL_VIEWS = {"long_vol", "short_vol", "neutral"}


# ─── STORAGE LAYER ────────────────────────────────────────────────────────────

def _db():
    from src.db import get_client
    return get_client()


def _user_id():
    from src.db import get_user_id
    return get_user_id()


def _load_local() -> list[dict]:
    if SIGNALS_FILE.exists():
        try:
            return json.loads(SIGNALS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save_local(signals: list[dict]) -> None:
    signals = sorted(signals, key=lambda s: s.get("timestamp", ""))[-MAX_SIGNALS:]
    try:
        SIGNALS_FILE.write_text(json.dumps(signals, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save signals locally: {e}")


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def write_signal(source: str, ticker: str, direction: str, conviction: float,
                 vol_view: str = "neutral", reasoning: str = "") -> None:
    """Write a structured signal from any page."""
    if direction not in VALID_DIRECTIONS:
        direction = "neutral"
    if vol_view not in VALID_VOL_VIEWS:
        vol_view = "neutral"
    conviction = max(0.0, min(1.0, float(conviction)))
    now = datetime.now()
    ticker = ticker.upper()

    db = _db()
    if db:
        try:
            # Delete old signal from same source+ticker within 1 hour
            cutoff = (now - timedelta(hours=1)).isoformat()
            db.table("signals").delete().eq("source", source).eq("ticker", ticker)\
                .eq("user_id", _user_id()).gte("created_at", cutoff).execute()

            db.table("signals").insert({
                "user_id": _user_id(),
                "source": source, "ticker": ticker, "direction": direction,
                "conviction": conviction, "vol_view": vol_view,
                "reasoning": reasoning[:500],
            }).execute()
            return
        except Exception as e:
            logger.warning(f"Supabase signal write failed, falling back to local: {e}")

    # Local fallback
    signal = {
        "source": source, "ticker": ticker, "direction": direction,
        "conviction": conviction, "vol_view": vol_view,
        "reasoning": reasoning[:500], "timestamp": now.isoformat(),
    }
    signals = _load_local()
    cutoff = (now - timedelta(hours=1)).isoformat()
    signals = [s for s in signals if not (
        s.get("source") == source and s.get("ticker") == ticker and s.get("timestamp", "") > cutoff
    )]
    signals.append(signal)
    _save_local(signals)


def get_signals(ticker: str = None, max_age_hours: float = 24) -> list[dict]:
    """Get all active signals, optionally filtered by ticker."""
    cutoff = (datetime.now() - timedelta(hours=max_age_hours)).isoformat()

    db = _db()
    if db:
        try:
            q = db.table("signals").select("*").eq("user_id", _user_id())\
                .gte("created_at", cutoff).order("created_at", desc=True)
            if ticker:
                q = q.eq("ticker", ticker.upper())
            result = q.limit(500).execute()
            rows = result.data or []
            # Normalize column names for compatibility
            return [{
                "source": r["source"], "ticker": r["ticker"], "direction": r["direction"],
                "conviction": r["conviction"], "vol_view": r.get("vol_view", "neutral"),
                "reasoning": r.get("reasoning", ""), "timestamp": r.get("created_at", ""),
            } for r in rows]
        except Exception as e:
            logger.warning(f"Supabase signal read failed: {e}")

    # Local fallback
    signals = _load_local()
    signals = [s for s in signals if s.get("timestamp", "") > cutoff]
    if ticker:
        ticker = ticker.upper()
        signals = [s for s in signals if s.get("ticker") == ticker]
    return signals


def _direction_score(direction: str) -> float:
    return {"bull": 1.0, "bear": -1.0, "neutral": 0.0}.get(direction, 0.0)


def _vol_score(vol_view: str) -> float:
    return {"long_vol": 1.0, "short_vol": -1.0, "neutral": 0.0}.get(vol_view, 0.0)


def compute_composite(ticker: str) -> dict | None:
    """Combine all signals for a ticker into a weighted composite."""
    signals = get_signals(ticker)
    if not signals:
        return None

    weighted_dir = 0.0
    weighted_vol = 0.0
    total_weight = 0.0
    dir_scores = []

    for s in signals:
        w = SOURCE_WEIGHTS.get(s["source"], 1.0) * s["conviction"]
        d = _direction_score(s["direction"])
        v = _vol_score(s["vol_view"])
        weighted_dir += d * w
        weighted_vol += v * w
        total_weight += w
        dir_scores.append(d)

    if total_weight == 0:
        return None

    avg_dir = weighted_dir / total_weight
    avg_vol = weighted_vol / total_weight
    agreement = 1.0 - np.std(dir_scores) if len(dir_scores) > 1 else 1.0
    overall_dir = "bull" if avg_dir > 0.2 else ("bear" if avg_dir < -0.2 else "neutral")
    vol_regime = "long_vol" if avg_vol > 0.2 else ("short_vol" if avg_vol < -0.2 else "neutral")
    overall_conviction = min(1.0, abs(avg_dir) * agreement)

    return {
        "ticker": ticker.upper(), "overall_direction": overall_dir,
        "overall_conviction": round(overall_conviction, 2),
        "direction_score": round(avg_dir, 3), "vol_regime": vol_regime,
        "vol_score": round(avg_vol, 3), "n_signals": len(signals),
        "signal_agreement": round(agreement, 2), "signals": signals,
        "updated_at": datetime.now().isoformat(),
    }


def get_top_trade_ideas(n: int = 5) -> list[dict]:
    """Top N tickers ranked by conviction * agreement.
    Uses SQL view if available for speed."""
    # Try fast path: SQL view
    db = _db()
    if db:
        try:
            result = db.table("signal_composites").select("*")\
                .order("avg_conviction", desc=True).limit(n).execute()
            if result.data:
                return [{
                    "ticker": r["ticker"],
                    "overall_direction": r.get("overall_direction", "neutral"),
                    "overall_conviction": float(r.get("avg_conviction", 0)),
                    "direction_score": float(r.get("direction_score", 0)),
                    "n_signals": r.get("n_signals", 0),
                    "signal_agreement": 1.0,
                    "strength": float(r.get("avg_conviction", 0)),
                    "signals": [],
                    "updated_at": "",
                } for r in result.data]
        except Exception:
            pass  # View may not exist, fall through

    # Slow path: Python computation
    all_signals = get_signals()
    tickers = set(s["ticker"] for s in all_signals)
    composites = []
    for t in tickers:
        comp = compute_composite(t)
        if comp and comp["n_signals"] >= 2:
            comp["strength"] = comp["overall_conviction"] * comp["signal_agreement"]
            composites.append(comp)
    composites.sort(key=lambda c: c["strength"], reverse=True)
    return composites[:n]


def get_signal_summary() -> dict:
    """Portfolio-level signal summary."""
    all_signals = get_signals()
    tickers = set(s["ticker"] for s in all_signals)
    bulls, bears, neutrals = [], [], []
    convictions = []
    for t in tickers:
        comp = compute_composite(t)
        if comp:
            convictions.append(comp["overall_conviction"])
            if comp["overall_direction"] == "bull":
                bulls.append(t)
            elif comp["overall_direction"] == "bear":
                bears.append(t)
            else:
                neutrals.append(t)
    return {
        "n_tickers": len(tickers), "n_bullish": len(bulls),
        "n_bearish": len(bears), "n_neutral": len(neutrals),
        "avg_conviction": round(float(np.mean(convictions)), 2) if convictions else 0,
        "top_bulls": bulls[:3], "top_bears": bears[:3],
    }
