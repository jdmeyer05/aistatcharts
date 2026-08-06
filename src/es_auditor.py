"""Audit the card against itself.

Every AI block on this page NARRATES: it reads the numbers and writes them back
as prose. That is low value here, because the reader goes through every number
anyway. What a model over a page full of numbers is genuinely good at is
something nothing else on the page does — noticing when two parts of it
disagree.

The 2026-08-03 card carried at least three catchable contradictions:

  - the market-driver block wrote "No breadth data in the payload" directly
    above a breadth block reporting 3,403 of 3,410 names live
  - it wrote "a crude break with no matching macro headline" while the tweet
    that caused it sat two modules away
  - it called the tape "cyclical rotation" on IWM +1.58% while equal-weight
    (RSP +0.78%) underperformed the index, which is the opposite of broad

Each is checkable against the page's own contents. None was caught, because
nothing was looking.

DETERMINISTIC CHECKS RUN FIRST AND ARE NOT OPTIONAL. Anything expressible as a
rule belongs in code, where it is testable and cannot hallucinate: those run
always, and the model is handed only what rules cannot express. A model asked to
find everything will invent a fourth finding to keep the first three company.

FINDINGS ARE CLAIMS ABOUT THE PAYLOAD, NOT ABOUT THE MARKET. The auditor never
says what the market will do. It says two parts of this page cannot both be
right, and names them.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-5"
_MAX_FINDINGS = 5


def _deterministic(brief: dict) -> list[dict]:
    """Contradictions expressible as rules. Always run, never hallucinated."""
    out: list[dict] = []

    cond = brief.get("conditions") or {}
    regime = brief.get("regime") or {}
    path = (regime.get("path_implied") or {})
    em = brief.get("expected_move") or {}
    ros = brief.get("rest_of_session") or {}
    setup = brief.get("macro_setup") or {}
    clusters = brief.get("level_clusters") or {}

    # The gate can only speak for factors it could read.
    scored = cond.get("factors_scored")
    if cond.get("verdict") == "stand aside" and isinstance(scored, int) and scored < 3:
        out.append({
            "severity": "high",
            "where": "conditions gate",
            "finding": (f"The strongest warning on the card is being issued off "
                        f"{scored} readable factors. 'Several conditions are hostile "
                        f"at once' is a claim about a conjunction."),
        })

    # Two range estimates that disagree by more than half.
    #
    # Only while path-implied is still an ESTIMATE. It is computed as
    # `range_so_far / typical_pct_covered`, so once the session has covered its
    # full path the divisor is 1.0 and the "estimate" is just the realised
    # range. Comparing a pre-session forecast against the outcome it was trying
    # to predict is not an internal contradiction — it is the forecast being
    # graded, which the card has a track record for. Left unguarded this fired
    # on every quiet session by construction (97.27 forecast vs 44.25 delivered
    # = 2.2x), putting a false "THIS CARD DISAGREES WITH ITSELF" banner above
    # the real content after the close every day.
    implied = em.get("expected_range")
    pi = path.get("implied_range")
    covered = path.get("typical_pct_covered")
    still_forecasting = not isinstance(covered, (int, float)) or covered < 95.0
    if implied and pi and still_forecasting and max(implied, pi) / min(implied, pi) >= 1.5:
        out.append({
            "severity": "medium",
            "where": "expected move vs session character",
            "finding": (f"The options-implied range ({implied:.0f}) and the "
                        f"path-implied range ({pi:.0f}) differ by more than 50%. "
                        f"Any statement about room to run depends on which one it used."),
        })

    # A significance FLAG that disagrees with the p-value beside it.
    #
    # CANNOT CURRENTLY FIRE, and that is deliberate rather than an oversight:
    # `es_macro_setup` derives `size_significant` as `p_size < 0.05`, so the two
    # agree by construction today. It is here because they are computed in one
    # place and READ in another, and the p-values in that table were wrong once
    # already — if the threshold is ever changed on one side, or a driver is
    # added with the flag set by hand, this catches it. Do not delete as dead.
    if setup.get("drivers") and setup.get("direction"):
        for d in setup.get("drivers", []):
            if d.get("size_significant") is False and d.get("p_size", 1) < 0.05:
                out.append({
                    "severity": "high", "where": "macro setup",
                    "finding": (f"Driver '{d.get('label')}' is flagged non-significant "
                                f"while carrying p={d.get('p_size')}. The flag and the "
                                f"p-value disagree."),
                })

    # Rest-of-session quoting a cell it had to borrow.
    if ros.get("available") and ros.get("exact_cell") is False:
        out.append({
            "severity": "low",
            "where": "rest of session",
            "finding": ("The quoted distribution is a fallback cell from the other "
                        "regime at this time of day, not the exact one."),
        })

    # Levels double-counted as agreement.
    if clusters.get("n_cross_method"):
        out.append({
            "severity": "low",
            "where": "reference levels",
            "finding": (f"{clusters['n_cross_method']} price zones each carry several "
                        f"co-located levels. Any read that counts them separately is "
                        f"counting one observation more than once."),
        })

    return out


def _payload(brief: dict) -> dict:
    """Only the parts that make CLAIMS. A model handed the whole brief spends its
    attention on the ladder, which cannot contradict anything."""
    def txt(*path, default=None):
        cur = brief
        for p in path:
            cur = (cur or {}).get(p) if isinstance(cur, dict) else None
        return cur if cur is not None else default

    return {
        "conditions": {k: (brief.get("conditions") or {}).get(k)
                       for k in ("verdict", "score", "note", "factors_scored")},
        "conditions_reasons": [
            {"factor": r.get("factor"), "effect": r.get("effect"), "why": r.get("why")}
            for r in (brief.get("conditions") or {}).get("reasons", [])
        ],
        "derived_read": txt("read"),
        "session_character": {
            "character": txt("regime", "character"),
            "multiplier": txt("regime", "path_implied", "multiplier"),
            "note": txt("regime", "path_implied", "note"),
        },
        # PROVENANCE TRAVELS WITH THE NUMBER. Sending sigmas without
        # `quote_source` had the auditor report the settled 0DTE straddle
        # (7.65) as contradicting VIX1D (45.14) — a divergence the expected-move
        # module already knows about and handles by never making a settled quote
        # the headline. Strip the qualifier and a handled case reads as a defect.
        #
        # The UNIT travels with the number for exactly the same reason. This
        # sent only sigma while the conditions text quotes a RANGE ("Options
        # price 97 handles for the session"), so the auditor compared 97 against
        # sigmas of 60.95 and 64.93 and reported — correctly, on the evidence it
        # was handed — that no estimate supported 97. But 97.27 IS the headline,
        # its `range_handles`. A number stripped of its unit is as unreconcilable
        # as one stripped of its provenance, and produces the same false
        # positive on the most alarming block of the card.
        "expected_move_estimates": [
            {"source": e.get("source"), "sigma_handles": e.get("sigma_handles"),
             "range_handles": e.get("range_handles"),
             "quote_source": e.get("quote_source"),
             "forward_looking": e.get("forward_looking")}
            for e in (brief.get("expected_move") or {}).get("estimates", [])
        ],
        "expected_move_headline_source": txt("expected_move", "headline", "source"),
        "expected_move_headline_range_handles": txt("expected_move", "headline", "range_handles"),
        "consumed_pct": txt("expected_move", "consumed", "pct"),
        "breadth": {k: (brief.get("breadth") or {}).get(k)
                    for k in ("available", "live", "net_advancers_pct", "divergence")},
        "gamma": {k: (brief.get("gamma") or {}).get(k)
                  for k in ("regime", "zero_dte_share", "es_basis_is_live")},
        "macro_setup_drivers": [
            {"label": d.get("label"), "p_size": d.get("p_size"),
             "significant": d.get("size_significant"), "chain": d.get("chain")}
            for d in (brief.get("macro_setup") or {}).get("drivers", [])
        ],
        "macro_setup_size_note": txt("macro_setup", "size", "note"),
        "direction_verdict": txt("macro_setup", "direction", "verdict"),
        "rest_of_session": {k: (brief.get("rest_of_session") or {}).get(k)
                            for k in ("p_new_high", "p_close_above", "band", "regime", "n")},
        "attribution_headline": txt("attribution", "headline"),
        "news_digest": txt("news_digest", "text"),
        # AGE TRAVELS WITH THE HEADLINE, for the same reason. Titles alone had
        # the auditor flag "Exxon and Chevron profits surge on rising oil prices"
        # as contradicting a crude break — a two-day-old earnings story about a
        # previous period, which contradicts nothing about today's tape.
        "headlines": [{"title": h.get("title"), "age": h.get("age"),
                       "hours_ago": h.get("hours_ago")}
                      for h in (brief.get("news") or [])[:12]],
    }


_SYSTEM = """You audit one page of a trading dashboard for INTERNAL CONTRADICTIONS. You are not a narrator, a summariser, or a market analyst. You never say what the market will do.

Your only question: do any two parts of this payload make claims that cannot both be true?

WHAT COUNTS
- A block asserting data is absent when another block contains it.
- A block asserting no catalyst when the headline list contains a matching one.
- Two blocks describing the same quantity with numbers that do not reconcile.
- A characterisation contradicted by a figure elsewhere (e.g. calling a tape broad while an equal-weight measure underperforms).
- A verdict whose stated reasons do not support it.

WHAT DOES NOT COUNT — do not report these:
- Two modules disagreeing about the FUTURE. That is genuine uncertainty, not a contradiction.
- A block being cautious while another is confident.
- Anything you would have to assume market context to call wrong.
- A number you think is unusual. You audit consistency, not plausibility.
- A HEADLINE OLDER THAN TODAY conflicting with today's price action. Headlines carry `age` and `hours_ago`. A story from yesterday or earlier describes a different period and cannot contradict the current tape — an earnings report about a past quarter of rising oil says nothing about whether oil is falling now.
- ESTIMATES THAT CARRY THEIR OWN QUALIFIER. An estimate marked `quote_source: "settled"` or `forward_looking: false` is explicitly not a live figure, and the card already declines to headline it. Divergence between a settled quote and a live one is disclosed behaviour, not an inconsistency. Only flag estimator divergence when the diverging estimates are all live.

RULES
- Quote the specific values that clash. A finding without both sides quoted is not a finding.
- If nothing genuinely contradicts, return an empty list. THIS IS THE COMMON CASE AND IT IS A SUCCESS. Do not manufacture a finding to fill the list.
- Never report more than %d findings; rank by how misleading each would be to a trader.

Return ONLY valid JSON:
{"findings": [{"severity": "high|medium|low", "where": "which two blocks", "finding": "one sentence quoting both clashing values"}]}""" % _MAX_FINDINGS


def audit_card(brief: dict, with_model: bool = True) -> dict:
    """Deterministic checks always; the model only for what rules cannot express."""
    rules = _deterministic(brief)
    model_findings: list[dict] = []
    model_used = None

    if with_model:
        try:
            import anthropic
            from src.api_keys import get_secret
            api_key = get_secret("ANTHROPIC_API_KEY")
            if not api_key:
                raise RuntimeError("no ANTHROPIC_API_KEY")
            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=_MODEL,
                # Sonnet 5 runs adaptive thinking when `thinking` is omitted and
                # max_tokens caps thinking AND the answer together, so this is
                # headroom for both rather than for the JSON alone.
                max_tokens=3000,
                # Medium rather than low: cross-referencing a dozen blocks for
                # claims that cannot both hold is a reasoning task, unlike the
                # digest's summarising. `fallbacks` is an Opus-5/Fable-5
                # parameter and Sonnet 5 rejects it with a 400 — do not add it.
                output_config={"effort": "medium"},
                system=_SYSTEM,
                messages=[{"role": "user",
                           "content": "Audit this payload:\n```json\n"
                                      + json.dumps(_payload(brief), indent=1,
                                                   default=str)[:14000]
                                      + "\n```"}],
            )
            raw = "".join(b.text for b in resp.content
                          if getattr(b, "type", "") == "text").strip()
            txt = (raw or "").strip()
            if "```" in txt:
                txt = txt.split("```")[1].removeprefix("json").strip()
            parsed = json.loads(txt)
            model_findings = [f for f in (parsed.get("findings") or [])
                              if f.get("finding")][:_MAX_FINDINGS]
            model_used = _MODEL
        except Exception as e:
            logger.warning(f"card auditor model pass failed: {e}")

    findings = rules + [{**f, "source": "model"} for f in model_findings]
    for f in findings:
        f.setdefault("source", "rule")
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f.get("severity"), 3))

    return {
        "available": True,
        "findings": findings[:_MAX_FINDINGS + len(rules)],
        "n_rule": len(rules),
        "n_model": len(model_findings),
        "model": model_used,
        "clean": not findings,
        "note": ("Nothing on the card contradicts anything else on it."
                 if not findings else
                 f"{len(findings)} internal inconsistency"
                 f"{'y' if len(findings) == 1 else 'ies'} found."),
        "caveat": (
            "These are claims about THIS PAGE, not about the market. The auditor checks "
            "whether two blocks can both be right; it has no view on which one is, and "
            "no view on what price does next. Rule-based findings are deterministic; "
            "model-sourced ones are a reading of the payload and can be wrong."
        ),
    }
