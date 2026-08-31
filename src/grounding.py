"""Numeric grounding: does every number in a model's prose trace to its payload?

Lifted verbatim out of `api/routes/ai.py`, where it only ever guarded the
/interpret endpoint. The prompt loop needs the same check on the market-driver
narrative and on replayed outputs graded inside the worker, and the worker must
not import FastAPI to get it.

The check is deliberately generous — verbatim substring, then a 2% fuzzy match,
then a two-number ratio derivation — because a false "hallucinated" flag is
worse than a missed one when the score feeds an automatic prompt change.
"""

from __future__ import annotations

import json
import re


# Extract numeric-looking tokens from Claude's interpretation so we can check
# each one appears in the data payload. Handles plain numbers, thousands
# commas, percentages, and $B/$M/$K suffixes.
# A SCALE SUFFIX MUST BE A SUFFIX, NOT THE NEXT WORD (fixed 2026-08-29).
#
# The previous pattern ended `\s*[%xBMKTbmkt]?`, which consumed the first letter
# of whatever English word followed the number — and `_normalize_num` then read
# that letter as a multiplier. Straight from production:
#
#   "watch for SPY support near 765.00 to hold"  -> "765.00 t" -> 765 TRILLION
#   "the index at 7702 basis"                    -> "7702 b"   -> 7.7 trillion
#   "DXY at 99.50 today"                         -> "99.50 t"  -> 99.5 trillion
#
# English is full of words starting with t/b/m/k, so any number followed by
# "to", "today", "the", "but", "basis", "market", "keeps"... was compared to the
# payload after being scaled by 1e9 or 1e12. Nothing could ever match, and the
# rule reported "does not trace to any payload value" — a true statement about
# the corrupted number and a meaningless one about the prose. Same family as the
# invented_ticker incident: findings that read plausibly while the internals are
# broken, which is why the evidence column showed "765.00 t" and "7702 b".
#
# The lookahead is the fix: a suffix counts only when not followed by another
# letter. Spelled-out scales are listed explicitly so that losing the greedy
# single letter does not silently UNDER-scale "2.5 billion".
_NUM_TOKEN = re.compile(
    r"""\$?\s*-?\d+(?:,\d{3})*(?:\.\d+)?"""
    r"""(?:\s*(?:%|bn|mm|trillion|billion|million|thousand|[xbmkt])(?![A-Za-z]))?""",
    re.IGNORECASE,
)

# Longest first, so "b" does not eat "bn" and "bn" does not eat "billion".
_SCALE_SUFFIXES = (
    ("trillion", 1e12), ("billion", 1e9), ("million", 1e6), ("thousand", 1e3),
    ("bn", 1e9), ("mm", 1e6),
    ("t", 1e12), ("b", 1e9), ("m", 1e6), ("k", 1e3),
)


# A ratio someone would actually write. "1.37x" is a derivation; "765x" is not
# a claim anyone makes, so treating 765 as a possible quotient of two payload
# fields is not verification, it is a licence to invent.
_RATIO_MIN = 0.05
_RATIO_MAX = 50.0


def _tolerance(value: float, is_pct: bool) -> float:
    """How far a stated number may sit from a payload value and still count.

    A SINGLE TOLERANCE IS WRONG, and ours was 2% of the value for everything.
    On a price that is enormous: 2% of 765 is 15.3, so "support near 765.00"
    matched an index at 769.245 — and would equally have matched 750 or 780.
    The looseness was invisible while the tokenizer was corrupting scales
    (nothing matched anyway); fixing that exposed it, and the two bugs had been
    cancelling. A rule can be wrong in both directions at once.

    So: tight and relative on levels, absolute on percents and small ratios.
    0.1% is the WirelessBench convention, chosen there explicitly to "separate
    benign numerical imprecision from catastrophic unit/magnitude errors" —
    which is exactly the distinction we need. It accepts 7702 for a real 7701.25
    (0.0097% off) and rejects 765.00 for a real 769.245 (0.55% off).

    The 0.05 floor for percents and small ratios is one-decimal rounding: a note
    saying "-0.2%" for a payload's -0.24 is rounding, not invention.
    """
    if is_pct or abs(value) < 10:
        return 0.05
    return max(abs(value) * 0.001, 0.01)


def _normalize_num(token: str) -> tuple[float | None, bool]:
    """Turn a token like '$1.2B', '15%', '1,500', '1.37x', '2.5 billion' into a
    float. Returns (value, is_percent). is_percent tells the grounding check to
    also try the decimal form when matching against payload numbers (since data
    often stores percentages as decimals — 0.153 vs "15.3%")."""
    s = token.strip().replace("$", "").replace(",", "").replace(" ", "").lower()
    mult = 1.0
    is_percent = False
    if s.endswith("%"):
        s = s[:-1]
        is_percent = True
    elif s.endswith("x"):
        s = s[:-1]
    else:
        for suffix, factor in _SCALE_SUFFIXES:
            if s.endswith(suffix) and len(s) > len(suffix):
                s = s[:-len(suffix)]
                mult = factor
                break
    try:
        return float(s) * mult, is_percent
    except (ValueError, TypeError):
        return None, False


def _collect_payload_numbers(obj, out: set[float]) -> None:
    """Recursively gather every numeric value in the payload for fuzzy match."""
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.add(float(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_payload_numbers(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_payload_numbers(v, out)
    elif isinstance(obj, str):
        # Parse numeric substrings in string values too (e.g., "15.3%" in data)
        for m in _NUM_TOKEN.findall(obj):
            n, _ = _normalize_num(m)
            if n is not None:
                out.add(n)


def _check_grounding(interpretation: str, data: dict) -> dict:
    """Post-hoc hallucination check.

    Every numeric claim in the interpretation either (a) appears verbatim in
    the payload, (b) matches a payload value within 2% tolerance (for rounded
    claims like "$1.2B" when payload has 1,215,432,000), or (c) is a trivial
    derivation (ratio/percent of two payload values within tolerance).
    Unverified tokens are surfaced in the response so the UI can flag them.
    """
    payload_nums: set[float] = set()
    _collect_payload_numbers(data, payload_nums)
    # Also include the stringified version for verbatim substring matches.
    data_str = json.dumps(data, default=str)

    grounded: list[str] = []
    unverified: list[str] = []
    skip_tiny = {0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0}

    for raw_token in _NUM_TOKEN.findall(interpretation):
        token = raw_token.strip()
        if not token or token in ("-", "$"):
            continue
        n, is_pct = _normalize_num(token)
        if n is None:
            continue
        # Skip tiny counting numbers — "3 buys", "2 sells" are everywhere and
        # a false flag here is worse than a missed one.
        if n in skip_tiny and "." not in token and "%" not in token:
            continue

        # Verbatim substring match on data JSON (handles "NDAQ (6 buys)"
        # where 6 is in the payload's count field).
        clean = token.replace("$", "").replace(",", "").replace(" ", "").rstrip("%xBMKTbmkt")
        if clean and clean in data_str:
            grounded.append(token)
            continue

        # Fuzzy numeric match against any payload number. For percentage tokens
        # also check the decimal form — payloads often store "15.3%" as 0.153.
        # Also try the /100 form for bare integers in 1..100 (likely implicit
        # percentile claims like "95th percentile" when the payload stores 0.95).
        #
        # THE TOLERANCE MUST TRAVEL WITH THE SCALE. Computing it from the
        # divided candidate makes it 100x looser: an absolute 0.05 on 0.153 is
        # five percentage points, so a claimed "45" matched a payload's 0.42.
        # The tolerance is fixed on the token as written, then divided by the
        # same factor the candidate was.
        base_tol = _tolerance(n, is_pct)
        candidates = [(n, 1.0)]
        if is_pct:
            candidates.append((n / 100.0, 100.0))
        elif "." not in token and "%" not in token and 1 <= n <= 100 and n == int(n):
            candidates.append((n / 100.0, 100.0))
        matched = False
        for c, scale in candidates:
            tol = base_tol / scale
            if any(abs(c - p) <= tol for p in payload_nums):
                matched = True
                break
        if matched:
            grounded.append(token)
            continue

        # Ratio check: is this a derivation of two payload numbers?
        # Covers cases like "1.37x" when payload has {purchases: 177, sales: 129}.
        #
        # THIS PATH GROUNDED 98% OF EVERYTHING (measured 2026-08-29). It searched
        # all ordered pairs — 64 payload numbers is 4,032 candidate ratios — and
        # accepted any within 2%. On a real payload, 293 of 300 randomly invented
        # numbers in (0.01, 1000) came back "grounded". The rule could not flag a
        # number below 1000 at all, which is why every token it ever caught in
        # production (250K, 240K, 1.3M) is above that line.
        #
        # Two constraints, per the data-to-text literature's advice that a
        # derivation must be NAMEABLE rather than searched for: a ratio has to
        # look like a ratio (nobody writes "765x"), and it gets the same tight
        # tolerance as everything else. The matching pair is recorded so a
        # spurious match is auditable rather than invisible.
        ratio_match = False
        if _RATIO_MIN <= abs(n) <= _RATIO_MAX:
            tol = _tolerance(n, is_pct)
            nums_list = list(payload_nums)
            for i, a in enumerate(nums_list):
                if a == 0:
                    continue
                for j, b in enumerate(nums_list):
                    if i == j:
                        continue
                    if abs(b / a - n) <= tol:
                        ratio_match = True
                        break
                if ratio_match:
                    break
        if ratio_match:
            grounded.append(token)
            continue

        # Difference check: "the put wall is 146.76 below", where the answer has
        # already stated 7707.25 and 7560.49. Added 2026-08-31 after the home
        # chat flagged exactly that — correct arithmetic reported as unverified.
        #
        # NOT SEARCHED OVER THE PAYLOAD, and that is the whole design. Differences
        # between arbitrary payload pairs would recreate the failure the ratio
        # path above documents: a real payload carries ~780 numbers, so ~600,000
        # candidate differences blanket the line densely enough to ground almost
        # anything, and the check would stop meaning anything.
        #
        # The operands must be NAMEABLE, and the strictest available definition
        # of nameable is that the model wrote them down: the pair is drawn only
        # from numbers ALREADY GROUNDED IN THIS ANSWER. A few dozen candidates
        # instead of hundreds of thousands, both of them values the answer has
        # itself committed to, so a reader can check the subtraction on screen.
        # Distances between two quoted levels — the motivating case — all have
        # this shape.
        diff_tol = _tolerance(n, is_pct)
        vals = [v for v in (_normalize_num(t)[0] for t in grounded) if v is not None]
        if any(abs(abs(a - b) - abs(n)) <= diff_tol
               for i, a in enumerate(vals) for b in vals[i + 1:]):
            grounded.append(token)
            continue

        unverified.append(token)

    return {
        "grounded_count": len(grounded),
        "unverified_count": len(unverified),
        "unverified_tokens": unverified[:10],  # cap to avoid noisy UI
    }
