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

logger = logging.getLogger(__name__)

_REPLAY_MODEL = {
    "market_driver": "gemini-3.1-pro-preview",
    "home_interpret": "claude-opus-5",
    "es_audit": "claude-sonnet-5",
}

_DEFAULT_N = 24
_MIN_N = 8
# The margin a challenger must clear on top of statistical significance. With
# small samples a difference can be significant and still be worth nothing;
# this is a declared floor, not an estimate.
_MIN_MARGIN = 0.02
_BOOTSTRAP = 2000


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
        except Exception:
            # Degrades rather than fails: without the page blurb both arms lose
            # the same context, so the comparison stays fair even though the
            # absolute scores drift from production.
            logger.debug("prompt_replay: PAGE_CONTEXT unavailable, replaying without it")
        return (
            "Page: home_page\n\n"
            f"What this page shows: {ctx}\n\n"
            "Current data:\n```json\n"
            + json.dumps(payload, default=str, indent=2)[:20000]
            + "\n```\n\nInterpret these results for me. What does it mean?"
        )

    if surface == "es_audit":
        return ("Audit this payload:\n```json\n"
                + json.dumps(payload, indent=1, default=str)[:14000] + "\n```")

    return json.dumps(payload, default=str)[:12000]


def _generate(surface: str, system: str, payload: dict) -> object | None:
    """One replay generation. Returns the parsed output, or None on failure."""
    model = _REPLAY_MODEL.get(surface, "claude-sonnet-5")
    user = _user_message(surface, payload)

    try:
        if model.startswith("gemini"):
            from google import genai
            from google.genai import types
            from src.api_keys import get_secret
            key = get_secret("GEMINI_API_KEY")
            if not key:
                return None
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
                return None
            client = anthropic.Anthropic(api_key=key)
            kwargs = {
                "model": model,
                "max_tokens": 6000,
                "output_config": {"effort": "medium"},
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
            if getattr(msg, "stop_reason", None) in ("refusal", "max_tokens"):
                return None
            raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    except Exception as e:
        logger.debug(f"prompt_replay: generation failed ({surface}): {e}")
        return None

    return _parse(surface, raw)


def _parse(surface: str, raw: str):
    txt = (raw or "").strip()
    if surface == "home_interpret":
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
        a = _generate(surface, champ["body"], payload)
        b = _generate(surface, chall["body"], payload)
        if a is None or b is None:
            pairs.append({"snapshot_id": snap["id"], "champion": None, "challenger": None,
                          "champion_failed": a is None, "challenger_failed": b is None})
            continue
        ga = prompt_rules.grade(surface, payload, a)
        gb = prompt_rules.grade(surface, payload, b)
        pairs.append({
            "snapshot_id": snap["id"],
            "champion": ga, "challenger": gb,
            "champion_failed": False, "challenger_failed": False,
            "regressions": prompt_rules.regression_failures(gb.get("findings") or []),
        })

    return _summarise(surface, champ, chall, pairs)


def _summarise(surface: str, champ: dict, chall: dict, pairs: list[dict]) -> dict:
    scored = [p for p in pairs if p.get("champion") and p.get("challenger")]
    n = len(scored)
    fails_a = sum(1 for p in pairs if p.get("champion_failed"))
    fails_b = sum(1 for p in pairs if p.get("challenger_failed"))

    if n < _MIN_N:
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
    regressions = sorted({r for p in scored for r in (p.get("regressions") or [])})

    # THE GATE. Each condition is here because of a specific way this could go
    # wrong: significance without size, a win bought by reintroducing a known
    # defect, or a challenger that simply fails to produce parseable output more
    # often and is never penalised for it because failures are not scored.
    reasons = []
    if regressions:
        reasons.append(f"reintroduces known defects: {', '.join(regressions)}")
    if c_b["critical"] > c_a["critical"]:
        reasons.append(f"more critical findings ({c_b['critical']} vs {c_a['critical']})")
    if fails_b > fails_a:
        reasons.append(f"failed to generate more often ({fails_b} vs {fails_a})")
    if lo <= 0:
        reasons.append(f"95% CI on the paired difference includes zero [{lo:.3f}, {hi:.3f}]")
    if mean_diff < _MIN_MARGIN:
        reasons.append(f"improvement {mean_diff:+.3f} is under the {_MIN_MARGIN} margin")

    verdict = "reject" if reasons else "win"
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
        "generation_failures": {"champion": fails_a, "challenger": fails_b},
        "win_rate": round(sum(1 for d in diffs if d > 0) / n, 4),
        "tie_rate": round(sum(1 for d in diffs if d == 0) / n, 4),
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
