"""Read the prompt loop: what it measured, what it changed, and why.

Everything here is admin-gated. These endpoints return full prompt bodies, the
payloads behind them and the critic's unedited reasoning — that is the operating
detail of the platform, not product, and the tables themselves are RLS'd to
service_role for the same reason.

The one exception is `/track-record`, which is deliberately narrow: scores and
calibration with no prompt text and no payloads. It is the number a subscriber
could reasonably be shown about how the page's own commentary has done.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_current_user, require_admin

logger = logging.getLogger(__name__)
router = APIRouter()

SURFACES = ("market_driver", "home_interpret", "es_audit")


def _check_surface(surface: str) -> str:
    if surface not in SURFACES:
        raise HTTPException(400, f"unknown surface '{surface}'")
    return surface


@router.get("/summary")
async def summary(
    surface: str = Query("market_driver"),
    days: int = Query(30, ge=1, le=180),
    user: str = Depends(require_admin),
):
    """Full picture for one surface: scores, versions, experiments, calibration."""
    _check_surface(surface)
    from src.prompt_loop import summary as _summary
    return _summary(surface, days=days)


@router.get("/overview")
async def overview(
    days: int = Query(30, ge=1, le=180),
    user: str = Depends(require_admin),
):
    """All three surfaces at once, trimmed to what a dashboard header needs."""
    from src.prompt_loop import summary as _summary
    out = {}
    for s in SURFACES:
        try:
            full = _summary(s, days=days)
            out[s] = {
                "ok": full.get("ok", False),
                "champion": full.get("champion"),
                "n_graded": full.get("n_graded"),
                "mean_score": full.get("mean_score"),
                "finding_totals": full.get("finding_totals"),
                "findings_per_output": full.get("findings_per_output"),
                "score_series": full.get("score_series"),
                "calibration": full.get("calibration"),
                "challenger_version": (full.get("challenger") or {}).get("version"),
                "n_versions": len(full.get("versions") or []),
                "last_experiment": (full.get("experiments") or [{}])[0],
            }
        except Exception as e:
            logger.warning(f"prompt-loop overview failed for {s}: {e}")
            out[s] = {"ok": False, "error": str(e)}
    return {"ok": True, "surfaces": out, "window_days": days}


@router.get("/version")
async def version(
    surface: str = Query(...),
    version: int = Query(..., ge=0),
    user: str = Depends(require_admin),
):
    """One prompt version in full, with a line diff against its parent."""
    _check_surface(surface)
    from src.prompt_registry import get_version
    row = get_version(surface, version)
    if not row:
        raise HTTPException(404, f"{surface} v{version} not found")

    parent = None
    diff = None
    if row.get("parent_version") is not None:
        parent = get_version(surface, int(row["parent_version"]))
        if parent:
            import difflib
            diff = list(difflib.unified_diff(
                (parent.get("body") or "").splitlines(),
                (row.get("body") or "").splitlines(),
                fromfile=f"v{parent['version']}", tofile=f"v{row['version']}",
                lineterm="", n=2,
            ))[:400]
    return {"ok": True, "version": row, "parent_version": (parent or {}).get("version"),
            "diff": diff}


@router.get("/snapshots")
async def snapshots(
    surface: str = Query("market_driver"),
    split: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    days: int = Query(14, ge=1, le=120),
    user: str = Depends(require_admin),
):
    """Recent generations with their rule grades — the raw record."""
    _check_surface(surface)
    from src.prompt_loop import graded_snapshots
    rows = graded_snapshots(surface, split=split or "discovery", days=days, limit=limit)
    return {
        "ok": True,
        "count": len(rows),
        "data": [{
            "id": r["snapshot"]["id"],
            "created_at": r["snapshot"]["created_at"],
            "session_phase": r["snapshot"].get("session_phase"),
            "prompt_version": r["snapshot"].get("prompt_version"),
            "model": r["snapshot"].get("model"),
            "split": r["snapshot"].get("split"),
            "output": r["snapshot"].get("output"),
            "score": r["grade"].get("score"),
            "counts": r["grade"].get("counts"),
            "findings": r["grade"].get("findings"),
        } for r in rows],
    }


@router.get("/claims")
async def claims(
    surface: str = Query("market_driver"),
    days: int = Query(90, ge=1, le=365),
    status: str = Query("resolved"),
    limit: int = Query(100, ge=1, le=500),
    user: str = Depends(require_admin),
):
    """Individual falsifiable calls and how they settled."""
    _check_surface(surface)
    from datetime import datetime, timedelta, timezone
    from src.db import get_client
    db = get_client()
    if db is None:
        raise HTTPException(503, "database unavailable")
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        q = db.table("ai_claims").select("*").eq("surface", surface).gte("stated_at", since)
        if status != "all":
            q = q.eq("status", status)
        rows = q.order("stated_at", desc=True).limit(limit).execute().data or []
    except Exception as e:
        raise HTTPException(502, f"claim fetch failed: {e}")

    from src.prompt_claims import scoreboard
    return {"ok": True, "count": len(rows), "data": rows,
            "scoreboard": scoreboard(surface, days=days)}


@router.get("/track-record")
async def track_record(
    days: int = Query(90, ge=7, le=365),
    user: str = Depends(get_current_user),
):
    """How the home page's own commentary has scored. No prompts, no payloads.

    Both halves are reported because either alone misleads. The rule score says
    the notes were faithful to their data; it says nothing about whether they
    were right. The calibration block says whether the calls beat their own base
    rate; it is silent until enough calls have settled, and says so rather than
    filling in a number.
    """
    if user == "anonymous":
        raise HTTPException(401, "Sign in required")
    from src.prompt_loop import summary as _summary
    from src.prompt_claims import scoreboard

    out = {}
    for s in SURFACES:
        try:
            full = _summary(s, days=min(days, 180))
            out[s] = {
                "n_graded": full.get("n_graded"),
                "mean_score": full.get("mean_score"),
                "findings_per_output": full.get("findings_per_output"),
                "score_series": full.get("score_series"),
                "champion_version": (full.get("champion") or {}).get("version"),
            }
        except Exception as e:
            logger.debug(f"track-record failed for {s}: {e}")
            out[s] = {"error": "unavailable"}

    board = scoreboard("market_driver", days=days)
    return {
        "ok": True,
        "surfaces": out,
        "calibration": board,
        "caveat": (
            "Two different measurements. The rule score is how faithful each note was to the "
            "data it was given — grounded numbers, no contradictions with the rest of the page. "
            "It cannot tell you whether a read was right. That is what the calibration block "
            "measures, and only against the base rate of the same call, so a hit rate on its "
            "own means nothing here."
        ),
    }


@router.post("/seed")
async def seed(user: str = Depends(require_admin)):
    """Record the current git baselines as version 0. Idempotent."""
    from src.prompt_registry import seed_baselines
    return seed_baselines()


@router.post("/rollback")
async def rollback(
    surface: str = Query(...),
    user: str = Depends(require_admin),
):
    """Return a surface to its previously-served prompt. The escape hatch."""
    _check_surface(surface)
    from src.prompt_registry import rollback as _rollback
    res = _rollback(surface)
    if not res.get("ok"):
        raise HTTPException(400, res.get("error", "rollback failed"))
    return res


@router.post("/promote")
async def promote(
    surface: str = Query(...),
    version: int = Query(..., ge=0),
    user: str = Depends(require_admin),
):
    """Serve a specific version now, bypassing the gate.

    Deliberately available, deliberately manual. The automatic path requires two
    holdout wins; this is what you use when you have read a challenger yourself
    and want it live, or when you want the baseline back immediately.
    """
    _check_surface(surface)
    from src.prompt_registry import promote as _promote, get_version
    if not get_version(surface, version):
        raise HTTPException(404, f"{surface} v{version} not found")
    ok = _promote(surface, version, metrics={"promoted_by": "manual", "user": user})
    if not ok:
        raise HTTPException(502, "promotion failed")
    return {"ok": True, "surface": surface, "version": version}
