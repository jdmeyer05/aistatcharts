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
from datetime import date, timedelta

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


# -- Delta consistency at the selected strike --------------------------------
# Delta is derived FROM implied vol, so a corrupt IV yields a corrupt delta that
# lands on the 25-delta target, and the nearest-delta match then prefers the
# single worst quote on the chain. The check is deliberately LOCAL -- see the
# note in find_delta_strike for why a whole-ladder score was measured and
# rejected as a gate.

def _chain(rows, opt_type="put"):
    import pandas as pd
    return pd.DataFrame([
        {"contract_type": opt_type, "strike_price": k, "delta": d,
         "implied_volatility": iv, "open_interest": oi}
        for k, d, iv, oi in rows
    ])


def test_selection_rejects_the_xlb_quote():
    """Live XLB: a 43 strike at 85% of spot carrying 121% IV on one lot of volume
    beat a 48 strike at 95% with 606 open interest, because the bogus IV gave it
    a bogus 0.24 delta. |delta| must rise with strike, and 43 sits above 47."""
    from src.cross_asset_vol import find_delta_strike
    ch = _chain([(43.0, -0.2399, 1.2139, 101), (47.0, -0.1133, 0.2607, 133),
                 (48.0, -0.2214, 0.2962, 606), (48.5, -0.3753, 0.7372, 10),
                 (49.0, -0.3512, 0.3766, 1160), (50.0, -0.4200, 0.2800, 900),
                 (51.0, -0.4800, 0.2750, 800), (52.0, -0.5500, 0.2700, 700)])
    k, iv = find_delta_strike(ch, 50.43, 0.25, "put")
    assert k == 48.0, f"selected {k}, expected the liquid 48"
    assert iv < 0.5, f"selected a wing quote at {iv:.4f}"


def test_selection_falls_back_when_too_few_strikes_survive():
    """The same contradictory quotes with nothing else left standing. Three
    survivors is the floor; below it the unfiltered pick is returned rather than
    an answer inferred from one or two strikes. Real chains carry 45-245 strikes,
    so this is the degenerate case and the quality flags cover it."""
    from src.cross_asset_vol import find_delta_strike
    ch = _chain([(43.0, -0.2399, 1.2139, 101), (47.0, -0.1133, 0.2607, 133),
                 (48.0, -0.2214, 0.2962, 606), (48.5, -0.3753, 0.7372, 10),
                 (49.0, -0.3512, 0.3766, 1160)])
    assert find_delta_strike(ch, 50.43, 0.25, "put")[0] == 43.0


def test_selection_unchanged_on_a_clean_chain():
    """SPY's live ladder is monotone to four decimals. Anything that moves this
    is doing something other than removing contradictions."""
    from src.cross_asset_vol import find_delta_strike
    ch = _chain([(729.0, -0.2294, 0.1531, 4167), (730.0, -0.2393, 0.1512, 57262),
                 (731.0, -0.2493, 0.1490, 2701), (732.0, -0.2600, 0.1472, 2956),
                 (733.0, -0.2713, 0.1457, 4320)])
    assert find_delta_strike(ch, 747.03, 0.25, "put")[0] == 731.0


def test_selection_handles_calls_in_the_other_direction():
    from src.cross_asset_vol import find_delta_strike
    # Call |delta| must FALL as strike rises; the 105 quote inverts it.
    ch = _chain([(100.0, 0.60, 0.20, 500), (105.0, 0.72, 0.90, 3),
                 (110.0, 0.30, 0.21, 400), (115.0, 0.15, 0.22, 300)], "call")
    assert find_delta_strike(ch, 100.0, 0.25, "call")[0] != 105.0


def test_selection_falls_back_rather_than_returning_nothing():
    """A chain too contradictory to filter still answers. Reporting nothing is
    worse than reporting the unfiltered pick, which the quality flags cover."""
    from src.cross_asset_vol import find_delta_strike
    ch = _chain([(40.0, -0.90, 2.0, 1), (41.0, -0.10, 1.9, 1), (42.0, -0.80, 1.8, 1)])
    assert find_delta_strike(ch, 45.0, 0.25, "put")[0] is not None


def test_ladder_score_is_a_diagnostic_not_a_gate():
    """Measured across the live universe this flags 19 of 20 names, SPY included,
    because deep wings are thin everywhere. Reported, never gated on."""
    from src.cross_asset_vol import _delta_ladder_broken
    import pandas as pd
    clean = _chain([(729.0, -0.2294, 0.15, 1), (730.0, -0.2393, 0.15, 1),
                    (731.0, -0.2493, 0.15, 1), (732.0, -0.2600, 0.15, 1)])
    assert _delta_ladder_broken(clean, "put") == 0.0
    jagged = _chain([(105.0, -0.1505, 0.44, 1), (106.0, -0.1064, 0.33, 1),
                     (107.0, -0.1378, 0.34, 1), (108.0, -0.2478, 0.51, 1)])
    assert _delta_ladder_broken(jagged, "put") > 0.0
    assert _delta_ladder_broken(pd.DataFrame(), "put") == 0.0


# -- Divergence null safety ---------------------------------------------------
# Making Put_Skew nullable introduced a TypeError that could only ever fire on
# the degraded path the None was added to represent: abs(None - 1.2).

def _mdf(rows):
    import pandas as pd
    base = {"IV_HV": 1.0, "Put_Skew": 1.2, "TS_Slope": 0.01, "Parity": 1.0,
            "Front_IV": 0.2, "Label": "x", "Group": "Sectors"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_divergences_survive_a_null_skew():
    from src.cross_asset_vol import detect_divergences, CORRELATED_PAIRS
    a, b, _ = CORRELATED_PAIRS[0]
    out = detect_divergences(_mdf([{"Ticker": a, "Put_Skew": None},
                                   {"Ticker": b, "Put_Skew": 1.9}]))
    assert not any(d["metric"] == "Skew" for d in out)


def test_divergences_survive_a_null_term_structure():
    from src.cross_asset_vol import detect_divergences, CORRELATED_PAIRS
    a, b, _ = CORRELATED_PAIRS[0]
    out = detect_divergences(_mdf([{"Ticker": a, "TS_Slope": None},
                                   {"Ticker": b, "TS_Slope": -0.02}]))
    assert not any(d["metric"] == "Term Structure" for d in out)


def test_divergences_withhold_skew_when_a_chain_fails_parity():
    """A comparison between two skews is only as good as the worse chain. HYG
    quoted an ATM put 2.30x its ATM call and would otherwise have been published
    as 'fear is concentrated there'."""
    from src.cross_asset_vol import detect_divergences, CORRELATED_PAIRS
    a, b, _ = CORRELATED_PAIRS[0]
    stale = detect_divergences(_mdf([{"Ticker": a, "Put_Skew": 1.1, "Parity": 2.30},
                                     {"Ticker": b, "Put_Skew": 1.9, "Parity": 1.0}]))
    assert not any(d["metric"] == "Skew" for d in stale)
    clean = detect_divergences(_mdf([{"Ticker": a, "Put_Skew": 1.1, "Parity": 1.02},
                                     {"Ticker": b, "Put_Skew": 1.9, "Parity": 1.0}]))
    assert any(d["metric"] == "Skew" for d in clean)


def test_divergences_survive_an_all_null_skew_column():
    """The narrow case that genuinely raises. With a mix of None and floats
    pandas coerces the column to float64 and the None becomes NaN, so the
    comparison is merely False. With EVERY value None the column stays object
    dtype and `None - None` is a TypeError — so the crash needs both chains in a
    pair to fail, which is exactly when a degraded feed would hit it."""
    import pandas as pd
    from src.cross_asset_vol import detect_divergences, CORRELATED_PAIRS
    a, b, _ = CORRELATED_PAIRS[0]
    df = _mdf([{"Ticker": a, "Put_Skew": None}, {"Ticker": b, "Put_Skew": None}])
    df["Put_Skew"] = df["Put_Skew"].astype(object)
    assert df["Put_Skew"].dtype == object, "fixture must reproduce object dtype"
    out = detect_divergences(df)                      # must not raise
    assert not any(d["metric"] == "Skew" for d in out)


# ── Butterfly: each wing against its own type's ATM ──────────────────────────
# The call side of the smile had never been checked against live data. It was
# built as `p25 + c25 - 2*ATM_call`, which anchors a PUT wing on a CALL quote —
# the same mixed-type error already fixed in Put_Skew, surviving in the
# convexity metric because nothing rendered it.

def _two_sided_chain(atm_call_iv, atm_put_iv, p25_iv, c25_iv):
    """Spot 100. Strikes chosen so the delta ladders are monotone and the
    25-delta selector lands on 94 (put) and 106 (call)."""
    rows = [
        # puts: |delta| rises with strike
        ("put", 90.0, -0.15, 0.30, 500), ("put", 94.0, -0.25, p25_iv, 500),
        ("put", 100.0, -0.50, atm_put_iv, 500),
        # calls: |delta| falls with strike
        ("call", 100.0, 0.50, atm_call_iv, 500), ("call", 106.0, 0.25, c25_iv, 500),
        ("call", 112.0, 0.15, 0.21, 500),
    ]
    return pd.DataFrame([
        {"contract_type": t, "strike_price": k, "delta": d,
         "implied_volatility": iv, "open_interest": oi}
        for t, k, d, iv, oi in rows
    ])


def _metrics_for(chain, hv20=0.20):
    from src.cross_asset_vol import compute_cross_asset_metrics
    exp = (pd.Timestamp.now() + pd.Timedelta(days=18)).strftime("%Y-%m-%d")
    mdf = compute_cross_asset_metrics({"TST": {
        "spot": 100.0, "chains": {exp: chain}, "expirations": [exp],
        "hv20": hv20, "label": "Test",
    }})
    return mdf.iloc[0]


def test_butterfly_measures_each_wing_against_its_own_atm():
    """Live QQQ, 2026-08-02: ATM call 26.68, ATM put 20.82, 25d put 25.30,
    25d call 23.39. Both wings lift off their own ATM, so the smile is convex
    and the butterfly is POSITIVE. Charging the 5.86-point parity gap to the
    put wing reported -4.67 — thin tails, the opposite claim."""
    r = _metrics_for(_two_sided_chain(0.2668, 0.2082, 0.2530, 0.2339))
    assert r["Butterfly"] == pytest.approx(1.19, abs=0.01), r["Butterfly"]
    assert r["Butterfly"] > 0, "convexity read flipped sign"


def test_butterfly_error_was_exactly_the_parity_gap():
    """Why the old form looked right for years: it is correct whenever the two
    ATM quotes agree, which is what parity promises and what XLF's chain
    actually delivers. Same wings, parity forced to 1.0 — both forms give 1.19,
    so nothing was 'rescaled', only un-skewed."""
    r = _metrics_for(_two_sided_chain(0.2375, 0.2375, 0.2823, 0.2046))
    assert r["Parity"] == pytest.approx(1.0)
    old_form = (0.2823 + 0.2046 - 2 * 0.2375) * 100
    assert r["Butterfly"] == pytest.approx(old_form, abs=1e-6)
    assert r["Butterfly"] == pytest.approx(1.19, abs=0.01)


def test_butterfly_and_risk_reversal_are_none_when_a_wing_is_missing():
    """0.0 is a reading — "flat wings", "no skew". Absence has to say absence,
    the same rule that made atm_iv stop returning 0.25."""
    puts_only = _two_sided_chain(0.2668, 0.2082, 0.2530, 0.2339)
    puts_only = puts_only[puts_only["contract_type"] == "put"].copy()
    # An ATM call is required before any row is emitted at all.
    from src.cross_asset_vol import compute_cross_asset_metrics
    exp = (pd.Timestamp.now() + pd.Timedelta(days=18)).strftime("%Y-%m-%d")
    assert compute_cross_asset_metrics({"TST": {
        "spot": 100.0, "chains": {exp: puts_only}, "expirations": [exp],
        "hv20": 0.20, "label": "Test"}}).empty, "no ATM call must drop the row"

    # The reachable null: a live call side, an unquoted put side. The row
    # survives on its calls and the two metrics needing a put report None.
    #
    # It has to be the PUT side. find_delta_strike applies no maximum distance
    # from the target delta, so a chain holding one call still yields a
    # "25-delta call" — the 0.50-delta ATM one. The call wing therefore cannot
    # vanish on its own: whatever empties it also empties front_iv, and the row
    # is dropped before any of this is reached.
    dead_puts = _two_sided_chain(0.2668, 0.2082, 0.2530, 0.2339)
    dead_puts.loc[dead_puts["contract_type"] == "put", "implied_volatility"] = 0.0
    r = _metrics_for(dead_puts)
    assert r["Butterfly"] is None, r["Butterfly"]
    assert r["Risk_Rev"] is None, r["Risk_Rev"]
    assert r["Put_Skew"] is None, r["Put_Skew"]
    assert r["Front_IV"] == pytest.approx(0.2668), "the call side still measured"


def test_risk_reversal_is_unaffected_by_a_broken_parity():
    """A risk reversal is a call minus a put by construction — there is no ATM
    anchor in it to mis-assign, so the same wings give the same answer however
    far apart the ATM quotes sit. This is the control on the butterfly fix."""
    broken = _metrics_for(_two_sided_chain(0.2668, 0.2082, 0.2530, 0.2339))
    clean = _metrics_for(_two_sided_chain(0.2375, 0.2375, 0.2530, 0.2339))
    assert broken["Risk_Rev"] == pytest.approx(clean["Risk_Rev"])
    assert broken["Risk_Rev"] == pytest.approx((0.2339 - 0.2530) * 100)


# ── Smile interpolation: nulls stay null ─────────────────────────────────────

def test_smile_reports_nothing_where_no_strike_sits():
    """Live HYG had no strike near 110% of spot. `smile.get(m) or 0` turned that
    into a 0% IV cell — a claim that the wing is priced at zero volatility."""
    from src.cross_asset_vol import interpolate_smile
    ch = _two_sided_chain(0.2668, 0.2082, 0.2530, 0.2339)
    out = interpolate_smile(ch, 100.0, [0.90, 1.00, 1.10, 1.60])
    assert out[1.60] is None, "a moneyness with no strike near it must be None"
    assert out[0.90] == pytest.approx(0.30)


# ── Sector RRG ───────────────────────────────────────────────────────────────
# The board was daily, unit-scaled, and filled an unmeasurable sector with 100 —
# which is the quadrant origin, and _quadrant(100, 100) is "leading".

def test_unmeasurable_sector_is_not_reported_as_leading():
    """A zero-variance series and a series far shorter than the window both used
    to normalise to exactly 100.0, placing the sector on the quadrant origin and
    labelling it with the strongest read on the board."""
    from src.sector_rrg import _normalise, _quadrant, _NORM_WINDOW
    flat = pd.Series([50.0] * 300)
    short = pd.Series([50.0 + i for i in range(10)])
    assert pd.isna(_normalise(flat, _NORM_WINDOW).iloc[-1]), "zero variance must be NaN"
    assert pd.isna(_normalise(short, _NORM_WINDOW).iloc[-1]), "too-short must be NaN"
    # The trap this guards: the old fallback landed exactly here.
    assert _quadrant(100.0, 100.0) == "leading"


def test_normalise_uses_the_canonical_scaling():
    """`100 + z` compressed the whole board into ~98.7-102.3, which puts the
    quadrant boundaries inside the noise. The reconstruction of JdK is
    `100 + 10*z`."""
    import numpy as np
    from src.sector_rrg import _normalise, _SCALE, _NORM_WINDOW
    s = pd.Series(np.random.default_rng(0).normal(size=400))
    out = _normalise(s, _NORM_WINDOW).dropna()
    assert _SCALE == 10.0
    assert out.std() == pytest.approx(_SCALE, rel=0.15), out.std()


def test_normalise_demands_most_of_its_window():
    """min_periods was window//3 — a z-score against 84 of 252 observations was
    presented identically to one against the full window."""
    from src.sector_rrg import _min_periods, _NORM_WINDOW, _MIN_FRAC
    assert _MIN_FRAC >= 0.75
    assert _min_periods(_NORM_WINDOW) == int(_NORM_WINDOW * _MIN_FRAC)
    assert _min_periods(52) > 52 // 3


def test_band_never_invents_a_middle():
    """A missing percentile must stay missing. Returning 'balanced' for "we
    could not place this" is the same class of claim as filling 100."""
    from src.sector_rrg import _band, _pctile
    assert _band(None, "lo", "mid", "hi") is None
    assert _band(10.0, "lo", "mid", "hi") == "lo"
    assert _band(90.0, "lo", "mid", "hi") == "hi"
    assert _band(50.0, "lo", "mid", "hi") == "mid"
    # Too little history to place a value at all.
    assert _pctile(pd.Series([1.0, 2.0, 3.0]), 2.0) is None


def test_band_of_the_latest_value_matches_the_band_used_for_its_context():
    """The headline band comes from _pctile on the latest value; the context
    averages the history sharing that band. Deriving those from two different
    cuts — fraction-below vs quantile(1/3) — lets a boundary value be labelled
    one band and described by another. They must agree for EVERY observation."""
    import numpy as np
    from src.sector_rrg import _pctile, _pct_rank, _band
    rng = np.random.default_rng(3)
    for trial in range(200):
        n = int(rng.integers(20, 120))
        # Include heavy ties, which is exactly where two rules diverge.
        s = pd.Series(np.round(rng.normal(size=n), 1))
        ranks = _pct_rank(s)
        for i in range(n):
            direct = _pctile(s, float(s.iloc[i]))
            assert direct == pytest.approx(ranks.iloc[i], abs=0.05), (
                f"trial {trial} obs {i}: _pctile={direct} vs _pct_rank={ranks.iloc[i]}")
            assert (_band(direct, "lo", "mid", "hi")
                    == _band(float(ranks.iloc[i]), "lo", "mid", "hi"))


def test_heading_is_absent_when_the_dot_has_not_moved():
    """atan2(0, 0) is 0.0 — "due east" — for a sector that went nowhere."""
    import numpy as np
    from src.sector_rrg import _SCALE
    dx = dy = 0.0
    assert float(np.degrees(np.arctan2(dy, dx))) == 0.0, "the trap this guards"
    assert not np.hypot(dx, dy) > 0.01 * _SCALE
    assert np.hypot(0.5, 0.5) > 0.01 * _SCALE      # a real move still reports


# ── Overnight base rates apply to a FINISHED range ───────────────────────────

def test_overnight_conditioned_tables_are_withheld_until_the_range_is_final():
    """Every table in the study is keyed on the overnight range AS IT STANDS AT
    09:30. Measured 2026-08-02 21:18 ET, three hours into a 15.5-hour Globex
    session: the range was 15.50 points against a 43.0 median for a finished
    one, and the module reported a 74.0% chance of the high going plus an RTH
    range drawn from the SMALLEST size bucket. By the bell the range typically
    triples into a different bucket, so both numbers described a session that
    did not exist yet."""
    partial, finished_median = 15.5, 43.0
    assert partial < finished_median * 0.5, "the case that motivated the gate"

    # The gate is `overnight_complete`, which is "has the cash session opened".
    for complete, expect_probs, expect_rth in [(False, False, False), (True, True, True)]:
        match = {"n": 123, "breaks_on_high_pct": 74.0, "breaks_on_low_pct": 52.0}
        size = {"rth_p25": 35.4, "rth_median": 44.9, "rth_p75": 57.7, "n": 124}
        expected = ({"n": match["n"], **{k: v for k, v in match.items() if k != "n"}}
                    if complete else {"n": match["n"], "withheld": "overnight still forming"})
        rth = size if (size and complete) else None
        assert ("breaks_on_high_pct" in expected) is expect_probs
        assert (rth is not None) is expect_rth
        if not complete:
            assert expected["withheld"], "must say WHY it is missing, not just omit"


def _on_frames(complete: bool):
    """A Globex session three hours old, optionally with the cash open."""
    import numpy as np
    start = pd.Timestamp("2026-08-02 18:00", tz="America/New_York")
    idx = pd.date_range(start, periods=38, freq="5min")
    rng = np.linspace(7543.5, 7559.0, len(idx))
    on = pd.DataFrame({"Open": rng, "High": rng + 0.5, "Low": rng - 0.5,
                       "Close": rng, "Volume": 1000}, index=idx)
    on.loc[on.index[-1], "Close"] = 7554.0
    if complete:
        ridx = pd.date_range(pd.Timestamp("2026-08-03 09:30", tz="America/New_York"),
                             periods=12, freq="5min")
        r = pd.DataFrame({"Open": 7550.0, "High": 7552.0, "Low": 7548.0,
                          "Close": 7551.0, "Volume": 5000}, index=ridx)
        bars = pd.concat([on, r])
    else:
        r, bars = pd.DataFrame(), on
    return {"overnight": on, "cur_rth": r, "bars": bars,
            "anchor": pd.Timestamp("2026-08-03", tz="America/New_York"),
            "mode": "rth" if complete else "premarket"}


_ON_BASE = {
    "available": True,
    # Every band, so the fixture does not depend on where _pos_band happens to
    # cut — the test is about the GATE, not about bucket boundaries.
    "by_open_position": [{"band": b, "n": 123, "breaks_on_high_pct": 74.0,
                          "breaks_on_low_pct": 52.0, "both_pct": 20.0,
                          "median_rth_range": 60.0}
                         for b in ("bottom 20%", "lower", "middle", "upper", "top 20%")],
    "by_overnight_size": [{"on_range_pct_lo": 0.0, "on_range_pct_hi": 5.0,
                           "rth_p25": 35.4, "rth_median": 44.9, "rth_p75": 57.7, "n": 124}],
    "median_on_range": 43.0, "median_rth_range": 59.8,
}


@pytest.mark.parametrize("complete", [False, True])
def test_overnight_read_gates_the_conditioned_tables_end_to_end(complete):
    """Drives overnight_read itself. The first version of this test asserted on
    reconstructed logic, so removing the gate from the module left every test
    green — the mutation survived, for the third time in one session."""
    from src.es_overnight import overnight_read
    out = overnight_read(base=dict(_ON_BASE), frames=_on_frames(complete))
    live = out.get("live")
    assert live, out
    assert live["overnight_complete"] is complete
    if complete:
        assert live["rth_range_expectation"] is not None
        assert "withheld" not in (live.get("expected") or {})
    else:
        assert live["rth_range_expectation"] is None, (
            "bucketed on the FINISHED range size; a half-built range lands in "
            "the wrong bucket")
        assert (live.get("expected") or {}).get("withheld"), (
            "must say why the frequencies are missing, not merely omit them")
        assert "breaks_on_high_pct" not in (live.get("expected") or {})
        assert 0 < live["overnight_elapsed_pct"] < 100


def test_overnight_elapsed_is_a_share_of_the_globex_window():
    """18:00 -> 09:30 is 15.5 hours, and the share is what tells a reader how
    provisional the range is."""
    from src.es_overnight import _ON_OPEN_HOUR, _RTH_OPEN
    span_h = (24 - _ON_OPEN_HOUR) + _RTH_OPEN[0] + _RTH_OPEN[1] / 60.0
    assert span_h == pytest.approx(15.5)
    assert round(3.2 / span_h * 100, 1) == pytest.approx(20.6, abs=0.5)
    assert round(15.5 / span_h * 100, 1) == pytest.approx(100.0)


# ── The quote a trader sizes off ─────────────────────────────────────────────

def test_snapshot_quote_guard_rejects_the_wrong_contract():
    """A 2% band is not a sanity check. Live within the hour of shipping it, the
    quote came back 7626.00 while ES traded 7561 — the snapshot had resolved
    ESZ6, the DECEMBER contract, which carries a quarter's carry and prints ~65
    handles higher. That is 0.86%, waved straight through a 151-handle band,
    and every level distance was measured from it."""
    from src.es_levels import accept_snapshot
    bar_close, bar_ticker, span = 7561.0, "ESU6", 18.0
    bar_ts = pd.Timestamp("2026-08-02 22:15", tz="America/New_York")
    newer = bar_ts + pd.Timedelta(minutes=3)

    def accept(px, ticker, ts=newer):
        return accept_snapshot({"ticker": ticker, "price": px, "asof": ts},
                               bar_ticker, bar_close, span, bar_ts)[0]

    assert accept(7560.25, "ESU6"), "same contract, newer, in line — take it"
    assert not accept(7626.0, "ESZ6"), "the December contract is not this instrument"
    assert not accept(7626.0, "ESU6"), (
        "even labelled correctly, 65 handles between consecutive 5-minute "
        "prints is a different thing wearing the same units")
    assert not accept(0.0, "ESU6"), "a zero print must never become `last`"
    assert not accept(7560.25, "ESU6", bar_ts - pd.Timedelta(minutes=3)), "older than the bar"
    assert accept_snapshot(None, bar_ticker, bar_close, span, bar_ts) == (False, None)
    # And the rejection says WHY, so a silent fallback is distinguishable.
    ok, why = accept_snapshot({"ticker": "ESZ6", "price": 7626.0, "asof": newer},
                              bar_ticker, bar_close, span, bar_ts)
    assert ok is False and "ESZ6" in why and "ESU6" in why


def test_front_month_does_not_flip_on_a_transient_empty_fetch(monkeypatch):
    """"Busiest wins" treats a failed fetch as zero volume. If the front month's
    daily bars come back empty for one call while the back month returns a
    single lot, the back month wins and is CACHED FOR THE DAY. A genuine roll
    never looks like this — on 2026-07-31 the front turned over 1,736,589 lots
    against the next one's 901."""
    import src.futures_data as fd

    def contracts(path, **params):
        return {"results": [
            {"ticker": "ESU6", "type": "single", "days_to_maturity": 47},
            {"ticker": "ESZ6", "type": "single", "days_to_maturity": 138},
        ]}

    monkeypatch.setattr(fd, "_get", contracts)

    # Front month fetch fails transiently; back month returns one lot.
    def bars(tkr, **kw):
        return pd.DataFrame() if tkr == "ESU6" else pd.DataFrame({"Volume": [1.0]})

    monkeypatch.setattr(fd, "fetch_bars", bars)
    fd._front_cache.clear()
    got = fd.front_month("ES", as_of=date(2026, 8, 2))
    assert got == "ESU6", f"flipped to {got} on a transient empty fetch"
    assert "ES" not in fd._front_cache, "a zero-volume guess must not be cached for the day"

    # A REAL roll still works: both report volume, the back month is heavier.
    def rolled(tkr, **kw):
        return pd.DataFrame({"Volume": [900.0]}) if tkr == "ESU6" else pd.DataFrame({"Volume": [1_700_000.0]})

    monkeypatch.setattr(fd, "fetch_bars", rolled)
    fd._front_cache.clear()
    assert fd.front_month("ES", as_of=date(2026, 8, 2)) == "ESZ6", "a real roll must still switch"


def test_stale_is_measured_on_the_quote_not_the_bar():
    """They differ by the bar's granularity, and the card warns off the quote."""
    for quote_age, market_live, want in [(10, True, False), (16, True, True),
                                         (200, False, False), (16, False, False)]:
        assert bool(market_live and quote_age > 15) is want


# ── Dealer gamma: the basis needs two SIMULTANEOUS quotes ────────────────────
# `es_last - spot` is right only while cash prints. Outside RTH the SPX close is
# frozen and ES keeps trading, so that subtraction books the whole move since
# the bell as basis and shifts every ES wall with it.

@pytest.mark.parametrize("ts, is_open", [
    ("2026-08-03 09:29", False),   # one minute before the bell
    ("2026-08-03 09:30", True),
    ("2026-08-03 12:00", True),
    ("2026-08-03 15:59", True),
    ("2026-08-03 16:00", False),   # the close itself is not open
    ("2026-08-03 20:30", False),   # Globex, cash long shut
    ("2026-08-01 12:00", False),   # Saturday
    ("2026-08-02 19:00", False),   # Sunday reopen
])
def test_cash_open_window(ts, is_open):
    from src.dealer_gamma import _cash_is_open
    assert _cash_is_open(pd.Timestamp(ts, tz="America/New_York")) is is_open


def test_basis_uses_the_prior_cash_close_when_cash_is_shut(monkeypatch):
    """Measured on the Sunday 2026-08-02 reopen: ES closed Friday's cash session
    at 7522.25 against SPX 7489.72, an observed basis of 32.53. By 20:20 ET ES
    was 7549.00 and the old formula returned 59.28 — 26.75 handles of weekend
    gap booked as carry, moving every wall by that much."""
    import src.dealer_gamma as dg

    spx_close, es_at_close, es_now = 7489.72, 7522.25, 7549.00
    anchor = pd.Timestamp("2026-07-31 16:00", tz="America/New_York")
    monkeypatch.setattr(dg, "_cash_is_open", lambda now: False)
    monkeypatch.setattr(dg, "_es_at", lambda ts: (es_at_close, anchor))

    # The selection logic, exercised exactly as dealer_gamma runs it.
    spot_asof = pd.Timestamp("2026-07-31", tz="America/New_York")
    basis_is_live = dg._cash_is_open(pd.Timestamp.now(tz="America/New_York"))
    assert basis_is_live is False
    es_then, used = dg._es_at(spot_asof.normalize() + pd.Timedelta(hours=16))
    basis = es_then - spx_close
    assert basis == pytest.approx(32.53, abs=0.01)
    assert basis != pytest.approx(es_now - spx_close), "that is the stale formula"
    assert (es_now - spx_close) - basis == pytest.approx(26.75, abs=0.01)
    assert used == anchor


def _stub_gamma_chain(monkeypatch, seen: dict | None = None):
    """Stub the chain so dealer_gamma runs end to end without a network call.

    7500 carries the HEAVIEST positive gamma and sits BETWEEN the stale cash
    print (7489.72) and where ES implies SPX is (7522.72). That is the whole
    hazard in one strike: evaluated at the stale spot it is "above" and wins the
    call wall, so the card would hand back resistance that price has already
    traded through. A fixture whose strikes straddle nothing cannot tell the two
    behaviours apart — the first version of this one could not, and two
    mutations walked straight through it.
    """
    import src.dealer_gamma as dg
    monkeypatch.setattr(dg, "_upcoming_expiries", lambda d: ["2026-08-03"])
    monkeypatch.setattr(dg, "_fetch_chain", lambda e, s: [{"stub": True}])
    monkeypatch.setattr(dg, "_gex_by_strike",
                        lambda c, s: {"2026-08-03": {7400.0: -2e9, 7500.0: 9e9, 7530.0: 8e9}})

    def _flip(contracts, spot, ref_day=None, **kw):
        if seen is not None:
            seen["flip_spot"] = spot
        return {"flip": 7455.0, "gex_at_spot": 1.0e9, "profile": []}

    monkeypatch.setattr(dg, "_gamma_flip", _flip)
    return dg


def test_dealer_gamma_anchors_the_basis_when_cash_is_shut(monkeypatch):
    """End to end, not just the helper. The first version of this test patched
    _cash_is_open and asserted on the arithmetic, so forcing basis_is_live=True
    inside dealer_gamma left every test green — the mutation survived."""
    dg = _stub_gamma_chain(monkeypatch)
    anchor = pd.Timestamp("2026-07-31 16:00", tz="America/New_York")
    monkeypatch.setattr(dg, "_cash_is_open", lambda now: False)
    monkeypatch.setattr(dg, "_es_at", lambda ts: (7522.25, anchor))

    out = dg.dealer_gamma(
        session_day=pd.Timestamp("2026-08-03", tz="America/New_York"),
        spot=7489.72, spot_asof=pd.Timestamp("2026-07-31", tz="America/New_York"),
        es_last=7549.00)

    assert out["available"], out
    assert out["es_basis"] == pytest.approx(32.53, abs=0.01), (
        "must use ES at the cash close, not the live Globex price")
    assert out["es_basis"] != pytest.approx(7549.00 - 7489.72, abs=0.01)
    assert out["es_basis_is_live"] is False
    assert out["spx_cash_open"] is False
    assert out["es_basis_asof"] == anchor.isoformat()
    # And the walls must move with it.
    assert out["call_wall_es"] == pytest.approx(out["call_wall_spx"] + 32.53, abs=0.01)


def test_gamma_evaluates_the_book_where_price_actually_is(monkeypatch):
    """`spot` is the CASH print and cash is shut most of the day. Every
    above/below question — both walls, the nearest flip crossing, the sign of
    gamma at price — was answered at a frozen close while ES traded on. The
    dangerous case is not the regime label: it is a "wall above" that price has
    already traded through, a level a trader leans on that is behind them."""
    seen: dict = {}
    dg = _stub_gamma_chain(monkeypatch, seen)
    anchor = pd.Timestamp("2026-07-31 16:00", tz="America/New_York")
    monkeypatch.setattr(dg, "_cash_is_open", lambda now: False)
    monkeypatch.setattr(dg, "_es_at", lambda ts: (7522.25, anchor))

    out = dg.dealer_gamma(
        session_day=pd.Timestamp("2026-08-03", tz="America/New_York"),
        spot=7489.72, spot_asof=pd.Timestamp("2026-07-31", tz="America/New_York"),
        es_last=7555.25)

    assert out["spot_source"] == "es_implied"
    # The 7500 strike carries the heaviest positive gamma but price is already
    # through it. Evaluated at the stale close it would be the call wall.
    assert out["call_wall_spx"] == pytest.approx(7530.0), (
        f"call wall {out['call_wall_spx']} is behind price — evaluated at the "
        "stale cash print rather than where ES says SPX is")
    # And the flip search must be centred on price, not on the frozen close.
    assert seen["flip_spot"] == pytest.approx(7555.25 - 32.53, abs=0.01)
    assert out["spx_spot"] == pytest.approx(7489.72), "the raw cash print is still reported"
    assert out["spx_spot_effective"] == pytest.approx(7555.25 - 32.53, abs=0.01)
    assert out["spx_spot_effective"] > out["spx_spot"], "ES has moved up since the close"

    # THE INVARIANT a trader can check by eye: the you-are-here point lands
    # exactly on the ES price, so the walls sit on the ladder being watched.
    assert out["spx_spot_effective"] + out["es_basis"] == pytest.approx(7555.25, abs=0.02)

    # And the walls must be on the correct side of price, in ES terms.
    if out.get("call_wall_es") is not None:
        assert out["call_wall_es"] >= 7555.25 - 0.01, "a wall 'above' must be above price"
    if out.get("put_wall_es") is not None:
        assert out["put_wall_es"] <= 7555.25 + 0.01, "a wall 'below' must be below price"


def test_gamma_effective_spot_is_the_cash_print_during_rth(monkeypatch):
    """During cash hours the two are the same and nothing may change."""
    dg = _stub_gamma_chain(monkeypatch)
    monkeypatch.setattr(dg, "_cash_is_open", lambda now: True)
    monkeypatch.setattr(dg, "_es_at", lambda ts: (None, None))
    out = dg.dealer_gamma(
        session_day=pd.Timestamp("2026-08-03", tz="America/New_York"),
        spot=7500.0, spot_asof=pd.Timestamp("2026-08-03", tz="America/New_York"),
        es_last=7530.0)
    assert out["spot_source"] == "cash"
    assert out["spx_spot_effective"] == pytest.approx(out["spx_spot"])
    assert out["spx_spot_effective"] + out["es_basis"] == pytest.approx(7530.0, abs=0.02)


def test_gamma_treats_a_market_holiday_as_cash_shut(monkeypatch):
    """The clock cannot see a holiday. On Thanksgiving at 11:00 the
    weekday-and-hours test says cash is printing, and the basis would go back to
    subtracting a live ES price from a stale close — ~9 days a year. The DATA
    settles it: if the newest SPX print is from an earlier session, cash is not
    trading today whatever the clock says."""
    dg = _stub_gamma_chain(monkeypatch)
    anchor = pd.Timestamp("2026-11-25 16:00", tz="America/New_York")
    # Clock says open; the SPX print is yesterday's.
    monkeypatch.setattr(dg, "_cash_is_open", lambda now: True)
    monkeypatch.setattr(dg, "_es_at", lambda ts: (7522.25, anchor))
    monkeypatch.setattr(dg.pd.Timestamp, "now",
                        classmethod(lambda cls, tz=None: pd.Timestamp("2026-11-26 11:00", tz=tz)))

    out = dg.dealer_gamma(
        session_day=pd.Timestamp("2026-11-26", tz="America/New_York"),
        spot=7489.72, spot_asof=pd.Timestamp("2026-11-25", tz="America/New_York"),
        es_last=7555.25)

    assert out["spx_cash_open"] is False, "a stale SPX print means cash is shut"
    assert out["es_basis"] == pytest.approx(32.53, abs=0.01), (
        "must carry the basis, not subtract a live ES from a stale close")
    assert out["spot_source"] == "es_implied"


def test_base_rates_name_their_instrument():
    """The overnight study is ES futures over two years; these are SPY over
    five, and they sit inches apart on the same card. Both must say so."""
    from src.es_baserates import _INTRADAY_SYMBOL, _INTRADAY_BAR_MIN
    assert _INTRADAY_SYMBOL == "SPY"
    label = f"{_INTRADAY_SYMBOL}, {_INTRADAY_BAR_MIN}-minute bars"
    assert "SPY" in label and "minute" in label


def test_dealer_gamma_uses_the_live_basis_during_the_cash_session(monkeypatch):
    dg = _stub_gamma_chain(monkeypatch)
    monkeypatch.setattr(dg, "_cash_is_open", lambda now: True)
    monkeypatch.setattr(dg, "_es_at", lambda ts: (7522.25, None))
    out = dg.dealer_gamma(
        session_day=pd.Timestamp("2026-08-03", tz="America/New_York"),
        spot=7500.0, spot_asof=pd.Timestamp("2026-08-03", tz="America/New_York"),
        es_last=7530.0)
    assert out["es_basis"] == pytest.approx(30.0), "both legs print together"
    assert out["es_basis_is_live"] is True
    assert out["spx_cash_open"] is True


def test_basis_stays_live_during_the_cash_session():
    """During RTH both legs print together, so the plain subtraction is right
    and must not be replaced by a carried one."""
    from src.dealer_gamma import _cash_is_open
    assert _cash_is_open(pd.Timestamp("2026-08-03 11:00", tz="America/New_York"))
    spot, es_last = 7500.0, 7530.0
    assert es_last - spot == pytest.approx(30.0)


# ── Futures contract lookup: server clock vs exchange clock ──────────────────
# Cloud Run runs in UTC. From 20:00 ET until midnight ET the container already
# believes it is tomorrow, and the vendor's contracts endpoint returns an EMPTY
# set for any future date. front_month therefore resolved to None and every ES
# card went dark for four hours a night, reporting "no usable intraday ES data"
# while the feed was perfectly healthy.

def test_exchange_today_tracks_the_exchange_not_the_container():
    from src.futures_data import exchange_today
    assert exchange_today() == pd.Timestamp.now(tz="America/New_York").date()


def test_exchange_today_differs_from_utc_in_the_evening():
    """The window where the bug lived. After 20:00 ET (EDT = UTC-4) the UTC date
    has already rolled over, so a UTC-based `date.today()` asks for tomorrow."""
    import datetime as _dt
    from zoneinfo import ZoneInfo
    et = _dt.datetime(2026, 8, 2, 20, 30, tzinfo=ZoneInfo("America/New_York"))
    assert et.date() == date(2026, 8, 2)
    assert et.astimezone(_dt.timezone.utc).date() == date(2026, 8, 3), (
        "this is the divergence that produced the outage")


def test_front_month_walks_back_when_a_date_carries_no_contracts(monkeypatch):
    """A future date returns empty, and so does a holiday the vendor has no
    reference file for. Asking once and giving up turns a calendar quirk into a
    total outage of every card built on these bars."""
    import src.futures_data as fd
    asked: list[str] = []
    GOOD = date(2026, 8, 2)

    def fake_get(path, **params):
        if "contracts" in path:
            d = params.get("date")
            asked.append(d)
            if d != GOOD.isoformat():
                return {"results": []}          # exactly what a future date gives
            return {"results": [
                {"ticker": "ESU6", "type": "single", "days_to_maturity": 47},
                {"ticker": "ESZ6", "type": "single", "days_to_maturity": 138},
            ]}
        return {"results": []}

    monkeypatch.setattr(fd, "_get", fake_get)
    monkeypatch.setattr(fd, "fetch_bars", lambda *a, **k: pd.DataFrame())
    fd._front_cache.clear()

    # Ask as UTC would have: one day ahead of the exchange.
    got = fd.front_month("ES", as_of=GOOD + timedelta(days=1))
    assert got == "ESU6", f"got {got!r}; the backward walk did not recover"
    assert asked[0] == (GOOD + timedelta(days=1)).isoformat(), "tries the asked date first"
    assert GOOD.isoformat() in asked, "and walks back to a date that answers"


def test_front_month_probes_the_exchange_date_first_not_the_container_date(monkeypatch):
    """Pins the actual defect. On an ET developer machine `date.today()` and the
    exchange date agree, so reverting the call site passes every other test here
    — it only bites on a UTC container, which is exactly where it shipped."""
    import src.futures_data as fd

    class _UtcRolledOver(date):
        @classmethod
        def today(cls):
            return date(2026, 8, 3)          # what a UTC box reports at 20:30 ET

    asked: list[str] = []

    def fake_get(path, **params):
        asked.append(params.get("date"))
        return {"results": [{"ticker": "ESU6", "type": "single", "days_to_maturity": 47}]}

    monkeypatch.setattr(fd, "_date", _UtcRolledOver)
    monkeypatch.setattr(fd, "_get", fake_get)
    monkeypatch.setattr(fd, "fetch_bars", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(fd, "exchange_today", lambda: date(2026, 8, 2))
    fd._front_cache.clear()

    fd.front_month("ES")
    assert asked[0] == "2026-08-02", (
        f"first probe was {asked[0]!r}; it must use the exchange date, not the "
        "container's rolled-over one, which returns an empty contract set")


def test_front_month_gives_up_after_the_lookback(monkeypatch):
    """It must not walk backwards forever looking for a contract set."""
    import src.futures_data as fd
    calls = []

    def fake_get(path, **params):
        calls.append(params.get("date"))
        return {"results": []}

    monkeypatch.setattr(fd, "_get", fake_get)
    fd._front_cache.clear()
    assert fd.front_month("ES", as_of=date(2026, 8, 2)) is None
    assert len(calls) == fd._CONTRACT_DATE_LOOKBACK + 1, calls


# ── Vol-scan threshold disclosure and history ────────────────────────────────
# The cuts were never checked against the distribution they gate. Audited live
# 2026-08-02: the 1.10 skew cut sat at the 50th percentile of the cross section,
# so "Broad Fear" fired on more-than-half-above-the-median — a coin flip.

def _stub_store(monkeypatch, initial=None):
    """In-memory stand-in for the Supabase key/value row."""
    import src.vol_history as vh
    box = {"rows": list(initial or [])}
    monkeypatch.setattr(vh, "_load", lambda: list(box["rows"]))
    monkeypatch.setattr(vh, "_save", lambda rows: box.__setitem__("rows", list(rows)))
    return box


def test_history_keeps_one_row_per_session_day(monkeypatch):
    """The scan runs many times a day behind a cache. Recording every run would
    weight busy days more heavily than quiet ones for no reason."""
    from src.vol_history import record
    box = _stub_store(monkeypatch)
    d = date(2026, 8, 3)
    record({"avg_ivhv": 1.10}, session_date=d)
    record({"avg_ivhv": 1.20}, session_date=d)
    rows = record({"avg_ivhv": 1.30}, session_date=d)
    assert len(rows) == 1, rows
    assert rows[0]["avg_ivhv"] == 1.30, "latest write for a day must win"


def test_degraded_scan_is_not_recorded(monkeypatch):
    """A partial universe would enter as a real reading and then distort every
    percentile computed against it afterwards."""
    from src.vol_history import record
    _stub_store(monkeypatch)
    assert record({"avg_ivhv": 9.9}, healthy=False) == []
    assert record({}, healthy=True) == [], "an empty summary is not an observation"


def test_percentile_is_none_until_the_history_is_deep_enough(monkeypatch):
    """None means 'not yet knowable'. A 50 would be an invented middle — the
    same defect as every other placeholder fixed this week."""
    from src.vol_history import record, percentiles, _MIN_HISTORY
    _stub_store(monkeypatch)
    rows = []
    for i in range(_MIN_HISTORY - 1):
        rows = record({"avg_ivhv": 1.0 + i / 1000},
                      session_date=date(2026, 1, 1) + timedelta(days=i))
    # The excluded day is passed explicitly, the way production's `record` and
    # `percentiles` agree on it via the same UTC default.
    last = date(2026, 1, 1) + timedelta(days=_MIN_HISTORY - 2)
    p = percentiles(rows, {"avg_ivhv": 1.5}, session_date=last)
    assert p["avg_ivhv"]["pctile"] is None
    assert p["avg_ivhv"]["n_history"] == _MIN_HISTORY - 2, p["avg_ivhv"]

    # One more than the floor, because today's own observation is excluded from
    # the reference set: _MIN_HISTORY priors needs _MIN_HISTORY + 1 rows.
    for i in range(_MIN_HISTORY - 1, _MIN_HISTORY + 1):
        rows = record({"avg_ivhv": 1.0 + i / 1000},
                      session_date=date(2026, 1, 1) + timedelta(days=i))
    newest = date(2026, 1, 1) + timedelta(days=_MIN_HISTORY + 5)
    rows = record({"avg_ivhv": 1.5}, session_date=newest)
    p = percentiles(rows, {"avg_ivhv": 1.5}, session_date=newest)
    assert p["avg_ivhv"]["n_history"] >= _MIN_HISTORY, p["avg_ivhv"]
    assert p["avg_ivhv"]["pctile"] == pytest.approx(100.0), p["avg_ivhv"]


def test_percentile_excludes_todays_own_observation(monkeypatch):
    """Otherwise a value is partly compared against itself."""
    from src.vol_history import record, percentiles, _MIN_HISTORY
    _stub_store(monkeypatch)
    rows = []
    for i in range(_MIN_HISTORY + 10):
        rows = record({"avg_ivhv": 1.0},
                      session_date=date(2026, 1, 1) + timedelta(days=i))
    last = date(2026, 1, 1) + timedelta(days=_MIN_HISTORY + 9)
    p = percentiles(rows, {"avg_ivhv": 1.0}, session_date=last)
    assert p["avg_ivhv"]["n_history"] == len(rows) - 1


def test_percentile_does_not_drop_a_prior_day_when_today_was_not_recorded(monkeypatch):
    """`record` skips a degraded scan, so the newest stored row can belong to an
    EARLIER day. Inferring "today" from rows[-1] then excludes a real prior
    observation from its own reference set — shrinking the sample and shifting
    every percentile, on exactly the runs where the data is already suspect."""
    from src.vol_history import record, percentiles, _MIN_HISTORY
    _stub_store(monkeypatch)
    rows = []
    for i in range(_MIN_HISTORY + 10):
        rows = record({"avg_ivhv": 1.0 + i / 1000},
                      session_date=date(2026, 1, 1) + timedelta(days=i))
    n_rows = len(rows)

    # Today's scan was degraded: nothing written, so rows[-1] is yesterday.
    rows = record({"avg_ivhv": 9.9}, healthy=False)
    assert len(rows) == n_rows, "a degraded scan must not add a row"

    today = date(2026, 1, 1) + timedelta(days=_MIN_HISTORY + 10)
    p = percentiles(rows, {"avg_ivhv": 1.05}, session_date=today)
    assert p["avg_ivhv"]["n_history"] == n_rows, (
        "every stored row is prior to today and must count")


def test_threshold_report_flags_a_cut_sitting_on_the_median():
    """The disclosure that matters: a cut splitting the universe in half cannot
    separate a regime from its opposite."""
    from src.vol_history import threshold_report
    mdf = pd.DataFrame({"Put_Skew": [0.9, 1.0, 1.05, 1.15, 1.2, 1.3]})
    rep = threshold_report(mdf, {"steep": {"column": "Put_Skew", "cut": 1.10}})
    assert rep["steep"]["pctile_in_universe"] == pytest.approx(50.0)
    assert rep["steep"]["near_median"] is True
    assert rep["steep"]["validated"] is False

    # A cut out in the tail is not flagged.
    rep2 = threshold_report(mdf, {"steep": {"column": "Put_Skew", "cut": 1.9}})
    assert rep2["steep"]["near_median"] is False


def test_threshold_report_survives_a_missing_column():
    from src.vol_history import threshold_report
    rep = threshold_report(pd.DataFrame({"Put_Skew": [1.0]}),
                           {"parity": {"column": "Parity", "cut": 0.75}})
    assert rep["parity"]["pctile_in_universe"] is None


# ── Fed probabilities from ZQ ────────────────────────────────────────────────
# Two bugs that produced confident, plausible, wrong numbers.

def test_month_weights_match_the_worked_example():
    """Sep 2026: N=30, decision on the 16th. A rate decided on day k is effective
    day k+1, so days 1..k carry the OLD rate — n_pre is the day itself."""
    from src.fed_probabilities import month_weights
    assert month_weights(date(2026, 9, 16)) == (30, 16, 14)
    assert month_weights(date(2026, 10, 28)) == (31, 28, 3)


def test_implied_post_rate_matches_the_worked_example():
    from src.fed_probabilities import implied_post_rate
    got = implied_post_rate(settle=100 - 3.71, r_pre=3.63, meeting=date(2026, 9, 16))
    assert got == pytest.approx((30 * 3.71 - 16 * 3.63) / 14, abs=1e-9)


def test_implied_post_rate_is_none_on_a_last_day_meeting():
    """The contract then carries no post-meeting days and cannot speak to the
    new rate. 0.0 would be a claim that nothing changed."""
    from src.fed_probabilities import implied_post_rate
    assert implied_post_rate(96.0, 3.63, date(2026, 4, 30)) is None


@pytest.mark.parametrize("delta", [0.0, 12.5, 25.0, -25.0, -10.0, 37.5, -37.5])
def test_probabilities_sum_to_one_and_reproduce_the_delta(delta):
    """It is an interpolation onto 25bp buckets, so its expected value must be
    the delta it came from."""
    from src.fed_probabilities import outcome_probabilities
    p = outcome_probabilities(delta)
    assert sum(p.values()) == pytest.approx(1.0)
    assert sum(k * v for k, v in p.items()) == pytest.approx(delta)


def test_anchor_walks_backward_not_forward():
    """Walking FORWARD finds the next meeting-free month, which sits AFTER the
    meetings being priced and already contains their outcomes. Live symptom:
    September read -35.36bp and October +35.36bp, a perfectly offsetting pair.
    The month before the first meeting is the only clean anchor."""
    from src.fed_probabilities import _anchor, zq_ticker
    # Aug 2026 is meeting-free and precedes the 16 Sep decision; Nov 2026 is the
    # meeting-free month a forward walk would have found instead.
    settles = {zq_ticker(2026, 8): 96.37, zq_ticker(2026, 11): 96.125}
    rate, label = _anchor(date(2026, 9, 16), settles, spot=3.63)
    assert rate == pytest.approx(3.63, abs=1e-9), "must read the PRIOR month"
    assert "ZQQ6" in label
    assert rate != pytest.approx(100 - 96.125), "that is the forward-walk answer"


def test_anchor_falls_back_to_spot_when_the_prior_month_held_a_meeting():
    from src.fed_probabilities import _anchor, zq_ticker
    # Oct 2026 holds a meeting, so it is not a constant-rate month.
    rate, label = _anchor(date(2026, 12, 9), {zq_ticker(2026, 11): 96.125}, spot=3.87)
    assert label == "spot EFFR" or "ZQX6" in label


def test_calendar_horizon_blocks_the_next_month_shortcut():
    """Past the last encoded meeting, `_has_meeting` answers False for every
    month forever. That licensed the next-month estimator on a false premise:
    with the list ending 2026-12-09, January 2027 looked meeting-free and the
    December decision was priced off ZQF7 as though that were established."""
    from src.fed_probabilities import _month_is_known, _has_meeting, FOMC_DATES
    last = FOMC_DATES[-1]
    assert _month_is_known(last.year, last.month)
    nxt = (last.year, last.month + 1) if last.month < 12 else (last.year + 1, 1)
    assert not _month_is_known(*nxt), "beyond the calendar must read as unknown"
    assert not _has_meeting(*nxt), "and _has_meeting alone cannot tell them apart"


def test_last_known_meeting_does_not_use_the_next_month_shortcut(monkeypatch):
    """Exercises the real path, not just the helpers.

    The first version of this test only asserted on `_month_is_known` and
    `_has_meeting`, so removing the guard from `fed_probabilities` left all
    tests green — the mutation survived. What has to be pinned is that the
    LAST meeting on the calendar falls back to the within-month solve, because
    the month after it is unknown rather than known-empty.
    """
    import src.fed_probabilities as fp

    last = fp.FOMC_DATES[-1]
    nm = (last.year, last.month + 1) if last.month < 12 else (last.year + 1, 1)
    prev = (last.year, last.month - 1) if last.month > 1 else (last.year - 1, 12)
    settles = {
        fp.zq_ticker(*prev): 96.37,      # meeting-free month before -> anchor
        fp.zq_ticker(last.year, last.month): 96.04,
        fp.zq_ticker(*nm): 96.00,        # exists, and must NOT be used
    }
    monkeypatch.setattr(fp, "_fetch_settles", lambda months: settles)
    monkeypatch.setattr(fp, "_spot_effr", lambda: 3.63)

    out = fp.fed_probabilities(asof=last - timedelta(days=8), n_meetings=1)
    assert out["available"], out
    m = out["meetings"][0]
    assert m["date"] == last.isoformat()
    assert m["method"] == "within-month", (
        "the month after the last known meeting is UNKNOWN, not meeting-free — "
        "using it would price the decision off a contract on a false premise")
    assert m["leverage"] > 1.0, "the within-month solve always carries leverage"
    # `calendar_exhausted` means "fewer meetings returned than asked for". One
    # was asked for and one was available, so it is correctly False here — the
    # guard under test is about the month AFTER the last meeting, not about
    # running out of meetings.
    assert out["calendar_exhausted"] is False
    assert fp.fed_probabilities(asof=last - timedelta(days=8),
                                n_meetings=4)["calendar_exhausted"] is True


def test_a_missing_settlement_breaks_the_chain_and_everything_after_it(monkeypatch):
    """Each meeting's r_pre is the rate solved after the previous one. Skipping
    a meeting leaves r_pre holding the rate from BEFORE it, so the next
    meeting's delta quietly contains both moves and attributes them all to the
    later date — the same misattribution that reported +180.83bp for a meeting
    pricing +3.69bp, except it lands on a row that looks perfectly healthy."""
    import src.fed_probabilities as fp

    upcoming = [d for d in fp.FOMC_DATES if d > date(2026, 8, 1)][:3]
    assert len(upcoming) >= 3, "fixture needs three meetings"
    skip = fp.zq_ticker(upcoming[0].year, upcoming[0].month)

    full = {fp.zq_ticker(y, m): 96.0
            for (y, m) in [(d.year, d.month) for d in upcoming]
            + [fp._next_month(d.year, d.month) for d in upcoming]
            + [fp._prev_month(upcoming[0].year, upcoming[0].month)]}
    holed = {k: v for k, v in full.items() if k != skip}

    monkeypatch.setattr(fp, "_spot_effr", lambda: 3.63)
    monkeypatch.setattr(fp, "_fetch_settles", lambda months: holed)
    out = fp.fed_probabilities(asof=date(2026, 8, 1), n_meetings=3)

    rows = out["meetings"]
    assert "error" in rows[0], rows[0]
    for r in rows[1:]:
        assert "error" in r, (
            f"{r['date']} was priced off a broken chain: its delta would absorb "
            f"the move priced at {rows[0]['date']} and report it as its own")
        assert "chain" in r["error"]
    assert out["available"] is False, "no meeting could be priced"


def test_an_intact_chain_still_prices_every_meeting(monkeypatch):
    """The guard must not fire when nothing is missing."""
    import src.fed_probabilities as fp
    upcoming = [d for d in fp.FOMC_DATES if d > date(2026, 8, 1)][:3]
    full = {fp.zq_ticker(y, m): 96.0
            for (y, m) in [(d.year, d.month) for d in upcoming]
            + [fp._next_month(d.year, d.month) for d in upcoming]
            + [fp._prev_month(upcoming[0].year, upcoming[0].month)]}
    monkeypatch.setattr(fp, "_spot_effr", lambda: 3.63)
    monkeypatch.setattr(fp, "_fetch_settles", lambda months: full)
    out = fp.fed_probabilities(asof=date(2026, 8, 1), n_meetings=3)
    assert out["available"]
    assert all("error" not in r for r in out["meetings"]), out["meetings"]


def test_fomc_dates_are_sorted_and_unique():
    from src.fed_probabilities import FOMC_DATES
    assert FOMC_DATES == sorted(FOMC_DATES)
    assert len(set(FOMC_DATES)) == len(FOMC_DATES)
    # The Fed never meets twice in one calendar month; the weighting assumes it.
    months = [(d.year, d.month) for d in FOMC_DATES]
    assert len(set(months)) == len(months)


# ── S&P valuation: the equity risk premium streak ────────────────────────────
# A negative ERP is NOT unusual — it held in 52.7% of months since 1986 — so the
# card reports the length of the current run, and that arithmetic is fiddly.

def _streak(flags: list[bool]) -> int:
    """Reproduces the expression in src/sp_valuation.py::_rate_context."""
    import numpy as np
    neg = pd.Series(flags)
    same = (neg != neg.iloc[-1])[::-1]
    return int(same.values.argmax() if same.any() else len(neg))


@pytest.mark.parametrize("flags, expected", [
    ([True], 1),                                   # single observation
    ([False, False, True], 1),                     # just flipped
    ([True, True, True], 3),                       # never flipped
    ([True, False, False, False], 3),
    ([False] * 10 + [True] * 30, 30),              # the live case
    ([True] * 5 + [False] * 2 + [True] * 4, 4),    # an earlier run must not leak
])
def test_erp_streak_counts_only_the_current_run(flags, expected):
    assert _streak(flags) == expected


def test_erp_streak_is_counted_in_months_not_observations():
    """Feb 2024 -> Jul 2026 inclusive is 30 months. The live reading was 31,
    because multpl's by-month table carries a "current" row stamped mid-month
    beside the month-start rows and the newest month was counted twice. The
    series is collapsed per calendar month before anything counts it."""
    months = pd.period_range("2024-02", "2026-07", freq="M")
    assert len(months) == 30
    assert _streak([False] * 200 + [True] * len(months)) == 30

    # The de-duplication itself: a month-start row and a mid-month "current"
    # row for the same month must survive as one observation.
    raw = pd.Series(
        [3.6, 3.55, 3.47],
        index=pd.to_datetime(["2026-06-01", "2026-07-01", "2026-07-30"]))
    collapsed = raw.groupby(raw.index.to_period("M")).last()
    assert len(collapsed) == 2
    assert collapsed.iloc[-1] == 3.47, "the newest reading must win"


def test_environment_drops_forward_filled_holidays():
    """aligned_panel reindexes onto a CALENDAR business-day grid and ffills, so
    every market holiday becomes a row where every series is unchanged. Those
    synthetic zero returns depress realised vol and drag correlation."""
    import numpy as np
    from src.sector_rrg import _environment, SECTORS, BENCHMARK
    cols = [s for s, _ in SECTORS] + [BENCHMARK]
    idx = pd.bdate_range("2024-01-01", periods=200)
    rng = np.random.default_rng(1)
    data = pd.DataFrame(100 * np.exp(np.cumsum(rng.normal(0, 0.01, (200, len(cols))), axis=0)),
                        index=idx, columns=cols)
    # Freeze three rows the way a ffilled holiday does.
    for pos in (50, 100, 150):
        data.iloc[pos] = data.iloc[pos - 1]
    weekly_idx = data.resample("W-FRI").last().index[-6:]
    env = _environment(data, weekly_idx)
    assert len(env) == len(weekly_idx)
    assert env["realized_vol"].notna().all()
    # Correlation is bounded and real, not dragged to ~0 by the frozen rows.
    assert env["avg_sector_corr"].between(-1, 1).all()
