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
_NUM_TOKEN = re.compile(
    r"""\$?\s*-?\d+(?:,\d{3})*(?:\.\d+)?\s*[%xBMKTbmkt]?""",
)


def _normalize_num(token: str) -> tuple[float | None, bool]:
    """Turn a token like '$1.2B', '15%', '1,500', '1.37x', '$3.2T' into a float.
    Returns (value, is_percent). is_percent tells the grounding check to also
    try the decimal form when matching against payload numbers (since data
    often stores percentages as decimals — 0.153 vs "15.3%")."""
    s = token.strip().replace("$", "").replace(",", "").replace(" ", "")
    mult = 1.0
    is_percent = False
    if s.endswith("%"):
        s = s[:-1]
        is_percent = True
    elif s.lower().endswith("t"):
        s = s[:-1]
        mult = 1e12
    elif s.lower().endswith("b"):
        s = s[:-1]
        mult = 1e9
    elif s.lower().endswith("m"):
        s = s[:-1]
        mult = 1e6
    elif s.lower().endswith("k"):
        s = s[:-1]
        mult = 1e3
    elif s.lower().endswith("x"):
        s = s[:-1]
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

        # Fuzzy numeric match within 2% tolerance against any payload number.
        # For percentage tokens, also check the decimal form — payloads often
        # store "15.3%" as 0.153. Also try the /100 form for bare integers in
        # 1..100 (likely implicit percentile / percentage claims like
        # "95th percentile" when the payload stores 0.95).
        candidates = [n]
        if is_pct:
            candidates.append(n / 100.0)
        elif "." not in token and "%" not in token and 1 <= n <= 100 and n == int(n):
            candidates.append(n / 100.0)
        matched = False
        for c in candidates:
            tolerance = max(abs(c) * 0.02, 0.01)
            if any(abs(c - p) <= tolerance for p in payload_nums):
                matched = True
                break
        if matched:
            grounded.append(token)
            continue

        # Ratio check: is this a derivation of two payload numbers?
        # Covers cases like "1.37x" when payload has {purchases: 177, sales: 129}.
        ratio_match = False
        if 0.01 < abs(n) < 1000:
            nums_list = list(payload_nums)
            for i, a in enumerate(nums_list):
                if a == 0:
                    continue
                for j, b in enumerate(nums_list):
                    if i == j:
                        continue
                    r = b / a
                    if abs(r - n) <= abs(n) * 0.02:
                        ratio_match = True
                        break
                if ratio_match:
                    break
        if ratio_match:
            grounded.append(token)
            continue

        unverified.append(token)

    return {
        "grounded_count": len(grounded),
        "unverified_count": len(unverified),
        "unverified_tokens": unverified[:10],  # cap to avoid noisy UI
    }
