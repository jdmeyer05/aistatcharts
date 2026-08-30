"""Regression tests for the measured event-impact axis and the clustered scoring.

Every case here guards a way a MEASURED number could quietly become an assigned
one — which is the specific failure these modules exist to prevent.

Network-free by construction: the event table is a generated JSON file and the
clustering is arithmetic on rows handed in directly.

Run: python -m pytest tests/test_event_impact.py -v
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


# ── the generated table must still agree with the study it came from ──
# The whole point of generating it is that nobody hand-edits a multiplier. If
# somebody does, this fails and names the row.

def _study():
    p = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "research", "market_movers", "output", "study.json",
    )
    if not os.path.exists(p):
        pytest.skip("study.json not present in this checkout")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def test_every_table_row_matches_the_study_it_was_generated_from():
    from src.event_impact import table
    study = _study()
    by_event = {r["event"]: r for r in study["events"]["ranking"]}

    for cal_name, row in table()["events"].items():
        src = by_event.get(row["study_event"])
        assert src is not None, f"{cal_name} points at a study event that no longer exists"
        assert row["multiplier"] == round(src["rel_abs_median"], 3), \
            f"{cal_name} multiplier drifted from the study — regenerate, do not edit"
        assert row["n"] == src["n"]
        assert row["survives_fdr"] == bool(src["survives_fdr"])


def test_nonfarm_payrolls_is_the_only_survivor():
    """The headline finding. If a second event starts surviving, that is a real
    change worth a human noticing rather than something to absorb silently."""
    from src.event_impact import table
    survivors = [k for k, v in table()["events"].items() if v["survives_fdr"]]
    assert survivors == ["Nonfarm payrolls"]


# ── the bands, which are what the UI colours by ──

def test_cpi_is_not_promoted_by_its_reputation():
    """CPI is `high` impact on the timing axis and ordinary on the measured one.
    Collapsing the two is the bug this whole axis exists to fix."""
    from src.event_impact import measured
    m = measured("CPI")
    assert m is not None
    assert m["band"] == "none"
    assert m["multiplier"] < 1.10
    assert m["rank"] > 10


def test_quad_witching_is_narrower_than_a_normal_day():
    """On dividend-adjusted closes. The reputation for wide witching days came
    from SPY's quarterly ex-dividend landing on the same date."""
    from src.event_impact import measured
    m = measured("Quad witching (OpEx)")
    assert m is not None
    assert m["multiplier"] < 1.0
    assert m["band"] == "none"


def test_payrolls_is_established():
    from src.event_impact import measured
    m = measured("Nonfarm payrolls")
    assert m is not None
    assert m["band"] == "established"
    assert m["survives_fdr"] is True


def test_fomc_is_unconfirmed_not_established():
    """Nominally significant, fails the family correction. The study's own
    phrasing is 'possibly real, not established here'."""
    from src.event_impact import measured
    m = measured("FOMC decision")
    assert m is not None
    assert m["band"] == "unconfirmed"
    assert m["survives_fdr"] is False


def test_sep_meeting_is_flagged_as_a_pooled_quote():
    """The SEP meetings were never split out of the pooled FOMC sample. Quoting
    the parent's number on the child without saying so is the quiet substitution
    this field exists to prevent."""
    from src.event_impact import measured
    m = measured("FOMC decision + SEP/dot plot")
    assert m is not None
    assert m["exact"] is False
    assert "caveat" in m


def test_unmeasured_events_return_none_not_a_default():
    """An event outside the study's universe must not come back as 1.00x. A
    fabricated ordinary reading is worse than an admitted absence."""
    from src.event_impact import measured
    for name in ("Consumer confidence", "EIA petroleum status", "U-Mich sentiment (prelim)"):
        assert measured(name) is None, f"{name} should have no measurement at all"


def test_attach_sets_the_key_explicitly_even_when_absent():
    """A missing key and a null read identically in JS, and the UI has to be
    able to say 'not measured' out loud."""
    from src.event_impact import attach
    out = attach([{"name": "Consumer confidence"}, {"name": "CPI"}])
    assert "measured" in out[0] and out[0]["measured"] is None
    assert out[1]["measured"] is not None


def test_the_two_axes_actually_disagree():
    """The reason the sizing axis was added at all. If CPI's timing label and
    its measured band ever agree, the calendar is no longer making the point."""
    from src.economic_calendar import _FRED_RELEASES
    from src.event_impact import measured
    cpi_impact = next(v[3] for v in _FRED_RELEASES.values() if v[0] == "CPI")
    assert cpi_impact == "high"
    assert measured("CPI")["band"] == "none"


def test_no_hardcoded_multiplier_left_in_the_calendar_notes():
    """The notes carried '~1.1-1.2x' for payrolls while the study that produced
    it said 1.39x — an assigned statistic sitting next to a measured one. There
    is now exactly one source for these numbers."""
    import re
    from src.economic_calendar import _FRED_RELEASES
    for name, _, _, _, note in _FRED_RELEASES.values():
        assert not re.search(r"\d\.\d+\s*x", note, re.I), \
            f"{name}'s note hardcodes a multiplier — that number belongs in the measured block"


# ── day-clustered scoring ──

def test_clustered_interval_is_wider_than_the_naive_one():
    """Ten reloads of one session are one observation, not ten. Wilson counts
    them as ten independent draws and returns an interval sqrt(m) too narrow."""
    from src.prompt_claims import _cluster_ci, _wilson
    rows = []
    for day in range(1, 13):
        outcome = day % 2 == 0          # perfectly correlated within a day
        for _ in range(10):
            rows.append({"stated_at": f"2026-08-{day:02d}T15:00:00+00:00", "correct": outcome})

    out = _cluster_ci(rows, b=500)
    naive = _wilson(sum(1 for r in rows if r["correct"]), len(rows))
    naive_width = naive[1] - naive[0]
    clustered_width = out["ci95_clustered"][1] - out["ci95_clustered"][0]

    assert out["n_days"] == 12
    assert out["claims_per_day"] == 10.0
    assert clustered_width > naive_width
    assert out["width_ratio_vs_naive"] > 1.5


def test_cluster_key_is_eastern_not_utc():
    """A claim stated at 20:00 ET is 01:00 UTC the next day. Grouping on UTC
    would split one evening across two clusters and understate the dependence."""
    from src.prompt_claims import _session_day
    assert _session_day("2026-08-05T00:00:00+00:00") == "2026-08-04"
    assert _session_day("2026-08-05T14:00:00+00:00") == "2026-08-05"


def test_single_day_refuses_an_interval():
    """One cluster is a statement about that day, not about the surface."""
    from src.prompt_claims import _cluster_ci
    out = _cluster_ci([{"stated_at": "2026-08-05T15:00:00Z", "correct": True}])
    assert out["n_days"] == 1
    assert out["ci95_clustered"] is None
    assert "note" in out


def test_too_few_clusters_refuses_rather_than_quoting_an_artefact():
    """CAUGHT LIVE ON /prompt-loop. A bootstrap over 3 days resamples from ten
    distinct multisets, so the interval it returns is shaped by having three
    days — it came back NARROWER than the naive Wilson interval it exists to
    widen, and the page rendered the self-contradictory "0.8x too narrow".
    Below the floor the interval is withheld and the point estimates stand."""
    from src.prompt_claims import _cluster_ci, _MIN_CLUSTERS
    rows = [{"stated_at": f"2026-08-{d:02d}T15:00:00Z", "correct": d % 2 == 0}
            for d in range(1, 4) for _ in range(7)]
    out = _cluster_ci(rows, b=200)
    assert out["n_days"] == 3
    assert out["ci95_clustered"] is None
    assert out["min_clusters"] == _MIN_CLUSTERS
    assert "too few" in out["note"]
    # The point estimates are still real and still reported.
    assert out["hit_rate_pooled"] is not None
    assert out["hit_rate_by_day"] is not None


def test_at_the_floor_the_interval_is_quoted_and_widens():
    """Above the floor the clustering does what it is for: the interval comes
    back WIDER than naive, not narrower."""
    from src.prompt_claims import _cluster_ci, _MIN_CLUSTERS
    rows = [{"stated_at": f"2026-08-{d:02d}T15:00:00Z", "correct": d % 2 == 0}
            for d in range(1, _MIN_CLUSTERS + 1) for _ in range(6)]
    out = _cluster_ci(rows, b=400)
    assert out["ci95_clustered"] is not None
    assert out["width_ratio_vs_naive"] > 1


def test_pooled_and_by_day_diverge_when_traffic_is_lopsided():
    """A day the page was loaded forty times should not outvote thirty-nine
    other days. When these two disagree, the record is measuring traffic."""
    from src.prompt_claims import _cluster_ci
    rows = [{"stated_at": "2026-08-03T15:00:00Z", "correct": True} for _ in range(40)]
    for day in range(4, 14):
        rows.append({"stated_at": f"2026-08-{day:02d}T15:00:00Z", "correct": False})

    out = _cluster_ci(rows, b=300)
    assert out["hit_rate_pooled"] > out["hit_rate_by_day"]
    assert out["hit_rate_by_day"] == pytest.approx(1 / 11, abs=0.01)


def test_empty_input_reports_zero_days_rather_than_a_rate():
    from src.prompt_claims import _cluster_ci
    assert _cluster_ci([]) == {"n_days": 0}


# ── calendar selection: the cap that used to drop payrolls ──

def _ev(name, days, impact=None, band=None):
    m = None if band is None else {"band": band, "multiplier": 1.4 if band == "established" else 1.2}
    return {"name": name, "date": f"2026-09-{days:02d}", "days_away": days,
            "impact": impact, "measured": m}


def test_payrolls_survives_the_cap_from_the_far_end_of_the_window():
    """The original bug: sort by date, take the nearest eight. Payrolls lands on
    the first Friday and sits past the eighth-nearest row for most of a
    fortnight, so it was dropped BEFORE the client's careful selection ever saw
    the list."""
    from api.routes.market import _select_calendar_events
    items = [_ev(f"Filler {i}", i, impact="low") for i in range(1, 13)]
    items.append(_ev("Nonfarm payrolls", 13, impact="high", band="established"))

    out = _select_calendar_events(items, cap=8)
    assert any(e["name"] == "Nonfarm payrolls" for e in out)


def test_a_low_labelled_event_with_real_measurement_is_kept():
    """The correction runs in both directions. The trade balance is labelled
    `low` by judgement and ranks 6th of 23 measured — keeping it is as much the
    point as demoting CPI."""
    from api.routes.market import _select_calendar_events
    items = [_ev(f"Filler {i}", i, impact="low") for i in range(1, 13)]
    items.append(_ev("Trade balance", 14, impact="low", band="unconfirmed"))

    out = _select_calendar_events(items, cap=8)
    assert any(e["name"] == "Trade balance" for e in out)


def test_selection_is_date_ordered_and_capped():
    from api.routes.market import _select_calendar_events
    items = [_ev(f"Filler {i}", i, impact="low") for i in range(1, 30)]
    out = _select_calendar_events(items, cap=6)
    assert len(out) == 6
    assert [e["days_away"] for e in out] == sorted(e["days_away"] for e in out)


def test_priority_events_are_never_dropped_even_past_the_cap():
    """A window full of scheduled discontinuities should return all of them
    rather than silently honouring the cap — the cap exists to trim filler."""
    from api.routes.market import _select_calendar_events
    items = [_ev(f"Big {i}", i, impact="high") for i in range(1, 11)]
    out = _select_calendar_events(items, cap=4)
    assert len(out) == 10


# ── the gate ticker's decision, which otherwise first runs unobserved ──

def _et(day, hh, mm):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime(2026, 9, day, hh, mm, tzinfo=ZoneInfo("America/New_York"))


def test_ticker_writes_inside_the_marks_own_window():
    """A Tuesday at 10:05 belongs to the 10:00 mark."""
    from api.main import _due_mark
    assert _due_mark(_et(1, 10, 5)) == 10.0
    assert _due_mark(_et(1, 10, 35)) == 10.5
    assert _due_mark(_et(1, 15, 10)) == 15.0


def test_ticker_refuses_a_mark_that_has_already_passed():
    """`_mark` returns the LAST mark at or before now, so at 16:05 it still
    answers 15.0 — writing then stamps a 16:05 snapshot onto the 15:00 slot, a
    verdict recorded half an hour after the slot it claims to describe."""
    from api.main import _due_mark
    assert _due_mark(_et(1, 16, 5)) is None
    assert _due_mark(_et(1, 15, 45)) is None      # 45 min past the 15:00 mark


def test_ticker_skips_outside_the_cash_session_and_on_weekends():
    from api.main import _due_mark
    assert _due_mark(_et(1, 9, 45)) is None       # before the first mark
    assert _due_mark(_et(1, 3, 0)) is None        # overnight
    assert _due_mark(_et(5, 10, 5)) is None       # Saturday
    assert _due_mark(_et(6, 10, 5)) is None       # Sunday


def test_ticker_covers_every_mark_the_logger_defines():
    """Each of the eleven marks must be reachable, or the record is built from
    a subset of the session without anyone noticing."""
    from api.main import _due_mark
    from src.es_gate_log import _MARKS
    hit = {_due_mark(_et(1, int(m), int(round((m % 1) * 60)))) for m in _MARKS}
    assert hit == set(_MARKS), f"unreachable marks: {set(_MARKS) - hit}"


# ── trend breadth: the one measure worth taking from Finviz's front page ──

def test_trend_breadth_windows_are_string_keyed():
    """A JSON round-trip through the Supabase cache turns int keys into strings,
    so a fresh compute and a cached read would hand callers dicts keyed
    differently depending on cache state — a KeyError on one path and not the
    other, weeks later."""
    import json
    from src import breadth_trend
    sample = {
        "available": True, "windows": {}, "universe": {"n": 10, "note": ""},
    }
    # Mirror what _compute writes, without the 200 network calls.
    for w in breadth_trend._WINDOWS:
        sample["windows"][str(w)] = {"above": 1, "below": 1, "n": 2,
                                     "pct_above": 50.0, "excluded_short_history": 0}
    assert all(isinstance(k, str) for k in sample["windows"])
    assert list(json.loads(json.dumps(sample))["windows"]) == list(sample["windows"])
    # And the source writes string keys — the thing that actually broke. Without
    # this the test passes on a fixture I built correctly by hand.
    import inspect
    assert 'out["windows"][str(w)]' in inspect.getsource(breadth_trend._compute)


def test_trend_breadth_excludes_names_without_the_full_window():
    """Averaging a 200-day over 80 sessions is a different measure wearing the
    same name, and it would quietly shift the percentage as new listings age in."""
    import inspect
    from src import breadth_trend
    src = inspect.getsource(breadth_trend._compute)
    assert "c.get(tk, 0) < w" in src, "the short-history guard is the point of this measure"


def test_trend_breadth_walk_does_not_pollute_the_shared_grouped_cache():
    """The first version left 209 frames and 2.4M rows resident for six hours
    while its own docstring claimed constant memory. The walk must pass
    cache=False."""
    import inspect
    from src import breadth_trend
    assert "cache=False" in inspect.getsource(breadth_trend._accumulate)


def test_trend_breadth_shares_the_history_routine_rather_than_copying_it():
    """Two implementations of a percentile rule drift. vol_history's record and
    percentiles are parameterised by key/tracked so this can reuse them."""
    import inspect
    from src import breadth_trend, vol_history
    assert "key" in inspect.signature(vol_history.record).parameters
    assert "tracked" in inspect.signature(vol_history.percentiles).parameters
    src = inspect.getsource(breadth_trend._compute)
    assert "from src.vol_history import percentiles, record" in src


def test_trend_breadth_never_anchors_on_a_session_in_progress():
    """Polygon's grouped-daily serves the CURRENT day's aggregate while the
    market trades. Anchoring there takes a partial close and a partial volume as
    the newest bar — and volume is the liquidity filter, so the universe would
    shrink at the open and grow through the day, drifting the percentage for a
    mechanical reason. That is the exact failure es_breadth documents for its
    own filter."""
    import pandas as pd
    from src.breadth_trend import _last_completed_session as last

    def et(s):
        return pd.Timestamp(s, tz="America/New_York")

    # Mid-session on a Tuesday -> Monday.
    assert last(et("2026-09-01 10:00")) == __import__("datetime").date(2026, 8, 31)
    assert last(et("2026-09-01 09:00")) == __import__("datetime").date(2026, 8, 31)
    # After the tape has settled -> today.
    assert last(et("2026-09-01 16:20")) == __import__("datetime").date(2026, 9, 1)
    # 16:00 exactly is NOT settled; the consolidated tape prints late.
    assert last(et("2026-09-01 16:00")) == __import__("datetime").date(2026, 8, 31)


def test_trend_breadth_calendar_span_clears_measured_need_with_margin():
    """200 trading days spanned 293 calendar days when measured. Running short
    does not degrade gracefully — it returns available:false — so the bound
    needs real margin over one extra market holiday, not seven days of it."""
    from src.breadth_trend import _CALENDAR_SPAN, _WINDOWS
    assert _CALENDAR_SPAN >= 293 + 30, "too tight; one extra holiday breaks the measure"
    assert max(_WINDOWS) == 200


def test_trend_breadth_anchor_is_stable_across_a_weekend():
    """Saturday and Sunday describe the same Friday data. An unclamped anchor
    made them two different keys, so the scheduled refresh saw the anchor move
    and re-ran a 157-second walk to produce a byte-identical answer — every
    weekend, and repeatedly."""
    import datetime as dt
    import pandas as pd
    from src.breadth_trend import _last_completed_session as last

    def et(s):
        return pd.Timestamp(s, tz="America/New_York")

    friday = dt.date(2026, 8, 28)
    assert last(et("2026-08-28 16:20")) == friday      # Friday after the close
    assert last(et("2026-08-29 12:00")) == friday      # Saturday
    assert last(et("2026-08-30 12:00")) == friday      # Sunday
    assert last(et("2026-08-31 10:00")) == friday      # Monday, mid-session


def test_trend_breadth_refresh_keys_on_the_anchor_not_the_data_date():
    """Comparing the cached `asof` to the candidate date would never settle on a
    market holiday: the newest data is legitimately older than the candidate, so
    the check stays true and the walk re-runs every minute."""
    import inspect
    from src import breadth_trend
    src = inspect.getsource(breadth_trend.refresh_due)
    assert 'cur.get("anchor")' in src
    assert 'cur.get("asof")' not in src


def test_trend_breadth_never_computes_on_the_request_path():
    """A miss on the request path is ~157s against the ES brief's 20s server
    timeout — the card would not render slowly, it would not render."""
    import inspect
    from src import breadth_trend, es_breadth
    assert "cached_only=True" in inspect.getsource(es_breadth._trend_or_none)
    assert "cached_only" in inspect.signature(breadth_trend.trend_breadth).parameters
    # And cached_only must never fall through to _compute.
    src = inspect.getsource(breadth_trend.trend_breadth)
    assert src.index("if cached_only:") < src.index("out = _compute(")


def test_trend_breadth_failed_walk_does_not_retry_every_minute(monkeypatch):
    """A failed compute does not cache, so `refresh_due` stays true. Without a
    throttle the minute ticker would re-run a ~147-second walk every sixty
    seconds for as long as the upstream stayed down — hammering the provider on
    exactly the morning it is already having trouble."""
    import time
    from src import breadth_trend as bt

    monkeypatch.setattr(bt, "_CACHE", {})
    # No cached answer at all -> genuinely due.
    monkeypatch.setattr(bt, "trend_breadth",
                        lambda *a, **k: {"available": False, "reason": "down"})
    monkeypatch.setattr(bt, "_last_attempt", 0.0)
    assert bt.refresh_due() is True, "a cold, uncached measure should be due"

    # Immediately after an attempt, it must back off rather than re-run.
    monkeypatch.setattr(bt, "_last_attempt", time.time())
    assert bt.refresh_due() is False

    # And come back once the window passes.
    monkeypatch.setattr(bt, "_last_attempt", time.time() - bt._RETRY_AFTER_S - 1)
    assert bt.refresh_due() is True


def test_trend_breadth_records_the_attempt_even_when_it_fails():
    """The throttle only works if the timestamp is set BEFORE the compute, so a
    raise still counts as an attempt."""
    import inspect
    from src import breadth_trend as bt
    src = inspect.getsource(bt.trend_breadth)
    assert src.index("_last_attempt = _now()") < src.index("out = _compute(")
