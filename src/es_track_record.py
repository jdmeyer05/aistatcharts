"""How the session-character read has actually done.

Nothing on this card was ever scored. The gate said STAND ASIDE at -7 on
2026-08-03 and no record exists of that, or of what followed — so a module could
be wrong for months and the only way to notice was to remember. The Trump decoder
already solved this with a track record and an auto-validator; the ES card never
got one.

COMPUTED BY REPLAY, NOT BY LOGGING FORWARD. The character read is a
deterministic function of price: range so far divided by the share of its range a
typical session has covered by that hour. So its whole history can be
reconstructed today rather than accumulated over the next six months. That gives
a thousand sessions immediately instead of a handful by March, and it cannot
drift out of sync with the code the way a log written by an older build does.

The limit of the approach, stated because it decides what belongs here: only
modules that are pure functions of PRICE can be replayed. The conditions gate
cannot — it reads dealer gamma, which needs the option chain as it stood that
morning, and that history is not retained. Scoring the gate needs forward
logging, which is a separate job. What is here is what can be answered honestly
today.

WHAT IT MEASURES. For each bucket of the character read: what the session's final
range actually turned out to be, how often it cleared 1.3x, and how often the
session closed up. That last column is included precisely BECAUSE the read makes
no directional claim — it should sit at the base rate in every band, and showing
it lets the reader confirm that rather than take the disclaimer on trust.
"""

from __future__ import annotations

import logging
from time import time as _now

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL_S = 12 * 3600

# The hour the read is quoted at. 10:30 is the first mark where a full bucket has
# closed, and the one an entry decision would actually use.
_MARK = 10.5
_TYP_AT_MARK = 0.688

_BANDS = [
    (1.30, 99.0, "wide"),
    (0.90, 1.30, "normal"),
    (0.00, 0.90, "compressed"),
]


def _build() -> dict:
    from src.es_baserates import _polygon_5m, _to_slots, _SLOTS, _INTRADAY_SYMBOL

    fine = _polygon_5m(_INTRADAY_SYMBOL, 5)
    if fine is None or fine.empty:
        return {}
    fine = fine.copy()
    fine["day"] = fine.index.normalize()
    h = _to_slots(fine)
    full = h.groupby("day")["slot"].nunique()
    fine = fine[fine["day"].isin(set(full[full == len(_SLOTS)].index))]

    sess = fine.groupby("day").agg(hi=("High", "max"), lo=("Low", "min"),
                                   open=("Open", "first"), close=("Close", "last"))
    sess["rng"] = sess["hi"] - sess["lo"]
    sess["normal"] = sess["rng"].shift(1).rolling(20).median()
    sess = sess.dropna()
    sess = sess[(sess["normal"] > 0) & (sess["rng"] > 0)]
    if len(sess) < 300:
        return {}

    rows = []
    for day, g in fine[fine["day"].isin(sess.index)].groupby("day"):
        s = sess.loc[day]
        hrs = (g.index.hour + g.index.minute / 60).values
        upto = g[hrs < _MARK]
        if len(upto) < 6:
            continue
        rng_so = upto["High"].max() - upto["Low"].min()
        if rng_so <= 0:
            continue
        rows.append({
            "read": (rng_so / _TYP_AT_MARK) / s["normal"],   # what the card said
            "actual": s["rng"] / s["normal"],                # what happened
            "predicted_range": rng_so / _TYP_AT_MARK,
            "actual_range": s["rng"],
            "closed_up": bool(s["close"] > s["open"]),
        })

    D = pd.DataFrame(rows)
    if D.empty:
        return {}

    buckets = []
    for lo, hi, label in _BANDS:
        b = D[(D["read"] >= lo) & (D["read"] < hi)]
        if len(b) < 30:
            continue
        err = (b["predicted_range"] / b["actual_range"] - 1) * 100
        buckets.append({
            "band": label,
            "n": int(len(b)),
            "said_x": round(float(b["read"].median()), 2),
            "actual_x": round(float(b["actual"].median()), 2),
            # The calibration question: when it said wide, was it?
            "delivered_wide_pct": round(float((b["actual"] >= 1.3).mean()) * 100, 1),
            "median_abs_err_pct": round(float(np.median(np.abs(err))), 1),
            "median_err_pct": round(float(np.median(err)), 1),
            "closed_up_pct": round(float(b["closed_up"].mean()) * 100, 1),
        })

    overall_err = (D["predicted_range"] / D["actual_range"] - 1) * 100
    return {
        "sessions": int(len(D)),
        "buckets": buckets,
        "base_wide_pct": round(float((D["actual"] >= 1.3).mean()) * 100, 1),
        "base_up_pct": round(float(D["closed_up"].mean()) * 100, 1),
        "median_abs_err_pct": round(float(np.median(np.abs(overall_err))), 1),
        "mark": "10:30",
    }


def character_track_record() -> dict:
    """Calibration of the character read, replayed over the full history."""
    hit = _CACHE.get("tr")
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]
    try:
        t = _build()
    except Exception as e:
        logger.warning(f"character track record failed: {e}")
        t = {}
    if not t:
        out = {"available": False, "reason": "insufficient history"}
    else:
        wide = next((b for b in t["buckets"] if b["band"] == "wide"), None)
        out = {
            "available": True,
            **t,
            "headline": (
                None if not wide else
                (f"When the card called a session wide at {t['mark']}, it ran 1.3x or "
                 f"wider {wide['delivered_wide_pct']:.0f}% of the time against a "
                 f"{t['base_wide_pct']:.0f}% base rate, and its range forecast was off by "
                 f"a median {wide['median_abs_err_pct']:.0f}% (n={wide['n']}).")
            ),
            # THE FIRST THING THIS TRACK RECORD FOUND, and it is a defect in the
            # module it scores. The read is essentially unbiased when it calls a
            # session wide (median error -0.6%) and runs badly low when it calls
            # one compressed (-30.0%: it says 0.62x and the session delivers
            # 0.83x). Quiet mornings revert upward and inverting the median path
            # fraction linearly does not capture that. Surfaced rather than
            # silently corrected — a fitted adjustment on top would be a second
            # model to validate, and the honest interim is to say which end of
            # the scale to trust.
            "bias_note": (
                None if not t["buckets"] else
                "Calibration is not uniform. "
                + "; ".join(
                    f"{b['band']} reads run {abs(b['median_err_pct']):.0f}% "
                    f"{'low' if b['median_err_pct'] < 0 else 'high'}"
                    for b in t["buckets"] if abs(b["median_err_pct"]) >= 5)
                + ". The wide end is the trustworthy one; a compressed read "
                  "understates how much room the session actually has."
            ),
            "direction_note": (
                f"The `closed up` column is why it is here: it should sit at the "
                f"{t['base_up_pct']:.0f}% base rate in every band, because this read makes "
                f"no directional claim. Showing it lets that be checked rather than "
                f"taken on trust."
            ),
            "method": (
                "Replayed, not logged. The read is a deterministic function of price, so "
                "its history is reconstructed from 5-minute bars rather than accumulated "
                "forward — which is why it can be scored on a thousand sessions today. "
                "Modules depending on the option chain as it stood that morning (the "
                "conditions gate, via dealer gamma) cannot be replayed and are not scored "
                "here."
            ),
        }
    _CACHE["tr"] = (_now(), out)
    return out
