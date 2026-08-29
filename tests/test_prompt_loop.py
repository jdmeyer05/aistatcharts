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


def test_market_structure_shorthand_is_not_an_invented_ticker():
    """The 17 false criticals that scored the home page at 0.139.

    Every `invented_ticker` finding recorded in production between 2026-08-19
    and 2026-08-28 was desk vocabulary, not a security. This is the real text
    from snapshot 217, whose 7700.04 traces to the payload.
    """
    text = ("Price sits above prior value (PVAH 7700.04) and above the prior day "
            "high 7705.5, but only 17.5% up the overnight range. RVOL is "
            "unavailable and the TGA drawdown continues. Bottom line: watch 7700.04.")
    payload = {"levels": {"prior_value_area_high": 7700.04, "prior_day_high": 7705.5},
               "overnight": {"position_in_range": 0.175}}
    rules = {f["rule"] for f in prompt_rules.grade_home_interpret(payload, text)}
    assert "invented_ticker" not in rules


def test_an_acronym_labelling_a_grounded_value_is_not_a_ticker():
    """The structural guard, for the abbreviation nobody thought to list."""
    payload = {"levels": {"weekly_pivot": 7688.25}}
    clean = {f["rule"] for f in prompt_rules.grade_home_interpret(
        payload, "The WKPV 7688.25 shelf held. Bottom line: watch it.")}
    assert "invented_ticker" not in clean

    # ...but the same acronym with no number of its own is still a finding.
    dirty = {f["rule"] for f in prompt_rules.grade_home_interpret(
        payload, "WKPV led the tape higher. Bottom line: watch it.")}
    assert "invented_ticker" in dirty


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

def _pair(champ_score, chall_score, regressions=None, counts_a=None, counts_b=None,
          champ_regressions=None):
    return {
        "snapshot_id": 1,
        "champion": {"score": champ_score, "counts": counts_a or {"critical": 0, "major": 0, "minor": 0}},
        "challenger": {"score": chall_score, "counts": counts_b or {"critical": 0, "major": 0, "minor": 0}},
        "champion_failed": False, "challenger_failed": False,
        "challenger_regressions": regressions or [],
        "champion_regressions": champ_regressions or [],
    }


def test_gate_passes_a_clean_run_to_the_accumulating_gate():
    """No substantive fault means the run is 'pending', not 'win'.

    Since 2026-08-29 _summarise decides only the per-run facts (regressions,
    criticals, generation failures). Promote/retire is decided by the e-value
    over the challenger's whole life — a single night cannot carry it.
    """
    pairs = [_pair(0.7, 0.9) for _ in range(20)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "pending", res["reasons"]
    assert res["reasons"] == []
    assert res["mean_diff"] > 0
    assert res["wins"] == 20 and res["losses"] == 0 and res["ties"] == 0


def test_gate_rejects_a_win_that_reintroduces_a_known_defect():
    pairs = [_pair(0.7, 0.95, regressions=["vix_band_contradiction"]) for _ in range(20)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "reject"
    assert any("reintroduces" in r for r in res["reasons"])


def test_gate_does_not_charge_the_challenger_for_the_champions_defect():
    """The rejection that blocked market_driver v3 on 2026-08-27.

    v3 scored 0.950 against the champion's 0.902 with a CI excluding zero, and
    was rejected for `invented_ticker` while the champion produced exactly as
    many. A defect endemic to the surface is not something the challenger
    reintroduced, and the gate must not read it as one.
    """
    pairs = [_pair(0.902, 0.950,
                   regressions=["invented_ticker"],
                   champ_regressions=["invented_ticker"]) for _ in range(20)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 3}, pairs)
    assert res["regressions"] == []
    assert res["verdict"] == "pending", res["reasons"]


def test_gate_still_rejects_a_defect_the_challenger_makes_more_common():
    """Relative, not absent: a higher rate than the champion is still a veto."""
    pairs = ([_pair(0.7, 0.95, regressions=["invented_ticker"],
                    champ_regressions=["invented_ticker"]) for _ in range(10)]
             + [_pair(0.7, 0.95, regressions=["invented_ticker"]) for _ in range(10)])
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["regressions"] == ["invented_ticker"]
    assert res["verdict"] == "reject"
    assert any("20 vs champion 10" in r for r in res["reasons"])


def test_a_reliable_but_trivial_improvement_is_not_shipped():
    """30 straight wins of +0.005 each: statistically certain, practically nil.

    The sign test detects DIRECTION, not magnitude, so this crosses any evidence
    threshold. The old gate caught it with _MIN_MARGIN and called it 'reject' —
    indistinguishable from a challenger that was actually worse, and two of those
    retired it. The honest verdict is 'trivial': real, and too small to ship.
    """
    from src import prompt_evidence
    pairs = [_pair(0.70, 0.705) for _ in range(30)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["verdict"] == "pending"      # no substantive fault
    v, why = prompt_evidence.verdict(res["wins"], res["losses"],
                                     prompt_evidence.evalue(res["wins"], res["losses"]),
                                     n=res["n"], sum_d=res["sum_d"], sum_d2=res["sum_d2"])
    assert v == "trivial", why
    assert "does not matter" in why


def test_a_real_improvement_of_shippable_size_still_wins():
    """The control for the test above: same 30 pairs, a gain that clears ROPE."""
    from src import prompt_evidence
    pairs = [_pair(0.70, 0.85) for _ in range(30)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    v, why = prompt_evidence.verdict(res["wins"], res["losses"],
                                     prompt_evidence.evalue(res["wins"], res["losses"]),
                                     n=res["n"], sum_d=res["sum_d"], sum_d2=res["sum_d2"])
    assert v == "win", why


def test_a_noisy_wash_does_not_accumulate_evidence():
    """A challenger that wins half and loses half must never cross."""
    from src import prompt_evidence
    scores = [(0.6, 0.9), (0.9, 0.5), (0.7, 0.8), (0.8, 0.6)] * 5
    pairs = [_pair(a, b) for a, b in scores]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 2}, pairs)
    assert res["wins"] == 10 and res["losses"] == 10
    e = prompt_evidence.evalue(res["wins"], res["losses"])
    assert e < 1.0, e
    assert prompt_evidence.verdict(res["wins"], res["losses"], e)[0] == "collecting"


def _dead_pair(champ_why=None, chall_why=None):
    return {"snapshot_id": 99, "champion": None, "challenger": None,
            "champion_failed": champ_why is not None,
            "challenger_failed": chall_why is not None,
            "champion_why": champ_why, "challenger_why": chall_why}


def test_a_vendor_outage_cannot_reject_a_winning_challenger():
    """The 2026-08-28 rejection of market_driver v3.

    v3 won +0.036 with a CI excluding zero and was rejected for "failed to
    generate more often" during a Gemini 503 spike. A 503 lands on whichever
    arm called during the spike, so it is not evidence about the prompt.
    """
    pairs = ([_pair(0.90, 0.94) for _ in range(14)]
             + [_dead_pair(chall_why="api_error") for _ in range(3)]
             + [_dead_pair(champ_why="api_error")])
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 3}, pairs)
    assert res["generation_failures"]["vendor_discounted"] == 4
    assert res["verdict"] == "pending", res["reasons"]

    # A challenger that genuinely fails to parse more often is still rejected.
    prompt_broken = ([_pair(0.90, 0.94) for _ in range(14)]
                     + [_dead_pair(chall_why="unparseable") for _ in range(3)])
    bad = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 3}, prompt_broken)
    assert bad["verdict"] == "reject"
    assert any("failed to generate more often" in r for r in bad["reasons"])


def test_a_vendor_ruined_sample_is_inconclusive_not_a_rejection():
    """Two rejects retire a challenger, so a 503 spike must not cast one.

    2026-08-28: 15 of 24 pairs died to Gemini 503s, n fell to 11, the bootstrap
    CI widened to include zero and v3 was rejected — a fact about the vendor's
    capacity, not about the prompt.
    """
    noisy = [(0.90, 1.00), (0.90, 0.85), (0.90, 0.98), (0.90, 0.90), (0.90, 0.95),
             (0.90, 0.88), (0.90, 0.99), (0.90, 0.84), (0.90, 0.93)]
    pairs = ([_pair(a, b) for a, b in noisy]
             + [_dead_pair(chall_why="api_error") for _ in range(15)])
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 3}, pairs)
    # No substantive fault, so nothing is charged to the prompt. And the pairs
    # the outage left are now BANKED rather than discarded, which is the deeper
    # fix — a ruined night costs sample size, not a strike.
    assert res["verdict"] == "pending", res["reasons"]
    assert res["wins"] == 5 and res["losses"] == 3


def test_a_ruined_sample_still_rejects_on_a_substantive_fault():
    """Degradation excuses a thin CI, never an actual defect."""
    pairs = ([_pair(0.90, 0.94, regressions=["invented_ticker"]) for _ in range(9)]
             + [_dead_pair(chall_why="api_error") for _ in range(15)])
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 3}, pairs)
    assert res["verdict"] == "reject"
    assert any("reintroduces" in r for r in res["reasons"])


def test_a_broken_arm_is_not_reported_as_a_thin_sample():
    """Every generation failing must not read like "not enough data yet".

    On 2026-08-28 an uninstalled SDK failed all 48 calls and the run reported
    `inconclusive, n=0` at INFO — indistinguishable from a surface with no
    holdout rows, which is how a challenger sits unevaluated indefinitely.
    """
    dead = [{"snapshot_id": i, "champion": None, "challenger": None,
             "champion_failed": True, "challenger_failed": True} for i in range(24)]
    res = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 3}, dead)
    assert res["ok"] is False
    assert res["n"] == 0
    assert "generation failed" in res["error"]

    # A genuinely thin but healthy sample still reports the quiet way.
    thin = [_pair(0.9, 0.9) for _ in range(3)]
    quiet = prompt_replay._summarise("market_driver", {"version": 1}, {"version": 3}, thin)
    assert quiet["ok"] is True
    assert quiet["verdict"] == "inconclusive"


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


# ── watching the watcher ──────────────────────────────────────────

def test_the_zero_precision_rule_would_have_been_flagged():
    """The real 2026-08-19..28 firing pattern of `invented_ticker`.

    18 findings, every one a false positive, and the tell is that they repeat:
    TGA seven times, VAH five, then singletons. A rule catching genuine
    fabrications would name a different symbol nearly every time.
    """
    from collections import Counter
    from src import prompt_health
    ev = Counter({"TGA": 7, "VAH": 5, "PVAH": 1, "PDH": 1,
                  "RVOL": 1, "PVAL": 1, "PDL": 1, "UK": 1})
    flags = prompt_health.flags_for("invented_ticker", ev, n=18, n_graded=248)
    assert flags, "the broken rule must be flagged"
    assert any("distinct values" in f for f in flags)


def test_a_healthy_rule_is_not_flagged():
    """Genuine fabrications are varied, so diversity stays high."""
    from collections import Counter
    from src import prompt_health
    ev = Counter({f"FAKE{i}": 1 for i in range(12)})
    assert prompt_health.flags_for("invented_ticker", ev, n=12, n_graded=248) == []


def test_a_regression_rule_firing_constantly_is_flagged():
    from collections import Counter
    from src import prompt_health
    ev = Counter({f"x{i}": 1 for i in range(60)})
    flags = prompt_health.flags_for("invented_ticker", ev, n=60, n_graded=100)
    assert any("firing on" in f for f in flags)


def test_few_firings_are_never_flagged():
    """Two identical findings are not evidence of anything."""
    from collections import Counter
    from src import prompt_health
    assert prompt_health.flags_for("invented_ticker", Counter({"VAH": 2}), 2, 248) == []


def test_only_vendor_errors_are_retried():
    """A refusal or an unparseable answer is the prompt's, and must not be re-rolled."""
    from src import prompt_replay
    for msg in ("503 UNAVAILABLE", "429 too many requests", "connection reset",
                "deadline exceeded"):
        assert prompt_replay._retryable(Exception(msg)), msg
    for msg in ("400 invalid_request", "401 authentication_error", "no such model"):
        assert not prompt_replay._retryable(Exception(msg)), msg


def test_a_ceilinged_surface_is_not_critiqued(monkeypatch):
    """es_audit sits at 0.997-0.999; a challenger there cannot be shown to win."""
    from src import prompt_loop
    monkeypatch.setattr(prompt_loop, "_open_challenger", lambda s: None)
    monkeypatch.setattr(prompt_loop, "graded_snapshots",
                        lambda *a, **k: [{"snapshot": {},
                                          "grade": {"score": 0.998, "findings": []}}
                                         for _ in range(20)])
    res = prompt_loop.critique_cycle("es_audit")
    assert "headroom" in res.get("skipped", "")


def test_a_surface_whose_strict_pass_is_unknown_is_never_skipped():
    """Rows carrying a score but no findings cannot confirm a ceiling. Skipping
    the critic on a statistic we could not compute is the absence-as-calm bug
    this whole statistic exists to expose."""
    from src import prompt_health
    unknown = [{"snapshot": {}, "grade": {"score": 0.998}} for _ in range(20)]
    assert prompt_health.strict_pass(unknown) is None
    room, why = prompt_health.has_headroom(unknown)
    assert room is True
    assert "unknown" in why


# ── the mean was not the gate (audit 2026-08-29) ──────────────────

def _rows(n_clean, n_defective, clean=1.0, defective=0.9583):
    """Graded rows shaped like production: a finding is what makes a row dirty."""
    return ([{"snapshot": {}, "grade": {"score": clean, "findings": []}}
             for _ in range(n_clean)]
            + [{"snapshot": {}, "grade": {"score": defective,
                                          "findings": [{"rule": "digest_too_long",
                                                        "severity": "minor"}]}}
               for _ in range(n_defective)])


def test_strict_pass_counts_rows_not_severity():
    from src import prompt_health
    assert abs(prompt_health.strict_pass(_rows(45, 6)) - 45 / 51) < 1e-9
    assert prompt_health.strict_pass([]) is None


def test_the_news_digest_ceiling_was_a_false_ceiling():
    """The real numbers that motivated the change.

    news_digest discovery: 51 rows, 6 breaking the 70-word cap, mean 0.99509 —
    which cleared the 0.995 ceiling by 0.000094 and barred the critic from the
    one surface with a live systematic defect.
    """
    from src import prompt_health
    graded = _rows(45, 6)
    scores = [g["grade"]["score"] for g in graded]
    assert sum(scores) / len(scores) >= prompt_health._CEILING   # old gate: "done"
    room, why = prompt_health.has_headroom(graded)
    assert room is True                                          # new gate: not done
    assert "strict pass" in why


def test_a_genuinely_finished_surface_still_skips():
    """Both statistics have to agree before the critique spend is skipped."""
    from src import prompt_health
    room, why = prompt_health.has_headroom(_rows(199, 1))
    assert room is False
    assert "no measurable headroom" in why


def test_a_mean_can_hide_a_surface_defective_ten_times_in_eleven():
    """home_interpret pre-fix: mean 0.9394 reads like a B-plus; strict was 9.1%."""
    from src import prompt_health
    graded = _rows(1, 10, defective=0.9333)
    assert abs(prompt_health.strict_pass(graded) - 1 / 11) < 1e-9
    assert prompt_health.has_headroom(graded)[0] is True


def test_a_defective_surface_is_critiqued(monkeypatch):
    from src import prompt_loop
    monkeypatch.setattr(prompt_loop, "_open_challenger", lambda s: None)
    monkeypatch.setattr(prompt_loop, "graded_snapshots", lambda *a, **k: _rows(45, 6))
    res = prompt_loop.critique_cycle("news_digest")
    assert "headroom" not in res.get("skipped", "")


# ── the accumulating gate (fix #2, 2026-08-29) ────────────────────

def test_the_evalue_is_a_true_martingale():
    """THE ENTIRE GUARANTEE RESTS ON THIS. E_H0[E_t] must be exactly 1 at every
    t, or Ville's inequality does not apply and 'look every night for free' is
    not licensed. Checked against the Binomial(t, 1/2) null directly."""
    from math import comb
    from src import prompt_evidence
    for t in (1, 2, 3, 5, 11, 24, 60):
        expectation = sum(comb(t, w) * 0.5 ** t * prompt_evidence.evalue(w, t - w)
                          for w in range(t + 1))
        assert abs(expectation - 1.0) < 1e-9, (t, expectation)


def test_our_actual_run_is_insufficient_data_not_a_rejection():
    """Experiment 9: 11 pairs, 3 wins, 8 ties, 0 losses — the run that left v3
    unresolved. Three discordant pairs cannot resolve anything (exact sign-test
    floor 0.125), so the honest verdict is 'keep collecting'. Under the old rule
    this counted as a strike, and two strikes retired the challenger."""
    from src import prompt_evidence
    e = prompt_evidence.evalue(3, 0)
    assert abs(e - 2.85) < 0.01, e          # matches the published derivation
    v, why = prompt_evidence.verdict(3, 0, e)
    assert v == "insufficient_data"
    assert "not a reject" in why


def test_evidence_accumulates_across_nights():
    """Three nights of 3-0 is 9-0, not three separate non-significant runs."""
    from src import prompt_evidence
    assert prompt_evidence.evalue(9, 0) > prompt_evidence.evalue(3, 0) * 3
    # Still under the 11-pair floor, so still not a verdict — but the evidence
    # is banked, which is the whole difference from three fresh bootstraps.
    assert prompt_evidence.verdict(9, 0, prompt_evidence.evalue(9, 0))[0] == "insufficient_data"
    e11 = prompt_evidence.evalue(11, 0)
    assert prompt_evidence.verdict(11, 0, e11)[0] == "win"
    # A mixed record keeps collecting rather than being retired.
    assert prompt_evidence.verdict(12, 4, prompt_evidence.evalue(12, 4))[0] == "collecting"


def test_eleven_straight_wins_is_the_earliest_possible_promotion():
    """Below this the threshold is unreachable, so no verdict may be returned."""
    from src import prompt_evidence
    assert prompt_evidence.pairs_to_promote(0) == 11
    assert prompt_evidence.evalue(10, 0) < prompt_evidence._T_PROMOTE
    assert prompt_evidence.evalue(11, 0) >= prompt_evidence._T_PROMOTE


def test_a_losing_challenger_is_retired_on_evidence():
    from src import prompt_evidence
    v, why = prompt_evidence.verdict(0, 29, prompt_evidence.evalue(0, 29))
    assert v == "reject"
    assert "not absence of evidence" in why


def test_old_experiment_rows_still_count(monkeypatch):
    """Rows written before this change carry rates, not integers. Reconstruct
    them rather than discarding the history the new gate exists to accumulate."""
    from src import prompt_loop
    old = {"n": 20, "win_rate": 0.3, "tie_rate": 0.7, "mean_diff": 0.048}
    got = prompt_loop._run_counts(old)
    assert got["wins"] == 6 and got["losses"] == 0 and got["n"] == 20
    assert abs(got["sum_d"] - 0.96) < 1e-9
    new = {"n": 11, "wins": 3, "losses": 0, "ties": 8, "sum_d": 0.418, "sum_d2": 0.058}
    assert prompt_loop._run_counts(new)["wins"] == 3


def test_a_run_that_scored_no_pairs_contributes_nothing():
    """A thin or vendor-ruined run must not be banked as LOSSES.

    `_summarise` returns early when n < _MIN_N with only `n` set — no wins, no
    win_rate. Reconstructing from rates then computed losses = n - 0 - 0 = n, so
    an outage that left 3 usable pairs recorded 3 defeats. That is the
    two-strikes failure coming back through the accumulator: the thinner the
    sample, the harder the challenger is punished for it.
    """
    from src import prompt_loop
    for thin in ({"ok": True, "verdict": "inconclusive", "n": 5},
                 {"ok": True, "verdict": "inconclusive", "n": 3,
                  "reason": "only 3 paired generations completed"}):
        got = prompt_loop._run_counts(thin)
        assert got["losses"] == 0, got
        assert got["wins"] == 0 and got["n"] == 0, got
