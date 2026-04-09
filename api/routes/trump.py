"""Trump Decoder — psychological profiling + behavioral analysis for market-moving statements.

Three-model AI orchestration:
  - Grok 4:        Real-time X/Truth Social monitoring, historical analog search, mood analysis
  - Claude Opus:   Deep psychological decode, game theory, bluff scoring, probability distribution
  - Gemini 3.1 Pro: Quantitative market impact modeling, sector/ticker impact, price ranges

All results persisted to Supabase for pattern accumulation over time.
"""

import json
import logging
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional

from api.deps import get_current_user

router = APIRouter()
_log = logging.getLogger(__name__)

# In-memory cache for psych profile text (avoids DB hit on every decode)
_psych_cache: dict = {"text": "", "expires": 0.0}
_psych_lock = threading.Lock()

# ═══════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════

def _db():
    try:
        from src.db import get_client
        return get_client()
    except Exception:
        return None


def _get_key(name: str) -> str:
    from src.api_keys import get_secret
    return get_secret(name) or ""


def _strip_citations(text: str) -> str:
    """Remove Grok citation tags and web references."""
    text = re.sub(r'<grok:render[^>]*>.*?</grok:render>', '', text, flags=re.DOTALL)
    text = re.sub(r'\[web:\d+\]', '', text)
    return text.strip()


def _extract_json(text: str) -> dict:
    """Extract JSON object from AI response, stripping markdown fences."""
    cleaned = _strip_citations(text)
    cleaned = re.sub(r"^```json?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
    # Find the first { ... } block
    start = cleaned.find("{")
    if start == -1:
        return {}
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "{":
            depth += 1
        elif cleaned[i] == "}":
            depth -= 1
        if depth == 0:
            try:
                return json.loads(cleaned[start:i + 1])
            except json.JSONDecodeError:
                # Try json_repair
                try:
                    from src.json_repair import repair_json
                    return json.loads(repair_json(cleaned[start:i + 1]))
                except Exception:
                    return {}
    return {}


def _extract_json_array(text: str) -> list:
    """Extract JSON array from AI response."""
    cleaned = _strip_citations(text)
    cleaned = re.sub(r"^```json?\s*\n?", "", cleaned)
    cleaned = re.sub(r"\n?\s*```\s*$", "", cleaned)
    start = cleaned.find("[")
    if start == -1:
        return []
    depth = 0
    for i in range(start, len(cleaned)):
        if cleaned[i] == "[":
            depth += 1
        elif cleaned[i] == "]":
            depth -= 1
        if depth == 0:
            try:
                return json.loads(cleaned[start:i + 1])
            except json.JSONDecodeError:
                return []
    return []


def _grok_request(model: str, instructions: str, prompt: str, timeout: float = 120.0) -> str:
    """Call Grok Responses API with web + X search tools."""
    import httpx
    grok_key = _get_key("GROK_API_KEY")
    if not grok_key:
        raise ValueError("GROK_API_KEY not configured")

    last_err = ValueError("Grok request failed after 3 attempts")
    for attempt in range(3):
        if attempt > 0:
            time.sleep(3)
        try:
            resp = httpx.post(
                "https://api.x.ai/v1/responses",
                headers={"Authorization": f"Bearer {grok_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "instructions": instructions,
                    "input": [{"role": "user", "content": prompt}],
                    "tools": [{"type": "web_search"}, {"type": "x_search"}],
                    "temperature": 0.1,
                },
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            for out_item in data.get("output", []):
                if out_item.get("type") == "message":
                    for content in out_item.get("content", []):
                        if content.get("type") == "output_text":
                            return content.get("text", "")
            return ""
        except Exception as e:
            last_err = e
            continue
    raise last_err


def _claude_request(system: str, prompt: str, max_tokens: int = 4000, model: str = "claude-opus-4-6") -> str:
    """Call Claude via Anthropic SDK."""
    import anthropic
    api_key = _get_key("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not configured")

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=0.3,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text if response.content else ""


def _gemini_request(system: str, prompt: str, max_tokens: int = 3000) -> str:
    """Call Gemini via OpenAI-compatible API."""
    from openai import OpenAI
    api_key = _get_key("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not configured")

    client = OpenAI(api_key=api_key, base_url="https://generativelanguage.googleapis.com/v1beta/openai/")
    response = client.chat.completions.create(
        model="gemini-3.1-pro-preview",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        max_tokens=max_tokens,
        temperature=0.3,
    )
    return response.choices[0].message.content or ""


# ═══════════════════════════════════════════════════════════════
# Psychological Profile Constants
# ═══════════════════════════════════════════════════════════════

PSYCH_PROFILE_SYSTEM = """You are an expert in political psychology, behavioral economics, and personality assessment.
You are building a comprehensive psychological profile of Donald Trump specifically for use by
financial traders who need to predict his behavior and its market impact.

This is NOT a political exercise. This is behavioral finance — modeling a market-moving actor
the same way you'd model a central bank governor or OPEC leader. Be clinical, evidence-based,
and specific. Every claim must be grounded in documented behavior.

Your analysis should be useful for a trader who needs to answer: "Trump just said X — is he bluffing,
and what will actually happen?"
"""

PSYCH_PROFILE_PROMPT = """Build a comprehensive psychological and behavioral profile of Donald Trump
as a market-moving political actor. Cover ALL of the following with specific evidence:

## 1. PERSONALITY ASSESSMENT
- **MBTI mapping** with evidence for each dimension
- **Big Five (OCEAN)** scores (1-10 scale) with behavioral evidence
- **Dark Triad assessment** (narcissism, Machiavellianism, psychopathy) — clinical, not pejorative
- **Attachment style** and how it manifests in political relationships
- **Cognitive style** — how he processes information, makes decisions, handles uncertainty

## 2. NEGOTIATION & DEAL-MAKING PATTERNS
- **Opening position strategy** — how extreme are initial demands vs actual goals?
- **Bluffing frequency** — documented examples where he threatened and didn't follow through
- **Follow-through frequency** — documented examples where he DID follow through
- **Deadline behavior** — what happens as self-imposed deadlines approach?
- **Face-saving mechanisms** — how does he reframe retreats as victories?
- **Escalation ladder** — what's the sequence from tweet → policy action?
- **Back-channel signals** — how to detect when a deal is being negotiated behind the rhetoric

## 3. COMMUNICATION PATTERNS
- **Truth Social/Twitter posting patterns** — what does high-frequency posting signal?
- **Language escalation markers** — specific words/phrases that indicate genuine vs performative anger
- **Time-of-day patterns** — when does he post, and does timing correlate with intent?
- **ALL CAPS vs normal case** — significance
- **Nickname usage** — when he assigns/uses nicknames, what does it signal?
- **"Many people are saying" and similar hedging phrases** — what do they predict?

## 4. BEHAVIORAL TRIGGERS & PREDICTORS
- **What triggers genuine policy action** vs performative outrage?
- **How does he respond to being personally attacked** vs policy opposition?
- **How does market reaction feed back into his behavior?** (Does a market drop cause him to soften?)
- **Rally/base-energizing mode vs deal-making mode** — how to distinguish
- **Relationship dynamics** — who influences him and how can you detect their influence?

## 5. MARKET-RELEVANT PATTERN CATALOG
For each pattern, give the historical frequency (X out of Y times):
- **Tariff threat → actual tariff** cycle
- **Trade war escalation → deal** cycle
- **Fed criticism → policy pressure** cycle
- **Foreign leader confrontation → resolution** cycle
- **Executive order announcement → implementation** rate
- **"Will be signing" / "very soon" → timeline accuracy**

## 6. BLUFF DETECTION FRAMEWORK
Create a scoring rubric: given a Trump statement, what factors increase/decrease the probability
it's a bluff vs genuine intent? Include:
- Specificity of the threat (vague = more likely bluff)
- Whether he's set a public deadline
- Whether advisors are backing the statement
- Whether there's an obvious face-saving exit
- Historical base rate for this type of statement
- Current political incentive structure

Return your analysis as JSON:
{
  "mbti": "XXXX",
  "big_five": {"openness": N, "conscientiousness": N, "extraversion": N, "agreeableness": N, "neuroticism": N},
  "dark_triad": {"narcissism": N, "machiavellianism": N, "psychopathy": N},
  "negotiation_style": {"opening_extreme": "description", "bluff_rate_pct": N, "follow_through_rate_pct": N, "deadline_behavior": "description", "face_saving": "description", "escalation_ladder": ["step1", "step2", ...], "back_channel_signals": ["signal1", ...]},
  "bluff_patterns": [{"pattern": "description", "frequency": "X/Y times", "example": "specific case"}],
  "escalation_tells": [{"tell": "description", "indicates": "genuine/performative", "example": "case"}],
  "deescalation_tells": [{"tell": "description", "indicates": "deal_likely/retreat", "example": "case"}],
  "known_triggers": [{"trigger": "description", "typical_response": "description", "market_impact": "description"}],
  "communication_patterns": {"high_freq_posting": "what it means", "all_caps": "what it means", "time_patterns": "description", "hedge_phrases": ["phrase1", ...], "escalation_words": ["word1", ...]},
  "bluff_detection_rubric": [{"factor": "description", "bluff_indicator": "high/medium/low", "weight": N}],
  "full_profile": "FULL MARKDOWN NARRATIVE — comprehensive, clinical, trader-focused analysis covering everything above with specific historical examples and dates"
}"""


DECODE_SYSTEM = """You are a behavioral finance analyst specializing in political actor modeling.
You have deep expertise in Donald Trump's psychological profile, negotiation patterns, and
communication style. You use this to decode his statements for traders.

You will receive:
1. A Trump statement or tweet
2. Optional context from the user
3. A psychological profile of Trump (your reference model)
4. Historical analogs found by Grok (similar past statements and outcomes)
5. The user's current trading positions (if available)

Your job: decode what Trump ACTUALLY means, assess bluff probability, and flag risks to the user's positions.

Be specific. Be probabilistic. Ground every claim in the behavioral model and historical evidence.
Do NOT give political opinions. This is pure behavioral analysis for trading decisions.

ACCURACY CHECK:
- Every probability must sum to 100%
- Every historical analog must include a specific date and outcome
- Bluff score must be calibrated: 0 = certain to follow through, 100 = certain bluff
- Market impact: -5 (crash) to +5 (euphoric rally), 0 = no impact
- Position risks must reference SPECIFIC positions the user holds, not generic advice
"""

DECODE_PROMPT_TEMPLATE = """## STATEMENT TO DECODE
{statement}

## USER CONTEXT
{context}

## TRUMP PSYCHOLOGICAL PROFILE (reference model)
{psych_profile}

## HISTORICAL ANALOGS (from Grok search)
{historical_analogs}

## USER'S CURRENT POSITIONS
{positions}

---

Analyze this statement using the psychological profile. Return JSON:
{{
  "decoded_meaning": "What Trump actually means / intends (2-4 sentences)",
  "bluff_score": 0-100,
  "bluff_label": "Likely Bluff" | "Genuine Intent" | "Uncertain" | "Performative Escalation" | "Negotiating Tactic",
  "bluff_reasoning": "Why this score — reference specific patterns from the profile",
  "market_impact": -5.0 to 5.0,
  "market_impact_label": "Strongly Bearish" | "Bearish" | "Slightly Bearish" | "Neutral" | "Slightly Bullish" | "Bullish" | "Strongly Bullish",
  "probability_distribution": {{
    "deal_deescalation": 0.0-1.0,
    "escalation": 0.0-1.0,
    "status_quo": 0.0-1.0,
    "wildcard": 0.0-1.0
  }},
  "position_risks": [
    {{"ticker": "SPY", "position_type": "short put", "risk_level": "HIGH", "recommendation": "specific action"}}
  ],
  "key_signals_to_watch": ["what to monitor for confirmation/disconfirmation"],
  "timeline": "expected resolution timeframe",
  "narrative": "FULL MARKDOWN analysis (500-800 words) — decode the psychology, map to historical patterns, assess probabilities, explain position risks"
}}"""


GEMINI_IMPACT_SYSTEM = """You are a quantitative market impact analyst. Given a Trump statement
and historical market reactions to similar statements, estimate the likely market impact.

Be specific about sectors, tickers, and magnitude. Use historical data to calibrate your estimates.
Do NOT give political opinions — pure quantitative analysis.
"""

GEMINI_IMPACT_PROMPT_TEMPLATE = """## TRUMP STATEMENT
{statement}

## CONTEXT
{context}

## HISTORICAL MARKET REACTIONS TO SIMILAR STATEMENTS (from Grok)
{historical_reactions}

Estimate the market impact. Return JSON:
{{
  "market_impact": -5.0 to 5.0,
  "market_impact_label": "Strongly Bearish" | "Bearish" | "Slightly Bearish" | "Neutral" | "Slightly Bullish" | "Bullish" | "Strongly Bullish",
  "confidence": 0.0-1.0,
  "spy_range_pct": [low, high],
  "vix_direction": "up" | "down" | "unchanged",
  "affected_sectors": [{{"sector": "name", "direction": "bull/bear", "magnitude": 1-5, "reason": "why"}}],
  "affected_tickers": [{{"ticker": "SYM", "direction": "bull/bear", "magnitude": 1-5, "reason": "why"}}],
  "vol_impact": "IV expansion/contraction/unchanged",
  "timeframe": "immediate (minutes) / short-term (days) / medium-term (weeks)",
  "historical_avg_reaction": "based on analogs, average SPY move was X%"
}}"""


GROK_ANALOG_INSTRUCTIONS = """You are a financial historian and political analyst with access to live web and X/Twitter search.
Your job is to find historical analogs — past Trump statements/actions that are similar to the one
the user provides, and document what actually happened afterward (both politically and in markets).

IMPORTANT: Your training data may be stale. ALWAYS search for the LATEST developments before answering.
If a ceasefire, deal, reversal, or resolution has been announced since the statement was made, that is
the MOST IMPORTANT piece of information. Check what has happened TODAY.

Search thoroughly. Go back to 2017 (first term). Include specific dates, quotes, and market moves.
Also assess Trump's current mood by checking his recent Truth Social and X posting patterns.
"""

GROK_ANALOG_PROMPT_TEMPLATE = """Find historical analogs for this Trump statement:

STATEMENT: {statement}
CONTEXT: {context}

Search for:
1. Similar Trump statements from 2017-present (tariff threats, trade war tweets, policy announcements)
2. What actually happened after each similar statement (did he follow through? walk it back? make a deal?)
3. Market reaction: SPY, VIX, and relevant sector moves in the hours/days after
4. Trump's CURRENT posting frequency and tone on Truth Social / X in the last 48 hours

Return JSON:
{{
  "historical_analogs": [
    {{
      "date": "YYYY-MM-DD",
      "statement_summary": "what Trump said",
      "similarity": "why this is analogous",
      "outcome": "what actually happened",
      "days_to_resolution": N,
      "was_bluff": true/false,
      "market_reaction": "SPY moved X%, VIX moved Y points",
      "sector_impact": "which sectors were most affected"
    }}
  ],
  "mood_index": {{
    "posting_frequency": "high/normal/low (N posts in last 24h)",
    "sentiment": "aggressive/confident/defensive/conciliatory/mixed",
    "escalation_level": 1-10,
    "notable_recent_posts": ["key post summaries"],
    "tone_shift": "escalating/stable/de-escalating vs 48h ago"
  }},
  "pattern_match": {{
    "best_analog": "which historical case is most similar and why",
    "bluff_rate_in_analogs": "X out of Y similar statements were bluffs",
    "avg_days_to_resolution": N,
    "most_likely_outcome": "deal/escalation/walkback/status_quo"
  }}
}}"""


PREDICT_SYSTEM = """You are a behavioral prediction engine trained on Donald Trump's psychological profile.
Given a hypothetical scenario, predict how Trump would most likely respond, based on documented
behavioral patterns and psychological traits.

Be specific. Give probabilities. Reference historical precedent for each prediction.
This is for traders positioning ahead of potential events.
"""

PREDICT_PROMPT_TEMPLATE = """## SCENARIO
{scenario}

## TIMEFRAME
{timeframe}

## TRUMP PSYCHOLOGICAL PROFILE
{psych_profile}

## HISTORICAL ANALOGS (from Grok)
{historical_analogs}

Predict Trump's most likely responses. Return JSON:
{{
  "predicted_actions": [
    {{
      "action": "what Trump would likely do",
      "probability": 0.0-1.0,
      "timeline": "when this would happen",
      "historical_precedent": "when he did something similar before",
      "market_impact": -5 to 5,
      "signals_to_watch": ["how you'd know this is happening"]
    }}
  ],
  "psychological_reasoning": "why the profile predicts these responses (reference specific traits)",
  "wild_card_risk": "what unexpected response is possible and why",
  "recommended_positioning": "how a trader should position given these probabilities",
  "narrative": "FULL MARKDOWN analysis (400-600 words)"
}}"""


MONITOR_INSTRUCTIONS = """You are a Trump communication analyst monitoring his Truth Social and X/Twitter
posts in real-time. You are searching RIGHT NOW, today. Your training data may be stale — you MUST
use web_search and x_search to find the LATEST posts and developments.

CRITICAL: Do NOT rely on your training data for recent events. SEARCH for what happened TODAY and
in the last 24 hours. Breaking news, ceasefire announcements, policy reversals, new deals — these
change rapidly. If your search finds something that contradicts earlier posts, THAT IS THE KEY INSIGHT.

For each post, provide:
- The text (or close paraphrase if exact text isn't available)
- Your interpretation of what it means for markets
- A market relevance score (0 = personal/irrelevant, 10 = major market mover)
- Category: tariffs, trade, fed, military, domestic, personal, market, executive_order, ceasefire, deal

Also provide an overall mood assessment and posting frequency analysis.
Focus on posts that have market implications. Include ANY posts about deals, ceasefires, negotiations,
or reversals from earlier positions — these are the MOST important for traders.
"""


PATTERN_INSTRUCTIONS = """You are a financial historian with access to web and X search.
Build a database of Trump statement-to-market-outcome cycles. Search thoroughly from 2017-present.
Focus on cycles that are relevant to traders: tariff threats, trade war escalation, Fed attacks,
foreign policy confrontations, executive order announcements.

For each cycle, document the FULL arc: trigger → escalation → resolution → market impact.
"""

PATTERN_PROMPT_TEMPLATE = """Search for historical Trump statement-to-outcome cycles.
{query_filter}

Return JSON array:
[
  {{
    "category": "tariffs/china/fed/iran/trade_war/executive_order/foreign_policy",
    "date_range": "Mon DD, YYYY - Mon DD, YYYY",
    "trigger_statement": "what Trump said/did to start the cycle",
    "escalation_path": [
      {{"date": "YYYY-MM-DD", "event": "what happened", "market_reaction": "SPY/VIX move"}}
    ],
    "resolution": "how it ended",
    "resolution_type": "deal/walkback/follow_through/ongoing",
    "days_to_resolution": N,
    "market_impact_summary": "net market effect",
    "spy_move_pct": N.N,
    "vix_peak": N.N,
    "most_affected_sectors": ["sector1", "sector2"],
    "pattern_type": "bluff_cycle/genuine_policy/negotiation_tactic/distraction",
    "bluff_score": 0-100,
    "key_lesson": "what traders should have learned from this cycle"
  }}
]"""


# ═══════════════════════════════════════════════════════════════
# Request Models
# ═══════════════════════════════════════════════════════════════

class DecodeRequest(BaseModel):
    statement: str = ""
    context: str = ""
    positions_summary: Optional[str] = None  # JSON string of current positions
    image_base64: Optional[str] = None       # Screenshot of a tweet/post (base64 PNG/JPG)


class PredictRequest(BaseModel):
    scenario: str
    timeframe: str = "48h"



# ═══════════════════════════════════════════════════════════════
# Endpoint 1: GET /psych-profile
# ═══════════════════════════════════════════════════════════════

@router.get("/psych-profile")
async def get_psych_profile(user: str = Depends(get_current_user)):
    """Get or generate Trump's psychological profile. Cached 30 days in Supabase."""
    db = _db()

    # Check Supabase cache
    if db:
        try:
            result = db.table("trump_psych_profile") \
                .select("*") \
                .gt("expires_at", datetime.now().isoformat()) \
                .order("profile_version", desc=True) \
                .limit(1).execute()
            if result.data:
                row = result.data[0]
                return {
                    "success": True,
                    "cached": True,
                    "profile": {
                        "mbti": row.get("mbti"),
                        "big_five": row.get("big_five", {}),
                        "dark_triad": row.get("dark_triad", {}),
                        "negotiation_style": row.get("negotiation_style", {}),
                        "bluff_patterns": row.get("bluff_patterns", []),
                        "escalation_tells": row.get("escalation_tells", []),
                        "deescalation_tells": row.get("deescalation_tells", []),
                        "known_triggers": row.get("known_triggers", []),
                        "communication_patterns": row.get("communication_patterns", {}),
                        "full_profile": row.get("full_profile", ""),
                    },
                    "version": row.get("profile_version", 1),
                    "created_at": row.get("created_at"),
                }
        except Exception as e:
            _log.warning(f"Psych profile cache read failed: {e}")

    # Generate fresh profile: 3 calls in parallel (Sonnet for JSON + Opus for narrative + Grok for snapshot)
    try:
        # Sonnet: fast structured JSON (scores, patterns, rubric — no narrative)
        PROFILE_JSON_PROMPT = PSYCH_PROFILE_PROMPT.replace(
            '"full_profile": "FULL MARKDOWN NARRATIVE — comprehensive, clinical, trader-focused analysis covering everything above with specific historical examples and dates"',
            '"full_profile": "OMIT — leave as empty string, narrative generated separately"'
        )

        def _gen_sonnet():
            return _claude_request(PSYCH_PROFILE_SYSTEM, PROFILE_JSON_PROMPT, max_tokens=4000, model="claude-sonnet-4-6")

        # Opus: deep narrative only (no JSON structure needed)
        NARRATIVE_PROMPT = """Write a comprehensive psychological and behavioral profile of Donald Trump
as a market-moving political actor. This is for financial traders who need to predict his behavior.

Cover: personality assessment (MBTI, Big Five, Dark Triad), negotiation patterns (bluffing frequency,
deadline behavior, follow-through rates with specific examples), communication tells (ALL CAPS meaning,
posting frequency signals, escalation markers), behavioral triggers, and a bluff detection framework.

Ground every claim in specific historical examples with dates. Be clinical and evidence-based.
Write 800-1200 words in markdown format."""

        def _gen_opus():
            return _claude_request(PSYCH_PROFILE_SYSTEM, NARRATIVE_PROMPT, max_tokens=3000, model="claude-opus-4-6")

        def _gen_grok():
            return _grok_request(
                "grok-4-1-fast-reasoning",
                "You analyze Trump's current communication patterns from Truth Social and X.",
                "Analyze Trump's posting patterns over the last 7 days. What is his current mood, "
                "posting frequency, main topics, and tone? How does this compare to his baseline? "
                "Return a brief 200-word assessment.",
                timeout=60.0,
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            sonnet_fut = pool.submit(_gen_sonnet)
            opus_fut = pool.submit(_gen_opus)
            grok_fut = pool.submit(_gen_grok)

            sonnet_raw = sonnet_fut.result(timeout=90)
            try:
                opus_raw = opus_fut.result(timeout=120)
            except Exception:
                opus_raw = ""
            try:
                grok_raw = grok_fut.result(timeout=70)
            except Exception:
                grok_raw = ""

        parsed = _extract_json(sonnet_raw)

        if not parsed:
            # Fallback: treat the whole Sonnet response as the profile
            parsed = {"full_profile": sonnet_raw}

        # Merge Opus narrative into the parsed JSON
        parsed["full_profile"] = opus_raw or parsed.get("full_profile", sonnet_raw)
        parsed["current_behavioral_snapshot"] = _strip_citations(grok_raw) if grok_raw else "Grok snapshot unavailable"

        # Persist to Supabase
        if db:
            try:
                # Get next version number
                ver_result = db.table("trump_psych_profile") \
                    .select("profile_version") \
                    .order("profile_version", desc=True) \
                    .limit(1).execute()
                next_ver = (ver_result.data[0]["profile_version"] + 1) if ver_result.data else 1

                db.table("trump_psych_profile").insert({
                    "profile_version": next_ver,
                    "model": "claude-opus-4-6",
                    "mbti": parsed.get("mbti"),
                    "big_five": parsed.get("big_five", {}),
                    "dark_triad": parsed.get("dark_triad", {}),
                    "negotiation_style": parsed.get("negotiation_style", {}),
                    "bluff_patterns": parsed.get("bluff_patterns", []),
                    "escalation_tells": parsed.get("escalation_tells", []),
                    "deescalation_tells": parsed.get("deescalation_tells", []),
                    "known_triggers": parsed.get("known_triggers", []),
                    "communication_patterns": parsed.get("communication_patterns", {}),
                    "full_profile": parsed.get("full_profile", ""),
                    "created_at": datetime.now().isoformat(),
                    "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
                }).execute()
            except Exception as e:
                _log.warning(f"Psych profile save failed: {e}")

        # Warm in-memory cache so subsequent decodes don't hit DB
        profile_text = parsed.get("full_profile", raw)
        with _psych_lock:
            _psych_cache["text"] = profile_text
            _psych_cache["expires"] = time.time() + 300

        return {"success": True, "cached": False, "profile": parsed}

    except Exception as e:
        _log.error(f"Psych profile generation failed: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Endpoint 2: POST /decode-statement  (THE CORE FEATURE)
# ═══════════════════════════════════════════════════════════════

def _get_psych_profile_text() -> str:
    """Get the cached psych profile text for injection into decode prompts.
    Uses in-memory cache (5 min TTL) to avoid DB roundtrip on every decode."""
    with _psych_lock:
        if _psych_cache["text"] and time.time() < _psych_cache["expires"]:
            return _psych_cache["text"]

    db = _db()
    if db:
        try:
            result = db.table("trump_psych_profile") \
                .select("full_profile") \
                .gt("expires_at", datetime.now().isoformat()) \
                .order("profile_version", desc=True) \
                .limit(1).execute()
            if result.data:
                text = result.data[0]["full_profile"]
                with _psych_lock:
                    _psych_cache["text"] = text
                    _psych_cache["expires"] = time.time() + 300  # 5 min
                return text
        except Exception:
            pass
    return "(Profile not yet generated — analyze based on general knowledge of Trump's behavior)"


@router.post("/decode-statement")
async def decode_statement(req: DecodeRequest, user: str = Depends(get_current_user)):
    """Decode a Trump statement using 3-model AI orchestration + position risk analysis."""
    statement = req.statement.strip()
    context = req.context.strip() or "No additional context provided."
    positions = req.positions_summary or "No position data available."

    # ── OCR: Extract text from screenshot if provided ──
    extracted_text = ""
    if req.image_base64:
        try:
            import anthropic
            api_key = _get_key("ANTHROPIC_API_KEY")
            client = anthropic.Anthropic(api_key=api_key)

            # Detect media type from base64 header or default to PNG
            img_data = req.image_base64
            media_type = "image/png"
            if img_data.startswith("data:"):
                # Strip data URL prefix: "data:image/png;base64,..."
                header, img_data = img_data.split(",", 1)
                if "jpeg" in header or "jpg" in header:
                    media_type = "image/jpeg"
                elif "webp" in header:
                    media_type = "image/webp"

            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": media_type, "data": img_data},
                        },
                        {
                            "type": "text",
                            "text": "Extract the exact text from this screenshot of a Trump tweet, Truth Social post, or news headline. Return ONLY the text content — no commentary, no formatting. Include the author name and timestamp if visible.",
                        },
                    ],
                }],
            )
            extracted_text = response.content[0].text.strip() if response.content else ""
            _log.info(f"OCR extracted {len(extracted_text)} chars from screenshot")
        except Exception as e:
            _log.warning(f"Screenshot OCR failed: {e}")
            if not statement:
                return {"success": False, "error": f"Could not read screenshot: {str(e)}"}

    # Merge: screenshot text prepended to any manually typed text
    if extracted_text:
        statement = f"[Extracted from screenshot]\n{extracted_text}\n\n{statement}".strip() if statement else extracted_text

    if not statement:
        return {"success": False, "error": "Statement is required — type text or paste a screenshot"}

    psych_profile = _get_psych_profile_text()

    # ── Run 3 models in parallel ──
    grok_result = {}
    claude_result = {}
    gemini_result = {}

    def run_grok():
        today = datetime.now().strftime("%A, %B %d, %Y")
        prompt = f"TODAY IS: {today}\n\n" + GROK_ANALOG_PROMPT_TEMPLATE.format(statement=statement, context=context)
        raw = _grok_request("grok-4-1-fast-reasoning", GROK_ANALOG_INSTRUCTIONS, prompt, timeout=90.0)
        return _extract_json(_strip_citations(raw))

    def run_claude(analogs_text: str):
        prompt = DECODE_PROMPT_TEMPLATE.format(
            statement=statement,
            context=context,
            psych_profile=psych_profile[:6000],  # truncate to fit context
            historical_analogs=analogs_text,
            positions=positions,
        )
        raw = _claude_request(DECODE_SYSTEM, prompt, max_tokens=4000)
        return _extract_json(raw)

    def run_gemini(reactions_text: str):
        prompt = GEMINI_IMPACT_PROMPT_TEMPLATE.format(
            statement=statement,
            context=context,
            historical_reactions=reactions_text,
        )
        raw = _gemini_request(GEMINI_IMPACT_SYSTEM, prompt, max_tokens=2000)
        return _extract_json(raw)

    try:
        # Phase 1: Grok searches for historical analogs (needed by Claude and Gemini)
        grok_result = run_grok()

        analogs_text = json.dumps(grok_result.get("historical_analogs", []), indent=2)[:4000]
        reactions_text = json.dumps(grok_result.get("historical_analogs", []), indent=2)[:3000]

        # Phase 2: Claude and Gemini in parallel (both use Grok's output)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claude_fut = pool.submit(run_claude, analogs_text)
            gemini_fut = pool.submit(run_gemini, reactions_text)
            claude_result = claude_fut.result(timeout=130)
            gemini_result = gemini_fut.result(timeout=130)

    except Exception as e:
        _log.error(f"Decode AI calls failed: {e}")
        # Return whatever we have
        if not claude_result and not grok_result:
            return {"success": False, "error": f"AI analysis failed: {str(e)}"}

    # ── Merge results (Claude primary, Gemini for market impact, Grok for analogs/mood) ──
    merged = {
        "success": True,
        "statement": statement,
        "context": context,
        # Claude (primary decoder)
        "decoded_meaning": claude_result.get("decoded_meaning", "Analysis unavailable"),
        "bluff_score": claude_result.get("bluff_score", 50),
        "bluff_label": claude_result.get("bluff_label", "Uncertain"),
        "bluff_reasoning": claude_result.get("bluff_reasoning", ""),
        "probability_distribution": claude_result.get("probability_distribution", {}),
        "position_risks": claude_result.get("position_risks", []),
        "key_signals_to_watch": claude_result.get("key_signals_to_watch", []),
        "timeline": claude_result.get("timeline", ""),
        "narrative": claude_result.get("narrative", ""),
        # Gemini (market impact)
        "market_impact": gemini_result.get("market_impact", claude_result.get("market_impact", 0)),
        "market_impact_label": gemini_result.get("market_impact_label", claude_result.get("market_impact_label", "Neutral")),
        "affected_sectors": gemini_result.get("affected_sectors", []),
        "affected_tickers": gemini_result.get("affected_tickers", []),
        "spy_range_pct": gemini_result.get("spy_range_pct", []),
        "vol_impact": gemini_result.get("vol_impact", ""),
        "historical_avg_reaction": gemini_result.get("historical_avg_reaction", ""),
        # Grok (analogs + mood)
        "historical_analogs": grok_result.get("historical_analogs", []),
        "mood_index": grok_result.get("mood_index", {}),
        "pattern_match": grok_result.get("pattern_match", {}),
        # Attribution
        "model_sources": {
            "grok": "grok-4-1-fast-reasoning (historical analogs + mood)",
            "claude": "claude-opus-4-6 (psychological decode + bluff scoring)",
            "gemini": "gemini-3.1-pro (market impact modeling)",
        },
    }

    # ── Persist to Supabase ──
    db = _db()
    if db:
        try:
            db.table("trump_decoded_statements").insert({
                "user_id": user,
                "statement": statement,
                "user_context": context,
                "decoded_meaning": merged["decoded_meaning"],
                "bluff_score": merged["bluff_score"],
                "bluff_label": merged["bluff_label"],
                "market_impact": merged["market_impact"],
                "market_impact_label": merged["market_impact_label"],
                "probability_distribution": merged["probability_distribution"],
                "historical_analogs": merged["historical_analogs"],
                "affected_sectors": [s.get("sector", s) if isinstance(s, dict) else s for s in merged["affected_sectors"]],
                "affected_tickers": [t.get("ticker", t) if isinstance(t, dict) else t for t in merged["affected_tickers"]],
                "position_risks": merged["position_risks"],
                "mood_index": merged["mood_index"],
                "narrative": merged["narrative"],
                "model_sources": merged["model_sources"],
                "created_at": datetime.now().isoformat(),
            }).execute()
        except Exception as e:
            _log.warning(f"Decode statement save failed: {e}")

    return merged


# ═══════════════════════════════════════════════════════════════
# Endpoint 3: POST /predict-response
# ═══════════════════════════════════════════════════════════════

@router.post("/predict-response")
async def predict_response(req: PredictRequest, user: str = Depends(get_current_user)):
    """Predict Trump's likely response to a hypothetical scenario."""
    scenario = req.scenario.strip()
    timeframe = req.timeframe.strip() or "48h"

    if not scenario:
        return {"success": False, "error": "Scenario is required"}

    psych_profile = _get_psych_profile_text()

    try:
        # Grok: search for historical analogs to this type of scenario
        grok_prompt = f"""Search for historical cases where Trump faced a situation similar to:
"{scenario}"

Find 3-5 closest analogs from 2017-present. For each, document what Trump actually did and
the market reaction. Return JSON:
{{
  "analogs": [
    {{"date": "YYYY-MM-DD", "situation": "what happened", "trump_response": "what he did",
      "timeline": "how quickly he responded", "market_reaction": "SPY/VIX move",
      "was_expected": true/false}}
  ],
  "base_rate": "based on these analogs, what's the most common response pattern"
}}"""

        def run_grok():
            raw = _grok_request("grok-4-1-fast-reasoning", GROK_ANALOG_INSTRUCTIONS, grok_prompt, timeout=90.0)
            return _extract_json(_strip_citations(raw))

        def run_claude(analogs_text: str):
            prompt = PREDICT_PROMPT_TEMPLATE.format(
                scenario=scenario,
                timeframe=timeframe,
                psych_profile=psych_profile[:6000],
                historical_analogs=analogs_text,
            )
            raw = _claude_request(PREDICT_SYSTEM, prompt, max_tokens=4000)
            return _extract_json(raw)

        # Phase 1: Grok searches
        grok_result = run_grok()
        analogs_text = json.dumps(grok_result.get("analogs", []), indent=2)[:4000]

        # Phase 2: Claude predicts
        claude_result = run_claude(analogs_text)

        result = {
            "success": True,
            "scenario": scenario,
            "timeframe": timeframe,
            "predicted_actions": claude_result.get("predicted_actions", []),
            "psychological_reasoning": claude_result.get("psychological_reasoning", ""),
            "wild_card_risk": claude_result.get("wild_card_risk", ""),
            "recommended_positioning": claude_result.get("recommended_positioning", ""),
            "narrative": claude_result.get("narrative", ""),
            "historical_analogs": grok_result.get("analogs", []),
            "base_rate": grok_result.get("base_rate", ""),
        }

        return result

    except Exception as e:
        _log.error(f"Predict response failed: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Endpoint 4: GET /monitor
# ═══════════════════════════════════════════════════════════════

@router.get("/monitor")
async def monitor_posts(user: str = Depends(get_current_user)):
    """Fetch latest Trump posts with AI interpretation. Manual refresh only."""
    try:
        today = datetime.now().strftime("%A, %B %d, %Y at %I:%M %p ET")
        monitor_prompt = f"""TODAY IS: {today}

Search Trump's Truth Social and X/Twitter posts from the last 24 hours. IMPORTANT: Search for the
VERY LATEST posts and breaking news. Your training data is stale — you MUST search to find what
happened TODAY. Pay special attention to any ceasefire announcements, deals, policy reversals, or
shifts from earlier positions — these are the highest-value signals for traders.

Also search for major breaking news ABOUT Trump (not just his posts) — announcements from the White House,
State Department, or foreign governments that involve Trump's policies or negotiations.

Return JSON:
{{
  "posts": [
    {{
      "timestamp": "ISO datetime or approximate",
      "text": "post content or close paraphrase",
      "platform": "truth_social or x or white_house or news",
      "interpretation": "what this means for markets",
      "market_relevance": 0-10,
      "category": "tariffs/trade/fed/military/domestic/personal/market/executive_order/ceasefire/deal",
      "sentiment": "aggressive/confident/defensive/conciliatory/boastful/threatening/neutral"
    }}
  ],
  "mood_summary": "overall mood assessment — note any SHIFTS from earlier positions",
  "posting_frequency": "high/normal/low with count",
  "escalation_trend": "increasing/stable/de-escalating",
  "key_themes": ["main topics"],
  "market_alert": "any development that traders should act on immediately (or null)",
  "breaking_developments": "any ceasefire, deal, reversal, or major policy change in the last 24h"
}}"""
        raw = _grok_request("grok-4-1-fast-reasoning", MONITOR_INSTRUCTIONS, monitor_prompt, timeout=90.0)
        result = _extract_json(_strip_citations(raw))

        if not result:
            return {"success": True, "posts": [], "mood_summary": raw[:500]}

        posts = result.get("posts", [])

        # Persist new posts to Supabase
        db = _db()
        if db and posts:
            for post in posts:
                try:
                    db.table("trump_monitor_posts").upsert({
                        "post_text": post.get("text", "")[:2000],
                        "platform": post.get("platform", "unknown"),
                        "post_timestamp": post.get("timestamp"),
                        "interpretation": post.get("interpretation", ""),
                        "market_relevance": post.get("market_relevance", 0),
                        "category": post.get("category", ""),
                        "sentiment": post.get("sentiment", ""),
                        "fetched_at": datetime.now().isoformat(),
                    }, on_conflict="post_text,post_timestamp").execute()
                except Exception:
                    pass  # duplicate or parse error — skip

        return {
            "success": True,
            "posts": posts,
            "mood_summary": result.get("mood_summary", ""),
            "posting_frequency": result.get("posting_frequency", ""),
            "escalation_trend": result.get("escalation_trend", ""),
            "key_themes": result.get("key_themes", []),
            "market_alert": result.get("market_alert"),
            "breaking_developments": result.get("breaking_developments"),
        }

    except Exception as e:
        _log.error(f"Monitor fetch failed: {e}")
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Endpoint 5: GET /pattern-database
# ═══════════════════════════════════════════════════════════════

@router.get("/pattern-database")
async def pattern_database(
    query: str = "",
    category: str = "",
    user: str = Depends(get_current_user),
):
    """Search historical Trump statement-to-outcome cycles. Builds DB dynamically via Grok."""
    db = _db()

    # First check Supabase for existing patterns
    existing = []
    if db:
        try:
            q = db.table("trump_pattern_history").select("*").order("created_at", desc=True)
            if category:
                q = q.eq("category", category)
            result = q.limit(50).execute()
            existing = result.data or []
        except Exception:
            pass

    # If we have patterns and no specific query, return cached
    if existing and not query:
        return {"success": True, "patterns": existing, "source": "cached", "count": len(existing)}

    # Otherwise, ask Grok to search for patterns
    try:
        query_filter = ""
        if query:
            query_filter = f"Focus on cycles related to: {query}"
        elif category:
            query_filter = f"Focus on the '{category}' category (e.g. tariff threats, trade negotiations, etc.)"
        else:
            query_filter = "Cover the major categories: tariffs, China trade war, Fed criticism, Iran/foreign policy, executive orders. Get 10-15 of the most significant cycles."

        prompt = PATTERN_PROMPT_TEMPLATE.format(query_filter=query_filter)
        raw = _grok_request("grok-4-1-fast-reasoning", PATTERN_INSTRUCTIONS, prompt, timeout=90.0)
        patterns = _extract_json_array(_strip_citations(raw))

        if not patterns:
            # Return existing if Grok failed
            return {"success": True, "patterns": existing, "source": "cached", "count": len(existing)}

        # Persist new patterns to Supabase
        if db:
            for p in patterns:
                try:
                    db.table("trump_pattern_history").upsert({
                        "category": p.get("category", "unknown"),
                        "date_range": p.get("date_range", ""),
                        "trigger_statement": p.get("trigger_statement", ""),
                        "escalation_path": p.get("escalation_path", []),
                        "resolution": p.get("resolution", ""),
                        "resolution_type": p.get("resolution_type", ""),
                        "days_to_resolution": p.get("days_to_resolution"),
                        "market_impact_summary": p.get("market_impact_summary", ""),
                        "spy_move_pct": p.get("spy_move_pct"),
                        "vix_peak": p.get("vix_peak"),
                        "most_affected_sectors": p.get("most_affected_sectors", []),
                        "pattern_type": p.get("pattern_type", ""),
                        "bluff_score": p.get("bluff_score"),
                        "source_model": "grok-4-1-fast-reasoning",
                        "search_query": query or category or "general",
                        "created_at": datetime.now().isoformat(),
                    }, on_conflict="category,trigger_statement").execute()
                except Exception:
                    pass

        # Merge with existing (deduplicate by trigger_statement)
        seen = {p.get("trigger_statement", "") for p in patterns}
        for ex in existing:
            if ex.get("trigger_statement", "") not in seen:
                patterns.append(ex)

        return {"success": True, "patterns": patterns, "source": "fresh", "count": len(patterns)}

    except Exception as e:
        _log.error(f"Pattern database search failed: {e}")
        if existing:
            return {"success": True, "patterns": existing, "source": "cached_fallback", "count": len(existing)}
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Endpoint 6: GET /history (past decoded statements)
# ═══════════════════════════════════════════════════════════════

@router.get("/history")
async def decode_history(limit: int = 20, user: str = Depends(get_current_user)):
    """Get past decoded statements for this user."""
    db = _db()
    if not db:
        return {"success": False, "error": "Database unavailable"}

    try:
        result = db.table("trump_decoded_statements") \
            .select("*") \
            .eq("user_id", user) \
            .order("created_at", desc=True) \
            .limit(limit).execute()
        return {"success": True, "statements": result.data or []}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════
# Endpoint 7: PATCH /history/{id}/outcome (mark actual outcome)
# ═══════════════════════════════════════════════════════════════

@router.patch("/history/{statement_id}/outcome")
async def update_outcome(
    statement_id: int,
    actual_outcome: str = "",
    outcome_market_move: float = 0,
    was_accurate: bool = False,
    user: str = Depends(get_current_user),
):
    """Update a decoded statement with what actually happened. Builds calibration data."""
    db = _db()
    if not db:
        return {"success": False, "error": "Database unavailable"}

    try:
        db.table("trump_decoded_statements") \
            .update({
                "actual_outcome": actual_outcome,
                "outcome_date": datetime.now().isoformat(),
                "outcome_market_move": outcome_market_move,
                "was_accurate": was_accurate,
            }) \
            .eq("id", statement_id) \
            .eq("user_id", user) \
            .execute()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
