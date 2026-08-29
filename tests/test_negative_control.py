"""The negative control: rules must not fire on output we assert is clean.

WHY THIS EXISTS. Two rules have now shipped to production while broken, and
neither was caught by reading code or by watching the dashboard:

  - `invented_ticker` fired 18 times over nine days and was wrong all 18 times.
    It flagged desk vocabulary — VAH, PVAL, PDH, RVOL, TGA — as fabricated
    securities.
  - `ungrounded_number` had THREE interacting bugs. It read the first letter of
    the following word as a scale suffix (so "765.00 to hold" became 765
    trillion, which is what manufactured its findings), applied a flat 2%
    tolerance, and grounded 98% of invented values under 1000 through an
    unconstrained ratio search.

Both looked healthy the entire time. The static-analysis literature explains
why and is blunt about it: developers identify true positives well, but their
false-positive identification rate is **no better than chance**, and experience
does not help. You cannot eyeball this. Google's Tricorder requires a new check
to hold under 10% effective false positives and DISABLES analyzers that exceed
it; the fleet-wide rate sits just under 5%.

THE MECHANISM. Freeze a corpus of production outputs that the current rules
score clean. Any rule change that starts firing on them is presumed broken until
someone looks. This is a regression test for PRECISION, which is the axis a
normal test suite never covers — every other test here asserts that a rule
catches something.

REFRESHING THE CORPUS. Only ever add rows that a human has read. Regenerating it
from whatever currently scores clean would make it circular: a rule that stops
firing would silently rewrite its own control. If a row here legitimately
becomes a defect (the rules got better), delete that row in the same commit that
changes the rule, and say why.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import prompt_rules  # noqa: E402

_FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "fixtures", "negative_control.json")

with open(_FIXTURE, encoding="utf-8") as _f:
    CORPUS = json.load(_f)


def test_the_corpus_is_big_enough_to_mean_something():
    """A control that covers one surface only protects that surface."""
    assert len(CORPUS) >= 50, len(CORPUS)
    seen = Counter(r["surface"] for r in CORPUS)
    assert set(seen) >= {"market_driver", "news_digest", "es_audit"}, seen


def test_no_new_rule_fires_on_known_good_output():
    """The whole point — and it is PER RULE, not per row.

    Requiring every row to be defect-free would have been too blunt to catch the
    bug that motivated this file. `ungrounded_number`'s scale corruption only
    bit numbers that did NOT appear verbatim in the payload, because the literal
    substring check runs first and short-circuits. A row is clean precisely when
    its numbers trace literally, so a corpus of wholly-clean rows never exercises
    the tolerance path at all.

    So each row records the findings a human accepted, and the assertion is that
    no OTHER rule fires. Snapshot 151 is the case that makes this concrete: 366
    words against a 220 cap is a real `too_long` defect, while its "7702" against
    a payload holding 7701.25 is a correct rounding. It is a negative control for
    the number rule and a positive example for the length rule, and only a
    per-rule contract can express that.
    """
    offenders = []
    for row in CORPUS:
        accepted = set(row.get("accepted_findings") or [])
        graded = prompt_rules.grade(row["surface"], row["payload"], row["output"])
        for f in graded.get("findings") or []:
            if f.get("rule") in accepted:
                continue
            offenders.append(
                f"snapshot {row['id']} ({row['surface']}, {row['created_at'][:10]}): "
                f"{f.get('rule')} [{f.get('severity')}] on {f.get('evidence')!r}")
    assert not offenders, (
        f"{len(offenders)} NEW rule firing(s) on the negative control — a rule "
        f"change has started flagging output previously accepted as clean. "
        f"Developers identify false positives no better than chance, so read the "
        f"evidence before trusting any score that depends on these rules:\n  "
        + "\n  ".join(offenders[:15]))


def test_the_control_actually_detects_a_known_regression():
    """WHO VALIDATES THE VALIDATOR. A negative control that cannot detect a bug
    it has already seen is decoration, and the first two versions of this corpus
    were exactly that — sampled at random, they missed the tokenizer defect
    entirely. Reintroduce the pre-2026-08-29 pattern and require the control to
    notice.

    (Three attempts at checking this by hand were themselves wrong: passing the
    regex through a shell string mangled it, so the "old" tokenizer under test
    was the fixed one. Hence a real test, in a real file.)
    """
    import re
    from src import grounding

    old_pattern = re.compile(r"""\$?\s*-?\d+(?:,\d{3})*(?:\.\d+)?\s*[%xBMKTbmkt]?""")
    assert old_pattern.findall("the 7663-7702 band and") == [" 7663", "-7702 b"], \
        "the reconstructed pre-fix pattern is not the one that broke"

    saved = grounding._NUM_TOKEN
    try:
        grounding._NUM_TOKEN = old_pattern
        caught = []
        for row in CORPUS:
            accepted = set(row.get("accepted_findings") or [])
            graded = prompt_rules.grade(row["surface"], row["payload"], row["output"])
            caught += [f for f in graded.get("findings") or []
                       if f.get("rule") not in accepted]
    finally:
        grounding._NUM_TOKEN = saved

    assert caught, ("the negative control did not notice the scale-suffix bug — "
                    "it is not protecting the rule it was built for")


def test_the_corpus_exercises_the_path_that_broke():
    """A control that never reaches the tolerance path cannot protect it.

    The first version of this corpus was sampled at random and would NOT have
    caught the tokenizer bug — no sampled row contained a number followed by a
    word starting t/b/m/k. Rows are now selected for that shape.
    """
    import re
    trap = re.compile(r"[0-9](?:\.[0-9]+)?\s+[tbmkTBMK][a-z]")
    hits = sum(1 for r in CORPUS if trap.search(json.dumps(r["output"], default=str)))
    assert hits >= 10, f"only {hits} rows exercise the number-then-word shape"


def test_desk_vocabulary_is_not_a_fabricated_security():
    """The exact tokens that made `invented_ticker` wrong 18 times out of 18.
    Kept as an explicit case because the corpus above only covers what happened
    to appear in it, and this class must never regress."""
    payload = {"quotes": {"SPY": {"price": 769.245}}, "levels": {}}
    prose = ("Value area held: PVAH and PVAL bracketed the session, price never "
             "tested PDH or PDL, and RVOL stayed subdued. The TGA drawdown and "
             "the UK gilt move were the macro backdrop.")
    for surface in ("market_driver", "home_interpret"):
        out = ({"paragraphs": {"whats_driving": prose}, "citations": [],
                "confidence": 5, "regime_label": "quiet"}
               if surface == "market_driver" else {"text": prose})
        findings = prompt_rules.grade(surface, payload, out).get("findings") or []
        invented = [f for f in findings if f.get("rule") == "invented_ticker"]
        assert not invented, (surface, invented)


def test_a_rounded_reference_to_a_real_level_is_not_invention():
    """`ungrounded_number`'s documented false positive: the note rounds 7701.25
    to 7702 and the grader called it fabricated."""
    payload = {"levels": {"prior_low": 7701.25, "prior_close": 7700.93}}
    out = {"text": "The index held 7702 into the close."}
    findings = prompt_rules.grade("home_interpret", payload, out).get("findings") or []
    assert not [f for f in findings if f.get("rule") == "ungrounded_number"], findings
