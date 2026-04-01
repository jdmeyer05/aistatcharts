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


def _get_openai_client(api_key, base_url=None):
    from openai import OpenAI
    kwargs = {"api_key": api_key}
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

        import re
        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
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
            import re
            cleaned = re.sub(r"^```json?\s*", "", raw.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned)
            data = json.loads(cleaned)
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
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model="gemini-3.1-pro-preview",
                contents=base_prompt,
                config=types.GenerateContentConfig(max_output_tokens=1000, temperature=0.2),
            )
            raw = resp.text
            import re
            cleaned = re.sub(r"^```json?\s*", "", raw.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned)
            data = json.loads(cleaned)
            data["model"] = "Gemini 3.1 Pro"
            assessments.append(data)
            logger.info("Gemini assessment: done")
        except Exception as e:
            logger.warning(f"Gemini assessment failed: {e}")

    # Claude
    if anthropic_key:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=anthropic_key)
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                messages=[{"role": "user", "content": base_prompt}],
            )
            raw = resp.content[0].text
            import re
            cleaned = re.sub(r"^```json?\s*", "", raw.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned)
            data = json.loads(cleaned)
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


# ─── MAIN ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AI Statcharts hourly worker")
    parser.add_argument("--task", choices=["all", "conflict", "briefing", "timeline",
                                            "metrics", "cleanup", "prewarm", "options",
                                            "market_news"],
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

    if args.task in ("all", "cleanup"):
        cleanup_caches(db)

    logger.info("Worker finished")


if __name__ == "__main__":
    main()
