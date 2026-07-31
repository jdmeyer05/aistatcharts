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

SOURCE FRAGILITY: multpl publishes as HTML with no API, so this parses a
specific page structure. Every field is independently optional and a failed
metric is dropped rather than failing the set, so a layout change degrades the
block instead of taking it down.
"""

from __future__ import annotations

import logging
import re
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

    return {
        "key": mt.key,
        "label": mt.label,
        "unit": mt.unit,
        "why": mt.why,
        "value": round(value, 2),
        "mean": round(mean, 2) if mean is not None else None,
        "median": round(median, 2) if median is not None else None,
        "min": parsed.get("min"),
        "max": parsed.get("max"),
        "premium_to_median_pct": round(premium, 1) if premium is not None else None,
        "asof_text": parsed.get("asof_text"),
    }


def sp_valuation() -> dict:
    """Current S&P valuation multiples against their own long-run history."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(_fetch_metric, METRICS))

    rows = [r for r in results if r]
    if not rows:
        return {"available": False, "reason": "multpl unreachable or layout changed"}

    prem = [r["premium_to_median_pct"] for r in rows if r["premium_to_median_pct"] is not None]
    return {
        "available": True,
        "asof": datetime.utcnow().isoformat() + "Z",
        "source": "multpl.com",
        # Median across metrics rather than mean — one metric with a distorted
        # long-run baseline (price/book drifts structurally) shouldn't drag the
        # headline.
        "median_premium_pct": round(sorted(prem)[len(prem) // 2], 1) if prem else None,
        "rows": rows,
        "unavailable": [m.key for m, r in zip(METRICS, results) if not r],
    }
