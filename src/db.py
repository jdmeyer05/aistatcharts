"""Shared Supabase client accessor for all data modules.

Returns the initialized client from auth.py, or None if unavailable.
All callers must handle None gracefully (fall back to local JSON).

Works in both Streamlit and non-Streamlit contexts (FastAPI, worker, scripts).
For non-Streamlit: call set_user_id() at startup or pass override to get_user_id().
"""

import os
import logging

logger = logging.getLogger(__name__)
_client = None
_override_user_id = None


def get_client():
    """Get the shared Supabase client. Returns None if not configured."""
    global _client
    if _client is not None:
        return _client

    # Try auth.py first (Streamlit context)
    try:
        from src.auth import init_supabase
        _client = init_supabase()
        if _client is not None:
            return _client
    except Exception:
        pass

    # Direct initialization (FastAPI/worker context)
    try:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if url and key:
            from supabase import create_client
            _client = create_client(url, key)
            return _client
    except Exception as e:
        logger.debug(f"Supabase not available: {e}")

    return None


def set_user_id(user_id: str):
    """Set user ID for non-Streamlit contexts (FastAPI, worker)."""
    global _override_user_id
    _override_user_id = user_id


def get_user_id(override: str = None) -> str:
    """Get the current user ID.

    Priority: explicit override → set_user_id() → st.session_state → 'default'.
    """
    if override:
        return override
    if _override_user_id:
        return _override_user_id
    try:
        import streamlit as st
        user = st.session_state.get("user", {})
        return user.get("id", user.get("email", "default"))
    except Exception:
        return "default"
