import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import requests
import os
import re
import json
import logging
import time
import concurrent.futures
from datetime import date, datetime, timedelta
from openai import OpenAI
from src.data_engine import fetch_massive_data
from src.layout import setup_page, error_boundary, fun_loader
from src.gdelt_events import fetch_gdelt_bulk_events, summarize_gdelt_events, build_gdelt_ai_context

logger = logging.getLogger(__name__)

setup_page("19_Iran_Conflict")

st.title("🎖️ Iran Conflict Monitor")
st.markdown("Geopolitical risk tracking via GDELT media intensity, oil price correlation, and defense/energy market impact.")


def _get_key(name: str):
    key = os.environ.get(name)
    if not key:
        try:
            key = st.secrets[name]
        except Exception:
            pass
    return key


# ─────────────────────────────────────────────
# AI WAR ANALYSIS ENGINE — 4-model blend
# ─────────────────────────────────────────────

CONFLICT_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "iran_conflict_history.json")

CONFLICT_TIMELINE_EVENTS = [
    {"date": "2026-02-28", "event": "US-Israel joint strikes begin on Iranian nuclear facilities",
     "category": "Military", "impact": "Oil jumps 8% intraday; Brent crosses $95",
     "infrastructure": "Bushehr nuclear plant targeted; Kharg Island on high alert"},
    {"date": "2026-03-01", "event": "Iran retaliates with ballistic missiles at US bases in Iraq/Qatar",
     "category": "Military", "impact": "Oil gaps up to $105; gold surges 3%",
     "infrastructure": "Al Udeid Air Base struck; Qatar LNG shipments temporarily halted"},
    {"date": "2026-03-02", "event": "IRGC threatens to close Strait of Hormuz",
     "category": "Escalation", "impact": "Shipping insurance rates spike 500%; tanker bookings freeze",
     "infrastructure": "20% of global oil transit at risk; 18 mbpd flow threatened"},
    {"date": "2026-03-03", "event": "Iran deploys naval mines near Hormuz; first tanker damaged",
     "category": "Military", "impact": "Brent hits $115; Asian buyers scramble for alternatives",
     "infrastructure": "Fujairah port operations disrupted; UAE reroutes via Oman"},
    {"date": "2026-03-05", "event": "US Navy begins minesweeping operations in Strait of Hormuz",
     "category": "Military", "impact": "Brief dip to $108 on de-escalation hopes, then back to $112",
     "infrastructure": "Partial shipping lane reopened; insurance still at war-risk premiums"},
    {"date": "2026-03-07", "event": "Iranian missile strikes hit Abqaiq processing facility in Saudi Arabia",
     "category": "Escalation", "impact": "Brent spikes to $126 peak; SPX drops 3.2% intraday",
     "infrastructure": "Abqaiq processes 7 mbpd — 5.7 mbpd temporarily offline"},
    {"date": "2026-03-08", "event": "Saudi Aramco declares force majeure on some crude contracts",
     "category": "Supply", "impact": "Asian crude premiums hit record; SPR release rumors",
     "infrastructure": "Ras Tanura export terminal operating at 60% capacity"},
    {"date": "2026-03-10", "event": "US releases 30M barrels from Strategic Petroleum Reserve",
     "category": "Policy", "impact": "Brent pulls back to $118; temporary supply relief",
     "infrastructure": "SPR at lowest level since 1984; limited further release capacity"},
    {"date": "2026-03-11", "event": "Houthi attacks on Red Sea shipping intensify in solidarity with Iran",
     "category": "Escalation", "impact": "Suez Canal traffic down 40%; freight rates double",
     "infrastructure": "Bab el-Mandeb strait partially blocked; Jeddah refinery on alert"},
    {"date": "2026-03-13", "event": "Iran strikes Kirkuk oil fields in northern Iraq",
     "category": "Military", "impact": "Iraq crude exports drop 400 kbpd; Brent back to $122",
     "infrastructure": "Kirkuk-Ceyhan pipeline shut; Turkey's Ceyhan terminal idled"},
    {"date": "2026-03-14", "event": "G7 emergency meeting; coordinated SPR release of 60M barrels",
     "category": "Policy", "impact": "Brent eases to $116; market skeptical of sustained relief",
     "infrastructure": "IEA coordinates emergency sharing; Japan/Korea draw reserves"},
    {"date": "2026-03-16", "event": "Partial ceasefire talks begin via Omani mediation",
     "category": "Diplomatic", "impact": "Oil drops 5% on ceasefire hopes; equities rally 2%",
     "infrastructure": "Hormuz still mined; clearance estimated at 2-4 weeks minimum"},
    {"date": "2026-03-18", "event": "FOMC holds rates at 3.50-3.75%; cites oil shock uncertainty",
     "category": "Policy", "impact": "Fed trapped — inflation above target but growth weakening",
     "infrastructure": "Fed dot plot shows only 1 cut in 2026; stagflation fears dominate"},
    {"date": "2026-03-19", "event": "Attacks on Qatar Ras Laffan LNG facility cause significant damage",
     "category": "Escalation", "impact": "LNG exports severely disrupted; European gas prices spike",
     "infrastructure": "Ras Laffan handles 77 mtpa LNG — major export capacity offline"},
]

INFRASTRUCTURE_TARGETS = {
    "Strait of Hormuz": {
        "capacity": "18-21 mbpd (20% of global oil)",
        "status": "Partially mined; US minesweeping ongoing",
        "impact_level": "Critical",
        "color": "#ff4b4b",
        "lat": 26.56, "lon": 56.25,
    },
    "Abqaiq (Saudi Arabia)": {
        "capacity": "7 mbpd processing",
        "status": "5.7 mbpd offline after Mar 7 strike; repairs underway",
        "impact_level": "Critical",
        "color": "#ff6b35",
        "lat": 25.94, "lon": 49.67,
    },
    "Ras Tanura (Saudi Arabia)": {
        "capacity": "6.5 mbpd export terminal",
        "status": "Operating at 60% capacity",
        "impact_level": "Severe",
        "color": "#ffaa00",
        "lat": 26.64, "lon": 50.17,
    },
    "Kharg Island (Iran)": {
        "capacity": "5 mbpd (90% of Iran exports)",
        "status": "Under US naval blockade; exports near zero",
        "impact_level": "Critical",
        "color": "#ff4b4b",
        "lat": 29.23, "lon": 50.31,
    },
    "Kirkuk-Ceyhan Pipeline": {
        "capacity": "450 kbpd",
        "status": "Shut after Mar 13 strike on Kirkuk fields",
        "impact_level": "Moderate",
        "color": "#ffaa00",
        "lat": 35.47, "lon": 44.39,
    },
    "Fujairah (UAE)": {
        "capacity": "Major bunkering hub & storage",
        "status": "Operations disrupted; rerouting via Oman",
        "impact_level": "Moderate",
        "color": "#ffaa00",
        "lat": 25.12, "lon": 56.33,
    },
    "Bab el-Mandeb / Red Sea": {
        "capacity": "4.8 mbpd transit",
        "status": "Houthi attacks intensifying; Suez traffic -40%",
        "impact_level": "Severe",
        "color": "#ff6b35",
        "lat": 12.58, "lon": 43.33,
    },
    "Qatar LNG (Ras Laffan)": {
        "capacity": "77 mtpa LNG",
        "status": "Significant damage from recent attacks; major export disruption",
        "impact_level": "Critical",
        "color": "#ff4b4b",
        "lat": 25.93, "lon": 51.53,
    },
}

# Conflict phases for timeline labeling
CONFLICT_PHASES = [
    {"start": "2026-02-28", "end": "2026-03-02", "name": "Initial Strikes", "color": "rgba(255,75,75,0.1)"},
    {"start": "2026-03-02", "end": "2026-03-06", "name": "Hormuz Mining & Naval Standoff", "color": "rgba(255,107,53,0.1)"},
    {"start": "2026-03-07", "end": "2026-03-12", "name": "Infrastructure Attacks", "color": "rgba(255,75,75,0.15)"},
    {"start": "2026-03-13", "end": "2026-03-15", "name": "Regional Escalation", "color": "rgba(255,107,53,0.1)"},
    {"start": "2026-03-16", "end": "2026-03-19", "name": "Ceasefire Talks", "color": "rgba(0,255,150,0.08)"},
]

# Cumulative supply disruption by date (mbpd offline)
DISRUPTION_TIMELINE = [
    {"date": "2026-02-27", "disruption": 0, "note": "Pre-conflict baseline"},
    {"date": "2026-02-28", "disruption": 0.5, "note": "Initial strikes; Kharg Island on alert"},
    {"date": "2026-03-01", "disruption": 2.0, "note": "Qatar LNG halted; Kharg exports declining"},
    {"date": "2026-03-02", "disruption": 4.0, "note": "IRGC mines Hormuz; tanker traffic stops"},
    {"date": "2026-03-03", "disruption": 5.5, "note": "First tanker hit; Fujairah disrupted"},
    {"date": "2026-03-05", "disruption": 4.8, "note": "US minesweeping; partial lane reopened"},
    {"date": "2026-03-07", "disruption": 10.5, "note": "Abqaiq struck — 5.7 mbpd offline"},
    {"date": "2026-03-08", "disruption": 11.0, "note": "Ras Tanura at 60%; Aramco force majeure"},
    {"date": "2026-03-10", "disruption": 10.2, "note": "SPR release -30M bbl; slight relief"},
    {"date": "2026-03-11", "disruption": 11.4, "note": "Houthi attacks; Red Sea +1.2 mbpd disrupted"},
    {"date": "2026-03-13", "disruption": 11.85, "note": "Kirkuk struck; pipeline shut"},
    {"date": "2026-03-14", "disruption": 11.0, "note": "G7 SPR release + Saudi spare capacity"},
    {"date": "2026-03-16", "disruption": 10.75, "note": "Ceasefire talks; no operational change"},
    {"date": "2026-03-18", "disruption": 10.75, "note": "FOMC holds; situation stable"},
    {"date": "2026-03-19", "disruption": 12.45, "note": "Qatar LNG attacks — major export disruption"},
]

# Single source of truth for facility-by-facility disruption (used in waterfall + AI prompt)
DISRUPTION_BREAKDOWN = [
    {"facility": "Kharg Island (Iran exports)", "mbpd": -4.0},
    {"facility": "Abqaiq (Saudi processing)", "mbpd": -5.7},
    {"facility": "Ras Tanura (reduced capacity)", "mbpd": -2.6},
    {"facility": "Kirkuk-Ceyhan pipeline", "mbpd": -0.45},
    {"facility": "Red Sea / Suez rerouting", "mbpd": -1.2},
    {"facility": "Qatar LNG (Ras Laffan attacks)", "mbpd": -2.0},
    {"facility": "SPR releases (US+G7)", "mbpd": 1.5},
    {"facility": "Saudi spare capacity activated", "mbpd": 1.2},
    {"facility": "UAE/Kuwait increases", "mbpd": 0.8},
]
CURRENT_NET_DISRUPTION = round(sum(d["mbpd"] for d in DISRUPTION_BREAKDOWN), 2)  # -10.75

# Base models (always used): Grok, Gemini, Claude
# Platinum-only: GPT-5 (added at runtime if tier = platinum)
MODEL_CONFIGS = {
    "grok": {
        "name": "Grok 3",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-3",
        "key_name": "GROK_API_KEY",
        "extra_instructions": (
            "YOUR ROLE: Breaking news & viral social intelligence specialist.\n\n"

            "PRIORITY ACCOUNTS — check these FIRST for the latest posts:\n"
            "  OSINT: @sentdefender, @IntelCrab, @AuroraIntel, @Faytuks, @GeoConfirmed\n"
            "  Energy/Oil: @JavierBlas, @amaboraz, @EnergyIntel, @OilSheppard\n"
            "  Military: @RALee85, @TheStudyofWar, @NOELreports, @OAlexanderDK\n"
            "  Journalists: @Joyce_Karam, @IranIntl_En, @BarakRavid, @Charles_Lister\n"
            "  Official: @CENTCOM, @IDF, @ABORAZAVI\n\n"

            "VIRAL TWEET DETECTION — find the HOTTEST tweets right now:\n"
            "  - Prioritize tweets by VELOCITY: likes + retweets per hour since posting.\n"
            "    A tweet with 500 likes in 20 minutes is far more important than 2,000 likes over 3 days.\n"
            "  - Flag any tweet that got >500 likes in <1 hour.\n"
            "  - Flag tweets with >100 quote tweets (signals debate/controversy).\n"
            "  - Flag small accounts (<10K followers) getting 10x their normal engagement (breakout signal).\n"
            "  - Find ANALYSIS THREADS (multi-tweet chains), not just single tweets — threads have deeper insight.\n\n"

            "INFRASTRUCTURE MONITORING — search X/Twitter specifically for:\n"
            "  - Attacks on oil/gas/LNG facilities (Abqaiq, Ras Tanura, Kharg Island, Ras Laffan, etc.)\n"
            "  - Strait of Hormuz shipping status, mine clearance progress\n"
            "  - Pipeline damage or repairs (Kirkuk-Ceyhan, East-West, etc.)\n"
            "  - Refinery outages or restarts across the Gulf region\n"
            "  - Red Sea / Bab el-Mandeb / Houthi attack updates\n"
            "  - Any facility coming BACK ONLINE or NEW facilities being hit\n"
            "  For EACH facility in infrastructure_status, search for the latest tweets about it.\n\n"

            "CONTRARIAN SIGNAL DETECTION:\n"
            "  - If viral tweets CONTRADICT the hard data (GDELT, oil prices, ACLED), flag the divergence explicitly.\n"
            "  - If social sentiment is moving opposite to price action, note it — this is often the most valuable signal.\n\n"

            "STRUCTURED TWEET CITATIONS:\n"
            "  In your trending_tweets array, return each tweet with: handle, engagement metrics, time posted, and content.\n"
            "  Format: {\"handle\": \"@user\", \"content\": \"tweet text\", \"likes\": N, \"retweets\": N, \"replies\": N,\n"
            "           \"time\": \"Xh ago\", \"velocity\": \"high/medium\", \"why_important\": \"reason\"}\n"
            "  Include 5-8 of the most important tweets. Prioritize velocity over total engagement.\n\n"

            "YOU MUST: (1) Cite specific tweets with engagement metrics and timestamps. "
            "(2) Note where social sentiment DIVERGES from the hard data provided. "
            "(3) Flag any breaking developments not yet in the data feed. "
            "(4) Include at least one analysis thread if available.\n"
            "Every claim in your rationale MUST reference a specific data point, tweet, or headline."
        ),
        "color": "#ff4444",
        "weight_escalation": 1.3,
        "weight_oil": 0.8,
        "weight_ceasefire": 0.9,
        "max_tokens": 3500,
        "data_sections": ["timeline", "infrastructure", "oil", "gdelt_tone", "disruption", "prior"],
        "tier_required": None,  # all tiers
    },
    "gemini": {
        "name": "Gemini 3 Pro",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-3-pro-preview",
        "key_name": "GEMINI_API_KEY",
        "extra_instructions": (
            "YOUR ROLE: Military strategy & energy markets specialist.\n"
            "You cover TWO domains — provide analysis on BOTH:\n\n"
            "MILITARY & STRATEGIC:\n"
            "  (1) Military balance of forces and operational tempo.\n"
            "  (2) Historical parallels — compare to 1980 Iran-Iraq War, 1990 Gulf War, 2019 Soleimani escalation.\n"
            "  (3) Escalation ladder — what would move this conflict to the next level vs. de-escalation.\n"
            "  Every escalation score must reference a comparable historical event at that level.\n\n"
            "ENERGY & ECONOMIC:\n"
            "  (1) Exact supply disruption by facility (mbpd offline at each chokepoint).\n"
            "  (2) Oil price mechanics — spare capacity, SPR drawdown math, shipping rerouting costs.\n"
            "  (3) Second-order economic effects — inflation pass-through, refinery margins, LNG substitution.\n"
            "  Your disruption estimate must add up facility-by-facility.\n\n"
            "YOU MUST: Ground your analysis in the LIVE DATA provided. Cite GDELT scores, oil prices, "
            "event counts, and specific facility statuses."
        ),
        "color": "#4285f4",
        "weight_escalation": 1.1,
        "weight_oil": 1.4,
        "weight_ceasefire": 0.8,
        "max_tokens": 2500,
        "data_sections": ["timeline", "infrastructure", "gdelt_intensity", "gdelt_bulk", "oil", "disruption", "prior"],
        "tier_required": None,
    },
    "claude": {
        "name": "Claude Sonnet",
        "base_url": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "key_name": "ANTHROPIC_API_KEY",
        "extra_instructions": (
            "YOUR ROLE: Diplomatic & probabilistic reasoning specialist.\n"
            "Focus on: (1) Ceasefire probability — who are the mediators, what are the obstacles, "
            "what concessions are each side likely to make. "
            "(2) Scenario tree — map out the 3-5 most likely paths forward with conditional probabilities. "
            "(3) Second-order geopolitical effects — alliances, UN Security Council dynamics, China/Russia positioning.\n"
            "YOU MUST: Express uncertainty explicitly. Use calibrated probability ranges, not point estimates "
            "where uncertain. Cite specific diplomatic signals from the data provided."
        ),
        "color": "#d4a574",
        "weight_escalation": 1.0,
        "weight_oil": 0.9,
        "weight_ceasefire": 1.4,
        "max_tokens": 2500,
        "data_sections": ["timeline", "infrastructure", "oil", "gdelt_tone", "acled", "disruption", "prior"],
        "tier_required": None,
    },
}

# Platinum-only model — added to MODEL_CONFIGS at runtime for platinum users
GPT5_CONFIG = {
    "name": "GPT-5",
    "base_url": None,
    "model": "gpt-5",
    "key_name": "OPENAI_API_KEY",
    "extra_instructions": (
        "YOUR ROLE: Deep strategic analysis & synthesis specialist.\n"
        "Focus on: (1) Synthesize intelligence from all dimensions — military, economic, diplomatic. "
        "(2) Identify non-obvious second and third-order effects. "
        "(3) Historical parallels with precise calibration. "
        "(4) Challenge assumptions in the other models' likely conclusions.\n"
        "YOU MUST: Provide the most rigorous, nuanced analysis. Cite specific data points."
    ),
    "color": "#00cc66",
    "weight_escalation": 1.2,
    "weight_oil": 1.0,
    "weight_ceasefire": 1.1,
    "max_tokens": 3000,
    "data_sections": ["timeline", "infrastructure", "gdelt_intensity", "gdelt_bulk", "acled", "oil", "disruption", "prior"],
    "tier_required": "platinum",
}

CONFLICT_SYSTEM_PROMPT = """You are an elite geopolitical intelligence analyst specializing in Middle East conflicts and energy security.

You are analyzing the ongoing US-Israel war on Iran that began February 28, 2026.

Key context:
- US-Israel launched joint strikes on Iranian nuclear facilities on Feb 28, 2026
- Iran retaliated with ballistic missiles; IRGC mined the Strait of Hormuz
- Abqaiq processing facility in Saudi Arabia was struck on Mar 7 (5.7 mbpd offline)
- Houthi attacks on Red Sea shipping intensified in solidarity
- Partial ceasefire talks via Omani mediation began Mar 16
- FOMC held rates Mar 18, citing oil shock uncertainty
- Attacks on Qatar Ras Laffan LNG facility caused significant damage on Mar 19
- Oil peaked at $126/bbl (Brent); currently elevated above $100

VERIFIED SUPPLY DISRUPTION BREAKDOWN (use these exact numbers as your baseline):
  Losses:
    Kharg Island (Iran exports): -4.0 mbpd (blockaded, near zero exports)
    Abqaiq (Saudi processing): -5.7 mbpd (struck Mar 7, repairs underway)
    Ras Tanura (Saudi export): -2.6 mbpd (operating at 60%)
    Kirkuk-Ceyhan pipeline: -0.45 mbpd (shut after Mar 13 strike)
    Red Sea / Suez rerouting: -1.2 mbpd (Houthi attacks, Suez -40%)
    Qatar LNG (Ras Laffan): -2.0 mbpd equivalent (significant damage from attacks Mar 19)
  Offsets:
    SPR releases (US+G7): +1.5 mbpd equivalent
    Saudi spare capacity: +1.2 mbpd
    UAE/Kuwait increases: +0.8 mbpd
  NET DISRUPTION: -12.45 mbpd
Your current_disruption_mbpd estimate MUST be close to 12.45 unless you have specific evidence
that a facility has come back online or a new facility has been hit. If you deviate, explain why.

CALIBRATION ANCHORS — use these to ground your escalation_risk score:
  10 = Cuban Missile Crisis (1962) — imminent nuclear exchange, civilization-level risk
   9 = Yom Kippur War (1973) — full-scale multi-front war, nuclear weapons considered
   8 = Gulf War (1990-91) — major regional war, oil infrastructure targeted, coalition intervention
   7 = Iran-Iraq War peak (1984-88 Tanker War) — sustained attacks on shipping and oil facilities
   6 = 2019-20 Soleimani/Hormuz crisis — direct US-Iran strikes, brink of war, shipping attacks
   5 = 2023-24 Israel-Hamas + Houthi escalation — regional proxy conflict, Red Sea disruption
   4 = Elevated baseline — active tensions, diplomatic breakdowns, military posturing
   3 = Managed tensions — back-channel diplomacy active, no active strikes
   2 = De-escalation in progress — ceasefire holding, diplomatic talks advancing
   1 = Post-conflict normalization

CITATION RULES — every claim must be grounded:
- Escalation rationale MUST cite at least 2 specific data points from the LIVE DATA provided
- Oil forecasts MUST reference the current oil price and % change from the data feed
- Infrastructure status MUST reference specific facilities and their known capacity
- If you are uncertain about a fact, say so explicitly — do NOT fabricate
- If the live data contradicts your prior beliefs, trust the data and note the divergence

ANTI-DRIFT: You will be given your MOST RECENT PRIOR analysis (if any). Do NOT continue trends
just because prior runs showed movement. Each analysis is independent. Only shift your scores
if the LIVE DATA justifies it. If nothing material changed, your scores should be similar to last time.

Respond with ONLY valid JSON in this exact format:
{
  "situation_summary": "3-5 sentence executive summary citing specific data points",
  "latest_developments": ["development 1 (cite source)", "development 2 (cite source)", "development 3"],
  "escalation_risk": {
    "level": "Critical/High/Elevated/Moderate/Low",
    "score": N (1-10, calibrated to the anchor scale above),
    "rationale": "MUST cite 2+ data points: e.g., 'GDELT intensity at X (up Y% from 30d avg), N ACLED events this week with Z fatalities, oil at $X (+Y%). Comparable to [historical anchor] because...'",
    "citations": ["data point 1", "data point 2"]
  },
  "oil_impact": {
    "current_disruption_mbpd": N,
    "price_forecast_30d": {"low": N, "base": N, "high": N},
    "key_chokepoints": ["chokepoint: capacity, current status, disruption estimate"]
  },
  "infrastructure_status": [
    {"name": "facility name", "status": "operational/degraded/offline", "detail": "capacity and current state", "changed": true/false}
  ],
  "infrastructure_alerts": ["IMPORTANT: List any facility whose status has CHANGED from the baseline data provided. Include: facility name, old status, new status, and source/evidence for the change. If nothing changed, return empty array."],
  "diplomatic_outlook": {
    "ceasefire_probability_30d": N,
    "confidence_range": [low_N, high_N],
    "key_mediators": ["..."],
    "obstacles": ["..."],
    "signals": ["specific diplomatic signal cited"]
  },
  "market_implications": {
    "energy": "cite oil price level and direction",
    "equities": "cite specific sector impacts",
    "safe_havens": "gold, treasuries, USD",
    "currencies": "petrocurrency and import-dependent impacts"
  },
  "scenario_forecast": [
    {"scenario": "name", "probability": N, "description": "...", "oil_impact": "$X-Y range", "trigger": "what would cause this scenario"}
  ],
  "trending_tweets": [
    {"handle": "@username", "content": "tweet text (max 200 chars)", "likes": N, "retweets": N, "replies": N, "time": "Xh ago", "velocity": "high/medium/low", "why_important": "brief reason this tweet matters"}
  ]
}

IMPORTANT: scenario_forecast probabilities MUST sum to approximately 100%.
They are MUTUALLY EXCLUSIVE outcomes — only ONE scenario will materialize.
Provide exactly 3 scenarios (most likely, second most likely, tail risk).
Ensure their probabilities add up to 95-100%."""


def build_live_data_context(gdelt_data: dict, df_oil, df_acled, df_tone,
                            sections: list[str] | None = None) -> str:
    """Build a live data block to inject into the AI prompt.

    Args:
        sections: optional filter — only include these data sections.
            Valid: gdelt_intensity, gdelt_tone, oil, acled, disruption
            If None, include all.
    """
    include_all = sections is None
    lines = [f"Current data as of {datetime.now().replace(minute=0, second=0, microsecond=0).strftime('%B %d, %Y %I:%M %p')}:"]

    # GDELT media intensity
    if (include_all or "gdelt_intensity" in sections) and gdelt_data:
        lines.append("\nGDELT Media Intensity:")
        for label, df_g in gdelt_data.items():
            if df_g is not None and not df_g.empty:
                latest = df_g["value"].iloc[-1]
                avg_7d = df_g["value"].tail(7).mean()
                avg_30d = df_g["value"].tail(30).mean()
                chg_7v30 = ((avg_7d / avg_30d) - 1) * 100 if avg_30d > 0 else 0
                peak = df_g["value"].max()
                lines.append(f"  {label}: latest={latest:.4f}, 7d_avg={avg_7d:.4f}, "
                             f"30d_avg={avg_30d:.4f}, 7d_vs_30d={chg_7v30:+.1f}%, peak={peak:.4f}")
        lines.append("  (DATA FRESHNESS: GDELT data is daily granularity, cached up to 2 hours)")

    # GDELT tone/sentiment
    if (include_all or "gdelt_tone" in sections) and df_tone is not None and not df_tone.empty:
        latest_tone = df_tone["value"].iloc[-1]
        avg_tone = df_tone["value"].mean()
        lines.append(f"\nGDELT Media Tone:")
        lines.append(f"  Current tone: {latest_tone:.2f}, 6-month avg: {avg_tone:.2f}")

    # Oil price
    if (include_all or "oil" in sections) and df_oil is not None and not df_oil.empty:
        latest_price = df_oil["value"].iloc[-1]
        prev_price = df_oil["value"].iloc[-2] if len(df_oil) > 1 else latest_price
        chg_1d = ((latest_price / prev_price) - 1) * 100 if prev_price > 0 else 0
        price_7d_ago = df_oil["value"].iloc[-min(5, len(df_oil))] if len(df_oil) > 5 else df_oil["value"].iloc[0]
        chg_7d = ((latest_price / price_7d_ago) - 1) * 100 if price_7d_ago > 0 else 0
        price_30d_ago = df_oil["value"].iloc[-min(22, len(df_oil))] if len(df_oil) > 22 else df_oil["value"].iloc[0]
        chg_30d = ((latest_price / price_30d_ago) - 1) * 100 if price_30d_ago > 0 else 0
        high_180d = df_oil["value"].max()
        low_180d = df_oil["value"].min()
        lines.append(f"\nWTI Crude Oil:")
        lines.append(f"  Current: ${latest_price:.2f}/bbl, 1d: {chg_1d:+.1f}%, "
                     f"7d: {chg_7d:+.1f}%, 30d: {chg_30d:+.1f}%")
        lines.append(f"  180d range: ${low_180d:.2f} — ${high_180d:.2f}")

    # ACLED conflict events
    if (include_all or "acled" in sections) and df_acled is not None and not df_acled.empty:
        lines.append(f"\nACLED Conflict Events:")
        total = len(df_acled)
        total_fatal = int(df_acled["fatalities"].sum()) if "fatalities" in df_acled.columns else 0
        # Last 7 days
        if "event_date" in df_acled.columns:
            cutoff_7d = pd.Timestamp.now() - pd.Timedelta(days=7)
            recent = df_acled[df_acled["event_date"] >= cutoff_7d]
            lines.append(f"  Last 7 days: {len(recent)} events, "
                         f"{int(recent['fatalities'].sum()) if 'fatalities' in recent.columns else '?'} fatalities")
            # By country (top 5)
            if "country" in recent.columns and not recent.empty:
                by_country = recent.groupby("country")["fatalities"].agg(["count", "sum"]).sort_values("sum", ascending=False).head(5)
                for country, row in by_country.iterrows():
                    lines.append(f"    {country}: {int(row['count'])} events, {int(row['sum'])} fatalities")
            # By event type
            if "event_type" in recent.columns and not recent.empty:
                by_type = recent["event_type"].value_counts().head(5)
                lines.append(f"  Event types (7d): {', '.join(f'{t}={c}' for t, c in by_type.items())}")
        lines.append(f"  Total since Jan 2026: {total} events, {total_fatal} fatalities")
        lines.append("  (DATA FRESHNESS: ACLED data may lag 1-7 days from real events)")
    elif include_all or "acled" in sections:
        lines.append("")

    # Verified disruption breakdown
    if include_all or "disruption" in sections:
        lines.append(f"\nSupply Disruption ({DISRUPTION_TIMELINE[-1]['date']}):")
        for d in DISRUPTION_BREAKDOWN:
            lines.append(f"  {d['facility']}: {d['mbpd']:+.1f} mbpd")
        lines.append(f"  NET DISRUPTION: {CURRENT_NET_DISRUPTION:+.2f} mbpd")
        lines.append("  YOUR current_disruption_mbpd MUST match ~12.45 unless you cite a specific change.")

    return "\n".join(lines)


def build_prior_analysis_context() -> str:
    """Build the most recent prior analysis context (anti-drift: only last entry)."""
    history = load_conflict_history()
    if not history:
        return ""

    latest = history[-1]
    blended = latest.get("blended", {})
    ts = latest.get("timestamp", "unknown")

    lines = [f"\n=== YOUR MOST RECENT PRIOR ANALYSIS ({ts}) ==="]
    esc = blended.get("escalation_risk", {})
    lines.append(f"  Escalation: {esc.get('score', '?')}/10 ({esc.get('level', '?')})")
    oil = blended.get("oil_impact", {})
    fc = oil.get("price_forecast_30d", {})
    lines.append(f"  Oil disruption: {oil.get('current_disruption_mbpd', '?')} mbpd, "
                 f"30d forecast: ${fc.get('low', '?')}-${fc.get('base', '?')}-${fc.get('high', '?')}")
    diplo = blended.get("diplomatic_outlook", {})
    lines.append(f"  Ceasefire prob: {diplo.get('ceasefire_probability_30d', '?')}%")
    summary = blended.get("situation_summary", "")
    if summary:
        lines.append(f"  Summary: {summary[:300]}")
    lines.append("INSTRUCTION: Only shift materially from these values if the LIVE DATA above justifies it.")

    return "\n".join(lines)


def load_conflict_history() -> list:
    try:
        if os.path.exists(CONFLICT_HISTORY_FILE):
            with open(CONFLICT_HISTORY_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load conflict history: {e}")
    return []


def save_conflict_result(result: dict) -> None:
    history = load_conflict_history()
    entry = {
        "timestamp": datetime.now().isoformat(),
        "models_used": result.get("models_used", []),
        "blended": result,
    }
    history.append(entry)
    # Keep last 48 entries
    history = history[-48:]
    try:
        os.makedirs(os.path.dirname(CONFLICT_HISTORY_FILE), exist_ok=True)
        with open(CONFLICT_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save conflict history: {e}")


def get_latest_conflict_result() -> tuple:
    history = load_conflict_history()
    if not history:
        return None, True
    latest = history[-1]
    is_stale = True
    try:
        ts = pd.Timestamp(latest["timestamp"])
        is_stale = (pd.Timestamp.now() - ts) > pd.Timedelta(hours=1)
    except Exception:
        pass
    return latest, is_stale


@st.cache_data(ttl=1800, show_spinner=False)
def run_single_conflict_model(model_key: str, api_key: str, context_prompt: str, model_config: dict = None) -> dict:
    """Call a single AI model for conflict analysis."""
    config = model_config or MODEL_CONFIGS.get(model_key) or GPT5_CONFIG
    model_max_tokens = config.get("max_tokens", 3000)

    user_prompt = f"""{context_prompt}

{config['extra_instructions']}
Produce your complete analysis. JSON only."""

    try:
        if config["base_url"] == "anthropic":
            import anthropic
            client = anthropic.Anthropic(api_key=api_key)
            response = client.messages.create(
                model=config["model"],
                max_tokens=model_max_tokens,
                temperature=0.3,
                system=CONFLICT_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
        else:
            client_kwargs = {"api_key": api_key}
            if config["base_url"]:
                client_kwargs["base_url"] = config["base_url"]
            client = OpenAI(**client_kwargs)

            is_gpt5 = "gpt-5" in config["model"]
            # GPT-5 needs higher max_completion_tokens — it won't respond with too low a ceiling
            effective_max = max(model_max_tokens, 8000) if is_gpt5 else model_max_tokens
            call_kwargs = dict(
                model=config["model"],
                messages=[
                    {"role": "system", "content": CONFLICT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                **{"max_completion_tokens" if is_gpt5 else "max_tokens": effective_max},
                **({"temperature": 0.3} if not is_gpt5 else {}),
            )

            # response_format not supported by all providers — try with, fallback without
            try:
                response = client.chat.completions.create(
                    **call_kwargs,
                    response_format={"type": "json_object"},
                )
            except Exception:
                response = client.chat.completions.create(**call_kwargs)

            raw = response.choices[0].message.content
            # Check for truncation
            finish = response.choices[0].finish_reason
            if finish == "length":
                logger.warning(f"{config['name']} response truncated (finish_reason=length)")

        if not raw or not raw.strip():
            return {"success": False, "error": "Empty response (model may need higher max_completion_tokens)", "model_name": config["name"]}

        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)

        if not cleaned:
            return {"success": False, "error": "Response contained no JSON content", "model_name": config["name"]}

        # Attempt to repair truncated JSON (e.g., missing closing braces)
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError:
            # Try closing open strings, arrays, and objects
            repaired = cleaned
            # Close any unterminated string
            if repaired.count('"') % 2 == 1:
                repaired += '"'
            # Close brackets/braces by counting opens vs closes
            open_braces = repaired.count("{") - repaired.count("}")
            open_brackets = repaired.count("[") - repaired.count("]")
            repaired += "]" * max(0, open_brackets)
            repaired += "}" * max(0, open_braces)
            result = json.loads(repaired)
        result["success"] = True
        result["model_name"] = config["name"]
        return result
    except Exception as e:
        logger.error(f"{config['name']} conflict analysis failed: {e}")
        return {"success": False, "error": str(e), "model_name": config["name"]}


def _domain_weighted_avg(results: dict, field_path: list[str], weight_key: str, default: float = 0, configs: dict = None) -> float:
    """Compute a domain-weighted average for a nested field across model results."""
    _cfgs = configs or MODEL_CONFIGS
    total_weight = 0
    weighted_sum = 0
    for k, v in results.items():
        val = v
        for key in field_path:
            val = val.get(key, {}) if isinstance(val, dict) else default
        if not isinstance(val, (int, float)):
            val = default
        w = _cfgs.get(k, {}).get(weight_key, 1.0)
        weighted_sum += val * w
        total_weight += w
    return round(weighted_sum / total_weight, 1) if total_weight else default


def _scenario_cluster_key(name: str) -> str:
    """Map a scenario name to a canonical cluster key using keyword matching.
    This groups scenarios that describe the same theme even with different phrasing."""
    n = name.lower()
    # Escalation / war / closure
    if any(w in n for w in ["escalation", "closure", "hormuz", "crisis", "regional war", "chokepoint"]):
        return "escalation"
    # Proxy / spillover / expansion
    if any(w in n for w in ["proxy", "spillover", "expansion", "hezbollah", "yemen", "militia"]):
        return "proxy_expansion"
    # Ceasefire / de-escalation / diplomatic
    if any(w in n for w in ["ceasefire", "de-escalation", "deconfliction", "diplomatic", "peace"]):
        return "ceasefire"
    # Stalemate / prolonged / status quo / infrastructure war
    if any(w in n for w in ["stalemate", "prolonged", "status quo", "infrastructure war", "high-risk"]):
        return "stalemate"
    # Recovery / improvement
    if any(w in n for w in ["recovery", "repair", "improvement", "faster", "restoration"]):
        return "recovery"
    # Fallback — use simplified name
    return n.strip()[:30]


def _blend_scenario_forecasts(successful: dict) -> list:
    """Cluster similar scenarios from multiple models, average their probabilities,
    and normalize so the final set sums to 100%."""
    # Collect all raw scenarios with their source model
    raw = []
    for model_key, v in successful.items():
        for s in v.get("scenario_forecast", []):
            raw.append({**s, "_model": v.get("model_name", model_key)})

    if not raw:
        return []

    # Cluster by keyword-based theme matching
    cluster_map = {}  # cluster_key -> list of scenarios
    for s in raw:
        key = _scenario_cluster_key(s.get("scenario", ""))
        cluster_map.setdefault(key, []).append(s)
    clusters = list(cluster_map.values())

    # Merge each cluster into a single scenario
    merged = []
    for cluster in clusters:
        # Use the shortest name (usually the cleanest)
        names = [s.get("scenario", "") for s in cluster]
        best_name = min(names, key=len) if names else ""

        # Average probability across models
        probs = [s.get("probability", 0) for s in cluster]
        avg_prob = sum(probs) / len(probs)

        # Pick the longest (most detailed) description
        descriptions = [s.get("description", "") for s in cluster]
        best_desc = max(descriptions, key=len) if descriptions else ""

        # Pick the most specific oil impact
        oil_impacts = [s.get("oil_impact", "") for s in cluster if s.get("oil_impact")]
        best_oil = oil_impacts[0] if oil_impacts else ""

        # Collect triggers
        triggers = [s.get("trigger", "") for s in cluster if s.get("trigger")]
        best_trigger = max(triggers, key=len) if triggers else ""

        # Note how many models agreed
        model_names = list(set(s.get("_model", "") for s in cluster))

        merged.append({
            "scenario": best_name,
            "probability": round(avg_prob),
            "description": best_desc,
            "oil_impact": best_oil,
            "trigger": best_trigger,
            "models_agree": len(cluster),
            "models": model_names,
        })

    # Normalize probabilities to sum to 100%
    total = sum(s["probability"] for s in merged)
    if total > 0:
        for s in merged:
            s["probability"] = round(s["probability"] / total * 100)

    # Fix rounding — adjust largest to make sum exactly 100
    rounding_diff = 100 - sum(s["probability"] for s in merged)
    if merged and rounding_diff != 0:
        merged[0]["probability"] += rounding_diff

    # Sort by probability descending
    merged.sort(key=lambda s: s["probability"], reverse=True)

    return merged


_PROFANITY = {
    "fuck", "shit", "damn", "bitch", "ass", "dick", "cock", "pussy", "cunt",
    "stfu", "gtfo", "lmao", "lmfao", "wtf", "af", "bs", "fk", "fck",
    "retard", "retarded", "faggot", "nigger", "nigga",
}


def _is_quality_tweet(content: str) -> bool:
    """Filter out low-quality tweets: hashtag spam, ticker-only, profanity, too short."""
    if not content or not content.strip():
        return False
    text = content.strip()
    # Too short to be useful
    if len(text) < 20:
        return False
    # Hashtag spam: more than 40% of the text is hashtags
    hashtag_chars = sum(len(w) for w in text.split() if w.startswith("#"))
    if hashtag_chars > len(text) * 0.4:
        return False
    # Ticker-only: just cashtags/tickers with no real content
    words = [w for w in text.split() if not w.startswith("$") and not w.startswith("#") and not w.startswith("@")]
    if len(words) < 3:
        return False
    # Profanity check
    text_lower = text.lower()
    for word in _PROFANITY:
        # Check as whole word to avoid false positives (e.g. "class" matching "ass")
        if f" {word} " in f" {text_lower} " or text_lower.startswith(f"{word} ") or text_lower.endswith(f" {word}"):
            return False
    # All caps spam (entire tweet is shouting)
    alpha_chars = [c for c in text if c.isalpha()]
    if len(alpha_chars) > 10 and sum(1 for c in alpha_chars if c.isupper()) / len(alpha_chars) > 0.8:
        return False
    return True


def blend_conflict_results(results: dict, configs: dict = None) -> dict:
    """Blend multiple model outputs into a unified conflict assessment with domain-weighted scoring."""
    if configs is None:
        configs = MODEL_CONFIGS
    successful = {k: v for k, v in results.items() if v.get("success")}
    if not successful:
        return {"success": False, "error": "All models failed"}

    n = len(successful)
    models_used = [v.get("model_name", k) for k, v in successful.items()]

    # Domain-weighted escalation
    avg_escalation = _domain_weighted_avg(
        successful, ["escalation_risk", "score"], "weight_escalation", 5, configs)

    # Domain-weighted oil disruption
    avg_disruption = _domain_weighted_avg(
        successful, ["oil_impact", "current_disruption_mbpd"], "weight_oil", 0, configs)

    # Domain-weighted oil price forecasts
    price_low = _domain_weighted_avg(
        successful, ["oil_impact", "price_forecast_30d", "low"], "weight_oil", 0, configs)
    price_base = _domain_weighted_avg(
        successful, ["oil_impact", "price_forecast_30d", "base"], "weight_oil", 0, configs)
    price_high = _domain_weighted_avg(
        successful, ["oil_impact", "price_forecast_30d", "high"], "weight_oil", 0, configs)

    # Domain-weighted ceasefire probability
    avg_ceasefire = round(_domain_weighted_avg(
        successful, ["diplomatic_outlook", "ceasefire_probability_30d"], "weight_ceasefire", 0, configs))

    # Collect all unique developments
    all_devs = []
    seen = set()
    for v in successful.values():
        for d in v.get("latest_developments", []):
            d_lower = d.lower()[:50]
            if d_lower not in seen:
                all_devs.append(d)
                seen.add(d_lower)

    # Best situation summary — prefer Grok (live data), then longest
    summaries = [(k, v.get("situation_summary", "")) for k, v in successful.items()]
    summaries.sort(key=lambda x: (x[0] == "grok", len(x[1])), reverse=True)
    best_summary = summaries[0][1] if summaries else ""

    # Collect all infrastructure statuses
    infra_map = {}
    for v in successful.values():
        for item in v.get("infrastructure_status", []):
            name = item.get("name", "")
            if name and name not in infra_map:
                infra_map[name] = item

    # Collect market implications — prefer specialist per domain
    market_keys = ["energy", "equities", "safe_havens", "currencies"]
    # Gemini is energy specialist, Claude for safe havens, GPT-4o for equities
    market_priority = {"energy": "gemini", "equities": "gemini", "safe_havens": "claude", "currencies": "claude"}
    market_blended = {}
    for mk in market_keys:
        preferred = market_priority.get(mk)
        texts = {}
        for model_key, v in successful.items():
            t = v.get("market_implications", {}).get(mk, "")
            if t:
                texts[model_key] = t
        if preferred in texts:
            market_blended[mk] = texts[preferred]
        elif texts:
            market_blended[mk] = list(texts.values())[0]
        else:
            market_blended[mk] = ""

    # Infrastructure alerts — collect from all models
    all_infra_alerts = []
    seen_alerts = set()
    for v in successful.values():
        for alert in v.get("infrastructure_alerts", []):
            if isinstance(alert, str) and alert.lower()[:40] not in seen_alerts:
                all_infra_alerts.append(alert)
                seen_alerts.add(alert.lower()[:40])
    # Also check for changed facilities in infrastructure_status
    for v in successful.values():
        for item in v.get("infrastructure_status", []):
            if item.get("changed"):
                alert_text = f"{item.get('name', 'Unknown')}: {item.get('detail', '')}"
                if alert_text.lower()[:40] not in seen_alerts:
                    all_infra_alerts.append(alert_text)
                    seen_alerts.add(alert_text.lower()[:40])

    # Scenario forecasts — cluster similar scenarios across models, average probabilities, normalize to 100%
    all_scenarios = _blend_scenario_forecasts(successful)

    # Per-model assessments with citations
    model_assessments = []
    escalation_scores = []
    ceasefire_scores = []
    for k, v in successful.items():
        esc = v.get("escalation_risk", {})
        score = esc.get("score", 0)
        escalation_scores.append(score)
        ceasefire_scores.append(v.get("diplomatic_outlook", {}).get("ceasefire_probability_30d", 0))
        model_assessments.append({
            "model": v.get("model_name", k),
            "model_key": k,
            "level": esc.get("level", "Unknown"),
            "score": score,
            "rationale": esc.get("rationale", ""),
            "citations": esc.get("citations", []),
            "color": configs.get(k, {}).get("color", "#ffffff"),
        })

    # Disagreement detection
    disagreements = []
    if len(escalation_scores) >= 2:
        esc_spread = max(escalation_scores) - min(escalation_scores)
        if esc_spread >= 2:
            high_model = model_assessments[escalation_scores.index(max(escalation_scores))]
            low_model = model_assessments[escalation_scores.index(min(escalation_scores))]
            disagreements.append({
                "field": "Escalation Risk",
                "spread": esc_spread,
                "high": {"model": high_model["model"], "score": high_model["score"],
                         "rationale": high_model["rationale"][:200]},
                "low": {"model": low_model["model"], "score": low_model["score"],
                        "rationale": low_model["rationale"][:200]},
            })
    if len(ceasefire_scores) >= 2:
        cf_spread = max(ceasefire_scores) - min(ceasefire_scores)
        if cf_spread >= 15:
            high_idx = ceasefire_scores.index(max(ceasefire_scores))
            low_idx = ceasefire_scores.index(min(ceasefire_scores))
            disagreements.append({
                "field": "Ceasefire Probability",
                "spread": cf_spread,
                "high": {"model": model_assessments[high_idx]["model"],
                         "score": max(ceasefire_scores)},
                "low": {"model": model_assessments[low_idx]["model"],
                        "score": min(ceasefire_scores)},
            })

    # Oil price disagreement
    oil_bases = [v.get("oil_impact", {}).get("price_forecast_30d", {}).get("base", 0)
                 for v in successful.values()]
    if len(oil_bases) >= 2 and max(oil_bases) > 0:
        oil_spread = max(oil_bases) - min(oil_bases)
        if oil_spread >= 10:
            model_keys = list(successful.keys())
            high_idx = oil_bases.index(max(oil_bases))
            low_idx = oil_bases.index(min(oil_bases))
            disagreements.append({
                "field": "Oil Price Forecast (30d base)",
                "spread": oil_spread,
                "high": {"model": successful[model_keys[high_idx]].get("model_name", ""),
                         "score": f"${max(oil_bases)}"},
                "low": {"model": successful[model_keys[low_idx]].get("model_name", ""),
                        "score": f"${min(oil_bases)}"},
            })

    blended = {
        "success": True,
        "models_used": models_used,
        "situation_summary": best_summary,
        "latest_developments": all_devs[:8],
        "escalation_risk": {
            "score": avg_escalation,
            "level": "Critical" if avg_escalation >= 8 else "High" if avg_escalation >= 6 else "Elevated" if avg_escalation >= 4 else "Moderate",
            "model_assessments": model_assessments,
        },
        "oil_impact": {
            "current_disruption_mbpd": avg_disruption,
            "price_forecast_30d": {
                "low": round(price_low),
                "base": round(price_base),
                "high": round(price_high),
            },
        },
        "infrastructure_status": list(infra_map.values()),
        "diplomatic_outlook": {
            "ceasefire_probability_30d": avg_ceasefire,
        },
        "market_implications": market_blended,
        "scenario_forecast": all_scenarios,
        "disagreements": disagreements,
        "infrastructure_alerts": all_infra_alerts,
    }

    # Collect trending tweets — prioritize Grok (has live X access), then any model
    all_tweets = []
    seen_handles = set()
    for model_pref in ["grok", "gemini", "claude", "openai"]:
        if model_pref in successful:
            for tw in successful[model_pref].get("trending_tweets", []):
                handle = tw.get("handle", "")
                content = tw.get("content", "")
                if handle and handle not in seen_handles:
                    all_tweets.append(tw)
                    seen_handles.add(handle)
    # Sort by velocity: high > medium > low, then by likes
    vel_order = {"high": 0, "medium": 1, "low": 2}
    all_tweets.sort(key=lambda t: (vel_order.get(t.get("velocity", "low"), 2), -t.get("likes", 0)))
    blended["trending_tweets"] = all_tweets[:10]

    return blended


def _get_eia_key():
    key = os.environ.get("EIA_API_KEY")
    if not key:
        try:
            key = st.secrets["EIA_API_KEY"]
        except Exception:
            pass
    return key


GDELT_HEADERS = {
    "User-Agent": "AIStatCharts/1.0 (conflict-monitor; research)",
    "Accept": "application/json",
}


def _gdelt_request(params: dict, max_retries: int = 3) -> dict | None:
    """Make a GDELT API request with retry + exponential backoff."""
    for attempt in range(max_retries):
        try:
            r = requests.get(
                "https://api.gdeltproject.org/api/v2/doc/doc",
                params=params,
                headers=GDELT_HEADERS,
                timeout=30,
            )
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                wait = 3 * (2 ** attempt)
                logger.warning(f"GDELT rate limited, retrying in {wait}s (attempt {attempt + 1})")
                time.sleep(wait)
                continue
            logger.warning(f"GDELT returned {r.status_code}")
            return None
        except Exception as e:
            logger.warning(f"GDELT request failed: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
    return None


@st.cache_data(ttl=7200, show_spinner=False)
def fetch_gdelt_timeline(query: str, timespan: str = "180d"):
    """Fetch media volume intensity timeline from GDELT."""
    data = _gdelt_request({
        "query": query, "mode": "TimelineVol",
        "format": "json", "TIMESPAN": timespan,
    })
    if data:
        tl = data.get("timeline", [])
        if tl and tl[0].get("data"):
            df = pd.DataFrame(tl[0]["data"])
            df["date"] = pd.to_datetime(df["date"])
            return df.sort_values("date")
    return pd.DataFrame()


@st.cache_data(ttl=7200, show_spinner=False)
def fetch_gdelt_tone(query: str, timespan: str = "180d"):
    """Fetch media tone timeline from GDELT (positive/negative sentiment)."""
    data = _gdelt_request({
        "query": query, "mode": "TimelineTone",
        "format": "json", "TIMESPAN": timespan,
    })
    if data:
        tl = data.get("timeline", [])
        for series in (tl or []):
            if series.get("series") == "Tone":
                df = pd.DataFrame(series["data"])
                df["date"] = pd.to_datetime(df["date"])
                return df.sort_values("date")
    return pd.DataFrame()


@st.cache_data(ttl=7200, show_spinner=False)
def fetch_all_gdelt_data(queries: dict, timespan: str = "180d"):
    """Fetch all GDELT queries in one cached call to minimize API hits."""
    results = {}
    for label, query in queries.items():
        df = fetch_gdelt_timeline(query, timespan)
        if not df.empty:
            results[label] = df
        time.sleep(2)
    tone = fetch_gdelt_tone(list(queries.values())[0], timespan)
    return results, tone


# ─────────────────────────────────────────────
# ACLED — Armed Conflict Location & Event Data
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _acled_get_token(email: str, password: str) -> str | None:
    """Obtain a 24-hour OAuth token from ACLED."""
    try:
        r = requests.post(
            "https://acleddata.com/oauth/token",
            data={
                "username": email,
                "password": password,
                "grant_type": "password",
                "client_id": "acled",
            },
            timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("access_token")
        logger.warning(f"ACLED auth failed: {r.status_code}")
    except Exception as e:
        logger.error(f"ACLED auth error: {e}")
    return None


@st.cache_data(ttl=7200, show_spinner=False)
def fetch_acled_events(token: str, countries: list[str], start_date: str = "2026-01-01",
                       event_types: list[str] | None = None, limit: int = 5000) -> pd.DataFrame:
    """Fetch conflict events from ACLED for given countries and date range."""
    all_events = []
    for country in countries:
        params = {
            "country": country,
            "event_date": f"{start_date}|{date.today().isoformat()}",
            "event_date_where": "BETWEEN",
            "limit": limit,
            "_format": "json",
        }
        if event_types:
            params["event_type"] = "|".join(event_types)

        try:
            r = requests.get(
                "https://acleddata.com/api/acled/read",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                events = data.get("data", data) if isinstance(data, dict) else data
                if isinstance(events, list):
                    all_events.extend(events)
            else:
                logger.warning(f"ACLED fetch for {country}: {r.status_code}")
        except Exception as e:
            logger.warning(f"ACLED fetch failed for {country}: {e}")

    if not all_events:
        return pd.DataFrame()

    return _clean_acled_df(pd.DataFrame(all_events))


def _clean_acled_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize ACLED dataframe columns."""
    if "event_date" in df.columns:
        df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
    if "fatalities" in df.columns:
        df["fatalities"] = pd.to_numeric(df["fatalities"], errors="coerce").fillna(0).astype(int)
    if "latitude" in df.columns:
        df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    if "longitude" in df.columns:
        df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    return df.sort_values("event_date", ascending=False) if "event_date" in df.columns else df


ACLED_CSV_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "acled_events.csv")


def load_acled_csv() -> pd.DataFrame:
    """Load ACLED data from a manually downloaded CSV export."""
    try:
        if os.path.exists(ACLED_CSV_PATH):
            df = pd.read_csv(ACLED_CSV_PATH)
            return _clean_acled_df(df)
    except Exception as e:
        logger.error(f"Failed to load ACLED CSV: {e}")
    return pd.DataFrame()


ACLED_COUNTRIES = ["Iran", "Iraq", "Israel", "Saudi Arabia", "Yemen",
                   "Syria", "Lebanon", "United Arab Emirates", "Qatar", "Kuwait", "Bahrain"]

ACLED_EVENT_TYPES = ["Battles", "Explosions/Remote violence", "Violence against civilians",
                     "Strategic developments", "Protests", "Riots"]


@st.cache_data(ttl=3600)
def fetch_eia_oil_price(eia_key: str):
    """Fetch WTI spot price from EIA."""
    try:
        r = requests.get(
            f"https://api.eia.gov/v2/seriesid/PET.RWTC.D?api_key={eia_key}",
            timeout=30,
        )
        data = r.json()
        raw = data["response"]["data"]
        df = pd.DataFrame(raw)
        df["period"] = pd.to_datetime(df["period"])
        df["value"] = pd.to_numeric(df["value"])
        df = df.sort_values("period")
        return df.tail(180)
    except Exception as e:
        logger.error(f"EIA oil price fetch failed: {e}")
        return pd.DataFrame()


# --- FETCH DATA (parallelized) ---
with fun_loader("data"):
    import threading

    gdelt_queries = {
        "Iran Military/War": "Iran war military attack strike",
        "Strait of Hormuz": "Hormuz shipping oil strait",
        "Iran Sanctions & Diplomacy": "Iran sanctions ceasefire nuclear diplomacy",
    }

    # Run all independent data fetches in parallel
    _results = {}

    def _fetch_gdelt():
        _results["gdelt"] = fetch_all_gdelt_data(gdelt_queries)

    def _fetch_acled():
        acled_email = _get_key("ACLED_EMAIL")
        acled_password = _get_key("ACLED_PASSWORD")
        df = pd.DataFrame()
        src = None
        if acled_email and acled_password:
            token = _acled_get_token(acled_email, acled_password)
            if token:
                df = fetch_acled_events(token, ACLED_COUNTRIES)
                if not df.empty:
                    src = "api"
        if df.empty:
            df = load_acled_csv()
            if not df.empty:
                src = "csv"
        _results["acled"] = (df, src)

    def _fetch_oil():
        eia_key = _get_eia_key()
        _results["oil"] = fetch_eia_oil_price(eia_key) if eia_key else pd.DataFrame()

    def _fetch_stocks():
        data = {}
        for ticker in ["LMT", "RTX", "NOC", "XLE", "USO", "XOP"]:
            px = fetch_massive_data(ticker, 180)
            if px is not None and not px.empty:
                data[ticker] = px
        _results["stocks"] = data

    threads = [
        threading.Thread(target=_fetch_gdelt),
        threading.Thread(target=_fetch_acled),
        threading.Thread(target=_fetch_oil),
        threading.Thread(target=_fetch_stocks),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    gdelt_data, df_tone = _results.get("gdelt", ({}, None))
    df_acled, acled_source = _results.get("acled", (pd.DataFrame(), None))
    df_oil = _results.get("oil", pd.DataFrame())
    stock_data = _results.get("stocks", {})

    defense_tickers = {"LMT": "Lockheed Martin", "RTX": "RTX Corp", "NOC": "Northrop Grumman"}
    energy_tickers = {"XLE": "Energy ETF", "USO": "Oil ETF", "XOP": "Oil & Gas E&P ETF"}

    # GDELT bulk — use cached parquet, refresh in background
    df_gdelt_bulk = pd.DataFrame()
    _gdelt_parquet = os.path.join(os.path.dirname(__file__), "..", "data", "gdelt_events", "iran_conflict_events.parquet")
    if os.path.exists(_gdelt_parquet):
        try:
            df_gdelt_bulk = pd.read_parquet(_gdelt_parquet)
        except Exception:
            pass

    if df_gdelt_bulk.empty or "gdelt_bg_started" not in st.session_state:
        st.session_state["gdelt_bg_started"] = True
        def _bg_gdelt():
            try:
                fetch_gdelt_bulk_events(days=21)
            except Exception:
                pass
        threading.Thread(target=_bg_gdelt, daemon=True).start()


# --- ESCALATION METRICS ---
main_data = gdelt_data.get("Iran Military/War")

if main_data is not None and not main_data.empty:
    latest_vol = main_data["value"].iloc[-1]
    avg_7d = main_data["value"].tail(7).mean()
    avg_30d = main_data["value"].tail(30).mean()
    avg_90d = main_data["value"].tail(90).mean()
    trend_7v30 = ((avg_7d / avg_30d) - 1) * 100 if avg_30d > 0 else 0
    peak_vol = main_data["value"].max()
    peak_date = main_data.loc[main_data["value"].idxmax(), "date"]

    st.subheader("Escalation Dashboard")
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Today's Media Intensity", f"{latest_vol:.4f}")
    c2.metric("7-Day Average", f"{avg_7d:.4f}", f"{trend_7v30:+.1f}% vs 30d",
              delta_color="inverse")
    c3.metric("30-Day Average", f"{avg_30d:.4f}")
    c4.metric("90-Day Average", f"{avg_90d:.4f}")
    c5.metric("Peak Intensity", f"{peak_vol:.4f}", f"{peak_date.strftime('%b %d')}")

    # Escalation status
    if trend_7v30 > 50:
        st.error("ESCALATION ALERT: 7-day media intensity is significantly above 30-day baseline.")
    elif trend_7v30 > 20:
        st.warning("Elevated: Media intensity trending above baseline.")
    elif trend_7v30 < -20:
        st.success("De-escalation: Media intensity declining from baseline.")

    st.divider()


# --- TABS ---
tab_ai, tab_timeline, tab_acled, tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "AI War Analysis",
    "Conflict Timeline",
    "ACLED Events",
    "Media Intensity",
    "Topic Tracker",
    "Oil Price Correlation",
    "Market Impact",
    "Sentiment Analysis",
])


# ---- TAB: AI War Analysis (4-model blend) ----
with tab_ai, error_boundary("AI Analysis"):
    st.subheader("AI-Powered Conflict Intelligence")

    # Determine which models to use based on user tier
    from src.auth import get_user_tier
    user_tier = get_user_tier()
    active_configs = dict(MODEL_CONFIGS)
    if user_tier == "platinum":
        gpt5_key = _get_key(GPT5_CONFIG["key_name"])
        if gpt5_key:
            active_configs["openai"] = GPT5_CONFIG

    model_names = ", ".join(mc["name"] for mc in active_configs.values())
    platinum_note = " (Platinum tier: +GPT-5)" if user_tier == "platinum" else ""
    st.caption(f"Blended analysis from {model_names}{platinum_note} — updated hourly.")

    # Check for cached result
    latest_conflict, is_conflict_stale = get_latest_conflict_result()

    # Build base context (shared across all models — timeline + infrastructure)
    # Round to the hour so the prompt cache key stays stable within the same hour
    _ctx_time = datetime.now().replace(minute=0, second=0, microsecond=0)
    base_context = f"""Analyze the current state of the US-Israel war on Iran as of {_ctx_time.strftime('%B %d, %Y %I:%M %p')}.

Recent timeline of key events:
"""
    for evt in CONFLICT_TIMELINE_EVENTS[-6:]:
        base_context += f"- {evt['date']}: {evt['event']} (Impact: {evt['impact']})\n"
    base_context += "\nKey infrastructure status:\n"
    for name, info in INFRASTRUCTURE_TARGETS.items():
        base_context += f"- {name}: {info['capacity']} — {info['status']}\n"

    # Pre-build data sections (cached strings)
    prior_ctx = build_prior_analysis_context()
    gdelt_bulk_ctx = build_gdelt_ai_context(df_gdelt_bulk) if not df_gdelt_bulk.empty else ""

    def _build_model_context(model_key: str) -> str:
        """Build a slimmed-down context for a specific model based on its data_sections."""
        sections = active_configs.get(model_key, MODEL_CONFIGS.get(model_key, {})).get("data_sections")
        ctx = base_context

        # Only include data sections this model needs
        if sections is None or "timeline" in sections or "infrastructure" in sections:
            pass  # already in base_context

        ctx += "\n" + build_live_data_context(gdelt_data, df_oil, df_acled, df_tone, sections=sections)

        if (sections is None or "gdelt_bulk" in sections) and gdelt_bulk_ctx:
            ctx += "\n" + gdelt_bulk_ctx

        if (sections is None or "prior" in sections) and prior_ctx:
            ctx += "\n" + prior_ctx

        return ctx

    # Gather available API keys
    available_models = {}
    for mk, mc in active_configs.items():
        key = _get_key(mc["key_name"])
        if key:
            available_models[mk] = key

    if not available_models:
        st.warning("No AI API keys configured. Add GROK_API_KEY, GEMINI_API_KEY, or ANTHROPIC_API_KEY to secrets.")
    else:
        col_run, col_status = st.columns([1, 3])
        with col_run:
            _conflict_running = st.session_state.get("_conflict_running", False)
            force_refresh = st.button(
                "Running..." if _conflict_running else "Run AI Analysis",
                type="primary", use_container_width=True,
                disabled=_conflict_running,
            )
        with col_status:
            if latest_conflict and not is_conflict_stale:
                ts = latest_conflict.get("timestamp", "")
                try:
                    ts_fmt = pd.Timestamp(ts).strftime("%b %d, %I:%M %p")
                except Exception:
                    ts_fmt = ts
                models_str = ", ".join(latest_conflict.get("models_used", []))
                st.caption(f"Last updated: {ts_fmt} | Models: {models_str}")

        # Run analysis if stale or forced
        conflict_result = None
        if force_refresh or (is_conflict_stale and latest_conflict is None):
            st.session_state["_conflict_running"] = True
            n_models = len(available_models)
            model_names_list = [active_configs.get(mk, MODEL_CONFIGS.get(mk, {})).get("name", mk)
                                for mk in available_models]

            with fun_loader("ai"):
                model_results = {}
                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {}
                    for mk, api_key in available_models.items():
                        model_ctx = _build_model_context(mk)
                        mc = active_configs.get(mk)
                        futures[executor.submit(run_single_conflict_model, mk, api_key, model_ctx, mc)] = mk

                    for future in concurrent.futures.as_completed(futures):
                        mk = futures[future]
                        model_name = active_configs.get(mk, MODEL_CONFIGS.get(mk, {})).get("name", mk)
                        try:
                            model_results[mk] = future.result()
                        except Exception as e:
                            model_results[mk] = {"success": False, "error": str(e), "model_name": model_name}

            # Report per-model status
            succeeded = [mk for mk, r in model_results.items() if r.get("success")]
            failed = {mk: r.get("error", "Unknown") for mk, r in model_results.items() if not r.get("success")}

            conflict_result = blend_conflict_results(model_results, active_configs)
            if conflict_result.get("success"):
                save_conflict_result(conflict_result)
                st.success(f"Analysis complete — {len(succeeded)}/{n_models} models: {', '.join(active_configs.get(mk, {}).get('name', mk) for mk in succeeded)}")

            st.session_state["_conflict_running"] = False

            if failed:
                for mk, err in failed.items():
                    st.warning(f"{active_configs.get(mk, {}).get('name', mk)} failed: {err[:150]}")
        elif latest_conflict and latest_conflict.get("blended"):
            conflict_result = latest_conflict["blended"]
            conflict_result["success"] = True
            conflict_result["models_used"] = latest_conflict.get("models_used", [])

        # Display results
        if conflict_result and conflict_result.get("success"):
            st.divider()

            # Escalation gauge + key metrics
            esc = conflict_result.get("escalation_risk", {})
            oil = conflict_result.get("oil_impact", {})
            diplo = conflict_result.get("diplomatic_outlook", {})

            esc_score = esc.get("score", 5)
            esc_level = esc.get("level", "Unknown")
            esc_color = "#ff4b4b" if esc_score >= 8 else "#ff6b35" if esc_score >= 6 else "#ffaa00" if esc_score >= 4 else "#00ff96"

            # Infrastructure alerts — show at top if any status changes detected
            infra_alerts = conflict_result.get("infrastructure_alerts", [])
            if infra_alerts:
                alert_items = "".join(f"<li style='margin-bottom:4px;'>{a}</li>" for a in infra_alerts)
                st.markdown(f"""<div style="padding:12px 16px; border:2px solid #ff4b4b; border-radius:8px; background:rgba(255,75,75,0.08); margin-bottom:16px;">
                    <div style="color:#ff4b4b; font-weight:bold; font-size:16px;">INFRASTRUCTURE STATUS CHANGE DETECTED</div>
                    <ul style="color:#ccc; margin:8px 0 0 16px; padding:0; font-size:13px;">{alert_items}</ul>
                    <div style="color:#888; font-size:11px; margin-top:6px;">Source: AI models scanning X/Twitter and news feeds. Verify independently before acting.</div>
                </div>""", unsafe_allow_html=True)

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.markdown(f"""<div style="text-align:center; padding:12px; border:1px solid {esc_color}; border-radius:8px;">
                <div style="font-size:32px; font-weight:bold; color:{esc_color};">{esc_score}/10</div>
                <div style="color:{esc_color}; font-weight:bold;">{esc_level}</div>
                <div style="color:#888; font-size:12px;">Escalation Risk</div>
            </div>""", unsafe_allow_html=True)

            ai_disruption = oil.get("current_disruption_mbpd", 0)
            verified_disruption = abs(CURRENT_NET_DISRUPTION)
            # Use verified baseline; flag if AI significantly disagrees
            disruption_display = verified_disruption
            disruption_note = "Verified facility-by-facility"
            if ai_disruption and abs(ai_disruption - verified_disruption) > 1.0:
                disruption_note = f"Verified: {verified_disruption} | AI est: {ai_disruption}"
            mc2.markdown(f"""<div style="text-align:center; padding:12px; border:1px solid #ff6b35; border-radius:8px;">
                <div style="font-size:32px; font-weight:bold; color:#ff6b35;">{disruption_display} mbpd</div>
                <div style="color:#888; font-size:12px;">{disruption_note}</div>
            </div>""", unsafe_allow_html=True)

            price_fc = oil.get("price_forecast_30d", {})
            mc3.markdown(f"""<div style="text-align:center; padding:12px; border:1px solid #00d1ff; border-radius:8px;">
                <div style="font-size:32px; font-weight:bold; color:#00d1ff;">${price_fc.get('base', 0)}</div>
                <div style="color:#888; font-size:12px;">Oil 30d Base (${price_fc.get('low', 0)}-${price_fc.get('high', 0)})</div>
            </div>""", unsafe_allow_html=True)

            ceasefire = diplo.get("ceasefire_probability_30d", 0)
            cf_color = "#00ff96" if ceasefire >= 50 else "#ffaa00" if ceasefire >= 25 else "#ff4b4b"
            mc4.markdown(f"""<div style="text-align:center; padding:12px; border:1px solid {cf_color}; border-radius:8px;">
                <div style="font-size:32px; font-weight:bold; color:{cf_color};">{ceasefire}%</div>
                <div style="color:#888; font-size:12px;">Ceasefire Prob (30d)</div>
            </div>""", unsafe_allow_html=True)

            st.divider()

            # Situation summary
            st.markdown("#### Situation Summary")
            st.markdown(conflict_result.get("situation_summary", ""))

            # Latest developments
            devs = conflict_result.get("latest_developments", [])
            if devs:
                st.markdown("#### Latest Developments")
                for d in devs:
                    st.markdown(f"- {d}")

            st.divider()

            # Per-model escalation assessments — stacked for full readability
            st.markdown("#### Model Assessments")
            role_map = {"Grok 3": "Breaking News", "GPT-5": "Strategic Synthesis",
                        "Gemini 3 Pro": "Military/Energy", "Claude Sonnet": "Diplomatic"}
            model_assess = esc.get("model_assessments", [])
            for ma in model_assess:
                role = role_map.get(ma["model"], "")
                # Sanitize rationale — escape HTML tags and handle empty
                rationale = ma.get("rationale", "")
                if not rationale or not rationale.strip():
                    rationale = "No rationale provided."
                rationale = rationale.replace("<", "&lt;").replace(">", "&gt;")
                citations_html = ""
                if ma.get("citations"):
                    safe_cites = [c.replace("<", "&lt;").replace(">", "&gt;") for c in ma["citations"][:4]]
                    cites = " · ".join(safe_cites)
                    citations_html = f'<div style="color:#888; font-size:11px; margin-top:4px;">Citations: {cites}</div>'
                st.markdown(f"""<div style="padding:12px 14px; border-left:3px solid {ma['color']}; margin-bottom:8px; background:rgba(255,255,255,0.02); border-radius:0 6px 6px 0;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <span style="color:{ma['color']}; font-weight:bold; font-size:14px;">{ma['model']}</span>
                            <span style="color:#888; font-size:11px; margin-left:8px;">{role}</span>
                        </div>
                        <span style="font-size:20px; font-weight:bold; color:white;">{ma['score']}/10 — {ma['level']}</span>
                    </div>
                    <div style="color:#ccc; font-size:13px; margin-top:8px; line-height:1.5;">{rationale}</div>
                    {citations_html}
                </div>""", unsafe_allow_html=True)

            st.divider()

            # Infrastructure status from AI
            infra_list = conflict_result.get("infrastructure_status", [])
            if infra_list:
                st.markdown("#### Infrastructure Status (AI Assessment)")
                status_colors = {"offline": "#ff4b4b", "degraded": "#ffaa00", "operational": "#00ff96"}
                status_icons = {"offline": "🔴", "degraded": "🟡", "operational": "🟢"}
                infra_rows = ""
                for item in infra_list:
                    status = item.get("status", "unknown")
                    changed = item.get("changed", False)
                    sc = status_colors.get(status, "#888")
                    si = status_icons.get(status, "⚪")
                    change_badge = f'<span style="color:#ff4b4b;font-size:0.65rem;font-weight:700;border:1px solid #ff4b4b;padding:1px 5px;border-radius:3px;margin-left:4px;">CHANGED</span>' if changed else ""
                    infra_rows += (
                        f'<tr style="border-bottom:1px solid #30363d;">'
                        f'<td style="padding:6px 8px;font-weight:600;">{si} {item.get("name", "")}{change_badge}</td>'
                        f'<td style="padding:6px 8px;color:{sc};font-weight:700;text-transform:uppercase;">{status}</td>'
                        f'<td style="padding:6px 8px;color:#aaa;font-size:0.82rem;">{item.get("detail", "")}</td>'
                        f'</tr>'
                    )
                st.markdown(
                    f'<table style="width:100%;border-collapse:collapse;font-size:0.85rem;">'
                    f'<tr style="border-bottom:2px solid #30363d;">'
                    f'<th style="padding:6px 8px;text-align:left;color:#888;">Facility</th>'
                    f'<th style="padding:6px 8px;text-align:left;color:#888;">Status</th>'
                    f'<th style="padding:6px 8px;text-align:left;color:#888;">Detail</th>'
                    f'</tr>{infra_rows}</table>',
                    unsafe_allow_html=True,
                )

            st.divider()

            # Market implications
            mkt = conflict_result.get("market_implications", {})
            if any(mkt.values()):
                st.markdown("#### Market Implications")
                for sector, text in mkt.items():
                    if text:
                        st.markdown(f"**{sector.replace('_', ' ').title()}:** {text}")

            # Scenario forecasts — independent probabilities
            scenarios = conflict_result.get("scenario_forecast", [])
            if scenarios:
                st.divider()
                st.markdown("#### Scenario Forecasts")
                st.caption("Independent probability estimates — scenarios may overlap and are not mutually exclusive.")
                for s in sorted(scenarios, key=lambda x: x.get("probability", 0), reverse=True):
                    prob = s.get("probability", 0)
                    bar_color = "#ff4b4b" if "escal" in s.get("scenario", "").lower() else "#00ff96" if "ceasefire" in s.get("scenario", "").lower() or "de-escal" in s.get("scenario", "").lower() else "#ffaa00"
                    bar_width = max(2, min(prob, 100))
                    trigger = s.get("trigger", "")
                    trigger_html = f'<br/><span style="color:#888; font-size:12px;">Trigger: {trigger}</span>' if trigger else ""
                    oil_html = f'<br/><span style="color:#888; font-size:12px;">Oil: {s.get("oil_impact", "")}</span>' if s.get("oil_impact") else ""
                    st.markdown(f"""<div style="padding:10px 12px; border:1px solid {bar_color}33; margin-bottom:8px; background:rgba(255,255,255,0.02); border-radius:6px;">
                        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                            <span style="color:{bar_color}; font-weight:bold; font-size:14px;">{s.get('scenario', '')}</span>
                            <span style="color:{bar_color}; font-weight:bold; font-size:18px;">{prob}%</span>
                        </div>
                        <div style="background:rgba(255,255,255,0.05); border-radius:3px; height:6px; margin-bottom:8px;">
                            <div style="background:{bar_color}; width:{bar_width}%; height:100%; border-radius:3px;"></div>
                        </div>
                        <span style="color:#ccc; font-size:13px;">{s.get('description', '')}</span>
                        {oil_html}{trigger_html}
                    </div>""", unsafe_allow_html=True)

            # Trending tweets from X/Twitter (primarily from Grok)
            tweets = conflict_result.get("trending_tweets", [])
            if tweets:
                st.divider()
                st.markdown("#### Trending on X")
                st.caption("High-velocity tweets detected by Grok — sorted by engagement speed.")
                for tw in tweets:
                    handle = tw.get("handle", "")
                    content = tw.get("content", "")
                    likes = tw.get("likes", 0)
                    rts = tw.get("retweets", 0)
                    replies = tw.get("replies", 0)
                    time_ago = tw.get("time", "")
                    velocity = tw.get("velocity", "")
                    why = tw.get("why_important", "")

                    vel_color = "#ff4444" if velocity == "high" else "#ffaa00" if velocity == "medium" else "#888"
                    vel_icon = "🔥" if velocity == "high" else "📈" if velocity == "medium" else "💬"

                    metrics_parts = []
                    if likes:
                        metrics_parts.append(f"♥ {likes:,}")
                    if rts:
                        metrics_parts.append(f"🔁 {rts:,}")
                    if replies:
                        metrics_parts.append(f"💬 {replies:,}")
                    metrics_str = "&nbsp;&nbsp;".join(metrics_parts)

                    st.markdown(
                        f'<div style="padding:10px 14px;border-left:3px solid {vel_color};margin-bottom:6px;'
                        f'background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
                        f'<span style="color:#00d1ff;font-weight:600;">{handle}</span>'
                        f'<span style="font-size:0.7rem;color:#888;">{time_ago} {vel_icon}</span>'
                        f'</div>'
                        f'<div style="color:#ddd;font-size:0.85rem;line-height:1.5;margin-bottom:6px;">{content}</div>'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                        f'<span style="font-size:0.7rem;color:#888;">{metrics_str}</span>'
                        f'<span style="font-size:0.7rem;color:{vel_color};font-style:italic;">{why}</span>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )


# ---- TAB: Conflict Timeline ----
with tab_timeline, error_boundary("Timeline"):
    st.subheader("Iran War Timeline & Infrastructure Impact")

    # ── Conflict Clock ──
    war_start = pd.Timestamp("2026-02-28")
    days_of_conflict = (pd.Timestamp.now() - war_start).days
    total_events = len(CONFLICT_TIMELINE_EVENTS)
    current_disruption = abs(CURRENT_NET_DISRUPTION)
    peak_disruption = max(d["disruption"] for d in DISRUPTION_TIMELINE)
    current_phase = "Unknown"
    for phase in reversed(CONFLICT_PHASES):
        if pd.Timestamp.now() >= pd.Timestamp(phase["start"]):
            current_phase = phase["name"]
            break

    st.markdown(f"""<div style="padding:16px; border:1px solid #ff4b4b; border-radius:10px; background:rgba(255,75,75,0.05); margin-bottom:16px;">
        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
            <div style="text-align:center;">
                <div style="font-size:42px; font-weight:bold; color:#ff4b4b;">DAY {days_of_conflict}</div>
                <div style="color:#888; font-size:12px;">of the Iran War</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:20px; font-weight:bold; color:#ffaa00;">{current_phase}</div>
                <div style="color:#888; font-size:12px;">Current Phase</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:20px; font-weight:bold; color:#ff6b35;">{current_disruption} mbpd</div>
                <div style="color:#888; font-size:12px;">Supply Offline (peak: {peak_disruption})</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:20px; font-weight:bold; color:#00d1ff;">{total_events} events</div>
                <div style="color:#888; font-size:12px;">Key Events Tracked</div>
            </div>
        </div>
    </div>""", unsafe_allow_html=True)

    # ── Filter Controls ──
    cat_colors = {
        "Military": "#ff4b4b",
        "Escalation": "#ff6b35",
        "Supply": "#ffaa00",
        "Policy": "#00d1ff",
        "Diplomatic": "#00ff96",
    }

    filter_cols = st.columns([3, 2])
    with filter_cols[0]:
        selected_cats = st.multiselect(
            "Filter by category", list(cat_colors.keys()),
            default=list(cat_colors.keys()), key="tl_cat_filter")
    with filter_cols[1]:
        date_range = st.date_input(
            "Date range", value=(date(2026, 2, 28), date.today()),
            min_value=date(2026, 2, 28), max_value=date.today(), key="tl_date_range")

    # Build filtered timeline
    timeline_df = pd.DataFrame(CONFLICT_TIMELINE_EVENTS)
    timeline_df["date"] = pd.to_datetime(timeline_df["date"])
    if selected_cats:
        timeline_df = timeline_df[timeline_df["category"].isin(selected_cats)]
    if len(date_range) == 2:
        timeline_df = timeline_df[
            (timeline_df["date"].dt.date >= date_range[0]) &
            (timeline_df["date"].dt.date <= date_range[1])
        ]

    st.divider()

    # ── Oil Price + Events Annotated Chart ──
    st.markdown("#### Oil Price & Conflict Events")
    st.caption("WTI crude price with event markers — see cause and effect in real time.")

    fig_oil_tl = go.Figure()

    # Phase backgrounds
    for phase in CONFLICT_PHASES:
        fig_oil_tl.add_vrect(
            x0=phase["start"], x1=phase["end"],
            fillcolor=phase["color"], line_width=0, layer="below",
            annotation_text=phase["name"], annotation_position="top left",
            annotation=dict(font_size=9, font_color="rgba(255,255,255,0.5)"),
        )

    # Oil price line
    if not df_oil.empty:
        # Filter to conflict period
        oil_conflict = df_oil[df_oil["period"] >= "2026-02-20"].copy()
        fig_oil_tl.add_trace(go.Scatter(
            x=oil_conflict["period"], y=oil_conflict["value"],
            mode="lines", name="WTI Crude",
            line=dict(color="#00ff96", width=2.5),
            fill="tozeroy", fillcolor="rgba(0, 255, 150, 0.05)",
        ))

    # Event markers on oil chart
    for _, evt_row in timeline_df.iterrows():
        color = cat_colors.get(evt_row["category"], "#ffffff")
        fig_oil_tl.add_trace(go.Scatter(
            x=[evt_row["date"]], y=[None],
            mode="markers", name=evt_row["category"],
            marker=dict(size=0), showlegend=False,
        ))
        fig_oil_tl.add_vline(
            x=evt_row["date"], line=dict(color=color, width=1, dash="dot"),
        )
        fig_oil_tl.add_annotation(
            x=evt_row["date"], y=1.02, yref="paper",
            text=evt_row["event"][:30], showarrow=False,
            font=dict(size=8, color=color), textangle=-45,
        )

    fig_oil_tl.update_layout(
        template="plotly_dark", height=450, margin=dict(t=60, b=0, l=0, r=0),
        yaxis_title="WTI ($/bbl)", hovermode="x unified",
        xaxis=dict(range=["2026-02-20", pd.Timestamp.now().strftime("%Y-%m-%d")]),
    )
    st.plotly_chart(fig_oil_tl, use_container_width=True)

    st.divider()

    # ── Supply Disruption Over Time ──
    st.markdown("#### Cumulative Supply Disruption")
    st.caption("How total oil supply disruption evolved as each facility went offline or was restored.")

    disrupt_df = pd.DataFrame(DISRUPTION_TIMELINE)
    disrupt_df["date"] = pd.to_datetime(disrupt_df["date"])

    fig_disrupt = go.Figure()

    # Area chart for disruption
    fig_disrupt.add_trace(go.Scatter(
        x=disrupt_df["date"], y=disrupt_df["disruption"],
        mode="lines+markers", name="Total Disruption (mbpd)",
        line=dict(color="#ff4b4b", width=2.5, shape="spline"),
        fill="tozeroy", fillcolor="rgba(255, 75, 75, 0.15)",
        marker=dict(size=6, color="#ff4b4b"),
        hovertext=disrupt_df["note"],
        hoverinfo="text+y",
    ))

    # Phase backgrounds
    for phase in CONFLICT_PHASES:
        fig_disrupt.add_vrect(
            x0=phase["start"], x1=phase["end"],
            fillcolor=phase["color"], line_width=0, layer="below",
        )

    # Annotations for key moments
    peak_row = disrupt_df.loc[disrupt_df["disruption"].idxmax()]
    fig_disrupt.add_annotation(
        x=peak_row["date"], y=peak_row["disruption"],
        text=f"Peak: {peak_row['disruption']} mbpd",
        showarrow=True, arrowhead=2, arrowcolor="#ff4b4b",
        font=dict(color="#ff4b4b", size=12),
    )

    fig_disrupt.update_layout(
        template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
        yaxis_title="mbpd offline", hovermode="x unified",
    )
    st.plotly_chart(fig_disrupt, use_container_width=True)

    st.divider()

    # ── Infrastructure Map ──
    st.markdown("#### Infrastructure Impact Map")
    st.caption("Key facilities, pipelines, and chokepoints — color-coded by impact severity.")

    infra_map_data = []
    for name, info in INFRASTRUCTURE_TARGETS.items():
        infra_map_data.append({
            "name": name, "lat": info["lat"], "lon": info["lon"],
            "capacity": info["capacity"], "status": info["status"],
            "impact_level": info["impact_level"], "color": info["color"],
        })
    infra_map_df = pd.DataFrame(infra_map_data)

    size_map = {"Critical": 20, "Severe": 15, "Moderate": 10}
    symbol_map = {"Critical": "circle", "Severe": "diamond", "Moderate": "square"}

    fig_infra_map = go.Figure()

    for level in ["Critical", "Severe", "Moderate"]:
        subset = infra_map_df[infra_map_df["impact_level"] == level]
        if subset.empty:
            continue
        color = subset.iloc[0]["color"]
        fig_infra_map.add_trace(go.Scattermapbox(
            lat=subset["lat"], lon=subset["lon"],
            mode="markers+text",
            name=f"{level} Impact",
            marker=dict(
                size=size_map.get(level, 10),
                color=color,
                opacity=0.9,
            ),
            text=subset["name"],
            textposition="top center",
            textfont=dict(size=10, color=color),
            hovertext=subset.apply(
                lambda r: f"<b>{r['name']}</b><br>"
                          f"Capacity: {r['capacity']}<br>"
                          f"Status: {r['status']}<br>"
                          f"Impact: {r['impact_level']}", axis=1),
            hoverinfo="text",
        ))

    fig_infra_map.update_layout(
        mapbox=dict(
            style="carto-darkmatter",
            center=dict(lat=27, lon=50),
            zoom=4,
        ),
        height=500, margin=dict(t=0, b=0, l=0, r=0),
        showlegend=True,
        legend=dict(x=0, y=1, bgcolor="rgba(0,0,0,0.5)", font=dict(color="white")),
    )
    st.plotly_chart(fig_infra_map, use_container_width=True)

    st.divider()

    # ── Event Timeline (category lanes) ──
    st.markdown("#### Event Timeline by Category")

    if not timeline_df.empty:
        fig_tl = go.Figure()

        for cat, color in cat_colors.items():
            cat_events = timeline_df[timeline_df["category"] == cat]
            if cat_events.empty:
                continue
            fig_tl.add_trace(go.Scatter(
                x=cat_events["date"],
                y=[cat] * len(cat_events),
                mode="markers",
                name=cat,
                marker=dict(size=14, color=color, symbol="diamond"),
                hovertext=cat_events.apply(
                    lambda r: f"<b>{r['date'].strftime('%b %d')}: {r['event']}</b><br>"
                              f"Impact: {r['impact']}<br>"
                              f"Infrastructure: {r['infrastructure']}", axis=1),
                hoverinfo="text",
            ))

        # Phase backgrounds
        for phase in CONFLICT_PHASES:
            fig_tl.add_vrect(
                x0=phase["start"], x1=phase["end"],
                fillcolor=phase["color"], line_width=0, layer="below",
            )

        fig_tl.update_layout(
            template="plotly_dark", height=300, margin=dict(t=10, b=0, l=0, r=0),
            showlegend=True, hovermode="closest",
            xaxis=dict(title="", gridcolor="rgba(255,255,255,0.05)"),
            yaxis=dict(title="", categoryorder="array",
                       categoryarray=["Diplomatic", "Policy", "Supply", "Escalation", "Military"]),
        )
        st.plotly_chart(fig_tl, use_container_width=True)

    st.divider()

    # ── Detailed Event Cards ──
    st.markdown("#### Event Detail")
    for evt in reversed(CONFLICT_TIMELINE_EVENTS):
        cat = evt["category"]
        if cat not in selected_cats:
            continue
        evt_date = pd.Timestamp(evt["date"])
        if len(date_range) == 2 and (evt_date.date() < date_range[0] or evt_date.date() > date_range[1]):
            continue
        color = cat_colors.get(cat, "#ffffff")
        day_num = (evt_date - war_start).days
        st.markdown(f"""<div style="padding:10px 14px; border-left:3px solid {color}; margin-bottom:8px; background:rgba(255,255,255,0.02); border-radius:0 4px 4px 0;">
            <span style="color:{color}; font-weight:bold;">{evt['date']} (Day {day_num}) — {cat.upper()}</span><br/>
            <span style="color:white; font-size:14px; font-weight:bold;">{evt['event']}</span><br/>
            <span style="color:#ccc; font-size:13px;">Market Impact: {evt['impact']}</span><br/>
            <span style="color:#888; font-size:12px;">Infrastructure: {evt['infrastructure']}</span>
        </div>""", unsafe_allow_html=True)

    st.divider()

    # ── Infrastructure Status Cards ──
    st.markdown("#### Regional Infrastructure Status")
    st.caption("Key production facilities, refineries, and shipping chokepoints affected by the conflict.")

    infra_cols = st.columns(2)
    for i, (name, info) in enumerate(INFRASTRUCTURE_TARGETS.items()):
        with infra_cols[i % 2]:
            level = info["impact_level"]
            color = info["color"]
            icon = "🔴" if level == "Critical" else "🟡" if level == "Severe" else "🟠"
            st.markdown(f"""<div style="padding:10px 14px; border:1px solid {color}; border-radius:8px; margin-bottom:8px;">
                <div style="color:{color}; font-weight:bold; font-size:14px;">{icon} {name}</div>
                <div style="color:white; font-size:13px;">Capacity: {info['capacity']}</div>
                <div style="color:#ccc; font-size:13px;">Status: {info['status']}</div>
                <div style="color:{color}; font-size:12px; font-weight:bold;">Impact: {level}</div>
            </div>""", unsafe_allow_html=True)

    # ── Supply Disruption Waterfall ──
    st.divider()
    st.markdown("#### Current Supply Disruption Breakdown")

    waterfall_names = [d["facility"] for d in DISRUPTION_BREAKDOWN]
    waterfall_vals = [d["mbpd"] for d in DISRUPTION_BREAKDOWN]

    fig_wf = go.Figure(go.Waterfall(
        name="Supply Impact",
        orientation="v",
        x=waterfall_names,
        y=waterfall_vals,
        connector=dict(line=dict(color="rgba(255,255,255,0.2)")),
        decreasing=dict(marker=dict(color="#ff4b4b")),
        increasing=dict(marker=dict(color="#00ff96")),
        totals=dict(marker=dict(color="#00d1ff")),
        textposition="outside",
        text=[f"{v:+.1f}" for v in waterfall_vals],
        textfont=dict(color="white"),
    ))

    fig_wf.add_annotation(
        x=0.5, y=1.12, xref="paper", yref="paper",
        text=f"Net disruption: {CURRENT_NET_DISRUPTION:+.1f} mbpd",
        showarrow=False, font=dict(size=16, color="#ff4b4b" if CURRENT_NET_DISRUPTION < 0 else "#00ff96"),
    )

    fig_wf.update_layout(
        template="plotly_dark", height=450, margin=dict(t=50, b=0, l=0, r=0),
        yaxis_title="mbpd", showlegend=False,
        xaxis=dict(tickangle=-35),
    )
    st.plotly_chart(fig_wf, use_container_width=True)


# ---- TAB: ACLED Events ----
with tab_acled, error_boundary("ACLED Data"):
    st.subheader("Armed Conflict Events (ACLED)")
    st.caption("Real conflict events from the Armed Conflict Location & Event Data Project — battles, explosions, violence, protests across the region.")

    if acled_source:
        st.caption(f"Data source: {'ACLED API (live)' if acled_source == 'api' else 'Local CSV export'}")

    # File upload — always available as override or initial load
    with st.expander("Upload ACLED CSV Export", expanded=df_acled.empty):
        st.markdown("""
**To get ACLED data:**
1. Go to [ACLED Data Export Tool](https://acleddata.com/data-export-tool/)
2. Log in with your Google account
3. Select **Region:** Middle East, **Date range:** 2026-01-01 to today
4. Click **Export** → download the CSV
5. Upload it below
""")
        uploaded = st.file_uploader("Upload ACLED CSV", type=["csv", "xlsx"], key="acled_upload")
        if uploaded:
            try:
                if uploaded.name.endswith(".xlsx"):
                    df_upload = pd.read_excel(uploaded)
                else:
                    df_upload = pd.read_csv(uploaded)
                df_upload = _clean_acled_df(df_upload)
                if not df_upload.empty:
                    # Save to data/ for future sessions
                    os.makedirs(os.path.dirname(ACLED_CSV_PATH), exist_ok=True)
                    df_upload.to_csv(ACLED_CSV_PATH, index=False)
                    df_acled = df_upload
                    acled_source = "upload"
                    st.success(f"Loaded {len(df_acled):,} events from upload. Saved for future sessions.")
            except Exception as e:
                st.error(f"Failed to parse file: {e}")

    if df_acled.empty:
        st.info("No ACLED data available yet. Upload a CSV export above to get started.")
    else:
        # Summary metrics
        total_events = len(df_acled)
        total_fatalities = int(df_acled["fatalities"].sum()) if "fatalities" in df_acled.columns else 0
        countries_hit = df_acled["country"].nunique() if "country" in df_acled.columns else 0
        date_range = ""
        if "event_date" in df_acled.columns and not df_acled["event_date"].isna().all():
            date_range = f"{df_acled['event_date'].min().strftime('%b %d')} — {df_acled['event_date'].max().strftime('%b %d, %Y')}"

        ac1, ac2, ac3, ac4 = st.columns(4)
        ac1.metric("Total Events", f"{total_events:,}")
        ac2.metric("Total Fatalities", f"{total_fatalities:,}")
        ac3.metric("Countries Affected", countries_hit)
        ac4.metric("Date Range", date_range)

        st.divider()

        # Events by type over time
        if "event_type" in df_acled.columns and "event_date" in df_acled.columns:
            st.markdown("#### Events by Type Over Time")
            daily = df_acled.groupby([df_acled["event_date"].dt.date, "event_type"]).size().reset_index(name="count")
            daily["event_date"] = pd.to_datetime(daily["event_date"])

            type_colors = {
                "Battles": "#ff4b4b",
                "Explosions/Remote violence": "#ff6b35",
                "Violence against civilians": "#ffaa00",
                "Strategic developments": "#00d1ff",
                "Protests": "#ad7fff",
                "Riots": "#ff69b4",
            }

            fig_etype = go.Figure()
            for etype in daily["event_type"].unique():
                subset = daily[daily["event_type"] == etype]
                fig_etype.add_trace(go.Bar(
                    x=subset["event_date"], y=subset["count"],
                    name=etype, marker_color=type_colors.get(etype, "#888888"),
                ))

            fig_etype.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                barmode="stack", yaxis_title="Events", hovermode="x unified",
                legend=dict(orientation="h", y=-0.15),
            )
            st.plotly_chart(fig_etype, use_container_width=True)

        # Fatalities by country
        if "country" in df_acled.columns and "fatalities" in df_acled.columns:
            st.markdown("#### Fatalities by Country")
            country_fat = df_acled.groupby("country")["fatalities"].sum().sort_values(ascending=True)
            country_fat = country_fat[country_fat > 0]

            if not country_fat.empty:
                fig_cf = go.Figure(go.Bar(
                    x=country_fat.values, y=country_fat.index,
                    orientation="h", marker_color="#ff4b4b",
                    text=country_fat.values, textposition="outside",
                    textfont=dict(color="white"),
                ))
                fig_cf.update_layout(
                    template="plotly_dark", height=max(250, len(country_fat) * 40),
                    margin=dict(t=10, b=0, l=0, r=0),
                    xaxis_title="Fatalities",
                )
                st.plotly_chart(fig_cf, use_container_width=True)

        # Fatalities over time
        if "event_date" in df_acled.columns and "fatalities" in df_acled.columns:
            st.markdown("#### Daily Fatalities")
            daily_fat = df_acled.groupby(df_acled["event_date"].dt.date)["fatalities"].sum().reset_index()
            daily_fat.columns = ["date", "fatalities"]
            daily_fat["date"] = pd.to_datetime(daily_fat["date"])
            daily_fat = daily_fat.sort_values("date")
            daily_fat["ma7"] = daily_fat["fatalities"].rolling(7, min_periods=1).mean()

            fig_fat = go.Figure()
            fig_fat.add_trace(go.Bar(
                x=daily_fat["date"], y=daily_fat["fatalities"],
                name="Daily", marker_color="rgba(255, 75, 75, 0.4)",
            ))
            fig_fat.add_trace(go.Scatter(
                x=daily_fat["date"], y=daily_fat["ma7"],
                name="7-Day Avg", line=dict(color="#ff4b4b", width=2.5),
            ))
            fig_fat.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Fatalities", hovermode="x unified",
            )
            st.plotly_chart(fig_fat, use_container_width=True)

        # Event map
        if "latitude" in df_acled.columns and "longitude" in df_acled.columns:
            map_df = df_acled.dropna(subset=["latitude", "longitude"]).copy()
            if not map_df.empty:
                st.markdown("#### Conflict Event Map")
                map_df["size"] = (map_df["fatalities"].clip(lower=1) ** 0.5) * 3 if "fatalities" in map_df.columns else 5

                fig_map = go.Figure(go.Scattermapbox(
                    lat=map_df["latitude"], lon=map_df["longitude"],
                    mode="markers",
                    marker=dict(
                        size=map_df["size"],
                        color=map_df["event_type"].map(type_colors) if "event_type" in map_df.columns else "#ff4b4b",
                        opacity=0.7,
                    ),
                    hovertext=map_df.apply(
                        lambda r: f"<b>{r.get('event_type', '')}</b><br>"
                                  f"{r.get('location', '')}, {r.get('country', '')}<br>"
                                  f"{r.get('event_date', '')}<br>"
                                  f"Fatalities: {r.get('fatalities', 0)}<br>"
                                  f"{str(r.get('notes', ''))[:120]}", axis=1),
                    hoverinfo="text",
                ))

                center_lat = map_df["latitude"].mean()
                center_lon = map_df["longitude"].mean()
                fig_map.update_layout(
                    mapbox=dict(
                        style="carto-darkmatter",
                        center=dict(lat=center_lat, lon=center_lon),
                        zoom=3.5,
                    ),
                    height=500, margin=dict(t=0, b=0, l=0, r=0),
                )
                st.plotly_chart(fig_map, use_container_width=True)

        # Recent events table
        st.divider()
        st.markdown("#### Recent Events")
        display_cols = [c for c in ["event_date", "event_type", "sub_event_type", "country",
                                     "location", "fatalities", "actor1", "actor2", "notes"]
                        if c in df_acled.columns]
        st.dataframe(
            df_acled[display_cols].head(100),
            use_container_width=True, height=400,
            column_config={
                "event_date": st.column_config.DateColumn("Date"),
                "fatalities": st.column_config.NumberColumn("Deaths", format="%d"),
                "notes": st.column_config.TextColumn("Details", width="large"),
            },
        )


# ---- TAB 1: Conflict Intensity Timeline ----
with tab1:
    if main_data is not None and not main_data.empty:
        st.subheader("Iran Conflict Media Intensity (6 Months)")

        fig_main = go.Figure()

        # Main intensity
        fig_main.add_trace(go.Scatter(
            x=main_data["date"], y=main_data["value"],
            mode="lines", name="Daily Intensity",
            line=dict(color="#ff4b4b", width=1.5),
            fill="tozeroy", fillcolor="rgba(255, 75, 75, 0.1)",
        ))

        # 7-day rolling average
        main_data_plot = main_data.copy()
        main_data_plot["ma7"] = main_data_plot["value"].rolling(7).mean()
        main_data_plot["ma30"] = main_data_plot["value"].rolling(30).mean()

        fig_main.add_trace(go.Scatter(
            x=main_data_plot["date"], y=main_data_plot["ma7"],
            mode="lines", name="7-Day MA",
            line=dict(color="#ffaa00", width=2),
        ))
        fig_main.add_trace(go.Scatter(
            x=main_data_plot["date"], y=main_data_plot["ma30"],
            mode="lines", name="30-Day MA",
            line=dict(color="#00d1ff", width=2, dash="dash"),
        ))

        fig_main.update_layout(
            template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="GDELT Volume Intensity", hovermode="x unified",
        )
        st.plotly_chart(fig_main, use_container_width=True)

        # Dual axis with oil
        if not df_oil.empty:
            st.subheader("Conflict Intensity vs. WTI Crude Price")
            fig_dual = go.Figure()

            fig_dual.add_trace(go.Scatter(
                x=main_data["date"], y=main_data["value"],
                mode="lines", name="Conflict Intensity",
                line=dict(color="#ff4b4b", width=2), yaxis="y",
            ))

            fig_dual.add_trace(go.Scatter(
                x=df_oil["period"], y=df_oil["value"],
                mode="lines", name="WTI Crude ($/bbl)",
                line=dict(color="#00ff96", width=2), yaxis="y2",
            ))

            fig_dual.update_layout(
                template="plotly_dark", height=450, margin=dict(t=10, b=0, l=0, r=0),
                hovermode="x unified",
                yaxis=dict(title="Conflict Intensity", side="left", showgrid=False),
                yaxis2=dict(title="WTI ($/bbl)", side="right", overlaying="y", showgrid=False),
            )
            st.plotly_chart(fig_dual, use_container_width=True)
    else:
        st.warning("GDELT API data unavailable. API may be rate-limited — try refreshing in a minute.")

    # GDELT Bulk Events section (always available — direct downloads, no rate limits)
    if not df_gdelt_bulk.empty:
        st.divider()
        st.subheader("GDELT Conflict Events (Bulk Data)")
        st.caption("Direct daily downloads from GDELT — no API rate limits. Covers all conflict events in the Iran/Middle East region.")

        gdelt_summary = summarize_gdelt_events(df_gdelt_bulk)

        # Metrics row
        gc1, gc2, gc3, gc4, gc5 = st.columns(5)
        gc1.metric("Total Events", f"{gdelt_summary.get('total_events', 0):,}")
        gc2.metric("Daily Average", f"{gdelt_summary.get('daily_avg', 0)}")
        gc3.metric("Peak Day", f"{gdelt_summary.get('daily_peak', 0):,}",
                   gdelt_summary.get("peak_date", ""))
        gc4.metric("Avg Tone", f"{gdelt_summary.get('avg_tone', 0):.1f}",
                   "hostile" if gdelt_summary.get("avg_tone", 0) < -3 else "neutral",
                   delta_color="inverse")
        gc5.metric("Media Mentions", f"{gdelt_summary.get('total_mentions', 0):,}")

        # Daily event count + tone chart
        daily_events = df_gdelt_bulk.groupby(df_gdelt_bulk["SQLDATE"].dt.date).agg(
            events=("GLOBALEVENTID", "count"),
            tone=("AvgTone", "mean"),
            mentions=("NumMentions", "sum"),
        ).reset_index()
        daily_events.columns = ["date", "events", "tone", "mentions"]
        daily_events["date"] = pd.to_datetime(daily_events["date"])
        daily_events = daily_events.sort_values("date")

        fig_gdelt = go.Figure()

        fig_gdelt.add_trace(go.Bar(
            x=daily_events["date"], y=daily_events["events"],
            name="Conflict Events", marker_color="rgba(255, 75, 75, 0.5)",
            yaxis="y",
        ))
        fig_gdelt.add_trace(go.Scatter(
            x=daily_events["date"], y=daily_events["tone"],
            name="Avg Tone", line=dict(color="#00d1ff", width=2.5),
            yaxis="y2",
        ))

        fig_gdelt.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            hovermode="x unified",
            yaxis=dict(title="Events/Day", side="left"),
            yaxis2=dict(title="Tone (neg=hostile)", side="right", overlaying="y",
                        showgrid=False),
            legend=dict(orientation="h", y=-0.12),
        )
        st.plotly_chart(fig_gdelt, use_container_width=True)

        # Event type breakdown
        evtype_cols = st.columns(2)
        with evtype_cols[0]:
            st.markdown("**Events by Type**")
            by_type = df_gdelt_bulk["EventType"].value_counts()
            fig_type = go.Figure(go.Bar(
                x=by_type.values, y=by_type.index,
                orientation="h",
                marker_color=["#ff4b4b", "#ff6b35", "#ffaa00", "#00d1ff", "#ad7fff"][:len(by_type)],
                text=by_type.values, textposition="outside",
                textfont=dict(color="white"),
            ))
            fig_type.update_layout(
                template="plotly_dark", height=250, margin=dict(t=0, b=0, l=0, r=40),
                xaxis_title="Events",
            )
            st.plotly_chart(fig_type, use_container_width=True)

        with evtype_cols[1]:
            st.markdown("**Events by Country**")
            by_country = df_gdelt_bulk["ActionCountry"].value_counts().head(8)
            fig_country = go.Figure(go.Bar(
                x=by_country.values, y=by_country.index,
                orientation="h",
                marker_color="#00ff96",
                text=by_country.values, textposition="outside",
                textfont=dict(color="white"),
            ))
            fig_country.update_layout(
                template="plotly_dark", height=250, margin=dict(t=0, b=0, l=0, r=40),
                xaxis_title="Events",
            )
            st.plotly_chart(fig_country, use_container_width=True)

        # Event map
        map_df = df_gdelt_bulk.dropna(subset=["ActionGeo_Lat", "ActionGeo_Long"]).copy()
        if not map_df.empty and len(map_df) > 0:
            st.markdown("**Conflict Event Map (GDELT)**")
            # Sample if too many points
            if len(map_df) > 3000:
                map_df = map_df.sample(3000, random_state=42)

            type_colors = {
                "Military Force": "#ff4b4b", "Fight": "#ff6b35", "Assault": "#ffaa00",
                "Coerce": "#00d1ff", "Protest": "#ad7fff", "Other Conflict": "#888888",
            }

            fig_gmap = go.Figure()
            for etype, color in type_colors.items():
                subset = map_df[map_df["EventType"] == etype]
                if subset.empty:
                    continue
                fig_gmap.add_trace(go.Scattermapbox(
                    lat=subset["ActionGeo_Lat"], lon=subset["ActionGeo_Long"],
                    mode="markers", name=etype,
                    marker=dict(size=4, color=color, opacity=0.6),
                    hovertext=subset.apply(
                        lambda r: f"<b>{r.get('EventType', '')}</b><br>"
                                  f"{r.get('ActionGeo_FullName', '')}<br>"
                                  f"{r.get('Actor1Name', '')} → {r.get('Actor2Name', '')}<br>"
                                  f"Goldstein: {r.get('GoldsteinScale', '')}", axis=1),
                    hoverinfo="text",
                ))

            fig_gmap.update_layout(
                mapbox=dict(style="carto-darkmatter",
                            center=dict(lat=30, lon=48), zoom=3.5),
                height=450, margin=dict(t=0, b=0, l=0, r=0),
                showlegend=True,
                legend=dict(x=0, y=1, bgcolor="rgba(0,0,0,0.5)", font=dict(color="white")),
            )
            st.plotly_chart(fig_gmap, use_container_width=True)


# ---- TAB 2: Topic Tracker ----
with tab2:
    st.subheader("Sub-Topic Media Intensity")
    st.markdown("Track individual conflict dimensions to identify which risks are driving headlines.")

    if gdelt_data:
        topic_colors = {
            "Iran Military/War": "#ff4b4b",
            "Iran-Israel": "#00d1ff",
            "Strait of Hormuz": "#ffaa00",
            "Iran Nuclear": "#ad7fff",
            "Iran Sanctions": "#00ff96",
        }

        fig_topics = go.Figure()
        for label, df_t in gdelt_data.items():
            # 7-day smoothing for readability
            df_t_plot = df_t.copy()
            df_t_plot["smooth"] = df_t_plot["value"].rolling(7, min_periods=1).mean()
            fig_topics.add_trace(go.Scatter(
                x=df_t_plot["date"], y=df_t_plot["smooth"],
                mode="lines", name=label,
                line=dict(color=topic_colors.get(label, "white"), width=2),
            ))

        fig_topics.update_layout(
            template="plotly_dark", height=500, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Media Intensity (7d MA)", hovermode="x unified",
        )
        st.plotly_chart(fig_topics, use_container_width=True)

        # Topic comparison metrics
        st.subheader("Current Topic Intensity (7-Day Average)")
        topic_cols = st.columns(len(gdelt_data))
        for col, (label, df_t) in zip(topic_cols, gdelt_data.items()):
            avg_7 = df_t["value"].tail(7).mean()
            avg_30 = df_t["value"].tail(30).mean()
            change = ((avg_7 / avg_30) - 1) * 100 if avg_30 > 0 else 0
            col.metric(label.replace("Iran ", "").replace("Iran-", ""),
                       f"{avg_7:.4f}", f"{change:+.0f}% vs 30d",
                       delta_color="inverse")

        # Stacked area showing relative share
        st.subheader("Topic Share Over Time")
        # Merge all topics by date
        merged = None
        for label, df_t in gdelt_data.items():
            df_temp = df_t[["date", "value"]].copy()
            df_temp = df_temp.rename(columns={"value": label})
            df_temp["date"] = df_temp["date"].dt.date
            if merged is None:
                merged = df_temp
            else:
                merged = pd.merge(merged, df_temp, on="date", how="outer")

        if merged is not None:
            merged = merged.sort_values("date").fillna(0)
            merged["date"] = pd.to_datetime(merged["date"])

            fig_stack = go.Figure()
            for label in gdelt_data.keys():
                if label in merged.columns:
                    fig_stack.add_trace(go.Scatter(
                        x=merged["date"], y=merged[label].rolling(7, min_periods=1).mean(),
                        mode="lines", name=label, stackgroup="topics",
                        line=dict(width=0.5, color=topic_colors.get(label, "white")),
                    ))

            fig_stack.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Combined Intensity", hovermode="x unified",
            )
            st.plotly_chart(fig_stack, use_container_width=True)
    else:
        st.warning("No topic data available.")


# ---- TAB 3: Oil Price Correlation ----
with tab3:
    st.subheader("Conflict Impact on Oil Markets")

    if not df_oil.empty and main_data is not None and not main_data.empty:
        # Merge conflict and oil data
        df_conflict = main_data[["date", "value"]].copy()
        df_conflict["date"] = df_conflict["date"].dt.date

        df_oil_merge = df_oil[["period", "value"]].copy()
        df_oil_merge["date"] = df_oil_merge["period"].dt.date
        df_oil_merge = df_oil_merge.rename(columns={"value": "oil_price"})

        df_corr = pd.merge(
            df_conflict.rename(columns={"value": "conflict"}),
            df_oil_merge[["date", "oil_price"]],
            on="date", how="inner",
        )

        if not df_corr.empty:
            # Rolling correlation
            df_corr["date"] = pd.to_datetime(df_corr["date"])
            df_corr = df_corr.sort_values("date")
            df_corr["rolling_corr"] = df_corr["conflict"].rolling(30).corr(df_corr["oil_price"])

            overall_corr = df_corr["conflict"].corr(df_corr["oil_price"])

            oc1, oc2 = st.columns(2)
            oc1.metric("Overall Correlation", f"{overall_corr:.3f}")
            recent_corr = df_corr["rolling_corr"].iloc[-1] if not df_corr["rolling_corr"].isna().all() else 0
            oc2.metric("30-Day Rolling Correlation", f"{recent_corr:.3f}")

            # Rolling correlation chart
            fig_corr = go.Figure()
            fig_corr.add_trace(go.Scatter(
                x=df_corr["date"], y=df_corr["rolling_corr"],
                mode="lines", line=dict(color="#00d1ff", width=2),
                name="30-Day Rolling Correlation",
            ))
            fig_corr.add_hline(y=0, line_color="white", line_width=1)
            fig_corr.add_hrect(y0=0.3, y1=1, fillcolor="rgba(0, 255, 150, 0.05)", line_width=0)
            fig_corr.add_hrect(y0=-1, y1=-0.3, fillcolor="rgba(255, 75, 75, 0.05)", line_width=0)

            fig_corr.update_layout(
                template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                yaxis_title="Correlation", yaxis=dict(range=[-1, 1]),
                hovermode="x unified",
            )
            st.plotly_chart(fig_corr, use_container_width=True)

            # Scatter plot
            st.subheader("Conflict Intensity vs. Oil Price (Scatter)")
            fig_scatter = go.Figure()
            fig_scatter.add_trace(go.Scatter(
                x=df_corr["conflict"], y=df_corr["oil_price"],
                mode="markers", marker=dict(color="#ff4b4b", size=5, opacity=0.5),
                hovertemplate="Conflict: %{x:.4f}<br>Oil: $%{y:.2f}<extra></extra>",
            ))
            fig_scatter.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                xaxis_title="Conflict Media Intensity",
                yaxis_title="WTI Crude ($/bbl)",
            )
            st.plotly_chart(fig_scatter, use_container_width=True)
    else:
        st.warning("Oil price or conflict data unavailable.")


# ---- TAB 4: Market Impact ----
with tab4:
    st.subheader("Defense & Energy Sector Performance")

    if stock_data:
        # Normalize all stocks to % return from start of period
        st.markdown("**Defense Stocks**")
        fig_def = go.Figure()
        def_colors = {"LMT": "#00d1ff", "RTX": "#00ff96", "NOC": "#ffaa00"}

        for ticker, name in defense_tickers.items():
            if ticker in stock_data:
                px = stock_data[ticker]
                normalized = (px["Close"] / px["Close"].iloc[0] - 1) * 100
                fig_def.add_trace(go.Scatter(
                    x=normalized.index, y=normalized.values,
                    mode="lines", name=f"{ticker} ({name})",
                    line=dict(color=def_colors.get(ticker, "white"), width=2),
                ))

        fig_def.add_hline(y=0, line_color="white", line_width=1)
        fig_def.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Return (%)", hovermode="x unified",
        )
        st.plotly_chart(fig_def, use_container_width=True)

        # Defense metrics
        dc = st.columns(len(defense_tickers))
        for col, (ticker, name) in zip(dc, defense_tickers.items()):
            if ticker in stock_data:
                px = stock_data[ticker]
                ret_total = (px["Close"].iloc[-1] / px["Close"].iloc[0] - 1) * 100
                ret_30d = (px["Close"].iloc[-1] / px["Close"].iloc[-min(22, len(px))] - 1) * 100
                col.metric(f"{ticker}", f"${px['Close'].iloc[-1]:.2f}",
                           f"{ret_30d:+.1f}% (30d)")

        st.divider()
        st.markdown("**Energy Stocks & ETFs**")
        fig_energy = go.Figure()
        energy_colors = {"XLE": "#ff4b4b", "USO": "#ffaa00", "XOP": "#ad7fff"}

        for ticker, name in energy_tickers.items():
            if ticker in stock_data:
                px = stock_data[ticker]
                normalized = (px["Close"] / px["Close"].iloc[0] - 1) * 100
                fig_energy.add_trace(go.Scatter(
                    x=normalized.index, y=normalized.values,
                    mode="lines", name=f"{ticker} ({name})",
                    line=dict(color=energy_colors.get(ticker, "white"), width=2),
                ))

        fig_energy.add_hline(y=0, line_color="white", line_width=1)
        fig_energy.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Return (%)", hovermode="x unified",
        )
        st.plotly_chart(fig_energy, use_container_width=True)

        ec = st.columns(len(energy_tickers))
        for col, (ticker, name) in zip(ec, energy_tickers.items()):
            if ticker in stock_data:
                px = stock_data[ticker]
                ret_30d = (px["Close"].iloc[-1] / px["Close"].iloc[-min(22, len(px))] - 1) * 100
                col.metric(f"{ticker}", f"${px['Close'].iloc[-1]:.2f}",
                           f"{ret_30d:+.1f}% (30d)")

        # Overlay conflict with defense ETF
        st.divider()
        st.subheader("Conflict Intensity vs. Defense Sector")
        if main_data is not None and "LMT" in stock_data:
            fig_overlay = go.Figure()

            fig_overlay.add_trace(go.Scatter(
                x=main_data["date"], y=main_data["value"],
                mode="lines", name="Conflict Intensity",
                line=dict(color="#ff4b4b", width=2), yaxis="y",
            ))

            lmt_px = stock_data["LMT"]
            fig_overlay.add_trace(go.Scatter(
                x=lmt_px.index, y=lmt_px["Close"],
                mode="lines", name="LMT Price",
                line=dict(color="#00d1ff", width=2), yaxis="y2",
            ))

            fig_overlay.update_layout(
                template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
                hovermode="x unified",
                yaxis=dict(title="Conflict Intensity", side="left", showgrid=False),
                yaxis2=dict(title="LMT ($)", side="right", overlaying="y", showgrid=False),
            )
            st.plotly_chart(fig_overlay, use_container_width=True)
    else:
        st.warning("Stock data unavailable.")


# ---- TAB 5: Sentiment Analysis ----
with tab5:
    st.subheader("Media Sentiment (GDELT Tone)")
    st.markdown("GDELT tone measures the average sentiment of global media coverage. **Negative** = more hostile/threatening coverage.")

    if df_tone is not None and not df_tone.empty:
        df_tone_plot = df_tone.copy()
        df_tone_plot["ma7"] = df_tone_plot["value"].rolling(7, min_periods=1).mean()

        latest_tone = df_tone_plot["value"].iloc[-1]
        avg_tone = df_tone_plot["value"].mean()
        min_tone = df_tone_plot["value"].min()
        min_tone_date = df_tone_plot.loc[df_tone_plot["value"].idxmin(), "date"]

        tc1, tc2, tc3 = st.columns(3)
        tc1.metric("Current Tone", f"{latest_tone:.2f}",
                    delta_color="normal" if latest_tone > avg_tone else "inverse")
        tc2.metric("6-Month Average", f"{avg_tone:.2f}")
        tc3.metric("Most Negative", f"{min_tone:.2f}", min_tone_date.strftime("%b %d"))

        fig_tone = go.Figure()

        # Color fill based on positive/negative
        fig_tone.add_trace(go.Scatter(
            x=df_tone_plot["date"], y=df_tone_plot["value"],
            mode="lines", name="Daily Tone",
            line=dict(color="#888888", width=1),
        ))
        fig_tone.add_trace(go.Scatter(
            x=df_tone_plot["date"], y=df_tone_plot["ma7"],
            mode="lines", name="7-Day MA",
            line=dict(color="#00d1ff", width=2.5),
        ))

        fig_tone.add_hline(y=0, line_color="white", line_width=1)
        fig_tone.add_hrect(y0=-15, y1=0, fillcolor="rgba(255, 75, 75, 0.05)", line_width=0)
        fig_tone.add_hrect(y0=0, y1=5, fillcolor="rgba(0, 255, 150, 0.05)", line_width=0)

        fig_tone.update_layout(
            template="plotly_dark", height=400, margin=dict(t=10, b=0, l=0, r=0),
            yaxis_title="Tone (negative = hostile)", hovermode="x unified",
        )
        st.plotly_chart(fig_tone, use_container_width=True)
        st.caption("Tone scale: Large negative values indicate hostile/threatening coverage. Values near 0 are neutral.")
    else:
        st.warning("Sentiment data unavailable. GDELT may be rate-limited — try refreshing.")
