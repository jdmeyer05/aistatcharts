"""Numeric grounding: does every number in the prose trace to the payload?

Every case here is a real production string. The checker had THREE interacting
bugs, found 2026-08-29, and they masked each other so completely that the rule
looked like it was working at ~90% precision:

  1. `_NUM_TOKEN` ate the first letter of the following word and read it as a
     scale suffix -- "support near 765.00 to hold" became 765 TRILLION. Every
     number followed by a t/b/m/k word was compared at 1e9 or 1e12, so nothing
     could match and the rule flagged it. That bug was MANUFACTURING the flags.
  2. A flat 2% tolerance. On a price that is enormous -- 2% of 765 is 15.3, so
     "support near 765.00" would match an index anywhere from 750 to 780.
  3. An unconstrained O(n^2) search over every ratio of every pair of payload
     numbers, at 2% tolerance. Measured on a real payload: 293 of 300 randomly
     invented numbers in (0.01, 1000) came back "grounded" -- 98%. The rule
     could not flag anything below 1000, which is why every token it ever
     caught in production (250K, 240K, 1.3M) is above that line.

Fixing (1) alone would have flipped the rule from over-flagging to
under-flagging: the true positives it had caught by accident would have started
passing. All three had to move together.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.grounding import _NUM_TOKEN, _check_grounding, _normalize_num  # noqa: E402


def _flags(prose: str, payload: dict) -> list[str]:
    return [t.strip() for t in (_check_grounding(prose, payload).get("unverified_tokens") or [])]


# ── bug 1: a scale suffix must be a suffix, not the next word ─────

def test_the_next_word_is_not_a_scale_suffix():
    """The exact strings that produced '765.00 t' and '7702 b' in production."""
    for prose, expected in (
        ("watch for SPY support near 765.00 to hold", 765.0),
        ("the index at 7702 basis", 7702.0),
        ("DXY at 99.50 today", 99.5),
        ("crude near $120 through the week", 120.0),
        ("SPY at 760 but fading", 760.0),
    ):
        tok = _NUM_TOKEN.findall(prose)[0]
        assert _normalize_num(tok)[0] == expected, (prose, tok)


def test_real_scale_suffixes_still_scale():
    for token, expected in (("250K", 250_000.0), ("1.3M", 1_300_000.0),
                            ("$1.2B", 1.2e9), ("$3.2T", 3.2e12),
                            ("2.5 billion", 2.5e9), ("400 thousand", 400_000.0),
                            ("15.3%", 15.3), ("1.37x", 1.37)):
        got = _normalize_num(_NUM_TOKEN.findall(token)[0])[0]
        assert got == expected, (token, got)


# ── bug 2: tolerance must be tight, and per class ─────────────────

def test_an_invented_support_level_is_caught():
    """The 765.00 case. SPY is at 769.245 and the payload has no level field —
    0.55% away, which the old 2% tolerance swallowed whole."""
    payload = {"quotes": {"SPY": {"price": 769.245, "change_pct_1d": -0.24}}}
    assert "765.00" in _flags("watch for SPY support near 765.00 to hold", payload)


def test_a_rounded_reference_to_a_real_level_is_not_invention():
    """The 7702 case: the payload holds 7701.25, the note rounds it. 0.0097%."""
    payload = {"levels": {"prior_low": 7701.25, "prior_close": 7700.93}}
    assert _flags("the index held 7702 into the close", payload) == []


def test_percent_rounding_to_one_decimal_is_allowed():
    payload = {"quotes": {"SPY": {"change_pct_1d": -0.24}}}
    assert _flags("SPY fell 0.2% on the session", payload) == []


def test_a_wrong_percent_is_still_caught():
    payload = {"quotes": {"SPY": {"change_pct_1d": -0.24}}}
    assert "1.8%" in _flags("SPY fell 1.8% on the session", payload)


# ── bug 3: a ratio has to look like a ratio ───────────────────────

def test_the_ratio_path_does_not_ground_an_invented_level():
    """With 60+ payload numbers there are thousands of candidate quotients, and
    the old unconstrained search grounded 98% of invented values under 1000."""
    payload = {"a": 177, "b": 129, "c": 4.2, "d": 1520, "e": 0.37,
               "f": 88.1, "g": 12.5, "h": 940, "i": 3.14, "j": 66.6}
    assert "765.00" in _flags("support at 765.00 holds", payload)


def test_a_genuine_ratio_still_grounds():
    """The case the path exists for: 177/129 = 1.372."""
    payload = {"purchases": 177, "sales": 129}
    assert _flags("buying ran 1.37x selling", payload) == []


# ── the tolerance has to travel with the scale ────────────────────

def test_the_decimal_candidate_does_not_get_a_hundredfold_tolerance():
    """Payloads store percents as decimals, so a percent token is also tried as
    n/100. Computing the tolerance from the DIVIDED value made it 100x looser --
    an absolute 0.05 on 0.45 is five percentage points, so a claimed "45" matched
    a payload holding 0.42."""
    assert "45" in _flags("a reading of 45 here", {"x": 0.42})


def test_a_correctly_rounded_decimal_percent_still_grounds():
    """0.153 stored, "15.3%" written — the case the /100 path exists for."""
    assert _flags("breadth ran 15.3% of names", {"up_pct": 0.153}) == []
    assert _flags("breadth ran 15.3% of names", {"up_pct": 0.1528}) == []


# ── the defect class this surfaced ────────────────────────────────

def test_invented_round_support_levels_are_the_recurring_defect():
    """Three separate production notes invented a round level near the real
    price, and every one of them was silently grounded by the ratio path."""
    cases = [
        ("QQQ support sits at 700.00 ahead of earnings", {"QQQ": 706.832}, "700.00"),
        ("For SPY, monitor support at 765; a break shifts the tone", {"SPY": 769.06}, "765"),
        ("a print below 98.0 would accelerate defensive flows", {"dxy": 99.041}, "98.0"),
    ]
    for prose, payload, expected in cases:
        assert expected in _flags(prose, {"quotes": payload}), prose
