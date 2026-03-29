"""Shared Supabase client accessor for all data modules.

Returns the initialized client from auth.py, or None if unavailable.
All callers must handle None gracefully (fall back to local JSON).
"""

import logging

logger = logging.getLogger(__name__)
_client = None


def get_client():
    """Get the shared Supabase client. Returns None if not configured."""
    global _client
    if _client is not None:
        return _client
    try:
        from src.auth import init_supabase
        _client = init_supabase()
        return _client
    except Exception as e:
        logger.debug(f"Supabase not available: {e}")
        return None


def get_user_id() -> str:
    """Get the current user ID from session state, or 'default'."""
    try:
        import streamlit as st
        user = st.session_state.get("user", {})
        return user.get("id", user.get("email", "default"))
    except Exception:
        return "default"
