"""How the chop/trend read has actually done — scored walk-forward.

WHY WALK-FORWARD AND NOT A REPLAY AGAINST THE CURRENT FIT. `es_chop` calibrates
its confidence on the whole sample. Scoring it against that same fit would ask
the read how well it does on the sessions that taught it, and it would flatter
itself by exactly the amount the fit is overfitted — which is the one quantity a
track record exists to expose. So every session here is scored against a fit
built ONLY from sessions before it: a 500-session minimum training window,
refitted every 21 sessions, everything after that scored out of sample. Sessions
inside the initial window are not scored at all rather than scored cheaply.

WHAT IS AND IS NOT SCOREABLE. The cumulative read makes a claim — this session
will FINISH in that class — so it can be checked. The hourly read makes no claim
at all: it reports what an hour did, and an hour that is over has no outcome
left to be right or wrong about. Inventing a score for it would be dressing a
measurement as a forecast, so the hourly rows are deliberately absent here and
the payload says why.

HOW IT IMPROVES. The gap between what a label CLAIMS and what it DELIVERS is
the lever. A "confident" bucket delivering well above its floor is leaving calls
on the table and the threshold could come down; one delivering below it is
miscalibrated and must go up. Coverage matters alongside accuracy — a read that
says "mixed" four times in five is well behaved and useless. Both print, plus an
era split, because a fit that was right on 2022 and wrong on 2026 is a stale fit
rather than a broken idea, and those need different fixes.
"""

from __future__ import annotations

import logging
from time import time as _now

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL_S = 12 * 3600

_MIN_TRAIN = 500        # sessions before anything is scored
_REFIT_EVERY = 21       # roughly monthly, the standard walk-forward cadence


def _label_for(er_now: float, train_hist: np.ndarray, train_fin: np.ndarray,
               lo_f: float, hi_f: float, edges_q, confident: float,
               likely: float, min_cell: int) -> tuple[str, str, float]:
    """Reproduce the production label from a training slice. Returns
    (label, side, p_best)."""
    edges = np.quantile(train_hist, edges_q)
    bi = 0
    for i in range(len(edges) - 1):
        last = i == len(edges) - 2
        if edges[i] <= er_now < edges[i + 1] or (last and er_now >= edges[i]):
            bi = i
            break
    top = bi >= len(edges) - 2
    m = (train_hist >= edges[bi]) & (
        np.ones_like(train_hist, dtype=bool) if top else (train_hist < edges[bi + 1]))
    if int(m.sum()) < min_cell:
        m = (train_hist < np.quantile(train_hist, 1 / 3)) if er_now < float(np.median(train_hist)) \
            else (train_hist >= np.quantile(train_hist, 2 / 3))
    if int(m.sum()) == 0:
        return "mixed", "mixed", float("nan")
    f = train_fin[m]
    p_chop, p_trend = float((f < lo_f).mean()), float((f >= hi_f).mean())
    p_best, side = (p_trend, "trendy") if p_trend >= p_chop else (p_chop, "choppy")
    if not np.isfinite(p_best) or p_best < likely:
        return "mixed", "mixed", p_best
    return (("confident " if p_best >= confident else "likely ") + side), side, p_best


def chop_track_record(fine: pd.DataFrame | None = None) -> dict | None:
    """Walk-forward scorecard for the cumulative chop/trend read."""
    try:
        from src.es_chop import (_panel, _MARKS, _EDGES, _CONFIDENT, _LIKELY,
                                 _MIN_CELL)
        if fine is None:
            from src.es_baserates import _fine
            fine = _fine()
        if fine is None or fine.empty:
            return {"available": False, "reason": "no intraday bars"}

        key = ("record", len(fine), str(fine.index[-1]))
        hit = _CACHE.get(key)
        if hit and (_now() - hit[0]) < _TTL_S:
            return hit[1]

        today = fine.index.normalize().max()
        panel = _panel(fine[fine.index.normalize() != today])
        if panel.empty or len(panel) < _MIN_TRAIN + 100:
            return {"available": False, "reason": "not enough history to score"}

        rows = []                      # one per (session, mark) actually scored
        n = len(panel)
        for start in range(_MIN_TRAIN, n, _REFIT_EVERY):
            tr = panel.iloc[:start]
            te = panel.iloc[start:start + _REFIT_EVERY]
            if te.empty:
                break
            # Class cuts are part of the fit and must come from the training
            # slice too — cutting them on the whole sample would leak the
            # future into the definition of the thing being predicted.
            q = tr["final"].dropna().quantile([1 / 3, 2 / 3])
            lo_f, hi_f = float(q.iloc[0]), float(q.iloc[1])
            for mark in _MARKS:
                c = tr[[mark, "final"]].dropna()
                if len(c) < 200:
                    continue
                th, tf = c[mark].to_numpy(float), c["final"].to_numpy(float)
                for day, r in te.iterrows():
                    er_now, fin = r.get(mark), r.get("final")
                    if not (np.isfinite(er_now) and np.isfinite(fin)):
                        continue
                    label, side, p = _label_for(er_now, th, tf, lo_f, hi_f,
                                                _EDGES, _CONFIDENT, _LIKELY, _MIN_CELL)
                    actual = "choppy" if fin < lo_f else ("trendy" if fin >= hi_f else "mixed")
                    rows.append({"day": day, "mark": mark, "label": label,
                                 "side": side, "p": p, "actual": actual,
                                 "hit": (side == actual) if label != "mixed"
                                        else (actual == "mixed")})
        if not rows:
            return {"available": False, "reason": "nothing scored"}
        r = pd.DataFrame(rows)
        total = len(r)

        def _floor(lbl: str):
            if lbl.startswith("confident"):
                return _CONFIDENT * 100
            if lbl.startswith("likely"):
                return _LIKELY * 100
            return None

        out_rows = []
        for lbl in ("confident trendy", "likely trendy", "mixed",
                    "likely choppy", "confident choppy"):
            s = r[r.label == lbl]
            if s.empty:
                out_rows.append({"label": lbl, "n": 0, "never_fired": True})
                continue
            delivered = float(s.hit.mean() * 100)
            fl = _floor(lbl)
            out_rows.append({
                "label": lbl,
                "n": int(len(s)),
                "coverage_pct": round(len(s) / total * 100, 1),
                "delivered_pct": round(delivered, 1),
                "claimed_floor_pct": round(fl, 1) if fl is not None else None,
                "claimed_avg_pct": round(float(s.p.mean() * 100), 1) if s.p.notna().any() else None,
                "clears_floor": bool(fl is None or delivered >= fl),
                "margin_pp": round(delivered - fl, 1) if fl is not None else None,
            })

        # Drift: the same labels over the first and second half of the SCORED
        # window. A read that was calibrated in 2023 and is not now is a stale
        # fit, which is a different problem from a bad idea and takes a
        # different fix.
        r = r.sort_values("day")
        half = len(r) // 2
        eras = []
        for name, part in (("earlier", r.iloc[:half]), ("recent", r.iloc[half:])):
            if part.empty:
                continue
            eras.append({
                "era": name,
                "from": str(pd.Timestamp(part.day.min()).date()),
                "to": str(pd.Timestamp(part.day.max()).date()),
                "confident_delivered_pct": round(float(
                    part[part.label.str.startswith("confident")].hit.mean() * 100), 1)
                    if (part.label.str.startswith("confident")).any() else None,
                "likely_delivered_pct": round(float(
                    part[part.label.str.startswith("likely")].hit.mean() * 100), 1)
                    if (part.label.str.startswith("likely")).any() else None,
            })

        # What would actually improve it, stated only where the numbers support
        # a direction. Silence is preferable to a suggestion invented to fill
        # the field.
        notes = []
        for row in out_rows:
            if row.get("never_fired") or row["label"] == "mixed":
                continue
            m = row.get("margin_pp")
            if m is None:
                continue
            if m < 0:
                notes.append(f"{row['label']} delivers {row['delivered_pct']:.0f}% against a "
                             f"{row['claimed_floor_pct']:.0f}% floor — the threshold is too "
                             f"loose and should rise.")
            elif m > 12:
                notes.append(f"{row['label']} delivers {row['delivered_pct']:.0f}% against a "
                             f"{row['claimed_floor_pct']:.0f}% floor, {m:.0f}pp of headroom — "
                             f"the threshold is conservative and could be relaxed to call more "
                             f"sessions.")
        mixed = next((x for x in out_rows if x["label"] == "mixed"), None)
        if mixed and mixed.get("coverage_pct", 0) >= 50:
            notes.append(f"The read declines to call {mixed['coverage_pct']:.0f}% of "
                         f"(session, hour) pairs. Accuracy is not the binding constraint; "
                         f"coverage is.")
        if len(eras) == 2 and all(e.get("confident_delivered_pct") is not None for e in eras):
            gap = eras[1]["confident_delivered_pct"] - eras[0]["confident_delivered_pct"]
            if abs(gap) >= 10:
                notes.append(f"Confident calls deliver {gap:+.0f}pp differently in the recent "
                             f"half than the earlier one — the fit is drifting, so refitting "
                             f"matters more than retuning.")

        out = {
            "available": True,
            "rows": out_rows,
            "eras": eras,
            "observations": total,
            "sessions_scored": int(r.day.nunique()),
            "scored_from": str(pd.Timestamp(r.day.min()).date()),
            "scored_to": str(pd.Timestamp(r.day.max()).date()),
            "train_min": _MIN_TRAIN,
            "refit_every": _REFIT_EVERY,
            "improvements": notes,
            "hourly_scored": False,
            "hourly_reason": (
                "The hourly rows make no prediction — they report what an hour did, "
                "and a finished hour has no outcome left to be right about. Scoring "
                "them would dress a measurement as a forecast."
            ),
            "method": (
                f"Walk-forward: a {_MIN_TRAIN}-session minimum training window refitted "
                f"every {_REFIT_EVERY} sessions, each session scored only against sessions "
                "before it. The tercile class cuts are refitted with the rest, since cutting "
                "them on the whole sample would leak the future into the definition of the "
                "outcome. One observation per session per mark."
            ),
        }
        _CACHE[key] = (_now(), out)
        return out
    except Exception as e:
        logger.warning(f"chop_track_record failed: {e}")
        return {"available": False, "reason": "computation failed"}
