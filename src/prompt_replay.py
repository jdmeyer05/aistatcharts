"""Run a challenger against the champion on the same frozen situations.

THIS IS THE ONLY REASON THE LOOP IS ALLOWED TO PROMOTE ANYTHING. A critic that
proposes an edit and a system that ships it has measured nothing — it has
measured a model's opinion of its own work. Here both prompts are handed the
SAME historical payloads, their outputs are graded by the same deterministic
rules, and the comparison is paired: each situation contributes one difference,
so a quiet weekend and a CPI morning cannot swap places between the two arms.

HOLDOUT ONLY. Replay draws from `split = 'holdout'`, which the critic is never
shown. A prompt written against the failures in one sample and then scored on
that same sample will always look better; the whole point of paying for a
second sample is that it can say no.

MODEL HELD FIXED. Market-driver escalates to Opus on big days in production.
Replay does not — both arms run on the same model so the difference measured is
the prompt's, not the router's.

WHAT THIS CANNOT MEASURE. Replay scores grounding, contradiction and shape:
everything the rules can settle from the payload alone. It cannot score whether
the challenger's CALLS would have been right, because a call written today about
a week that already happened is not a forecast. Calibration is therefore only
ever measured forward, on live claims, and never enters the promotion gate. Read
that limit as a hard one — it is the difference between a backtest and a
hindsight machine.
"""

from __future__ import annotations

import json
import logging
import random
import re
import time

logger = logging.getLogger(__name__)

_REPLAY_MODEL = {
    "market_driver": "gemini-3.1-pro-preview",
    "home_interpret": "claude-opus-5",
    "es_audit": "claude-sonnet-5",
    "news_digest": "claude-sonnet-5",
}

# MUST TRACK PRODUCTION, per surface. The model was held fixed from the start;
# the rest of the generation config was not, and it silently drifted. The worst
# of it: news_digest is served at effort="low" and was replayed at "medium", so
# the gate was scoring a more deliberate model than the page ever runs. Budgets
# cap reasoning AND prose together on these models, so a replay ceiling above
# production's also hides truncation defects that real traffic would hit.
# Sources: api/routes/ai.py (home_interpret), src/es_auditor.py (es_audit),
# src/news_digest.py (news_digest).
_REPLAY_PARAMS = {
    "home_interpret": {"max_tokens": 4000, "effort": "medium"},
    "es_audit": {"max_tokens": 3000, "effort": "medium"},
    "news_digest": {"max_tokens": 2000, "effort": "low"},
}
_DEFAULT_PARAMS = {"max_tokens": 6000, "effort": "medium"}

_DEFAULT_N = 24
_MIN_N = 8
# The margin a challenger must clear on top of statistical significance. With
# small samples a difference can be significant and still be worth nothing;
# this is a declared floor, not an estimate.
#
# NO LONGER A GATE (2026-08-29). It is now the ROPE half-width in
# prompt_evidence, where the same 0.02 answers a question this design can
# actually resolve: a challenger inside +/-0.02 is EQUIVALENT (or, if it also
# crossed the evidence threshold, TRIVIAL — reliably better by an amount that
# does not matter). The old gate collapsed both into "reject", which was
# indistinguishable from a challenger that was genuinely worse, and two of those
# retired it.
_MIN_MARGIN = 0.02
# The bootstrap CI is kept as a DESCRIPTIVE statistic on the run and no longer
# decides anything. It could not: with 8 of 11 differences at zero the 2.5th
# percentile is pinned at exactly 0.000 by (8/11)**11 = 3.01% > 2.5%, so its
# lower bound was arithmetic rather than evidence. A percentile bootstrap needs
# at least 4 discordant pairs to EVER exclude zero, at any n.
_BOOTSTRAP = 2000
# Share of the drawn payloads that must survive generation for a statistical
# rejection to mean anything. DECLARED, not estimated — like _MIN_MARGIN above.
_MIN_USABLE_SHARE = 0.6


# ── generation ────────────────────────────────────────────────────

def _user_message(surface: str, payload: dict) -> str:
    if surface == "market_driver":
        body = json.dumps(payload, indent=2, default=str)[:12000]
        tail = ("Markets are CLOSED — frame paragraph 2 as the current after-hours / weekend "
                "stance and paragraph 3 as the forward view for the next session."
                if not payload.get("market_open") else
                "Markets are OPEN — frame paragraph 2 as the live regime and paragraph 3 as "
                "intraday levels + overnight catalysts.")
        return "Context (cite from here only):\n```json\n" + body + "\n```\n" + tail

    if surface == "home_interpret":
        ctx = ""
        try:
            from api.routes.ai import PAGE_CONTEXT
            ctx = PAGE_CONTEXT.get("home_page", "")
        except Exception as e:
            # Both arms lose the same context, so the A/B stays fair — but the
            # replayed system is no longer the one production serves, and any
            # ABSOLUTE reading taken off it is wrong. home_page's blurb is ~5.8k
            # chars; without it the same prompt on the same payloads came back
            # ~100 words shorter than the stored production outputs, which is
            # enough to hide a word-cap violation entirely. WARNING, not debug:
            # a degraded harness must announce itself.
            logger.warning(f"prompt_replay: PAGE_CONTEXT unavailable ({e}) — replaying "
                           "WITHOUT the page blurb. Paired comparison is still valid; "
                           "absolute scores are NOT comparable to production.")
        return (
            "Page: home_page\n\n"
            f"What this page shows: {ctx}\n\n"
            "Current data:\n```json\n"
            + json.dumps(payload, default=str, indent=2)[:20000]
            + "\n```\n\nInterpret these results for me. What does it mean?"
        )

    if surface == "news_digest":
        lines = (payload or {}).get("lines") or []
        if not lines:
            lines = [f"[tier {h.get('tier','?')}] {h.get('title','')}"
                     for h in (payload or {}).get("headlines", []) if h.get("title")]
        return "Headlines, most index-relevant first:\n\n" + "\n".join(lines)

    if surface == "es_audit":
        return ("Audit this payload:\n```json\n"
                + json.dumps(payload, indent=1, default=str)[:14000] + "\n```")

    return json.dumps(payload, default=str)[:12000]


# Failures that belong to the VENDOR, not to the prompt under test. A 503 from
# an overloaded model lands on whichever arm happened to call during the spike,
# so counting it against the challenger is counting a coin flip. Everything
# else — a refusal, a truncation, output that will not parse — is caused by the
# prompt and is exactly what the gate is supposed to punish.
_INFRA_FAILURES = {"api_error", "api_fatal", "no_key"}

# Vendor errors worth waiting out. Discounting a 503 from the failure count was
# not enough on its own: it still deletes the pair, and a deleted pair shrinks
# the sample until the bootstrap CI swallows a real result. On 2026-08-28 Gemini
# returned 503 through the afternoon and killed 15 of 24 pairs in one replay and
# 8 in another, which is what left market_driver v3 unresolved. Retrying costs
# seconds; not retrying costs the experiment.
_RETRYABLE = re.compile(
    r"(?<![0-9])(429|500|502|503|504)(?![0-9])|unavailable|overloaded|resource[_ ]exhausted|"
    r"rate.?limit|timeout|timed out|deadline|connection|temporarily",
    re.I)
_MAX_ATTEMPTS = 3
_BACKOFF_S = (2.0, 6.0)


def _retryable(e: Exception) -> bool:
    return bool(_RETRYABLE.search(f"{type(e).__name__} {e}"))


def _generate_once(surface: str, system: str, payload: dict) -> tuple[object | None, str | None]:
    """One replay generation ATTEMPT.

    Returns `(output, failure_reason)`. The reason is None on success and
    otherwise names WHY, because the previous version returned a bare None down
    four different paths — missing key, API exception, refusal/max_tokens, and
    unparseable output — and only one of them logged anything. The gate then
    charged all four to the prompt. On 2026-08-28 that rejected market_driver v3
    for "failed to generate more often (5 vs 3)" during a Gemini 503 spike,
    while the same replay had it winning +0.036 with a CI excluding zero.
    """
    model = _REPLAY_MODEL.get(surface, "claude-sonnet-5")
    user = _user_message(surface, payload)

    try:
        if model.startswith("gemini"):
            from google import genai
            from google.genai import types
            from src.api_keys import get_secret
            key = get_secret("GEMINI_API_KEY")
            if not key:
                logger.error("prompt_replay: GEMINI_API_KEY missing — replay cannot run")
                return None, "no_key"
            client = genai.Client(api_key=key)
            resp = client.models.generate_content(
                model=model,
                contents=f"{system}\n\n{user}",
                config=types.GenerateContentConfig(
                    max_output_tokens=12000,
                    temperature=0.25,
                    response_mime_type="application/json",
                    thinking_config=types.ThinkingConfig(thinking_level="medium"),
                ),
            )
            raw = resp.text or ""
        else:
            import anthropic
            from src.api_keys import get_secret
            key = get_secret("ANTHROPIC_API_KEY")
            if not key:
                logger.error("prompt_replay: ANTHROPIC_API_KEY missing — replay cannot run")
                return None, "no_key"
            client = anthropic.Anthropic(api_key=key)
            params = _REPLAY_PARAMS.get(surface, _DEFAULT_PARAMS)
            kwargs = {
                "model": model,
                "max_tokens": params["max_tokens"],
                "output_config": {"effort": params["effort"]},
                "system": [{"type": "text", "text": system,
                            "cache_control": {"type": "ephemeral"}}],
                "messages": [{"role": "user", "content": user}],
                "timeout": 120.0,
            }
            # `fallbacks` is an Opus-5 / Fable-5 parameter; Sonnet 5 rejects it
            # with a 400. Same trap the ES auditor hit.
            if model.startswith("claude-opus") or model.startswith("claude-fable"):
                kwargs["betas"] = ["server-side-fallback-2026-07-01"]
                kwargs["fallbacks"] = "default"
            msg = client.beta.messages.create(**kwargs)
            stop = getattr(msg, "stop_reason", None)
            if stop in ("refusal", "max_tokens"):
                logger.warning(f"prompt_replay: {surface} generation stopped on {stop}")
                return None, stop
            raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception as e:
        # WARNING, not DEBUG: this is the only place the actual cause appears,
        # and the caller can only report a count. A silent DEBUG here is what
        # made an uninstalled SDK look like an empty holdout set.
        kind = "api_error" if _retryable(e) else "api_fatal"
        logger.warning(f"prompt_replay: generation failed ({surface}, {model}) [{kind}]: {e}")
        return None, kind

    obj = _parse(surface, raw)
    if obj is None:
        logger.warning(f"prompt_replay: {surface} output did not parse ({len(raw or '')} chars)")
        return None, "unparseable"
    return obj, None


def _generate(surface: str, system: str, payload: dict) -> tuple[object | None, str | None]:
    """One replay generation, waiting out transient vendor failures.

    ONLY INFRASTRUCTURE IS RETRIED. A refusal, a truncation or output that will
    not parse is a property of the prompt on this payload — running it again is
    just paying twice for the same verdict, and hiding a real defect the gate is
    supposed to catch. A 503 is a property of the afternoon.
    """
    last = None
    for attempt in range(_MAX_ATTEMPTS):
        obj, why = _generate_once(surface, system, payload)
        if why != "api_error":
            return obj, why          # success, prompt fault, or a permanent error
        last = why
        if attempt < _MAX_ATTEMPTS - 1:
            wait = _BACKOFF_S[min(attempt, len(_BACKOFF_S) - 1)]
            logger.info(f"prompt_replay: {surface} vendor failure, retrying in {wait:.0f}s "
                        f"(attempt {attempt + 2}/{_MAX_ATTEMPTS})")
            time.sleep(wait)
    logger.warning(f"prompt_replay: {surface} gave up after {_MAX_ATTEMPTS} attempts")
    return None, last


def _parse(surface: str, raw: str):
    txt = (raw or "").strip()
    if surface in ("home_interpret", "news_digest"):
        return txt or None
    if txt.startswith("```"):
        txt = txt.strip("`")
        txt = txt.removeprefix("json").strip()
    if not txt:
        return None
    try:
        obj = json.loads(txt)
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    if surface == "market_driver" and not (obj.get("paragraphs") or {}).get("what_happened"):
        return None
    return obj


# ── the experiment ────────────────────────────────────────────────

def run(surface: str, champion_version: int, challenger_version: int,
        n: int = _DEFAULT_N, seed: int = 7) -> dict:
    """Paired replay of two prompt versions over holdout payloads."""
    from src import prompt_snapshots, prompt_registry, prompt_rules

    champ = prompt_registry.get_version(surface, champion_version)
    chall = prompt_registry.get_version(surface, challenger_version)
    if not champ or not chall:
        return {"ok": False, "error": "champion or challenger version not found"}

    rows = prompt_snapshots.fetch(surface, split="holdout", limit=max(n * 3, 60), days=90)
    if len(rows) < _MIN_N:
        return {"ok": False, "error": f"only {len(rows)} holdout snapshots, need {_MIN_N}",
                "n_holdout_available": len(rows)}

    # Spread the draw across the record rather than taking the most recent n:
    # the last 24 generations can all be one quiet week, and a prompt that wins
    # on a quiet week has not been tested.
    rng = random.Random(seed)
    sample = rng.sample(rows, min(n, len(rows)))

    pairs = []
    for snap in sample:
        payload = snap.get("payload") or {}
        if payload.get("_truncated"):
            continue          # a trimmed payload is not the situation the model saw
        a, why_a = _generate(surface, champ["body"], payload)
        b, why_b = _generate(surface, chall["body"], payload)
        if a is None or b is None:
            pairs.append({"snapshot_id": snap["id"], "champion": None, "challenger": None,
                          "champion_failed": a is None, "challenger_failed": b is None,
                          "champion_why": why_a, "challenger_why": why_b})
            continue
        ga = prompt_rules.grade(surface, payload, a)
        gb = prompt_rules.grade(surface, payload, b)
        pairs.append({
            "snapshot_id": snap["id"],
            "champion": ga, "challenger": gb,
            "champion_failed": False, "challenger_failed": False,
            "challenger_regressions": prompt_rules.regression_failures(gb.get("findings") or []),
            "champion_regressions": prompt_rules.regression_failures(ga.get("findings") or []),
        })

    return _summarise(surface, champ, chall, pairs)


def _summarise(surface: str, champ: dict, chall: dict, pairs: list[dict]) -> dict:
    scored = [p for p in pairs if p.get("champion") and p.get("challenger")]
    n = len(scored)
    fails_a = sum(1 for p in pairs if p.get("champion_failed"))
    fails_b = sum(1 for p in pairs if p.get("challenger_failed"))

    # Only PROMPT-attributable failures may count against an arm. A vendor 503
    # hits whichever arm called during the spike; charging it to the challenger
    # is scoring a coin flip as a defect.
    def _blamed(side: str) -> int:
        return sum(1 for p in pairs
                   if p.get(f"{side}_failed")
                   and p.get(f"{side}_why") not in _INFRA_FAILURES)

    blamed_a, blamed_b = _blamed("champion"), _blamed("challenger")
    infra = (fails_a + fails_b) - (blamed_a + blamed_b)
    if infra:
        logger.warning(f"prompt_replay: {surface} discounted {infra} vendor-side "
                       "generation failures from the gate")

    if n < _MIN_N:
        # A BROKEN ARM MUST NOT READ LIKE A QUIET ONE. `_generate` swallows its
        # exception and returns None, so a missing dependency, a dead key or a
        # changed SDK produces exactly the same "inconclusive, n=0" line as a
        # surface that simply has not accumulated holdout rows yet — and the
        # challenger sits unevaluated indefinitely while the log looks calm.
        # (Local run, 2026-08-28: 48 generations failed on an uninstalled
        # google-genai and reported n=0 in two seconds.) Same failure family as
        # the ES card's empty panels: say the lookup broke, do not render it as
        # an absence of news.
        broke = fails_a + fails_b
        if broke:
            logger.error(
                f"prompt_replay: {surface} INCONCLUSIVE because generation failed — "
                f"champion {fails_a}, challenger {fails_b} of {len(pairs)} payloads. "
                "This is a broken arm, not a thin sample; check keys and SDKs.")
            return {"ok": False, "verdict": "inconclusive", "n": n,
                    "error": f"generation failed on {broke} of {2 * len(pairs)} calls",
                    "generation_failures": {"champion": fails_a, "challenger": fails_b}}
        return {"ok": True, "verdict": "inconclusive", "n": n,
                "reason": f"only {n} paired generations completed",
                "generation_failures": {"champion": fails_a, "challenger": fails_b}}

    diffs = [float(p["challenger"]["score"] or 0) - float(p["champion"]["score"] or 0)
             for p in scored]
    mean_diff = sum(diffs) / n
    lo, hi = _bootstrap_ci(diffs)

    def _counts(key):
        out = {"critical": 0, "major": 0, "minor": 0}
        for p in scored:
            for k, v in (p[key].get("counts") or {}).items():
                out[k] = out.get(k, 0) + v
        return out

    c_a, c_b = _counts("champion"), _counts("challenger")

    # RELATIVE, NOT ABSOLUTE — and the difference decided a real promotion.
    # "Reintroduces" can only mean a defect the CHAMPION does not already
    # produce on the same payloads. Counting the challenger's regression rules
    # alone made this gate fire whenever a defect was endemic to the surface,
    # so the challenger was blamed for inheriting the champion's problem: on
    # 2026-08-27 market_driver v3 scored 0.950 against the champion's 0.902,
    # with a CI excluding zero, and was rejected for `invented_ticker` while
    # the champion produced exactly as many of them. A challenger is only
    # charged for a defect it makes MORE common than the prompt it replaces.
    def _rule_rate(key: str) -> dict:
        out: dict = {}
        for p in scored:
            for r in (p.get(key) or []):
                out[r] = out.get(r, 0) + 1
        return out

    reg_b, reg_a = _rule_rate("challenger_regressions"), _rule_rate("champion_regressions")
    regressions = sorted(r for r, n in reg_b.items() if n > reg_a.get(r, 0))

    # THE GATE. Each condition is here because of a specific way this could go
    # wrong: significance without size, a win bought by reintroducing a known
    # defect, or a challenger that simply fails to produce parseable output more
    # often and is never penalised for it because failures are not scored.
    # Defects the challenger actually exhibits, versus the sample simply being
    # too thin to tell. The distinction matters because two rejects retire a
    # challenger permanently, and only the first kind is evidence about it.
    faults: list[str] = []
    if regressions:
        detail = ", ".join(f"{r} ({reg_b.get(r, 0)} vs champion {reg_a.get(r, 0)})"
                           for r in regressions)
        faults.append(f"reintroduces known defects: {detail}")
    if c_b["critical"] > c_a["critical"]:
        faults.append(f"more critical findings ({c_b['critical']} vs {c_a['critical']})")
    if blamed_b > blamed_a:
        faults.append(f"failed to generate more often ({blamed_b} vs {blamed_a}, "
                      "vendor errors excluded)")
    # THE STATISTICAL DECISION NO LONGER LIVES HERE. It used to: a nightly
    # percentile bootstrap CI plus a margin, both recomputed from scratch and
    # both discarded afterwards. That gate could not return "win" on our actual
    # data no matter how good the challenger was -- with 3 discordant pairs the
    # exact sign-test floor is 0.125, and the bootstrap's lower bound was pinned
    # at 0.000 by (8/11)**11 = 3.01% > 2.5%. See src/prompt_evidence.
    #
    # What stays here is the part that IS a per-run fact about this prompt: the
    # regression veto, more criticals, and failing to generate. Those are
    # substantive faults and they reject on whatever pairs survived. The
    # accumulating evidence -- and therefore promote/retire -- is decided by
    # prompt_loop.evaluate_cycle over the challenger's whole life.
    reasons = list(faults)
    verdict = "reject" if faults else "pending"

    # A RUN THE VENDOR RUINED IS NOT A VERDICT ON THE PROMPT. Discounting a 503
    # from the failure count still lets it decide the outcome the other way: it
    # deletes the pair, the sample shrinks, the bootstrap CI widens, and the
    # challenger is rejected for "CI includes zero" — which is a fact about
    # Gemini's capacity that day, not about the prompt. Two such rejects retire
    # it for good. So when vendor errors have eaten the sample and the only
    # complaints left are statistical, the honest answer is "run it again".
    # A substantive fault still rejects on whatever pairs survived.
    attempted = len(pairs)
    degraded = bool(infra) and attempted and n < _MIN_USABLE_SHARE * attempted
    if degraded and verdict == "reject" and not faults:
        verdict = "inconclusive"
        reasons.insert(0, f"vendor failures left {n} usable pairs of {attempted}; "
                          "rerun rather than a verdict on the prompt")
    return {
        "ok": True,
        "surface": surface,
        "champion_version": champ["version"],
        "challenger_version": chall["version"],
        "n": n,
        "verdict": verdict,
        "reasons": reasons,
        "mean_diff": round(mean_diff, 4),
        "ci95": [round(lo, 4), round(hi, 4)],
        "champion_score": round(sum(float(p["champion"]["score"] or 0) for p in scored) / n, 4),
        "challenger_score": round(sum(float(p["challenger"]["score"] or 0) for p in scored) / n, 4),
        "champion_counts": c_a,
        "challenger_counts": c_b,
        "regressions": regressions,
        # Both sides, so a rejection can be read without rerunning the replay.
        "regression_rates": {"champion": reg_a, "challenger": reg_b},
        "generation_failures": {"champion": fails_a, "challenger": fails_b,
                                "champion_blamed": blamed_a, "challenger_blamed": blamed_b,
                                "vendor_discounted": infra},
        "win_rate": round(sum(1 for d in diffs if d > 0) / n, 4),
        "tie_rate": round(sum(1 for d in diffs if d == 0) / n, 4),
        # RAW COUNTS, because rates lose the sample size and the accumulating
        # gate needs integers. Ties are carried for the record but are not
        # evidence -- a tied pair says nothing about which prompt is better,
        # which is exactly why 8 of our 11 pairs did nothing but inflate n.
        "wins": sum(1 for d in diffs if d > 0),
        "losses": sum(1 for d in diffs if d < 0),
        "ties": sum(1 for d in diffs if d == 0),
        # Sufficient statistics for the equivalence arm, so "these prompts are
        # the same" can be distinguished from "not enough data yet" without
        # storing every pair.
        "sum_d": round(sum(diffs), 6),
        "sum_d2": round(sum(d * d for d in diffs), 6),
    }


def _bootstrap_ci(diffs: list[float], iters: int = _BOOTSTRAP, seed: int = 11) -> tuple[float, float]:
    """Percentile bootstrap on the paired differences.

    Bootstrap rather than a t-test because these scores are bounded in [0,1],
    heavily tied at 1.0 on clean days, and nothing about their distribution is
    normal at n = 24.
    """
    if not diffs:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(diffs)
    means = []
    for _ in range(iters):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return (means[int(0.025 * iters)], means[min(iters - 1, int(0.975 * iters))])
