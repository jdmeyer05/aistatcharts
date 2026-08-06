"""Most-similar-session matching — the "similar day" method from power trading.

Load forecasters do not extrapolate a curve; they find the historical days whose
conditions most resemble today and read what those days did. The same idea ports
to a session, and this platform already has the panel it needs: five years of
5-minute SPY, which is 1,244 complete sessions.

WHAT WAS MEASURED, and what it is allowed to claim
--------------------------------------------------
Walk-forward over 744 out-of-sample sessions, candidates drawn only from
sessions strictly before the one being described, standardised on an expanding
window so no future statistic touches a historical feature.

  SIZE (range multiplier)   MAE 0.393 vs 0.429 for the unconditional forecast,
                            a 8.4% improvement, Wilcoxon p=0.0005. Stable across
                            halves: +8.3% then +8.5%.
  WIDE-DAY FLAG             analogs calling >=1.3x are right 52.9% of the time
                            against a 28.2% base rate — 1.87x lift, on 104 calls,
                            catching 26% of all wide days.
  DIRECTION                 NULL. 53.8% correct against a 53.4% base rate. Every
                            feature set tested lands inside the confidence
                            interval of the base rate. The analog split is
                            reported because a reader will ask, and it is
                            labelled a null because that is what it measured.
  NEXT SESSION              NOT VALIDATED. +3.8% MAE, p=0.051, and the sign
                            flips between halves (-1.3%, then +5.7%). Carried as
                            context with that stated, never as a forecast.

WHAT MADE IT WORSE, measured rather than assumed
------------------------------------------------
Adding CFTC positioning drops significance from p=0.0005 to p=0.13; adding CFTC
and the macro curve together takes it to p=0.52. The reason is mechanical: COT
is WEEKLY, so all five sessions in a week carry identical values. Those columns
cannot separate one day from another, but they still dilute the distance metric
— nine dimensions of noise. Trend and cross-asset blocks hurt for the same
reason. Eleven features beat twenty-six.

News is deliberately NOT a matching dimension. There is no sample on which to
calibrate "geopolitical de-escalation day", so any weight given to it would be
invented — the same reason the headline-multiplier design was rejected in
`es_regime`. Match on the tape; annotate from the feed.
"""
from __future__ import annotations

import logging
from time import time as _now

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_TTL_S = 30 * 60

# Measured on the walk-forward described above. Shipped as constants because the
# card states them, and a statistic quoted on a card must be one that was run.
ACCURACY = {
    "n_out_of_sample": 744,
    "mae_analog": 0.393,
    "mae_baseline": 0.429,
    "mae_gain_pct": 8.4,
    "p_value": 0.0005,
    "wide_precision": 52.9,
    "wide_base_rate": 28.2,
    "wide_lift": 1.87,
    "wide_recall": 26.2,
    "direction_accuracy": 53.8,
    "direction_base": 53.4,
    "next_day_p": 0.051,
}

# The set that won the ablation. Order is irrelevant; membership is not.
_STRUCTURE = ["prior_rng_mult", "prior_close_pos", "prior_trendiness",
              "prior_ret_oc", "gap_pct"]
_VOL = ["rv_10d", "rv_ratio", "vix", "vix_chg5", "vix_term", "vix_vs_rv"]
FEATURES = _STRUCTURE + _VOL

_K = 5              # analogs surfaced to the reader
_K_STAT = 10        # analogs used for the aggregate — validated at 10, not 5
_EMBARGO = 5        # trading days excluded either side of the target
_MIN_POOL = 250
_WIDE = 1.3


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


def _panel() -> pd.DataFrame:
    """One row per session: the outcome columns, then the point-in-time features.

    Every feature is shifted so it describes information available BEFORE the
    session opens. The single exception is `gap_pct`, which is knowable at 09:30
    and is the one legal session-t input.
    """
    hit = _CACHE.get("panel")
    if hit and (_now() - hit[0]) < _TTL_S:
        return hit[1]

    from src.es_baserates import _hourly, _sessions
    s = _sessions(_hourly()).copy()
    if s.empty:
        return pd.DataFrame()
    s.index = pd.to_datetime(s.index).tz_localize(None).normalize()
    s = s.sort_index()

    # ── outcomes ────────────────────────────────────────────────────────
    s["ret_oc"] = (s["close"] - s["open"]) / s["open"] * 100
    s["up"] = (s["close"] > s["open"]).astype(int)
    s["close_pos"] = (s["close"] - s["lo"]) / (s["hi"] - s["lo"])
    s["rng_pct"] = s["rng"] / s["open"] * 100
    s["max_up"] = (s["hi"] - s["open"]) / s["open"] * 100
    s["max_dn"] = (s["open"] - s["lo"]) / s["open"] * 100
    s["trendiness"] = (s["close"] - s["open"]).abs() / s["rng"]
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

    spy = _daily("SPY").reindex(s.index).ffill()
    r1 = spy.pct_change()
    rv10 = r1.rolling(10).std() * np.sqrt(252) * 100
    rv60 = r1.rolling(60).std() * np.sqrt(252) * 100
    s["rv_10d"] = rv10.shift(1)
    s["rv_ratio"] = (rv10 / rv60).shift(1)

    vix = _daily("^VIX").reindex(s.index).ffill()
    v9 = _daily("^VIX9D").reindex(s.index).ffill()
    v3 = _daily("^VIX3M").reindex(s.index).ffill()
    s["vix"] = vix.shift(1)
    s["vix_chg5"] = (vix - vix.shift(5)).shift(1)
    s["vix_term"] = (v9 / v3).shift(1)
    s["vix_vs_rv"] = (vix / rv10).shift(1)

    _CACHE["panel"] = (_now(), s)
    return s


def _nearest(panel: pd.DataFrame, i: int, k: int) -> np.ndarray | None:
    """Indices of the k most similar sessions strictly before `i`.

    The embargo matters more than it looks. Adjacent sessions share most of
    these features by construction — a 5-day return moves one element, VIX
    barely moves overnight — so without it the "most similar day" is reliably
    yesterday, and the match measures autocorrelation rather than resemblance.
    """
    X = panel[FEATURES].to_numpy(dtype=float)
    if i < _MIN_POOL:
        return None
    pool = np.arange(0, max(0, i - _EMBARGO))
    cur = X[i]
    if not np.isfinite(cur).all():
        return None
    sub = X[pool]
    ok = np.isfinite(sub).all(axis=1)
    if ok.sum() < _MIN_POOL:
        return None
    pool, sub = pool[ok], sub[ok]
    # Standardise on the pool as it exists at i. Using full-sample moments would
    # leak later regimes into every historical feature.
    mu, sd = sub.mean(axis=0), sub.std(axis=0)
    sd = np.where(sd < 1e-9, 1.0, sd)
    d = np.linalg.norm((sub - mu) / sd - (cur - mu) / sd, axis=1)
    return pool[np.argsort(d)[:k]]


def _outcome(panel: pd.DataFrame, j: int) -> dict:
    r = panel.iloc[j]
    return {
        "date": panel.index[j].strftime("%Y-%m-%d"),
        "range_mult": _f(r.get("rng_mult")),
        "range_pct": _f(r.get("rng_pct")),
        "ret_oc": _f(r.get("ret_oc")),
        "up": bool(r.get("up")) if pd.notna(r.get("up")) else None,
        "close_pos": _f(r.get("close_pos")),
        "trendiness": _f(r.get("trendiness")),
        "max_up": _f(r.get("max_up")),
        "max_dn": _f(r.get("max_dn")),
    }


def _f(v) -> float | None:
    try:
        return None if v is None or pd.isna(v) else round(float(v), 4)
    except (TypeError, ValueError):
        return None


def session_analogs(k: int = _K) -> dict:
    """The k most similar prior sessions to the one now in progress."""
    try:
        panel = _panel()
    except Exception as e:
        logger.warning(f"analog panel failed: {e}")
        return {"available": False, "reason": f"panel unavailable: {e}"}
    if panel.empty or len(panel) < _MIN_POOL:
        return {"available": False, "reason": "not enough session history"}

    i = len(panel) - 1
    idx_stat = _nearest(panel, i, _K_STAT)
    if idx_stat is None:
        return {"available": False,
                "reason": "today's feature vector is incomplete — a daily series "
                          "has not published yet"}

    shown = idx_stat[:k]
    analogs = []
    for j in shown:
        rec = _outcome(panel, int(j))
        # The session AFTER each analog. Reported as context, never as the
        # next-session forecast — that horizon did not validate.
        rec["next"] = _outcome(panel, int(j) + 1) if int(j) + 1 < len(panel) else None
        analogs.append(rec)

    rm = panel["rng_mult"].to_numpy(dtype=float)[idx_stat]
    rm = rm[np.isfinite(rm)]
    up = panel["up"].to_numpy(dtype=float)[idx_stat]
    up = up[np.isfinite(up)]

    nxt = idx_stat + 1
    nxt = nxt[nxt < len(panel)]
    rm_n = panel["rng_mult"].to_numpy(dtype=float)[nxt]
    rm_n = rm_n[np.isfinite(rm_n)]

    implied = float(np.median(rm)) if len(rm) else None
    return {
        "available": True,
        "session_date": panel.index[i].strftime("%Y-%m-%d"),
        "n_history": int(len(panel)),
        "k_shown": len(analogs),
        "k_scored": int(len(idx_stat)),
        "features": FEATURES,
        "analogs": analogs,
        "today": {
            "implied_range_mult": round(implied, 3) if implied is not None else None,
            "calls_wide": bool(implied is not None and implied >= _WIDE),
            "share_up": round(float(up.mean()), 3) if len(up) else None,
            "median_close_pos": _f(np.median(
                panel["close_pos"].to_numpy(dtype=float)[idx_stat])),
        },
        "next_session": {
            "implied_range_mult": round(float(np.median(rm_n)), 3) if len(rm_n) else None,
            "validated": False,
            "note": ("Shown as context only. On the same walk-forward the next "
                     "session improved on the unconditional forecast by 3.8% at "
                     "p=0.051, and the sign flipped between halves — that is not "
                     "an edge, it is noise with a direction."),
        },
        "accuracy": ACCURACY,
        "caveat": ("Similarity is measured on prior-session structure and the "
                   "volatility complex only. Positioning and macro were tested "
                   "and made it worse; news is annotation, never a matching "
                   "dimension. Direction is a measured null and is printed as "
                   "one."),
    }
