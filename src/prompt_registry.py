"""Which prompt text is actually being served, right now, for a given surface.

THE CONTRACT. `active(surface)` returns `(body, version)` and NEVER raises and
NEVER returns empty. If Supabase is down, if the table is missing, if the kill
switch is set, if a promotion wrote something malformed — the caller gets the
git baseline and version 0. A self-editing prompt system that can take the page
down when its database hiccups is not worth having.

THE KILL SWITCH. `PROMPT_LOOP_DISABLED=1` in the environment pins every surface
to its baseline immediately, with no deploy and no database write. That is the
first thing to reach for if a promoted prompt starts producing something wrong.
`PROMPT_LOOP_PIN=market_driver:3,es_audit:0` pins named surfaces to named
versions for the same reason, at finer grain.

CACHING. The champion changes at most once a day and is read on every home-page
generation, so it is held in-process for 5 minutes. A promotion therefore takes
up to five minutes to reach every Cloud Run instance; `invalidate()` clears it
in the process that performed the promotion.
"""

from __future__ import annotations

import hashlib
import logging
import os
from time import time as _now

logger = logging.getLogger(__name__)

SURFACES = ("market_driver", "home_interpret", "es_audit")

_TTL_S = 300
_CACHE: dict[str, tuple[float, str, int]] = {}


def _db():
    try:
        from src.db import get_client
        return get_client()
    except Exception:
        return None


def body_hash(body: str) -> str:
    return hashlib.sha256((body or "").encode("utf-8")).hexdigest()[:16]


def baseline(surface: str) -> str:
    from src.prompt_defaults import BASELINES
    return BASELINES.get(surface, "")


def _pinned_version(surface: str) -> int | None:
    """Parse PROMPT_LOOP_PIN. Malformed entries are ignored, not fatal."""
    raw = os.environ.get("PROMPT_LOOP_PIN", "")
    if not raw:
        return None
    for part in raw.split(","):
        if ":" not in part:
            continue
        name, _, ver = part.partition(":")
        if name.strip() != surface:
            continue
        try:
            return int(ver.strip())
        except ValueError:
            return None
    return None


def active(surface: str) -> tuple[str, int]:
    """The prompt body to send, and the version number to record with it."""
    base = baseline(surface)

    if os.environ.get("PROMPT_LOOP_DISABLED", "").strip() in ("1", "true", "TRUE"):
        return base, 0

    pin = _pinned_version(surface)
    if pin == 0:
        return base, 0

    hit = _CACHE.get(surface)
    if hit and (_now() - hit[0]) < _TTL_S and (pin is None or pin == hit[2]):
        return hit[1], hit[2]

    db = _db()
    if db is None:
        return base, 0

    try:
        q = db.table("prompt_versions").select("body,version").eq("surface", surface)
        if pin is not None:
            q = q.eq("version", pin)
        else:
            q = q.eq("status", "champion")
        rows = q.order("version", desc=True).limit(1).execute().data or []
    except Exception as e:
        logger.debug(f"prompt_registry: lookup failed for {surface}: {e}")
        return base, 0

    if not rows:
        return base, 0

    body = (rows[0].get("body") or "").strip()
    version = int(rows[0].get("version") or 0)

    # A champion that lost most of its text is a corrupted write, not a bold
    # edit. Anything under half the baseline's length is refused on sight —
    # the loop's own gates should catch this first, but this is the layer that
    # runs on the request path, and it is the one that must not serve garbage.
    if len(body) < max(400, len(base) // 2):
        logger.warning(
            f"prompt_registry: {surface} v{version} is {len(body)} chars vs "
            f"baseline {len(base)} — serving baseline instead"
        )
        return base, 0

    _CACHE[surface] = (_now(), body, version)
    return body, version


def invalidate(surface: str | None = None) -> None:
    if surface is None:
        _CACHE.clear()
    else:
        _CACHE.pop(surface, None)


# ── Write side: used by the seeder, the critic and the promoter ──────────

def seed_baselines(promote_edits: bool = True) -> dict:
    """Record version 0 for every surface from git, if not already present.

    Idempotent, and re-run safely after a baseline edit: if the git text has
    changed, the old version is left in place and the new text is appended as
    the next version with origin `baseline`, so history stays truthful about
    what was served when.

    A GIT EDIT IS PROMOTED IMMEDIATELY, unlike a machine proposal. The two-win
    holdout gate exists to stop the loop from promoting its own opinion of its
    own work; it is not a review process for the operator. Someone editing
    `prompt_defaults.py` and deploying it has already decided. Pass
    `promote_edits=False` to stage a baseline edit as a challenger instead and
    make it earn its way in like any other.
    """
    db = _db()
    if db is None:
        return {"ok": False, "error": "no database"}

    out: dict[str, str] = {}
    for surface in SURFACES:
        base = baseline(surface)
        if not base:
            out[surface] = "missing baseline"
            continue
        try:
            rows = db.table("prompt_versions").select("version,body_hash,status") \
                .eq("surface", surface).order("version", desc=True).limit(1).execute().data or []
        except Exception as e:
            out[surface] = f"read failed: {e}"
            continue

        h = body_hash(base)
        if not rows:
            _insert(db, surface, 0, base, status="champion", origin="baseline",
                    rationale="Initial baseline, lifted from git.")
            out[surface] = "seeded v0"
            continue

        if any(r.get("body_hash") == h for r in rows):
            out[surface] = "current"
            continue

        # Git baseline drifted from everything on record — append it.
        try:
            existing = db.table("prompt_versions").select("body_hash") \
                .eq("surface", surface).eq("body_hash", h).limit(1).execute().data or []
        except Exception:
            existing = []
        if existing:
            out[surface] = "current"
            continue

        nxt = int(rows[0].get("version") or 0) + 1
        if promote_edits:
            prior = champion(surface)
            _insert(db, surface, nxt, base, status="challenger", origin="baseline",
                    rationale="Baseline edited in git after the loop started.")
            promote(surface, nxt)
            if prior and int(prior.get("version") or 0) != nxt:
                out[surface] = f"promoted v{nxt} from git (was v{prior.get('version')})"
            else:
                out[surface] = f"promoted v{nxt} from git"
        else:
            _insert(db, surface, nxt, base, status="challenger", origin="baseline",
                    rationale="Baseline edited in git; staged, not promoted.")
            out[surface] = f"staged v{nxt} from git as a challenger"

    return {"ok": True, "surfaces": out}


def _insert(db, surface: str, version: int, body: str, *, status: str,
            origin: str, rationale: str, parent: int | None = None,
            diff_summary: str = "") -> dict | None:
    from datetime import datetime, timezone
    row = {
        "surface": surface,
        "version": version,
        "body": body,
        "body_hash": body_hash(body),
        "parent_version": parent,
        "status": status,
        "origin": origin,
        "rationale": rationale[:4000] if rationale else None,
        "diff_summary": diff_summary[:2000] if diff_summary else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if status == "champion":
        row["promoted_at"] = row["created_at"]
    try:
        res = db.table("prompt_versions").insert(row).execute()
        return (res.data or [None])[0]
    except Exception as e:
        logger.warning(f"prompt_registry: insert {surface} v{version} failed: {e}")
        return None


def next_version(surface: str) -> int:
    db = _db()
    if db is None:
        return 1
    try:
        rows = db.table("prompt_versions").select("version") \
            .eq("surface", surface).order("version", desc=True).limit(1).execute().data or []
        return int(rows[0]["version"]) + 1 if rows else 1
    except Exception:
        return 1


def record_challenger(surface: str, body: str, *, parent: int, rationale: str,
                      diff_summary: str = "") -> dict | None:
    """Append a proposed prompt. Never served until `promote` says so."""
    db = _db()
    if db is None:
        return None
    return _insert(db, surface, next_version(surface), body, status="challenger",
                   origin="critic", rationale=rationale, parent=parent,
                   diff_summary=diff_summary)


def get_version(surface: str, version: int) -> dict | None:
    db = _db()
    if db is None:
        return None
    try:
        rows = db.table("prompt_versions").select("*") \
            .eq("surface", surface).eq("version", version).limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception:
        return None


def champion(surface: str) -> dict | None:
    db = _db()
    if db is None:
        return None
    try:
        rows = db.table("prompt_versions").select("*") \
            .eq("surface", surface).eq("status", "champion") \
            .order("version", desc=True).limit(1).execute().data or []
        return rows[0] if rows else None
    except Exception:
        return None


def promote(surface: str, version: int, *, metrics: dict | None = None) -> bool:
    """Make `version` the served prompt. Retires the incumbent."""
    from datetime import datetime, timezone
    db = _db()
    if db is None:
        return False
    now = datetime.now(timezone.utc).isoformat()
    try:
        db.table("prompt_versions").update({"status": "retired", "retired_at": now}) \
            .eq("surface", surface).eq("status", "champion").execute()
        payload = {"status": "champion", "promoted_at": now}
        if metrics:
            payload["metrics"] = metrics
        db.table("prompt_versions").update(payload) \
            .eq("surface", surface).eq("version", version).execute()
        invalidate(surface)
        logger.info(f"prompt_registry: promoted {surface} v{version}")
        return True
    except Exception as e:
        logger.error(f"prompt_registry: promote {surface} v{version} failed: {e}")
        return False


def reject(surface: str, version: int, *, metrics: dict | None = None,
           note: str = "") -> bool:
    db = _db()
    if db is None:
        return False
    try:
        payload: dict = {"status": "rejected"}
        if metrics:
            payload["metrics"] = metrics
        if note:
            payload["diff_summary"] = note[:2000]
        db.table("prompt_versions").update(payload) \
            .eq("surface", surface).eq("version", version).execute()
        return True
    except Exception as e:
        logger.warning(f"prompt_registry: reject {surface} v{version} failed: {e}")
        return False


def rollback(surface: str) -> dict:
    """Return to the most recent previously-served version, or the baseline.

    The manual escape hatch. Picks the highest retired version below the current
    champion; if there is none, promotes v0.
    """
    db = _db()
    if db is None:
        return {"ok": False, "error": "no database"}
    cur = champion(surface)
    cur_v = int((cur or {}).get("version") or 0)
    try:
        rows = db.table("prompt_versions").select("version") \
            .eq("surface", surface).eq("status", "retired") \
            .lt("version", cur_v).order("version", desc=True).limit(1).execute().data or []
    except Exception as e:
        return {"ok": False, "error": str(e)}
    target = int(rows[0]["version"]) if rows else 0
    if target == cur_v:
        return {"ok": False, "error": "nothing to roll back to"}
    ok = promote(surface, target)
    if ok and cur_v:
        reject(surface, cur_v, note="rolled back")
    return {"ok": ok, "from_version": cur_v, "to_version": target}
