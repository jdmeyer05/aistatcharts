"""Regression tests for single-name earnings on the ES card.

Network-free, like `test_session_logic`. Everything here guards a rule that
produces correct-LOOKING output when it is wrong, which is the only kind of
failure worth a test in this part of the codebase:

  - An after-the-bell report filed under the session it cannot touch. The date
    is right, the row renders, and the trader braces for a 16:15 event during
    a 10:00 chop.
  - `before_open` computed from a wall clock, which says a 16:15 report is not
    before a 09:30 open — true of the numbers, false of the sessions.
  - A schedule sorted on `time_et`, which buries yesterday's 16:15 megacap
    report below today's 08:30 claims print.
  - An event premium that divides by a near-worthless straddle late in the day
    and reports 11x when nothing is happening.

Run: python -m pytest tests/test_earnings_calendar.py -v
"""
import json
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

# Before the 8/26 open — the only window in which the multiple is like-for-like.
_PREOPEN = pd.Timestamp("2026-08-25 21:00", tz=ET)

# Tue 2026-08-25 .. Thu 2026-08-27. NVDA reports Wednesday after the close, so
# it belongs to THURSDAY's session — this is the real calendar shape that
# started the whole thing.
_ROWS = [
    {"symbol": "NVDA", "date": "2026-08-26", "hour": "amc", "epsEstimate": 2.13},
    {"symbol": "CRM", "date": "2026-08-26", "hour": "amc", "epsEstimate": 2.77},
    {"symbol": "TINY", "date": "2026-08-26", "hour": "amc", "epsEstimate": 0.10},
    {"symbol": "BIGBMO", "date": "2026-08-26", "hour": "bmo", "epsEstimate": 1.00},
    {"symbol": "NOCOVER", "date": "2026-08-26", "hour": "amc", "epsEstimate": None},
    {"symbol": "MRVL", "date": "2026-08-27", "hour": "amc", "epsEstimate": 0.75},
]

_CAPS = {
    "NVDA": 5_160e9,
    "CRM": 168e9,
    "MRVL": 211e9,
    "BIGBMO": 1_400e9,
    "TINY": 9e9,          # below the floor
    "NOCOVER": 2_000e9,   # huge, but no analyst estimate — must never appear
}


@pytest.fixture
def cal(monkeypatch):
    import src.earnings_calendar as ec
    monkeypatch.setattr(ec, "_finnhub_window", lambda s, e: _ROWS)
    monkeypatch.setattr(ec, "_market_cap", lambda sym: _CAPS.get(sym))
    return ec


# ── Effect windows ────────────────────────────────────────────────
# The whole point of the module: an earnings row is attached to the session it
# AFFECTS, not the date it carries.

def test_after_the_close_report_belongs_to_the_next_session(cal):
    """NVDA reports Wednesday AMC. Thursday's card must show it as the gap it
    already made, not as something still to come."""
    wed = {e["symbol"]: e for e in cal.earnings_for_session(date(2026, 8, 26))}
    thu = {e["symbol"]: e for e in cal.earnings_for_session(date(2026, 8, 27))}

    assert wed["NVDA"]["affects"] == "next_session_gap"
    assert thu["NVDA"]["affects"] == "this_session_gap"
    # Same event, same date, on both cards — only the framing changes.
    assert wed["NVDA"]["date"] == thu["NVDA"]["date"] == "2026-08-26"


def test_before_the_open_report_is_this_session(cal):
    wed = {e["symbol"]: e for e in cal.earnings_for_session(date(2026, 8, 26))}
    assert wed["BIGBMO"]["affects"] == "this_session_open"
    assert (wed["BIGBMO"]["hour"], wed["BIGBMO"]["minute"]) < (9, 30)


def test_a_monday_session_reaches_back_over_the_weekend(cal):
    """Friday-AMC reports are Monday's gap. A naive date-1 lookup lands on
    Sunday and silently finds nothing."""
    import src.earnings_calendar as ec
    assert ec._prev_trading_day(date(2026, 8, 31)) == date(2026, 8, 28)
    assert ec._next_trading_day(date(2026, 8, 28)) == date(2026, 8, 31)


@pytest.mark.parametrize("session_day,expected,why", [
    (date(2026, 5, 26), date(2026, 5, 22), "Tue after Memorial Day"),
    (date(2026, 1, 20), date(2026, 1, 16), "Tue after MLK"),
    (date(2026, 7, 6), date(2026, 7, 2), "Mon after July 4 observed on Fri"),
    (date(2026, 4, 6), date(2026, 4, 2), "Mon after Good Friday — exchange shut"),
])
def test_holidays_are_stepped_over_not_landed_on(session_day, expected, why):
    """Weekends were not enough. On the Tuesday after Memorial Day the previous
    calendar weekday IS the holiday, so the AMC lookup landed on a closed
    market, found nothing, and the panel rendered empty on a morning that opens
    on Friday's reports — the same silent-absence failure this module exists to
    fix, one layer down."""
    import src.earnings_calendar as ec
    assert ec._prev_trading_day(session_day) == expected, why


@pytest.mark.parametrize("session_day,expected,why", [
    (date(2026, 10, 13), date(2026, 10, 12), "Columbus Day — exchange OPEN"),
    (date(2026, 11, 12), date(2026, 11, 11), "Veterans Day — exchange OPEN"),
])
def test_federal_only_holidays_are_still_trading_days(session_day, expected, why):
    """The federal calendar would have been the easy proxy and is wrong in this
    direction: Columbus Day and Veterans Day are federal holidays on which the
    exchange trades and companies report. Skipping them steps over a real
    report day, the mirror image of the bug above."""
    import src.earnings_calendar as ec
    assert ec._prev_trading_day(session_day) == expected, why


def test_truncated_lists_say_they_are_truncated(monkeypatch):
    """Showing the top three silently reads as 'three names report tonight',
    which is a different session from six."""
    import src.earnings_calendar as ec
    many = [{"symbol": f"S{i}", "date": "2026-08-26", "hour": "amc",
             "epsEstimate": 1.0, "revenueEstimate": 1e9 * (50 - i)} for i in range(6)]
    monkeypatch.setattr(ec, "_finnhub_window", lambda s, e: many)
    monkeypatch.setattr(ec, "_market_cap", lambda sym: 2_000e9)

    out = ec.earnings_for_session(date(2026, 8, 26))
    assert len(out) == ec._MAX_PER_WINDOW
    last = out[-1]
    assert last["also_reporting"] == ["S3", "S4", "S5"]
    assert "without rows of their own" in last["note"]
    # Must not claim to be everything that reports.
    assert "not tracked here" in last["note"]


# ── Selection ─────────────────────────────────────────────────────

def test_small_caps_are_dropped_and_uncovered_names_never_priced(cal):
    wed = {e["symbol"] for e in cal.earnings_for_session(date(2026, 8, 26))}
    assert "TINY" not in wed                # below the floor
    # NOCOVER is above every threshold but has no EPS estimate, so it is
    # filtered BEFORE any market-cap lookup — the pre-filter is what keeps this
    # affordable inside a request handler, so it has to actually bite.
    assert "NOCOVER" not in wed


def test_tiers_follow_size(cal):
    wed = {e["symbol"]: e for e in cal.earnings_for_session(date(2026, 8, 26))}
    assert wed["NVDA"]["impact"] == "high"      # $5.16T
    assert wed["CRM"]["impact"] == "low"        # $168B
    thu = {e["symbol"]: e for e in cal.earnings_for_session(date(2026, 8, 27))}
    assert thu["MRVL"]["impact"] == "low"       # $211B, under the medium cut


def test_nothing_claims_to_be_an_index_weight(cal):
    """No constituent feed exists on this stack. The note must say what the
    number is, because a bare '$5.16T' beside an index card reads as weight."""
    wed = {e["symbol"]: e for e in cal.earnings_for_session(date(2026, 8, 26))}
    note = wed["NVDA"]["note"].lower()
    assert "not index weight" in note
    assert wed["NVDA"]["market_cap"] == 5_160e9


def test_a_dead_feed_degrades_to_an_empty_list(monkeypatch):
    """The earnings feed must never take the ES bundle down with it — a missing
    calendar degrades to the macro-only card it replaced."""
    import src.earnings_calendar as ec
    monkeypatch.setattr(ec, "_finnhub_window", lambda s, e: [])
    assert ec.earnings_for_session(date(2026, 8, 26)) == []


def test_unsizeable_candidates_are_logged_not_swallowed(monkeypatch, caplog):
    """This actually happened: FMP answered 429, every cap came back None, and
    the panel rendered empty — indistinguishable from a calm evening. An empty
    panel caused by a dead vendor has to say so somewhere."""
    import src.earnings_calendar as ec
    monkeypatch.setattr(ec, "_finnhub_window", lambda s, e: _ROWS)
    monkeypatch.setattr(ec, "_market_cap", lambda sym: None)

    with caplog.at_level("WARNING"):
        assert ec.earnings_for_session(date(2026, 8, 26)) == []
    assert any("no market cap resolved" in r.message for r in caplog.records)


def test_a_failed_lookup_is_cached_so_one_429_is_not_a_storm(monkeypatch):
    """Without a negative cache a rate-limited vendor is re-asked once per
    symbol per render, turning a transient 429 into a sustained outage."""
    import src.earnings_calendar as ec
    ec._CACHE.clear()
    calls = {"n": 0}

    def boom(symbol):
        calls["n"] += 1
        raise RuntimeError("429")

    monkeypatch.setattr(ec, "_cap_from_massive", boom)
    monkeypatch.setattr(ec, "_cap_from_fmp", lambda s: None)

    assert ec._market_cap("NVDA") is None
    assert ec._market_cap("NVDA") is None
    assert calls["n"] == 1
    ec._CACHE.clear()


def test_the_fallback_vendor_is_used_when_the_primary_fails(monkeypatch):
    import src.earnings_calendar as ec
    ec._CACHE.clear()
    monkeypatch.setattr(ec, "_cap_from_massive", lambda s: (_ for _ in ()).throw(RuntimeError("429")))
    monkeypatch.setattr(ec, "_cap_from_fmp", lambda s: 4_000e9)
    assert ec._market_cap("NVDA") == 4_000e9
    ec._CACHE.clear()


def test_only_the_shortlist_is_priced(monkeypatch):
    """The cold-cache cost of the panel is bounded, or a busy Thursday spends
    ~25 lookups per window to show three names."""
    import src.earnings_calendar as ec
    many = [{"symbol": f"S{i}", "date": "2026-08-26", "hour": "amc",
             "epsEstimate": 1.0, "revenueEstimate": 1e9 * (50 - i)} for i in range(25)]
    monkeypatch.setattr(ec, "_finnhub_window", lambda s, e: many)
    asked: list[str] = []

    def track(sym):
        asked.append(sym)
        return 2_000e9

    monkeypatch.setattr(ec, "_market_cap", track)
    out = ec.earnings_for_session(date(2026, 8, 26))

    assert len(asked) <= ec._SHORTLIST
    # Shortlisted by revenue, so the biggest names are the ones that got priced.
    assert "S0" in asked and "S24" not in asked
    assert len(out) == ec._MAX_PER_WINDOW


# ── Merged schedule ───────────────────────────────────────────────

@pytest.fixture
def sched(monkeypatch, cal):
    import src.es_session as es
    monkeypatch.setattr(
        "src.economic_calendar.todays_events",
        lambda d: [{"name": "Initial jobless claims", "date": "2026-08-27",
                    "time_et": "08:30", "hour": 8, "minute": 30,
                    "impact": "medium", "note": "", "source": "fred",
                    "derived": False}],
    )
    return es


def test_yesterdays_report_is_before_todays_open(sched):
    """16:15 is not before 09:30 on a clock, but Wednesday's 16:15 IS before
    Thursday's open. Comparing wall times instead of instants marked the single
    biggest event of the session as mid-session risk."""
    now = pd.Timestamp("2026-08-27 10:00", tz=ET)
    rows = {e["name"]: e for e in sched.todays_schedule(now)}
    nvda = rows["NVDA earnings"]
    assert nvda["before_open"] is True
    assert nvda["status"] == "released"
    assert nvda["affects"] == "this_session_gap"


def test_schedule_sorts_on_the_instant_not_the_wall_clock(sched):
    """Sorted on `time_et`, yesterday's 16:15 megacap report sorts BELOW
    today's 08:30 claims print — the biggest thing on the card, last."""
    now = pd.Timestamp("2026-08-27 10:00", tz=ET)
    names = [e["name"] for e in sched.todays_schedule(now)]
    assert names.index("NVDA earnings") < names.index("Initial jobless claims")


def test_after_the_bell_report_is_not_next_on_the_clock(sched):
    """`next_event` drives the countdown and the 'liquidity thins into it'
    warning. Pointing either at a 16:15 report tells a trader to brace for
    something that cannot touch their range, so it must be routed to
    `after_close` instead — and still be somewhere, not dropped."""
    now = pd.Timestamp("2026-08-27 10:00", tz=ET)
    routed = sched.split_schedule(sched.todays_schedule(now))

    assert routed["next_event"] is None or \
        routed["next_event"]["affects"] != "next_session_gap"
    assert [e["name"] for e in routed["after_close"]] == ["MRVL earnings"]
    assert all(e["affects"] != "next_session_gap" for e in routed["high_impact_today"])


def test_after_the_bell_report_is_routed_not_dropped(sched):
    """The failure this replaced was silence. Excluding it from the countdown
    must not exclude it from the card."""
    now = pd.Timestamp("2026-08-27 10:00", tz=ET)
    assert any(e["name"] == "MRVL earnings" for e in sched.todays_schedule(now))


def test_a_high_impact_report_before_the_open_still_counts_as_this_session(sched):
    """Routing keys on `affects`, not on `kind` — a pre-open megacap report is
    exactly as much this session's risk as an 08:30 print, and filtering all
    earnings out of the intraday view would lose it."""
    rows = [
        {"name": "BIGBMO earnings", "affects": "this_session_open",
         "impact": "high", "status": "upcoming", "minutes_away": 45},
        {"name": "MRVL earnings", "affects": "next_session_gap",
         "impact": "high", "status": "upcoming", "minutes_away": 400},
    ]
    routed = sched.split_schedule(rows)
    assert routed["next_event"]["name"] == "BIGBMO earnings"
    assert [e["name"] for e in routed["high_impact_today"]] == ["BIGBMO earnings"]


def test_split_is_pure_on_a_macro_only_schedule(sched):
    """Macro rows carry no `affects` at all; they must all stay intraday."""
    rows = [{"name": "CPI", "impact": "high", "status": "upcoming", "minutes_away": 20}]
    routed = sched.split_schedule(rows)
    assert routed["after_close"] == []
    assert routed["next_event"]["name"] == "CPI"


def test_macro_rows_keep_their_shape(sched):
    """Adding a second calendar must not change what a macro row looks like."""
    now = pd.Timestamp("2026-08-27 10:00", tz=ET)
    claims = next(e for e in sched.todays_schedule(now)
                  if e["name"] == "Initial jobless claims")
    assert claims["kind"] == "macro"
    assert claims["affects"] is None
    assert claims["time_approx"] is False
    assert claims["before_open"] is True


# ── Event premium ─────────────────────────────────────────────────

@pytest.fixture
def prem(monkeypatch):
    import src.es_expected_move as em
    monkeypatch.setattr(em, "_spx_spot", lambda: 7675.83)
    return em


def _straddles(em, monkeypatch, today: float, nxt: float):
    def fake(spot, expiry):
        val = today if expiry == "2026-08-26" else nxt
        return {"expiry": expiry, "strike": 7675.0, "straddle": val,
                "call": val / 2, "put": val / 2, "strike_offset": 0.0,
                "quote_source": "quote"}
    monkeypatch.setattr(em, "_atm_straddle", fake)


def test_segment_is_the_variance_difference_not_the_price_difference(prem, monkeypatch):
    """Variance is additive, prices are not. Subtracting straddles gives 30 for
    a segment the market prices at 52 — a 42% understatement of the event."""
    _straddles(prem, monkeypatch, 30.0, 60.0)
    r = prem.event_premium(pd.Timestamp("2026-08-26"), now=_PREOPEN)
    assert r["available"] is True
    assert r["segment_handles"] == pytest.approx(51.96, abs=0.01)   # sqrt(60²−30²)
    assert r["segment_handles"] != pytest.approx(30.0, abs=0.01)    # not 60−30
    assert r["vs_session"] == pytest.approx(1.73, abs=0.01)


def test_an_ordinary_night_prices_near_one_session(prem, monkeypatch):
    """Calibration check: two equal-variance sessions must come back at 1.0, so
    a multiple above 1 always means something is actually being paid for."""
    _straddles(prem, monkeypatch, 30.0, 30.0 * (2 ** 0.5))
    r = prem.event_premium(pd.Timestamp("2026-08-26"), now=_PREOPEN)
    assert r["vs_session"] == pytest.approx(1.0, abs=0.01)


def test_the_multiple_is_withheld_once_the_session_is_running(prem, monkeypatch):
    """The denominator is not a constant — it is what is LEFT of the current
    period. Pre-open it spans a session; at 10:00 it spans six hours while the
    numerator still spans a full close-to-close segment, so the same night
    reads 1.7x before the open and ~3.7x after it. The second number is wrong,
    not merely caveated."""
    _straddles(prem, monkeypatch, 15.0, 57.8)
    r = prem.event_premium(pd.Timestamp("2026-08-26"),
                           now=pd.Timestamp("2026-08-26 10:00", tz=ET))
    assert r["available"] is True
    assert r["vs_session"] is None
    assert r["baseline_is_full_session"] is False
    assert "vs_session_withheld" in r
    # The priced segment does not depend on the clock, so it is still reported.
    assert r["segment_handles"] == pytest.approx(55.82, abs=0.05)
    # And the inflated figure must appear nowhere in the payload.
    assert "3.7" not in json.dumps(r)


def test_the_same_night_is_published_before_the_open(prem, monkeypatch):
    """Same straddles, pre-open: the comparison is like-for-like and stands."""
    _straddles(prem, monkeypatch, 29.67, 57.8)
    r = prem.event_premium(pd.Timestamp("2026-08-26"),
                           now=pd.Timestamp("2026-08-25 21:00", tz=ET))
    assert r["baseline_is_full_session"] is True
    assert r["vs_session"] == pytest.approx(1.67, abs=0.01)


def test_a_decayed_close_of_day_baseline_never_reports_a_multiple(prem, monkeypatch):
    """At a Monday close the 0DTE straddle was 2.53 against 29.67 — an 11x
    reading that says how little time is left today, not how big the event is.
    The clock gate has to catch this case too."""
    _straddles(prem, monkeypatch, 2.53, 29.67)
    r = prem.event_premium(pd.Timestamp("2026-08-26"),
                           now=pd.Timestamp("2026-08-26 15:55", tz=ET))
    assert r["vs_session"] is None
    assert r["segment_handles"] == pytest.approx(29.56, abs=0.05)


def test_no_premium_when_the_next_expiry_prices_no_more(prem, monkeypatch):
    _straddles(prem, monkeypatch, 60.0, 55.0)
    r = prem.event_premium(pd.Timestamp("2026-08-26"), now=_PREOPEN)
    assert r["available"] is False


def test_a_missing_chain_degrades_without_raising(prem, monkeypatch):
    monkeypatch.setattr(prem, "_atm_straddle", lambda spot, expiry: None)
    r = prem.event_premium(pd.Timestamp("2026-08-26"), now=_PREOPEN)
    assert r["available"] is False
    assert "straddle" in r["reason"]


def test_next_session_expiry_skips_the_weekend(prem):
    """A Friday session's next expiry is Monday. Landing on Saturday returns no
    chain at all, which would silently kill the premium every Friday."""
    assert prem._next_session_expiry(pd.Timestamp("2026-08-28")) == "2026-08-31"
