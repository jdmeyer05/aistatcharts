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

HOW IT IMPROVES — AND THE COMPARISON THAT LOOKS RIGHT AND IS NOT. The first
version of this file scored each label against the FLOOR its name implies:
"confident" claims 65%, it delivered 77%, so it looked like 12 points of headroom
and the file duly recommended loosening the threshold to call more sessions.

That recommendation was backwards. A floor is a minimum, not a forecast. Cells
that clear a 65% threshold have an average well above 65% — measured here, 80.2%
— so delivering 77% against that floor is not headroom at all. Against what the
label ACTUALLY claimed it is 3 points SHORT, and loosening the threshold would
have made the read worse while the scorecard congratulated it.

So calibration is measured against the mean claimed probability of the readings
that fired, never against the threshold that admitted them. And a deviation is
only reported when it clears sampling noise: with five labels scored, the largest
of five z-scores runs about 2.3 under the null, so the bar is 2.5 rather than the
usual 2. Silence — "calibrated within noise" — is a real and common answer, and a
scorecard that always finds something to fix is a scorecard tuning itself into
the sample.

SHRINKAGE WAS TESTED AND REJECTED — do not retry it. The top of the reliability
curve runs about three points hot, which is the signature of a winner's curse:
the band with the highest observed rate is partly high BECAUSE it was highest.
The textbook fix is to shrink each band toward the base rate, so it was
implemented with an empirical-Bayes weight (estimated, not chosen) and scored the
same way as everything else here. It changed weighted calibration error not at
all — 1.43pp either way — was marginally worse in BOTH halves, and cost half a
point of coverage. The estimated prior weight came out near zero, which is the
explanation: between-band variance dominates within-cell noise, so the bands
genuinely differ and the cells are not thin enough to need shrinking. The
residual top-end gap is therefore not a cell-noise problem, and the scorecard
reports it rather than pretending a known-ineffective remedy would close it.

Coverage matters alongside accuracy: a read that says "mixed" four times in five
is well behaved and useless. An era split prints too, because a fit that was
right on 2023 and wrong on 2026 is a stale fit rather than a broken idea, and
those need different fixes.
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

# The record must score what production actually does, so it uses the same
# rolling fit window. Scoring an expanding fit would grade a module that is not
# the one shipping.


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
        # The probability returned must be the probability of the OUTCOME THIS
        # ROW IS SCORED ON, and a "mixed" row is scored on the session finishing
        # mixed. Returning p_best here — the chance of the class that merely came
        # closest — compared the odds of one outcome against the occurrence of
        # another, and it showed: the lowest reliability bin read 32% claimed
        # against 43% delivered at z=9.2, and the scorecard duly reported "mixed
        # understates" as something to fix. Nothing was wrong with the read.
        return "mixed", "mixed", max(0.0, 1.0 - p_chop - p_trend)
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
            from src.es_chop import _FIT_WINDOW
            tr = panel.iloc[max(0, start - _FIT_WINDOW):start]
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

        def _calib(s_):
            """Delivered minus CLAIMED, in points, with its sampling z-score.

            The claim is the mean of the per-reading probabilities that fired,
            not the threshold that let them through. Those are different numbers
            and only the first is a forecast."""
            if s_.empty or not s_.p.notna().any():
                return None, None
            claimed = float(s_.p.mean())
            delivered_ = float(s_.hit.mean())
            n_ = len(s_)
            se = float(np.sqrt(max(claimed * (1 - claimed), 1e-9) / n_))
            return (delivered_ - claimed) * 100, (delivered_ - claimed) / se if se > 0 else None

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
                # The honest calibration statistic. `margin_pp` above is kept
                # because the floor is what the WORD promises a reader, but it
                # must never drive a tuning decision — see the module docstring.
                "calibration_pp": (lambda c: round(c[0], 1) if c[0] is not None else None)(_calib(s)),
                "calibration_z": (lambda c: round(c[1], 2) if c[1] is not None else None)(_calib(s)),
            })

        # A reliability curve, which is the diagnostic a per-label table cannot
        # be: it bins by what was CLAIMED rather than by which word was printed,
        # so a read that is calibrated on average while being optimistic at the
        # top and pessimistic in the middle shows up here and nowhere else.
        reliability = []
        rr = r[r.p.notna()]
        if len(rr) >= 200:
            edges = [0.0, 0.35, 0.45, 0.55, 0.65, 0.75, 1.01]
            for i in range(len(edges) - 1):
                seg = rr[(rr.p >= edges[i]) & (rr.p < edges[i + 1])]
                if len(seg) < 50:
                    continue
                cl, dv = float(seg.p.mean()), float(seg.hit.mean())
                se = float(np.sqrt(max(cl * (1 - cl), 1e-9) / len(seg)))
                reliability.append({
                    "claimed_pct": round(cl * 100, 1),
                    "delivered_pct": round(dv * 100, 1),
                    "gap_pp": round((dv - cl) * 100, 1),
                    "z": round((dv - cl) / se, 2) if se > 0 else None,
                    "n": int(len(seg)),
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
        Z_BAR = 2.5     # five labels scored; the largest of five |z| runs ~2.3 under the null
        worst = None
        for row in out_rows:
            if row.get("never_fired"):
                continue
            z, cpp = row.get("calibration_z"), row.get("calibration_pp")
            if z is None or cpp is None:
                continue
            if worst is None or abs(z) > abs(worst[1]):
                worst = (row["label"], z, cpp)
            if abs(z) < Z_BAR:
                continue
            marginal = "Marginally: " if abs(z) < 3.0 else ""
            if cpp < 0:
                notes.append(f"{marginal}{row['label']} promises "
                             f"{row['claimed_avg_pct']:.0f}% and delivers "
                             f"{row['delivered_pct']:.0f}% ({cpp:+.0f}pp, {z:.1f} SE) — it "
                             f"overstates. Empirical-Bayes shrinkage was tested against this "
                             f"and did not improve calibration, so thin cells are not the "
                             f"cause; reported rather than corrected.")
            else:
                notes.append(f"{marginal}{row['label']} promises "
                             f"{row['claimed_avg_pct']:.0f}% and delivers "
                             f"{row['delivered_pct']:.0f}% ({cpp:+.0f}pp, {z:.1f} SE) — it "
                             f"understates, so the threshold is conservative and could admit "
                             f"more readings.")
        if not notes and worst is not None:
            notes.append(f"Calibrated within noise on every label — the largest deviation is "
                         f"{worst[0]} at {worst[2]:+.1f}pp ({worst[1]:.1f} SE, and the bar is "
                         f"{Z_BAR:.1f} across five labels). No threshold change is supported by "
                         f"this window.")
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

        from src.es_chop import _FIT_WINDOW as _FIT_WINDOW_DOC
        out = {
            "available": True,
            "fit_window": _FIT_WINDOW_DOC,
            "rows": out_rows,
            "eras": eras,
            "reliability": reliability,
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
                f"Walk-forward on the same rolling {_FIT_WINDOW_DOC}-session window "
                f"production fits on, refitted every {_REFIT_EVERY} sessions after a "
                f"{_MIN_TRAIN}-session warm-up, each session scored only against sessions "
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
