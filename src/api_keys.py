"""Centralized API key retrieval. Single source of truth for all secrets."""
import os
import streamlit as st


def get_secret(name: str) -> str | None:
    """Get an API key from environment variables or Streamlit secrets.
    Returns None if not found."""
    key = os.environ.get(name)
    if not key:
        try:
            key = st.secrets.get(name)
        except Exception:
            pass
    return key
