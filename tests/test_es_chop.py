"""Tests for the chop/trend read.

Weighted towards the bugs this module actually produced rather than towards
coverage: a helper deleted by an over-wide edit and hidden by a blanket except,
a statistic truncated to the wrong window, and a probability compared against
the occurrence of a different outcome. Each of those shipped, and each is one
assertion away from being caught.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.es_chop import (
    _band, _bucket_idx, _EDGES, _er, _EXACT_MAX_N, _hourly_rows, _hour_panel,
    _panel, _sign_flip_p, session_chop,
)

_TZ = "America/New_York"


# ---------------------------------------------------------------- efficiency

def test_er_of_a_straight_line_is_one():
    assert _er(np.array([1.0, 2.0, 3.0, 4.0])) == pytest.approx(1.0)


def test_er_of_a_round_trip_is_zero():
    assert _er(np.array([1.0, 2.0, 3.0, 2.0, 1.0])) == pytest.approx(0.0)


def test_er_needs_three_points_and_movement():
    assert np.isnan(_er(np.array([1.0, 2.0])))
    assert np.isnan(_er(np.array([5.0, 5.0, 5.0])))     # no travel to divide by


# ------------------------------------------------------------- the null test

def test_sign_flip_flags_a_straight_line_as_trending():
    """A monotone run is the most extreme outcome available, so essentially no
    sign-flipped world matches it."""
    p_trend, p_chop = _sign_flip_p(np.ones(12))
    assert p_trend < 0.01
    assert p_chop == pytest.approx(1.0)


def test_sign_flip_flags_an_alternating_run_as_chop():
    """Magnitudes must VARY. With twelve identical ones an alternating run has
    an efficiency of exactly zero, and so does every sign-flipped world that
    happens to split six-six — C(12,6)/2^12 = 0.226 — so the p-value bottoms out
    around a fifth however choppy the run looks. See the discreteness test."""
    r = np.array([2.4, -0.7, 1.9, -2.8, 0.6, -1.5,
                  2.2, -0.4, 1.1, -2.6, 0.8, -1.0])
    assert abs(r.sum()) < 0.05 * np.abs(r).sum()      # genuinely goes nowhere
    p_trend, p_chop = _sign_flip_p(r)
    assert p_chop < 0.05
    assert p_trend > 0.9


def test_the_choppy_tail_has_an_atom_at_zero():
    """Why the choppy side is harder to clear than the trending side, measured:
    12.9% of sessions clear p<0.10 trending against 7.6% chopping. The null puts
    real mass exactly on zero net progress whenever the signs can balance, so a
    perfectly choppy run is not a rare event under it. A trend has no such
    competition — only one sign vector produces a straight line."""
    from math import comb
    p_trend, p_chop = _sign_flip_p(np.array([1.0, -1.0] * 6))
    assert p_chop == pytest.approx(comb(12, 6) / 2 ** 12, abs=1e-9)
    assert p_trend == pytest.approx(1.0)


def test_sign_flip_is_a_probability_and_handles_degenerate_input():
    for r in (np.array([1.0, -1.0, 2.0, -3.0, 1.0, 0.5, -0.5, 2.0]),
              np.array([0.3, 0.1, -0.2, 0.4, -0.1, 0.2, 0.1, -0.3])):
        pt, pc = _sign_flip_p(r)
        assert 0.0 <= pt <= 1.0 and 0.0 <= pc <= 1.0
        # Both tails include the observation itself, so they exceed 1 together
        # by exactly the mass sitting on it — never by more.
        assert pt + pc >= 1.0
    assert all(np.isnan(x) for x in _sign_flip_p(np.array([1.0, 2.0])))   # too short
    assert all(np.isnan(x) for x in _sign_flip_p(np.zeros(10)))           # no travel


def test_sign_flip_is_deterministic_above_the_enumeration_cap():
    """Past the cap the null is sampled, and a p-value that moved between loads
    would make the card flicker. The seed is fixed for that reason."""
    r = np.linspace(-1.0, 1.0, _EXACT_MAX_N + 6) + 0.1
    assert _sign_flip_p(r) == _sign_flip_p(r)


def test_enumeration_cap_stays_within_a_sane_allocation():
    """The exact branch allocates 2^(n-1) x (n-1) floats. At n=20 that is 80MB
    in one call; the cap exists to keep it near 4."""
    mb = (1 << (_EXACT_MAX_N - 1)) * (_EXACT_MAX_N - 1) * 8 / 1e6
    assert mb < 8.0


# ------------------------------------------------------------------ buckets

def test_bucket_idx_boundaries():
    ix = pd.to_datetime([
        "2026-08-31 09:29", "2026-08-31 09:30", "2026-08-31 10:29",
        "2026-08-31 10:30", "2026-08-31 15:30", "2026-08-31 15:59",
        "2026-08-31 16:00",
    ]).tz_localize(_TZ)
    assert list(_bucket_idx(ix)) == [-1, 0, 0, 1, 6, 6, -1]


def test_band_always_contains_the_percentile_it_reports():
    rng = np.random.default_rng(0)
    hist = np.sort(rng.random(500))
    edges = np.quantile(hist, _EDGES)
    for v in np.quantile(hist, [0.01, 0.15, 0.3, 0.5, 0.7, 0.85, 0.99]):
        lo, hi, _ = _band(v, edges)
        pct = float((hist < v).mean())
        assert lo - 1e-9 <= pct <= hi + 1e-9


# ------------------------------------------------------- synthetic end to end

def _synth(n_sessions: int = 320, seed: int = 4) -> pd.DataFrame:
    """Random-walk sessions on the real 5-minute RTH grid."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2024-01-02", periods=n_sessions, tz=_TZ)
    frames = []
    for d in days:
        idx = pd.date_range(d + pd.Timedelta(hours=9, minutes=30), periods=78,
                            freq="5min")
        px = 400 + np.cumsum(rng.normal(0, 0.15, 78))
        frames.append(pd.DataFrame({"Open": px, "High": px + 0.1,
                                    "Low": px - 0.1, "Close": px}, index=idx))
    return pd.concat(frames)


def test_panel_drops_short_sessions():
    f = _synth(210)
    last = f.index.normalize().max()
    short = f[f.index.normalize() == last].iloc[:40]
    mixed = pd.concat([f[f.index.normalize() != last], short])
    assert last not in _panel(mixed).index


def test_hourly_rows_never_score_an_unfinished_hour():
    f = _synth(210)
    hp = _hour_panel(f)
    day = f.index.normalize().max()
    partial = f[(f.index.normalize() == day) & (f.index.hour < 11)]
    rows = {r["bucket"]: r for r in _hourly_rows(partial, hp)}
    assert rows["09:30"]["state"] == "complete"
    for b in ("12:30", "13:30", "14:30", "15:30"):
        assert rows[b]["state"] in ("pending", "not_started")
        assert "verdict" not in rows[b], "an unfinished hour must carry no verdict"


def test_session_chop_shape_and_bounds(monkeypatch):
    f = _synth(320)
    day = f.index.normalize().max()
    # Never touch the network in a test; hand back the synthetic session.
    monkeypatch.setattr("src.es_chop._today_bars",
                        lambda d: f[f.index.normalize() == day])
    out = session_chop(fine=f, now=day + pd.Timedelta(hours=15, minutes=5))
    assert out["available"] is True
    assert out["label"] in {"mixed", "likely choppy", "confident choppy",
                            "likely trendy", "confident trendy"}
    assert 0.0 <= out["pctile"] <= 100.0
    assert out["sessions"] <= out["history_available"]
    for key in ("p_finish_choppy_pct", "p_finish_trendy_pct",
                "base_choppy_pct", "base_trendy_pct"):
        assert out[key] is None or 0.0 <= out[key] <= 100.0


def test_random_walk_test_uses_every_bar_not_just_up_to_the_mark(monkeypatch):
    """The regression that hid the strongest reading on the card.

    The percentile must be clock-matched, so it stops at the last completed
    30-minute mark. The sign-flip test compares the session only against
    sign-flipped copies of itself and needs no matching — truncating it there
    once turned p=0.019 into p=0.177 and printed "coin flip" over the choppiest
    session in the sample.
    """
    f = _synth(320)
    day = f.index.normalize().max()
    sess = f[f.index.normalize() == day]
    monkeypatch.setattr("src.es_chop._today_bars", lambda d: sess)
    out = session_chop(fine=f, now=day + pd.Timedelta(hours=15, minutes=5))
    assert out["mark"] == "15:00"
    assert out["random_walk"]["bars"] == len(sess)
    assert out["random_walk"]["through"] == sess.index[-1].strftime("%H:%M")


def test_hourly_forecast_null_is_carried_not_hidden(monkeypatch):
    f = _synth(320)
    day = f.index.normalize().max()
    monkeypatch.setattr("src.es_chop._today_bars",
                        lambda d: f[f.index.normalize() == day])
    out = session_chop(fine=f, now=day + pd.Timedelta(hours=15, minutes=5))
    fc = out["hourly_forecast"]
    assert fc["verdict"] == "null"
    assert fc["accuracy_pct"] <= fc["baseline_pct"] + 0.5


def test_random_walk_verdict_matches_its_own_p_values(monkeypatch):
    f = _synth(320)
    day = f.index.normalize().max()
    monkeypatch.setattr("src.es_chop._today_bars",
                        lambda d: f[f.index.normalize() == day])
    rw = session_chop(fine=f, now=day + pd.Timedelta(hours=15, minutes=5))["random_walk"]
    if rw["verdict"] == "trended":
        assert rw["p_trend"] < 0.10
    elif rw["verdict"] == "chopped":
        assert rw["p_chop"] < 0.10
    else:
        assert rw["p_trend"] >= 0.10 and rw["p_chop"] >= 0.10


# --------------------------------------------------------------- the record

def test_mixed_rows_carry_the_probability_of_being_mixed():
    """A "mixed" row is scored on the session finishing mixed, so it must carry
    P(mixed). It used to carry max(p_chop, p_trend) — the odds of one outcome
    against the occurrence of another — which read as a z=9.2 miscalibration."""
    from src.es_chop_record import _label_for
    rng = np.random.default_rng(1)
    hist = rng.random(600)
    fin = rng.random(600)
    lo_f, hi_f = np.quantile(fin, [1 / 3, 2 / 3])
    label, side, p = _label_for(float(np.median(hist)), hist, fin, lo_f, hi_f,
                                _EDGES, 0.65, 0.45, 40)
    if label == "mixed":
        m = (fin >= lo_f) & (fin < hi_f)
        # The band the median falls in is roughly the middle of the sample.
        assert 0.0 <= p <= 1.0
        assert abs(p - m.mean()) < 0.25
        assert side == "mixed"


def test_a_bucket_short_by_one_bar_is_pending_not_complete():
    """The 0.8 threshold this replaces let a 10-bar hour be ranked against a
    population of 12-bar hours. Efficiency falls with bar count, so the short
    hour read systematically more trending — the exact bias the module exists to
    avoid, reintroduced by a tolerance."""
    f = _synth(210)
    hp = _hour_panel(f)
    day = f.index.normalize().max()
    sess = f[f.index.normalize() == day]
    short = sess[~((sess.index.hour == 9) & (sess.index.minute == 55))]
    rows = {r["bucket"]: r for r in _hourly_rows(short, hp)}
    assert rows["09:30"]["state"] == "pending"
    assert rows["10:30"]["state"] == "complete"


def test_bar_counts_are_closes_and_returns_is_one_fewer():
    f = _synth(210)
    hp = _hour_panel(f)
    day = f.index.normalize().max()
    for r in _hourly_rows(f[f.index.normalize() == day], hp):
        if r["state"] != "complete":
            continue
        assert r["bars"] == r["bars_expected"]
        assert r["returns"] == r["bars"] - 1


def test_an_untested_hour_is_never_called_a_coin_flip():
    """"Coin flip" means the null RAN and was not beaten. When it could not run,
    that is a different statement and must not borrow the same words."""
    f = _synth(210)
    hp = _hour_panel(f)
    day = f.index.normalize().max()
    for r in _hourly_rows(f[f.index.normalize() == day], hp):
        if r.get("verdict") == "coin flip":
            assert r["p"] is not None
        if r.get("p") is None:
            assert r.get("verdict") != "coin flip"
