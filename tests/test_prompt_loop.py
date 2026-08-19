"""Tests for the self-improving prompt loop's pure logic.

NETWORK-FREE AND DATABASE-FREE BY DESIGN. Everything worth testing here is a
rule, a validation, or a piece of arithmetic — the parts that decide whether a
prompt gets promoted. The parts that talk to Supabase or a model are thin and
fail closed; the parts below are where a silent error would change what the
platform serves without anyone noticing.

Several cases are the specific defects the rules were written for, so this file
doubles as the regression suite's own regression suite: if `grade_market_driver`
ever stops catching a sub-20 VIX called "elevated", this fails.

Run: python -m pytest tests/test_prompt_loop.py -v
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import prompt_rules, prompt_claims, prompt_critic, prompt_replay  # noqa: E402
from src import prompt_defaults, prompt_snapshots  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────

def _payload(**over):
    base = {
        "market_open": True,
        "vix_level_band": "muted",
        "quotes": {
            "SPY": {"price": 600.0, "change_pct_1d": 0.42},
            "QQQ": {"price": 520.0, "change_pct_1d": 0.61},
            "TLT": {"price": 88.0, "change_pct_1d": -0.15},
            "^VIX": {"price": 17.2, "change_pct_1d": 1.1},
        },
        "macro_headlines": [{"title": "CPI comes in at 0.2% MoM"}],
        "news_headlines": [],
        "breadth": {"divergence": "confirming", "net_advancers_pct": 12.0},
    }
    base.update(over)
    return base


def _output(**over):
    base = {
        "regime_label": "risk-on / duration-neutral",
        "paragraphs": {
            "what_happened": "SPY rose 0.42% and QQQ added 0.61% after CPI printed 0.2% MoM.",
            "whats_driving": "Breadth confirms the move with net advancers at 12.0%.",
            "what_to_watch": "TLT at 88.0 is the level that decides the duration trade.",
        },
        "citations": [{"label": "SPY +0.42%", "source": "news"}],
        "confidence": 6,
    }
    base.update(over)
    return base


def _findings(payload, output):
    return {f["rule"] for f in prompt_rules.grade_market_driver(payload, output)}


# ── the four defects that reached a live page ─────────────────────

def test_clean_output_scores_clean():
    g = prompt_rules.grade("market_driver", _payload(), _output())
    assert g["counts"]["critical"] == 0, g["findings"]
    assert g["score"] >= 0.9


def test_sub20_vix_called_elevated_is_critical():
    out = _output(paragraphs={
        "what_happened": "SPY rose 0.42%.",
        "whats_driving": "Elevated volatility is driving the bid for duration.",
        "what_to_watch": "TLT at 88.0.",
    })
    assert "vix_band_contradiction" in _findings(_payload(), out)


def test_elevated_is_fine_when_the_band_says_elevated():
    out = _output(paragraphs={
        "what_happened": "SPY rose 0.42%.",
        "whats_driving": "Elevated volatility is driving the bid for duration.",
        "what_to_watch": "TLT at 88.0.",
    })
    assert "vix_band_contradiction" not in _findings(_payload(vix_level_band="elevated"), out)


def test_absent_change_described_as_flat_is_critical():
    pay = _payload(quotes={
        "SPY": {"price": 600.0},                      # no change_pct_1d — move UNKNOWN
        "QQQ": {"price": 520.0, "change_pct_1d": 0.61},
    })
    out = _output(paragraphs={
        "what_happened": "SPY was flat on the session while QQQ added 0.61%.",
        "whats_driving": "Rotation into large-cap tech.",
        "what_to_watch": "QQQ 520.0.",
    })
    assert "absent_move_called_flat" in _findings(pay, out)


def test_no_catalyst_while_macro_feed_has_a_story():
    out = _output(paragraphs={
        "what_happened": "SPY rose 0.42% with no matching macro headline.",
        "whats_driving": "Positioning, not news.",
        "what_to_watch": "TLT 88.0.",
    })
    assert "no_catalyst_despite_macro_feed" in _findings(_payload(), out)


def test_divergent_breadth_must_qualify_a_broad_rally():
    pay = _payload(breadth={"divergence": "divergent", "net_advancers_pct": -8.0})
    out = _output(paragraphs={
        "what_happened": "A broad rally lifted SPY 0.42%.",
        "whats_driving": "Risk appetite.",
        "what_to_watch": "TLT 88.0.",
    })
    assert "breadth_divergence_unqualified" in _findings(pay, out)

    qualified = _output(paragraphs={
        "what_happened": "A broad rally lifted SPY 0.42%, though breadth is divergent and narrow.",
        "whats_driving": "Risk appetite in a handful of names.",
        "what_to_watch": "TLT 88.0.",
    })
    assert "breadth_divergence_unqualified" not in _findings(pay, qualified)


def test_invented_ticker_is_critical():
    out = _output(paragraphs={
        "what_happened": "SPY rose 0.42% while NVDA led.",
        "whats_driving": "Semis.",
        "what_to_watch": "TLT 88.0.",
    })
    assert "invented_ticker" in _findings(_payload(), out)


def test_empty_paragraph_is_critical():
    out = _output(paragraphs={"what_happened": "", "whats_driving": "x", "what_to_watch": "y"})
    assert "empty_paragraph" in _findings(_payload(), out)


def test_regression_rules_are_the_critical_ones():
    out = _output(paragraphs={
        "what_happened": "SPY was flat with no matching macro headline.",
        "whats_driving": "Elevated volatility.",
        "what_to_watch": "TLT 88.0.",
    })
    pay = _payload(quotes={"SPY": {"price": 600.0}})
    fails = prompt_rules.regression_failures(prompt_rules.grade("market_driver", pay, out)["findings"])
    assert "vix_band_contradiction" in fails
    assert "no_catalyst_despite_macro_feed" in fails


# ── the other two surfaces ────────────────────────────────────────

def test_home_interpret_flags_missing_bottom_line_and_invented_ticker():
    rules = {f["rule"] for f in prompt_rules.grade_home_interpret(
        {"tickers": ["SPY"]}, "SPY looks constructive. PLTR does not.")}
    assert "missing_bottom_line" in rules
    assert "invented_ticker" in rules


def test_es_audit_empty_findings_is_a_success():
    g = prompt_rules.grade("es_audit", {"a": 1}, {"findings": []})
    assert g["counts"]["critical"] == 0
    assert g["score"] == 1.0


def test_es_audit_forecast_is_penalised():
    g = prompt_rules.grade("es_audit", {"range": 42.0, "em": 30.0},
                           {"findings": [{"severity": "high", "where": "a vs b",
                                          "finding": "Range 42.0 vs EM 30.0 means price will rally."}]})
    assert any(f["rule"] == "auditor_forecast" for f in g["findings"])


# ── claims ────────────────────────────────────────────────────────

def test_claim_extraction_keeps_only_settleable_calls():
    pay = _payload()
    out = {"calls": [
        {"subject": "SPY", "op": "up_gte", "threshold": 0.5, "sessions": 1, "confidence": 0.6},
        {"subject": "TLT", "op": "outperform", "vs": "SPY", "threshold": 0.3, "sessions": 2,
         "confidence": 0.55},
        {"subject": "NVDA", "op": "up_gte", "threshold": 1.0, "sessions": 1, "confidence": 0.7},
        {"subject": "SPY", "op": "moon", "threshold": 1.0, "sessions": 1, "confidence": 0.7},
        {"subject": "SPY", "op": "up_gte", "threshold": 900, "sessions": 1, "confidence": 0.7},
        {"subject": "SPY", "op": "up_gte", "threshold": 0.5, "sessions": 40, "confidence": 0.7},
    ]}
    kept = prompt_claims.extract(out, pay)
    assert len(kept) == 2                              # the rest are unsettleable
    assert {c["subject"] for c in kept} == {"SPY", "TLT"}
    assert kept[1]["vs"] == "SPY"


def test_outperform_without_a_reference_is_dropped():
    kept = prompt_claims.extract(
        {"calls": [{"subject": "TLT", "op": "outperform", "threshold": 0.3,
                    "sessions": 1, "confidence": 0.6}]}, _payload())
    assert kept == []


def test_confidence_is_clamped_not_dropped():
    kept = prompt_claims.extract(
        {"calls": [{"subject": "SPY", "op": "up_gte", "threshold": 0.5,
                    "sessions": 1, "confidence": 1.0}]}, _payload())
    assert kept and kept[0]["confidence"] == 0.99


def test_outcome_operators():
    assert prompt_claims._outcome("up_gte", 0.5, 0.7) is True
    assert prompt_claims._outcome("up_gte", 0.5, 0.3) is False
    assert prompt_claims._outcome("down_gte", 0.5, -0.7) is True
    assert prompt_claims._outcome("abs_lt", 1.0, -0.4) is True
    assert prompt_claims._outcome("abs_lt", 1.0, -1.4) is False
    assert prompt_claims._outcome("abs_gte", 1.0, -1.4) is True


def test_reference_close_is_never_one_the_model_already_saw():
    """The leak this schema exists to prevent, and the session boundary.

    A note written intraday must be scored from a close that had not printed
    when it was written — so a 10:30 ET Tuesday note is measured from TUESDAY's
    close, which is still hours away. A note written after the bell has already
    seen that close, so it is measured from Wednesday's. Getting this wrong in
    either direction either gives away a session or hands the model one it has
    already read.
    """
    import pandas as pd
    from datetime import datetime, timezone
    idx = [d.date() for d in pd.to_datetime(
        ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"])]
    df = pd.DataFrame({"Close": [100.0, 101.0, 103.0, 99.0]}, index=idx)

    # 14:30 UTC = 10:30 ET Tuesday — Tuesday's close has not printed.
    ref, out, ref_d, out_d = prompt_claims._closes_after(
        df, datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc), 1)
    assert (ref_d, out_d) == ("2026-08-11", "2026-08-12")
    assert (ref, out) == (101.0, 103.0)

    # 21:05 UTC = 17:05 ET Tuesday — Tuesday's close is known, so skip it.
    ref, out, ref_d, out_d = prompt_claims._closes_after(
        df, datetime(2026, 8, 11, 21, 5, tzinfo=timezone.utc), 1)
    assert (ref_d, out_d) == ("2026-08-12", "2026-08-13")
    assert (ref, out) == (103.0, 99.0)


def test_weekend_note_resolves_from_the_next_session():
    import pandas as pd
    from datetime import datetime, timezone
    idx = [d.date() for d in pd.to_datetime(
        ["2026-08-14", "2026-08-17", "2026-08-18"])]      # Fri, Mon, Tue
    df = pd.DataFrame({"Close": [100.0, 102.0, 101.0]}, index=idx)
    ref, out, ref_d, out_d = prompt_claims._closes_after(
        df, datetime(2026, 8, 15, 18, 0, tzinfo=timezone.utc), 1)   # Saturday
    assert (ref_d, out_d) == ("2026-08-17", "2026-08-18")


def test_a_datetime_index_resolves_the_same_way_as_a_date_index():
    """`fetch_ohlcv` returns either, depending on which path served the bars."""
    import pandas as pd
    from datetime import datetime, timezone
    dates = ["2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13"]
    closes = [100.0, 101.0, 103.0, 99.0]
    stated = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)
    a = prompt_claims._closes_after(
        pd.DataFrame({"Close": closes}, index=[d.date() for d in pd.to_datetime(dates)]),
        stated, 1)
    b = prompt_claims._closes_after(
        pd.DataFrame({"Close": closes}, index=pd.to_datetime(dates)), stated, 1)
    c = prompt_claims._closes_after(
        pd.DataFrame({"Close": closes},
                     index=pd.to_datetime(dates).tz_localize("America/New_York")),
        stated, 1)
    assert a == b == c


def test_horizon_not_reached_returns_none():
    import pandas as pd
    from datetime import datetime, timezone
    idx = [d.date() for d in pd.to_datetime(["2026-08-10", "2026-08-11"])]
    df = pd.DataFrame({"Close": [100.0, 101.0]}, index=idx)
    stated = datetime(2026, 8, 11, 14, 30, tzinfo=timezone.utc)
    assert prompt_claims._closes_after(df, stated, 3) is None


def test_wilson_interval_widens_on_small_samples():
    lo_small, hi_small = prompt_claims._wilson(6, 10)
    lo_big, hi_big = prompt_claims._wilson(600, 1000)
    assert (hi_small - lo_small) > (hi_big - lo_big)
    assert 0.0 <= lo_small <= hi_small <= 1.0


# ── challenger validation ─────────────────────────────────────────

def test_validate_rejects_a_challenger_that_drops_the_calls_contract():
    champ = prompt_defaults.MARKET_DRIVER_SYSTEM
    stripped = champ.replace("calls", "xxxx")
    ok, why = prompt_critic.validate("market_driver", champ, stripped)
    assert not ok and "calls" in why


def test_validate_rejects_a_wholesale_rewrite():
    champ = prompt_defaults.MARKET_DRIVER_SYSTEM
    ok, why = prompt_critic.validate("market_driver", champ, "Write a market note. " * 20)
    assert not ok


def test_validate_accepts_a_surgical_edit():
    champ = prompt_defaults.MARKET_DRIVER_SYSTEM
    edited = champ + "\n\nNEW RULE — never describe a move you cannot source to the payload.\n"
    ok, why = prompt_critic.validate("market_driver", champ, edited)
    assert ok, why


def test_validate_rejects_dropping_the_bottom_line_for_interpret():
    champ = prompt_defaults.BASE_SYSTEM
    ok, _ = prompt_critic.validate("home_interpret", champ, champ.replace("Bottom line", "Summary"))
    assert not ok


# ── the promotion gate ────────────────────────────────────────────

def _pair(champ_score, chall_score, regressions=None, counts_a=None, counts_b=None):
    return {
        "snapshot_id": 1,
        "champion": {"score": champ_score, "counts": counts_a or {"critical": 0, "major": 0, "minor": 0}},
        "challenger": {"score": chall_score, "counts": counts_b or {"critical": 0, "major": 0, "minor": 0}},
        "champion_failed": False, "challenger_failed": False,
        "regressions": regressions or [],
    }


def test_gate_promotes_a_clear_consistent_win():
    pairs = [_pair(0.7, 0.9) for _ in range(20)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "win", res["reasons"]
    assert res["mean_diff"] > 0


def test_gate_rejects_a_win_that_reintroduces_a_known_defect():
    pairs = [_pair(0.7, 0.95, regressions=["vix_band_contradiction"]) for _ in range(20)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "reject"
    assert any("reintroduces" in r for r in res["reasons"])


def test_gate_rejects_a_tiny_improvement_that_is_merely_significant():
    pairs = [_pair(0.70, 0.705) for _ in range(30)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "reject"
    assert any("margin" in r for r in res["reasons"])


def test_gate_rejects_a_noisy_wash():
    scores = [(0.6, 0.9), (0.9, 0.5), (0.7, 0.8), (0.8, 0.6)] * 5
    pairs = [_pair(a, b) for a, b in scores]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "reject"


def test_gate_rejects_a_challenger_that_fails_to_generate_more_often():
    pairs = [_pair(0.7, 0.9) for _ in range(20)]
    pairs += [{"snapshot_id": 99, "champion": None, "challenger": None,
               "champion_failed": False, "challenger_failed": True} for _ in range(4)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "reject"
    assert any("failed to generate" in r for r in res["reasons"])


def test_gate_is_inconclusive_below_the_minimum_sample():
    pairs = [_pair(0.7, 0.9) for _ in range(4)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "inconclusive"


def test_gate_rejects_more_critical_findings_even_when_the_score_rises():
    pairs = [_pair(0.7, 0.9, counts_a={"critical": 0, "major": 1, "minor": 0},
                   counts_b={"critical": 1, "major": 0, "minor": 0}) for _ in range(20)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "reject"
    assert any("critical" in r for r in res["reasons"])


# ── splits ────────────────────────────────────────────────────────

def test_split_assignment_is_deterministic_and_roughly_balanced():
    seeds = [f"payload-{i}" for i in range(2000)]
    first = [prompt_snapshots._split_for(s) for s in seeds]
    second = [prompt_snapshots._split_for(s) for s in seeds]
    assert first == second
    share = first.count("holdout") / len(first)
    assert 0.33 < share < 0.47, share


def test_every_surface_has_a_baseline_and_a_rule_set():
    for surface, body in prompt_defaults.BASELINES.items():
        assert len(body) > 500, surface
        g = prompt_rules.grade(surface, {}, {} if surface != "home_interpret" else "")
        assert "error" not in g, (surface, g)


# ── the cross-asset attribution block ─────────────────────────────

def _payload_with_drivers(**over):
    pay = _payload(**over)
    pay["cross_asset_drivers"] = {
        "available": True,
        "window_sessions": 126,
        "explained_share": 0.43,
        "ranking": [
            {"driver": "Gold", "ticker": "GLD", "rank": 1,
             "share_of_variance": 0.10, "corr_with_spy": 0.482, "rank_a_year_ago": 4},
            {"driver": "Oil", "ticker": "USO", "rank": 2,
             "share_of_variance": 0.056, "corr_with_spy": -0.477, "rank_a_year_ago": 1},
        ],
        "credit_increment": 0.238,
    }
    return pay


def _driver_rules(paragraph, payload=None):
    out = _output(paragraphs={
        "what_happened": "SPY rose 0.42%.",
        "whats_driving": paragraph,
        "what_to_watch": "TLT 88.0.",
    })
    return {f["rule"] for f in prompt_rules.grade_market_driver(
        payload if payload is not None else _payload_with_drivers(), out)}


def test_calling_a_driver_causal_is_flagged():
    """The natural sentence to write when handed a ranked list, and the wrong one.

    The attribution is a regression on same-day returns whose next-day
    correlations were measured at essentially zero, so causation and prediction
    are both unsupported by the payload that prompted them.
    """
    for bad in ("Gold is driving the tape higher.",
                "The advance was driven by gold.",
                "Rates will push equities lower into the close.",
                "Credit has been driving risk appetite."):
        assert "driver_causal_language" in _driver_rules(bad), bad


def test_co_movement_wording_is_accepted():
    for ok in ("The tape has moved with gold this quarter, gold at a 0.10 share.",
               "SPY and gold have risen together; the 0.482 correlation is unusual for a haven.",
               "Oil co-moved inversely at -0.477."):
        assert "driver_causal_language" not in _driver_rules(ok), ok


def test_causal_rule_is_silent_without_the_attribution_block():
    """Scoped to the instruction it tests.

    Without `cross_asset_drivers` in the payload there is no attribution to
    misuse, and firing here would penalise a sentence about a headline instead.
    """
    assert "driver_causal_language" not in _driver_rules(
        "Gold is driving the tape higher.", payload=_payload())


def test_attribution_block_does_not_trip_the_other_rules():
    """Its numbers must not read as ungrounded claims once quoted back."""
    g = prompt_rules.grade("market_driver", _payload_with_drivers(), _output(paragraphs={
        "what_happened": "SPY rose 0.42% and QQQ added 0.61%.",
        "whats_driving": "The tape has moved with gold, a 0.1 share of daily variation "
                         "against 0.43 for the four together; gold was fourth a year ago.",
        "what_to_watch": "TLT at 88.0.",
    }))
    assert g["counts"]["critical"] == 0, g["findings"]


# ── news_digest: the ES card's one unmeasured AI block ────────────

_HEADLINES = {"headlines": [
    {"title": "Waller says one more cut is appropriate this year", "tier": 1},
    {"title": "Retail sales beat at 0.6% MoM", "tier": 1},
    {"title": "Hormuz shipping resumes after de-escalation", "tier": 2},
]}


def _digest(text):
    return {f["rule"] for f in prompt_rules.grade_news_digest(_HEADLINES, text)}


def test_a_clean_digest_scores_clean():
    g = prompt_rules.grade("news_digest", _HEADLINES,
                           "Waller signalled one more cut this year while retail sales beat at "
                           "0.6%, leaving the policy path unresolved into the open. Shipping "
                           "through Hormuz resumed after the de-escalation.")
    assert g["counts"] == {"critical": 0, "major": 0, "minor": 0}, g["findings"]
    assert g["score"] == 1.0


def test_implying_a_direction_is_critical():
    """The ES card's founding distinction: context is not signal.

    This paragraph is the easiest place on that card to blur it, which is why
    the rule is critical and sits in the regression suite.
    """
    for bad in ("Retail sales beat at 0.6%, which is supportive of higher prices into the open.",
                "The tone is constructive after Waller's comments.",
                "Watch support at 6400 after the retail sales beat.",
                "Headlines favour the upside into the cash open."):
        assert "digest_implies_direction" in _digest(bad), bad


def test_named_jargon_is_flagged():
    assert "digest_jargon" in _digest("A risk-on tone after the retail sales beat at 0.6%.")


def test_quiet_tape_wording_is_not_penalised():
    """"Nothing new" is explicitly a success in this prompt, not a failure."""
    g = prompt_rules.grade("news_digest", _HEADLINES,
                           "Nothing new since Friday. The headlines repeat the retail sales "
                           "beat at 0.6% and add no policy detail.")
    assert g["counts"]["critical"] == 0, g["findings"]


def test_headline_only_rule_catches_an_invented_name():
    assert "invented_ticker" in _digest("Waller signalled a cut and NVDA guided higher.")


def test_digest_structure_and_length():
    assert "digest_has_structure" in _digest("- Waller signalled a cut\n- Retail sales beat 0.6%")
    assert "digest_too_long" in _digest("Waller signalled one more cut this year. " * 14)


def test_digest_regression_rules_are_registered():
    assert "digest_implies_direction" in prompt_rules.REGRESSION_RULES
    assert "empty_digest" in prompt_rules.REGRESSION_RULES


def test_replay_can_rebuild_the_digest_user_message():
    """Replay must reconstruct what the model saw from the frozen payload alone."""
    msg = prompt_replay._user_message("news_digest", {"lines": ["[tier 1] Waller says one more cut"]})
    assert "Waller says one more cut" in msg
    # And from raw headlines when `lines` was not stored.
    msg2 = prompt_replay._user_message("news_digest", _HEADLINES)
    assert "Retail sales beat at 0.6% MoM" in msg2


def test_every_registered_surface_has_rules_and_invariants():
    from src import prompt_loop
    for surface in prompt_loop.SURFACES:
        assert surface in prompt_defaults.BASELINES, surface
        assert surface in prompt_rules._GRADERS, surface
        assert prompt_critic._INVARIANTS.get(surface), surface
        assert surface in prompt_replay._REPLAY_MODEL, surface


def test_interpretation_panel_is_held_to_the_same_driver_rule():
    """It receives the same measured attribution, so it inherits the same limit."""
    pay = {"tickers": ["SPY"], "drivers": {"available": True,
           "ranking": [{"driver": "Gold", "share_of_variance": 0.1}]}}
    bad = {f["rule"] for f in prompt_rules.grade_home_interpret(
        pay, "Gold is driving the tape. Bottom line: risk stays on.")}
    assert "driver_causal_language" in bad

    ok = {f["rule"] for f in prompt_rules.grade_home_interpret(
        pay, "The tape has moved with gold. Bottom line: watch that co-movement.")}
    assert "driver_causal_language" not in ok


def test_per_page_interpret_surfaces_reuse_the_home_rules():
    g = prompt_rules.grade("interpret:smart-money", {"tickers": ["SPY"]},
                           "SPY looks constructive. PLTR does not.")
    assert "error" not in g
    assert any(f["rule"] == "invented_ticker" for f in g["findings"])
