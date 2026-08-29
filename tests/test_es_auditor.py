"""The card auditor's two passes.

The auditor's payload carries seventeen MEASURED fields and one that is another
model's prose — `news_digest`. Checking that prose against the measured card is
this surface's most valuable output; doing it in the SAME call that checks the
measured fields against each other means every measured-vs-measured judgement
is formed while holding a sibling model's narrative in context.

Since 2026-08-29 that is two passes: a blind audit of the measured card, and a
separate cross-check of the digest against it. Recorded as separate surfaces so
the loop grades each for the question it actually asks.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import es_auditor, prompt_rules  # noqa: E402


def _brief(with_digest: bool = True) -> dict:
    b = {
        "session": {"phase": "rth_midday"},
        "conditions": {"factors_scored": 5, "verdict": "mixed", "score": 0.4,
                       "reasons": [{"factor": "session", "effect": "+",
                                    "why": "Opening hour"}]},
        "regime": {"path_implied": {}},
        "expected_move": {},
        "rest_of_session": {"p_new_high": 0.3, "p_close_above": 0.5,
                            "band": "normal", "regime": "chop", "n": 120},
        "macro_setup": {"drivers": [], "size": {"note": None},
                        "direction": {"verdict": "none"}},
        "level_clusters": {},
        "attribution": {"headline": "The session's largest expansion ran 12 handles"},
        "news": [{"title": "Fed speaker crosses", "age": "2h", "hours_ago": 2}],
    }
    if with_digest:
        b["news_digest"] = {"text": "The tightening tilt stands unresolved into the open."}
    return b


def test_the_blind_pass_cannot_see_the_digest():
    measured = es_auditor._payload(_brief(), include_digest=False)
    assert "news_digest" not in measured


def test_the_cross_check_pass_can():
    digest = es_auditor._payload(_brief(), include_digest=True)
    assert digest["news_digest"] == "The tightening tilt stands unresolved into the open."


def test_the_two_payloads_differ_only_by_the_generated_field():
    """Nothing else is quarantined — `attribution_headline` reads like prose but
    is a deterministic f-string over measured values, and headlines are vendor
    titles. `news_digest` is the only model-written field on the card."""
    brief = _brief()
    measured = es_auditor._payload(brief, include_digest=False)
    full = es_auditor._payload(brief, include_digest=True)
    assert set(full) - set(measured) == {"news_digest"}
    for k in measured:
        assert measured[k] == full[k], k
    assert measured["attribution_headline"]  # kept: computed, not generated


def test_a_card_with_no_digest_has_nothing_to_cross_check():
    """The second pass must not run on an empty digest — a pass with no work
    records as a clean audit and inflates the surface's score."""
    assert es_auditor._digest_text(_brief(with_digest=False)) is None
    assert es_auditor._digest_text(_brief()) is not None
    measured = es_auditor._payload(_brief(with_digest=False), include_digest=True)
    assert measured.get("news_digest") is None


def test_rule_findings_still_run_without_a_model():
    out = es_auditor.audit_card(_brief(), with_model=False)
    assert out["available"] is True
    assert out["n_model"] == 0
    assert all(f.get("source") == "rule" for f in out["findings"])


def test_both_passes_are_graded_by_the_same_rules():
    assert prompt_rules._GRADERS["es_audit_digest"] is prompt_rules._GRADERS["es_audit"]


def test_a_finding_carries_which_pass_produced_it(monkeypatch):
    """Sources must stay distinguishable, or a digest finding and a measured
    finding become the same row and the split buys nothing."""
    calls = []

    def fake_pass(system, payload):
        calls.append("news_digest" in payload)
        return [{"finding": "x", "severity": "high"}]

    monkeypatch.setattr(es_auditor, "_model_pass", fake_pass)
    monkeypatch.setattr(es_auditor, "_active_prompt_cache", None, raising=False)
    out = es_auditor.audit_card(_brief(), with_model=True)
    assert calls == [False, True], calls          # blind first, then the digest
    sources = {f.get("source") for f in out["findings"] if f.get("source") != "rule"}
    assert sources == {"model", "model:digest"}
    assert out["n_model_measured"] == 1 and out["n_model_digest"] == 1
