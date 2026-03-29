"""User Preferences — persistent settings via Supabase.

Stores per-user preferences (active ticker, watchlist, chart settings, etc.)
so they survive page refreshes, restarts, and work across devices.

Usage:
    from src.user_prefs import save_pref, load_pref, save_prefs, load_all_prefs

    # Single value
    save_pref("active_ticker", "SPY")
    ticker = load_pref("active_ticker", "SPY")

    # Bulk
    save_prefs({"heatmap_list": "Sectors", "heatmap_period": "1D"})
"""

import json
import logging
from datetime import datetime

import streamlit as st

logger = logging.getLogger(__name__)

_SESSION_KEY = "_user_prefs_cache"


def _db():
    try:
        from src.db import get_client
        return get_client()
    except Exception:
        return None


def _user_id():
    try:
        from src.db import get_user_id
        return get_user_id()
    except Exception:
        return "default"


def _get_cache() -> dict:
    """Get the in-session preference cache."""
    if _SESSION_KEY not in st.session_state:
        st.session_state[_SESSION_KEY] = {}
    return st.session_state[_SESSION_KEY]


def load_all_prefs() -> dict:
    """Load all preferences from Supabase into session cache. Call once on page load."""
    cache = _get_cache()
    if cache.get("_loaded"):
        return cache

    db = _db()
    if db:
        try:
            result = db.table("user_preferences").select("key, value")\
                .eq("user_id", _user_id()).execute()
            for row in (result.data or []):
                cache[row["key"]] = row["value"]
            cache["_loaded"] = True
        except Exception as e:
            logger.debug(f"Prefs load failed: {e}")
            cache["_loaded"] = True

    return cache


def load_pref(key: str, default=None):
    """Load a single preference. Returns default if not set."""
    cache = load_all_prefs()
    val = cache.get(key)
    if val is None:
        return default
    # JSONB wraps primitives — unwrap if needed
    if isinstance(val, dict) and "_v" in val:
        return val["_v"]
    return val


def save_pref(key: str, value) -> None:
    """Save a single preference. Writes to session cache + Supabase."""
    # Wrap primitives for JSONB storage
    if not isinstance(value, (dict, list)):
        db_value = {"_v": value}
    else:
        db_value = value

    # Update session cache immediately
    cache = _get_cache()
    cache[key] = db_value

    # Persist to Supabase
    db = _db()
    if db:
        try:
            db.table("user_preferences").upsert({
                "user_id": _user_id(),
                "key": key,
                "value": db_value,
                "updated_at": datetime.now().isoformat(),
            }, on_conflict="user_id,key").execute()
        except Exception as e:
            logger.debug(f"Pref save failed for {key}: {e}")


def save_prefs(prefs: dict) -> None:
    """Bulk save multiple preferences."""
    for key, value in prefs.items():
        save_pref(key, value)


def load_pref_widget(key: str, default=None):
    """Load preference and also set it in session_state for Streamlit widget binding."""
    val = load_pref(key, default)
    if val is not None and key not in st.session_state:
        st.session_state[key] = val
    return val
