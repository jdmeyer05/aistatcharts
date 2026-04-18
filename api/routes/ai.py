"""AI-powered page interpretation via Claude Opus 4.7.

Each page sends a compact JSON summary of what the user is looking at. Claude
reads the data in context and returns a short trader-facing interpretation:
what's notable, what's noise, what (if anything) to act on.

All calls use prompt caching on the base system prompt so repeated
interpretations across users / pages keep cost low (~75% input discount on
cache hits).
"""

import json
import logging
import re

import anthropic
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_current_user
from src.api_keys import get_secret

logger = logging.getLogger(__name__)
router = APIRouter()

MODEL = "claude-opus-4-7"

BASE_SYSTEM = """You are a senior quantitative analyst at an institutional trading desk. The user is looking at a page in our quant research platform and wants you to tell them WHAT IT MEANS — not describe what they're seeing, but interpret it.

Style rules:
- Be direct, specific, and actionable. No throat-clearing, no meta-commentary.
- Use bullet points. Keep under 220 words total.
- Distinguish signal from noise. A single outlier number is often noise; confluence of signals is usually signal.
- Name the tradeable implication where one exists (long/short bias, sizing consideration, what would invalidate the read).
- Be honest about data limitations — if the sample is small or the window is short, say so.
- Assume the user is sophisticated: don't explain basic terms.
- End with one line: "Bottom line:" followed by a single crisp takeaway.

ACCURACY RULES — non-negotiable:
- Only cite numbers that appear in the payload, either literally or as direct derivations (ratios, averages, sums) of payload values.
- If you compute a derived number, either show the inputs (e.g., "177/129 = 1.37x buy/sell") or prefix with "roughly"/"approximately".
- Prefer qualitative language ("modestly bullish", "heavy skew") over invented precise figures when precision isn't in the data.
- Never cite a ticker, fund, or person not present in the payload.
- If the data is too sparse for a specific claim, say so — don't pad with generalities."""


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
        # store "15.3%" as 0.153.
        candidates = [n]
        if is_pct:
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

# Per-page context so Claude understands the semantic meaning of each payload
# without reinventing it every call. These stay in the user turn, not the
# system prompt (the system prompt cache works better when it's stable).
PAGE_CONTEXT: dict[str, str] = {
    "overview": "Smart Money Overview — the Conviction Score dashboard. Aggregates ticker-level signals across insider Form 4 activity, activist 13D filings, and 8-K event pulse. A confluence of 2+ signal families historically produces institutional-grade edge in backtests.",
    "insiders": "Form 4 insider trades for a single ticker. Cluster buys (3+ distinct insiders within 30 days same direction) are highly predictive per the insider-trading literature (Seyhun, Cohen, Malloy, Pomorski). Sells are noisier — routine 10b5-1 plans, options exercises, and diversification dominate.",
    "13f": "Quarterly 13F-HR institutional holdings for a single fund. Fund's top positions by value. Stale data — filed 45 days after quarter end, so positions may have changed by the time you're reading it.",
    "political": "Congressional stock trades (House PTR filings) with performance vs SPY overlay. Politicians are not monolithic — some beat, most match, a few significantly underperform. Alpha is measured as stock return from trade date to today minus SPY return over the same window.",
    "activist": "Recent 13D/13D-A filings. New 13D = someone took a >5% stake with intent to influence (historically bullish 3-12 months); amendment = stake change (could be up or down, needs reading the filing text).",
    "shorts": "Short interest structural metrics. High % float + high days-to-cover + recent price momentum = squeeze setup. Alone, heavy shorting is usually correct. The setup that matters is structurally-trapped shorts facing a catalyst.",
    "buybacks": "Company's own capital return history from cashflow statements. Buyback yield = TTM repurchases / market cap. Total shareholder yield = buyback + dividend yield. Research (Ikenberry, Michaely) shows buyback-announcing firms outperform peers by ~4-12% over 4 years. Execution matters more than authorization — look for consistent actual repurchases.",
    "exits": "Inverse smart-money tracker — where institutional and political money is reducing exposure. Congressional net selling + activist amendments + cluster insider sells on the same ticker is a coordinated exit signal.",
    "global": "Sovereign wealth funds, public pensions, and endowments — 10+ year horizon. Cross-fund consensus picks (held by 3+ global funds) are structural conviction blue chips. Quarter-over-quarter deltas reveal rotation.",
    "factors": "Fama-French 5-factor regression on a single ticker. Betas quantify exposure to Market / Size / Value / Profitability / Investment factors. Alpha is the excess return unexplained by factor exposures — what's left after stripping out the systematic bets.",
}


class InterpretRequest(BaseModel):
    page: str
    data: dict
    subject: str | None = None  # e.g., ticker, fund name, politician — for context


@router.post("/interpret")
async def interpret(
    body: InterpretRequest,
    user: str = Depends(get_current_user),
):
    """Ask Claude to interpret the data on a page. Returns plain text."""
    # Auth gate: every Claude call costs real money, so reject anonymous
    # requests at the edge rather than relying on Cloud Run rate limits.
    if user == "anonymous":
        raise HTTPException(401, "Sign in required for AI interpretation")

    api_key = get_secret("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(503, "AI interpretation unavailable — ANTHROPIC_API_KEY not configured")

    ctx = PAGE_CONTEXT.get(body.page, "")
    subject = f"Subject: {body.subject}\n\n" if body.subject else ""

    user_message = (
        f"Page: {body.page}\n\n"
        f"What this page shows: {ctx}\n\n"
        f"{subject}"
        f"Current data:\n```json\n{json.dumps(body.data, default=str, indent=2)[:20000]}\n```\n\n"
        f"Interpret these results for me. What does it mean?"
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=700,
            system=[
                {
                    "type": "text",
                    "text": BASE_SYSTEM,
                    # Prompt cache — system prompt is reused across every
                    # interpretation request, so cache hits cut input cost ~75%.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        text_blocks = [b.text for b in msg.content if hasattr(b, "text")]
        interpretation = "\n".join(text_blocks)
        grounding = _check_grounding(interpretation, body.data)
        return {
            "ok": True,
            "model": MODEL,
            "interpretation": interpretation,
            "grounding": grounding,
            "cache_creation_tokens": getattr(msg.usage, "cache_creation_input_tokens", 0),
            "cache_read_tokens": getattr(msg.usage, "cache_read_input_tokens", 0),
            "input_tokens": msg.usage.input_tokens,
            "output_tokens": msg.usage.output_tokens,
        }
    except anthropic.BadRequestError as e:
        logger.warning(f"Claude rejected interpret request: {e}")
        raise HTTPException(400, f"Claude rejected the request: {e}")
    except anthropic.APIError as e:
        logger.warning(f"Claude API error: {e}")
        raise HTTPException(502, f"Claude API error: {e}")
    except Exception as e:
        logger.warning(f"interpret failed: {e}")
        raise HTTPException(500, f"Interpretation failed: {e}")
