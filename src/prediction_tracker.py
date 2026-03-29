"""Prediction tracking system — records predictions and evaluates against actual outcomes.

Storage: Supabase `predictions` table, with JSON file fallback.

Every AI score, ML forecast, and signal ranking gets saved with a timestamp.
30/60/90 days later, we fetch the actual price and compute whether the prediction was right.

Usage:
    from src.prediction_tracker import record_prediction, evaluate_pending, get_track_record
    record_prediction(source="stock_analysis", ticker="AAPL",
                       prediction={"direction": "Buy", "score": 7.5}, spot=230.50)
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

PREDICTIONS_DIR = Path(__file__).parent.parent / "data" / "predictions"
PREDICTIONS_FILE = PREDICTIONS_DIR / "predictions.json"
MAX_PREDICTIONS = 5000


def _db():
    from src.db import get_client
    return get_client()


def _user_id():
    from src.db import get_user_id
    return get_user_id()


def _load_local() -> list[dict]:
    if not PREDICTIONS_FILE.exists():
        return []
    try:
        with open(PREDICTIONS_FILE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_local(predictions: list[dict]):
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)
    if len(predictions) > MAX_PREDICTIONS:
        predictions = predictions[-MAX_PREDICTIONS:]
    with open(PREDICTIONS_FILE, "w") as f:
        json.dump(predictions, f, indent=2, default=str)


def record_prediction(source: str, ticker: str, prediction: dict, spot: float,
                       metadata: dict = None) -> str:
    """Record a new prediction. Returns prediction ID."""
    pred_id = str(uuid.uuid4())[:8]
    now = datetime.now()
    recent_cutoff = (now - timedelta(hours=1)).isoformat()

    db = _db()
    if db:
        try:
            # Dedup check
            existing = db.table("predictions").select("id")\
                .eq("source", source).eq("ticker", ticker)\
                .eq("user_id", _user_id())\
                .gte("created_at", recent_cutoff).limit(1).execute()
            if existing.data:
                return ""

            db.table("predictions").insert({
                "id": pred_id, "user_id": _user_id(),
                "source": source, "ticker": ticker,
                "prediction": json.dumps(prediction),
                "spot_at_prediction": spot,
                "metadata": json.dumps(metadata or {}),
                "outcomes": json.dumps({}),
                "evaluated_days": [],
            }).execute()
            logger.info(f"Prediction recorded (Supabase): {source}/{ticker} @ ${spot:.2f}")
            return pred_id
        except Exception as e:
            logger.warning(f"Supabase prediction insert failed: {e}")

    # Local fallback
    entry = {
        "id": pred_id, "timestamp": now.isoformat(),
        "source": source, "ticker": ticker,
        "prediction": prediction, "spot_at_prediction": spot,
        "metadata": metadata or {}, "outcomes": {}, "evaluated_days": [],
    }
    predictions = _load_local()
    duplicate = any(
        p["source"] == source and p["ticker"] == ticker and p.get("timestamp", "") > recent_cutoff
        for p in predictions
    )
    if duplicate:
        return ""
    predictions.append(entry)
    _save_local(predictions)
    logger.info(f"Prediction recorded (local): {source}/{ticker} @ ${spot:.2f}")
    return pred_id


def _get_all_predictions() -> list[dict]:
    """Get all predictions from Supabase or local."""
    db = _db()
    if db:
        try:
            result = db.table("predictions").select("*")\
                .eq("user_id", _user_id())\
                .order("created_at", desc=True).limit(MAX_PREDICTIONS).execute()
            rows = result.data or []
            for r in rows:
                for jf in ("prediction", "metadata", "outcomes"):
                    if isinstance(r.get(jf), str):
                        try:
                            r[jf] = json.loads(r[jf])
                        except Exception:
                            r[jf] = {}
                r.setdefault("timestamp", r.get("created_at", ""))
                r.setdefault("evaluated_days", [])
            return rows
        except Exception as e:
            logger.warning(f"Supabase predictions read failed: {e}")
    return _load_local()


def evaluate_pending():
    """Evaluate predictions that are old enough to have outcomes."""
    predictions = _get_all_predictions()
    if not predictions:
        return

    now = datetime.now()
    for pred in predictions:
        ts_str = pred.get("timestamp") or pred.get("created_at", "")
        if not ts_str:
            continue
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00").split("+")[0])
        age_days = (now - ts).days
        spot = pred.get("spot_at_prediction", 0)
        if spot <= 0:
            continue

        ticker = pred["ticker"]
        evaluated = pred.get("evaluated_days", [])
        outcomes = pred.get("outcomes", {})
        changed = False

        for horizon in [30, 60, 90]:
            if horizon in evaluated or str(horizon) in evaluated:
                continue
            if age_days < horizon:
                continue

            try:
                target_date = ts + timedelta(days=horizon)
                import yfinance as yf
                hist = yf.download(ticker,
                                    start=(target_date - timedelta(days=5)).strftime("%Y-%m-%d"),
                                    end=(target_date + timedelta(days=5)).strftime("%Y-%m-%d"),
                                    progress=False)
                if hist is not None and not hist.empty:
                    actual_price = float(hist["Close"].iloc[-1])
                    actual_return = (actual_price / spot - 1) * 100

                    direction = pred.get("prediction", {}).get("direction", "").lower()
                    correct = None
                    if direction in ("buy", "bullish", "strong buy", "long", "bull"):
                        correct = actual_return > 0
                    elif direction in ("sell", "bearish", "strong sell", "short", "bear"):
                        correct = actual_return < 0

                    outcomes[f"{horizon}d"] = {
                        "price": round(actual_price, 2),
                        "return_pct": round(actual_return, 2),
                        "date": target_date.strftime("%Y-%m-%d"),
                        "correct": correct,
                    }
                    evaluated.append(horizon)
                    changed = True
            except Exception as e:
                logger.warning(f"Could not evaluate {ticker} at T+{horizon}d: {e}")

        if changed:
            db = _db()
            if db:
                try:
                    db.table("predictions").update({
                        "outcomes": json.dumps(outcomes),
                        "evaluated_days": evaluated,
                    }).eq("id", pred["id"]).execute()
                except Exception:
                    pass
            else:
                pred["outcomes"] = outcomes
                pred["evaluated_days"] = evaluated

    # Save local if using fallback
    if not _db():
        _save_local(predictions)


def get_track_record(source: str = None, ticker: str = None,
                      horizon: int = 30) -> dict:
    """Compute accuracy statistics for predictions."""
    predictions = _get_all_predictions()

    filtered = predictions
    if source:
        filtered = [p for p in filtered if p.get("source") == source]
    if ticker:
        filtered = [p for p in filtered if p.get("ticker") == ticker]

    horizon_key = f"{horizon}d"
    evaluated = [p for p in filtered if horizon_key in p.get("outcomes", {})]
    correct = [p for p in evaluated if p["outcomes"][horizon_key].get("correct") is True]
    incorrect = [p for p in evaluated if p["outcomes"][horizon_key].get("correct") is False]

    actual_returns = [p["outcomes"][horizon_key]["return_pct"] for p in evaluated
                       if "return_pct" in p["outcomes"][horizon_key]]

    predicted_returns = []
    for p in evaluated:
        pred = p.get("prediction", {})
        spot = p.get("spot_at_prediction", 0)
        target = pred.get("target") or pred.get("target_price") or pred.get("base_target")
        if target and spot and spot > 0:
            predicted_returns.append((target / spot - 1) * 100)

    n_directional = len(correct) + len(incorrect)

    return {
        "total_predictions": len(filtered), "evaluated": len(evaluated),
        "with_direction": n_directional, "correct": len(correct),
        "accuracy": len(correct) / n_directional if n_directional > 0 else None,
        "avg_actual_return": sum(actual_returns) / len(actual_returns) if actual_returns else None,
        "avg_predicted_return": sum(predicted_returns) / len(predicted_returns) if predicted_returns else None,
        "horizon_days": horizon, "source": source,
    }


def get_recent_predictions(source: str = None, limit: int = 20) -> list[dict]:
    """Get the most recent predictions."""
    predictions = _get_all_predictions()
    if source:
        predictions = [p for p in predictions if p.get("source") == source]
    return predictions[-limit:]


def get_all_sources() -> list[str]:
    """Get list of all prediction sources."""
    predictions = _get_all_predictions()
    return sorted(set(p.get("source", "") for p in predictions if p.get("source")))
