"""The loop itself: grade, critique, replay, promote — and the record of all four.

THE CYCLE, AND WHY IT IS SPLIT INTO FOUR JOBS. Grading is free and runs hourly,
so nothing waits on it. Critique costs two model calls and runs nightly against
what grading found. Replay costs roughly fifty and runs after critique, once a
challenger exists. Promotion costs nothing and is a pure function of the
experiment record. Splitting them means a failure in the expensive stage never
blocks the cheap ones, and the measurement half of the system keeps running even
if the improvement half is switched off.

PROMOTION NEEDS TWO WINS ON DIFFERENT DAYS. One holdout draw that clears the gate
is one draw. The second experiment re-samples the holdout with a different seed
on a later day, which means fresh live snapshots have entered the pool. Winning
twice is not proof, and this file does not pretend otherwise — but a single
lucky draw cannot promote, and that is the failure mode worth spending an extra
day on.

WHAT GETS OPTIMISED IS NOT WHAT GETS TRUSTED. The gate is built on the
deterministic rules only. Calibration — whether the calls were right — is
measured forward and reported, but never enters the promotion decision, because
a challenger cannot make a forecast about a week that has already happened.
Anyone reading a rising rule score should read the calibration number beside it
before believing the page got smarter.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

SURFACES = ("market_driver", "home_interpret", "es_audit", "news_digest")

# RETIRED 2026-08-29 — counting verdicts instead of pairs. "Two rejects and a
# challenger is not coming back" sounded prudent and was the single most costly
# rule in the loop: it destroyed ~72% of challengers that never lost a pair
# (because a nightly test with 3 discordant pairs cannot return a win at all),
# while giving a worthless challenger two independent shots at a 5% fluke. Worse
# on both error rates at once, which is the signature of a rule that was never
# derived from anything. Promotion and retirement are now decided by an
# accumulating test martingale over PAIRS — see src/prompt_evidence.


def _db():
    from src.db import get_client
    return get_client()


def discover_surfaces(days: int = 30) -> list[str]:
    """Core surfaces plus every `interpret:<page>` seen recently.

    The per-page interpretations are not enumerated anywhere in this process —
    the page list lives in the API's PAGE_CONTEXT, which the worker has no
    business importing — so they are read back off the record instead. A page
    that has not been interpreted in the window simply has nothing to grade.
    """
    from src.prompt_snapshots import paged
    db = _db()
    found = set(SURFACES)
    if db is None:
        return sorted(found)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        rows = paged(lambda: db.table("ai_snapshots").select("surface")
                     .gte("created_at", since).order("created_at", desc=True),
                     page=1000, max_pages=4)
        found |= {r["surface"] for r in rows if r.get("surface")}
    except Exception as e:
        logger.debug(f"prompt_loop: surface discovery failed: {e}")
    return sorted(found)


# ── stage 1: grade ────────────────────────────────────────────────

def grade_pending(surface: str | None = None, days: int = 30,
                  page: int = 400, max_pages: int = 12,
                  regrade: bool = False) -> dict:
    """Apply the rule set to every snapshot that has not been graded yet.

    PAGES THE WHOLE WINDOW, oldest first. The obvious version — take the newest
    N snapshots and grade whatever is ungraded among them — strands rows
    permanently: miss a few runs, and by the time the job comes back the newest
    N are all graded already, so it writes nothing and the gap behind them is
    never revisited. Nothing would look broken, and the sample every downstream
    number is computed over would just be quietly missing a week.

    `regrade=True` re-scores rows that already carry a grade. A rule fix would
    otherwise leave the record frozen under the rule it fixed: on 2026-08-28 a
    false-positive `invented_ticker` was corrected, and until the stored grades
    were rewritten the critic went on reading 17 criticals that no longer
    existed, arguing against a defect the prompt never had. A grade is a cache
    of a pure function of (rules, payload, output) — when the rules change the
    cache is stale, not historical.
    """
    from src import prompt_rules
    from src.prompt_snapshots import paged
    db = _db()
    if db is None:
        return {"ok": False, "error": "no database"}

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    out: dict = {}
    for surf in ([surface] if surface else discover_surfaces(days)):
        rows = paged(lambda surf=surf: db.table("ai_snapshots").select("*")
                     .eq("surface", surf).eq("is_replay", False)
                     .gte("created_at", since).order("created_at"),
                     page=page, max_pages=max_pages)
        if not rows:
            out[surf] = {"graded": 0, "skipped": 0, "scanned": 0}
            continue

        already: set[int] = set()
        ids = [r["id"] for r in rows]
        if not regrade:
            for i in range(0, len(ids), 200):
                try:
                    done = (db.table("ai_grades").select("snapshot_id")
                            .in_("snapshot_id", ids[i:i + 200]).eq("grader", "rules")
                            .execute().data or [])
                    already |= {r["snapshot_id"] for r in done}
                except Exception as e:
                    logger.warning(f"prompt_loop: grade lookup failed for {surf}: {e}")

        payloads = []
        for r in rows:
            if r["id"] in already:
                continue
            g = prompt_rules.grade(surf, r.get("payload") or {}, r.get("output"))
            if g.get("score") is None and g.get("error"):
                continue
            payloads.append({
                "snapshot_id": r["id"],
                "surface": surf,
                "grader": "rules",
                "score": g["score"],
                "findings": g["findings"],
                "counts": g["counts"],
            })

        wrote = 0
        for i in range(0, len(payloads), 50):
            try:
                db.table("ai_grades").upsert(payloads[i:i + 50],
                                             on_conflict="snapshot_id,grader").execute()
                wrote += len(payloads[i:i + 50])
            except Exception as e:
                logger.warning(f"prompt_loop: grade write failed for {surf}: {e}")
        out[surf] = {"graded": wrote, "skipped": len(already), "scanned": len(rows)}

    return {"ok": True, "surfaces": out}


def graded_snapshots(surface: str, split: str = "discovery", days: int = 30,
                     limit: int = 120) -> list[dict]:
    """Snapshots joined to their rule grades. The critic's reading material."""
    from src import prompt_snapshots
    db = _db()
    if db is None:
        return []
    rows = prompt_snapshots.fetch(surface, split=split, limit=limit, days=days)
    if not rows:
        return []
    try:
        grades = db.table("ai_grades").select("snapshot_id,score,findings,counts") \
            .in_("snapshot_id", [r["id"] for r in rows]).eq("grader", "rules") \
            .execute().data or []
    except Exception:
        return []
    by_id = {g["snapshot_id"]: g for g in grades}
    return [{"snapshot": r, "grade": by_id[r["id"]]} for r in rows if r["id"] in by_id]


# ── stage 2: critique + propose ───────────────────────────────────

def critique_cycle(surface: str, min_samples: int = 10) -> dict:
    """Adversarial pass over the discovery set; records a challenger if warranted."""
    from src import prompt_registry, prompt_critic, prompt_claims

    if _open_challenger(surface):
        return {"ok": True, "skipped": "a challenger is already under evaluation"}

    graded = graded_snapshots(surface, split="discovery")
    if len(graded) < min_samples:
        return {"ok": True, "skipped": f"only {len(graded)} graded discovery snapshots, need {min_samples}"}

    # NO HEADROOM, NO CRITIQUE. A surface the rules already score at the ceiling
    # cannot produce a challenger that demonstrates an improvement — es_audit
    # sits at 0.997-0.999 and its one challenger was rejected on an exact tie
    # (0.9983 vs 0.9983, 100% tie rate) after spending two Opus calls to write
    # it and twenty-four generations to score it. Skipping is self-correcting:
    # if quality ever degrades the mean falls and critique resumes on its own.
    #
    # BUT THE MEAN ALONE WAS THE WRONG GATE (2026-08-29). news_digest cleared it
    # by 0.000094 while breaking its own 70-word cap in 1 output in 10, so the
    # critic was barred from the surface with a live systematic defect. Headroom
    # now needs the mean AND the strict pass rate — see prompt_health.has_headroom.
    from src.prompt_health import has_headroom
    room, why = has_headroom(graded)
    if not room:
        return {"ok": True, "skipped": why}

    champ = prompt_registry.champion(surface) or {}
    body = champ.get("body") or prompt_registry.baseline(surface)
    version = int(champ.get("version") or 0)

    board = None
    if surface == "market_driver":
        try:
            board = prompt_claims.scoreboard(surface, days=90)
        except Exception as e:
            logger.debug(f"prompt_loop: scoreboard failed: {e}")

    crit = prompt_critic.critique(surface, graded, board, body)
    if not crit.get("ok"):
        return {"ok": False, "stage": "critique", **crit}

    findings = crit.get("findings") or []
    if not findings:
        return {"ok": True, "surface": surface, "findings": 0,
                "verdict": crit.get("verdict", ""),
                "note": "critic found no prompt defects — nothing proposed"}

    prop = prompt_critic.propose(surface, body, crit)
    if not prop.get("ok"):
        return {"ok": False, "stage": "propose", "findings": len(findings), **prop}

    row = prompt_registry.record_challenger(
        surface, prop["body"], parent=version,
        rationale=prop.get("rationale", ""),
        diff_summary=f"{len(findings)} findings from {len(graded)} graded outputs",
    )
    return {
        "ok": True,
        "surface": surface,
        "findings": len(findings),
        "verdict": crit.get("verdict", ""),
        "challenger_version": (row or {}).get("version"),
        "champion_version": version,
    }


def _open_challenger(surface: str) -> dict | None:
    db = _db()
    if db is None:
        return None
    try:
        rows = db.table("prompt_versions").select("*") \
            .eq("surface", surface).eq("status", "challenger") \
            .order("version", desc=True).limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception:
        return None


# ── stage 3: replay ───────────────────────────────────────────────

def evaluate_cycle(surface: str, n: int = 24) -> dict:
    """Score the open challenger against the champion on holdout payloads."""
    from src import prompt_registry, prompt_replay, prompt_evidence

    chall = _open_challenger(surface)
    if not chall:
        return {"ok": True, "skipped": "no challenger awaiting evaluation"}

    champ = prompt_registry.champion(surface)
    champ_version = int((champ or {}).get("version") or 0)
    chall_version = int(chall["version"])

    prior = _experiments(surface, chall_version)
    # A fresh seed per attempt, so the second draw is genuinely a second draw
    # and not the same 24 payloads re-run.
    seed = 7 + 13 * len(prior)

    result = prompt_replay.run(surface, champ_version, chall_version, n=n, seed=seed)
    if not result.get("ok"):
        return {"ok": False, "stage": "replay", **result}

    experiment_id = _record_experiment(surface, champ_version, chall_version, result)

    # ── the accumulating gate ──────────────────────────────────────
    # We used to count VERDICTS: two "reject" verdicts retired a challenger for
    # good. That destroyed ~72% of challengers that never lost a single pair,
    # because a nightly test with 3 discordant pairs cannot return "win" at all
    # (exact sign-test floor 0.125). Now we count PAIRS, across every night this
    # challenger has run, and let a test martingale decide. Looking every night
    # is free under Ville's inequality -- that is the whole point of the change.
    # INVALIDATED RUNS DO NOT ACCUMULATE. Experiments 5, 6 and 8 were graded
    # under the `invented_ticker` rule that had 0/18 precision in production and
    # were explicitly voided on 2026-08-28. Banking their pairs would import the
    # very defect the regrade removed — an accumulating gate makes stale evidence
    # more dangerous, not less, because it never ages out.
    usable = [e for e in prior if e.get("verdict") != "invalidated"]
    if len(usable) < len(prior):
        logger.info(f"prompt_loop: {surface} v{chall_version} — excluded "
                    f"{len(prior) - len(usable)} invalidated run(s) from the record")
    runs = [_run_counts(e.get("metrics") or {}) for e in usable]
    runs.append(_run_counts(result))
    e_max, cum = 1.0, {"wins": 0, "losses": 0, "n": 0, "sum_d": 0.0, "sum_d2": 0.0}
    for r in runs:
        for k in cum:
            cum[k] += r[k]
        # Ville bounds the RUNNING MAXIMUM, so promotion reads e_max while
        # retirement reads the current E -- a challenger that dipped must be
        # able to recover, a challenger that crossed must stay crossed.
        e_max = max(e_max, prompt_evidence.evalue(cum["wins"], cum["losses"]))

    stat_verdict, why = prompt_evidence.verdict(
        cum["wins"], cum["losses"], e_max,
        n=cum["n"], sum_d=cum["sum_d"], sum_d2=cum["sum_d2"])

    # A substantive fault from THIS run still vetoes outright: the regression
    # gate, extra criticals, or failing to generate are facts about the prompt,
    # not statistics that need a bigger sample.
    if result.get("verdict") == "reject":
        stat_verdict = "reject"
        why = "; ".join(result.get("reasons") or []) or why

    wins, losses = cum["wins"], cum["losses"]
    logger.info(f"prompt_loop: {surface} v{chall_version} — {stat_verdict}: {why}")

    decision = "hold"
    if stat_verdict == "win":
        if prompt_registry.promote(surface, chall_version, metrics=result):
            decision = "promoted"
            # Stamp the experiment that actually triggered it, so the dashboard's
            # `promoted` column means something. Without this every row reads
            # false and the version history is the only place a promotion shows.
            if experiment_id is not None:
                try:
                    _db().table("prompt_experiments").update({"promoted": True})                         .eq("id", experiment_id).execute()
                except Exception as e:
                    logger.debug(f"prompt_loop: promotion stamp failed: {e}")
    elif stat_verdict in ("reject", "equivalent", "trivial", "futile"):
        # RETIREMENT NOW REQUIRES EVIDENCE, not two non-significant nights.
        # "reject" means the martingale fell to 1/20 against the challenger;
        # "equivalent" means TOST put the difference inside the ROPE, so there
        # is nothing to gain; "futile" means the budget is spent. Each of those
        # is a finding. "insufficient_data" and "collecting" are NOT, and they
        # no longer retire anything -- that conflation is what killed v3.
        prompt_registry.reject(surface, chall_version, metrics=result, note=why)
        decision = "retired"
    else:
        need = prompt_evidence.pairs_to_promote(cum["losses"])
        logger.info(f"prompt_loop: {surface} v{chall_version} stays open — "
                    f"{cum['wins']}W-{cum['losses']}L over {len(runs)} run(s), "
                    f"~{max(0, need - cum['wins'])} more clean wins to cross")

    return {"ok": True, "surface": surface, "decision": decision,
            "verdict": stat_verdict, "why": why,
            "cumulative": {**cum, "e_value": round(e_max, 4),
                           "anytime_p": round(prompt_evidence.anytime_p(e_max), 4),
                           "runs": len(runs)},
            "wins": wins, "losses": losses,
            **{k: v for k, v in result.items() if k != "verdict"}}


def _run_counts(metrics: dict) -> dict:
    """Per-run paired counts from a replay result or a stored experiment.

    Rows written before 2026-08-29 carry only rates, so reconstruct integers
    from win_rate/tie_rate/n rather than dropping that history on the floor —
    the accumulating gate exists precisely so earlier evidence still counts.
    """
    n = int(metrics.get("n") or 0)
    empty = {"wins": 0, "losses": 0, "n": 0, "sum_d": 0.0, "sum_d2": 0.0}
    if not n:
        return empty
    # A RUN THAT SCORED NO PAIRS CONTRIBUTES NOTHING — it must not contribute
    # LOSSES. `_summarise` returns early when n < _MIN_N with only `n` set and
    # no wins/win_rate, so the reconstruction below would compute
    # losses = n - 0 - 0 = n and bank a vendor outage as a run of pure defeats.
    # That is the two-strikes failure reintroduced through the accumulator: the
    # thinner the sample, the harder the challenger gets punished for it.
    if metrics.get("wins") is None and metrics.get("win_rate") is None:
        return empty
    if metrics.get("wins") is not None:
        wins = int(metrics["wins"])
        losses = int(metrics.get("losses") or 0)
    else:
        wins = round(float(metrics.get("win_rate") or 0) * n)
        ties = round(float(metrics.get("tie_rate") or 0) * n)
        losses = max(0, n - wins - ties)
    sum_d = metrics.get("sum_d")
    if sum_d is None:
        # Old rows kept only the mean. n*mean is the sum exactly.
        sum_d = float(metrics.get("mean_diff") or 0) * n
    return {"wins": wins, "losses": losses, "n": n,
            "sum_d": float(sum_d), "sum_d2": float(metrics.get("sum_d2") or 0.0)}


def _experiments(surface: str, challenger_version: int) -> list[dict]:
    db = _db()
    if db is None:
        return []
    try:
        return db.table("prompt_experiments").select("*") \
            .eq("surface", surface).eq("challenger_version", challenger_version) \
            .order("created_at").limit(20).execute().data or []
    except Exception:
        return []


def _record_experiment(surface: str, champ_v: int, chall_v: int, result: dict) -> int | None:
    db = _db()
    if db is None:
        return None
    verdict = "promote" if result.get("verdict") == "win" else result.get("verdict", "inconclusive")
    try:
        res = db.table("prompt_experiments").insert({
            "surface": surface,
            "champion_version": champ_v,
            "challenger_version": chall_v,
            "n_holdout": int(result.get("n") or 0),
            "metrics": result,
            "regression_pass": not (result.get("regressions") or []),
            "verdict": verdict,
            "promoted": False,
            "notes": "; ".join(result.get("reasons") or []) or None,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
        rows = res.data or []
        return int(rows[0]["id"]) if rows else None
    except Exception as e:
        logger.warning(f"prompt_loop: experiment write failed: {e}")
        return None


# ── reporting ─────────────────────────────────────────────────────

def summary(surface: str, days: int = 30) -> dict:
    """Everything the dashboard needs for one surface, in one round trip."""
    from src import prompt_registry, prompt_claims
    db = _db()
    if db is None:
        return {"ok": False, "error": "no database"}

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    champ = prompt_registry.champion(surface) or {}

    # Paged: a 30-day window on a busy surface crosses PostgREST's silent
    # 1000-row ceiling, and a truncated read here would move the mean score
    # without moving anything that looks like an error.
    from src.prompt_snapshots import paged
    grades = paged(lambda: db.table("ai_grades")
                   .select("score,counts,created_at,snapshot_id")
                   .eq("surface", surface).eq("grader", "rules")
                   .gte("created_at", since).order("created_at"))

    try:
        versions = db.table("prompt_versions") \
            .select("version,status,origin,rationale,diff_summary,created_at,promoted_at,retired_at,body_hash") \
            .eq("surface", surface).order("version", desc=True).limit(30).execute().data or []
    except Exception:
        versions = []

    try:
        experiments = db.table("prompt_experiments").select("*") \
            .eq("surface", surface).order("created_at", desc=True).limit(20).execute().data or []
    except Exception:
        experiments = []

    scores = [float(g["score"]) for g in grades if g.get("score") is not None]
    totals = {"critical": 0, "major": 0, "minor": 0}
    for g in grades:
        for k, v in (g.get("counts") or {}).items():
            totals[k] = totals.get(k, 0) + int(v or 0)

    board = prompt_claims.scoreboard(surface, days=90) if surface == "market_driver" else None

    return {
        "ok": True,
        "surface": surface,
        "champion": {"version": champ.get("version", 0),
                     "promoted_at": champ.get("promoted_at"),
                     "origin": champ.get("origin"),
                     "rationale": champ.get("rationale"),
                     "chars": len(champ.get("body") or prompt_registry.baseline(surface))},
        "window_days": days,
        "n_graded": len(scores),
        "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
        "finding_totals": totals,
        "findings_per_output": {
            k: round(v / len(scores), 3) for k, v in totals.items()
        } if scores else {},
        "score_series": _series(grades),
        "versions": versions,
        "experiments": experiments,
        "calibration": board,
        "challenger": _open_challenger(surface),
    }


def _series(grades: list[dict]) -> list[dict]:
    """Daily mean rule score. The curve the loop is supposed to bend."""
    buckets: dict[str, list[float]] = {}
    for g in grades:
        if g.get("score") is None:
            continue
        day = str(g.get("created_at") or "")[:10]
        if day:
            buckets.setdefault(day, []).append(float(g["score"]))
    return [{"date": d, "mean_score": round(sum(v) / len(v), 4), "n": len(v)}
            for d, v in sorted(buckets.items())]


def run_all(stage: str = "grade", n: int = 24) -> dict:
    """Entry point for the worker. One stage across every surface."""
    out: dict = {}
    if stage == "grade":
        out["grade"] = grade_pending()
        try:
            from src import prompt_claims
            out["claims"] = prompt_claims.resolve_due()
        except Exception as e:
            out["claims"] = {"ok": False, "error": str(e)}
        return out

    for s in SURFACES:
        try:
            if stage == "critique":
                out[s] = critique_cycle(s)
            elif stage == "evaluate":
                out[s] = evaluate_cycle(s, n=n)
            else:
                out[s] = {"ok": False, "error": f"unknown stage {stage}"}
        except Exception as e:
            logger.warning(f"prompt_loop: {stage} failed for {s}: {e}")
            out[s] = {"ok": False, "error": str(e)}
    return out
