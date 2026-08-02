"""A few lines on what the overnight headlines add up to.

Read before the bell, so the job is SYNTHESIS, not a verdict: what changed since
the last close and what it sets up for the open. Deliberately not a call — the
rest of this cockpit is built on the principle that context and signal are
different products, and a paragraph of prose is the easiest place to blur them.

Cached on a hash of the headlines themselves rather than on a clock. The same
set of stories always yields the same digest, it regenerates the moment the
feeds move, and a quiet Sunday costs nothing.

Sonnet rather than Opus: this is summarising eleven sentences someone else
wrote, it runs on every news refresh, and it sits on the card a trader is
waiting to read at 09:25. Latency is the binding constraint, not depth.
"""

from __future__ import annotations

import hashlib
import logging

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-5"
_MAX_HEADLINES = 12
_CACHE_TTL_HOURS = 6

_SYSTEM = """You brief an intraday S&P futures trader before the cash open. You are given the macro headlines that have accumulated, already ranked by how much they move the index, each with its age.

Write 2-3 sentences. Under 70 words. No bullets, no heading, no preamble.

What to write:
- What actually changed since the last close, and what it leaves unresolved into the open.
- Where the headlines agree or conflict with each other. Say so when they are simply quiet — "nothing new since Friday" is a useful and honest brief.
- Weight by the tiers given. Tier 1 is policy and hard data. Tier 3 is single-company news and rarely matters to the index.

Hard rules:
- Use ONLY the headlines provided. Never add a number, name, ticker or event that is not in them.
- Never state or imply a direction to trade, a level, or a bias. This is context, not a signal. If a headline suggests pressure, describe the pressure, not the trade.
- Do not restate headlines one by one. If they add up to nothing, say that in one sentence.
- Prefer plain language over market jargon. No "risk-on", no "constructive"."""


def _fingerprint(headlines: list[dict]) -> str:
    joined = "|".join(h.get("title", "") for h in headlines[:_MAX_HEADLINES])
    return hashlib.md5(joined.encode("utf-8")).hexdigest()


def news_digest(headlines: list[dict]) -> dict | None:
    """Synthesis of the ranked headlines, or None when there is nothing to say.

    Returns None rather than a placeholder on every failure path — the card
    already renders the headlines themselves, and an empty summary line is
    worse than no summary line.
    """
    if not headlines:
        return None
    usable = [h for h in headlines[:_MAX_HEADLINES] if h.get("title")]
    if len(usable) < 3:
        return None

    key = None
    try:
        from src.ai_cache import build_cache_key, get_cached_ai, cache_ai_response
        key = build_cache_key("news_digest", "ES", _fingerprint(usable), MODEL)
        hit = get_cached_ai(key)
        if hit:
            return {"text": hit, "model": MODEL, "cached": True,
                    "n_headlines": len(usable)}
    except Exception as e:
        logger.debug(f"news digest cache lookup failed: {e}")

    # Tier and age travel with each headline so the model can weight them the
    # same way the ranking already does, instead of inferring importance from
    # word choice.
    lines = []
    for h in usable:
        age = h.get("age") or "unknown age"
        hrs = h.get("hours_ago")
        when = f"{hrs:.0f}h ago, {age}" if isinstance(hrs, (int, float)) else age
        lines.append(f"[tier {h.get('tier', '?')}] ({when}) {h['title']} — {h.get('source', '')}")
    payload = "\n".join(lines)

    try:
        import anthropic
        from src.api_keys import get_secret
        api_key = get_secret("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(
            model=MODEL,
            # Sonnet 5 runs adaptive thinking when `thinking` is omitted, and
            # max_tokens caps thinking AND the answer together — so the ceiling
            # is headroom for both, not for the ~70 words alone.
            max_tokens=2000,
            # Low effort, not disabled thinking. This is summarising eleven
            # sentences someone else wrote; the recommended way to cut latency
            # on this model is a lower effort with thinking left on.
            output_config={"effort": "low"},
            # No `fallbacks` here: it is an Opus-5/Fable-5 parameter and Sonnet 5
            # rejects it outright with a 400.
            system=_SYSTEM,
            messages=[{"role": "user",
                       "content": f"Headlines, most index-relevant first:\n\n{payload}"}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        if not text:
            return None
    except Exception as e:
        logger.warning(f"news digest failed: {e}")
        return None

    if key:
        try:
            from src.ai_cache import cache_ai_response
            cache_ai_response(key, text, model=MODEL, source_page="es_brief",
                              ticker="ES", ttl_hours=_CACHE_TTL_HOURS)
        except Exception as e:
            logger.debug(f"news digest cache write failed: {e}")

    return {"text": text, "model": MODEL, "cached": False, "n_headlines": len(usable)}
