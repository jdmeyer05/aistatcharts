"""Hourly background worker — runs independently of Streamlit.

Updates Iran conflict analysis, situation briefing, timeline, and key metrics.
Designed to run via GitHub Actions cron, Windows Task Scheduler, or any scheduler.

Usage:
    python worker.py                    # Run all tasks
    python worker.py --task conflict    # Run only conflict analysis
    python worker.py --task metrics     # Run only metrics snapshots
    python worker.py --task cleanup     # Run only cache cleanup
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("worker")

# No LLM call may be allowed to hang. The OpenAI and Anthropic SDKs both default to
# 600s per attempt with 2 retries — up to ~30 min for a single stalled provider,
# inside a job whose `timeout-minutes` is 10. A stall would look exactly like a
# runner outage: the job dies with no error attributable to us.
#
# The budget, measured off four real runs on 2026-08-07 (31206592724..31219519492):
# setup — almost entirely `pip install` — takes 49-58s of the 600s cap, leaving
# ~540s for this script. `--task all` makes six LLM calls (4 Grok, 1 Gemini,
# 1 Claude), so 40s x 2 attempts x 6 = 480s worst case, leaving ~60s for the
# non-LLM work (metrics, options prewarm, cleanup). Those same runs finished
# end-to-end in 9-24s, so 40s per attempt is over an order of magnitude of headroom.
LLM_TIMEOUT_SECONDS = 40
LLM_MAX_RETRIES = 1


def _load_secrets():
    """Load API keys from environment or .streamlit/secrets.toml."""
    # GitHub Actions sets env vars; local dev uses secrets.toml
    if os.environ.get("SUPABASE_URL"):
        return  # already set

    try:
        import toml
        secrets = toml.load(".streamlit/secrets.toml")
        for key, val in secrets.items():
            if isinstance(val, str):
                os.environ.setdefault(key, val)
    except Exception:
        pass


def _get_db():
    """Get Supabase client directly (no Streamlit dependency)."""
    from supabase import create_client
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
    return create_client(url, key)


def _parse_json_response(raw):
    """Pull a JSON value out of an LLM response, tolerating the three things these
    models actually do (all observed live on 2026-08-07, real prompts):

    1. A prose preamble before a ```json fence. Claude Sonnet 5 opens with a caveat
       paragraph about not being able to verify the scenario, THEN emits the JSON.
       The old two-line strip only removed fences anchored at the very start/end of
       the string, so a preamble made json.loads fail at char 0.
    2. Tilde-marked estimates -- `"disruption_mbpd": ~2`. Grok does this because
       ACCURACY_CHECK_LIGHT (src/ai_validation.py) says "Label estimates with ~".
       That is right for the prose pages that also use the constant, and invalid
       JSON here, so strip it at the value position instead of editing the shared text.
    3. Plain fenced or bare objects, which already worked.

    Raises ValueError with the head of the payload when nothing parses, so a failure
    says what came back instead of surfacing an offset into a string nobody logged.
    """
    import re

    if not raw or not raw.strip():
        raise ValueError("empty response")

    text = raw.strip()

    # A fenced block anywhere wins -- it is the model's own explicit delimiter.
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text

    # Otherwise take the first balanced object/array, skipping any preamble.
    if not candidate.startswith(("{", "[")):
        start = min((i for i in (candidate.find("{"), candidate.find("[")) if i != -1),
                    default=-1)
        if start == -1:
            raise ValueError(f"no JSON found in: {text[:200]}")
        opener = candidate[start]
        closer = "}" if opener == "{" else "]"
        depth, in_str, escaped, end = 0, False, False, -1
        for i, ch in enumerate(candidate[start:], start):
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = not in_str
            elif not in_str:
                if ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        end = i
                        break
        if end == -1:
            raise ValueError(f"unterminated JSON in: {text[:200]}")
        candidate = candidate[start:end + 1]

    # "score": ~15  ->  "score": 15
    candidate = re.sub(r'(:\s*)~\s*(-?[\d.])', r"\1\2", candidate)

    try:
        return json.loads(candidate)
    except json.JSONDecodeError as e:
        raise ValueError(f"{e}; payload started: {candidate[:200]}") from e


def _get_openai_client(api_key, base_url=None):
    from openai import OpenAI
    kwargs = {
        "api_key": api_key,
        "timeout": LLM_TIMEOUT_SECONDS,
        "max_retries": LLM_MAX_RETRIES,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


# ─── TASK 1: CONFLICT SITUATION BRIEFING ──────────────────────

def update_situation_briefing(db):
    """Fetch fresh situation briefing from Grok and store in Supabase."""
    grok_key = os.environ.get("GROK_API_KEY")
    if not grok_key:
        logger.warning("GROK_API_KEY not set, skipping briefing")
        return

    today = datetime.now().strftime("%B %d, %Y %I:%M %p")
    prompt = f"""TODAY: {today}. Search X/Twitter and news sources RIGHT NOW for the latest on the Iran war situation.

CONTEXT: The US-Israel-Iran war started Feb 28, 2026. Khamenei was killed in initial strikes. We are now in week 4+.
Strait of Hormuz is CLOSED. Multiple rounds of US strikes on Iranian infrastructure. Trump issued ultimatums.

Write a comprehensive situation update covering the LAST 4 HOURS.

Cover ALL:
1. MILITARY: Latest strikes, missile launches, interceptions, casualties
2. HORMUZ & ENERGY: Strait status, tanker movements, oil prices, ultimatum countdown
3. DIPLOMATIC: Ceasefire signals, UN activity, mediators
4. X/TWITTER PULSE: What are @sentdefender, @Faytuks, @inside_IL_intel, @JavierBlas, @IranIntl_En posting?

250-400 words. Be direct and specific.

Before responding: verify all facts are from the last 4 hours. Do not invent events, casualty counts, or prices not confirmed by sources."""

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[
                {"role": "system", "content": "You are a war correspondent covering the 2026 Iran War. Direct, specific, urgent."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1500,
            temperature=0.3,
        )
        brief = response.choices[0].message.content.strip()
        if brief:
            # Cache in ai_response_cache with hourly key
            hour_key = f"situation_briefing_{datetime.now().strftime('%Y%m%d_%H')}"
            db.table("ai_response_cache").upsert({
                "input_hash": hour_key,
                "model": "grok-4-1-fast",
                "source_page": "iran_conflict",
                "ticker": "CONFLICT",
                "response": brief,
                "prompt_summary": "Hourly situation briefing",
                "expires_at": (datetime.now() + timedelta(hours=1.5)).isoformat(),
            }, on_conflict="input_hash").execute()
            logger.info(f"Situation briefing updated ({len(brief)} chars)")
        else:
            logger.warning("Grok returned empty briefing")
    except Exception as e:
        logger.error(f"Briefing update failed: {e}")


# ─── TASK 2: CONFLICT TIMELINE UPDATE ─────────────────────────

def update_timeline(db):
    """Search for new conflict events and persist to Supabase."""
    grok_key = os.environ.get("GROK_API_KEY")
    if not grok_key:
        return

    # Get the latest event date from the timeline table
    try:
        result = db.table("conflict_timeline").select("date, event")\
            .order("date", desc=True).limit(1).execute()
        if result.data:
            last_date = result.data[0]["date"]
            last_event = result.data[0]["event"]
        else:
            last_date = "2026-03-28"
            last_event = "Iran launches 47 ballistic missiles"
    except Exception:
        last_date = "2026-03-28"
        last_event = "Iran launches 47 ballistic missiles"

    prompt = f"""Search X/Twitter and news for MAJOR Iran war developments AFTER {last_date}.
Last known event: {last_event}

Only include events significant enough to move oil prices or change military posture.
Check: Reuters, Bloomberg, @sentdefender, @IranIntl_En, @JavierBlas, @IDF, @CENTCOM.

Return ONLY a JSON array of NEW events (empty array [] if nothing major):
[{{"date": "YYYY-MM-DD", "event": "what happened", "category": "Military/Escalation/Diplomatic/Policy/Supply", "impact": "market impact", "infrastructure": "infrastructure affected"}}]

Only CONFIRMED events. Do NOT fabricate. Verify each event has a named source before including it."""

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[
                {"role": "system", "content": "Return only confirmed events in JSON format."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1500,
            temperature=0.1,
        )
        raw = response.choices[0].message.content
        if not raw:
            return

        parsed = _parse_json_response(raw)
        events = parsed if isinstance(parsed, list) else parsed.get("events", parsed.get("timeline", []))

        added = 0
        for evt in events:
            if not evt.get("date") or not evt.get("event"):
                continue
            try:
                db.table("conflict_timeline").upsert({
                    "date": evt["date"],
                    "event": evt["event"],
                    "category": evt.get("category", "Military"),
                    "impact": evt.get("impact", ""),
                    "infrastructure": evt.get("infrastructure", ""),
                    "source": "worker_grok",
                }, on_conflict="date,event").execute()
                added += 1
            except Exception:
                pass
        logger.info(f"Timeline: {added} new events added")
    except Exception as e:
        logger.error(f"Timeline update failed: {e}")


# ─── TASK 3: 3-MODEL CONFLICT ANALYSIS ────────────────────────

def update_conflict_analysis(db):
    """Run the 3-model conflict analysis blend and store in Supabase."""
    grok_key = os.environ.get("GROK_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")

    if not grok_key:
        logger.warning("GROK_API_KEY not set, skipping conflict analysis")
        return

    # Get latest briefing for context
    try:
        hour_key = f"situation_briefing_{datetime.now().strftime('%Y%m%d_%H')}"
        brief_result = db.table("ai_response_cache").select("response")\
            .eq("input_hash", hour_key).limit(1).execute()
        briefing = brief_result.data[0]["response"] if brief_result.data else ""
    except Exception:
        briefing = ""

    from src.ai_validation import ACCURACY_CHECK_LIGHT
    base_prompt = f"""Analyze the current state of the US-Israel-Iran war (started Feb 28, 2026).

LATEST INTELLIGENCE:
{briefing[:1500]}

Provide your assessment as JSON:
{{
    "escalation_score": <1-10>,
    "escalation_level": "<Low/Moderate/High/Critical/Extreme>",
    "rationale": "<2-3 sentences with verified citations>",
    "oil_impact": {{
        "disruption_mbpd": <number>,
        "price_direction": "<up/down/stable>",
        "hormuz_status": "<open/restricted/closed>"
    }},
    "ceasefire_probability_30d": <0-100>,
    "situation_summary": "<3-4 sentence summary>"
}}

{ACCURACY_CHECK_LIGHT}"""

    assessments = []

    # Grok
    if grok_key:
        try:
            client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
            resp = client.chat.completions.create(
                model="grok-4-1-fast-reasoning",
                messages=[{"role": "user", "content": base_prompt}],
                max_tokens=1000, temperature=0.2,
            )
            raw = resp.choices[0].message.content
            data = _parse_json_response(raw)
            data["model"] = "Grok 4"
            assessments.append(data)
            logger.info("Grok assessment: done")
        except Exception as e:
            logger.warning(f"Grok assessment failed: {e}")

    # Gemini
    if gemini_key:
        try:
            from google import genai
            from google.genai import types
            # google-genai takes its timeout in MILLISECONDS, unlike the OpenAI and
            # Anthropic SDKs which take seconds. It exposes no retry count to cap.
            client = genai.Client(
                api_key=gemini_key,
                http_options=types.HttpOptions(timeout=LLM_TIMEOUT_SECONDS * 1000),
            )
            # Same trap the Claude call below was already fixed for: thinking shares
            # the output budget with the JSON body we parse. Measured 2026-08-07 on
            # this exact prompt — at max_output_tokens=1000 thinking took 957 of them
            # and left 39 for the JSON, so every run finished MAX_TOKENS and died on a
            # JSONDecodeError swallowed by the handler below. Gemini 3.1 Pro will NOT
            # let thinking be disabled (thinking_budget=0 -> 400 INVALID_ARGUMENT), and
            # thinking_level alone does not help while the budget is the binding limit.
            # 4000 + LOW measured 745 thinking / 246 output, finish STOP, 14.0s.
            resp = client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=base_prompt,
                config=types.GenerateContentConfig(
                    max_output_tokens=4000,
                    temperature=0.2,
                    thinking_config=types.ThinkingConfig(
                        thinking_level=types.ThinkingLevel.LOW
                    ),
                ),
            )
            finish = getattr(resp.candidates[0], "finish_reason", None) if resp.candidates else None
            if str(finish).endswith("MAX_TOKENS"):
                # Otherwise this resurfaces as a confusing JSONDecodeError on a
                # half-written object, which is what hid the bug for months.
                raise RuntimeError(
                    f"Gemini truncated before finishing the JSON (thinking used "
                    f"{getattr(resp.usage_metadata, 'thoughts_token_count', '?')} tokens)"
                )
            raw = resp.text
            data = _parse_json_response(raw)
            data["model"] = "Gemini 3.1 Pro"
            assessments.append(data)
            logger.info("Gemini assessment: done")
        except Exception as e:
            logger.warning(f"Gemini assessment failed: {e}")

    # Claude
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(
                api_key=anthropic_key,
                timeout=LLM_TIMEOUT_SECONDS,
                max_retries=LLM_MAX_RETRIES,
            )
            resp = client.messages.create(
                model="claude-sonnet-5",
                # Sonnet 5 thinks by default where 4.6 did not, and thinking
                # shares max_tokens with the JSON body we need to parse below.
                thinking={"type": "disabled"},
                max_tokens=1000,
                messages=[{"role": "user", "content": base_prompt}],
            )
            if resp.stop_reason == "refusal":
                # Otherwise this surfaces as a confusing JSON decode error below.
                raise RuntimeError("Claude declined the assessment request")
            raw = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
            data = _parse_json_response(raw)
            data["model"] = "Claude Sonnet"
            assessments.append(data)
            logger.info("Claude assessment: done")
        except Exception as e:
            logger.warning(f"Claude assessment failed: {e}")

    if not assessments:
        logger.error("No model assessments completed")
        return

    # Blend scores
    scores = [a.get("escalation_score", 5) for a in assessments]
    avg_score = sum(scores) / len(scores)
    level = "Extreme" if avg_score >= 9 else "Critical" if avg_score >= 7 else "High" if avg_score >= 5 else "Moderate"

    # Build summary from best assessment
    best = max(assessments, key=lambda a: len(a.get("rationale", "")))
    summary = best.get("situation_summary", "")

    # Store in Supabase
    try:
        db.table("conflict_analysis").insert({
            "user_id": "worker",
            "region": "iran",
            "situation_summary": summary,
            "escalation_risk": json.dumps({
                "score": round(avg_score, 1),
                "level": level,
                "model_assessments": [{
                    "model": a.get("model", "?"),
                    "score": a.get("escalation_score", 5),
                    "rationale": a.get("rationale", ""),
                } for a in assessments],
            }),
            "models_used": [a.get("model", "?") for a in assessments],
            "latest_developments": json.dumps([]),
            "infrastructure_status": json.dumps({
                "hormuz": best.get("oil_impact", {}).get("hormuz_status", "unknown"),
                "disruption_mbpd": best.get("oil_impact", {}).get("disruption_mbpd", 0),
            }),
        }).execute()
        logger.info(f"Conflict analysis stored: {avg_score:.1f}/10 ({level}), {len(assessments)} models")
    except Exception as e:
        logger.error(f"Failed to store conflict analysis: {e}")


# ─── TASK 4: METRICS SNAPSHOTS ─────────────────────────────────

def update_metrics_snapshots(db):
    """Save daily metrics and pre-warm price_history for popular tickers."""
    import requests
    import math

    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        logger.warning("MASSIVE_API_KEY not set, skipping metrics")
        return

    # Core tickers for metrics snapshots (HV20 computed)
    METRICS_TICKERS = ["SPY", "QQQ", "IWM", "TLT", "GLD", "USO", "XLE", "XLF", "AAPL", "MSFT"]

    # Extended pre-warm list — popular tickers users load on first visit
    PREWARM_TICKERS = [
        "SPY", "QQQ", "IWM", "TLT", "GLD", "USO", "XLE", "XLF", "AAPL", "MSFT",
        "NVDA", "TSLA", "AMZN", "GOOGL", "META", "JPM", "V", "UNH", "XLK", "XLV",
        "DIA", "EEM", "HYG", "LQD", "SLV",
    ]

    today = datetime.now().strftime("%Y-%m-%d")

    for ticker in PREWARM_TICKERS:
        try:
            # Check if price_history already has recent data
            try:
                check = db.table("price_history").select("date")\
                    .eq("ticker", ticker).order("date", desc=True).limit(1).execute()
                if check.data:
                    last_date = check.data[0]["date"]
                    days_stale = (datetime.now().date() - datetime.strptime(last_date, "%Y-%m-%d").date()).days
                    if days_stale <= 1:
                        # Already current — just update metrics if needed
                        if ticker in METRICS_TICKERS:
                            _update_ticker_metrics(db, ticker, api_key, today)
                        continue
                    # Stale — only fetch the gap
                    gap_start = (datetime.strptime(last_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
                    fetch_start = gap_start
                else:
                    # Cold start — fetch 3 years (756 trading days)
                    fetch_start = (datetime.now() - timedelta(days=1100)).strftime("%Y-%m-%d")
            except Exception:
                fetch_start = (datetime.now() - timedelta(days=1100)).strftime("%Y-%m-%d")

            # Fetch from Polygon
            url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{fetch_start}/{today}"
            r = requests.get(url, params={"apiKey": api_key, "sort": "asc", "limit": 50000}, timeout=30)
            data = r.json()
            results = data.get("results", [])
            if not results:
                continue

            # Save to price_history in batches
            rows = []
            for bar in results:
                bar_date = datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d")
                rows.append({"ticker": ticker, "date": bar_date, "close": bar["c"]})
            for i in range(0, len(rows), 100):
                try:
                    db.table("price_history").upsert(
                        rows[i:i+100], on_conflict="ticker,date"
                    ).execute()
                except Exception:
                    pass

            logger.info(f"Price history: {ticker} — {len(rows)} bars saved")

            # Update metrics for core tickers
            if ticker in METRICS_TICKERS:
                _update_ticker_metrics(db, ticker, api_key, today)

        except Exception as e:
            logger.warning(f"Pre-warm failed for {ticker}: {e}")


def _update_ticker_metrics(db, ticker: str, api_key: str, today: str):
    """Compute and save HV20 metrics for a single ticker."""
    import requests
    import math

    try:
        start = (datetime.now() - timedelta(days=35)).strftime("%Y-%m-%d")
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start}/{today}"
        r = requests.get(url, params={"apiKey": api_key, "sort": "asc", "limit": 50}, timeout=15)
        results = r.json().get("results", [])
        if not results:
            return

        closes = [bar["c"] for bar in results]
        spot = closes[-1]

        hv20 = None
        if len(closes) >= 21:
            rets = [math.log(closes[i] / closes[i-1]) for i in range(1, len(closes))]
            hv20 = (sum(r**2 for r in rets[-20:]) / 20) ** 0.5 * (252 ** 0.5)

        db.table("metrics_history").upsert({
            "user_id": "worker",
            "ticker": ticker,
            "date": today,
            "spot": spot,
            "hv20": hv20,
        }, on_conflict="user_id,ticker,date").execute()

        logger.info(f"Metrics updated: {ticker} ${spot:.2f}")
    except Exception as e:
        logger.warning(f"Metrics update failed for {ticker}: {e}")


# ─── TASK 5: OPTIONS CHAIN PRE-WARM ────────────────────────────

def prewarm_options_chains(db):
    """Pre-fetch options chains for popular tickers into api_cache.
    Pages read from api_cache first (~100ms) instead of hitting Polygon (~2-5s)."""
    import requests

    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        logger.warning("MASSIVE_API_KEY not set, skipping options prewarm")
        return

    OPTIONS_TICKERS = ["SPY", "QQQ", "AAPL"]

    for ticker in OPTIONS_TICKERS:
        try:
            # Fetch full chain snapshot from Polygon
            url = f"https://api.polygon.io/v3/snapshot/options/{ticker}"
            params = {"limit": 250, "apiKey": api_key}
            all_results = []
            next_url = None

            for _ in range(10):  # max 10 pages
                if next_url:
                    r = requests.get(next_url, params={"apiKey": api_key}, timeout=15)
                else:
                    r = requests.get(url, params=params, timeout=15)
                data = r.json()
                results = data.get("results", [])
                all_results.extend(results)
                next_url = data.get("next_url")
                if not next_url:
                    break

            if not all_results:
                continue

            # Process raw Polygon results into same format as fetch_options_chain
            rows = []
            for r in all_results:
                d = r.get('details', {})
                g = r.get('greeks', {})
                day = r.get('day', {})
                quote = r.get('last_quote', {})
                bid = quote.get('bid') or 0
                ask = quote.get('ask') or 0
                day_close = day.get('close', 0) or 0
                day_vwap = day.get('vwap', 0) or 0
                quote_is_live = bid > 0 and ask > 0
                if bid == 0 and day_close > 0:
                    bid = day_close * 0.95
                if ask == 0 and day_close > 0:
                    ask = day_close * 1.05
                rows.append({
                    'strike_price': d.get('strike_price'),
                    'contract_type': d.get('contract_type'),
                    'expiration_date': d.get('expiration_date'),
                    'bid': bid, 'ask': ask,
                    'last_price': day_close or day_vwap or 0,
                    'quote_live': quote_is_live,
                    'volume': day.get('volume', 0),
                    'open_interest': r.get('open_interest', 0),
                    'implied_volatility': r.get('implied_volatility', 0),
                    'delta': g.get('delta', 0), 'gamma': g.get('gamma', 0),
                    'theta': g.get('theta', 0), 'vega': g.get('vega', 0),
                    'rho': g.get('rho', 0),
                    'day_open': day.get('open', 0) or 0,
                    'day_high': day.get('high', 0) or 0,
                    'day_low': day.get('low', 0) or 0,
                    'day_vwap': day_vwap,
                    'trade_count': day.get('trade_count', 0) or 0,
                })

            # Store in api_cache with 2h TTL (same key+format as fetch_options_chain)
            cache_key = f"chain_{ticker}_all"
            db.table("api_cache").upsert({
                "cache_key": cache_key,
                "response": rows,
                "endpoint": f"/v3/snapshot/options/{ticker}",
                "symbol": ticker,
                "ttl_seconds": 7200,
                "created_at": datetime.now().isoformat(),
                "expires_at": (datetime.now() + timedelta(hours=2)).isoformat(),
            }, on_conflict="cache_key").execute()

            logger.info(f"Options prewarm: {ticker} — {len(rows)} contracts cached")
        except Exception as e:
            logger.warning(f"Options prewarm failed for {ticker}: {e}")


# ─── TASK 6: CACHE CLEANUP ────────────────────────────────────

def cleanup_caches(db):
    """Remove expired cache entries and prune old data."""
    try:
        db.rpc("cleanup_expired_cache").execute()
        db.rpc("cleanup_expired_ai_cache").execute()
        db.rpc("cleanup_old_signals").execute()
        logger.info("RPC cache cleanup complete")
    except Exception as e:
        logger.warning(f"RPC cleanup failed: {e}")

    # Prune iv_surface_snapshots older than 30 days (unbounded growth)
    try:
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        result = db.table("iv_surface_snapshots").delete()\
            .lt("date", cutoff).execute()
        n = len(result.data) if result.data else 0
        if n > 0:
            logger.info(f"IV surface snapshots: pruned {n} rows older than {cutoff}")
    except Exception as e:
        logger.warning(f"IV surface cleanup failed: {e}")

    # Prune price_history older than 3 years (keep storage bounded)
    try:
        cutoff = (datetime.now() - timedelta(days=1100)).strftime("%Y-%m-%d")
        result = db.table("price_history").delete()\
            .lt("date", cutoff).execute()
        n = len(result.data) if result.data else 0
        if n > 0:
            logger.info(f"Price history: pruned {n} rows older than {cutoff}")
    except Exception as e:
        logger.warning(f"Price history cleanup failed: {e}")


# ─── TASK 7: MARKET NEWS SCAN ────────────────────────────────

def update_market_news_scan(db):
    """Scan for market-moving news via Grok with live X/Twitter search.

    Runs hourly during market hours. Cached in ai_response_cache,
    shared across all users. Read by the Summary landing page.
    Cost: ~$0.01/call × 8/day = ~$0.08/day.
    """
    grok_key = os.environ.get("GROK_API_KEY")
    if not grok_key:
        logger.warning("GROK_API_KEY not set, skipping market news scan")
        return

    now = datetime.now()
    today = now.strftime("%B %d, %Y %I:%M %p ET")
    weekday = now.strftime("%A")

    prompt = f"""TODAY: {weekday}, {today}. Search X/Twitter and financial news RIGHT NOW.

Report the most market-moving developments from the LAST 4 HOURS. If it is pre-market, focus on overnight moves, Asian/European session, and the setup for today's US session.

COVER (only what's actually happening — skip categories with nothing notable):
1. MACRO & FED — CPI/PPI/jobs data, Fed speakers, rate expectations, Treasury auctions
2. EARNINGS — beats/misses from the last 12 hours, guidance changes, pre-market movers
3. GEOPOLITICAL — trade policy, sanctions, conflicts, tariffs affecting markets
4. SECTOR MOVES — notable rotation, breakouts, or breakdowns by sector
5. COMMODITIES & FX — oil/gold/dollar/crypto moves with catalysts
6. OPTIONS & FLOW — unusual volume, large blocks, VIX moves, put/call skew shifts

SOURCES TO CHECK: @CNBC, @Bloomberg, @zaborsky, @DeItaone, @Fxhedgers, @unusual_whales, @spotgamma, @JavierBlas, @NickTimiraos, @LiveSquawk

FORMAT: Lead with the single biggest story. Then bullet the rest. 200-300 words max. Be specific — name tickers, numbers, percentages. No filler.

ACCURACY: Only report confirmed developments. Do not speculate or fabricate. If markets are quiet, say so briefly."""

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[
                {"role": "system", "content": (
                    "You are a senior market intelligence analyst at a quantitative trading firm. "
                    "Your job: scan X/Twitter and news for the developments that are actually moving "
                    "markets right now. Be direct, specific, and quantitative. No boilerplate."
                )},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
            temperature=0.2,
        )
        news = response.choices[0].message.content.strip()
        if news:
            hour_key = f"market_news_{now.strftime('%Y%m%d_%H')}"
            db.table("ai_response_cache").upsert({
                "input_hash": hour_key,
                "model": "grok-4-1-fast",
                "source_page": "market_news",
                "ticker": "MARKET",
                "response": news,
                "prompt_summary": "Hourly market-moving news scan",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(hours=1.5)).isoformat(),
            }, on_conflict="input_hash").execute()
            logger.info(f"Market news scan updated ({len(news)} chars)")
        else:
            logger.warning("Grok returned empty market news")
    except Exception as e:
        logger.error(f"Market news scan failed: {e}")


# ─── TASK 8: TRUMP DECODER VALIDATION ──────────────────────────

def validate_trump_decodes(db):
    """Grade past Trump decodes by measuring actual SPY movement 72h after
    the decode was submitted, then writing was_accurate back to Supabase.

    Scoring rule (matches /api/trump/track-record):
      - bluff_score ≥ 70 (bluff call) → correct if |SPY 72h move| < 1%
      - bluff_score < 40 (genuine call) → correct if |SPY 72h move| ≥ 1%
      - 40-69 (uncertain band) → skipped, left ungraded

    Only grades decodes 72h < age < 14d — older ones are unreliable to
    score retroactively (market drifted for reasons unrelated to the call).
    """
    import requests

    api_key = os.environ.get("MASSIVE_API_KEY")
    if not api_key:
        logger.warning("MASSIVE_API_KEY not set, skipping trump validation")
        return

    now = datetime.utcnow()
    cutoff = (now - timedelta(hours=72)).isoformat()
    floor = (now - timedelta(days=14)).isoformat()

    try:
        rows = db.table("trump_decoded_statements") \
            .select("id,created_at,bluff_score") \
            .is_("was_accurate", "null") \
            .lte("created_at", cutoff) \
            .gte("created_at", floor) \
            .limit(50).execute().data or []
    except Exception as e:
        logger.warning(f"trump_validate: fetch ungraded failed: {e}")
        return

    if not rows:
        logger.info("trump_validate: no pending decodes to grade")
        return

    graded = 0
    skipped = 0
    for row in rows:
        bluff = row.get("bluff_score")
        if bluff is None:
            continue
        if 40 <= bluff < 70:
            skipped += 1
            continue

        try:
            created_str = row["created_at"]
            # Supabase returns ISO-8601; normalize "Z" suffix for fromisoformat.
            start_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        except Exception:
            continue

        # Pad 5 days so weekends/holidays don't leave us short on bars.
        start_str = start_dt.strftime("%Y-%m-%d")
        end_str = (start_dt + timedelta(days=5)).strftime("%Y-%m-%d")

        try:
            url = f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/{start_str}/{end_str}"
            r = requests.get(url, params={"apiKey": api_key, "sort": "asc"}, timeout=30)
            bars = r.json().get("results") or []
            if len(bars) < 2:
                continue
            start_close = bars[0]["c"]
            # Pick the bar ~3 trading days after start — falls back to last
            # bar if we have fewer (e.g. decode near a holiday stretch).
            end_close = bars[min(3, len(bars) - 1)]["c"]
            move_pct = (end_close / start_close - 1) * 100
        except Exception as e:
            logger.warning(f"trump_validate: SPY fetch failed for id={row['id']}: {e}")
            continue

        if bluff >= 70:
            was_accurate = abs(move_pct) < 1.0
        else:  # bluff < 40
            was_accurate = abs(move_pct) >= 1.0

        try:
            db.table("trump_decoded_statements").update({
                "outcome_date": now.isoformat(),
                "outcome_market_move": round(move_pct, 2),
                "was_accurate": was_accurate,
                "actual_outcome": f"SPY {move_pct:+.2f}% over 72h (auto-validated)",
            }).eq("id", row["id"]).execute()
            graded += 1
        except Exception as e:
            logger.warning(f"trump_validate: DB update failed for id={row['id']}: {e}")

    logger.info(f"trump_validate: graded {graded}, skipped {skipped} (uncertain band), checked {len(rows)}")


# ─── TASK 9: DAILY SECTOR REFRESH ──────────────────────────────

def refresh_sectors(db):
    """Force-refresh every SPDR sector's 7 cached endpoints by busting cache
    keys and re-running the compute functions. Targets ~5pm ET daily so the
    sector analysis page opens with <24h-old fundamentals regardless of
    whether any user has triggered a cold fetch.

    Bypasses the 12h natural TTL of the @result_cached decorator by deleting
    the Supabase row (and in-process memo) before each call — so every run
    pulls fresh upstream data rather than returning a recent cached value.
    """
    try:
        from api.routes.sectors import (
            _compute_sector_overview,
            _compute_sector_capex,
            _compute_sector_valuation,
            _compute_sector_alpha,
            _compute_sector_prices,
            _compute_sector_guidance,
            _compute_sector_market,
            SECTOR_CONFIGS,
        )
        from src._cache_util import _RESULT_CACHE
    except Exception as e:
        logger.warning(f"refresh_sectors: import failed: {e}")
        return

    computes = [
        ("sector_overview",  _compute_sector_overview),
        ("sector_capex",     _compute_sector_capex),
        ("sector_valuation", _compute_sector_valuation),
        ("sector_alpha",     _compute_sector_alpha),
        ("sector_prices",    _compute_sector_prices),
        ("sector_guidance",  _compute_sector_guidance),
        ("sector_market",    _compute_sector_market),
    ]
    etfs = list(SECTOR_CONFIGS.keys())

    # Cache-key format tracks src._cache_util._stable_key's output for a
    # single-str-arg compute fn. We can't call _stable_key(fn, ...) directly
    # here because the @_result_cached wrapper's externally-visible signature
    # is (*args, **kwargs), so inspect.signature().bind() produces an empty
    # items list. Going straight to the final string keeps the bust aligned.
    def _bust_key(prefix: str, etf: str) -> str:
        return f"{prefix}:[('etf', {etf!r})]"

    ok = 0
    fail = 0
    for etf in etfs:
        for key, fn in computes:
            cache_key = _bust_key(key, etf)
            _RESULT_CACHE.pop(cache_key, None)
            try:
                db.table("cftc_cache").delete().eq("key", cache_key).execute()
            except Exception:
                # Supabase transient failure — proceed; recompute still wins at
                # the in-memory tier and will re-write both tiers on success.
                pass
            try:
                fn(etf)
                ok += 1
            except Exception as e:
                logger.warning(f"refresh_sectors: {key} {etf} failed: {e}")
                fail += 1
    logger.info(f"refresh_sectors: {ok} ok, {fail} failed across {len(etfs)} sectors × {len(computes)} endpoints")


# ─── MAIN ─────────────────────────────────────────────────────

# ─── TASK 10: SELF-IMPROVING PROMPT LOOP ───────────────────────
#
# Four stages, split by what they cost. See src/prompt_loop.py for the design;
# what matters here is that the free half (grading, claim resolution) runs on
# the hourly cron and the expensive half (critique, replay) runs nightly, so a
# failure in the improvement machinery can never stop the measurement.


def prompt_loop_seed(db):
    """Record the git baseline as version 0 for every surface. Idempotent."""
    try:
        from src.prompt_registry import seed_baselines
        res = seed_baselines()
        logger.info(f"prompt_seed: {res}")
    except Exception as e:
        logger.error(f"prompt_seed failed: {e}")


# THE LOOP CAN ONLY MEASURE WHAT WAS GENERATED, and a snapshot is only written on
# a cache MISS of a real inbound request — which made the entire record
# traffic-gated. Between 2026-08-20 and 2026-08-23 the nightly crons ran clean
# every night and read nothing, because nothing had been generated in between:
# `prompt_critique` wants 10 graded discovery rows per surface and had 2. The
# intake is therefore driven rather than waited for.
_PROMPT_PING_ENDPOINTS = (
    ("market_driver", "/api/market/market-driver"),
    # es-brief before es-card-audit: the audit reads the assembled brief, so this
    # order lets it reuse the 90-second cache the ping just filled instead of
    # paying for a second build.
    ("news_digest", "/api/market/es-brief"),
    ("es_audit", "/api/market/es-card-audit"),
)
_PROMPT_PING_TIMEOUT_S = 240


def _ping_state(surface: str, body: dict) -> str:
    """Did the ping produce a generation, or just re-serve a cache?

    Each endpoint reports this differently and one does not report it at all, so
    an unknown says "served" rather than guessing. A ping that quietly hit cache
    every hour and a ping that was quietly broken produce the same empty record,
    and the log is the only thing that separates them.
    """
    if surface == "market_driver":
        hit = body.get("cache_hit")
    elif surface == "news_digest":
        digest = body.get("news_digest")
        if not isinstance(digest, dict):
            # Fewer than three usable headlines — the digest declines to write a
            # placeholder, so there is nothing to record and nothing is wrong.
            return "no digest (too few headlines)"
        hit = digest.get("cached")
    else:
        return "served (endpoint reports no cache state)"
    if hit is True:
        return "cache hit, no new snapshot"
    if hit is False:
        return "fresh generation"
    return "served"


def prompt_loop_ping(db):
    """Drive the AI surfaces on a schedule so the loop has a record to read.

    This hits the same public endpoints a visitor hits — no private path, no
    payload reassembled here. The row that gets recorded is therefore the row a
    reader would have been served; anything assembled in this worker would be
    grading a payload nobody saw.

    Cost stays governed by the caches that were already there rather than by this
    cadence: market-driver re-serves its Supabase bundle inside a session-aware
    TTL (15 min in the cash session, 6 hours when the market is shut), and the
    digest is keyed on a fingerprint of the headlines, so a quiet hour generates
    nothing. The card audit holds only a 10-minute in-process cache and so does
    regenerate on every ping.

    THE BIAS WORTH NAMING: samples now arrive on the clock rather than when
    someone browses. The discovery/holdout split hashes the payload and not the
    time, so the split itself is unaffected — but the record over-represents the
    top of the hour, and under-represents whatever a human would have been
    looking at when they chose to look.

    `home_interpret` is deliberately absent. Its payload is assembled in the
    browser from nine separate responses, so pinging it would mean rebuilding
    that merge here and grading a payload that no longer matches the page.
    """
    import requests

    base = (os.environ.get("API_BASE_URL") or "").strip().rstrip("/")
    if not base:
        logger.warning("prompt_ping: API_BASE_URL not set, skipping intake ping")
        return

    for surface, path in _PROMPT_PING_ENDPOINTS:
        try:
            r = requests.get(f"{base}{path}", timeout=_PROMPT_PING_TIMEOUT_S)
        except Exception as e:
            logger.warning(f"prompt_ping: {surface} request failed: {e}")
            continue
        if r.status_code != 200:
            logger.warning(f"prompt_ping: {surface} -> HTTP {r.status_code}")
            continue
        try:
            body = r.json()
        except Exception:
            body = {}
        logger.info(f"prompt_ping: {surface} {_ping_state(surface, body)} "
                    f"({r.elapsed.total_seconds():.1f}s)")


def prompt_loop_grade(db):
    """Deterministic grading of new snapshots + settle any due claims."""
    try:
        from src.prompt_loop import run_all
        res = run_all("grade")
        graded = res.get("grade", {}).get("surfaces", {})
        for surface, stat in graded.items():
            if stat.get("graded"):
                logger.info(f"prompt_grade: {surface} graded {stat['graded']} new outputs")
        claims = res.get("claims", {})
        if claims.get("resolved") or claims.get("expired"):
            logger.info(f"prompt_grade: claims {claims}")
    except Exception as e:
        logger.error(f"prompt_grade failed: {e}")


def prompt_loop_regrade(db, days: int = 30):
    """Re-score every stored snapshot under the CURRENT rules.

    Deliberately manual and never part of `all`: it is the thing you run after
    fixing a rule, so that the record the critic reads describes the rules that
    exist rather than the ones that were replaced.
    """
    try:
        from src.prompt_loop import grade_pending
        res = grade_pending(days=days, regrade=True)
        for surface, stat in (res.get("surfaces") or {}).items():
            logger.info(f"prompt_regrade: {surface} rescored {stat.get('graded', 0)} "
                        f"of {stat.get('scanned', 0)} scanned")
    except Exception as e:
        logger.error(f"prompt_regrade failed: {e}")


def prompt_loop_critique(db):
    """Adversarial read of each surface's record; proposes challengers."""
    try:
        from src.prompt_loop import run_all
        res = run_all("critique")
        for surface, out in res.items():
            logger.info(f"prompt_critique: {surface} -> {out.get('skipped') or out.get('note') or out}")
    except Exception as e:
        logger.error(f"prompt_critique failed: {e}")


def prompt_loop_evaluate(db):
    """Replay open challengers on holdout payloads; promote what earns it."""
    try:
        from src.prompt_loop import run_all
        res = run_all("evaluate")
        for surface, out in res.items():
            if out.get("skipped"):
                logger.info(f"prompt_evaluate: {surface} skipped ({out['skipped']})")
                continue
            logger.info(
                f"prompt_evaluate: {surface} decision={out.get('decision')} "
                f"verdict={out.get('verdict')} n={out.get('n')} "
                f"diff={out.get('mean_diff')} ci={out.get('ci95')} "
                f"reasons={out.get('reasons')}"
            )
    except Exception as e:
        logger.error(f"prompt_evaluate failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="AI Statcharts hourly worker")
    parser.add_argument("--task", choices=["all", "conflict", "briefing", "timeline",
                                            "metrics", "cleanup", "prewarm", "options",
                                            "market_news", "trump_validate", "sector_refresh",
                                            "prompt_seed", "prompt_ping", "prompt_grade",
                                            "prompt_regrade", "prompt_critique",
                                            "prompt_evaluate"],
                        default="all", help="Which task to run")
    args = parser.parse_args()

    _load_secrets()
    db = _get_db()
    logger.info(f"Worker started: task={args.task}")

    if args.task in ("all", "briefing"):
        update_situation_briefing(db)

    if args.task in ("all", "timeline"):
        update_timeline(db)

    if args.task in ("all", "conflict"):
        update_conflict_analysis(db)

    if args.task in ("all", "metrics", "prewarm"):
        update_metrics_snapshots(db)

    if args.task in ("all", "options", "prewarm"):
        prewarm_options_chains(db)

    if args.task in ("all", "market_news"):
        update_market_news_scan(db)

    if args.task in ("all", "trump_validate"):
        validate_trump_decodes(db)

    # The prompt loop's hourly half. Grading is deterministic and claim
    # resolution is a price lookup, so both belong here. The ping is the one
    # step in this job that provokes model calls, but it provokes them on the
    # API side and only past each surface's own cache, so it buys a warm card
    # for whoever loads the page next as well as a row for the record.
    # The two stages that spend on REASONING run on their own schedule below.
    if args.task in ("all", "prompt_ping"):
        prompt_loop_ping(db)

    if args.task in ("all", "prompt_grade"):
        prompt_loop_grade(db)

    if args.task == "prompt_regrade":
        prompt_loop_regrade(db)

    # LLM stages — never in "all". Critique is two Opus calls and evaluate is
    # ~50 generations, which would blow the hourly job's 10-minute budget and
    # would be pointless at hourly cadence anyway: a day's snapshots is the
    # smallest sample worth re-reading.
    if args.task == "prompt_seed":
        prompt_loop_seed(db)

    if args.task == "prompt_critique":
        prompt_loop_critique(db)

    if args.task == "prompt_evaluate":
        prompt_loop_evaluate(db)

    # sector_refresh is NOT in "all" — runs on its own daily cron (5pm ET)
    # rather than hourly to avoid hammering Polygon/yfinance with 77 calls
    # every hour.
    if args.task == "sector_refresh":
        refresh_sectors(db)

    if args.task in ("all", "cleanup"):
        cleanup_caches(db)

    logger.info("Worker finished")


if __name__ == "__main__":
    main()
