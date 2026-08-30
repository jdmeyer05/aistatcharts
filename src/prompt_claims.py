"""The falsifiable half: statements that price can settle, and their base rates.

WHY THIS EXISTS. Grounding proves the note was faithful to the data it was
handed. It cannot prove the note was RIGHT — a perfectly grounded paragraph can
call the regime backwards. So the market-driver prompt is required to emit, in a
field the page never renders, two to four machine-resolvable calls. Those are
what get scored against what actually happened.

THE LEAK THIS SCHEMA IS BUILT TO AVOID. The obvious design — measure from the
last completed close to the horizon close — hands the model a free lunch. A note
written at 10:30 already knows SPY is up 0.4% on the day, so "SPY up ≥ 0.3%"
would resolve true from information it could see. Every claim here is instead
measured from THE FIRST CLOSE AT OR AFTER the note was written, to a close
`sessions` later. Both endpoints are in the future at the moment of writing, so
the whole measured interval is unknown to the model. A note written at 10:30
Tuesday for one session is scored Tuesday-close to Wednesday-close.

EVERY HIT RATE SHIPS WITH ITS BASE RATE. "62% correct" is not a result. The same
claim, asked unconditionally of the last year of that subject's daily bars, has
its own frequency — and if the model is calling 0.5% SPY moves that happen 58%
of the time anyway, 62% is noise wearing a suit. `base_rate` is stored per claim
and the Brier skill score in `scoreboard` is computed against exactly that
reference forecast.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Ops the resolver knows how to settle. Anything else is dropped at extraction
# time rather than stored and silently never scored.
#   up_gte        pct change >= +threshold
#   down_gte      pct change <= -threshold
#   abs_lt        |pct change| < threshold      (a "quiet" call)
#   abs_gte       |pct change| >= threshold     (a "big move" call)
#   outperform    subject pct change - vs pct change >= threshold
OPS = {"up_gte", "down_gte", "abs_lt", "abs_gte", "outperform"}

_MAX_CLAIMS = 4
_MAX_SESSIONS = 5
_EXPIRE_DAYS = 21
_BASE_RATE_SESSIONS = 252


# ── extraction ────────────────────────────────────────────────────

def extract(output: dict, payload: dict) -> list[dict]:
    """Pull `calls` out of a model response and keep only what can be settled.

    Validation is strict and silent. A malformed call is dropped, not repaired:
    a repaired claim is one the model did not actually make, and scoring the
    loop against claims it invented on the model's behalf would corrupt the only
    honest number in the system.
    """
    raw = (output or {}).get("calls") or []
    if not isinstance(raw, list):
        return []

    known = _payload_subjects(payload)
    out: list[dict] = []
    for item in raw[: _MAX_CLAIMS * 2]:
        if not isinstance(item, dict):
            continue
        subject = str(item.get("subject") or "").strip().upper()
        op = str(item.get("op") or "").strip().lower()
        if op not in OPS or not subject:
            continue
        # The subject has to be something the model was actually shown. A call
        # on a ticker absent from the payload is an invented claim.
        if known and subject not in known:
            continue
        try:
            threshold = float(item.get("threshold"))
            sessions = int(item.get("sessions") or 1)
            confidence = float(item.get("confidence") or 0.5)
        except (TypeError, ValueError):
            continue
        if not (0.01 <= abs(threshold) <= 25):
            continue
        if not (1 <= sessions <= _MAX_SESSIONS):
            continue
        # A confidence pinned at 0 or 1 is not a probability, it is a boast; the
        # Brier score would be dominated by it. Clamp rather than drop.
        confidence = min(0.99, max(0.01, confidence))

        vs = str(item.get("vs") or "").strip().upper()
        if op == "outperform":
            if not vs or (known and vs not in known):
                continue
        else:
            vs = ""

        out.append({
            "subject": subject,
            "vs": vs,
            "op": op,
            "threshold": round(abs(threshold), 4),
            "sessions": sessions,
            "confidence": round(confidence, 4),
            "text": str(item.get("text") or "")[:300],
        })
        if len(out) >= _MAX_CLAIMS:
            break
    return out


def _payload_subjects(payload: dict) -> set[str]:
    subs: set[str] = set()
    try:
        for tk in (payload or {}).get("quotes", {}) or {}:
            subs.add(str(tk).upper())
    except Exception:
        pass
    return subs


def store(snapshot_id: int, surface: str, claims: list[dict],
          stated_at: datetime | None = None) -> int:
    from src.db import get_client
    db = get_client()
    if db is None or not claims:
        return 0
    stated = stated_at or datetime.now(timezone.utc)
    rows = []
    for c in claims:
        # Padding covers weekends and holidays; the resolver re-checks that
        # enough bars actually exist and leaves the claim pending if not.
        resolve_at = stated + timedelta(days=int(c["sessions"]) + 4)
        rows.append({
            "snapshot_id": snapshot_id,
            "surface": surface,
            "claim": c,
            "confidence": c.get("confidence"),
            "stated_at": stated.isoformat(),
            "resolve_at": resolve_at.isoformat(),
            "status": "pending",
        })
    try:
        db.table("ai_claims").insert(rows).execute()
        return len(rows)
    except Exception as e:
        logger.warning(f"prompt_claims: store failed: {e}")
        return 0


# ── resolution ────────────────────────────────────────────────────

def _bars(ticker: str):
    """Daily bars, through the shared OHLCV cache (Polygon + yfinance + Supabase).

    Deliberately reuses `fetch_ohlcv` rather than hitting Polygon directly: it
    already knows which symbols Polygon can serve and never rewrites a symbol to
    reach it, which is what keeps `^VIX` off the equity endpoint and futures
    roots off Colgate and Eversource.
    """
    try:
        from src.ohlcv_cache import fetch_ohlcv
        df = fetch_ohlcv(ticker, lookback_days=504)
        if df is None or df.empty or "Close" not in df.columns:
            return None
        return df
    except Exception as e:
        logger.debug(f"prompt_claims: bars for {ticker} failed: {e}")
        return None


def _index_dates(df) -> list:
    """The bar index as plain `date` objects, however it arrived.

    `fetch_ohlcv` returns a `date` index from the Supabase cache path and a
    tz-aware DatetimeIndex from the Polygon path, and comparing a Timestamp
    against a `date` raises rather than returning False — so the resolver has
    to normalise before it can search.
    """
    import pandas as pd
    from datetime import date as _date, datetime as _dt
    out = []
    for v in df.index:
        if isinstance(v, _dt):
            out.append(v.date())
        elif isinstance(v, _date):
            out.append(v)
        else:
            out.append(pd.Timestamp(v).date())
    return out


def _closes_after(df, stated: datetime, sessions: int):
    """(ref_close, outcome_close, ref_date, outcome_date) or None if not settled.

    ref is the first close the model could NOT have seen — see the module
    docstring. Which close that is depends on the clock in New York, not on UTC:
    a note written at 10:30 ET is scored from that same session's close, because
    it had not printed yet; a note written at 17:05 ET is scored from the NEXT
    session's close, because that day's had. Resolving both the same way is how
    a scoring system either gives away a session or hands the model one it
    already read.
    """
    import pandas as pd
    from bisect import bisect_left
    from datetime import time as _time, timedelta as _td

    ts = pd.Timestamp(stated)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    local = ts.tz_convert("America/New_York")

    effective = local.date()
    if local.time() > _time(16, 0):
        effective = effective + _td(days=1)

    idx = _index_dates(df)
    pos = bisect_left(idx, effective)
    if pos >= len(idx):
        return None                      # the reference close has not printed yet
    out_pos = pos + sessions
    if out_pos >= len(idx):
        return None                      # horizon not reached
    return (float(df["Close"].iloc[pos]), float(df["Close"].iloc[out_pos]),
            str(idx[pos])[:10], str(idx[out_pos])[:10])


def _outcome(op: str, threshold: float, move_pct: float) -> bool:
    if op == "up_gte":
        return move_pct >= threshold
    if op == "down_gte":
        return move_pct <= -threshold
    if op == "abs_lt":
        return abs(move_pct) < threshold
    if op == "abs_gte":
        return abs(move_pct) >= threshold
    if op == "outperform":
        return move_pct >= threshold     # move_pct is already the spread
    return False


def _base_rate(df, op: str, threshold: float, sessions: int, df_vs=None) -> float | None:
    """How often this exact call is true unconditionally, over the last year.

    This is the "relative to what" for every hit rate downstream. Computed on
    overlapping windows for multi-session horizons, which is fine for a
    frequency and would not be for a t-statistic — nothing here computes one.
    """
    try:
        import numpy as np
        c = df["Close"].astype(float).values[-(_BASE_RATE_SESSIONS + sessions):]
        if len(c) < sessions + 30:
            return None
        move = (c[sessions:] / c[:-sessions] - 1.0) * 100.0
        if op == "outperform":
            if df_vs is None:
                return None
            c2 = df_vs["Close"].astype(float).values[-(_BASE_RATE_SESSIONS + sessions):]
            n = min(len(c), len(c2))
            if n < sessions + 30:
                return None
            m1 = (c[-n:][sessions:] / c[-n:][:-sessions] - 1.0) * 100.0
            m2 = (c2[-n:][sessions:] / c2[-n:][:-sessions] - 1.0) * 100.0
            move = m1 - m2
        hits = [_outcome(op, threshold, float(m)) for m in move]
        return round(float(np.mean(hits)), 4) if hits else None
    except Exception as e:
        logger.debug(f"prompt_claims: base rate failed: {e}")
        return None


def resolve_due(limit: int = 200) -> dict:
    """Settle every claim whose horizon has passed. Safe to run repeatedly."""
    from src.db import get_client
    db = get_client()
    if db is None:
        return {"ok": False, "error": "no database"}

    now = datetime.now(timezone.utc)
    try:
        rows = db.table("ai_claims").select("*") \
            .eq("status", "pending").lte("resolve_at", now.isoformat()) \
            .order("resolve_at").limit(limit).execute().data or []
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if not rows:
        return {"ok": True, "resolved": 0, "pending_checked": 0}

    bar_cache: dict = {}

    def bars(tk):
        if tk not in bar_cache:
            bar_cache[tk] = _bars(tk)
        return bar_cache[tk]

    resolved = expired = stuck = 0
    for row in rows:
        claim = row.get("claim") or {}
        subject = str(claim.get("subject") or "")
        op = str(claim.get("op") or "")
        try:
            threshold = float(claim.get("threshold"))
            sessions = int(claim.get("sessions") or 1)
            stated = datetime.fromisoformat(str(row["stated_at"]).replace("Z", "+00:00"))
        except Exception:
            _mark(db, row["id"], "unresolvable", note="malformed claim")
            expired += 1
            continue

        age_days = (now - stated).days
        df = bars(subject)
        if df is None:
            if age_days > _EXPIRE_DAYS:
                _mark(db, row["id"], "unresolvable", note=f"no bars for {subject}")
                expired += 1
            else:
                stuck += 1
            continue

        got = _closes_after(df, stated, sessions)
        if got is None:
            if age_days > _EXPIRE_DAYS:
                _mark(db, row["id"], "unresolvable", note="horizon never settled")
                expired += 1
            else:
                stuck += 1
            continue

        ref, out, ref_d, out_d = got
        move = (out / ref - 1.0) * 100.0 if ref else 0.0

        df_vs = None
        if op == "outperform":
            vs = str(claim.get("vs") or "")
            df_vs = bars(vs)
            got_vs = _closes_after(df_vs, stated, sessions) if df_vs is not None else None
            if got_vs is None:
                if age_days > _EXPIRE_DAYS:
                    _mark(db, row["id"], "unresolvable", note=f"no bars for {vs}")
                    expired += 1
                else:
                    stuck += 1
                continue
            move -= (got_vs[1] / got_vs[0] - 1.0) * 100.0 if got_vs[0] else 0.0

        correct = _outcome(op, threshold, move)
        br = _base_rate(df, op, threshold, sessions, df_vs)

        try:
            db.table("ai_claims").update({
                "status": "resolved",
                "correct": bool(correct),
                "base_rate": br,
                "actual": {
                    "move_pct": round(move, 4),
                    "ref_close": round(ref, 4),
                    "outcome_close": round(out, 4),
                    "ref_date": ref_d,
                    "outcome_date": out_d,
                },
                "resolved_at": now.isoformat(),
            }).eq("id", row["id"]).execute()
            resolved += 1
        except Exception as e:
            logger.warning(f"prompt_claims: update {row['id']} failed: {e}")

    return {"ok": True, "resolved": resolved, "expired": expired,
            "not_ready": stuck, "checked": len(rows)}


def _mark(db, claim_id: int, status: str, note: str = "") -> None:
    try:
        db.table("ai_claims").update({
            "status": status,
            "actual": {"note": note},
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", claim_id).execute()
    except Exception:
        pass


# ── scoring ───────────────────────────────────────────────────────

def scoreboard(surface: str = "market_driver", days: int = 90,
               prompt_version: int | None = None) -> dict:
    """Hit rate, base rate, and Brier skill for resolved claims.

    BRIER SKILL IS THE HEADLINE, NOT HIT RATE. Skill compares the model's own
    stated confidence against the reference forecast of simply quoting the base
    rate every time. Positive means the confidences carried information; zero
    means the calls were worth exactly what the calendar was worth; negative
    means stating the base rate would have been better. A hit rate alone cannot
    distinguish those three.

    `n` COUNTS CLAIMS, NOT SESSIONS, and those are far apart here — see
    `_cluster_ci`. The `clustered` block carries the day count and an interval
    that does not assume independence. Brier and Brier skill are means over the
    same non-independent rows, so they inherit the same problem: read them as
    point estimates whose uncertainty is the clustered interval's, not the
    naive one's.
    """
    from src.db import get_client
    db = get_client()
    if db is None:
        return {"ok": False, "error": "no database"}

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    # Paged, not limited: a 90-day window crosses PostgREST's silent 1000-row
    # ceiling inside two weeks of normal traffic, and a truncated sample here
    # would quietly change every calibration number on the page.
    from src.prompt_snapshots import paged
    try:
        rows = paged(lambda: db.table("ai_claims")
                     .select("claim,confidence,correct,base_rate,stated_at,snapshot_id")
                     .eq("surface", surface).eq("status", "resolved")
                     .gte("stated_at", since).order("stated_at"))
    except Exception as e:
        return {"ok": False, "error": str(e)}

    if prompt_version is not None:
        ids = {r["snapshot_id"] for r in rows}
        keep = _snapshot_versions(db, ids)
        rows = [r for r in rows if keep.get(r["snapshot_id"]) == prompt_version]

    if not rows:
        return {"ok": True, "n": 0, "note": "no resolved claims in window"}

    n = len(rows)
    hits = sum(1 for r in rows if r.get("correct"))
    with_br = [r for r in rows if r.get("base_rate") is not None]

    brier = _mean([(float(r.get("confidence") or 0.5) - (1.0 if r.get("correct") else 0.0)) ** 2
                   for r in rows])
    brier_ref = _mean([(float(r["base_rate"]) - (1.0 if r.get("correct") else 0.0)) ** 2
                       for r in with_br]) if with_br else None
    # Skill has to be computed on the SAME rows as its reference, or the
    # comparison is between two different samples.
    brier_on_ref_rows = _mean([(float(r.get("confidence") or 0.5) - (1.0 if r.get("correct") else 0.0)) ** 2
                               for r in with_br]) if with_br else None
    skill = None
    if brier_ref not in (None, 0) and brier_on_ref_rows is not None:
        skill = round(1.0 - (brier_on_ref_rows / brier_ref), 4)

    lo, hi = _wilson(hits, n)
    return {
        "ok": True,
        "surface": surface,
        "days": days,
        "n": n,
        "hit_rate": round(hits / n, 4),
        "hit_rate_ci95": [round(lo, 4), round(hi, 4)],
        # The interval above assumes independent draws and this one does not.
        # Both ship: a reader comparing them can see how much of the apparent
        # precision was an artefact of counting reloads as evidence.
        "clustered": _cluster_ci(rows),
        "base_rate": round(_mean([float(r["base_rate"]) for r in with_br]), 4) if with_br else None,
        "n_with_base_rate": len(with_br),
        "brier": round(brier, 4) if brier is not None else None,
        "brier_base_rate": round(brier_ref, 4) if brier_ref is not None else None,
        "brier_skill": skill,
        "by_op": _group(rows, lambda r: (r.get("claim") or {}).get("op", "?")),
        "by_subject": _group(rows, lambda r: (r.get("claim") or {}).get("subject", "?")),
    }


def _snapshot_versions(db, ids: set) -> dict:
    if not ids:
        return {}
    try:
        rows = db.table("ai_snapshots").select("id,prompt_version") \
            .in_("id", list(ids)[:1000]).execute().data or []
        return {r["id"]: r["prompt_version"] for r in rows}
    except Exception:
        return {}


def _group(rows: list[dict], key) -> dict:
    out: dict = {}
    for r in rows:
        k = str(key(r))
        b = out.setdefault(k, {"n": 0, "hits": 0, "base_rate_sum": 0.0, "n_br": 0})
        b["n"] += 1
        b["hits"] += 1 if r.get("correct") else 0
        if r.get("base_rate") is not None:
            b["base_rate_sum"] += float(r["base_rate"])
            b["n_br"] += 1
    for k, b in out.items():
        b["hit_rate"] = round(b["hits"] / b["n"], 4) if b["n"] else None
        b["base_rate"] = round(b["base_rate_sum"] / b["n_br"], 4) if b["n_br"] else None
        b.pop("base_rate_sum", None)
        b.pop("n_br", None)
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson interval — the small-n case is exactly where this loop operates.

    Assumes the n draws are independent. For this loop's claims they are not;
    see `_cluster_ci`. Kept and still reported so the naive and the clustered
    interval sit side by side — the gap between them is the thing worth seeing.
    """
    if n == 0:
        return (0.0, 1.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = (z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def _session_day(stated_at: str | None) -> str:
    """The ET calendar day a claim was made on — the cluster key.

    ET, not UTC: a claim stated at 20:00 ET is 01:00 UTC the following day, and
    grouping on UTC would split one evening across two clusters, understating
    exactly the dependence this exists to measure.
    """
    if not stated_at:
        return "unknown"
    try:
        from zoneinfo import ZoneInfo
        t = datetime.fromisoformat(str(stated_at).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return str(t.astimezone(ZoneInfo("America/New_York")).date())
    except Exception:
        return str(stated_at)[:10]


#: Days needed before a cluster bootstrap is quoted at all. Below this the
#: interval is a function of how few clusters there are rather than of the
#: data — see the refusal branch in `_cluster_ci`.
_MIN_CLUSTERS = 10


def _cluster_ci(rows: list[dict], b: int = 2000, seed: int = 20260829) -> dict:
    """Day-clustered interval for the hit rate, plus the equal-weight variant.

    WHY THIS IS NOT OPTIONAL RIGOUR. The home interpretation runs with
    `autoRun`, so it fires once per page load, and its payload carries a live
    price and an `as_of` — the cache key therefore changes every few minutes
    and each reload produces a fresh snapshot with a fresh set of claims. One
    session can contribute a dozen claims that are all restatements of the same
    bet about the same market state. Wilson counts them as a dozen independent
    draws and returns an interval roughly sqrt(m) too narrow, m being claims
    per day. That is the overlapping-window problem this project already
    handles with Newey-West in the swing screen, and the same discipline
    belongs on the number the loop uses to judge itself.

    METHOD. Resample DAYS with replacement and recompute — the cluster
    bootstrap. It assumes nothing about the within-day correlation structure,
    which matters, because there is no reason the correlation between two 10:00
    claims should match that between a 10:00 and a 15:30 one. Seeded, so the
    figure on the page does not drift on its own between reads.

    TWO ESTIMATES, DELIBERATELY. `pooled` weights each CLAIM equally, so a day
    the page was loaded forty times counts forty times — that is the number the
    loop has always reported. `by_day` weights each DAY equally. When the two
    disagree the record is being driven by traffic rather than by forecasting.
    """
    import random

    by_day: dict[str, list[bool]] = {}
    for r in rows:
        by_day.setdefault(_session_day(r.get("stated_at")), []).append(bool(r.get("correct")))

    days = sorted(by_day)
    k = len(days)
    n = sum(len(v) for v in by_day.values())
    if k == 0 or n == 0:
        return {"n_days": 0}

    day_rates = [sum(by_day[d]) / len(by_day[d]) for d in days]
    pooled = sum(sum(by_day[d]) for d in days) / n
    mean_by_day = sum(day_rates) / k

    # TOO FEW CLUSTERS TO BOOTSTRAP, and this refusal is load-bearing.
    #
    # The cluster bootstrap resamples DAYS, so its resolution is bounded by how
    # many days exist. At k=3 there are only ten distinct multisets to draw
    # from and the interval it returns is an artefact of that coarseness, not a
    # measurement — on live data it came back NARROWER than the naive Wilson
    # interval it was supposed to widen, which is the tell. The literature
    # wants dozens of clusters; ten is already generous.
    #
    # So below the floor this reports the day count and the two point estimates
    # and refuses the interval, exactly as the conditions gate refuses below 30
    # sessions. A wrong interval quoted confidently is worse than an absent one,
    # and this is the module whose whole purpose is not overstating precision.
    if k < _MIN_CLUSTERS:
        return {
            "n_days": k,
            "claims_per_day": round(n / k, 2),
            "hit_rate_pooled": round(pooled, 4),
            "hit_rate_by_day": round(mean_by_day, 4),
            "ci95_clustered": None,
            "min_clusters": _MIN_CLUSTERS,
            "note": (
                "one session in the window — no between-day variation to measure"
                if k < 2 else
                f"{k} sessions is too few to bootstrap over; {_MIN_CLUSTERS} are needed "
                f"before a day-clustered interval means anything. The point estimates "
                f"stand; the interval does not."
            ),
        }

    rng = random.Random(seed)
    pooled_draws: list[float] = []
    byday_draws: list[float] = []
    for _ in range(b):
        pick = [days[rng.randrange(k)] for _ in range(k)]
        num = sum(sum(by_day[d]) for d in pick)
        den = sum(len(by_day[d]) for d in pick)
        if den:
            pooled_draws.append(num / den)
        byday_draws.append(sum(sum(by_day[d]) / len(by_day[d]) for d in pick) / k)

    def _pct(xs: list[float], q: float) -> float:
        if not xs:
            return 0.0
        s = sorted(xs)
        i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
        return s[i]

    naive_lo, naive_hi = _wilson(sum(sum(by_day[d]) for d in days), n)
    clo, chi = _pct(pooled_draws, 0.025), _pct(pooled_draws, 0.975)
    return {
        "n_days": k,
        "claims_per_day": round(n / k, 2),
        "hit_rate_pooled": round(pooled, 4),
        "hit_rate_by_day": round(mean_by_day, 4),
        "ci95_clustered": [round(clo, 4), round(chi, 4)],
        "ci95_by_day": [round(_pct(byday_draws, 0.025), 4),
                        round(_pct(byday_draws, 0.975), 4)],
        # The honest headline of the whole block: how much narrower the
        # independence assumption was making the answer look.
        "width_ratio_vs_naive": (
            round((chi - clo) / (naive_hi - naive_lo), 2)
            if (naive_hi - naive_lo) > 0 else None
        ),
        "bootstrap_draws": b,
    }
