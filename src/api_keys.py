"""Centralized API key retrieval. Single source of truth for all secrets.

Priority: os.environ (always checked first) → st.secrets (Streamlit only).
Works in both Streamlit and non-Streamlit contexts (FastAPI, worker, scripts).
"""
import os


def get_secret(name: str) -> str | None:
    """Get an API key from environment variables or Streamlit secrets.
    Returns None if not found."""
    key = os.environ.get(name)
    if key:
        return key
    try:
        import streamlit as st
        return st.secrets.get(name)
    except Exception:
        return None
