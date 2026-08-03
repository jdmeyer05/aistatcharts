"""Levels that sit on top of each other are ONE reference, not several.

THE ERROR THIS PREVENTS, committed on this project on 2026-08-03: the dealer
gamma call wall sat at 7619.28 and the chart's resistance at 7620.00, and that
was reported as "two independent methods agreeing". They are not independent
evidence — they are two views of the same strike concentration, and describing
them as confirmation manufactures confidence out of one observation counted
twice. The same morning the gamma flip (7541.40), the prior day's high (7541.00)
and the prior value-area high (7540.05) sat inside 1.35 handles and read as three
separate reasons to care about one price.

The ladder makes this easy to do by accident, because it lists every level
separately and sorts by distance. A reader counting rows counts confirmations.

TOLERANCE IS THE MEDIAN BAR, NOT A TUNED CONSTANT. Two levels closer together
than a typical five-minute bar's range cannot be reacted to separately: price
crosses both inside one bar, so no trade can distinguish them and no fill can
respect one but not the other. That makes the median bar the natural unit of
"same price", and it rescales itself with volatility instead of needing a
percentage picked to fit whichever session was on screen when it was written.

Clusters are DESCRIBED, never removed. Each contributing method still matters —
a level that is both the prior high and the gamma flip really is more likely to
matter than one that is only either — but it is one level with several reasons,
which is a different sentence from three levels agreeing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# A cluster needs at least this many DISTINCT methods. Two readings of the same
# family (session high and session close on a trend day, say) are not the point.
_MIN_MEMBERS = 2

# A cluster may not stretch beyond this multiple of the tolerance, however many
# levels chain into it. Keeps "price cannot tell these apart" literally true of
# the whole group rather than only of each adjacent pair.
_MAX_SPAN_MULT = 1.5

# Which family each level belongs to. Levels from the SAME family co-locating is
# arithmetic, not confluence: the session high and the value-area high drift
# together by construction on a trend day, whereas an option-derived wall landing
# on a prior-session high is genuinely two different mechanisms pointing at one
# price. Only cross-family clusters are called out as such.
_FAMILY = {
    "today_high": "session", "today_low": "session", "today_open": "session",
    "vwap": "session",
    "poc": "profile", "vah": "profile", "val": "profile",
    "on_high": "overnight", "on_low": "overnight",
    "py_high": "prior", "py_low": "prior", "py_close": "prior",
    "gamma_flip_es": "options", "call_wall_es": "options", "put_wall_es": "options",
}

_GAMMA_LABELS = {
    "gamma_flip_es": "Gamma flip",
    "call_wall_es": "Call wall",
    "put_wall_es": "Put wall",
}


def cluster_levels(levels: list[dict] | None,
                   gamma: dict | None = None,
                   median_bar: float | None = None,
                   normal_range: float | None = None) -> dict:
    """Group reference prices that price cannot distinguish between.

    `median_bar` is the tolerance when available. Falling back to a fraction of
    the session's normal range keeps the unit self-scaling rather than fixed in
    handles, which would mean something different in a 40-handle regime than in
    a 120-handle one.
    """
    pts: list[dict] = []
    for l in (levels or []):
        v, k = l.get("value"), l.get("key")
        if v is None or k is None:
            continue
        pts.append({"key": k, "label": l.get("label") or k, "value": float(v),
                    "family": _FAMILY.get(k, "other")})

    for k, label in _GAMMA_LABELS.items():
        v = (gamma or {}).get(k)
        if v is not None:
            pts.append({"key": k, "label": label, "value": float(v),
                        "family": "options"})

    if len(pts) < 2:
        return {"available": False, "reason": "not enough levels to cluster"}

    tol = median_bar if (median_bar and median_bar > 0) else (
        normal_range * 0.04 if normal_range else None)
    if not tol or tol <= 0:
        return {"available": False, "reason": "no tolerance scale available"}

    pts.sort(key=lambda p: p["value"])
    groups: list[list[dict]] = [[pts[0]]]
    for p in pts[1:]:
        gap = p["value"] - groups[-1][-1]["value"]
        span = p["value"] - groups[-1][0]["value"]
        # SINGLE-LINKAGE CHAINS IF ONLY THE GAP IS CHECKED. Four levels each 2
        # handles apart are all "within one bar" of their neighbour and would
        # merge into an 6-handle zone that no single bar spans — the cluster
        # would then assert a co-location it has not demonstrated. Observed
        # live: prior high / session low / overnight low / session open chained
        # to 5.25 handles against a 3.75 tolerance. So the total span is capped
        # as well as the step, and a level that would burst the cap starts a new
        # group instead of stretching this one.
        if gap <= tol and span <= tol * _MAX_SPAN_MULT:
            groups[-1].append(p)
        else:
            groups.append([p])

    clusters = []
    for g in groups:
        if len(g) < _MIN_MEMBERS:
            continue
        fams = sorted({m["family"] for m in g})
        lo, hi = g[0]["value"], g[-1]["value"]
        clusters.append({
            "low": round(lo, 2), "high": round(hi, 2),
            "center": round(sum(m["value"] for m in g) / len(g), 2),
            "span": round(hi - lo, 2),
            "members": [{"key": m["key"], "label": m["label"],
                         "value": round(m["value"], 2)} for m in g],
            "n": len(g),
            "families": fams,
            # The distinction that matters. Same-family co-location is
            # arithmetic; cross-family is several mechanisms on one price.
            "cross_method": len(fams) > 1,
            "note": (
                f"{len(g)} levels within {round(hi - lo, 2)} handles"
                + (f" from {len(fams)} different methods ({', '.join(fams)}) — one "
                   f"reference with several reasons, not {len(g)} confirmations."
                   if len(fams) > 1 else
                   f" from the same family ({fams[0]}), which is arithmetic rather "
                   f"than confluence.")
            ),
        })

    cross = [c for c in clusters if c["cross_method"]]
    return {
        "available": True,
        "clusters": clusters,
        "n_clusters": len(clusters),
        "n_cross_method": len(cross),
        "tolerance": round(tol, 2),
        "tolerance_basis": "median 5-minute bar" if median_bar else "4% of a normal session range",
        "note": (
            None if not cross else
            "Co-located levels: "
            + "; ".join(f"{c['center']:.2f} ({', '.join(m['label'] for m in c['members'])})"
                        for c in cross[:3])
            + ". Price cannot react to levels closer together than one bar, so each "
              "of these is a single reference carrying several reasons."
        ),
        "caveat": (
            "Clusters are described, not removed. A price that is both a prior high and "
            "an option wall is more likely to matter than one that is only either — but "
            "it is ONE level with several reasons, which is a different claim from "
            "several independent methods agreeing."
        ),
    }
