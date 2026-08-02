"""S&P 500 valuation metrics scraped from multpl.com.

Deliberately NOT wired into the macro pressure scorecard. That board scores on
the z-score of recent CHANGE, which is right for rates, credit and vol — a
level that has been elevated for a year is already discounted. Valuation
inverts that assumption: CAPE sits at an extreme for years at a time, so
scored on change it would read "neutral" almost permanently and tell you
nothing. Valuation is a LEVEL signal, so it gets level treatment — current
value against its own long-run mean and median.

It is also not a timing signal, and the card says so. Rich valuation raises
the consequence of a drawdown; it does not predict one.

WHAT THIS BLOCK CAN SAY ABOUT A SESSION — measured 2026-08-02, and it is one
thing only. A multiple is a slow state variable and carries nothing about today.
The one number here that moves daily is the earnings yield against the 10-year,
so that is computed (`rate_context`) along with the thing it is actually a
proxy for: how much of the index's daily move rates are currently explaining.

The obvious hypothesis was tested and REJECTED. "A thin equity risk premium
makes equities more rate-sensitive" sorts beautifully — beta to the 10y runs
-0.014 in the thin-ERP tercile against +0.080 in the wide one across 9,077 days.
It is an artefact of collinearity: ERP is earnings yield MINUS the 10y, so
sorting on it mostly sorts on the level of rates. Put both in one regression
with HAC(60) errors and ERP collapses to t = -0.94 while the rate level holds
t = -7.66, and the tercile gap flips sign by decade (+0.038 in the 2000s,
-0.091 in the 2020s). So ERP is reported as a level with its own percentile and
is NOT claimed to condition anything.

The rate beta itself is the session-relevant number, because it is measured
rather than inferred: on 2026-07-30 it sat at -0.108% of SPX per basis point —
a 10bp move in the 10-year mapping to roughly 1.1% on the index — at the 4th
percentile of five years, with rates explaining 30% of daily variance.

SOURCE FRAGILITY: multpl publishes as HTML with no API, so this parses a
specific page structure. Every field is independently optional and a failed
metric is dropped rather than failing the set, so a layout change degrades the
block instead of taking it down.
"""

from __future__ import annotations

import logging
import re
import statistics
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

_UA = "Mozilla/5.0 (compatible; aistatcharts/1.0)"
_TIMEOUT = 15


@dataclass(frozen=True)
class Metric:
    key: str
    slug: str
    label: str
    unit: str        # "x" (multiple) | "pct"
    # Does a HIGH reading mean expensive? Dividend and earnings yield invert:
    # a high yield is cheap, a high P/E is expensive.
    high_is_expensive: bool
    why: str


METRICS: list[Metric] = [
    Metric("cape", "shiller-pe", "Shiller CAPE", "x", True,
           "Price against 10 years of inflation-adjusted earnings — smooths the cycle out of the "
           "denominator, which is why it reads high for years rather than spiking."),
    Metric("pe", "s-p-500-pe-ratio", "Trailing P/E", "x", True,
           "Price against the last twelve months of reported earnings."),
    Metric("earnings_yield", "s-p-500-earnings-yield", "Earnings yield", "pct", False,
           "The inverse of P/E, in yield terms — directly comparable to the 10-year, which is how "
           "the equity risk premium is framed."),
    Metric("div_yield", "s-p-500-dividend-yield", "Dividend yield", "pct", False,
           "Cash returned against price. Structurally lower than history because payout shifted "
           "toward buybacks, so the long-run mean flatters it."),
    Metric("price_to_book", "s-p-500-price-to-book", "Price / book", "x", True,
           "Price against balance-sheet equity. Drifts up over decades as the index gets more "
           "asset-light, so read the trend rather than the level."),
    Metric("price_to_sales", "s-p-500-price-to-sales", "Price / sales", "x", True,
           "Price against revenue — the hardest line to manage, so it's the least flattering of "
           "the multiples."),
]

_NUM = r"(-?[\d,]+\.?\d*)"

# Full monthly history — ~1,867 observations back to 1871 for CAPE. The #current
# block alone gives mean/median/min/max, which cannot answer "how unusual is
# this": a premium to the median says how FAR from typical, never how RARE.
_RECENT_YEARS = 30
_MIN_HISTORY = 120        # ten years of months before a percentile means anything


def _clean_cell(raw: str) -> str:
    # Order matters, painfully. Every cell is "\n&#x2002;\n40.91\n": the
    # en-space is a LITERAL entity, not decoded, so searching for a number
    # before stripping it returns 2002 — from the entity — for every row in the
    # table. That parses cleanly, fills the column, and puts CAPE at the 0th
    # percentile of a history made entirely of the number 2002. Strip entities
    # first, THEN take the number, which is what handles the "%" the yield
    # series carries.
    txt = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"&(#x?[0-9a-fA-F]+|\w+);", "", txt).replace(",", "").strip()


def _fetch_history_dated(mt: Metric) -> list[tuple[str, float]]:
    """(date, value) pairs, newest first. Empty on any failure.

    The dates are needed by `rate_context`, which has to line the monthly
    earnings yield up against a daily Treasury series. Percentiles never needed
    them, which is why they used to be parsed and thrown away.
    """
    try:
        r = requests.get(f"https://www.multpl.com/{mt.slug}/table/by-month",
                         headers={"User-Agent": _UA}, timeout=_TIMEOUT * 2)
        if r.status_code != 200:
            logger.warning(f"multpl {mt.slug} history: http {r.status_code}")
            return []
        pairs = re.findall(
            r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>",
            r.text, re.S | re.I)
        out: list[tuple[str, float]] = []
        for d_raw, v_raw in pairs:
            m = re.search(r"-?\d+(?:\.\d+)?", _clean_cell(v_raw))
            if m:
                out.append((_clean_cell(d_raw), float(m.group())))
        return out
    except Exception as e:
        logger.warning(f"multpl {mt.slug} history failed: {e}")
        return []


def _fetch_history(mt: Metric) -> list[float]:
    """Monthly observations, newest first. Empty list on any failure."""
    return [v for _, v in _fetch_history_dated(mt)]


def _distribution(values: list[float], current: float, high_is_expensive: bool) -> dict:
    """Where the current reading sits in its own history.

    Percentile leads because it is distribution-free, and these series are badly
    behaved: right-skewed, and so autocorrelated that 1,867 months hold far fewer
    independent observations than that number suggests. The z-score is carried
    because it is the thing people ask for, and labelled for the same reason it
    should not be leaned on.

    Also computed over the recent era. Several of these drift structurally —
    dividend yield fell as payout moved to buybacks, price/book rises as the
    index gets asset-light — so a percentile against 1871 partly measures how
    the market's composition changed rather than how its price did.
    """
    if len(values) < _MIN_HISTORY:
        return {}

    def _pct(sample: list[float]) -> float:
        raw = sum(1 for v in sample if v <= current) / len(sample) * 100
        # Direction matters: a HIGH dividend yield is cheap. The reported
        # percentile always answers "how expensive", never "how large".
        return raw if high_is_expensive else 100 - raw

    out: dict = {"percentile": round(_pct(values), 1), "n_months": len(values)}
    recent = values[:_RECENT_YEARS * 12]
    if len(recent) >= _MIN_HISTORY:
        out["percentile_recent"] = round(_pct(recent), 1)
        out["recent_years"] = _RECENT_YEARS
    try:
        mean, sd = statistics.fmean(values), statistics.stdev(values)
        if sd > 0:
            z = (current - mean) / sd
            out["z_score"] = round(z if high_is_expensive else -z, 2)
            out["sd"] = round(sd, 2)
    except statistics.StatisticsError:
        pass
    return out


def _parse_current_block(html: str) -> dict | None:
    """Pull current / mean / median / min / max out of multpl's #current block."""
    m = re.search(r'id=["\']current["\'](.{0,900})', html, re.S)
    if not m:
        return None
    # Strip tags once; the block is a flat run of labelled numbers.
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", m.group(1)))

    out: dict = {}
    cur = re.search(r":\s*" + _NUM, text)
    if not cur:
        return None
    out["value"] = float(cur.group(1).replace(",", ""))

    for field, pat in (("mean", "Mean"), ("median", "Median"),
                       ("min", "Min"), ("max", "Max")):
        f = re.search(pat + r":\s*" + _NUM, text)
        if f:
            out[field] = float(f.group(1).replace(",", ""))

    d = re.search(r"([A-Z][a-z]{2}\s+\d{1,2},?\s+\d{4})", text)
    if d:
        out["asof_text"] = d.group(1)
    return out


def _fetch_metric(mt: Metric) -> dict | None:
    try:
        r = requests.get(f"https://www.multpl.com/{mt.slug}",
                         headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        if r.status_code != 200:
            logger.warning(f"multpl {mt.slug}: http {r.status_code}")
            return None
        parsed = _parse_current_block(r.text)
        if not parsed:
            logger.warning(f"multpl {mt.slug}: could not parse #current block")
            return None
    except Exception as e:
        logger.warning(f"multpl {mt.slug} failed: {e}")
        return None

    value = parsed["value"]
    mean = parsed.get("mean")
    median = parsed.get("median")

    # Premium to the long-run median, expressed the direction a reader expects:
    # positive = more expensive than history, whichever way the metric points.
    premium = None
    if median not in (None, 0):
        raw = (value / median - 1) * 100
        premium = raw if mt.high_is_expensive else -raw

    # The distribution is a second request per metric. It fails independently —
    # a metric keeps its premium-to-median even if the history page moves.
    dist = _distribution(_fetch_history(mt), value, mt.high_is_expensive)

    return {
        "key": mt.key,
        "label": mt.label,
        "unit": mt.unit,
        "why": mt.why,
        "value": round(value, 2),
        **dist,
        "mean": round(mean, 2) if mean is not None else None,
        "median": round(median, 2) if median is not None else None,
        "min": parsed.get("min"),
        "max": parsed.get("max"),
        "premium_to_median_pct": round(premium, 1) if premium is not None else None,
        "asof_text": parsed.get("asof_text"),
    }


_BETA_WINDOW = 60          # trading days
_BETA_PCTILE_YEARS = 5


def _rate_context() -> dict:
    """Earnings yield against the 10-year, and how much rates are explaining.

    Two separate things, deliberately not joined by a causal claim — see the
    module docstring for the regression that killed the obvious link.
    """
    try:
        import numpy as np
        import pandas as pd

        from src.causality import aligned_panel

        ey_rows = _fetch_history_dated(next(m for m in METRICS if m.key == "earnings_yield"))
        if len(ey_rows) < _MIN_HISTORY:
            return {}
        ey = pd.Series({pd.to_datetime(d): v for d, v in ey_rows if d}).sort_index()
        # Collapse to ONE observation per calendar month before anything counts
        # them. multpl's by-month table carries a "current" row stamped
        # mid-month alongside the month-start rows, so the newest month appears
        # twice and every count expressed in months runs one long — the live
        # streak read 31 for a Feb-2024-to-Jul-2026 run that is 30 months.
        ey = ey.groupby(ey.index.to_period("M")).last()
        ey.index = ey.index.to_timestamp()

        panel = aligned_panel(["SPX", "UST10Y"], lookback=f"{_BETA_PCTILE_YEARS}Y")
        if panel.empty or "SPX" not in panel or "UST10Y" not in panel:
            return {}
        panel.index = pd.to_datetime(panel.index)
        # Same forward-fill trap as the RRG board: aligned_panel reindexes onto a
        # CALENDAR business-day grid, so every market holiday is a row where
        # nothing moved. Those (0, 0) points sit exactly on the origin of this
        # regression and pull the slope toward zero.
        moved = panel.diff().abs().sum(axis=1) > 0
        moved.iloc[0] = True
        panel = panel[moved]

        ten = panel["UST10Y"].dropna()
        d = pd.DataFrame({
            "ret": panel["SPX"].pct_change() * 100,      # percent
            "d10": panel["UST10Y"].diff() * 100,         # basis points
        }).dropna()
        if len(d) < _BETA_WINDOW * 2:
            return {}

        cov = d["ret"].rolling(_BETA_WINDOW).cov(d["d10"])
        var = d["d10"].rolling(_BETA_WINDOW).var()
        beta = (cov / var.replace(0, np.nan)).dropna()
        r = d["ret"].rolling(_BETA_WINDOW).corr(d["d10"]).dropna()
        if beta.empty:
            return {}

        cur_beta = float(beta.iloc[-1])
        cur_ey, cur_10y = float(ey.iloc[-1]), float(ten.iloc[-1])
        erp = cur_ey - cur_10y

        # ERP history on the monthly grid the earnings yield actually lives on.
        #
        # The 10-year has to be re-fetched long here. `panel` is 5Y because that
        # is all the beta needs, and reusing it left the ERP series with ~60
        # monthly observations against a 120-month floor — so every percentile
        # and the streak silently dropped out of the payload while the beta
        # fields looked perfectly healthy.
        erp_hist = pd.Series(dtype=float)
        try:
            from src.data_engine import _fred_history
            f = _fred_history("DGS10", days=365 * 40)
            if f is not None and not f.empty:
                long_10y = pd.Series(f["Close"].values,
                                     index=pd.to_datetime(f.index)).sort_index().dropna()
                ten_m = (long_10y.reindex(long_10y.index.union(ey.index))
                         .ffill().reindex(ey.index))
                erp_hist = (ey - ten_m).dropna()
        except Exception as e:
            logger.warning(f"long 10y history for ERP percentile failed: {e}")

        out: dict = {
            "earnings_yield_pct": round(cur_ey, 2),
            "ten_year_pct": round(cur_10y, 2),
            "erp_pct": round(erp, 2),
            "beta_pct_per_bp": round(cur_beta, 4),
            # The readable form: what a 10bp move in the 10-year has mapped to.
            "move_per_10bp_pct": round(cur_beta * 10, 2),
            "beta_window_days": _BETA_WINDOW,
            "rates_r2": round(float(r.iloc[-1] ** 2), 3),
            "beta_pctile": round(float((beta < cur_beta).mean() * 100), 0),
            "beta_pctile_years": _BETA_PCTILE_YEARS,
        }
        if len(erp_hist) >= _MIN_HISTORY:
            out["erp_pctile"] = round(float((erp_hist < erp).mean() * 100), 1)
            out["erp_n_months"] = int(len(erp_hist))
            neg = erp_hist < 0
            out["erp_negative_share_pct"] = round(float(neg.mean() * 100), 1)
            # Length of the current unbroken run on the same side of zero. A
            # negative ERP is NOT rare — it was the norm from 1986 to 2003 — so
            # the streak is the fact worth stating, not the sign.
            same = (neg != neg.iloc[-1])[::-1]
            out["erp_streak_months"] = int(same.values.argmax() if same.any() else len(neg))
            out["erp_streak_is_negative"] = bool(neg.iloc[-1])
        return out
    except Exception as e:
        logger.warning(f"rate context failed: {e}")
        return {}


def sp_valuation() -> dict:
    """Current S&P valuation multiples against their own long-run history."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_fetch_metric, METRICS))
    rate_context = _rate_context()

    rows = [r for r in results if r]
    if not rows:
        return {"available": False, "reason": "multpl unreachable or layout changed"}

    prem = [r["premium_to_median_pct"] for r in rows if r["premium_to_median_pct"] is not None]
    pcts = [r["percentile"] for r in rows if r.get("percentile") is not None]
    pcts_recent = [r["percentile_recent"] for r in rows if r.get("percentile_recent") is not None]
    return {
        "available": True,
        "asof": datetime.utcnow().isoformat() + "Z",
        "source": "multpl.com",
        # Median across metrics rather than mean — one metric with a distorted
        # long-run baseline (price/book drifts structurally) shouldn't drag the
        # headline.
        #
        # `sorted(prem)[len(prem) // 2]` is NOT a median on an even-length list:
        # it takes the upper of the two middle values. With all six metrics
        # present that reported 104.1% against a true median of 97.7%, biasing
        # the headline high by 6.4 points — and it was right only when an odd
        # number of metrics happened to be available, so the error came and went
        # with scraper availability.
        "median_premium_pct": round(statistics.median(prem), 1) if prem else None,
        # Headline percentile across whatever metrics have a distribution. Median
        # again, for the same reason as the premium: one structurally-drifting
        # series should not carry the summary.
        "median_percentile": (round(statistics.median(pcts), 1) if pcts else None),
        "median_percentile_recent": (round(statistics.median(pcts_recent), 1)
                                     if pcts_recent else None),
        "recent_years": _RECENT_YEARS,
        # The only part of this block that moves daily, and the only part with
        # anything to say about a session. Empty when unavailable — never
        # partially filled with defaults.
        "rate_context": rate_context,
        "rows": rows,
        "unavailable": [m.key for m, r in zip(METRICS, results) if not r],
        "distribution_note": (
            "Percentile is the honest reading — these series are right-skewed and "
            "heavily autocorrelated, so a z-score overstates its own precision. "
            f"The {_RECENT_YEARS}-year column matters where the metric drifts "
            "structurally: dividend yield fell as payout moved to buybacks, and "
            "price/book rises as the index gets more asset-light, so a percentile "
            "against 1871 partly measures a changing market rather than a dearer one."),
    }
