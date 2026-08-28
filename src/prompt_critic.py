"""The adversarial half: read the record, attack the prompt, rewrite it.

TWO CALLS, NOT ONE, AND THAT IS THE WHOLE DESIGN. A single "improve this prompt"
call produces a model that flatters its own edit — it decides what is wrong and
what fixes it in the same breath, and every proposal arrives pre-justified. So
the critic is given the prompt, the outputs, the deterministic findings and the
calibration record, and is told to find what the PROMPT causes and to propose
nothing. A second call sees the critique and the prompt and writes the
replacement. The editor cannot invent a problem; the critic cannot smuggle in a
fix.

THE CRITIC IS FED EVIDENCE, NOT VIBES. Every failure it is shown was found by a
rule in src/prompt_rules.py or settled against price in src/prompt_claims.py.
It is explicitly told that the deterministic findings are ground truth and its
own reading is not, because the failure mode of this stage is a model inventing
elegant criticism of an output that was fine.

DISCOVERY ROWS ONLY. The critic never sees holdout snapshots. A challenger
written against the sample that later judges it would be measuring its own
homework, and the improvement curve would be real-looking and fake — the same
in-sample/out-of-sample discipline the strategy work on this platform already
runs on.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

_CRITIC_MODEL = "claude-opus-5"
_EDITOR_MODEL = "claude-opus-5"

# How many graded examples the critic reads. Enough to see a pattern, small
# enough that the worst cases are not diluted by routine ones.
_N_EXAMPLES = 12
_N_WORST = 6


_CRITIC_SYSTEM = """You are auditing a production system prompt that writes market commentary. You are not here to be encouraging and you are not here to rewrite anything. You are here to find what this prompt makes the model do wrong.

WHAT YOU ARE GIVEN
- The prompt currently in production.
- Real outputs it produced, each with the exact payload the model was shown.
- Deterministic rule findings for those outputs. THESE ARE GROUND TRUTH. They were produced by code, not by a model, and each one traces to a defect that reached a live page.
- Where applicable, the calibration record: falsifiable calls the prompt required, and how they resolved against price, alongside the base rate of the same call.

HOW TO THINK
- Attribute failures to the PROMPT, not to the model or the data. The question is always: what instruction is missing, ambiguous, or actively misleading such that a competent model produced this?
- A failure that appears once is an anecdote. A failure that appears three times in twelve outputs is a prompt defect. Say which you have.
- Rule findings outrank your own reading. If you believe an output is bad but no rule fired, say so and mark it as your own judgment, clearly, so it can be weighted lower.
- Look hardest at what the prompt does NOT say. Most defects here are omissions, not bad sentences.
- Calibration failures are prompt failures too. If confidence numbers cluster at one value, or the calls beat nothing but their own base rate, the instruction asking for them is not working.

WHAT DOES NOT COUNT
- Style preferences. "Could be more engaging" is not a finding.
- Anything you cannot tie to a specific output or a specific number in the record.
- Speculation about outputs you were not shown.
- Restating a rule finding as though you found it. Cite it and move on to what it implies about the prompt.

Return ONLY valid JSON:
{
  "findings": [
    {
      "severity": "high|medium|low",
      "prompt_defect": "the instruction that is missing, ambiguous or wrong — one sentence",
      "evidence": "which outputs, which rules, which numbers",
      "frequency": "how many of the examples shown exhibit it",
      "source": "rule|calibration|judgment"
    }
  ],
  "verdict": "one paragraph: is this prompt failing in a patterned way, or is it working and the failures are noise?"
}

Rank findings by how misleading the resulting output would be to someone trading off it. Return at most 6. If the prompt is working, return few findings or none and say so in the verdict — an empty list is a legitimate and useful answer, and manufacturing findings to look thorough is the one unrecoverable error here."""


_EDITOR_SYSTEM = """You are editing a production system prompt. You have been handed an audit of it. Your job is to return the complete revised prompt — not a diff, not a commentary, not an explanation.

THE EDIT IS SURGICAL, NOT A REWRITE. Every line in the current prompt is there because something went wrong once. You are changing what the audit found and nothing else. If you cannot point to a finding that justifies a change, do not make it. A prompt that gets rewritten wholesale every night has no memory, and this one is mostly memory.

HARD INVARIANTS — breaking any of these means your output is discarded unread:
{invariants}

HOW TO WRITE THE CHANGES
- Prefer adding a specific, checkable rule over adding an adjective. "Never call a VIX under 20 elevated" survives; "be careful about volatility language" does not.
- State the failure the rule prevents, briefly, in the rule itself. The next editor needs to know why it is there.
- If a finding says an instruction is ambiguous, resolve the ambiguity by naming the case, not by adding emphasis. Capital letters do not disambiguate.
- If two instructions conflict, fix the conflict rather than adding a third that arbitrates.
- Keep the length within 20% of what you were given. Prompts that only grow eventually bury their own important rules.

Return ONLY the revised prompt text. No preamble, no code fence, no trailing notes."""


# Contract each surface's output must keep. A challenger that would break the
# page is refused mechanically, before any scoring, because scoring it means
# spending real calls on something that cannot ship.
_INVARIANTS = {
    "market_driver": [
        'The output must remain a single JSON object with keys: regime_label, paragraphs (with what_happened, whats_driving, what_to_watch), citations, confidence, calls.',
        'The `calls` contract must survive intact: 2-4 machine-resolvable calls, ops limited to up_gte / down_gte / abs_lt / abs_gte / outperform, subjects drawn from the payload quotes, sessions 1-5, threshold in percent, confidence a probability between 0.01 and 0.99.',
        'The instruction that calls are measured from the first close AT OR AFTER the note must remain, in substance. Removing it hands the model a move it has already seen.',
        'The rule that a quote with no change_pct_1d means the move is UNKNOWN, not zero, must remain.',
        'The VIX band calibration (complacent / muted / elevated / stressed / panic keyed off vix_level_band, not the 1-day change) must remain.',
        'The two-headline-feeds rule — check macro_headlines before concluding there is no catalyst — must remain.',
        'The breadth rule — a divergent reading qualifies an index move as narrow — must remain.',
    ],
    "home_interpret": [
        'The output must remain plain prose with bullet points, ending with a line beginning "Bottom line:".',
        'The rule that every number must trace to the payload, and that derivations must show their inputs, must remain.',
        'The instruction never to cite a ticker, fund or person absent from the payload must remain.',
        # Phrased as one soft bullet in a style list, this was broken by 10 of 10
        # outputs on record. It only holds while it is stated as its own rule.
        'The length ceiling must survive AS A CEILING, not a target: at most 6 bullets '
        'plus the closing Bottom line, 220 words absolute, carried in its own section '
        'rather than as one item in a style list. A rewrite that softens it or folds it '
        'back into the style bullets is a regression.',
    ],
    "news_digest": [
        'The output must remain 2-3 plain sentences of prose under 70 words, with no bullets, no heading and no preamble.',
        'The rule that ONLY the supplied headlines may be used — no number, name, ticker or event from outside them — must remain.',
        "The prohibition on stating or implying a direction, a level or a bias must remain, in "
        "substance. This is the ES card's founding distinction between context and signal, and "
        "this paragraph is the easiest place on that card to blur it.",
        'The instruction that saying "nothing new" is a useful and honest brief must remain, so the model does not manufacture significance on a quiet tape.',
        'The tier weighting must remain: tier 1 is policy and hard data, tier 3 is single-company news that rarely moves the index.',
    ],
    "es_audit": [
        'The output must remain a single JSON object of the form {"findings": [{"severity", "where", "finding"}]}.',
        'The auditor must never state what the market will do. It reports contradictions within the page and nothing else.',
        'An empty findings list must remain an explicitly stated success case, so the model does not manufacture findings to fill it.',
        'The requirement to quote both clashing values in every finding must remain.',
    ],
}


def _anthropic():
    import anthropic
    from src.api_keys import get_secret
    key = get_secret("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    return anthropic.Anthropic(api_key=key)


def _call(model: str, system: str, user: str, max_tokens: int = 16000,
          timeout_s: float = 180.0) -> str:
    client = _anthropic()
    msg = client.beta.messages.create(
        model=model,
        max_tokens=max_tokens,
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{"type": "text", "text": system}],
        messages=[{"role": "user", "content": user}],
        timeout=timeout_s,
    )
    if getattr(msg, "stop_reason", None) == "refusal":
        raise RuntimeError("model declined")
    if getattr(msg, "stop_reason", None) == "max_tokens":
        raise RuntimeError("response truncated at max_tokens")
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def _parse_json(raw: str) -> dict | None:
    txt = (raw or "").strip()
    if "```" in txt:
        parts = txt.split("```")
        for p in parts:
            p = p.removeprefix("json").strip()
            if p.startswith("{"):
                txt = p
                break
    try:
        obj = json.loads(txt)
        return obj if isinstance(obj, dict) else None
    except Exception:
        try:
            from src.json_repair import repair_json  # type: ignore
            return repair_json(txt)
        except Exception:
            return None


def _build_dossier(surface: str, graded: list[dict], scoreboard: dict | None) -> str:
    """What the critic reads. Worst cases first, then a random-ish spread.

    Payloads are heavily trimmed: the critic needs enough context to judge
    whether a claim was supportable, not the whole board. Sending the full
    payload for twelve examples would blow the context and bury the findings.
    """
    ranked = sorted(graded, key=lambda g: (g["grade"].get("score")
                                           if g["grade"].get("score") is not None else 1.0))
    worst = ranked[:_N_WORST]
    # The rest are spread evenly across the remaining score range rather than
    # taken from one end. A dossier of nothing but failures teaches the critic
    # that failure is the normal case and invites it to find defects in the
    # clean outputs too; a dossier of nothing but clean ones hides the pattern.
    remainder = ranked[_N_WORST:]
    want = max(0, _N_EXAMPLES - len(worst))
    if remainder and want:
        step = max(1, len(remainder) // want)
        rest = remainder[::step][:want]
    else:
        rest = []
    chosen = worst + rest

    blocks = []
    for i, g in enumerate(chosen, 1):
        snap = g["snapshot"]
        payload = snap.get("payload") or {}
        out = snap.get("output") or {}
        findings = g["grade"].get("findings") or []
        blocks.append(
            f"### EXAMPLE {i} — {snap.get('created_at', '')[:16]} "
            f"({snap.get('session_phase', '?')}, prompt v{snap.get('prompt_version', 0)})\n"
            f"PAYLOAD (trimmed):\n```json\n{json.dumps(payload, default=str)[:3500]}\n```\n"
            f"OUTPUT:\n```json\n{json.dumps(out, default=str)[:2500]}\n```\n"
            f"RULE FINDINGS ({len(findings)}): "
            f"{json.dumps(findings, default=str)[:1500] if findings else 'none — this output passed every rule'}\n"
        )

    parts = ["\n".join(blocks)]
    if scoreboard and scoreboard.get("n"):
        parts.append(
            "### CALIBRATION RECORD (resolved calls)\n"
            f"```json\n{json.dumps(scoreboard, default=str)[:2500]}\n```\n"
            "Read `brier_skill` first: positive means the stated confidences carried "
            "information beyond quoting the base rate, zero means they did not, negative "
            "means the base rate would have been the better forecast."
        )
    else:
        parts.append("### CALIBRATION RECORD\nNo calls have resolved yet in this window.")
    return "\n\n".join(parts)


def critique(surface: str, graded: list[dict], scoreboard: dict | None,
             prompt_body: str) -> dict:
    """Stage one: what is this prompt doing wrong? No fixes allowed."""
    if not graded:
        return {"ok": False, "error": "no graded snapshots to critique"}

    counts = {"critical": 0, "major": 0, "minor": 0}
    for g in graded:
        for k, v in (g["grade"].get("counts") or {}).items():
            counts[k] = counts.get(k, 0) + v

    user = (
        f"SURFACE: {surface}\n"
        f"SAMPLE: {len(graded)} outputs, rule findings totalling "
        f"{counts['critical']} critical / {counts['major']} major / {counts['minor']} minor.\n\n"
        f"=== PROMPT IN PRODUCTION ===\n{prompt_body}\n=== END PROMPT ===\n\n"
        f"=== EVIDENCE ===\n{_build_dossier(surface, graded, scoreboard)}\n"
    )
    try:
        raw = _call(_CRITIC_MODEL, _CRITIC_SYSTEM, user)
    except Exception as e:
        logger.warning(f"prompt_critic: critique call failed for {surface}: {e}")
        return {"ok": False, "error": str(e)}

    parsed = _parse_json(raw)
    if not parsed or "findings" not in parsed:
        return {"ok": False, "error": "critique returned unparseable JSON"}
    return {"ok": True, "model": _CRITIC_MODEL, **parsed,
            "sample_size": len(graded), "rule_counts": counts}


def propose(surface: str, prompt_body: str, critique_result: dict) -> dict:
    """Stage two: the revised prompt. Mechanically validated before it returns."""
    findings = critique_result.get("findings") or []
    if not findings:
        return {"ok": False, "error": "nothing to fix — critic found no defects"}

    invariants = "\n".join(f"- {s}" for s in _INVARIANTS.get(surface, []))
    system = _EDITOR_SYSTEM.replace("{invariants}", invariants or "- Preserve the existing output format exactly.")
    user = (
        f"=== CURRENT PROMPT ===\n{prompt_body}\n=== END CURRENT PROMPT ===\n\n"
        f"=== AUDIT FINDINGS ===\n{json.dumps(findings, indent=1, default=str)[:8000]}\n\n"
        f"Verdict from the auditor: {critique_result.get('verdict', '')}\n\n"
        "Return the complete revised prompt."
    )
    try:
        body = _call(_EDITOR_MODEL, system, user, max_tokens=20000)
    except Exception as e:
        logger.warning(f"prompt_critic: editor call failed for {surface}: {e}")
        return {"ok": False, "error": str(e)}

    ok, why = validate(surface, prompt_body, body)
    if not ok:
        return {"ok": False, "error": f"challenger rejected: {why}", "body": body[:2000]}

    return {"ok": True, "body": body, "model": _EDITOR_MODEL,
            "rationale": _rationale(findings, critique_result)}


def _rationale(findings: list[dict], critique_result: dict) -> str:
    lines = [f"{f.get('severity', '?')}: {f.get('prompt_defect', '')}" for f in findings[:6]]
    return ("Addresses:\n- " + "\n- ".join(lines) +
            f"\n\nAuditor verdict: {critique_result.get('verdict', '')}")[:4000]


def validate(surface: str, champion: str, challenger: str) -> tuple[bool, str]:
    """Cheap structural gate. Catches the ways a rewrite breaks the page.

    Runs before any scoring because a challenger that cannot ship should not
    cost a single generation to find out.
    """
    body = (challenger or "").strip()
    if len(body) < 400:
        return False, f"too short ({len(body)} chars)"
    ratio = len(body) / max(1, len(champion))
    if not (0.6 <= ratio <= 1.6):
        return False, f"length changed by {int((ratio - 1) * 100)}% against a 20% brief"
    if body.lstrip().startswith("```"):
        return False, "wrapped in a code fence"

    required = {
        "market_driver": ["regime_label", "paragraphs", "what_happened", "whats_driving",
                          "what_to_watch", "citations", "confidence", "calls",
                          "vix_level_band", "macro_headlines", "change_pct_1d"],
        # "220" is here so the ceiling cannot be dropped silently: the structural
        # gate refuses the rewrite before it costs a single generation to score.
        "home_interpret": ["Bottom line", "payload", "220"],
        "news_digest": ["headlines", "70 words", "direction"],
        "es_audit": ["findings", "severity"],
    }.get(surface, [])
    missing = [k for k in required if k not in body]
    if missing:
        return False, f"dropped required token(s): {', '.join(missing)}"

    if surface == "market_driver":
        for op in ("up_gte", "down_gte", "abs_lt", "abs_gte", "outperform"):
            if op not in body:
                return False, f"dropped the {op} call op"
    return True, "ok"
