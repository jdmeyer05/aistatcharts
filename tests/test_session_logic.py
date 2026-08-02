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
