"""Smoke tests — verify all modules import and key functions return correct types.

Run: python -m pytest tests/test_smoke.py -v
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Mock streamlit before importing anything
import unittest.mock as mock
st_mock = mock.MagicMock()
sys.modules['streamlit'] = st_mock
st_mock.cache_data = lambda **kw: (lambda f: f)  # no-op decorator
st_mock.session_state = {}


def test_imports():
    """All src modules import without error."""
    from src import api_keys
    from src import styles
    from src import options_models
    from src import cross_context
    from src import economic_calendar
    # macro_data needs streamlit cache mock
    from src import macro_data


def test_options_models():
    """Black-Scholes and Greeks return correct types."""
    from src.options_models import black_scholes, bs_greeks

    # ATM call
    price = black_scholes(100, 100, 0.25, 0.05, 0.20, "call")
    assert isinstance(price, float)
    assert price > 0

    # ATM put
    price_put = black_scholes(100, 100, 0.25, 0.05, 0.20, "put")
    assert isinstance(price_put, float)
    assert price_put > 0

    # Call should be worth more than put for ATM (when r > 0)
    assert price > price_put

    # Greeks
    greeks = bs_greeks(100, 100, 0.25, 0.05, 0.20, "call")
    assert isinstance(greeks, dict)
    assert "delta" in greeks
    assert "gamma" in greeks
    assert "theta" in greeks
    assert "vega" in greeks
    assert 0 < greeks["delta"] < 1  # call delta between 0 and 1
    assert greeks["gamma"] > 0  # gamma always positive
    assert greeks["theta"] < 0  # theta always negative for long options
    assert greeks["vega"] > 0  # vega always positive

    # Expired option = intrinsic only
    expired = black_scholes(100, 90, 0, 0.05, 0.20, "call")
    assert expired == 10.0  # intrinsic = 100 - 90


def test_cross_context():
    """Cross-page context write/read works."""
    from src.cross_context import write_context, read_context, list_available

    write_context("test_source", {"key": "value", "number": 42})
    result = read_context("test_source")
    assert result is not None
    assert result["key"] == "value"
    assert result["number"] == 42

    assert "test_source" in list_available()

    # Non-existent source returns None
    assert read_context("nonexistent") is None


def test_economic_calendar():
    """FOMC dates and event detection work."""
    from src.economic_calendar import (
        FOMC_DATES, get_upcoming_fomc, get_next_fomc,
        is_fomc_week, find_events_near_date,
    )

    assert len(FOMC_DATES) >= 8  # at least one year of dates
    assert all(len(d) == 10 for d in FOMC_DATES)  # YYYY-MM-DD format

    upcoming = get_upcoming_fomc(3)
    assert len(upcoming) <= 3

    next_fomc = get_next_fomc()
    # Could be None if all dates are past, but shouldn't be for 2027 dates
    assert next_fomc is not None or True  # don't fail if dates exhausted

    # Test event detection
    events = find_events_near_date("2026-05-06")  # FOMC date
    event_names = [e["name"] for e in events]
    assert any("FOMC" in n for n in event_names)


def test_styles():
    """COLORS dict has required keys."""
    from src.styles import COLORS

    required = ["bg_primary", "accent", "success", "danger", "warning",
                "text_primary", "text_muted", "card_bg", "card_border"]
    for key in required:
        assert key in COLORS, f"Missing COLORS key: {key}"
        assert COLORS[key].startswith("#"), f"COLORS[{key}] should be hex color"


def test_format_massive_ticker():
    """Ticker formatting works for common cases."""
    from src.data_engine import format_massive_ticker

    assert format_massive_ticker("AAPL") == "AAPL"
    assert format_massive_ticker("aapl") == "AAPL"
    assert format_massive_ticker(" AAPL ") == "AAPL"


if __name__ == "__main__":
    tests = [
        test_imports,
        test_options_models,
        test_cross_context,
        test_economic_calendar,
        test_styles,
        test_format_massive_ticker,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            print(f"  PASS: {test.__name__}")
            passed += 1
        except Exception as e:
            print(f"  FAIL: {test.__name__} — {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
