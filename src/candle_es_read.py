"""What the candle study implies for the ES session about to trade.

The card measures `^GSPC` — the cash index — and reports tomorrow's range in
index points against a 40,000-bar sample. Both facts are correct and neither is
what the reader needs: they trade ES, and "77.96 median" is a number with
nothing attached to it. Wide against what?

Two translations, and only two, because they are the two the card cannot make
for itself:

1. Index points ARE ES points. ES is a future ON the index — the basis is a
   level offset, not a scale factor, so a 78-point index range is a 78-point ES
   range. Worth stating rather than leaving the reader to assume it, since the
   card labels itself "cash index" and invites exactly the opposite doubt.
2. Wide against ES's OWN sessions. `es_overnight` measures the ES cash-session
   range directly across ~494 sessions, so the forecast can be quoted as a
   multiple of what ES actually does instead of in the abstract.

The direction line stays as the card already words it. The study's own numbers
say the tilt is worth about ten basis points, and nothing here should make that
sound like more than it is.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# How far from ES's own median before "wider than usual" is worth saying at all.
_WIDE = 1.15
_NARROW = 0.85


def candle_es_read(candles: dict | None) -> dict | None:
    """Turn the candle study's range forecast into an ES session expectation."""
    if not candles or not candles.get("available"):
        return None
    fc = candles.get("tomorrow_range") or {}
    p50 = fc.get("p50")
    if not p50:
        return None

    reads: list[dict] = []

    # ES's own measured range, for scale. Cached, so this is a dict lookup in
    # the normal case; if it is unavailable the forecast still stands on its own
    # and only the comparison is dropped.
    es_median = None
    try:
        from src.es_overnight import overnight_base_rates
        base = overnight_base_rates()
        if base.get("available"):
            es_median = base.get("median_rth_range")
    except Exception as e:
        logger.debug(f"ES range comparison unavailable: {e}")

    lo, hi = fc.get("p25"), fc.get("p75")
    band = f", typically {lo:.0f}-{hi:.0f}" if lo and hi else ""
    reads.append({
        "label": "Range, in ES points",
        "value": f"~{p50:.0f} pts{band}",
        "note": ("ES is a future on this index, so the basis is a level offset, not a "
                 "scale factor — index points and ES points are the same size. Read "
                 "these straight off the ES chart."),
    })

    if es_median:
        ratio = p50 / es_median
        if ratio >= _WIDE:
            verdict = (f"about {ratio:.2f}x a normal ES cash session ({es_median:.0f} pts "
                       "median) — a wider day than usual is being forecast")
        elif ratio <= _NARROW:
            verdict = (f"about {ratio:.2f}x a normal ES cash session ({es_median:.0f} pts "
                       "median) — a tighter day than usual is being forecast")
        else:
            verdict = f"in line with a normal ES cash session ({es_median:.0f} pts median)"
        reads.append({
            "label": "Wide against what",
            "value": f"{ratio:.2f}x ES's own median",
            "note": verdict,
        })

    # The exceedance rate is the stop-placement fact on this card, and it is
    # currently printed as a bare percentage next to the sample size.
    exceed = fc.get("prob_exceeds_1_atr")
    if exceed:
        pct = float(exceed)
        reads.append({
            "label": "Stop placement",
            "value": f"{pct:.0f}% exceed 1 ATR",
            "note": (f"roughly {pct/100:.0%} of sessions shaped like this one travel more than "
                     "a full ATR, so a stop sitting inside 1 ATR is reached that often on "
                     "range alone — before direction enters into it"),
        })

    if not reads:
        return None

    return {
        "available": True,
        "instrument_note": "measured on the cash index; ES tracks it point for point",
        "reads": reads,
        # Deliberately restating the study's own verdict rather than adding to
        # it. The range is what this card sizes; the tilt is not a reason.
        "direction_note": ("The close-location tilt is real and tiny — about ten basis points "
                           "of median return end to end. Size off the range; treat the tilt "
                           "as a tiebreaker and never as a reason to be in the trade."),
    }
