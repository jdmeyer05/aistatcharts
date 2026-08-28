"""Watching the watcher: is the rule set still telling the truth, and is the loop still running?

WHY THIS MODULE EXISTS. The deterministic rules are the loop's ONLY quality
signal — the critic reads their findings, and the promotion gate is scored on
them. Nothing checked the rules themselves. `invented_ticker` ran at ZERO
precision for nine days: 18 findings, every one of them false, scoring the home
page at 0.139 when its real score was 0.948 and vetoing a challenger that had
won its replay. It was caught by a person reading the evidence column, and
nothing in the system would ever have caught it.

THE SIGNAL THAT WOULD HAVE CAUGHT IT ON DAY TWO, and it is deterministic:
REAL DEFECTS ARE VARIED; A BROKEN RULE REPEATS ITSELF. Genuinely invented
tickers would be scattered — a different hallucinated symbol each time. What
`invented_ticker` actually produced was TGA seven times, VAH five times, the
same handful of desk abbreviations over and over. So low evidence diversity
relative to firing count is the tell, and it needs no model to evaluate.

The second half of the module answers a different question that also went
unnoticed for four days: is anything still arriving? A stage that declines for
a good reason and a stage that has silently stopped produce the same quiet log.
`readiness()` states what each surface needs before its next stage can run, so
"blocked" is visible instead of inferred.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# A rule needs to have fired a few times before its spread means anything.
_MIN_FIRINGS = 6
# Below this share of distinct evidence values, the rule is repeating itself.
_MIN_DIVERSITY = 0.60
# Or one single value dominates.
_MAX_TOP_SHARE = 0.30
# A regression-suite rule that fires on more than this share of graded outputs
# is either a live crisis or a broken rule. Both want a human.
_MAX_CRITICAL_RATE = 0.15

_CRITIQUE_MIN = 10        # graded discovery rows, per prompt_loop.critique_cycle
_REPLAY_MIN = 8           # holdout snapshots, per prompt_replay._MIN_N
# A surface scoring this well has no headroom the rules can measure, so a
# challenger cannot demonstrate an improvement and the critique spend is wasted.
_CEILING = 0.995


def _db():
    from src.db import get_client
    return get_client()


# ── half one: is the rule set honest? ─────────────────────────────
def flags_for(rule: str, evidence, n: int, n_graded: int) -> list[str]:
    """Why this rule looks broken, if it does. Pure — no database.

    Separated out precisely so it can be tested against the real 2026-08-19
    firing pattern, which is the only example of a zero-precision rule this
    system has actually produced.
    """
    from src import prompt_rules
    with_ev = sum(evidence.values())
    distinct = len(evidence)
    why = []
    if with_ev >= _MIN_FIRINGS:
        diversity = distinct / with_ev
        top, top_n = evidence.most_common(1)[0]
        top_share = top_n / with_ev
        if diversity < _MIN_DIVERSITY:
            why.append(f"only {distinct} distinct values across {with_ev} firings")
        if top_share >= _MAX_TOP_SHARE:
            why.append(f"'{top}' alone is {100*top_share:.0f}% of firings")
    if n_graded and rule in prompt_rules.REGRESSION_RULES and n / n_graded > _MAX_CRITICAL_RATE:
        why.append(f"regression-suite rule firing on {100*n/n_graded:.0f}% of outputs")
    return why


def audit_rules(days: int = 30) -> dict:
    """Per-rule firing statistics, flagging rules that repeat themselves."""
    from src import prompt_rules
    db = _db()
    if db is None:
        return {"ok": False, "error": "no database"}

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        from src.prompt_snapshots import paged
        rows = paged(lambda: db.table("ai_grades")
                     .select("surface,findings,created_at")
                     .gte("created_at", since).order("created_at"))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not rows:
        return {"ok": True, "n_graded": 0, "rules": [], "flags": []}

    per_rule: dict[str, dict] = {}
    for r in rows:
        for f in (r.get("findings") or []):
            rule = f.get("rule") or "?"
            d = per_rule.setdefault(rule, {
                "rule": rule, "n": 0, "severity": f.get("severity", "?"),
                "evidence": Counter(), "surfaces": Counter()})
            d["n"] += 1
            d["surfaces"][r.get("surface", "?")] += 1
            ev = (f.get("evidence") or "").strip()
            if ev:
                d["evidence"][ev] += 1

    n_graded = len(rows)
    out, flags = [], []
    for d in sorted(per_rule.values(), key=lambda x: -x["n"]):
        ev, n = d["evidence"], d["n"]
        distinct = len(ev)
        with_ev = sum(ev.values())
        top, top_n = (ev.most_common(1)[0] if ev else ("", 0))
        diversity = distinct / with_ev if with_ev else None
        top_share = top_n / with_ev if with_ev else None
        rec = {
            "rule": d["rule"], "severity": d["severity"], "n": n,
            "rate": round(n / n_graded, 4),
            "distinct_evidence": distinct,
            "diversity": round(diversity, 3) if diversity is not None else None,
            "top_evidence": top, "top_share": round(top_share, 3) if top_share is not None else None,
            "surfaces": dict(d["surfaces"]),
            "sample": [e for e, _ in ev.most_common(6)],
        }
        why = flags_for(d["rule"], ev, n, n_graded)
        if why:
            rec["flag"] = "; ".join(why)
            flags.append(rec)
        out.append(rec)

    return {"ok": True, "n_graded": n_graded, "days": days, "rules": out, "flags": flags}


# ── half two: is anything still arriving? ─────────────────────────
def readiness(days: int = 30) -> dict:
    """What each surface needs before its next stage can run."""
    from src import prompt_loop
    db = _db()
    if db is None:
        return {"ok": False, "error": "no database"}

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out, problems = [], []
    for surface in prompt_loop.discover_surfaces(days):
        try:
            snaps = (db.table("ai_snapshots")
                     .select("id,split,created_at").eq("surface", surface)
                     .eq("is_replay", False).gte("created_at", since)
                     .order("created_at", desc=True).limit(1000).execute().data or [])
        except Exception as e:
            problems.append(f"{surface}: snapshot read failed ({e})")
            continue
        if not snaps:
            problems.append(f"{surface}: no snapshots in {days}d")
            continue

        disc = [s for s in snaps if s.get("split") == "discovery"]
        hold = [s for s in snaps if s.get("split") == "holdout"]
        graded = prompt_loop.graded_snapshots(surface, split="discovery", days=days)
        scores = [g["grade"].get("score") for g in graded
                  if g["grade"].get("score") is not None]
        mean = sum(scores) / len(scores) if scores else None

        last = max(s["created_at"] for s in snaps)
        age_h = (datetime.now(timezone.utc)
                 - datetime.fromisoformat(last.replace("Z", "+00:00"))).total_seconds() / 3600

        blocks = []
        if len(graded) < _CRITIQUE_MIN:
            blocks.append(f"critique needs {_CRITIQUE_MIN - len(graded)} more graded discovery rows")
        if len(hold) < _REPLAY_MIN:
            blocks.append(f"replay needs {_REPLAY_MIN - len(hold)} more holdout rows")
        if mean is not None and mean >= _CEILING:
            blocks.append(f"no measurable headroom (mean score {mean:.4f}) — "
                          "a challenger cannot demonstrate an improvement")
        if len(snaps) - len(graded) - len(hold) > 20:
            blocks.append("large ungraded backlog")

        rec = {"surface": surface, "snapshots": len(snaps), "discovery": len(disc),
               "holdout": len(hold), "graded_discovery": len(graded),
               "mean_score": round(mean, 4) if mean is not None else None,
               "hours_since_last": round(age_h, 1), "blocked_by": blocks}
        if age_h > 72:
            problems.append(f"{surface}: no snapshot for {age_h:.0f}h")
        out.append(rec)

    return {"ok": True, "surfaces": out, "problems": problems}


def report(days: int = 30) -> dict:
    """Both halves, logged loudly enough that a broken loop cannot look calm."""
    a, r = audit_rules(days), readiness(days)

    if a.get("ok"):
        logger.info(f"prompt_health: audited {a['n_graded']} graded outputs, "
                    f"{len(a['rules'])} distinct rules fired")
        for f in a["flags"]:
            logger.error(
                f"prompt_health: RULE UNDER SUSPICION '{f['rule']}' ({f['severity']}) — "
                f"{f['flag']}. Examples: {', '.join(f['sample'][:5])}. "
                "Read the evidence before trusting any score that depends on it.")
        if not a["flags"]:
            logger.info("prompt_health: no rule shows repeat-evidence behaviour")
    else:
        logger.error(f"prompt_health: rule audit failed — {a.get('error')}")

    if r.get("ok"):
        for s in r["surfaces"]:
            msg = (f"prompt_health: {s['surface']} — {s['snapshots']} snaps "
                   f"({s['graded_discovery']} graded disc / {s['holdout']} holdout), "
                   f"mean {s['mean_score']}, last {s['hours_since_last']}h ago")
            logger.info(msg + (f" | BLOCKED: {'; '.join(s['blocked_by'])}"
                               if s["blocked_by"] else " | ready"))
        for p in r["problems"]:
            logger.error(f"prompt_health: {p}")
    else:
        logger.error(f"prompt_health: readiness failed — {r.get('error')}")

    return {"ok": a.get("ok", False) and r.get("ok", False), "audit": a, "readiness": r}
