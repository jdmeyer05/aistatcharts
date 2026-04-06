"""Historical Metrics Store — daily snapshot of key vol/options metrics per ticker.

Storage: Supabase `metrics_history` table, with JSON file fallback.
Provides percentile rank (252-day) for any metric.
"""

import json
import logging
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import date

logger = logging.getLogger(__name__)

# Local fallback
METRICS_DIR = Path(__file__).parent.parent / "data" / "metrics_history"
METRICS_DIR.mkdir(parents=True, exist_ok=True)
MAX_HISTORY = 504

METRIC_FIELDS = ["atm_iv", "put_skew", "vrp", "pc_ratio", "hv20", "hv60", "iv_hv_ratio", "ts_slope", "spot"]


def _db():
    from src.db import get_client
    return get_client()


def _user_id():
    from src.db import get_user_id
    return get_user_id()


# ─── LOCAL FALLBACK ───────────────────────────────────────────────────────────

def _ticker_file(ticker: str) -> Path:
    safe = ticker.replace(":", "_").replace("/", "_")
    return METRICS_DIR / f"{safe}.json"


def _load_local(ticker: str) -> list[dict]:
    f = _ticker_file(ticker)
    if not f.exists():
        return []
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_local(ticker: str, data: list[dict]) -> None:
    data = sorted(data, key=lambda r: r.get("date", ""))[-MAX_HISTORY:]
    try:
        _ticker_file(ticker).write_text(json.dumps(data), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to save metrics for {ticker}: {e}")


# ─── PUBLIC API ───────────────────────────────────────────────────────────────

def save_snapshot(ticker: str, metrics: dict) -> bool:
    """Save a daily metrics snapshot. Deduplicates by date."""
    today = metrics.get("date", date.today().isoformat())
    ticker = ticker.upper()

    row = {"ticker": ticker, "date": today, "user_id": _user_id()}
    for field in METRIC_FIELDS:
        val = metrics.get(field)
        row[field] = float(val) if val is not None and not (isinstance(val, float) and np.isnan(val)) else None

    db = _db()
    if db:
        try:
            db.table("metrics_history").upsert(row, on_conflict="user_id,ticker,date").execute()
            return True
        except Exception as e:
            logger.warning(f"Supabase metrics save failed: {e}")

    # Local fallback
    history = _load_local(ticker)
    history = [r for r in history if r.get("date") != today]
    history.append(row)
    _save_local(ticker, history)
    return True


def save_batch(metrics_list: list[dict]) -> int:
    """Save snapshots for multiple tickers in a single Supabase upsert."""
    if not metrics_list:
        return 0

    today = date.today().isoformat()
    uid = _user_id()
    rows = []
    for m in metrics_list:
        ticker = (m.get("ticker") or "").upper()
        if not ticker:
            continue
        row = {"ticker": ticker, "date": m.get("date", today), "user_id": uid}
        for field in METRIC_FIELDS:
            val = m.get(field)
            row[field] = float(val) if val is not None and not (isinstance(val, float) and np.isnan(val)) else None
        rows.append(row)

    if not rows:
        return 0

    db = _db()
    if db:
        try:
            db.table("metrics_history").upsert(rows, on_conflict="user_id,ticker,date").execute()
            return len(rows)
        except Exception as e:
            logger.warning(f"Supabase batch metrics save failed: {e}")

    # Local fallback
    for row in rows:
        ticker = row["ticker"]
        history = _load_local(ticker)
        history = [r for r in history if r.get("date") != row["date"]]
        history.append(row)
        _save_local(ticker, history)
    return len(rows)


def load_history(ticker: str, days: int = 252) -> pd.DataFrame:
    """Load historical metrics as a DataFrame."""
    ticker = ticker.upper()

    db = _db()
    if db:
        try:
            result = db.table("metrics_history").select("*")\
                .eq("ticker", ticker).eq("user_id", _user_id())\
                .order("date", desc=True).limit(days).execute()
            rows = result.data or []
            if rows:
                df = pd.DataFrame(rows)
                df["date"] = pd.to_datetime(df["date"])
                return df.sort_values("date").tail(days)
        except Exception as e:
            logger.warning(f"Supabase metrics load failed: {e}")

    # Local fallback
    data = _load_local(ticker)
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data).tail(days)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


def percentile_rank(ticker: str, metric: str, current_value: float = None,
                     lookback: int = 252) -> float | None:
    """Percentile rank (0-100) of a metric within its history."""
    df = load_history(ticker, lookback)
    if df.empty or metric not in df.columns or len(df) < 20:
        return None
    values = df[metric].dropna().values
    if len(values) < 20:
        return None
    if current_value is None:
        current_value = values[-1]
    return float(np.sum(values <= current_value) / len(values) * 100)


def percentile_ranks_all(ticker: str, lookback: int = 252) -> dict[str, float | None]:
    """Percentile ranks for ALL metrics. Uses materialized view if available."""
    ticker = ticker.upper()

    # Try fast path: materialized view (pre-computed in Postgres)
    db = _db()
    if db:
        try:
            result = db.table("metrics_percentiles").select("*")\
                .eq("ticker", ticker).limit(1).execute()
            if result.data:
                r = result.data[0]
                return {
                    "atm_iv": r.get("atm_iv_pctile"),
                    "put_skew": r.get("put_skew_pctile"),
                    "vrp": r.get("vrp_pctile"),
                    "iv_hv_ratio": r.get("iv_hv_pctile"),
                    "hv20": r.get("hv20_pctile"),
                    "pc_ratio": None, "hv60": None, "ts_slope": None, "spot": None,
                }
        except Exception:
            pass  # View may not exist yet, fall through to Python calc

    # Slow path: compute in Python
    result = {}
    for field in METRIC_FIELDS:
        result[field] = percentile_rank(ticker, field, lookback=lookback)
    return result


def get_metric_timeseries(ticker: str, metric: str, days: int = 252) -> pd.Series:
    """Single metric as a time series."""
    df = load_history(ticker, days)
    if df.empty or metric not in df.columns:
        return pd.Series(dtype=float)
    return df.set_index("date")[metric].dropna()


def get_latest_snapshot(ticker: str) -> dict | None:
    """Most recent snapshot for a ticker."""
    df = load_history(ticker, 1)
    if df.empty:
        return None
    return df.iloc[-1].to_dict()


def get_coverage() -> dict:
    """Summary of stored data."""
    db = _db()
    if db:
        try:
            result = db.rpc("get_metrics_coverage").execute()
            if result.data:
                return {r["ticker"]: r for r in result.data}
        except Exception:
            pass

    # Local fallback
    result = {}
    for f in METRICS_DIR.glob("*.json"):
        ticker = f.stem.replace("_", ":")
        data = _load_local(ticker)
        if data:
            result[ticker] = {
                "n_days": len(data),
                "first_date": data[0].get("date", ""),
                "last_date": data[-1].get("date", ""),
            }
    return result


def auto_save_from_page(df: pd.DataFrame) -> int:
    """Convenience for pages. Maps DataFrame columns to snapshot schema."""
    if df is None or df.empty:
        return 0

    today_str = date.today().isoformat()
    col_map = {}
    for col in df.columns:
        cl = col.lower().replace(" ", "").replace("_", "")
        if "ticker" in cl or "symbol" in cl:
            col_map["ticker"] = col
        elif "frontiv" in cl or "atmiv" in cl or cl == "iv":
            col_map["atm_iv"] = col
        elif "putskew" in cl or "skew" in cl:
            col_map["put_skew"] = col
        elif "vrp" in cl:
            col_map["vrp"] = col
        elif "pcratio" in cl or "pc" in cl or "putcall" in cl:
            col_map["pc_ratio"] = col
        elif "hv20" in cl or "rv20" in cl:
            col_map["hv20"] = col
        elif "hv60" in cl or "rv60" in cl:
            col_map["hv60"] = col
        elif "ivhv" in cl or "iv/hv" in cl:
            col_map["iv_hv_ratio"] = col
        elif "tsslope" in cl or "termslope" in cl or "slope" in cl:
            col_map["ts_slope"] = col
        elif "spot" in cl or "price" in cl:
            col_map["spot"] = col

    if "ticker" not in col_map:
        return 0

    snapshots = []
    for _, row in df.iterrows():
        ticker = str(row[col_map["ticker"]]).strip().upper()
        if not ticker:
            continue
        snapshot = {"ticker": ticker, "date": today_str}
        for field in METRIC_FIELDS:
            src_col = col_map.get(field)
            if src_col and src_col in row.index:
                val = row[src_col]
                try:
                    val = float(val)
                    if not np.isnan(val):
                        snapshot[field] = val
                except (ValueError, TypeError):
                    pass
        snapshots.append(snapshot)

    return save_batch(snapshots)
