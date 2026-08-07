"""Most-similar-session matching — the "similar day" method from power trading.

Load forecasters do not extrapolate a curve; they find the historical days whose
conditions most resembled today and read what those days did. This platform has
the panel that needs: five years of 5-minute SPY, 1,244 complete sessions.

WHAT WAS MEASURED
-----------------
Walk-forward over 744 out-of-sample sessions. Candidates drawn only from
sessions strictly before the one being described, standardised on an expanding
window so no future statistic touches a historical feature.

  size        MAE 0.389 vs 0.429 unconditional (+9.3%), Wilcoxon p=0.0004,
              stable across halves (+9.6% then +9.1%)
  wide days   calls of >=1.3x are right 56.3% against a 28.2% base rate —
              2.00x lift on 103 calls, catching 27.6% of all wide sessions
  direction   NULL, and slightly BELOW the base rate: 50.7% against 53.4%.
              Printed as a null rather than omitted.
  next day    NOT VALIDATED — +3.8% at p=0.051 with the sign flipping between
              halves. Carried as context with that stated.

FOUR THINGS TRIED, TWO KEPT
---------------------------
1. KEPT — relevance weighting. Plain Euclidean weights every feature equally,
   which is why extra columns kept hurting: they entered at full strength. With
   weights the prior-session SHAPE and the unused panel columns become additive
   (+8.4% -> +9.3%, lift 1.87x -> 2.00x). Unweighted, the same feature set is
   WORSE than the incumbent at +6.2% — the weighting is what makes them pay.

2. KEPT — the intraday blend, but only early. Averaging the analog estimate with
   the path-implied one beats path-implied alone at 10:30 (MAE 0.278 vs 0.298,
   p<0.0001) and 11:30 (0.241 vs 0.255, p=0.0001, and the hit rate inside +-25%
   jumps 59.8% -> 70.6%). By 12:30 it is a tie and by 13:30 the blend is
   significantly WORSE (0.145 vs 0.133, p=0.0031). Mechanical, not mysterious:
   once 87% of the range is in, range-so-far is nearly the answer and anything
   added to it is noise. So the blend is switched off after 11:30.

3. REJECTED — abstaining when nothing resembles today. The hypothesis was that
   a poor match should refuse to answer. Measured by match-distance quartile,
   the gain over the unconditional forecast is LARGEST on the furthest quartile
   (17.0%) and smallest on the closest (2.8%): unusual days are exactly where
   the naive baseline is worst and the analog earns most. Abstaining would have
   discarded its best cases.

4. REJECTED — CFTC positioning and macro, twice. At equal weight they take
   p from 0.0005 to 0.1332; weighted, and added to the best set, still +5.9% at
   p=0.0537 against +9.3% at p=0.0004 without them. The reason is mechanical:
   COT is WEEKLY, so all five sessions in a week carry identical values. Those
   columns cannot separate one day from another while still consuming distance.

News is deliberately not a matching dimension. There is no sample on which to
calibrate "geopolitical de-escalation day", so any weight given to it would be
invented — the reason the headline-multiplier design was rejected in es_regime.
Match on the tape, annotate from the feed.
"""
from __future__ import annotations

import logging
from time import time as _now

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL_S = 30 * 60

ACCURACY = {
    "n_out_of_sample": 744,
    "mae_analog": 0.389,
    "mae_baseline": 0.429,
    "mae_gain_pct": 9.3,
    "p_value": 0.0004,
    "halves_gain_pct": [9.6, 9.1],
    "wide_precision": 56.3,
    "wide_base_rate": 28.2,
    "wide_lift": 2.00,
    "wide_recall": 27.6,
    "direction_accuracy": 50.7,
    "direction_base": 53.4,
    "next_day_p": 0.051,
    # blend vs path-implied alone, by slot
    "blend": {"10:30": {"blend_mae": 0.278, "path_mae": 0.298, "hit": 58.7, "p": 0.0000},
              "11:30": {"blend_mae": 0.241, "path_mae": 0.255, "hit": 70.6, "p": 0.0001}},
}

_SLOTS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]
_BLEND_SLOTS = ("10:30", "11:30")     # measured; switched off after 11:30

_STRUCTURE = ["prior_rng_mult", "prior_close_pos", "prior_trendiness",
              "prior_ret_oc", "gap_pct"]
_VOL = ["rv_10d", "rv_ratio", "vix", "vix_chg5", "vix_term", "vix_vs_rv"]
_SHAPE = [f"prior_shape_{s}" for s in _SLOTS[:5]]
_UNUSED = ["prior_hi_slot", "prior_lo_slot", "prior_ib_share", "dow"]
FEATURES = _STRUCTURE + _VOL + _SHAPE + _UNUSED

_K, _K_STAT, _EMBARGO, _MIN_POOL, _WIDE = 5, 10, 5, 250, 1.3


def _daily(sym: str, years: int = 6) -> pd.Series:
    import yfinance as yf
    h = yf.Ticker(sym).history(period=f"{years}y", interval="1d", auto_adjust=False)
    if h is None or not len(h):
        return pd.Series(dtype=float)
    idx = pd.to_datetime(h.index)
    try:
        idx = idx.tz_localize(None)
    except (TypeError, AttributeError):
        idx = idx.tz_convert(None)
    return pd.Series(h["Close"].values, index=idx.normalize()).sort_index()


def _panel() -> tuple[pd.DataFrame, pd.Series]:
    """One row per session: outcomes, then point-in-time features.

    Returns the panel and the median shape curve (fraction of the final range a
    typical session has covered by the end of each slot) — the second is what
    the intraday path-implied estimate divides by.
    """
    hit = _CACHE.get("panel")
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]

    from src.es_baserates import _hourly, _sessions
    h = _hourly()
    h = h[~h.index.duplicated(keep="first")]
    s = _sessions(h).copy()
    if s.empty:
        return pd.DataFrame(), pd.Series(dtype=float)
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    s = s.sort_index()

    # ── per-slot cumulative range: the session's SHAPE ───────────────────
    cum, hl = {}, {}
    for day, g in h.groupby("day"):
        g = g.set_index("slot").reindex(_SLOTS)
        c = (g["High"].cummax() - g["Low"].cummin()).values
        if not np.isfinite(c[-1]) or c[-1] <= 0:
            continue
        cum[day] = c
        hl[day] = (float(np.nanargmax(g["High"].values)),
                   float(np.nanargmin(g["Low"].values)))
    cum = pd.DataFrame(cum).T
    if cum.empty:
        return pd.DataFrame(), pd.Series(dtype=float)
    cum.columns = _SLOTS
    cum.index = pd.to_datetime(cum.index).tz_localize(None).normalize()
    cum = cum.sort_index().reindex(s.index)
    hl = pd.DataFrame(hl).T
    hl.columns = ["hi_slot_i", "lo_slot_i"]
    hl.index = pd.to_datetime(hl.index).tz_localize(None).normalize()
    hl = hl.sort_index().reindex(s.index)

    # ── outcomes ────────────────────────────────────────────────────────
    s["ret_oc"] = (s["close"] - s["open"]) / s["open"] * 100
    s["up"] = (s["close"] > s["open"]).astype(int)
    s["close_pos"] = (s["close"] - s["lo"]) / (s["hi"] - s["lo"])
    s["rng_pct"] = s["rng"] / s["open"] * 100
    s["trendiness"] = (s["close"] - s["open"]).abs() / s["rng"]
    s["max_up"] = (s["hi"] - s["open"]) / s["open"] * 100
    s["max_dn"] = (s["open"] - s["lo"]) / s["open"] * 100
    # `.shift(1)` is load-bearing: a session normalised by a window containing
    # itself is graded against a number it helped set.
    s["norm_rng_pct"] = s["rng_pct"].rolling(20).median().shift(1)
    s["rng_mult"] = s["rng_pct"] / s["norm_rng_pct"]

    # ── features ────────────────────────────────────────────────────────
    s["prior_rng_mult"] = s["rng_mult"].shift(1)
    s["prior_close_pos"] = s["close_pos"].shift(1)
    s["prior_trendiness"] = s["trendiness"].shift(1)
    s["prior_ret_oc"] = s["ret_oc"].shift(1)
    prev_close = s["close"].shift(1)
    s["gap_pct"] = (s["open"] - prev_close) / prev_close * 100

    shape = cum.div(cum[_SLOTS[-1]], axis=0)
    for i, sl in enumerate(_SLOTS[:5]):
        s[f"prior_shape_{sl}"] = shape[sl].shift(1)
    # The FEATURE is the prior session's; the REPORTED value is the session's
    # own. Kept as two columns rather than one because reading the shifted
    # column back out to describe an analog reports the day BEFORE the analog
    # — which is what it did first, and every analog printed "hi@09:30".
    s["hi_slot_own"] = hl["hi_slot_i"]
    s["lo_slot_own"] = hl["lo_slot_i"]
    s["prior_hi_slot"] = hl["hi_slot_i"].shift(1)
    s["prior_lo_slot"] = hl["lo_slot_i"].shift(1)
    s["prior_ib_share"] = (s["ib_rng"] / s["rng"]).shift(1)
    s["dow"] = s.index.dayofweek

    spy = _daily("SPY").reindex(s.index).ffill()
    r1 = spy.pct_change()
    rv10 = r1.rolling(10).std() * np.sqrt(252) * 100
    rv60 = r1.rolling(60).std() * np.sqrt(252) * 100
    s["rv_10d"] = rv10.shift(1)
    s["rv_ratio"] = (rv10 / rv60).shift(1)
    vix, v9, v3 = (_daily("^VIX").reindex(s.index).ffill(),
                   _daily("^VIX9D").reindex(s.index).ffill(),
                   _daily("^VIX3M").reindex(s.index).ffill())
    s["vix"] = vix.shift(1)
    s["vix_chg5"] = (vix - vix.shift(5)).shift(1)
    s["vix_term"] = (v9 / v3).shift(1)
    s["vix_vs_rv"] = (vix / rv10).shift(1)

    # range so far, as a multiple of normal, at each slot — the intraday input
    for sl in _SLOTS:
        s[f"sofar_{sl}"] = (cum[sl] / s["open"] * 100) / s["norm_rng_pct"]

    out = (s, shape.median())
    _CACHE["panel"] = (_now(), out)
    return out


def _weights(X: np.ndarray, y: np.ndarray, upto: int) -> np.ndarray:
    """Relevance weights from the pool that exists at `upto`.

    |Spearman| against the target, normalised to mean 1 and clipped so nothing
    is switched fully off or allowed to dominate. Equal weighting is what made
    every added feature harmful; this is what lets the shape and panel columns
    pay their way.
    """
    from scipy import stats as _st
    sub, ys = X[:max(0, upto - _EMBARGO)], y[:max(0, upto - _EMBARGO)]
    ok = np.isfinite(sub).all(axis=1) & np.isfinite(ys)
    if ok.sum() < _MIN_POOL:
        return np.ones(X.shape[1])
    sub, ys = sub[ok], ys[ok]
    w = np.array([abs(_st.spearmanr(sub[:, j], ys).statistic)
                  for j in range(sub.shape[1])])
    w = np.nan_to_num(w, nan=0.0)
    return np.clip(w / (w.mean() or 1.0), 0.25, 3.0)


def _nearest(X, i, k, w):
    """Indices of the k most similar sessions strictly before `i`.

    The embargo matters more than it looks: adjacent sessions share most of
    these features by construction, so without it the "most similar day" is
    reliably yesterday and the match measures autocorrelation, not resemblance.
    """
    if i < _MIN_POOL:
        return None, None
    cur = X[i]
    if not np.isfinite(cur).all():
        return None, None
    pool = np.arange(0, max(0, i - _EMBARGO))
    sub = X[pool]
    ok = np.isfinite(sub).all(axis=1)
    if ok.sum() < _MIN_POOL:
        return None, None
    pool, sub = pool[ok], sub[ok]
    mu, sd = sub.mean(axis=0), sub.std(axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    d = np.linalg.norm((sub - mu) / sd * w - (cur - mu) / sd * w, axis=1)
    o = np.argsort(d)[:k]
    return pool[o], d[o]


def _f(v):
    try:
        return None if v is None or pd.isna(v) else round(float(v), 4)
    except (TypeError, ValueError):
        return None


def _outcome(panel, j):
    r = panel.iloc[j]
    return {"date": panel.index[j].strftime("%Y-%m-%d"),
            "range_mult": _f(r.get("rng_mult")), "ret_oc": _f(r.get("ret_oc")),
            "up": bool(r.get("up")) if pd.notna(r.get("up")) else None,
            "close_pos": _f(r.get("close_pos")), "trendiness": _f(r.get("trendiness")),
            "hi_slot": _SLOTS[int(r["hi_slot_own"])] if pd.notna(r.get("hi_slot_own")) else None,
            "lo_slot": _SLOTS[int(r["lo_slot_own"])] if pd.notna(r.get("lo_slot_own")) else None,
            "max_up": _f(r.get("max_up")), "max_dn": _f(r.get("max_dn"))}


def session_analogs(k: int = _K, slot: str | None = None) -> dict:
    """The k most similar prior sessions, and what they did.

    `slot` selects the intraday blend. Passing a slot the blend was not
    validated at falls back to the pre-open estimate rather than blending
    anyway — after 11:30 the blend measured significantly worse than
    path-implied alone.
    """
    try:
        panel, med_shape = _panel()
    except Exception as e:
        logger.warning(f"analog panel failed: {e}")
        return {"available": False, "reason": f"panel unavailable: {e}"}
    if panel is None or panel.empty or len(panel) < _MIN_POOL:
        return {"available": False, "reason": "not enough session history"}

    i = len(panel) - 1
    y = panel["rng_mult"].to_numpy(dtype=float)

    use_blend = slot in _BLEND_SLOTS
    cols = FEATURES + ([f"sofar_{slot}"] if use_blend else [])
    X = panel[cols].to_numpy(dtype=float)
    w = _weights(X, y, i)
    idx, dist = _nearest(X, i, _K_STAT, w)
    if idx is None:
        return {"available": False,
                "reason": "today's feature vector is incomplete — a daily "
                          "series has not published yet"}

    analogs = []
    for j in idx[:k]:
        rec = _outcome(panel, int(j))
        rec["next"] = _outcome(panel, int(j) + 1) if int(j) + 1 < len(panel) else None
        analogs.append(rec)

    a = y[idx][np.isfinite(y[idx])]
    up = panel["up"].to_numpy(dtype=float)[idx]
    up = up[np.isfinite(up)]
    nxt = idx + 1
    nxt = nxt[nxt < len(panel)]
    a_n = y[nxt][np.isfinite(y[nxt])]

    analog_est = float(np.median(a)) if len(a) else None
    path_est = None
    if use_blend:
        sofar = panel[f"sofar_{slot}"].to_numpy(dtype=float)[i]
        frac = float(med_shape.get(slot, np.nan))
        if np.isfinite(sofar) and np.isfinite(frac) and frac > 0:
            path_est = sofar / frac
    blended = (analog_est + path_est) / 2 if (use_blend and path_est and analog_est) else analog_est

    return {
        "available": True,
        "session_date": panel.index[i].strftime("%Y-%m-%d"),
        "n_history": int(len(panel)),
        "k_shown": len(analogs), "k_scored": int(len(idx)),
        "features": cols,
        "mode": "intraday blend" if (use_blend and path_est) else "pre-open",
        "slot": slot if use_blend else None,
        "analogs": analogs,
        "today": {
            "implied_range_mult": round(blended, 3) if blended is not None else None,
            "analog_only": round(analog_est, 3) if analog_est is not None else None,
            "path_implied": round(path_est, 3) if path_est is not None else None,
            "calls_wide": bool(blended is not None and blended >= _WIDE),
            # The SPREAD of the analogs, not just their middle. Ten sessions
            # agreeing on 1.1x and ten spanning 0.6-2.1x are different claims,
            # and a lone median cannot tell them apart.
            "p25": _f(np.percentile(a, 25)) if len(a) else None,
            "p75": _f(np.percentile(a, 75)) if len(a) else None,
            "share_up": round(float(up.mean()), 3) if len(up) else None,
            "median_distance": _f(np.median(dist)) if dist is not None else None,
        },
        "next_session": {
            "implied_range_mult": round(float(np.median(a_n)), 3) if len(a_n) else None,
            "validated": False,
            "note": ("Context only. On the same walk-forward the next session "
                     "improved on the unconditional forecast by 3.8% at p=0.051 "
                     "with the sign flipping between halves — noise with a "
                     "direction, not an edge."),
        },
        "accuracy": ACCURACY,
        "caveat": ("Matched on prior-session structure, its intraday shape and "
                   "the volatility complex, each weighted by measured relevance. "
                   "Positioning and macro were tested twice and made it worse. "
                   "Direction is a measured null and is printed as one."),
    }
