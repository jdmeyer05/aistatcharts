"""Regression tests for the pure session/date/classification logic.

Every case here is a bug that actually shipped, or a boundary that produced one.
They are deliberately all NETWORK-FREE — the failures worth catching this way
were never about the data being unavailable, they were about correct-looking
output derived from a subtly wrong rule. A wide overnight labelled tight, the
Fed sorted last, a contract list that ages out: none of those look broken.

Run: python -m pytest tests/test_session_logic.py -v
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import unittest.mock as mock
st_mock = mock.MagicMock()
sys.modules.setdefault("streamlit", st_mock)
st_mock.cache_data = lambda **kw: (lambda f: f)

import pandas as pd
import pytest

ET = "America/New_York"


# ── ES contract codes ─────────────────────────────────────────────
# Shipped hardcoded and ending at ESU6, which would have silently dropped the
# newest quarter about six weeks later while still reporting itself complete.

def test_contract_list_matches_the_hardcoded_one_it_replaced():
    from src.es_overnight import _contracts_for
    assert _contracts_for(date(2026, 8, 1)) == [
        "ESU4", "ESZ4", "ESH5", "ESM5", "ESU5", "ESZ5", "ESH6", "ESM6", "ESU6"]


def test_contract_list_rolls_forward():
    from src.es_overnight import _contracts_for
    assert "ESZ6" in _contracts_for(date(2026, 9, 20))
    assert "ESH7" in _contracts_for(date(2026, 12, 1))
    # and keeps roughly two years of depth rather than growing without bound
    assert 8 <= len(_contracts_for(date(2028, 1, 15))) <= 11


def test_contract_list_only_uses_quarterly_codes():
    from src.es_overnight import _contracts_for
    for c in _contracts_for(date(2026, 8, 1)):
        assert c[:2] == "ES" and c[2] in "HMUZ" and c[3].isdigit()


# ── "since the last close" ────────────────────────────────────────
# Shipped as fixed hours-ago buckets, which called Friday's news "earlier" when
# read on Monday morning — 65h old and also the most recent thing that happened.

@pytest.mark.parametrize("now,expected", [
    ("2026-08-01 19:55", "2026-07-31 16:00"),   # Saturday evening -> Friday
    ("2026-08-03 09:25", "2026-07-31 16:00"),   # Monday pre-bell -> across the weekend
    ("2026-08-03 17:00", "2026-08-03 16:00"),   # after today's close -> today
    ("2026-08-05 09:25", "2026-08-04 16:00"),   # midweek -> yesterday
    ("2026-08-05 15:59", "2026-08-04 16:00"),   # a minute before the bell rings out
    ("2026-08-02 20:00", "2026-07-31 16:00"),   # Sunday Globex reopen -> Friday
])
def test_last_cash_close(now, expected):
    from src.es_session import _last_cash_close
    got = _last_cash_close(pd.Timestamp(now, tz=ET))
    assert got == pd.Timestamp(expected, tz=ET)


# ── news tiering ──────────────────────────────────────────────────
# `tier = max(tier, 3)` demoted anything matching the single-name pattern, so a
# Fed headline mentioning shares sorted below a stock tip.

@pytest.mark.parametrize("title,tier", [
    ("Fed decision sends bank shares soaring", 1),   # single-name pattern, still policy
    ("CPI comes in hotter than expected", 1),
    ("Powell signals rate cut in September", 1),
    ("Jobless claims fall to lowest level since mid-May", 1),
    ("Stocks rally as yields fall", 2),
    ("Apple earnings takeaways: Weak forecast", 3),
    ("Linde post-earnings slide is a buying opportunity", 3),
])
def test_news_tiering(title, tier):
    # Calls the production rule, not a copy of it — a test that reimplements
    # the logic passes happily while the shipped version is wrong.
    from src.es_session import _headline_tier
    assert _headline_tier(title) == tier


def test_single_name_noise_is_filtered_before_it_is_tiered():
    from src.es_session import _RELEVANT
    assert not _RELEVANT.search("Nvidia shares jump 8% on AI demand")


# ── Polygon symbol eligibility ────────────────────────────────────
# Index/futures/FX/crypto return 200 with an EMPTY body rather than an error, and
# a bare futures root quotes an unrelated EQUITY (ES -> Eversource).

@pytest.mark.parametrize("ticker,ok", [
    ("SPY", True), ("XLK", True), ("MTUM", True), ("BRK.B", True),
    ("^GSPC", False), ("^VIX", False),
    ("ES=F", False), ("EURUSD=X", False),
    ("BTC-USD", False), ("ETH-USD", False),
    ("", False),
])
def test_polygon_eligible(ticker, ok):
    from src.ohlcv_cache import _polygon_eligible
    assert _polygon_eligible(ticker) is ok


# ── overnight open-position bands ─────────────────────────────────
# The band gates which base rate the live read quotes, so a gap between bands
# means a session silently gets no answer.

def test_open_position_bands_cover_the_whole_range():
    from src.es_overnight import _pos_band
    for x in [0.0, 0.199, 0.2, 0.399, 0.4, 0.6, 0.799, 0.8, 1.0]:
        assert _pos_band(x) is not None, f"{x} fell through every band"


def test_open_position_bands_are_ordered():
    from src.es_overnight import _pos_band
    assert _pos_band(0.05) == "bottom 20%"
    assert _pos_band(0.5) == "middle"
    assert _pos_band(0.95) == "top 20%"


# ── cached panel shape ────────────────────────────────────────────
# The panel is persisted as JSON. A cache written by an older shape must be
# treated as a miss, not fed to the statistics — where it would either raise or,
# worse, be papered over by a `.get()` and quietly change a number.

def _fake_panel_rows(n=150, drop=None):
    from src.es_overnight import _PANEL_COLUMNS
    row = {c: 1.0 for c in _PANEL_COLUMNS}
    row["first_break"] = "high"
    rows = []
    for i in range(n):
        r = dict(row)
        r["session"] = f"2026-01-{(i % 28) + 1:02d}"
        if drop:
            r.pop(drop, None)
        rows.append(r)
    return rows


def test_cached_panel_of_the_current_shape_loads(monkeypatch):
    import src._cache_util as cu
    from datetime import datetime
    import src.es_overnight as eo
    monkeypatch.setattr(cu, "_supabase_get",
                        lambda k: (datetime.utcnow(), {"rows": _fake_panel_rows()}))
    assert eo._load_panel_cache() is not None


def test_cached_panel_missing_a_column_is_a_miss(monkeypatch):
    import src._cache_util as cu
    from datetime import datetime
    import src.es_overnight as eo
    monkeypatch.setattr(cu, "_supabase_get",
                        lambda k: (datetime.utcnow(),
                                   {"rows": _fake_panel_rows(drop="true_gap")}))
    assert eo._load_panel_cache() is None


def test_cached_panel_keeps_its_timezone(monkeypatch):
    """Naive reload vs tz-aware fresh rows meant concat produced duplicates that
    `duplicated()` could not see — 493 + 53 became 546."""
    import src._cache_util as cu
    from datetime import datetime
    import src.es_overnight as eo
    monkeypatch.setattr(cu, "_supabase_get",
                        lambda k: (datetime.utcnow(), {"rows": _fake_panel_rows()}))
    s = eo._load_panel_cache()
    assert s.index.tz is not None and str(s.index.tz) == ET


# ── Dispersion formula ───────────────────────────────────────────────────────
# The implied-correlation denominator was wrong for a long time and no test
# noticed, because every test asserted on shape rather than on value. The
# round-trip below is the one that bites: build an index vol from a KNOWN rho
# via the dispersion identity, then check the function recovers it. The old
# avg(sigma^2)*(1-1/N) denominator fails this for any dispersed sigma set and
# passes it when the sigmas are equal, which is exactly the blind spot.

def _index_vol(sigmas, weights, rho):
    """Forward direction of the dispersion identity."""
    import numpy as np
    s, w = np.array(sigmas, float), np.array(weights, float)
    w = w / w.sum()
    ws = w * s
    own = float(np.sum(ws ** 2))
    cross = float(ws.sum() ** 2 - np.sum(ws ** 2))
    return float(np.sqrt(own + rho * cross))


def test_implied_correlation_round_trips_dispersed_sigmas():
    from src.cross_asset_vol import compute_implied_correlation
    sigmas = [0.335, 0.148, 0.209, 0.271, 0.156, 0.209, 0.280, 0.173, 0.190, 0.226, 0.157]
    weights = [1.0] * len(sigmas)
    for rho in (0.10, 0.32, 0.55, 0.80):
        idx = _index_vol(sigmas, weights, rho)
        got = compute_implied_correlation(idx, sigmas)
        assert abs(got - rho) < 1e-6, f"rho={rho} recovered as {got}"


def test_implied_correlation_round_trips_under_cap_weights():
    from src.cross_asset_vol import compute_implied_correlation
    sigmas = [0.335, 0.148, 0.209, 0.271, 0.156, 0.209, 0.280, 0.173, 0.190, 0.226, 0.157]
    weights = [0.32, 0.13, 0.11, 0.10, 0.09, 0.08, 0.06, 0.05, 0.03, 0.02, 0.01]
    for rho in (0.15, 0.45):
        idx = _index_vol(sigmas, weights, rho)
        got = compute_implied_correlation(idx, sigmas, weights)
        assert abs(got - rho) < 1e-6, f"rho={rho} recovered as {got}"


def test_implied_correlation_is_scale_invariant():
    """Percent and fraction inputs must agree — the pipeline passes fractions
    while the payload prints percent, and the two got mixed once already."""
    from src.cross_asset_vol import compute_implied_correlation
    s = [0.21, 0.25, 0.335, 0.16]
    a = compute_implied_correlation(0.1334, s)
    b = compute_implied_correlation(13.34, [x * 100 for x in s])
    assert a is not None and abs(a - b) < 1e-9


def test_implied_correlation_degrades_without_raising():
    from src.cross_asset_vol import compute_implied_correlation as f
    assert f(0.13, []) is None
    assert f(0.13, [0.2]) is None            # n < 2
    assert f(0.0, [0.2, 0.3]) is None        # no index vol
    assert f(0.13, [0.2, 0.3], [0, 0]) is None   # degenerate weights
    assert f(0.99, [0.2, 0.2, 0.2]) == 1.0   # clamped, not >1
    assert f(0.01, [0.2, 0.3, 0.4]) == 0.0   # clamped, not negative


# ── Skew basis and the parity gate ───────────────────────────────────────────

def _mrow(tk, skew, parity, **kw):
    r = {"Ticker": tk, "Put_Skew": skew, "Parity": parity, "Front_IV": 0.13,
         "TS_Slope": 0.01, "IV_HV": 1.0}
    r.update(kw)
    return r


def test_credit_read_suppressed_when_parity_broken():
    """HYG printed 3.10x off a chain whose ATM put was 2.30x its ATM call. The
    read must withhold rather than publish a fear signal off a stale quote."""
    from src.vol_es_read import es_vol_read
    out = es_vol_read([_mrow("SPY", 1.19, 0.94), _mrow("HYG", 3.10, 2.30)], 0.32, {})
    credit = [r for r in out["reads"] if r["label"] == "Credit vs equity"]
    assert len(credit) == 1
    assert credit[0]["value"] == "not readable today"
    assert "HYG" in credit[0]["note"]


def test_credit_read_published_when_parity_holds():
    from src.vol_es_read import es_vol_read
    out = es_vol_read([_mrow("SPY", 1.19, 0.94), _mrow("HYG", 1.35, 1.05)], 0.32, {})
    credit = [r for r in out["reads"] if r["label"] == "Credit vs equity"]
    assert len(credit) == 1 and credit[0]["value"] != "not readable today"
    assert "1.35" in credit[0]["value"]


def test_credit_read_gates_on_spy_side_too():
    from src.vol_es_read import es_vol_read
    out = es_vol_read([_mrow("SPY", 1.19, 0.60), _mrow("HYG", 1.35, 1.05)], 0.32, {})
    credit = [r for r in out["reads"] if r["label"] == "Credit vs equity"]
    assert credit[0]["value"] == "not readable today" and "SPY" in credit[0]["note"]


def test_missing_parity_does_not_suppress():
    """Absent Parity means the field was never computed, not that it failed."""
    from src.vol_es_read import es_vol_read
    out = es_vol_read([_mrow("SPY", 1.19, None), _mrow("HYG", 1.35, None)], 0.32, {})
    credit = [r for r in out["reads"] if r["label"] == "Credit vs equity"]
    assert credit[0]["value"] != "not readable today"


def test_vol_gap_leads_and_reports_signed_spread():
    """Front_IV is a fraction while avg_sector_iv is already percent — the two
    got mixed once, so this pins the scaling as well as the arithmetic."""
    from src.vol_es_read import es_vol_read
    out = es_vol_read([_mrow("SPY", 1.19, 0.94)], 0.32, {"avg_sector_iv": 21.41})
    gap = [r for r in out["reads"] if r["label"] == "Index vs its parts"]
    assert gap, "vol gap read missing"
    assert "13.0%" in gap[0]["value"], gap[0]["value"]   # 0.13 scaled up
    assert "21.4%" in gap[0]["value"], gap[0]["value"]   # already percent, left alone
    assert "+8.4" in gap[0]["note"], gap[0]["note"]


def test_vol_gap_flags_inverted_spread():
    """Sectors calmer than the index is the unusual direction and must not be
    narrated with the sentence written for the normal one."""
    from src.vol_es_read import es_vol_read
    out = es_vol_read([_mrow("SPY", 1.19, 0.94, Front_IV=0.25)], 0.32,
                      {"avg_sector_iv": 21.41})
    gap = [r for r in out["reads"] if r["label"] == "Index vs its parts"][0]
    assert "-3.6" in gap["note"], gap["note"]
    assert "unusual" in gap["note"], gap["note"]
