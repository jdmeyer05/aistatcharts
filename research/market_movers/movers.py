"""How much each dated event actually moves the tape, relative to a normal day.

THE METRIC, AND WHY IT IS NORMALISED BEFORE IT IS AGGREGATED. A CPI print in
2022 lands in a year where every day was large, and one in 2017 lands in a year
where nothing was. Pooling raw absolute returns across fourteen years therefore
ranks events partly by which regime they happened to fall in. Every session here
is first divided by the trailing 60-session median absolute move, so the unit is
"how big was this day compared with what was normal AT THE TIME". A value of
1.8 means the session ran 1.8x a then-typical day, whether that was 2017 or 2022.

CLOSE-TO-CLOSE, NOT RANGE, AS THE HEADLINE. The releases that matter land at
08:30 ET, an hour before the bell. An RTH high-low range cannot see that
reaction; the previous close to today's close spans it. Range is reported
alongside because it says something different — how much of the move was fought
over during the session rather than gapped — but the ranking is on close-to-close.

NO DIRECTION IS CLAIMED ANYWHERE IN THIS FILE. The question is how big, not
which way. Directional prediction from macro surprises has come back null in
every study run on this platform, and mixing a magnitude result with a direction
result is how a magnitude result gets read as a trade.

THE NULL IS A SHIFTED CALENDAR, NOT RANDOM DAYS. Volatility clusters, and events
are spaced, not scattered — so comparing CPI days against n randomly drawn
sessions tests against a null that could never produce a real calendar, and
returns p-values that are far too small. Instead the entire event calendar is
rotated by a random offset through the session index, preserving both its
spacing and the autocorrelation of the return series. The question that answers
is the right one: is this calendar's alignment with large days better than the
same calendar placed at a random phase?
"""

from __future__ import annotations

import logging
import random
from datetime import date

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_VOL_WINDOW = 60          # trailing sessions for the "normal day" reference
_PERMUTATIONS = 2000
_BOOTSTRAP = 2000


def load_sessions(ticker: str = "SPY", start: str = "2011-06-01") -> pd.DataFrame:
    """Daily bars with the regime-normalised magnitude columns attached.

    ADJUSTED CLOSES, AND THIS IS NOT A DETAIL. SPY goes ex-dividend on the third
    Friday of March, June, September and December — which is triple witching, on
    57 of the 58 witching days in this sample. On unadjusted closes each of those
    days carries a mechanical drop of roughly a quarter of a percent that has
    nothing to do with anyone trading, against a typical absolute move near half
    a percent. Measured that way, witching ranked first in this study by a margin
    it had partly been handed. `auto_adjust=True` removes the dividend step.

    yfinance is called through `Ticker().history()`, never `download()` — the
    latter is not thread-safe and this module is imported by code that fans out.
    """
    import yfinance as yf
    df = yf.Ticker(ticker).history(start=start, interval="1d", auto_adjust=True)
    if df is None or df.empty:
        raise RuntimeError(f"no bars for {ticker}")
    df = df[["Open", "High", "Low", "Close"]].copy()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()

    prev = df["Close"].shift(1)
    df["ret"] = df["Close"] / prev - 1.0
    df["abs_ret"] = df["ret"].abs()
    df["range_pct"] = (df["High"] - df["Low"]) / prev

    # Trailing reference EXCLUDES the day itself — including it would let a big
    # day inflate its own denominator and shrink toward 1.
    df["ref_abs"] = df["abs_ret"].shift(1).rolling(_VOL_WINDOW).median()
    df["ref_range"] = df["range_pct"].shift(1).rolling(_VOL_WINDOW).median()
    df["rel_abs"] = df["abs_ret"] / df["ref_abs"]
    df["rel_range"] = df["range_pct"] / df["ref_range"]

    return df.dropna(subset=["rel_abs", "rel_range"])


def snap_to_sessions(dates: list[date], index: pd.DatetimeIndex,
                     direction: str = "forward") -> list[int]:
    """Map calendar dates to session positions.

    A release published on a day the market did not trade is reacted to on the
    next session, so releases snap FORWARD. Month end and quarter end are the
    opposite: the last trading day of September IS quarter end, so structural
    dates snap BACKWARD. Getting this backwards would push every quarter-end
    observation onto the first day of the next quarter.
    """
    out = []
    n = len(index)
    for d in dates:
        ts = pd.Timestamp(d)
        if direction == "forward":
            pos = index.searchsorted(ts, side="left")
            if pos < n:
                out.append(int(pos))
        else:
            pos = index.searchsorted(ts, side="right") - 1
            if pos >= 0:
                out.append(int(pos))
    return sorted(set(out))


def _median(a) -> float:
    return float(np.median(a)) if len(a) else float("nan")


def _bootstrap_ci(values: np.ndarray, iters: int = _BOOTSTRAP,
                  seed: int = 17) -> tuple[float, float]:
    if len(values) < 3:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    meds = np.median(rng.choice(values, size=(iters, len(values)), replace=True), axis=1)
    return (float(np.percentile(meds, 2.5)), float(np.percentile(meds, 97.5)))


def _rotation_pvalue(series: np.ndarray, positions: list[int],
                     observed: float, iters: int = _PERMUTATIONS,
                     seed: int = 23) -> float:
    """p from rotating the whole calendar, preserving its spacing.

    One-sided: the hypothesis is that event days are LARGER than a random phase
    of the same calendar. A two-sided test here would be answering a question
    nobody asked.
    """
    n = len(series)
    if not positions or n < 100:
        return float("nan")
    rng = random.Random(seed)
    pos = np.asarray(positions)
    hits = 0
    for _ in range(iters):
        shift = rng.randrange(n)
        shifted = (pos + shift) % n
        if np.median(series[shifted]) >= observed:
            hits += 1
    return (hits + 1) / (iters + 1)


def benjamini_hochberg(pvals: dict[str, float], alpha: float = 0.10) -> dict[str, bool]:
    """Which events survive multiple testing across the whole universe.

    Twenty-three events tested at 0.05 yields roughly one spurious winner by
    construction. BH at 0.10 on the false-discovery rate is the same control the
    factor work on this platform uses, and it is applied to the full universe,
    not to the survivors.
    """
    items = [(k, v) for k, v in pvals.items() if v == v]     # drop NaN
    items.sort(key=lambda kv: kv[1])
    m = len(items)
    survivors: dict[str, bool] = {k: False for k in pvals}
    cutoff = 0
    for i, (_, p) in enumerate(items, start=1):
        if p <= alpha * i / m:
            cutoff = i
    for k, _ in items[:cutoff]:
        survivors[k] = True
    return survivors


def measure_event(df: pd.DataFrame, dates: list[date], snap: str = "forward",
                  horizons: tuple[int, ...] = (0, 1, 2, 3)) -> dict:
    """One event type: magnitude on the day, persistence after it, and a p-value."""
    idx = df.index
    positions = snap_to_sessions(dates, idx, snap)
    rel = df["rel_abs"].to_numpy()
    rel_rng = df["rel_range"].to_numpy()
    n_sessions = len(rel)

    day0 = [p for p in positions if 0 <= p < n_sessions]
    if len(day0) < 12:
        return {"n": len(day0), "insufficient": True}

    obs = _median(rel[day0])
    lo, hi = _bootstrap_ci(rel[day0])

    persistence = {}
    for h in horizons:
        shifted = [p + h for p in day0 if p + h < n_sessions]
        persistence[f"t+{h}"] = round(_median(rel[shifted]), 4) if shifted else None

    # Quiet-day contrast: sessions with no event of ANY type in the universe are
    # supplied by the caller through df["is_event"], because "a normal day" in a
    # calendar this dense is not the same thing as "the average day".
    quiet = None
    if "is_event" in df.columns:
        q = rel[~df["is_event"].to_numpy()]
        if len(q) > 50:
            quiet = round(float(np.median(q)), 4)

    return {
        "n": len(day0),
        "rel_abs_median": round(obs, 4),
        "rel_abs_ci95": [round(lo, 4), round(hi, 4)],
        "rel_range_median": round(_median(rel_rng[day0]), 4),
        "quiet_day_median": quiet,
        "vs_quiet": round(obs / quiet, 4) if quiet else None,
        "persistence": persistence,
        "p_rotation": round(_rotation_pvalue(rel, day0, obs), 4),
        "share_over_1_5x": round(float(np.mean(rel[day0] >= 1.5)), 4),
        "share_over_2x": round(float(np.mean(rel[day0] >= 2.0)), 4),
    }


def rank_universe(df: pd.DataFrame, universe: dict[str, dict],
                  alpha: float = 0.10) -> list[dict]:
    """Every event measured, ranked by magnitude, with FDR applied across all."""
    # Mark every session touched by any event, so the quiet-day baseline means
    # what it says.
    idx = df.index
    is_event = np.zeros(len(idx), dtype=bool)
    for meta in universe.values():
        snap = "backward" if meta.get("family") == "structural" else "forward"
        for p in snap_to_sessions(meta["dates"], idx, snap):
            if 0 <= p < len(idx):
                is_event[p] = True
    df = df.copy()
    df["is_event"] = is_event

    rows = []
    pvals: dict[str, float] = {}
    for label, meta in universe.items():
        snap = "backward" if meta.get("family") == "structural" else "forward"
        res = measure_event(df, meta["dates"], snap)
        if res.get("insufficient"):
            logger.info(f"{label}: only {res['n']} sessions, skipped")
            continue
        row = {"event": label, "family": meta.get("family"),
               "source": meta.get("source"), "release_time_et": meta.get("release_time_et"),
               **res}
        rows.append(row)
        pvals[label] = res["p_rotation"]

    survivors = benjamini_hochberg(pvals, alpha=alpha)
    for row in rows:
        row["survives_fdr"] = bool(survivors.get(row["event"], False))

    rows.sort(key=lambda r: -r["rel_abs_median"])
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def rank_by_year(df: pd.DataFrame, universe: dict[str, dict],
                 min_n: int = 6) -> dict[str, list[dict]]:
    """The same ranking, recomputed inside each calendar year.

    This is the half the headline table cannot show: which event mattered most
    is not a constant. A single fourteen-year ranking silently averages the year
    CPI was the only thing anyone traded with the years it was background noise.
    """
    out: dict[str, list[dict]] = {}
    for year in sorted({d.year for d in df.index}):
        sub = df[df.index.year == year]
        if len(sub) < 100:
            continue
        rows = []
        for label, meta in universe.items():
            dates = [d for d in meta["dates"] if d.year == year]
            if len(dates) < min_n:
                continue
            snap = "backward" if meta.get("family") == "structural" else "forward"
            positions = snap_to_sessions(dates, sub.index, snap)
            if len(positions) < min_n:
                continue
            vals = sub["rel_abs"].to_numpy()[positions]
            rows.append({"event": label, "n": len(positions),
                         "rel_abs_median": round(float(np.median(vals)), 4)})
        rows.sort(key=lambda r: -r["rel_abs_median"])
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
        out[str(year)] = rows
    return out


def rank_stability(by_year: dict[str, list[dict]], top_k: int = 10) -> list[dict]:
    """How much each event's rank moves around, and how often it makes the top K.

    An event with a good average rank and a wild spread is not the same
    proposition as one that sits at the same place every year, and a single
    ranking cannot tell them apart.
    """
    ranks: dict[str, list[int]] = {}
    for _, rows in by_year.items():
        for r in rows:
            ranks.setdefault(r["event"], []).append(r["rank"])

    out = []
    for event, rs in ranks.items():
        arr = np.array(rs)
        out.append({
            "event": event,
            "years": len(rs),
            "mean_rank": round(float(arr.mean()), 2),
            "best_rank": int(arr.min()),
            "worst_rank": int(arr.max()),
            "rank_sd": round(float(arr.std(ddof=1)), 2) if len(rs) > 1 else None,
            "years_in_top_k": int((arr <= top_k).sum()),
            "share_in_top_k": round(float((arr <= top_k).mean()), 3),
        })
    out.sort(key=lambda r: r["mean_rank"])
    return out
