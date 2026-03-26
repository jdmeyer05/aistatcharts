"""Persistence layer for AI analysis history (conflict, regime, etc.).
Handles load/save/staleness for JSON history files."""
import json
import logging
import os
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)


def load_history(filepath: str) -> list:
    """Load analysis history from a JSON file."""
    try:
        if os.path.exists(filepath):
            with open(filepath, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load history from {filepath}: {e}")
    return []


def save_history(filepath: str, history: list, max_entries: int = 48) -> None:
    """Save analysis history, keeping only the last max_entries."""
    history = history[-max_entries:]
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save history to {filepath}: {e}")


def get_latest(filepath: str, stale_hours: float = 1.0) -> tuple:
    """Get the latest entry from history and whether it's stale.

    Returns:
        (latest_entry, is_stale) — latest_entry is None if no history.
    """
    history = load_history(filepath)
    if not history:
        return None, True
    latest = history[-1]
    is_stale = True
    try:
        ts = pd.Timestamp(latest.get("timestamp", ""))
        is_stale = (pd.Timestamp.now() - ts) > pd.Timedelta(hours=stale_hours)
    except Exception:
        pass
    return latest, is_stale


def append_entry(filepath: str, result: dict, max_entries: int = 48) -> None:
    """Append a new analysis result to history."""
    history = load_history(filepath)
    entry = {
        "timestamp": datetime.now().isoformat(),
        "models_used": result.get("models_used", []),
        "blended": result,
    }
    history.append(entry)
    save_history(filepath, history, max_entries)


def compute_model_accuracy(history: list) -> dict:
    """Compute per-model accuracy weights from history.
    Compares each model's prior escalation forecast against actual next-entry score.
    Returns {model_key: weight} where higher = more accurate historically."""
    if len(history) < 3:
        return {}

    model_errors = {}
    for i in range(len(history) - 1):
        entry = history[i]
        blended = entry.get("blended", {})
        assessments = blended.get("escalation_risk", {}).get("model_assessments", [])
        next_blended = history[i + 1].get("blended", {})
        actual_esc = next_blended.get("escalation_risk", {}).get("score", 0)
        if not actual_esc:
            continue
        for ma in assessments:
            mk = ma.get("model_key", "")
            pred_score = ma.get("score", 0)
            if mk and pred_score:
                model_errors.setdefault(mk, []).append(abs(pred_score - actual_esc))

    if not model_errors:
        return {}

    weights = {}
    for mk, errors in model_errors.items():
        avg_err = sum(errors) / len(errors)
        weights[mk] = max(0.5, min(2.0, 1.0 / (avg_err + 0.5)))
    return weights
