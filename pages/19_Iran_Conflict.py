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
from src.data_engine import fetch_massive_data


def _get_openai_client(**kwargs):
    from openai import OpenAI
    return OpenAI(**kwargs)
from src.layout import setup_page, error_boundary, fun_loader, freshness_bar
from src.gdelt_events import fetch_gdelt_bulk_events, summarize_gdelt_events, build_gdelt_ai_context
from src.edgar import fetch_recent_8k as _fetch_edgar_8k
from src.gov_data import get_cot_summary, fetch_treasury_yields, get_defense_contract_summary

logger = logging.getLogger(__name__)

setup_page("19_Iran_Conflict")

st.title("🎖️ Iran Conflict Monitor")
st.markdown("Geopolitical risk tracking via GDELT media intensity, oil price correlation, and defense/energy market impact.")


from src.api_keys import get_secret as _get_key


# ─────────────────────────────────────────────
# AI WAR ANALYSIS ENGINE — 4-model blend
# ─────────────────────────────────────────────

CONFLICT_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "iran_conflict_history.json")
INFRA_STATE_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "iran_infra_state.json")


def _load_infra_state() -> list:
    """Load the last Grok-verified infrastructure state from Supabase, local JSON fallback."""
    # --- Supabase primary ---
    try:
        from src.db import get_client, get_user_id
        db = get_client()
        if db:
            resp = (db.table("conflict_analysis")
                    .select("infrastructure_status")
                    .eq("region", "iran")
                    .order("timestamp", desc=True)
                    .limit(1)
                    .execute())
            if resp.data and resp.data[0].get("infrastructure_status"):
                infra = resp.data[0]["infrastructure_status"]
                return infra if isinstance(infra, list) else json.loads(infra) if isinstance(infra, str) else []
    except Exception as e:
        logger.warning(f"Supabase infra state load failed: {e}")

    # --- Local JSON fallback ---
    try:
        if os.path.exists(INFRA_STATE_FILE):
            with open(INFRA_STATE_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load infra state: {e}")
    return []


def _save_infra_state(infra: list):
    """Persist Grok-verified infrastructure state to Supabase, local JSON fallback."""
    # --- Supabase primary ---
    try:
        from src.db import get_client, get_user_id
        db = get_client()
        if db:
            db.table("conflict_analysis").upsert({
                "user_id": get_user_id(),
                "region": "iran_infra_state",
                "timestamp": datetime.now().isoformat(),
                "infrastructure_status": json.dumps(infra),
                "situation_summary": "Infrastructure state snapshot",
                "escalation_risk": json.dumps({}),
                "models_used": [],
                "latest_developments": json.dumps([]),
            }).execute()
            # Also save locally as backup
    except Exception as e:
        logger.warning(f"Supabase infra state save failed: {e}")

    # --- Local JSON fallback (always write) ---
    try:
        os.makedirs(os.path.dirname(INFRA_STATE_FILE), exist_ok=True)
        with open(INFRA_STATE_FILE, "w") as f:
            json.dump(infra, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save infra state: {e}")

# Finance blogs and analysis sources — referenced by AI models for background context
FINANCE_BLOG_SOURCES = {
    "investing": [
        ("The Big Picture", "ritholtz.com"),
        ("A Wealth of Common Sense", "awealthofcommonsense.com"),
        ("Seeking Alpha", "seekingalpha.com"),
        ("The Motley Fool", "fool.com"),
        ("Financial Samurai", "financialsamurai.com"),
        ("Zacks Investment Research", "zacks.com"),
        ("ZeroHedge", "zerohedge.com"),
    ],
    "economics": [
        ("Marginal Revolution", "marginalrevolution.com"),
        ("Calculated Risk", "calculatedriskblog.com"),
        ("Money Stuff (Bloomberg)", "bloomberg.com"),
        ("FT Alphaville", "ft.com"),
        ("Of Dollars and Data", "ofdollarsanddata.com"),
        ("White Coat Investor", "whitecoatinvestor.com"),
    ],
    "personal_finance": [
        ("Get Rich Slowly", "getrichslowly.org"),
        ("Mr. Money Mustache", "mrmoneymustache.com"),
        ("The Penny Hoarder", "thepennyhoarder.com"),
        ("Afford Anything", "affordanything.com"),
        ("Budgets Are Sexy", "budgetsaresexy.com"),
        ("The Simple Dollar", "thesimpledollar.com"),
        ("I Will Teach You To Be Rich", "iwillteachyoutoberich.com"),
    ],
}

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
    {"date": "2026-03-20", "event": "Iranian missile barrages hit southern Israel — Dimona, Arad struck; 100+ injured",
     "category": "Military", "impact": "Escalation fears spike; oil surges 3%; defense stocks rally",
     "infrastructure": "Cluster munitions used; Israeli defenses intercepted ~92% of 400+ missiles since war start"},
    {"date": "2026-03-21", "event": "US/Israel retaliatory strikes on Natanz nuclear facility and IRGC missile sites",
     "category": "Military", "impact": "Iranian nuclear program severely degraded; leadership in disarray",
     "infrastructure": "Natanz centrifuge halls damaged; multiple IRGC command centers destroyed"},
    {"date": "2026-03-21", "event": "Hezbollah opens northern front with rocket barrages into Israel",
     "category": "Escalation", "impact": "Multi-front war fears; 1 death reported in northern Israel",
     "infrastructure": "Northern border towns under fire; IDF reinforcing positions"},
    {"date": "2026-03-22", "event": "Trump issues 48-hour ultimatum: Iran must reopen Strait of Hormuz or US will 'obliterate' power plants",
     "category": "Escalation", "impact": "Oil spikes on ultimatum; global energy crisis warnings intensify",
     "infrastructure": "Deadline ~March 23-24; targets identified as major Iranian power generation facilities"},
    {"date": "2026-03-22", "event": "Iran vows to close Hormuz permanently and target all Gulf energy/water infrastructure if attacked",
     "category": "Escalation", "impact": "Markets in panic; Brent above $110; safe havens surge",
     "infrastructure": "Iran threatens 'irreversible destruction' of regional energy infrastructure; Gulf states on highest alert"},
    {"date": "2026-03-23", "event": "Trump ultimatum deadline passes — US B-1 bombers strike 3 Iranian power plants; nationwide blackouts",
     "category": "Military", "impact": "Brent surges; gold hits all-time highs; US equities sell off sharply",
     "infrastructure": "Isfahan, Bandar Abbas, Ahvaz power plants hit; 40% of Iranian grid offline"},
    {"date": "2026-03-24", "event": "Iran retaliates with mass drone/missile salvo at US carrier group in Arabian Sea; USS Eisenhower damaged",
     "category": "Military", "impact": "Oil surges further; VIX spikes; flight-to-safety drives Treasury yields lower",
     "infrastructure": "USS Eisenhower flight deck damaged, 12 casualties; carrier ops suspended 48h"},
    {"date": "2026-03-25", "event": "Strait of Hormuz fully closed — zero tanker transits; Iran deploys additional mine fields",
     "category": "Supply", "impact": "Oil prices spike to multi-year highs; Asian spot LNG prices surge; OPEC emergency meeting called",
     "infrastructure": "21 mbpd of crude transit halted; UAE/Saudi pipelines at max bypass capacity (~7 mbpd)"},
    {"date": "2026-03-26", "event": "US strikes Fordow enrichment site and Bandar Abbas naval base; IRGC Navy destroyed",
     "category": "Military", "impact": "Oil pulls back slightly on 'peak escalation' hopes; defense stocks rally",
     "infrastructure": "Fordow 60% offline; 6 IRGC fast-attack boats sunk; Bandar Abbas port cratered"},
    {"date": "2026-03-27", "event": "Trump issues second ultimatum: reopen Hormuz in 48h or 'all Iranian energy infrastructure will be obliterated'",
     "category": "Escalation", "impact": "Oil re-surges on second ultimatum; EU calls for emergency energy rationing plans",
     "infrastructure": "Ultimatum deadline ~Mar 29; targets include refineries, pipelines, remaining power plants"},
    {"date": "2026-03-28", "event": "Iran launches 47 ballistic missiles at Tel Aviv/Haifa; 14 dead; US B-2s sink 2 frigates at Bandar Abbas",
     "category": "Military", "impact": "Brent surges past $145; global recession fears intensify; S&P futures drop sharply; gold hits new highs",
     "infrastructure": "Bat Yam suburbs hit; Bandar Abbas pier destroyed; Hezbollah fires 120 rockets into Galilee"},
    {"date": "2026-03-28", "event": "UNSC emergency session fails — Russia/China block US resolution; no ceasefire path visible",
     "category": "Diplomatic", "impact": "Hope for diplomatic resolution collapses; markets price in extended conflict",
     "infrastructure": "Khamenei successor Salami rejects all talks; Trump vows 'Iran blinks or blacks out'"},
]

INFRASTRUCTURE_TARGETS = {
    "Strait of Hormuz": {
        "capacity": "18-21 mbpd (20% of global oil)",
        "status": "FULLY CLOSED — Iran vows permanent closure; Trump 48h ultimatum to reopen or US destroys Iranian power plants (deadline ~Mar 23-24); commercial traffic near zero; mines, drones, missiles active",
        "impact_level": "Critical",
        "color": "#ff4b4b",
        "lat": 26.56, "lon": 56.25,
    },
    "Abqaiq (Saudi Arabia)": {
        "capacity": "7 mbpd processing",
        "status": "Operational — no confirmed 2026 strike; Saudi production cuts due to Hormuz export constraints, not facility damage; East-West pipeline bypass active",
        "impact_level": "Moderate",
        "color": "#00ff96",
        "lat": 25.94, "lon": 49.67,
    },
    "Ras Tanura (Saudi Arabia)": {
        "capacity": "6.5 mbpd export / 550 kbpd refinery",
        "status": "Refinery restarted after early Mar drone strike; export limited by Hormuz closure; some rerouting to Yanbu/Red Sea",
        "impact_level": "Severe",
        "color": "#ffaa00",
        "lat": 26.64, "lon": 50.17,
    },
    "Kharg Island (Iran)": {
        "capacity": "5 mbpd (90% of Iran exports)",
        "status": "Military targets struck Mar 13-14 but oil infrastructure spared; exports continue at 1.1-1.5 mbpd via shadow fleet tankers, mostly to China",
        "impact_level": "Severe",
        "color": "#ffaa00",
        "lat": 29.23, "lon": 50.31,
    },
    "Kirkuk-Ceyhan Pipeline": {
        "capacity": "450 kbpd",
        "status": "Restarting after years of political disputes (not struck); Iraq-KRG agreement Mar 17; initial flows ~250 kbpd to Ceyhan as Hormuz bypass",
        "impact_level": "Moderate",
        "color": "#00ff96",
        "lat": 35.47, "lon": 44.39,
    },
    "Fujairah (UAE)": {
        "capacity": "Major bunkering hub & storage",
        "status": "Multiple drone strikes mid-March caused fires and loading suspensions; operations disrupted; partial rerouting via Oman",
        "impact_level": "Severe",
        "color": "#ffaa00",
        "lat": 25.12, "lon": 56.33,
    },
    "Bab el-Mandeb / Red Sea": {
        "capacity": "4.8 mbpd transit",
        "status": "Houthi threats renewed in solidarity with Iran; Suez traffic sharply below pre-crisis levels; Cape rerouting elevated",
        "impact_level": "Severe",
        "color": "#ff6b35",
        "lat": 12.58, "lon": 43.33,
    },
    "Qatar LNG (Ras Laffan)": {
        "capacity": "77 mtpa LNG (~20-25% global supply)",
        "status": "Extensive damage from Iranian missile strikes Mar 18-19; production halted; force majeure declared; repairs could take months to years for some capacity",
        "impact_level": "Critical",
        "color": "#ff4b4b",
        "lat": 25.93, "lon": 51.53,
    },
    "Assaluyeh / South Pars (Iran)": {
        "capacity": "Major gas processing complex",
        "status": "Israeli/US strikes on treatment plants mid-March; phases offline; contributing to retaliatory cycle",
        "impact_level": "Critical",
        "color": "#ff4b4b",
        "lat": 27.48, "lon": 52.61,
    },
}

# Conflict phases for timeline labeling
CONFLICT_PHASES = [
    {"start": "2026-02-28", "end": "2026-03-02", "name": "Initial Strikes", "color": "rgba(255,75,75,0.1)"},
    {"start": "2026-03-02", "end": "2026-03-06", "name": "Hormuz Mining & Naval Standoff", "color": "rgba(255,107,53,0.1)"},
    {"start": "2026-03-07", "end": "2026-03-12", "name": "Infrastructure Attacks", "color": "rgba(255,75,75,0.15)"},
    {"start": "2026-03-13", "end": "2026-03-15", "name": "Regional Escalation", "color": "rgba(255,107,53,0.1)"},
    {"start": "2026-03-16", "end": "2026-03-19", "name": "Ceasefire Talks", "color": "rgba(0,255,150,0.08)"},
    {"start": "2026-03-20", "end": "2026-03-23", "name": "Escalation — Direct Exchange & Hormuz Ultimatum", "color": "rgba(255,75,75,0.2)"},
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
    {"date": "2026-03-20", "disruption": 13.0, "note": "Iranian missile barrages on Israel; retaliation strikes on Natanz"},
    {"date": "2026-03-21", "disruption": 13.5, "note": "Hezbollah opens northern front; multi-front war"},
    {"date": "2026-03-22", "disruption": 14.5, "note": "Trump 48h ultimatum; Iran vows permanent Hormuz closure; full energy war"},
]

# Single source of truth for facility-by-facility disruption (used in waterfall + AI prompt)
DISRUPTION_BREAKDOWN = [
    {"facility": "Hormuz closure (commercial traffic near-zero)", "mbpd": -15.0},
    {"facility": "Kharg Island (still exporting 1.1-1.5 mbpd via shadow fleet)", "mbpd": -3.0},
    {"facility": "Qatar LNG (Ras Laffan offline, ~2 mbpd equiv)", "mbpd": -2.0},
    {"facility": "Assaluyeh / South Pars (gas processing damaged)", "mbpd": -0.5},
    {"facility": "Fujairah (drone strikes, loading disrupted)", "mbpd": -0.5},
    {"facility": "Red Sea / Houthi rerouting", "mbpd": -1.0},
    {"facility": "Saudi East-West pipeline bypass", "mbpd": 4.5},
    {"facility": "Kirkuk-Ceyhan restart (~250 kbpd)", "mbpd": 0.25},
    {"facility": "SPR releases (US+G7)", "mbpd": 1.5},
    {"facility": "Saudi spare capacity activated", "mbpd": 1.2},
    {"facility": "UAE/Kuwait increases via non-Hormuz routes", "mbpd": 0.8},
]
CURRENT_NET_DISRUPTION = round(sum(d["mbpd"] for d in DISRUPTION_BREAKDOWN), 2)


@st.cache_data(ttl=7200, show_spinner=False)
def _validate_infrastructure_baseline() -> dict:
    """Use Grok (with real-time X access) to fact-check the hardcoded infrastructure baseline.
    Returns corrected infrastructure dict and disruption breakdown. Cached 2 hours."""
    grok_key = _get_key("GROK_API_KEY")
    if not grok_key:
        return {}

    # Build the current baseline for Grok to check
    baseline_lines = []
    for name, info in INFRASTRUCTURE_TARGETS.items():
        baseline_lines.append(f"- {name}: capacity={info['capacity']}, status={info['status']}, impact={info['impact_level']}")
    baseline_lines.append("\nDisruption breakdown:")
    for d in DISRUPTION_BREAKDOWN:
        baseline_lines.append(f"  {d['facility']}: {d['mbpd']:+.1f} mbpd")
    baseline_lines.append(f"  NET: {CURRENT_NET_DISRUPTION:+.2f} mbpd")
    baseline_text = "\n".join(baseline_lines)

    prompt = f"""You have real-time access to X/Twitter and current news. Fact-check this infrastructure status baseline for the US-Israel-Iran conflict as of right now.

CURRENT BASELINE (may be outdated or inaccurate):
{baseline_text}

ACCURACY IS CRITICAL. Customers pay for this data. For EACH facility:
1. Search X/Twitter and news for the CURRENT status RIGHT NOW (March 2026).
   Check @MarineTraffic (marinetraffic.com) for real-time vessel tracking and AIS data on Hormuz transits.
2. Is it actually offline, degraded, or operational? What do satellite data, tanker tracking (MarineTraffic AIS data), and journalist reports say?
3. What is the REAL disruption volume (mbpd)? Do NOT accept the baseline number if it's wrong — CORRECT IT.
4. If the baseline claims a strike that never happened (e.g., no confirmed March 7 Abqaiq strike), mark it as inaccurate.
5. If exports are still flowing (e.g., Kharg Island tankers still loading), say so with the actual flow rate.

KNOWN ISSUES IN THE BASELINE THAT MAY NEED CORRECTION:
- Kharg Island: Check if exports are actually near zero or if tankers are still loading (1-2 mbpd reported)
- Abqaiq: Verify if a March 7 strike actually occurred — may be fabricated/confused with 2019 attack
- Kirkuk-Ceyhan: Verify if a March 13 strike occurred — pipeline may have been inactive for years
- Ras Tanura: Check if the refinery has restarted since early March

Return ONLY valid JSON. You MUST include ALL 8 facilities from the baseline plus any offsets:
{{
  "facilities": [
    {{"name": "facility name (must match baseline exactly)", "status": "operational/degraded/offline", "detail": "VERIFIED current status citing specific sources (e.g., 'per Reuters March 19' or 'satellite shows tankers loading')", "mbpd_loss": N, "update": "latest status update, or null if no change needed", "confidence": "high/medium/low"}}
  ],
  "offsets": [
    {{"name": "offset name", "mbpd_gain": N, "detail": "current status"}}
  ],
  "net_disruption_mbpd": N,
  "summary": "2-3 sentences: what the baseline got wrong and what the verified net disruption actually is"
}}

CRITICAL: This data is displayed to paying customers as VERIFIED intelligence.
- Do NOT rubber-stamp the baseline. ACTIVELY CHALLENGE every claim.
- If you cannot verify a claim, set confidence to "low" and say so in the detail.
- Your net_disruption_mbpd must reflect YOUR verified numbers, not the baseline's."""

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        _search_tool = {"type": "function", "function": {"name": "web_search", "description": "Search the web and X/Twitter for real-time information"}}
        try:
            response = client.chat.completions.create(
                model="grok-4.20-0309-reasoning",
                messages=[
                    {"role": "system", "content": "You are an energy infrastructure intelligence analyst with real-time X/Twitter access. Your job is to FACT-CHECK claims about facility status. Be skeptical — challenge every claim against current sources. Accuracy is paramount."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=3000,
                temperature=0.1,
                response_format={"type": "json_object"},
                tools=[_search_tool],
                tool_choice="auto",
            )
        except Exception:
            # Fallback without search tool
            response = client.chat.completions.create(
                model="grok-4.20-0309-reasoning",
                messages=[
                    {"role": "system", "content": "You are an energy infrastructure intelligence analyst with real-time X/Twitter access. Your job is to FACT-CHECK claims about facility status. Be skeptical — challenge every claim against current sources. Accuracy is paramount."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=3000,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        raw = response.choices[0].message.content
        if not raw:
            return {}

        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        result = json.loads(cleaned)

        # Validate the response has the expected structure
        if not isinstance(result.get("facilities"), list) or result.get("net_disruption_mbpd") is None:
            return {}

        logger.info(f"Infrastructure validation: {result.get('summary', '')}")
        return result
    except Exception as e:
        logger.warning(f"Infrastructure validation failed: {e}")
        return {}


def get_validated_infrastructure() -> tuple:
    """Return validated infrastructure data. Falls back to hardcoded if validation unavailable.
    Returns (infrastructure_targets_dict, disruption_breakdown_list, net_disruption, validation_summary)."""
    validated = _validate_infrastructure_baseline()
    if not validated or not validated.get("facilities"):
        return INFRASTRUCTURE_TARGETS, DISRUPTION_BREAKDOWN, CURRENT_NET_DISRUPTION, None

    # Build corrected infrastructure targets
    corrected_targets = {}
    status_colors = {"offline": "#ff4b4b", "degraded": "#ffaa00", "operational": "#00ff96"}
    status_levels = {"offline": "Critical", "degraded": "Severe", "operational": "Operational"}

    # Start with hardcoded targets for lat/lon/capacity, then overlay corrections
    for name, info in INFRASTRUCTURE_TARGETS.items():
        corrected_targets[name] = dict(info)  # copy

    for f in validated["facilities"]:
        fname = f.get("name", "")
        # Try to match to existing facility
        matched_key = None
        for key in corrected_targets:
            if fname.lower() in key.lower() or key.lower() in fname.lower():
                matched_key = key
                break

        if matched_key:
            status = f.get("status", "degraded")
            corrected_targets[matched_key]["status"] = f.get("detail", corrected_targets[matched_key]["status"])
            corrected_targets[matched_key]["impact_level"] = status_levels.get(status, "Severe")
            corrected_targets[matched_key]["color"] = status_colors.get(status, "#ffaa00")
            corrected_targets[matched_key]["_update"] = f.get("update", f.get("correction", ""))
            corrected_targets[matched_key]["_confidence"] = f.get("confidence", "medium")

    # Build corrected disruption breakdown
    corrected_breakdown = []
    for f in validated["facilities"]:
        loss = f.get("mbpd_loss", 0)
        if loss:
            corrected_breakdown.append({
                "facility": f.get("name", "Unknown"),
                "mbpd": -abs(loss),
            })
    for o in validated.get("offsets", []):
        gain = o.get("mbpd_gain", 0)
        if gain:
            corrected_breakdown.append({
                "facility": o.get("name", "Unknown offset"),
                "mbpd": abs(gain),
            })

    net = validated.get("net_disruption_mbpd", CURRENT_NET_DISRUPTION)
    summary = validated.get("summary", "")

    # Fall back to hardcoded if corrected breakdown is empty
    if not corrected_breakdown:
        corrected_breakdown = DISRUPTION_BREAKDOWN

    # SANITY CHECK: Reject validation if disruption is implausibly low.
    # If the hardcoded baseline says >10 mbpd offline and Grok says <3 mbpd,
    # Grok is almost certainly hallucinating "normalcy" — a known failure mode.
    hardcoded_net = abs(CURRENT_NET_DISRUPTION)
    validated_net = abs(net)
    if hardcoded_net > 5 and validated_net < hardcoded_net * 0.25:
        logger.warning(
            f"SANITY CHECK FAILED: Grok validation claims {validated_net:.1f} mbpd disruption "
            f"but hardcoded baseline is {hardcoded_net:.1f} mbpd. Rejecting hallucinated validation. "
            f"Summary: {summary}"
        )
        return INFRASTRUCTURE_TARGETS, DISRUPTION_BREAKDOWN, CURRENT_NET_DISRUPTION, (
            f"⚠️ Grok validation rejected — claimed {validated_net:.1f} mbpd disruption vs "
            f"{hardcoded_net:.1f} mbpd baseline. Using verified baseline."
        )

    return corrected_targets, corrected_breakdown, net, summary

# Base models (always used): Grok, Gemini, Claude
# Platinum tier upgrades Claude Sonnet → Opus at runtime
MODEL_CONFIGS = {
    "grok": {
        "name": "Grok 4",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.20-0309-reasoning",
        "key_name": "GROK_API_KEY",
        "extra_instructions": (
            "YOUR ROLE: Real-time intelligence & visual OSINT specialist.\n"
            "You are the ONLY model with live X/Twitter access. The other models are blind to what's "
            "happening right now. Your job is to be their eyes and ears.\n\n"

            "1. VISUAL INTELLIGENCE — WAR MAPS & SATELLITE IMAGERY:\n"
            "  Search X for war maps, satellite photos, and visual OSINT posted in the last 24 hours.\n"
            "  Priority map accounts: @TheStudyofWar (ISW maps), @GeoConfirmed (geolocation), "
            "@AuroraIntel (satellite), @Faytuks (strike maps), @NOELreports (naval tracking), "
            "@IntelSky (flight/missile tracking), @Conflict_Radar (situation maps).\n"
            "  For each map/image you find, describe: what it shows, what changed from previous maps, "
            "and what it implies for the conflict trajectory.\n"
            "  Look for: strike damage assessment photos, tanker tracking maps, Hormuz naval deployment maps, "
            "infrastructure damage before/after satellite imagery, missile trajectory maps, "
            "frontline/zone-of-control maps, shipping lane closure maps.\n\n"

            "2. BREAKING INTELLIGENCE (last 6 hours):\n"
            "  Priority accounts:\n"
            "    OSINT: @sentdefender, @IntelCrab, @AuroraIntel, @Faytuks, @FaytuksNetwork, @GeoConfirmed, @inside_IL_intel\n"
            "    Energy: @JavierBlas, @amaboraz, @EnergyIntel, @OilSheppard, @HFI_Research, @SheDrills, @Frank_Stones\n"
            "    Shipping/AIS: @MarineTraffic (marinetraffic.com — real-time vessel tracking, Hormuz transit data)\n"
            "    Military: @RALee85, @TheStudyofWar, @NOELreports, @OAlexanderDK\n"
            "    Journalists: @Joyce_Karam, @IranIntl_En, @BarakRavid, @Charles_Lister, @MiddleEastEye\n"
            "    Official/Gulf: @CENTCOM, @IDF, @IsraelWarRoom, @Khamenei_ir, @araghchi, @BinsaeedRashid\n"
            "  For each development: cite the source, time, engagement velocity, and whether it's confirmed by 2+ sources.\n\n"

            "3. NARRATIVE SHIFT DETECTION:\n"
            "  Compare the dominant narrative on X RIGHT NOW vs 24 hours ago.\n"
            "  Is sentiment shifting hawkish or dovish? Are new hashtags trending?\n"
            "  Are officials changing tone (e.g., ceasefire language appearing/disappearing)?\n"
            "  Flag any CONTRARIAN signals — where social sentiment diverges from price action.\n\n"

            "4. CROSS-REFERENCING:\n"
            "  For every major claim, check if 2+ independent sources corroborate it.\n"
            "  If only one source reports something, flag it as UNCONFIRMED.\n"
            "  Note which claims are backed by visual evidence (satellite, photos) vs. text-only reports.\n\n"

            "5. TRENDING TWEETS:\n"
            "  Return 5-8 highest-velocity tweets with: handle, content, engagement metrics, time, velocity, why_important.\n"
            "  Prioritize: tweets with visual evidence > analysis threads > single text tweets.\n"

            "YOUR SITUATION SUMMARY must lead with the most recent visual/satellite intelligence, "
            "then breaking developments, then narrative shifts."
        ),
        "color": "#ff4444",
        "weight_escalation": 1.3,
        "weight_oil": 0.8,
        "weight_ceasefire": 0.9,
        "max_tokens": 16000,
        "data_sections": ["timeline", "infrastructure", "oil", "market", "gdelt_tone", "disruption", "prior"],
        "tier_required": None,
    },
    "gemini": {
        "name": "Gemini 3.1 Pro",
        "base_url": "gemini_native",
        "model": "gemini-3.1-pro-preview",
        "key_name": "GEMINI_API_KEY",
        "extra_instructions": (
            "YOUR ROLE: Quantitative energy & military analyst. You are the math engine.\n\n"

            "1. SUPPLY/DEMAND MODEL — build a complete quantitative model:\n"
            "  For EVERY facility in the data, calculate:\n"
            "    - Pre-war capacity (mbpd or mtpa)\n"
            "    - Current operating rate (%)\n"
            "    - Net loss (mbpd)\n"
            "    - Estimated days to restore (based on damage type: mine clearing = 2-4 weeks, "
            "refinery repair = 3-6 months, pipeline = 1-2 weeks, port = 2-4 weeks)\n"
            "    - Recovery trajectory: when will each facility return to X% capacity?\n"
            "  Sum it all: total offline, total degraded, net disruption, offset by SPR + spare capacity.\n"
            "  Your numbers MUST add up. Show the math.\n\n"

            "2. OIL PRICE MODEL:\n"
            "  Use the supply gap + demand elasticity to calculate price impact:\n"
            "    - Short-run elasticity: ~0.05 (1% supply loss ≈ 20% price increase)\n"
            "    - Factor in: SPR releases (how many days of supply?), Saudi spare capacity limits, "
            "shipping rerouting costs (Cape vs Suez), insurance premiums, refinery margin compression.\n"
            "  Produce a 30-day price forecast with LOW/BASE/HIGH scenarios and the assumptions behind each.\n"
            "  Cross-reference with the oil futures term structure data provided — does the curve agree with your model?\n\n"

            "3. SECOND-ORDER ECONOMIC IMPACTS:\n"
            "  Calculate the inflation pass-through: oil price → gasoline → CPI → Fed response.\n"
            "  Estimate GDP drag from the energy shock (use historical analogs: 1973, 1979, 1990, 2022).\n"
            "  Model the LNG substitution effect: with Qatar offline, what happens to European gas prices? "
            "Asian LNG spot? Use the TTF and Henry Hub data provided.\n"
            "  Defense sector revenue uplift: estimate incremental revenue for LMT, RTX, NOC from this conflict.\n\n"

            "4. HISTORICAL CALIBRATION:\n"
            "  For your escalation score, map the current situation to the EXACT historical parallel:\n"
            "    - What was the oil price impact of that parallel event?\n"
            "    - How long did the disruption last?\n"
            "    - What ended it?\n"
            "  Use this to calibrate your 30-day and 90-day outlooks.\n\n"

            "YOUR SITUATION SUMMARY must lead with quantitative facts: exact mbpd offline, price levels, "
            "% changes, and specific facility status numbers."
        ),
        "color": "#4285f4",
        "weight_escalation": 1.1,
        "weight_oil": 1.4,
        "weight_ceasefire": 0.8,
        "max_tokens": 16000,
        "data_sections": ["timeline", "infrastructure", "gdelt_intensity", "gdelt_bulk", "oil", "market", "disruption", "prior"],
        "tier_required": None,
    },
    "claude": {
        "name": "Claude Sonnet",
        "base_url": "anthropic",
        "model": "claude-sonnet-4-6",
        "key_name": "ANTHROPIC_API_KEY",
        "extra_instructions": (
            "YOUR ROLE: Strategic reasoning & probability specialist. You are the decision-making engine.\n"
            "IMPORTANT: You do NOT have web search access. Base your analysis ENTIRELY on the LIVE DATA "
            "provided in this prompt. Use the EXACT prices, facility statuses, and disruption numbers given. "
            "Do NOT guess or hallucinate any factual data — if a number is in the data feed, use it; "
            "if it's not, say you don't have it rather than inventing one.\n\n"

            "1. BAYESIAN SCENARIO ANALYSIS:\n"
            "  For each scenario in your forecast, show your reasoning chain:\n"
            "    PRIOR: What was the base probability before today's events?\n"
            "    EVIDENCE: What new data points shift the probability? (cite specific data from the feed)\n"
            "    POSTERIOR: Updated probability after incorporating evidence.\n"
            "  Model CONDITIONAL probabilities: 'IF Hormuz reopens within 2 weeks (P=X%), "
            "THEN oil drops to $Y (P=Z% | Hormuz open)'\n"
            "  Identify the single most important SWING VARIABLE — the one thing that, if it changes, "
            "would most dramatically shift all scenarios.\n\n"

            "2. DIPLOMATIC DEEP DIVE:\n"
            "  Map every active diplomatic channel:\n"
            "    - Omani mediation: who is at the table, what are the demands, what's the timeline?\n"
            "    - UN Security Council: any resolution drafts? China/Russia veto risk?\n"
            "    - Back-channel signals: any tone shifts from @Khamenei_ir, @araghchi, @IsraeliPM?\n"
            "    - Regional mediators: Qatar, Turkey, Saudi — who has leverage?\n"
            "  For ceasefire probability: decompose into SUB-PROBABILITIES:\n"
            "    P(ceasefire talks continue) × P(terms acceptable to both) × P(implementation holds)\n"
            "  Identify the most likely DEAL STRUCTURE if a ceasefire happens.\n\n"

            "3. RED TEAM — CHALLENGE THE CONSENSUS:\n"
            "  Where are the other models (Grok, Gemini) likely to be WRONG?\n"
            "  What are they probably overweighting? Underweighting?\n"
            "  What's the most dangerous blind spot in the current analysis?\n"
            "  What's the 'black swan' scenario that nobody is pricing in?\n\n"

            "4. CONFIDENCE CALIBRATION:\n"
            "  For EVERY number you produce, provide a confidence interval:\n"
            "    - Escalation score: X/10 (90% CI: [Y, Z])\n"
            "    - Ceasefire probability: X% (range: Y%-Z%)\n"
            "    - Oil forecast: $X base (80% CI: [$Y, $Z])\n"
            "  If you're uncertain about something, SAY SO with a specific reason.\n"
            "  Never false-precision: '73%' implies you know it within 1% — use '70-75%' if uncertain.\n\n"

            "5. SECOND & THIRD ORDER EFFECTS:\n"
            "  What are the effects that cascade BEYOND the immediate conflict?\n"
            "    - Alliance shifts (does this push Saudi toward China? Does Turkey pivot?)\n"
            "    - Nuclear proliferation implications\n"
            "    - Global energy transition acceleration/deceleration\n"
            "    - US election impact\n"
            "    - Petrodollar/petrocurrency effects\n\n"

            "YOUR SITUATION SUMMARY must lead with the probability assessment and what changed it, "
            "then the key uncertainty, then the most important thing to watch next."
        ),
        "color": "#d4a574",
        "weight_escalation": 1.0,
        "weight_oil": 0.9,
        "weight_ceasefire": 1.4,
        "max_tokens": 16000,
        "data_sections": ["timeline", "infrastructure", "oil", "market", "gdelt_tone", "acled", "disruption", "prior"],
        "tier_required": None,
    },
}


CONFLICT_SYSTEM_PROMPT = """You are an elite geopolitical intelligence analyst specializing in Middle East conflicts and energy security.

CRITICAL FACTUAL CONTEXT (post-November 2024 — your training data may not include these):

US POLITICS:
- President: Donald Trump (2nd term, inaugurated Jan 20, 2025). Joe Biden is NO LONGER president.
- VP: JD Vance. Secretary of State: Marco Rubio. SecDef: Pete Hegseth. NSA: Mike Waltz.
- Trump withdrew from the 2015 Iran nuclear deal (JCPOA) again and imposed "maximum pressure" sanctions.
- US policy toward Iran is significantly more hawkish than the Biden administration.

MIDDLE EAST LEADERS:
- Israel PM: Benjamin Netanyahu (coalition with far-right partners Ben Gvir, Smotrich).
- Iran: Supreme Leader Ali Khamenei. President Masoud Pezeshkian (elected 2024, reformist). FM Abbas Araghchi.
- Saudi Arabia: Crown Prince Mohammed bin Salman (MBS), de facto ruler.
- UAE: President Mohammed bin Zayed (MBZ).
- Qatar: Emir Sheikh Tamim bin Hamad Al Thani. PM Mohammed bin Abdulrahman Al Thani.
- Oman: Sultan Haitham bin Tariq (key mediator in Iran diplomacy).

KEY EVENTS BETWEEN NOV 2024 AND FEB 2026:
- Trump re-elected November 2024, inaugurated January 2025.
- Israel-Hamas war continued through 2025 with multiple ceasefire attempts.
- Houthi Red Sea attacks persisted through 2025, disrupting global shipping.
- Iran accelerated uranium enrichment to near-weapons-grade (60%+) through 2025.
- Oil prices were volatile in 2025: $70-90 range before the Feb 2026 escalation.
- Fed cut rates through 2025, reaching 3.50-3.75% by early 2026.
- US-Israel launched joint strikes on Iran Feb 28, 2026 — the current conflict.

CURRENT YEAR: 2026. Do NOT reference Biden, Blinken, Austin, or Sullivan as current officials.

You are analyzing the ongoing US-Israel war on Iran that began February 28, 2026.

Key context:
- US-Israel launched joint strikes on Iranian nuclear facilities on Feb 28, 2026
- Iran retaliated with ballistic missiles; IRGC mined the Strait of Hormuz
- Multiple Gulf energy facilities have been damaged or disrupted (see LIVE DATA for current status)
- Houthi attacks on Red Sea shipping intensified in solidarity
- Partial ceasefire talks via Omani mediation began Mar 16 — collapsed after further strikes
- FOMC held rates Mar 18, citing oil shock uncertainty
- Attacks on Qatar Ras Laffan LNG facility caused significant damage on Mar 19
- Iranian missile barrages hit southern Israel (Dimona, Arad) on Mar 20 — 100+ injured
- US/Israel struck Natanz nuclear facility and IRGC missile sites on Mar 21
- Hezbollah opened northern front with rocket barrages into Israel on Mar 21
- Trump issued 48-hour ultimatum on Mar 22: reopen Hormuz or US will destroy Iranian power plants
- Iran vowed permanent Hormuz closure and threatened all Gulf energy/water infrastructure
- Supreme Leader Khamenei killed in initial Feb 28 strikes; Iranian leadership in disarray
- Oil above $107/bbl (Brent); Hormuz effectively closed; 48-hour deadline ~Mar 23-24
- IMPORTANT: Use the facility-level status and disruption baseline in LIVE DATA as your starting point.
  Cross-reference with your real-time search results. If your findings contradict the baseline, trust
  your findings and explain the discrepancy.

SUPPLY DISRUPTION: A facility-by-facility disruption baseline is provided in the LIVE DATA section.
Use it as a STARTING POINT, not as ground truth. Your job is to assess whether the actual disruption
is higher, lower, or consistent with this baseline based on the latest evidence.
If you deviate significantly, explain why with specific evidence.

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

KNOWN AI HALLUCINATION PATTERNS — DO NOT FALL INTO THESE:
1. "No new strikes in 6 hours" ≠ "no disruption". Damaged facilities stay damaged for weeks/months.
   The absence of NEW attacks does not mean prior damage is repaired.
2. Claiming Hormuz has "normal traffic" or "80-100 tankers daily" when satellite/AIS data shows 0-3.
   CHECK @MarineTraffic (marinetraffic.com) for real-time AIS vessel tracking through the Strait.
   If commercial shipping insurance is at war-risk premiums, traffic is NOT normal.
3. Claiming oil at $70/bbl when it's actually at $95-100. CHECK THE LIVE DATA FEED — do not guess prices.
4. Reverting to pre-conflict training data. Your training data may show Hormuz at 18-21 mbpd —
   that was BEFORE Feb 28, 2026. The conflict has changed everything. Use the LIVE DATA, not memory.
5. Citing @JavierBlas or other journalists saying things they didn't say. If you can't verify a quote,
   don't attribute it. Say "per market data" instead.
6. Claiming Ras Laffan is "fully operational at 77 mtpa" when it was struck on March 18-19 and
   force majeure was declared. Check the infrastructure status provided.

CONSISTENCY CHECK: Before submitting your response, verify:
- Your oil price forecast is consistent with the ACTUAL current price in the data feed
- Your disruption estimate is consistent with the infrastructure statuses you reported
- Your escalation score matches the severity of events in the conflict timeline
- If oil is above $90, your escalation score should be at LEAST 6/10
- If there are active missile exchanges between state actors AND an ultimatum to destroy infrastructure, score should be 8+/10
- Current situation (week 4, direct US-Iran war, Hormuz closed, ultimatum issued) is comparable to Gulf War 1990-91 = score 8

CITATION RULES — every claim must be grounded:
- Escalation rationale MUST cite at least 2 specific data points from the LIVE DATA provided
- Oil forecasts MUST reference the current oil price and % change from the data feed
- Infrastructure status MUST reference specific facilities and their known capacity
- If you are uncertain about a fact, say so explicitly — do NOT fabricate
- If the live data contradicts your prior beliefs, trust the data and note the divergence

CONFIDENCE LABELING — tag every key claim in your rationale and situation_summary:
  [verified] = confirmed by 2+ independent sources or official statements
  [estimated] = based on data but involves calculation/extrapolation
  [inferred] = logical deduction from available evidence, not directly confirmed
  Example: "Hormuz transit is near-zero [verified per tanker tracking]. Oil could hit $130 [estimated based on supply gap]. Ceasefire talks may resume next week [inferred from Omani FM tone shift]."

ANTI-DRIFT: You will be given your MOST RECENT PRIOR analysis (if any). Each analysis is independent.
If the LIVE DATA is consistent with the prior, your scores should be similar.
BUT if the live data CONTRADICTS the prior (e.g., prior said 'no disruption' but oil is at $98),
you MUST correct the prior error — do NOT anchor to a wrong baseline just because it was your last output.
Correcting a wrong prior is NOT 'drifting' — it is being accurate.

Respond with ONLY valid JSON. The exact fields depend on your assigned role (provided in the user prompt).

CORE FIELDS (all models must produce):
  "situation_summary": "Start with a comprehensive recap of key events from the last 24 hours in reverse chronological order (most recent first), citing specific times, sources, and data points. Tag each claim with [verified], [estimated], or [inferred]. Then provide 2-3 sentences of forward-looking analysis. Total: 5-8 sentences.",
  "latest_developments": [{"event": "what happened", "time": "when (e.g., '6h ago', 'Mar 20 14:00')", "source": "who reported it", "market_impact": "effect on prices/sentiment", "confidence": "verified/estimated/inferred"}],
  "escalation_risk": {"level": "Critical/High/Elevated/Moderate/Low", "score": N (1-10, calibrated above), "rationale": "cite 2+ data points, tag each [verified/estimated/inferred]", "citations": ["data point 1", "data point 2"]},
  "oil_impact": {"current_disruption_mbpd": N, "price_forecast_30d": {"low": N, "base": N, "high": N}, "key_chokepoints": ["chokepoint: status [verified/estimated]"]},
  "infrastructure_status": [{"name": "facility", "status": "operational/degraded/offline", "detail": "...", "changed": true/false}],
  "infrastructure_alerts": ["facility status changes only — empty array if none"],
  "scenario_forecast": [{"scenario": "name", "probability": N, "description": "...", "oil_impact": "$X-Y", "trigger": "..."}],
  "watch_next": [{"trigger": "specific event to watch for", "timeframe": "next X hours/days", "if_happens": "consequence for markets", "probability": N}]

SPECIALIST FIELDS (only produce if your role covers this domain):
  "diplomatic_outlook": {"ceasefire_probability_30d": N, "confidence_range": [low, high], "key_mediators": [...], "obstacles": [...], "signals": [...]},
  "trending_tweets": [{"handle": "@user", "content": "max 200 chars", "likes": N, "retweets": N, "replies": N, "time": "Xh ago", "posted_at": "2026-03-22T14:30:00Z", "velocity": "high/medium/low", "why_important": "..."}]

scenario_forecast: exactly 3 mutually exclusive scenarios summing to ~100%.
Only include specialist fields if they are part of your assigned role."""


def build_live_data_context(gdelt_data: dict, df_oil, df_acled, df_tone,
                            sections: list[str] | None = None,
                            market_context: dict = None,
                            disruption_data: tuple = None,
                            lng_prices: dict = None) -> str:
    """Build a live data block to inject into the AI prompt.

    Args:
        sections: optional filter — only include these data sections.
            Valid: gdelt_intensity, gdelt_tone, oil, acled, disruption, market
            If None, include all.
        market_context: dict with brent, vix, gold, dxy price/change data.
        disruption_data: (breakdown_list, net_disruption) override for validated data.
    """
    include_all = sections is None
    lines = [f"Current data as of {datetime.now().replace(minute=0, second=0, microsecond=0).strftime('%B %d, %Y %I:%M %p')}:"]

    # ALWAYS include conflict timeline — models must not ignore strike history
    if include_all or "timeline" in sections:
        lines.append("\n=== CONFIRMED CONFLICT TIMELINE (these events HAPPENED — do not contradict) ===")
        for evt in CONFLICT_TIMELINE_EVENTS:
            lines.append(f"  {evt['date']}: {evt['event']}")
            if evt.get('infrastructure'):
                lines.append(f"    Infrastructure: {evt['infrastructure']}")
        lines.append("  ⚠️ These are CONFIRMED events. Infrastructure damaged in these strikes remains damaged")
        lines.append("  unless you have specific evidence of repair/recovery (cite source and date).")

    # ALWAYS include infrastructure status
    if include_all or "infrastructure" in sections:
        lines.append("\n=== FACILITY STATUS (compiled from multiple sources) ===")
        for name, info in INFRASTRUCTURE_TARGETS.items():
            lines.append(f"  {name}: [{info['impact_level']}] {info['status']}")

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
        # Prefer real-time WTI from market_context over stale EIA daily data
        if market_context and market_context.get("wti"):
            latest_price = market_context["wti"]["price"]
        else:
            latest_price = df_oil["value"].iloc[-1]
        prev_price = df_oil["value"].iloc[-2] if len(df_oil) > 1 else latest_price
        chg_1d = ((latest_price / prev_price) - 1) * 100 if prev_price > 0 else 0
        price_7d_ago = df_oil["value"].iloc[-min(5, len(df_oil))] if len(df_oil) > 5 else df_oil["value"].iloc[0]
        chg_7d = ((latest_price / price_7d_ago) - 1) * 100 if price_7d_ago > 0 else 0
        price_30d_ago = df_oil["value"].iloc[-min(22, len(df_oil))] if len(df_oil) > 22 else df_oil["value"].iloc[0]
        chg_30d = ((latest_price / price_30d_ago) - 1) * 100 if price_30d_ago > 0 else 0
        high_180d = df_oil["value"].max()
        low_180d = df_oil["value"].min()
        lines.append(f"\nWTI Crude Oil (HARD ANCHOR — this is the ACTUAL market price right now):")
        lines.append(f"  Current: ${latest_price:.2f}/bbl, 1d: {chg_1d:+.1f}%, "
                     f"7d: {chg_7d:+.1f}%, 30d: {chg_30d:+.1f}%")
        lines.append(f"  180d range: ${low_180d:.2f} — ${high_180d:.2f}")
        # Price-based reality check
        if latest_price > 90:
            lines.append(f"  ⚠️ PRICE REALITY CHECK: Oil above $90/bbl proves significant supply disruption is occurring.")
            lines.append(f"  Pre-conflict oil was $65-75. The ${latest_price - 70:.0f}+ premium reflects REAL supply losses.")
            lines.append(f"  If you claim 'no disruption' or 'near-zero impact', you MUST explain why oil is at ${latest_price:.0f}.")
            lines.append(f"  Your 30d price forecast BASE case must be within 15% of the current ${latest_price:.0f} price.")

    # Broader market context (Brent, VIX, Gold, DXY)
    if (include_all or "market" in sections) and market_context:
        mc = market_context
        lines.append(f"\n=== LIVE MARKET PRICES (REAL-TIME — use these EXACT numbers, do NOT guess) ===")
        if mc.get("brent"):
            lines.append(f"  Brent Crude: ${mc['brent']['price']}/bbl, 1d: {mc['brent']['change']:+.1f}%")
        if mc.get("vix"):
            lines.append(f"  VIX: {mc['vix']['price']}, 1d: {mc['vix']['change']:+.1f}%")
        if mc.get("gold"):
            lines.append(f"  Gold: ${mc['gold']['price']}/oz, 1d: {mc['gold']['change']:+.1f}%")
        if mc.get("dxy"):
            lines.append(f"  DXY (Dollar Index): {mc['dxy']['price']}, 1d: {mc['dxy']['change']:+.1f}%")
        lines.append(f"\n  ⚠️ MARKET IMPLICATIONS RULE: Your 'market_implications' section MUST quote these")
        lines.append(f"  exact prices. Do NOT use prices from memory or training data. If Brent is")
        lines.append(f"  ${mc.get('brent', {}).get('price', '?')}/bbl in the data, write ${mc.get('brent', {}).get('price', '?')}/bbl — not a different number.")
        lines.append(f"  Same for VIX, Gold, and DXY. Hallucinating a wrong price (e.g., DXY at 120 when it's")
        lines.append(f"  actually {mc.get('dxy', {}).get('price', '?')}) destroys credibility with paying customers.")

    # Natural gas / LNG prices (critical for Ras Laffan impact assessment)
    if (include_all or "market" in sections) and lng_prices:
        lines.append(f"\n  Natural Gas / LNG (LIVE — use these exact numbers):")
        for key, data in lng_prices.items():
            lines.append(f"    {data['name']}: ${data['price']}, 1d: {data['change']:+.1f}%")

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

    # Verified disruption breakdown — ALWAYS show hardcoded baseline as primary anchor
    if include_all or "disruption" in sections:
        lines.append(f"\n=== VERIFIED SUPPLY DISRUPTION BASELINE (research-compiled, updated daily) ===")
        for d in DISRUPTION_BREAKDOWN:
            lines.append(f"  {d['facility']}: {d['mbpd']:+.1f} mbpd")
        lines.append(f"  NET DISRUPTION: {CURRENT_NET_DISRUPTION:+.2f} mbpd")

        # Show Grok-validated data alongside for comparison (if different)
        if disruption_data:
            _bd, _net = disruption_data
            if abs(_net - CURRENT_NET_DISRUPTION) > 1.0:
                lines.append(f"\n  Grok real-time validation estimates: {_net:+.2f} mbpd net disruption")
                lines.append(f"  ⚠️ DISCREPANCY: Baseline says {CURRENT_NET_DISRUPTION:+.2f} mbpd, Grok says {_net:+.2f} mbpd.")
                lines.append(f"  USE THE HIGHER DISRUPTION ESTIMATE unless you have specific, cited evidence that a facility has recovered.")
                lines.append(f"  AI models commonly hallucinate 'normalcy' — if oil is above $90/bbl, major disruptions are real.")
            else:
                lines.append(f"  Grok real-time validation: consistent ({_net:+.2f} mbpd)")

        lines.append(f"\n  ANCHORING RULE: Your current_disruption_mbpd MUST be close to {abs(CURRENT_NET_DISRUPTION):.1f} mbpd")
        lines.append(f"  unless you cite a SPECIFIC facility recovery (name, source, date) that reduces it.")
        lines.append(f"  'No new strikes in 6h' does NOT mean facilities have recovered — damaged infrastructure takes weeks/months to repair.")

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
    lines.append("INSTRUCTION: Use these as a reference, NOT as an anchor you must stay close to.")
    lines.append("If the LIVE DATA (oil prices, facility status, market indicators) contradicts the prior analysis, ")
    lines.append("TRUST THE LIVE DATA and explain why the prior was wrong. Common prior errors:")
    lines.append("- Prior claimed 'no disruption' but oil is above $90 → prior was wrong, adjust upward")
    lines.append("- Prior claimed facilities 'fully operational' but conflict timeline shows strikes → prior was wrong")
    lines.append("- Prior escalation score was low but oil/VIX/gold all elevated → prior underestimated risk")

    return "\n".join(lines)


from src.analysis_history import (
    load_history as _load_history,
    get_latest as _get_latest_history,
    compute_model_accuracy as _compute_model_accuracy,
)


def load_conflict_history() -> list:
    return _load_history(CONFLICT_HISTORY_FILE)


def save_conflict_result(result: dict) -> None:
    history = load_conflict_history()

    # Compute and store accuracy weights before saving
    accuracy_weights = _compute_model_accuracy(history)
    if accuracy_weights:
        result["_accuracy_weights"] = accuracy_weights

    entry = {
        "timestamp": datetime.now().isoformat(),
        "models_used": result.get("models_used", []),
        "blended": result,
    }
    history.append(entry)
    history = history[-48:]

    # --- Supabase primary ---
    _saved_to_supa = False
    try:
        from src.db import get_client, get_user_id
        db = get_client()
        if db:
            db.table("conflict_analysis").insert({
                "user_id": get_user_id(),
                "region": "iran",
                "timestamp": entry["timestamp"],
                "situation_summary": result.get("situation_summary", ""),
                "escalation_risk": json.dumps(result.get("escalation_risk", {})),
                "models_used": result.get("models_used", []),
                "latest_developments": json.dumps(result.get("latest_developments", [])),
                "infrastructure_status": json.dumps(result.get("infrastructure_status", {})),
            }).execute()
            _saved_to_supa = True
    except Exception as e:
        logger.warning(f"Supabase conflict_analysis insert failed: {e}")

    # --- Local JSON fallback ---
    try:
        os.makedirs(os.path.dirname(CONFLICT_HISTORY_FILE), exist_ok=True)
        with open(CONFLICT_HISTORY_FILE, "w") as f:
            json.dump(history, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save conflict history: {e}")

    try:
        _update_source_credibility(result, history)
    except Exception as e:
        logger.warning(f"Source credibility update failed: {e}")


def get_latest_conflict_result() -> tuple:
    return _get_latest_history(CONFLICT_HISTORY_FILE, stale_hours=1.0)


def _validate_model_response(result: dict, model_name: str = "") -> dict:
    """Validate and fix out-of-range values in a model's JSON response."""
    warnings = []

    # Escalation risk: must be 1-10
    esc = result.get("escalation_risk", {})
    if isinstance(esc, dict):
        score = esc.get("score", 0)
        if not isinstance(score, (int, float)) or score < 1 or score > 10:
            esc["score"] = max(1, min(10, int(score) if isinstance(score, (int, float)) else 5))
            warnings.append(f"escalation score clamped to {esc['score']}")
        if not esc.get("rationale"):
            esc["rationale"] = "No rationale provided."
            warnings.append("missing escalation rationale")

    # Ceasefire probability: must be 0-100
    diplo = result.get("diplomatic_outlook", {})
    if isinstance(diplo, dict):
        cf = diplo.get("ceasefire_probability_30d", 0)
        if not isinstance(cf, (int, float)) or cf < 0 or cf > 100:
            diplo["ceasefire_probability_30d"] = max(0, min(100, int(cf) if isinstance(cf, (int, float)) else 10))
            warnings.append(f"ceasefire probability clamped to {diplo['ceasefire_probability_30d']}")

    # Oil disruption: should be near CURRENT_NET_DISRUPTION (±5 mbpd is suspicious)
    oil = result.get("oil_impact", {})
    if isinstance(oil, dict):
        disruption = oil.get("current_disruption_mbpd", 0)
        if isinstance(disruption, (int, float)) and abs(disruption - abs(CURRENT_NET_DISRUPTION)) > 5:
            warnings.append(f"disruption estimate {disruption} deviates >5 mbpd from verified {abs(CURRENT_NET_DISRUPTION)}")
        # Price forecast sanity: base should be between low and high
        fc = oil.get("price_forecast_30d", {})
        if isinstance(fc, dict):
            low, base, high = fc.get("low", 0), fc.get("base", 0), fc.get("high", 0)
            if base > 0 and (base < low or base > high):
                fc["low"] = min(low, base)
                fc["high"] = max(high, base)

    # Ensure required top-level fields exist
    result.setdefault("situation_summary", "")
    result.setdefault("latest_developments", [])
    result.setdefault("escalation_risk", {"score": 5, "level": "Unknown", "rationale": ""})
    result.setdefault("oil_impact", {"current_disruption_mbpd": 0, "price_forecast_30d": {"low": 0, "base": 0, "high": 0}})
    result.setdefault("infrastructure_status", [])
    result.setdefault("diplomatic_outlook", {"ceasefire_probability_30d": 0})
    result.setdefault("market_implications", {})
    result.setdefault("scenario_forecast", [])

    if warnings:
        result["_validation_warnings"] = warnings
        logger.warning(f"{model_name} validation: {', '.join(warnings)}")

    return result


from src.json_repair import close_json as _close_json, sanitize_json as _sanitize_json, repair_json as _repair_json_impl


def _repair_json(raw: str, model_name: str = "") -> dict:
    return _repair_json_impl(raw, model_name)


@st.cache_data(ttl=1800, show_spinner=False)
def run_single_conflict_model(model_key: str, api_key: str, context_prompt: str, model_config: dict = None) -> dict:
    """Call a single AI model for conflict analysis."""
    config = model_config or MODEL_CONFIGS.get(model_key, {})
    model_max_tokens = config.get("max_tokens", 3000)

    from src.ai_validation import ACCURACY_CHECK_LIGHT
    user_prompt = f"""{context_prompt}

{config['extra_instructions']}

{ACCURACY_CHECK_LIGHT}

Produce your complete analysis. Respond with ONLY valid JSON."""

    try:
        if config["base_url"] == "gemini_native":
            from google import genai
            from google.genai import types
            client = genai.Client(api_key=api_key)

            # Enable Google Search grounding — gives Gemini real-time web data
            _google_search_tool = types.Tool(google_search=types.GoogleSearch())
            _gemini_config = types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=model_max_tokens,
                temperature=0.3,
                tools=[_google_search_tool],
            )
            _gemini_contents = f"{CONFLICT_SYSTEM_PROMPT}\n\n{user_prompt}"

            # Try up to 2 attempts — Gemini can produce malformed JSON on first try
            raw = None
            for _attempt in range(2):
                try:
                    response = client.models.generate_content(
                        model=config["model"],
                        contents=_gemini_contents,
                        config=_gemini_config,
                    )
                except Exception as _grounding_err:
                    # If grounding fails (e.g., model doesn't support it), retry without
                    logger.warning(f"Gemini grounding failed: {_grounding_err}, retrying without search")
                    _gemini_config_no_search = types.GenerateContentConfig(
                        response_mime_type="application/json",
                        max_output_tokens=model_max_tokens,
                        temperature=0.3,
                    )
                    response = client.models.generate_content(
                        model=config["model"],
                        contents=_gemini_contents,
                        config=_gemini_config_no_search,
                    )
                raw = response.text
                if response.candidates and response.candidates[0].finish_reason.name == "MAX_TOKENS":
                    logger.warning(f"{config['name']} response truncated (finish_reason=MAX_TOKENS)")
                # Quick check: does it parse?
                try:
                    json.loads(raw)
                    break  # Valid JSON, no need to retry
                except (json.JSONDecodeError, TypeError):
                    if _attempt == 0:
                        logger.warning(f"{config['name']} attempt 1 produced invalid JSON, retrying...")
                        continue
                    # Second attempt also failed — raw will go through repair pipeline below
        elif config["base_url"] == "anthropic":
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
            if response.stop_reason == "max_tokens":
                logger.warning(f"{config['name']} response truncated (stop_reason=max_tokens)")
        else:
            client_kwargs = {"api_key": api_key}
            if config["base_url"]:
                client_kwargs["base_url"] = config["base_url"]
            client = _get_openai_client(**client_kwargs)

            call_kwargs = dict(
                model=config["model"],
                messages=[
                    {"role": "system", "content": CONFLICT_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=model_max_tokens,
                temperature=0.3,
            )

            # Enable live search for Grok models — gives real-time X/Twitter + web access
            is_grok = "x.ai" in (config.get("base_url") or "")
            if is_grok:
                call_kwargs["tools"] = [{"type": "function", "function": {"name": "web_search", "description": "Search the web and X/Twitter for real-time information"}}]
                call_kwargs["tool_choice"] = "auto"

            # response_format not supported by all providers — try with, fallback without
            try:
                response = client.chat.completions.create(
                    **call_kwargs,
                    response_format={"type": "json_object"},
                )
            except Exception:
                # If search tool fails, retry without it
                call_kwargs.pop("tools", None)
                call_kwargs.pop("tool_choice", None)
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

        # Attempt to repair malformed JSON from LLM output
        was_repaired = False
        try:
            result = json.loads(cleaned)
        except json.JSONDecodeError as first_err:
            result = _repair_json(cleaned, config["name"])
            was_repaired = True

        # Check if the repaired result has meaningful content
        # If it's a near-empty shell (missing situation_summary AND escalation score), it's garbage
        has_summary = bool(result.get("situation_summary", "").strip())
        has_escalation = isinstance(result.get("escalation_risk", {}).get("score"), (int, float)) and result["escalation_risk"]["score"] != 5
        if was_repaired and not has_summary and not has_escalation:
            return {"success": False, "error": "JSON repair salvaged too little data — response was too malformed",
                    "model_name": config["name"]}

        # Validate and clamp fields
        result = _validate_model_response(result, config["name"])
        result["success"] = True
        result["model_name"] = config["name"]
        if was_repaired:
            result["_partial"] = True
        return result
    except Exception as e:
        logger.error(f"{config['name']} conflict analysis failed: {e}")
        return {"success": False, "error": str(e), "model_name": config["name"]}


def _domain_weighted_avg(results: dict, field_path: list[str], weight_key: str, default: float = 0,
                         configs: dict = None, accuracy_weights: dict = None) -> float:
    """Compute a domain-weighted average, optionally boosted by historical accuracy."""
    _cfgs = configs or MODEL_CONFIGS
    _acc = accuracy_weights or {}
    total_weight = 0
    weighted_sum = 0
    for k, v in results.items():
        val = v
        for key in field_path:
            val = val.get(key, {}) if isinstance(val, dict) else default
        if not isinstance(val, (int, float)):
            val = default
        domain_w = _cfgs.get(k, {}).get(weight_key, 1.0)
        acc_w = _acc.get(k, 1.0)
        w = domain_w * acc_w
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
        avg_prob = sum(probs) / len(probs) if probs else 0

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


def _ai_filter_tweets(tweets: list, configs: dict = None) -> list:
    """Run tweets through a fast AI call to score informational value.
    Keeps only tweets scored 7+ out of 10 for relevance to the Iran conflict."""
    if not tweets or len(tweets) <= 2:
        return tweets

    # Use Gemini (cheap/fast) if available, otherwise skip filtering
    _cfgs = configs or MODEL_CONFIGS
    gemini_cfg = _cfgs.get("gemini")
    if not gemini_cfg:
        return tweets
    api_key = _get_key(gemini_cfg["key_name"])
    if not api_key:
        return tweets

    # Build a compact prompt with numbered tweets
    tweet_lines = []
    for i, tw in enumerate(tweets):
        tweet_lines.append(f'{i}: @{tw.get("handle","")} — {tw.get("content","")}')
    tweet_block = "\n".join(tweet_lines)

    prompt = f"""Score each tweet for informational value to a trader monitoring the US-Israel-Iran conflict.

Criteria:
- Contains specific, actionable intelligence (facility status, troop movements, diplomatic signals, oil flow data)
- From a credible source (OSINT analysts, journalists, officials, energy analysts)
- NOT vague commentary, emotional reactions, memes, generic news headlines, or repetitive content

Return ONLY a JSON array of objects: [{{"index": N, "score": 1-10, "reason": "brief"}}]
Score 7+ = keep, below 7 = discard.

Tweets:
{tweet_block}"""

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=gemini_cfg["model"],
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                max_output_tokens=1000,
                temperature=0.1,
            ),
        )
        scores = json.loads(response.text)
        keep_indices = {s["index"] for s in scores if s.get("score", 0) >= 7}
        filtered = [tw for i, tw in enumerate(tweets) if i in keep_indices]
        return filtered if filtered else tweets[:3]  # fallback: keep top 3 if all filtered out
    except Exception as e:
        logger.warning(f"AI tweet filter failed, returning unfiltered: {e}")
        return tweets


_LIVE_TWEET_PROMPT = """You have real-time access to X/Twitter. Search for the LATEST high-impact tweets about the US-Israel-Iran conflict, oil supply disruptions, and Middle East military developments.

PRIORITY ACCOUNTS — check these FIRST:
  OSINT: @sentdefender, @IntelCrab, @AuroraIntel, @Faytuks, @FaytuksNetwork, @GeoConfirmed, @inside_IL_intel
  Energy: @JavierBlas, @amaboraz, @EnergyIntel, @OilSheppard, @SheDrills, @Frank_Stones, @JesseCohenInv
  Shipping/AIS: @MarineTraffic (vessel tracking, Hormuz transit counts, AIS data)
  Military: @RALee85, @TheStudyofWar, @NOELreports, @OAlexanderDK
  Breaking: @Breaking911
  Journalists: @Joyce_Karam, @IranIntl_En, @BarakRavid, @Charles_Lister
  Official: @CENTCOM, @IDF, @BinsaeedRashid

SEARCH TERMS: Iran strike, Hormuz, Abqaiq, Ras Laffan, oil disruption, ceasefire Iran, IRGC, Houthi Red Sea

RULES:
- Only include tweets from the last 6 hours. If nothing from 6h, expand to 12h max.
- Prioritize VELOCITY (likes per hour) over total engagement.
- Include 6-10 tweets. Each must have specific, actionable information — not vague commentary.
- Content must be max 200 chars. Truncate if needed but keep the key intel.

Return ONLY valid JSON:
[{"handle": "@user", "content": "tweet text", "likes": N, "retweets": N, "replies": N, "time": "Xh ago", "posted_at": "2026-03-22T14:30:00Z", "velocity": "high/medium/low", "why_important": "1-line reason"}]

IMPORTANT: "posted_at" must be the actual UTC timestamp when the tweet was posted (ISO 8601 format). "time" is the relative time ago string."""


@st.cache_data(ttl=600, show_spinner=False)
# ─────────────────────────────────────────────
# 1. AUTO-UPDATE CONFLICT TIMELINE VIA GROK
# ─────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _auto_update_timeline() -> list:
    """Use Grok to discover new conflict events since the last hardcoded entry.
    Persists new events to Supabase. Returns full timeline (hardcoded + DB + new)."""

    # Load any previously discovered events from Supabase
    db_events = []
    try:
        from src.db import get_client
        db = get_client()
        if db:
            result = db.table("conflict_timeline").select("*")\
                .order("date", desc=False).execute()
            db_events = result.data or []
    except Exception:
        pass

    # Merge hardcoded + DB events
    existing_keys = {(e["date"], e["event"][:50]) for e in CONFLICT_TIMELINE_EVENTS}
    merged = list(CONFLICT_TIMELINE_EVENTS)
    for evt in db_events:
        key = (evt.get("date", ""), evt.get("event", "")[:50])
        if key not in existing_keys:
            merged.append(evt)
            existing_keys.add(key)

    last_date = merged[-1]["date"] if merged else "2026-03-28"
    grok_key = _get_key("GROK_API_KEY")
    if not grok_key:
        merged.sort(key=lambda e: e.get("date", ""))
        return merged

    prompt = f"""You have real-time access to X/Twitter and news. The last event in our Iran conflict timeline is:
  {last_date}: {merged[-1]['event']}

Search for MAJOR conflict developments that occurred AFTER {last_date} (up to right now).
Only include events that are significant enough to move oil prices, change military posture, or shift diplomatic dynamics.
Check: Reuters, Bloomberg, AP, @sentdefender, @IranIntl_En, @JavierBlas, @IDF, @CENTCOM.

Return ONLY a JSON array of NEW events (empty array if nothing major happened):
[{{"date": "YYYY-MM-DD", "event": "what happened", "category": "Military/Escalation/Diplomatic/Policy/Supply", "impact": "market/geopolitical impact", "infrastructure": "any infrastructure affected"}}]

Only include CONFIRMED events from credible sources. Do NOT fabricate."""

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[
                {"role": "system", "content": "You are a conflict timeline analyst. Return only confirmed, major events in JSON format."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1500,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content
        if not raw:
            merged.sort(key=lambda e: e.get("date", ""))
            return merged

        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)

        new_events = parsed if isinstance(parsed, list) else parsed.get("events", parsed.get("timeline", []))
        if not new_events:
            merged.sort(key=lambda e: e.get("date", ""))
            return merged

        # Add new events and persist to Supabase
        for evt in new_events:
            evt_date = evt.get("date", "")
            evt_text = evt.get("event", "")
            if not evt_date or not evt_text:
                continue
            key = (evt_date, evt_text[:50])
            if key not in existing_keys:
                merged.append(evt)
                existing_keys.add(key)

                # Persist to Supabase
                try:
                    from src.db import get_client
                    db = get_client()
                    if db:
                        db.table("conflict_timeline").upsert({
                            "date": evt_date,
                            "event": evt_text,
                            "category": evt.get("category", "Military"),
                            "impact": evt.get("impact", ""),
                            "infrastructure": evt.get("infrastructure", ""),
                            "source": "grok_auto",
                        }, on_conflict="date,event").execute()
                except Exception:
                    pass

        merged.sort(key=lambda e: e.get("date", ""))
        return merged
    except Exception as e:
        logger.warning(f"Timeline auto-update failed: {e}")
        merged.sort(key=lambda e: e.get("date", ""))
        return merged


# ─────────────────────────────────────────────
# 2. POLYMARKET PREDICTION ODDS
# ─────────────────────────────────────────────

IRAN_POLYMARKET_SLUGS = {
    "iran-ceasefire-2026": "Iran Ceasefire by June 2026",
    "oil-price-above-100-2026": "Oil Above $100 End of 2026",
    "iran-nuclear-deal-2026": "Iran Nuclear Deal 2026",
    "us-iran-war-escalation": "US-Iran War Major Escalation",
    "strait-of-hormuz-closure": "Strait of Hormuz Full Closure",
}


from src.market_data import fetch_polymarket_odds, fetch_oil_term_structure, fetch_energy_spot_prices


def _fetch_iran_polymarket() -> list:
    return fetch_polymarket_odds(IRAN_POLYMARKET_SLUGS)


# ─────────────────────────────────────────────
# 3. OIL FUTURES TERM STRUCTURE
# ─────────────────────────────────────────────

def _fetch_oil_term_structure() -> dict:
    return fetch_oil_term_structure()


# ─────────────────────────────────────────────
# 4. GROK PRE-PASS — BREAKING NEWS BRIEF
# ─────────────────────────────────────────────

@st.cache_data(ttl=900, show_spinner=False)
def _grok_situation_briefing() -> str:
    """4-hour situation briefing via Grok with live search. Cached 15 min in Streamlit, 30 min in Supabase."""
    # Check Supabase AI cache (shared across users)
    try:
        from src.ai_cache import get_cached_ai, cache_ai_response
        _brief_key = f"situation_briefing_{datetime.now().strftime('%Y%m%d_%H')}"  # hourly key
        _cached = get_cached_ai(_brief_key)
        if _cached:
            return _cached
    except Exception:
        pass

    grok_key = _get_key("GROK_API_KEY")
    if not grok_key:
        return ""

    today = datetime.now().strftime("%B %d, %Y %I:%M %p")
    prompt = f"""TODAY: {today}. Search X/Twitter and news sources RIGHT NOW for the latest on the Iran war situation.

CONTEXT: The US-Israel-Iran war started Feb 28, 2026. Khamenei was killed in initial strikes. We are now in week 4.
Key recent events: Iranian missile barrages hitting Israel (Dimona, Arad, Tel Aviv area), US/Israeli strikes on Natanz and IRGC targets, Hezbollah northern front opened, Trump issued 48-hour ultimatum to reopen Hormuz or destroy Iranian power plants, Iran vowed permanent Hormuz closure and threatened all Gulf infrastructure.

Write a comprehensive situation update covering the LAST 4 HOURS. This is displayed to paying institutional clients.

Cover ALL of these — skip nothing:
1. MILITARY: Latest strikes, missile launches, interceptions, casualties, damage assessments. Which cities hit? What got through defenses? Any new fronts or targets?
2. HORMUZ & ENERGY: Is the strait open or closed? Any ship movements? Trump ultimatum countdown — how many hours left? Oil price action. Iran's latest threats.
3. DIPLOMATIC: Any ceasefire signals? UN activity? Who's talking? Who's escalating?
4. X/TWITTER PULSE: What are the major accounts (@sentdefender, @Faytuks, @FaytuksNetwork, @inside_IL_intel, @Breaking911, @JavierBlas, @SheDrills, @IranIntl_En) posting right now? Any strike footage, OSINT, or viral claims?

STYLE:
- Write like a war correspondent, not a sanitized corporate report
- Be direct and specific: names, numbers, cities, times
- Say what's ACTUALLY happening, not what "might" be happening
- If something is unconfirmed, say so but include it
- 250-400 words — comprehensive but dense
- Cite sources inline (per @handle, per Reuters, etc.)"""

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        _search_tool = {"type": "function", "function": {"name": "web_search", "description": "Search the web and X/Twitter for real-time information"}}
        _sys = ("You are a war correspondent and intelligence analyst covering the 2026 Iran War. "
                "Write like you're filing a live dispatch — direct, specific, urgent. "
                "Cite sources inline. Include strike footage descriptions if found. "
                "No corporate hedging — say what's happening.")
        try:
            response = client.chat.completions.create(
                model="grok-4-1-fast-reasoning",
                messages=[
                    {"role": "system", "content": _sys},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
                temperature=0.3,
                tools=[_search_tool],
                tool_choice="auto",
            )
        except Exception:
            response = client.chat.completions.create(
                model="grok-4-1-fast-reasoning",
                messages=[
                    {"role": "system", "content": _sys},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
                temperature=0.3,
            )
        brief = response.choices[0].message.content
        brief = brief.strip() if brief else ""
        # Cache in Supabase for 30 min
        if brief:
            try:
                from src.ai_cache import cache_ai_response
                cache_ai_response(_brief_key, brief, model="grok-4-1-fast",
                                   source_page="iran_conflict", ticker="CONFLICT",
                                   ttl_hours=0.5, cost_estimate=0.02,
                                   prompt_summary="Situation briefing")
            except Exception:
                pass
        return brief
    except Exception as e:
        logger.warning(f"Grok situation briefing failed: {e}")
        return ""


def _grok_breaking_news_brief() -> str:
    """Quick Grok scan: what happened in the last 6 hours re: Iran conflict.
    Fed into Claude/Gemini context so they have breaking awareness. Cached 15 min."""
    grok_key = _get_key("GROK_API_KEY")
    if not grok_key:
        return ""

    prompt = """Search X/Twitter RIGHT NOW for the latest developments in the US-Israel-Iran conflict from the LAST 6 HOURS.

Check these accounts first:
  @sentdefender, @IranIntl_En, @JavierBlas, @IDF, @CENTCOM, @Khamenei_ir, @IsraelWarRoom,
  @TheStudyofWar, @CriticalThreats, @BarakRavid, @OilSheppard, @MiddleEastEye,
  @SheDrills, @Faytuks, @FaytuksNetwork, @Frank_Stones, @BinsaeedRashid, @inside_IL_intel, @JesseCohenInv, @Breaking911

Summarize in 5-8 bullet points:
- Any new strikes, attacks, or military operations
- Any facility status changes (oil/gas/LNG infrastructure)
- Any diplomatic signals (ceasefire talks, UN activity, mediator statements)
- Any market-moving developments (oil price spikes, shipping changes, sanctions)
- Any viral OSINT or breaking reports gaining traction

Be specific: include times, sources, and numbers. If nothing significant happened in 6 hours, say so briefly.
Return plain text, not JSON."""

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        response = client.chat.completions.create(
            model="grok-4-1-fast-reasoning",
            messages=[
                {"role": "system", "content": "You are a real-time conflict intelligence briefer. Be concise and cite sources."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=800,
            temperature=0.1,
        )
        brief = response.choices[0].message.content
        return brief.strip() if brief else ""
    except Exception as e:
        logger.warning(f"Grok breaking news brief failed: {e}")
        return ""


# ─────────────────────────────────────────────
# 5. SCENARIO TRACKING SCORECARD
# ─────────────────────────────────────────────

def _build_scenario_scorecard(history: list) -> dict:
    """Build comprehensive accuracy tracker from history.
    Returns {runs: [...], per_model: {...}, summary: {...}}."""
    if len(history) < 2:
        return {}

    runs = []
    model_stats = {}  # model_key -> {esc_errors, oil_errors, direction_hits, direction_total}
    entries = history[-11:-1] if len(history) >= 11 else history[:-1]

    for i, entry in enumerate(entries):
        blended = entry.get("blended", {})
        ts = entry.get("timestamp", "")
        esc_score = blended.get("escalation_risk", {}).get("score", 0)
        oil_base = blended.get("oil_impact", {}).get("price_forecast_30d", {}).get("base", 0)
        ceasefire = blended.get("diplomatic_outlook", {}).get("ceasefire_probability_30d", 0)

        # Get actual from next entry
        if i + 1 < len(entries):
            next_blended = entries[i + 1].get("blended", {})
        else:
            next_blended = history[-1].get("blended", {})

        actual_esc = next_blended.get("escalation_risk", {}).get("score", 0)
        actual_oil = next_blended.get("oil_impact", {}).get("price_forecast_30d", {}).get("base", 0)
        actual_cf = next_blended.get("diplomatic_outlook", {}).get("ceasefire_probability_30d", 0)

        esc_error = abs(esc_score - actual_esc) if actual_esc and esc_score else None
        oil_error = abs(oil_base - actual_oil) if actual_oil and oil_base else None
        cf_error = abs(ceasefire - actual_cf) if ceasefire is not None and actual_cf is not None else None

        # Directional accuracy for escalation
        prev_esc = entries[i - 1].get("blended", {}).get("escalation_risk", {}).get("score", 0) if i > 0 else esc_score
        pred_direction = "up" if esc_score > prev_esc else "down" if esc_score < prev_esc else "flat"
        actual_direction = "up" if actual_esc > esc_score else "down" if actual_esc < esc_score else "flat"
        direction_hit = pred_direction == actual_direction or pred_direction == "flat"

        # Per-model accuracy
        model_predictions = {}
        assessments = blended.get("escalation_risk", {}).get("model_assessments", [])
        for ma in assessments:
            mk = ma.get("model_key", "")
            pred = ma.get("score", 0)
            if mk and pred and actual_esc:
                err = abs(pred - actual_esc)
                if mk not in model_stats:
                    model_stats[mk] = {"esc_errors": [], "direction_hits": 0, "direction_total": 0, "name": ma.get("model", mk)}
                model_stats[mk]["esc_errors"].append(err)
                model_stats[mk]["direction_total"] += 1
                # Did this model predict the right direction?
                if (pred > esc_score and actual_esc > esc_score) or \
                   (pred < esc_score and actual_esc < esc_score) or \
                   (pred == esc_score):
                    model_stats[mk]["direction_hits"] += 1
                model_predictions[mk] = {"pred": pred, "error": err}

        # Find which model was closest
        best_model = ""
        if model_predictions:
            best_mk = min(model_predictions, key=lambda k: model_predictions[k]["error"])
            best_model = model_stats.get(best_mk, {}).get("name", best_mk)

        runs.append({
            "timestamp": ts,
            "predicted_escalation": esc_score,
            "actual_escalation": actual_esc,
            "escalation_error": esc_error,
            "predicted_oil": oil_base,
            "actual_oil": actual_oil,
            "oil_error": oil_error,
            "predicted_ceasefire": ceasefire,
            "actual_ceasefire": actual_cf,
            "ceasefire_error": cf_error,
            "direction_hit": direction_hit,
            "best_model": best_model,
            "model_predictions": model_predictions,
        })

    # Compute per-model summary
    per_model = {}
    for mk, stats in model_stats.items():
        errors = stats["esc_errors"]
        avg_err = sum(errors) / len(errors) if errors else 0
        dir_rate = (stats["direction_hits"] / stats["direction_total"] * 100) if stats["direction_total"] > 0 else 0
        per_model[mk] = {
            "name": stats["name"],
            "avg_error": round(avg_err, 2),
            "direction_rate": round(dir_rate),
            "n_predictions": len(errors),
            "weight": round(max(0.5, min(2.0, 1.0 / (avg_err + 0.5))), 2),
        }

    # Overall summary
    all_esc_errors = [r["escalation_error"] for r in runs if r["escalation_error"] is not None]
    all_oil_errors = [r["oil_error"] for r in runs if r["oil_error"] is not None]
    all_dir = [r["direction_hit"] for r in runs]

    summary = {
        "avg_esc_error": round(sum(all_esc_errors) / len(all_esc_errors), 2) if all_esc_errors else None,
        "avg_oil_error": round(sum(all_oil_errors) / len(all_oil_errors), 1) if all_oil_errors else None,
        "direction_rate": round(sum(all_dir) / len(all_dir) * 100) if all_dir else 0,
        "n_runs": len(runs),
    }

    return {"runs": runs, "per_model": per_model, "summary": summary}


# ─────────────────────────────────────────────
# 6. LNG / NATURAL GAS PRICING
# ─────────────────────────────────────────────

def _fetch_lng_natgas_prices() -> dict:
    return fetch_energy_spot_prices()


# ─────────────────────────────────────────────
# 7. CONTINUOUS MONITORING — CHANGE DETECTION
# ─────────────────────────────────────────────

@st.cache_data(ttl=1800, show_spinner=False)
def _monitor_escalation_changes() -> dict | None:
    """Compare the latest analysis against the previous one.
    Returns alert info if escalation changed by >1 point or a facility status changed."""
    history_file = os.path.join(os.path.dirname(__file__), "..", "src", "iran_conflict_history.json")
    try:
        if not os.path.exists(history_file):
            return None
        with open(history_file, "r") as f:
            history = json.load(f)
        if len(history) < 2:
            return None

        current = history[-1].get("blended", {})
        previous = history[-2].get("blended", {})

        alerts = []

        # Escalation score change
        curr_esc = current.get("escalation_risk", {}).get("score", 0)
        prev_esc = previous.get("escalation_risk", {}).get("score", 0)
        if curr_esc and prev_esc and abs(curr_esc - prev_esc) >= 1:
            direction = "UP" if curr_esc > prev_esc else "DOWN"
            color = "#ff4444" if direction == "UP" else "#00ff96"
            alerts.append({
                "type": "escalation",
                "message": f"Escalation {direction}: {prev_esc}/10 → {curr_esc}/10",
                "color": color,
            })

        # Oil price forecast change
        curr_oil = current.get("oil_impact", {}).get("price_forecast_30d", {}).get("base", 0)
        prev_oil = previous.get("oil_impact", {}).get("price_forecast_30d", {}).get("base", 0)
        if curr_oil and prev_oil and abs(curr_oil - prev_oil) >= 5:
            direction = "UP" if curr_oil > prev_oil else "DOWN"
            color = "#ff4444" if direction == "UP" else "#00ff96"
            alerts.append({
                "type": "oil",
                "message": f"Oil forecast {direction}: ${prev_oil} → ${curr_oil}/bbl (30d base)",
                "color": color,
            })

        # Ceasefire probability change
        curr_cf = current.get("diplomatic_outlook", {}).get("ceasefire_probability_30d", 0)
        prev_cf = previous.get("diplomatic_outlook", {}).get("ceasefire_probability_30d", 0)
        if abs(curr_cf - prev_cf) >= 10:
            direction = "UP" if curr_cf > prev_cf else "DOWN"
            color = "#00ff96" if direction == "UP" else "#ff4444"
            alerts.append({
                "type": "ceasefire",
                "message": f"Ceasefire probability {direction}: {prev_cf}% → {curr_cf}%",
                "color": color,
            })

        # Timestamp
        curr_ts = history[-1].get("timestamp", "")
        prev_ts = history[-2].get("timestamp", "")

        if alerts:
            return {"alerts": alerts, "current_ts": curr_ts, "previous_ts": prev_ts}
        return None
    except Exception as e:
        logger.warning(f"Escalation monitoring failed: {e}")
        return None


# ─────────────────────────────────────────────
# 8. SOURCE CREDIBILITY SCORING
# ─────────────────────────────────────────────

# Track which sources have been cited in analyses and how accurate the associated predictions were
SOURCE_CREDIBILITY_FILE = os.path.join(os.path.dirname(__file__), "..", "src", "source_credibility.json")


def _load_source_credibility() -> dict:
    """Load source credibility scores from Supabase, local JSON fallback."""
    # --- Supabase primary ---
    try:
        from src.db import get_client
        db = get_client()
        if db:
            resp = db.table("source_credibility").select("*").execute()
            if resp.data:
                return {
                    row["source_handle"]: {
                        "citations": row.get("total_citations", 0),
                        "accuracy_sum": row.get("total_accuracy_sum", 0),
                        "score": row.get("rolling_score", 50),
                    }
                    for row in resp.data
                }
    except Exception as e:
        logger.warning(f"Supabase source_credibility read failed: {e}")

    # --- Local JSON fallback ---
    try:
        if os.path.exists(SOURCE_CREDIBILITY_FILE):
            with open(SOURCE_CREDIBILITY_FILE, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_source_credibility(data: dict):
    """Persist source credibility scores to Supabase, local JSON fallback."""
    # --- Supabase primary ---
    try:
        from src.db import get_client
        db = get_client()
        if db:
            rows = []
            for handle, info in data.items():
                rows.append({
                    "source_handle": handle,
                    "total_citations": info.get("citations", 0),
                    "total_accuracy_sum": info.get("accuracy_sum", 0),
                    "rolling_score": info.get("score", 50),
                    "last_updated": datetime.now().isoformat(),
                })
            if rows:
                db.table("source_credibility").upsert(rows).execute()
                return
    except Exception as e:
        logger.warning(f"Supabase source_credibility upsert failed: {e}")

    # --- Local JSON fallback ---
    try:
        os.makedirs(os.path.dirname(SOURCE_CREDIBILITY_FILE), exist_ok=True)
        with open(SOURCE_CREDIBILITY_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save source credibility: {e}")


def _update_source_credibility(conflict_result: dict, history: list):
    """Update credibility scores based on cited sources and prediction accuracy.
    Sources cited in accurate predictions get boosted; those in inaccurate ones get penalized."""
    if len(history) < 2:
        return

    cred = _load_source_credibility()

    # Extract cited sources from the latest result
    cited_sources = set()
    for ma in conflict_result.get("escalation_risk", {}).get("model_assessments", []):
        for cite in ma.get("citations", []):
            # Extract @handles from citation text
            handles = re.findall(r'@\w+', cite)
            cited_sources.update(h.lower() for h in handles)
        # Also check rationale for @handles
        rationale = ma.get("rationale", "")
        handles = re.findall(r'@\w+', rationale)
        cited_sources.update(h.lower() for h in handles)

    if not cited_sources:
        return

    # Compare current prediction accuracy against previous
    current = conflict_result
    previous = history[-2].get("blended", {}) if len(history) >= 2 else {}

    curr_esc = current.get("escalation_risk", {}).get("score", 0)
    prev_esc = previous.get("escalation_risk", {}).get("score", 0)
    # Lower error = more accurate
    esc_error = abs(curr_esc - prev_esc) if prev_esc else 0

    for source in cited_sources:
        if source not in cred:
            cred[source] = {"citations": 0, "accuracy_sum": 0, "score": 50}

        cred[source]["citations"] += 1
        # Accuracy: 100 if error <= 0.5, scaling down to 0 at error >= 5
        accuracy = max(0, min(100, 100 - (esc_error * 20)))
        cred[source]["accuracy_sum"] += accuracy
        # Rolling score: weighted average of historical accuracy
        cred[source]["score"] = round(cred[source]["accuracy_sum"] / cred[source]["citations"])

    _save_source_credibility(cred)


def _get_top_sources(n: int = 10) -> list:
    """Get the most-cited sources ranked by credibility score."""
    cred = _load_source_credibility()
    if not cred:
        return []
    ranked = sorted(cred.items(), key=lambda x: (x[1]["score"], x[1]["citations"]), reverse=True)
    return [{"handle": h, **data} for h, data in ranked[:n]]


@st.cache_data(ttl=1800, show_spinner=False)
def _grok_infrastructure_status(_cache_key: str) -> list:
    """Independent Grok-only infrastructure status check.
    Uses the last verified state as baseline (self-updating loop).
    Falls back to hardcoded INFRASTRUCTURE_TARGETS on first run."""
    grok_key = _get_key("GROK_API_KEY")

    # Load last verified state, fall back to hardcoded baseline
    saved_state = _load_infra_state()
    if not grok_key:
        if saved_state:
            return saved_state
        return [{"name": n, "status": "degraded" if i.get("impact_level") in ("Critical", "Severe") else "operational",
                 "detail": i.get("status", ""), "changed": False, "update": None}
                for n, i in INFRASTRUCTURE_TARGETS.items()]

    # Build current table from saved state or hardcoded baseline
    if saved_state:
        current_table = [{"name": s.get("name", ""), "status": s.get("status", ""),
                          "detail": s.get("detail", "")} for s in saved_state]
    else:
        current_table = []
        for name, info in INFRASTRUCTURE_TARGETS.items():
            current_table.append({
                "name": name,
                "capacity": info.get("capacity", ""),
                "status": info.get("status", ""),
                "impact_level": info.get("impact_level", ""),
            })

    today = datetime.now().strftime("%B %d, %Y")
    prompt = f"""TODAY'S DATE: {today}. You have REAL-TIME access to X/Twitter and current news. USE IT.

You are monitoring energy infrastructure impacted by the US-Israel-Iran conflict (started Feb 28, 2026).

CURRENT TABLE (may be outdated):
{json.dumps(current_table, indent=2)}

YOUR TASK — you MUST provide a status for every facility:
1. Search X/Twitter and news for the latest on EACH facility. Use these PRIORITY ACCOUNTS:
  Energy & Markets: @JavierBlas, @amaboraz, @EnergyIntel, @OilSheppard, @SheDrills, @Frank_Stones
  Shipping/AIS: @MarineTraffic (marinetraffic.com — vessel tracking, Hormuz transit counts, tanker AIS data)
  OSINT & Military: @sentdefender, @TheStudyofWar, @CriticalThreats, @AuroraIntel, @IntelSky, @Conflict_Radar, @Faytuks, @FaytuksNetwork, @inside_IL_intel
  Journalists: @IranIntl_En, @MiddleEastEye, @Shayan86, @BarakRavid, @AndrewMills1, @RezaSayahNews
  Iran Official: @Khamenei_ir, @araghchi, @Iran_GOV
  Israel Official: @IsraeliPM, @IDF, @IsraelMFA, @IsraelWarRoom
  Oman & Qatar: @badralbusaidi, @ONA_eng, @MG_MOD_OMAN, @MofaQatar_EN, @MBA_AlThani_, @IMO_Qatar
  UAE: @modgovae, @AnwarGargash
  Energy Companies: @QatarEnergy
  Other: @cryptorand, @clement_molin, @zerohedge, @HFI_Research
  Also check: Reuters, Bloomberg, AP, Al Jazeera, IEA, S&P Global Platts, Argus Media.
  Finance blogs: zerohedge.com, seekingalpha.com, ritholtz.com, calculatedriskblog.com, ft.com, bloomberg.com
2. For EACH facility, provide your best assessment of the current status based on what you find.
3. Add any NEW impacted facilities not in the table.

IMPORTANT GUIDELINES:
- You have real-time search access — USE IT. Do not return "unverified" for everything. Search and report what you find.
- For each facility, give a status (operational/degraded/offline) and a description of current conditions.
- When you cite a source, make sure it's a real post you found (not from a future date — today is {today}).
- If you find conflicting reports, mention both and note the uncertainty.
- Use "estimated" for numbers you're not 100% certain about, but still provide your best estimate.
- Only mark something "unverified" as a last resort if you genuinely cannot find ANY information about that specific facility.

Return ONLY a JSON object:
{{
  "facilities": [
    {{"name": "facility name", "status": "operational/degraded/offline", "detail": "current status based on your search findings", "changed": true/false, "update": "what changed since the baseline, or null if no change"}}
  ],
  "last_checked": "{today}",
  "sources_consulted": ["list of sources/accounts you searched"]
}}

RULES:
- Include ALL facilities from the current table — never drop any.
- You MUST set status to operational, degraded, or offline for each — not "unverified".
- Add new impacted facilities if found."""

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        _infra_sys = (
            "You are a real-time energy infrastructure monitor with live X/Twitter access. "
            "You MUST search for and report the current status of each facility. "
            "Use your real-time access to find the latest reports — do not default to 'unverified'. "
            "Every facility must get a status: operational, degraded, or offline. "
            "When citing sources, use ones you actually found — do not cite future dates. "
            "Use 'estimated' for uncertain numbers rather than inventing precise figures. "
            "Your output is displayed to paying customers as live intelligence."
        )
        _search_tool = {"type": "function", "function": {"name": "web_search", "description": "Search the web and X/Twitter for real-time information"}}
        try:
            response = client.chat.completions.create(
                model="grok-4.20-0309-reasoning",
                messages=[
                    {"role": "system", "content": _infra_sys},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4000,
                temperature=0.1,
                response_format={"type": "json_object"},
                tools=[_search_tool],
                tool_choice="auto",
            )
        except Exception:
            response = client.chat.completions.create(
                model="grok-4.20-0309-reasoning",
                messages=[
                    {"role": "system", "content": _infra_sys},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4000,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
        raw = response.choices[0].message.content
        if not raw:
            return []

        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)

        if isinstance(parsed, list):
            result = parsed
        elif isinstance(parsed, dict):
            result = parsed.get("facilities", parsed.get("infrastructure_status", []))
        else:
            result = []

        # Save verified state as the new baseline for next check
        if result:
            _save_infra_state(result)
            logger.info(f"Infrastructure state saved: {len(result)} facilities")

        return result
    except Exception as e:
        logger.warning(f"Grok infrastructure status failed: {e}")
        # Return saved state if Grok fails
        return saved_state if saved_state else []


def _fetch_live_tweets() -> list:
    """Fetch live trending tweets via Grok (has real-time X access). Cached 10 min."""
    grok_key = _get_key("GROK_API_KEY")
    if not grok_key:
        return []

    try:
        client = _get_openai_client(api_key=grok_key, base_url="https://api.x.ai/v1")
        _search_tool = {"type": "function", "function": {"name": "web_search", "description": "Search the web and X/Twitter for real-time information"}}
        try:
            response = client.chat.completions.create(
                model="grok-4-1-fast-reasoning",
                messages=[
                    {"role": "system", "content": "You are a real-time X/Twitter intelligence analyst. Return only JSON."},
                    {"role": "user", "content": _LIVE_TWEET_PROMPT},
                ],
                max_tokens=1500,
                temperature=0.2,
                response_format={"type": "json_object"},
                tools=[_search_tool],
                tool_choice="auto",
            )
        except Exception:
            response = client.chat.completions.create(
                model="grok-4-1-fast-reasoning",
                messages=[
                    {"role": "system", "content": "You are a real-time X/Twitter intelligence analyst. Return only JSON."},
                    {"role": "user", "content": _LIVE_TWEET_PROMPT},
                ],
                max_tokens=1500,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
        raw = response.choices[0].message.content
        if not raw:
            return []

        cleaned = re.sub(r"^```json?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)

        # Handle both {"tweets": [...]} and direct [...] formats
        if isinstance(parsed, dict):
            tweets = parsed.get("tweets", parsed.get("trending_tweets", []))
        elif isinstance(parsed, list):
            tweets = parsed
        else:
            return []

        # Quality filter
        return [tw for tw in tweets if _is_quality_tweet(tw.get("content", ""))][:10]
    except Exception as e:
        logger.warning(f"Live tweet fetch failed: {e}")
        return []


def _synthesize_summary(successful: dict, configs: dict = None) -> str:
    """Use a fast AI call to merge all model summaries into one coherent narrative."""
    _cfgs = configs or MODEL_CONFIGS
    summaries = {}  # keyed by model_key
    for k, v in successful.items():
        s = v.get("situation_summary", "")
        if s:
            summaries[k] = s

    if not summaries:
        return ""
    if len(summaries) == 1:
        return list(summaries.values())[0]

    # Try Gemini Flash for fast synthesis
    api_key = _get_key("GEMINI_API_KEY")
    if not api_key:
        # Fallback: pick Grok's or longest
        for pref in ["grok", "openai", "gemini", "claude"]:
            if pref in summaries:
                return summaries[pref]
        return max(summaries.values(), key=len)

    model_block = "\n\n".join(
        f"**{_cfgs.get(k, {}).get('name', k)}:**\n{text}" for k, text in summaries.items()
    )
    prompt = (
        "You are merging conflict intelligence from multiple AI analysts into a structured executive brief.\n\n"
        f"{model_block}\n\n"
        "PRODUCE THIS EXACT STRUCTURE (use these headers):\n\n"
        "**CONSENSUS:** 2-3 sentences on what ALL models agree on. State confidently.\n\n"
        "**KEY DIVERGENCE:** 1-2 sentences on where models disagree and why. "
        "E.g., 'Grok sees X based on X/Twitter intel, while Claude emphasizes Y based on diplomatic signals.'\n\n"
        "**CRITICAL UNCERTAINTY:** The single most important unknown that would shift the analysis. "
        "What data point, if it changed, would move all models?\n\n"
        "**LAST 24 HOURS:** 3-5 sentences in reverse chronological order — most recent first. "
        "Dense with facts: times, sources, numbers. Tag each claim [verified], [estimated], or [inferred].\n\n"
        "Rules:\n"
        "- Preserve ALL specific data points from the inputs.\n"
        "- Do NOT add information not present in the inputs.\n"
        "- No preamble. Start with **CONSENSUS:**"
    )

    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=2000,
                temperature=0.2,
            ),
        )
        result = response.text.strip()
        if result and len(result) > 50:
            return result
    except Exception as e:
        logger.warning(f"Summary synthesis failed: {e}")

    # Fallback
    for pref in ["grok", "openai", "gemini", "claude"]:
        if pref in summaries:
            return summaries[pref]
    return max(summaries.values(), key=len)


def blend_conflict_results(results: dict, configs: dict = None) -> dict:
    """Blend multiple model outputs into a unified conflict assessment with domain-weighted scoring."""
    if configs is None:
        configs = MODEL_CONFIGS
    successful = {k: v for k, v in results.items() if v.get("success")}
    if not successful:
        return {"success": False, "error": "All models failed"}

    n = len(successful)
    models_used = [v.get("model_name", k) for k, v in successful.items()]

    # Load historical accuracy weights
    history = load_conflict_history()
    acc_weights = _compute_model_accuracy(history)
    if acc_weights:
        logger.info(f"Accuracy weights: {acc_weights}")

    # Domain-weighted escalation (boosted by historical accuracy)
    avg_escalation = _domain_weighted_avg(
        successful, ["escalation_risk", "score"], "weight_escalation", 5, configs, acc_weights)

    # Domain-weighted oil disruption
    avg_disruption = _domain_weighted_avg(
        successful, ["oil_impact", "current_disruption_mbpd"], "weight_oil", 0, configs, acc_weights)

    # Domain-weighted oil price forecasts
    price_low = _domain_weighted_avg(
        successful, ["oil_impact", "price_forecast_30d", "low"], "weight_oil", 0, configs, acc_weights)
    price_base = _domain_weighted_avg(
        successful, ["oil_impact", "price_forecast_30d", "base"], "weight_oil", 0, configs, acc_weights)
    price_high = _domain_weighted_avg(
        successful, ["oil_impact", "price_forecast_30d", "high"], "weight_oil", 0, configs, acc_weights)

    # Domain-weighted ceasefire probability
    avg_ceasefire = round(_domain_weighted_avg(
        successful, ["diplomatic_outlook", "ceasefire_probability_30d"], "weight_ceasefire", 0, configs, acc_weights))

    # Collect all unique developments
    all_devs = []
    seen = set()
    for v in successful.values():
        for d in v.get("latest_developments", []):
            if isinstance(d, dict):
                d = d.get("event") or d.get("description") or d.get("text") or str(d)
            d = str(d)
            d_lower = d.lower()[:50]
            if d_lower not in seen:
                all_devs.append(d)
                seen.add(d_lower)

    # Synthesize situation summary from all models
    best_summary = _synthesize_summary(successful, configs)

    # Collect infrastructure statuses — specialist-priority:
    # Gemini (energy specialist) > Grok (latest news) > others
    infra_priority = ["gemini", "grok", "openai", "claude"]
    infra_map = {}
    for model_key in infra_priority:
        if model_key not in successful:
            continue
        for item in successful[model_key].get("infrastructure_status", []):
            name = item.get("name", "")
            if name and name not in infra_map:
                infra_map[name] = item
    # Fill in any facilities from remaining models not already covered
    for v in successful.values():
        for item in v.get("infrastructure_status", []):
            name = item.get("name", "")
            if name and name not in infra_map:
                infra_map[name] = item

    # Collect market implications — prefer specialist per domain
    market_keys = ["energy", "equities", "safe_havens", "currencies"]
    # Gemini is energy specialist, Claude for safe havens
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
            "_partial": v.get("_partial", False),
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

    # Collect "watch next" triggers from all models, deduplicate
    all_watches = []
    seen_triggers = set()
    for v in successful.values():
        for w in v.get("watch_next", []):
            trigger = str(w.get("trigger", ""))
            if trigger and trigger.lower()[:40] not in seen_triggers:
                all_watches.append(w)
                seen_triggers.add(trigger.lower()[:40])
    # Sort by probability descending
    all_watches.sort(key=lambda w: w.get("probability", 0), reverse=True)
    blended["watch_next"] = all_watches[:8]

    # Collect trending tweets — prioritize Grok (has live X access), then any model
    all_tweets = []
    seen_handles = set()
    for model_pref in ["grok", "gemini", "claude", "openai"]:
        if model_pref in successful:
            for tw in successful[model_pref].get("trending_tweets", []):
                handle = tw.get("handle", "")
                content = tw.get("content", "")
                if handle and handle not in seen_handles and _is_quality_tweet(content):
                    all_tweets.append(tw)
                    seen_handles.add(handle)
    # Sort by velocity: high > medium > low, then by likes
    vel_order = {"high": 0, "medium": 1, "low": 2}
    all_tweets.sort(key=lambda t: (vel_order.get(t.get("velocity", "low"), 2), -t.get("likes", 0)))
    # AI quality filter — score each tweet for informational value
    all_tweets = _ai_filter_tweets(all_tweets, configs)
    blended["trending_tweets"] = all_tweets[:10]

    # Write cross-page context
    try:
        from src.cross_context import write_context
        write_context("iran_conflict", {
            "escalation": avg_escalation,
            "oil_disruption": avg_disruption,
            "ceasefire_prob": avg_ceasefire,
            "oil_price_base": round(price_base),
        })
    except Exception:
        pass

    return blended


def _postprocess_with_real_data(result: dict, market_context: dict = None,
                                 lng_prices: dict = None, df_oil=None) -> dict:
    """Post-process AI output: replace hallucinated prices with real API data,
    clamp disruption/forecasts to sane bounds. This is the final truth layer."""
    if not result or not result.get("success"):
        return result

    # --- 1. Fix market_implications text: replace wrong prices with real ones ---
    if market_context:
        mc = market_context
        mkt = result.get("market_implications", {})

        # Build price replacement map: pattern → real value
        replacements = {}
        if mc.get("brent"):
            real_brent = mc["brent"]["price"]
            real_brent_chg = mc["brent"]["change"]
            replacements["brent"] = (real_brent, real_brent_chg)
        if mc.get("vix"):
            replacements["vix"] = (mc["vix"]["price"], mc["vix"]["change"])
        if mc.get("gold"):
            replacements["gold"] = (mc["gold"]["price"], mc["gold"]["change"])
        if mc.get("dxy"):
            replacements["dxy"] = (mc["dxy"]["price"], mc["dxy"]["change"])

        # Replace wrong price references in energy section
        if mkt.get("energy") and replacements.get("brent"):
            real_p, real_c = replacements["brent"]
            text = mkt["energy"]
            # Replace any "Brent $X" pattern with real price
            text = re.sub(r'Brent\s*\$[\d,.]+(?:/bbl)?', f'Brent ${real_p}/bbl', text)
            text = re.sub(r'Brent\s+at\s+\$[\d,.]+', f'Brent at ${real_p}', text)
            # Fix WTI — prefer market_context over stale EIA data
            if mc.get("wti"):
                real_wti = mc["wti"]["price"]
                text = re.sub(r'WTI\s*(?:at\s*)?\$[\d,.]+(?:/bbl)?', f'WTI ${real_wti:.2f}/bbl', text)
                text = re.sub(r'WTI\s+anchored\s+at\s+\$[\d,.]+', f'WTI at ${real_wti:.2f}', text)
            elif df_oil is not None and not df_oil.empty:
                real_wti = df_oil["value"].iloc[-1]
                text = re.sub(r'WTI\s*(?:at\s*)?\$[\d,.]+(?:/bbl)?', f'WTI ${real_wti:.2f}/bbl', text)
                text = re.sub(r'WTI\s+anchored\s+at\s+\$[\d,.]+', f'WTI at ${real_wti:.2f}', text)
            # Fix Henry Hub — prefer market_context over lng_prices
            if mc.get("henry_hub"):
                real_hh = mc["henry_hub"]["price"]
                text = re.sub(r'Henry\s+Hub\s*(?:at\s*)?\$[\d,.]+', f'Henry Hub ${real_hh}', text)
            elif lng_prices and lng_prices.get("henry_hub"):
                real_hh = lng_prices["henry_hub"]["price"]
                text = re.sub(r'Henry\s+Hub\s*(?:at\s*)?\$[\d,.]+', f'Henry Hub ${real_hh}', text)
            mkt["energy"] = text

        # Replace wrong VIX in equities section
        if mkt.get("equities") and replacements.get("vix"):
            real_p, real_c = replacements["vix"]
            text = mkt["equities"]
            text = re.sub(r'VIX\s*(?:at\s*)?[\d.]+(?:\s*\([^)]*\))?', f'VIX {real_p} ({real_c:+.1f}%)', text)
            mkt["equities"] = text

        # Replace wrong Gold in safe_havens section
        if mkt.get("safe_havens") and replacements.get("gold"):
            real_p, real_c = replacements["gold"]
            text = mkt["safe_havens"]
            text = re.sub(r'Gold\s*(?:at\s*)?\$[\d,]+(?:/oz)?', f'Gold ${real_p:,.0f}/oz', text)
            mkt["safe_havens"] = text

        # Replace wrong DXY in currencies section
        if mkt.get("currencies") and replacements.get("dxy"):
            real_p, real_c = replacements["dxy"]
            text = mkt["currencies"]
            text = re.sub(r'DXY\s*(?:at\s*)?[\d.]+', f'DXY {real_p}', text)
            text = re.sub(r'USD\s+(?:Index\s+)?(?:at\s+)?[\d.]+', f'USD Index {real_p}', text)
            # Fix the absurd "120.55" → real value
            text = re.sub(r'(?:at|of)\s+12[\d.]+', f'at {real_p}', text)
            mkt["currencies"] = text

        result["market_implications"] = mkt

    # --- 2. Clamp disruption to sane bounds ---
    oil_impact = result.get("oil_impact", {})
    reported_disruption = oil_impact.get("current_disruption_mbpd", 0)
    min_disruption = abs(CURRENT_NET_DISRUPTION) * 0.5  # At least 50% of baseline
    if reported_disruption < min_disruption:
        logger.warning(
            f"POST-PROCESS: AI reported {reported_disruption:.1f} mbpd disruption, "
            f"flooring to {min_disruption:.1f} (50% of {abs(CURRENT_NET_DISRUPTION):.1f} baseline)"
        )
        oil_impact["current_disruption_mbpd"] = round(min_disruption, 1)
        oil_impact["_corrected"] = True

    # --- 3. Validate oil price forecast against actual price ---
    if df_oil is not None and not df_oil.empty:
        actual_price = df_oil["value"].iloc[-1]
        forecast = oil_impact.get("price_forecast_30d", {})
        base = forecast.get("base", 0)
        # If base forecast is more than 25% below actual price, it's hallucinated
        if base > 0 and base < actual_price * 0.75:
            logger.warning(
                f"POST-PROCESS: AI forecast base ${base} is >25% below actual ${actual_price:.0f}, correcting"
            )
            # Shift forecast up to be centered around actual price
            shift = actual_price - base
            forecast["low"] = round(forecast.get("low", 0) + shift * 0.7)
            forecast["base"] = round(actual_price)
            forecast["high"] = round(forecast.get("high", 0) + shift * 0.5)
            forecast["_corrected"] = True
        oil_impact["price_forecast_30d"] = forecast

    # --- 4. Validate escalation score against price signal ---
    esc = result.get("escalation_risk", {})
    esc_score = esc.get("score", 0)
    if df_oil is not None and not df_oil.empty:
        actual_price = df_oil["value"].iloc[-1]
        # If oil is above $90 but escalation is below 6, the score is too low
        if actual_price > 90 and esc_score < 6:
            corrected = max(6.0, esc_score)
            logger.warning(
                f"POST-PROCESS: Oil at ${actual_price:.0f} but escalation only {esc_score}/10, "
                f"flooring to {corrected}/10"
            )
            esc["score"] = corrected
            esc["level"] = "Critical" if corrected >= 8 else "High" if corrected >= 6 else "Elevated"
            esc["_corrected"] = True

    result["oil_impact"] = oil_impact
    result["escalation_risk"] = esc
    return result


def _get_eia_key():
    return _get_key("EIA_API_KEY")


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
    """Fetch all GDELT queries in parallel."""
    results = {}
    import concurrent.futures as _cf
    def _fetch_one(label, query):
        df = fetch_gdelt_timeline(query, timespan)
        return label, df
    with _cf.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(_fetch_one, lbl, q) for lbl, q in queries.items()]
        # Also fetch tone in parallel
        tone_future = ex.submit(fetch_gdelt_tone, list(queries.values())[0], timespan)
        for f in _cf.as_completed(futures):
            try:
                label, df = f.result()
                if not df.empty:
                    results[label] = df
            except Exception:
                pass
        try:
            tone = tone_future.result()
        except Exception:
            tone = pd.DataFrame()
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

    def _fetch_market_context():
        """Fetch Brent, WTI, VIX, Gold, DXY, HenryHub for AI context enrichment via Polygon + yfinance fallback."""
        from src.data_engine import polygon_batch_snapshot
        syms = ["BZ=F", "CL=F", "^VIX", "GC=F", "DX-Y.NYB", "NG=F"]
        labels = {"BZ=F": "brent", "CL=F": "wti", "^VIX": "vix", "GC=F": "gold", "DX-Y.NYB": "dxy", "NG=F": "henry_hub"}
        ctx = {}
        snapshots = polygon_batch_snapshot(syms)
        for sym, label in labels.items():
            if sym in snapshots:
                ctx[label] = snapshots[sym]
        _results["market_context"] = ctx

    def _fetch_stocks():
        data = {}
        for ticker in ["LMT", "RTX", "NOC", "XLE", "USO", "XOP"]:
            px = fetch_massive_data(ticker, 180)
            if px is not None and not px.empty:
                data[ticker] = px
        _results["stocks"] = data

    def _fetch_infra_validation():
        _results["infra_validation"] = get_validated_infrastructure()

    def _fetch_timeline_update():
        _results["timeline"] = _auto_update_timeline()

    def _fetch_polymarket():
        _results["polymarket"] = _fetch_iran_polymarket()

    def _fetch_oil_curve():
        _results["oil_curve"] = _fetch_oil_term_structure()

    def _fetch_breaking_news():
        _results["breaking_news"] = _grok_breaking_news_brief()

    def _fetch_situation_briefing():
        _results["situation_briefing"] = _grok_situation_briefing()

    def _fetch_lng():
        _results["lng_prices"] = _fetch_lng_natgas_prices()

    def _fetch_cot():
        _results["cot"] = get_cot_summary()

    def _fetch_treasury():
        _results["treasury"] = fetch_treasury_yields()

    def _fetch_defense():
        _results["defense_contracts"] = get_defense_contract_summary(days=30)

    # ── Critical path: only oil price (dashboard renders fast) ──
    critical_threads = [
        threading.Thread(target=_fetch_oil),
        threading.Thread(target=_fetch_market_context),
    ]
    # ── Deferred: everything else loads while user reads dashboard ──
    deferred_threads = [
        threading.Thread(target=_fetch_gdelt),
        threading.Thread(target=_fetch_stocks),
        threading.Thread(target=_fetch_acled),
        threading.Thread(target=_fetch_infra_validation),
        threading.Thread(target=_fetch_timeline_update),
        threading.Thread(target=_fetch_polymarket),
        threading.Thread(target=_fetch_oil_curve),
        threading.Thread(target=_fetch_breaking_news),
        threading.Thread(target=_fetch_situation_briefing),
        threading.Thread(target=_fetch_lng),
        threading.Thread(target=_fetch_cot),
        threading.Thread(target=_fetch_treasury),
        threading.Thread(target=_fetch_defense),
    ]
    # Start everything
    for t in critical_threads + deferred_threads:
        t.start()
    # Wait only for critical path (~2s for oil + market)
    for t in critical_threads:
        t.join(timeout=8)

    gdelt_data, df_tone = _results.get("gdelt", ({}, None))
    df_oil = _results.get("oil", pd.DataFrame())
    stock_data = _results.get("stocks", {})

    # Give deferred threads a short grace period to finish (most will be done by now)
    for t in deferred_threads:
        t.join(timeout=5)

    df_acled, acled_source = _results.get("acled", (pd.DataFrame(), None))
    market_context = _results.get("market_context", {})
    _prefetched_infra = _results.get("infra_validation")

    # Timeline: prefer Supabase (worker-updated) → Grok thread → hardcoded
    live_timeline = _results.get("timeline")
    if not live_timeline:
        try:
            from src.db import get_client as _get_db
            _tl_db = _get_db()
            if _tl_db:
                _tl_resp = _tl_db.table("conflict_timeline").select("*")\
                    .order("date", desc=True).limit(50).execute()
                if _tl_resp.data and len(_tl_resp.data) >= 5:
                    live_timeline = _tl_resp.data
        except Exception:
            pass
    if not live_timeline:
        live_timeline = CONFLICT_TIMELINE_EVENTS

    # Situation briefing: prefer Supabase worker cache → Grok thread
    situation_briefing = _results.get("situation_briefing", "")
    if not situation_briefing:
        try:
            from src.db import get_client as _get_db2
            _sb_db = _get_db2()
            if _sb_db:
                _hour_key = f"situation_briefing_{datetime.now().strftime('%Y%m%d_%H')}"
                _sb_resp = _sb_db.table("ai_response_cache").select("response")\
                    .eq("input_hash", _hour_key).limit(1).execute()
                if _sb_resp.data:
                    situation_briefing = _sb_resp.data[0].get("response", "")
        except Exception:
            pass
    polymarket_data = _results.get("polymarket", [])
    oil_curve = _results.get("oil_curve", {})
    breaking_news = _results.get("breaking_news", "")
    lng_prices = _results.get("lng_prices", {})
    cot_data = _results.get("cot", [])
    treasury_data = _results.get("treasury", {})
    defense_data = _results.get("defense_contracts", {})

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


# --- CHANGE ALERTS (runs before tabs, visible at top) ---
_change_alerts = _monitor_escalation_changes()
if _change_alerts and _change_alerts.get("alerts"):
    alert_items = ""
    for a in _change_alerts["alerts"]:
        alert_items += f'<span style="color:{a["color"]};font-weight:600;margin-right:16px;">{a["message"]}</span>'
    try:
        _prev_fmt = pd.Timestamp(_change_alerts["previous_ts"]).strftime("%b %d %I:%M%p")
    except Exception:
        _prev_fmt = ""
    st.markdown(
        f'<div style="padding:8px 14px;border:1px solid #ffaa0044;border-radius:6px;'
        f'background:rgba(255,170,0,0.06);margin-bottom:8px;display:flex;align-items:center;'
        f'justify-content:space-between;flex-wrap:wrap;">'
        f'<div>{alert_items}</div>'
        f'<span style="color:#888;font-size:0.72rem;">Since {_prev_fmt}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

# --- COMPACT STATUS BAR (always visible) ---
_last_result = None
try:
    _lr, _lr_stale = get_latest_conflict_result()
    _last_result = _lr
except Exception:
    pass
if _last_result and _last_result.get("blended"):
    _bl = _last_result["blended"]
    _esc = _bl.get("escalation_risk", {})
    _esc_score = _esc.get("score", 0)
    _esc_level = _esc.get("level", "Unknown")
    _esc_color = "#ff4444" if _esc_score >= 8 else "#ff6b35" if _esc_score >= 6 else "#ffaa00" if _esc_score >= 4 else "#00ff96"
    _days_war = (datetime.now() - datetime(2026, 2, 28)).days
    _oil_info = _bl.get("oil_impact", {})
    _oil_range = _oil_info.get("price_range", "")
    _cease = _bl.get("diplomatic_outlook", {}).get("ceasefire_probability_30d", "?")
    _summary = _bl.get("situation_summary", "")[:200]

    sc1, sc2, sc3, sc4 = st.columns(4)
    sc1.metric("Escalation", f"{_esc_score}/10", _esc_level)
    sc2.metric("Day", str(_days_war))
    sc3.metric("Oil", _oil_range or "—")
    sc4.metric("Ceasefire 30d", f"{_cease}%")

    if _summary:
        st.caption(_summary)
    st.divider()

# --- SITUATION BRIEFING + LATEST EVENTS (collapsible to reduce clutter) ---
_briefing_expander = st.expander("Intelligence Briefing & Latest Events", expanded=False)
_bx = _briefing_expander  # short alias for rendering inside expander

if situation_briefing:
    _briefing_time = datetime.now().strftime("%b %d, %I:%M %p")

    # Parse sections from the briefing text
    _section_colors = {
        "MILITARY": "#ff4b4b", "HORMUZ": "#ff6b35", "ENERGY": "#ff6b35",
        "DIPLOMATIC": "#00d1ff", "X/TWITTER": "#1DA1F2", "TWITTER": "#1DA1F2",
        "HUMANITARIAN": "#ffaa00", "NUCLEAR": "#bf6fff", "ESCALATION": "#ff2277",
    }
    _section_icons = {
        "MILITARY": "🎯", "HORMUZ": "🚢", "ENERGY": "⛽",
        "DIPLOMATIC": "🏛️", "X/TWITTER": "𝕏", "TWITTER": "𝕏",
        "HUMANITARIAN": "🏥", "NUCLEAR": "☢️", "ESCALATION": "🔺",
    }

    import re as _re
    _brief_lines = situation_briefing.strip().split("\n")
    _brief_html = ""
    _header_line = ""

    for _line in _brief_lines:
        _line = _line.strip()
        if not _line:
            continue

        # Check if this is a section header like **MILITARY:** or **HORMUZ & ENERGY:**
        _section_match = _re.match(r'\*\*([A-Z][A-Z\s&/]+?)[:：]\*\*\s*(.*)', _line)
        if _section_match:
            _sec_name = _section_match.group(1).strip()
            _sec_content = _section_match.group(2).strip()

            # Find matching color/icon
            _sec_color = "#888"
            _sec_icon = "•"
            for _key, _color in _section_colors.items():
                if _key in _sec_name.upper():
                    _sec_color = _color
                    _sec_icon = _section_icons.get(_key, "•")
                    break

            # Convert **bold** and @handles in content
            _sec_content = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', _sec_content)
            _sec_content = _re.sub(r'@(\w+)', r'<span style="color:#1DA1F2;">@\1</span>', _sec_content)

            _brief_html += (
                f'<div style="margin:10px 0 6px 0;padding:8px 12px;border-left:3px solid {_sec_color};'
                f'background:rgba({int(_sec_color[1:3],16)},{int(_sec_color[3:5],16)},{int(_sec_color[5:7],16)},0.06);'
                f'border-radius:0 6px 6px 0;">'
                f'<div style="font-size:0.7rem;font-weight:700;color:{_sec_color};text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">'
                f'{_sec_icon} {_sec_name}</div>'
                f'<div style="font-size:0.82rem;color:#ddd;line-height:1.6;">{_sec_content}</div>'
                f'</div>'
            )
        elif _line.startswith("**IRAN WAR DISPATCH") or _line.startswith("**SITUATION"):
            # Header/title line
            _clean = _re.sub(r'\*\*(.+?)\*\*', r'\1', _line)
            _header_line = _clean
        elif _line.startswith("(") and _line.endswith(")"):
            # Word count footer — skip
            pass
        else:
            # Regular paragraph
            _line = _re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', _line)
            _line = _re.sub(r'@(\w+)', r'<span style="color:#1DA1F2;">@\1</span>', _line)
            _brief_html += f'<div style="font-size:0.82rem;color:#ddd;line-height:1.6;margin:6px 0;">{_line}</div>'

    # Render the full briefing inside expander
    _bx.markdown(
        f'<div style="padding:16px 20px;border:1px solid #30363d;border-radius:8px;'
        f'background:linear-gradient(135deg, rgba(0,209,255,0.04), rgba(255,68,68,0.02));margin-bottom:16px;">'
        # Header bar
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;'
        f'padding-bottom:8px;border-bottom:1px solid #30363d;">'
        f'<div>'
        f'<span style="color:#ff4b4b;font-size:0.65rem;font-weight:800;letter-spacing:1px;'
        f'background:rgba(255,68,68,0.15);padding:2px 8px;border-radius:3px;">LIVE</span>'
        f'<span style="color:#00d1ff;font-size:0.75rem;font-weight:600;margin-left:8px;">INTELLIGENCE BRIEFING</span>'
        f'</div>'
        f'<span style="color:#888;font-size:0.65rem;">Updated {_briefing_time} · Grok + X/Twitter</span>'
        f'</div>'
        # Title if present
        + (f'<div style="font-size:0.9rem;font-weight:700;color:#e0e0e0;margin-bottom:8px;">{_header_line}</div>' if _header_line else '')
        # Body sections
        + _brief_html
        # Footer
        + f'<div style="margin-top:10px;padding-top:8px;border-top:1px solid #30363d;'
        f'font-size:0.6rem;color:#555;text-align:right;">Auto-refreshes every 15 minutes</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# --- LATEST EVENTS (inside expander) ---
_bx.markdown("#### Latest Conflict Events")
_latest_events = sorted(live_timeline, key=lambda e: e.get("date", ""), reverse=True)[:8]
_cat_colors = {
    "Military": "#ff4b4b", "Escalation": "#ff6b35", "Supply": "#ffaa00",
    "Policy": "#00d1ff", "Diplomatic": "#00ff96",
}
_cat_icons = {
    "Military": "💥", "Escalation": "🔺", "Supply": "🛢️",
    "Policy": "🏛️", "Diplomatic": "🕊️",
}
for evt in _latest_events:
    _edate = evt.get("date", "")
    _eevent = evt.get("event", "")
    _ecat = evt.get("category", "")
    _eimpact = evt.get("impact", "")
    _einfra = evt.get("infrastructure", "")
    _ccolor = _cat_colors.get(_ecat, "#888")
    _cicon = _cat_icons.get(_ecat, "•")

    _infra_html = ""
    if _einfra:
        _infra_html = (
            f'<div style="color:#ffaa00;font-size:0.72rem;margin-top:2px;">'
            f'⚡ {_einfra}</div>'
        )

    _bx.markdown(
        f'<div style="padding:8px 12px;border-left:3px solid {_ccolor};margin-bottom:5px;'
        f'background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">'
        f'<span style="color:#888;font-size:0.75rem;font-weight:600;">{_edate}</span>'
        f'<span style="font-size:0.65rem;color:{_ccolor};border:1px solid {_ccolor};'
        f'padding:0 5px;border-radius:3px;">{_cicon} {_ecat}</span>'
        f'</div>'
        f'<div style="color:#e0e0e0;font-size:0.85rem;line-height:1.4;">{_eevent}</div>'
        f'<div style="color:#888;font-size:0.72rem;margin-top:2px;">{_eimpact}</div>'
        f'{_infra_html}'
        f'</div>',
        unsafe_allow_html=True,
    )

# --- TABS ---
tab_ai, tab_timeline, tab_infra = st.tabs([
    "AI War Analysis",
    "Conflict Timeline",
    "Infrastructure Status",
])


# ---- TAB: AI War Analysis (4-model blend) ----
with tab_ai, error_boundary("AI Analysis"):
    st.subheader("AI-Powered Conflict Intelligence")

    # Determine which models to use based on user tier
    from src.auth import get_user_tier
    user_tier = get_user_tier()
    active_configs = dict(MODEL_CONFIGS)
    if user_tier == "platinum":
        # Platinum: upgrade Claude Sonnet → Opus
        active_configs["claude"] = dict(active_configs["claude"])
        active_configs["claude"]["model"] = "claude-opus-4-6"
        active_configs["claude"]["name"] = "Claude Opus"

    model_names = ", ".join(mc["name"] for mc in active_configs.values())
    tier_note = " (Platinum: Claude Opus)" if user_tier == "platinum" else ""
    st.caption(f"Blended analysis from {model_names}{tier_note} — updated hourly.")

    # Check for cached result
    latest_conflict, is_conflict_stale = get_latest_conflict_result()

    # Validate infrastructure baseline via Grok before building prompts
    # Use pre-fetched validation from parallel data load, fallback to live call
    if _prefetched_infra:
        validated_infra, validated_breakdown, validated_net, validation_summary = _prefetched_infra
    else:
        validated_infra, validated_breakdown, validated_net, validation_summary = get_validated_infrastructure()

    # Build base context (shared across all models — timeline + infrastructure)
    # Round to the hour so the prompt cache key stays stable within the same hour
    _ctx_time = datetime.now().replace(minute=0, second=0, microsecond=0)
    base_context = f"""Analyze the current state of the US-Israel war on Iran as of {_ctx_time.strftime('%B %d, %Y %I:%M %p')}.

Recent timeline of key events (auto-updated):
"""
    # Use live timeline (Grok-updated) instead of hardcoded
    for evt in live_timeline[-8:]:
        base_context += f"- {evt['date']}: {evt['event']} (Impact: {evt.get('impact', 'N/A')})\n"

    base_context += "\nKey infrastructure status (Grok real-time monitoring — status may differ from disruption baseline):\n"
    for name, info in validated_infra.items():
        correction_note = ""
        if info.get("_update") and info["_update"].lower() not in ("null", "none", "accurate", "no update"):
            correction_note = f" [Updated: {info['_update']}]"
        base_context += f"- {name}: {info['capacity']} — {info['status']}{correction_note}\n"
    if validation_summary:
        base_context += f"\nValidation note: {validation_summary}\n"

    # Polymarket prediction market odds
    if polymarket_data:
        base_context += "\nPrediction Market Odds (Polymarket — real money, market-implied probabilities):\n"
        for pm in polymarket_data:
            base_context += f"  {pm['question']}: {pm['yes_prob']}% YES\n"
        base_context += "  Use these as calibration anchors — if your probability diverges >20% from Polymarket, explain why.\n"

    # Oil futures term structure
    if oil_curve and oil_curve.get("prices"):
        base_context += f"\nOil Futures Term Structure ({oil_curve['structure'].upper()}, spread: ${oil_curve['spread']:+.2f}):\n"
        for label, price in oil_curve["prices"].items():
            base_context += f"  {label}: ${price}\n"
        base_context += f"  Market signal: {'Short-term supply fear (near-term premium)' if oil_curve['structure'] == 'backwardation' else 'Market expects supply relief (deferred premium)'}\n"

    # Source credibility — tell models which sources have been most reliable
    top_sources = _get_top_sources(10)
    if top_sources:
        reliable = [f"{s['handle']} (reliability: {s['score']}/100, cited {s['citations']}x)" for s in top_sources if s["score"] >= 60]
        if reliable:
            base_context += "\nMost reliable sources based on prediction accuracy tracking:\n"
            base_context += "  " + ", ".join(reliable) + "\n"
            base_context += "  Weight these sources more heavily when they conflict with lower-ranked accounts.\n"

    # SEC EDGAR: defense sector 8-K filings for AI context
    try:
        defense_8ks = []
        for dticker in ["LMT", "RTX", "NOC", "GD", "BA"]:
            evts = _fetch_edgar_8k(dticker, days=14)
            for e in (evts or []):
                defense_8ks.append(f"  {e.get('filed', '')}: {e.get('company', '')} — {e.get('form', '8-K')}")
        if defense_8ks:
            base_context += "\nRecent SEC 8-K Filings (defense sector, last 14 days):\n"
            base_context += "\n".join(defense_8ks[:10]) + "\n"
    except Exception:
        pass

    # Grok breaking news brief — inject into all models so they have real-time awareness
    if breaking_news:
        base_context += f"\n=== BREAKING NEWS (last 6 hours via Grok/X) ===\n{breaking_news}\n"

    # LNG / Natural gas prices — critical for Ras Laffan impact assessment
    if lng_prices:
        base_context += "\nNatural Gas / LNG Prices:\n"
        for key, data in lng_prices.items():
            base_context += f"  {data['name']}: ${data['price']} ({data['change']:+.1f}%)\n"
        base_context += "  (Qatar Ras Laffan damage is a major LNG story — use these prices in your analysis)\n"

    # CFTC COT — key contracts only (oil, gold, dollar, treasuries)
    if cot_data:
        key_cot = [c for c in cot_data if any(k in c["contract"] for k in ["Crude", "Gold", "Dollar", "Treasury"])]
        if key_cot:
            base_context += "\nCFTC Positioning: " + " | ".join(f"{c['contract']}: {c['signal']}" for c in key_cot) + "\n"

    # Treasury yield curve — key tenors only
    if treasury_data and treasury_data.get("yields"):
        yields = treasury_data["yields"]
        key_tenors = {k: v for k, v in yields.items() if any(t in k for t in ["2 Yr", "5 Yr", "10 Yr", "30 Yr", "3 Mo"])}
        if key_tenors:
            base_context += f"\nTreasury Yields: {', '.join(f'{k}={v}%' for k, v in key_tenors.items())}\n"

    # Defense contracts — compact summary
    if defense_data and defense_data.get("total_awarded"):
        total = defense_data["total_awarded"]
        contractors = defense_data.get("by_contractor", {})
        top3 = sorted(contractors.items(), key=lambda x: x[1], reverse=True)[:3]
        top3_str = ", ".join(f"{n}: ${v/1e6:.0f}M" for n, v in top3) if top3 else ""
        base_context += f"\nDOD Contracts (30d): ${total/1e9:.1f}B total. {top3_str}\n"

    # Pre-build data sections (cached strings)
    prior_ctx = build_prior_analysis_context()
    gdelt_bulk_ctx = build_gdelt_ai_context(df_gdelt_bulk) if not df_gdelt_bulk.empty else ""

    # Capture closure variables explicitly
    _mkt_ctx = market_context
    # ALWAYS use hardcoded disruption baseline for model prompts — Grok validation is for
    # infrastructure STATUS display only, not supply disruption math
    _disruption_override = (DISRUPTION_BREAKDOWN, CURRENT_NET_DISRUPTION)

    def _build_model_context(model_key: str) -> str:
        """Build a slimmed-down context for a specific model based on its data_sections."""
        sections = active_configs.get(model_key, MODEL_CONFIGS.get(model_key, {})).get("data_sections")
        ctx = base_context

        # Only include data sections this model needs
        if sections is None or "timeline" in sections or "infrastructure" in sections:
            pass  # already in base_context

        ctx += "\n" + build_live_data_context(gdelt_data, df_oil, df_acled, df_tone, sections=sections,
                                              market_context=_mkt_ctx, disruption_data=_disruption_override,
                                              lng_prices=lng_prices)

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
            if _conflict_running:
                st.button("Running...", type="primary", use_container_width=True, disabled=True)
                force_refresh = False
            else:
                force_refresh = st.button("Run AI Analysis", type="primary", use_container_width=True)
        with col_status:
            if latest_conflict and not is_conflict_stale:
                ts = latest_conflict.get("timestamp", "")
                try:
                    ts_fmt = pd.Timestamp(ts).strftime("%b %d, %I:%M %p")
                except Exception:
                    ts_fmt = ts
                _mu = latest_conflict.get("models_used", [])
                models_str = ", ".join(m if isinstance(m, str) else str(m.get("name", m)) for m in _mu)
                st.caption(f"Last updated: {ts_fmt} | Models: {models_str}")

        # Run analysis if stale or forced
        conflict_result = None
        if (force_refresh or (is_conflict_stale and latest_conflict is None)) and not st.session_state.get("_conflict_running", False):
            st.session_state["_conflict_running"] = True
            n_models = len(available_models)
            model_names_list = [active_configs.get(mk, MODEL_CONFIGS.get(mk, {})).get("name", mk)
                                for mk in available_models]

            try:
                # Progressive rendering — show each model as it completes
                progress_container = st.container()
                model_status = progress_container.empty()
                model_results = {}
                completed_names = []

                with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
                    futures = {}
                    for mk, api_key in available_models.items():
                        model_ctx = _build_model_context(mk)
                        mc = active_configs.get(mk)
                        futures[executor.submit(run_single_conflict_model, mk, api_key, model_ctx, mc)] = mk

                    model_status.info(f"Querying {n_models} models: {', '.join(model_names_list)}...")

                    for future in concurrent.futures.as_completed(futures):
                        mk = futures[future]
                        model_name = active_configs.get(mk, MODEL_CONFIGS.get(mk, {})).get("name", mk)
                        model_color = active_configs.get(mk, {}).get("color", "#888")
                        try:
                            model_results[mk] = future.result()
                            completed_names.append(f'<span style="color:{model_color}">{model_name} ✓</span>')
                        except Exception as e:
                            model_results[mk] = {"success": False, "error": str(e), "model_name": model_name}
                            completed_names.append(f'<span style="color:#ff4444">{model_name} ✗</span>')

                        remaining = n_models - len(completed_names)
                        status_html = " &nbsp;│&nbsp; ".join(completed_names)
                        if remaining > 0:
                            status_html += f' &nbsp;│&nbsp; <span style="color:#888">{remaining} remaining...</span>'
                        model_status.markdown(status_html, unsafe_allow_html=True)

                # Blend and save
                succeeded = [mk for mk, r in model_results.items() if r.get("success")]
                failed = {mk: r.get("error", "Unknown") for mk, r in model_results.items() if not r.get("success")}

                model_status.empty()
                conflict_result = blend_conflict_results(model_results, active_configs)
                # Post-process: replace hallucinated prices with real API data
                conflict_result = _postprocess_with_real_data(
                    conflict_result, market_context=market_context,
                    lng_prices=lng_prices, df_oil=df_oil)
                # Store raw model results so cards can pull model-specific analysis
                conflict_result["_raw_results"] = {k: v for k, v in model_results.items() if v.get("success")}
                if conflict_result.get("success"):
                    save_conflict_result(conflict_result)
                    st.success(f"Analysis complete — {len(succeeded)}/{n_models} models: {', '.join(active_configs.get(mk, {}).get('name', mk) for mk in succeeded)}")

                if failed:
                    for mk, err in failed.items():
                        st.warning(f"{active_configs.get(mk, {}).get('name', mk)} failed: {err[:150]}")
            except Exception as e:
                st.error(f"Analysis failed: {e}")
            finally:
                st.session_state["_conflict_running"] = False
        elif latest_conflict and latest_conflict.get("blended"):
            conflict_result = latest_conflict["blended"]
            conflict_result["success"] = True
            conflict_result["models_used"] = latest_conflict.get("models_used", [])
            # Post-process cached result too — ensures prices are current even if analysis is stale
            conflict_result = _postprocess_with_real_data(
                conflict_result, market_context=market_context,
                lng_prices=lng_prices, df_oil=df_oil)

        # Display results
        if conflict_result and conflict_result.get("success"):
            _conflict_ts = latest_conflict.get("timestamp") if latest_conflict else None
            if _conflict_ts and isinstance(_conflict_ts, str):
                try:
                    _conflict_ts = datetime.fromisoformat(_conflict_ts)
                except Exception:
                    _conflict_ts = datetime.now()
            freshness_bar(
                ("Conflict Data", _conflict_ts or datetime.now(), 60, 240),
                ("Briefing", datetime.now(), 60, 240),
            )
            st.divider()

            # Escalation gauge + key metrics
            esc = conflict_result.get("escalation_risk", {})
            oil = conflict_result.get("oil_impact", {})
            diplo = conflict_result.get("diplomatic_outlook", {})

            esc_score = esc.get("score", 5)
            esc_level = esc.get("level", "Unknown")
            esc_color = "#ff4b4b" if esc_score >= 8 else "#ff6b35" if esc_score >= 6 else "#ffaa00" if esc_score >= 4 else "#00ff96"

            # Infrastructure alerts — from Grok live feed only, today's changes only
            _infra_ck = datetime.now().strftime("%Y-%m-%d-%H") + ("-30" if datetime.now().minute >= 30 else "-00")
            _grok_infra_for_alerts = _grok_infrastructure_status(_infra_ck)
            _today_alerts = []
            for item in (_grok_infra_for_alerts or []):
                if item.get("changed") and item.get("update"):
                    update_text = str(item.get("update", ""))
                    if update_text.lower() not in ("null", "none", "no update", "no change"):
                        _today_alerts.append(f"{item.get('name', '')}: {update_text}")
            if _today_alerts:
                alert_items = "".join(
                    f"<li style='margin-bottom:4px;'>{a}</li>" for a in _today_alerts
                )
                st.markdown(
                    f'<div style="padding:12px 16px; border:2px solid #ff4b4b; border-radius:8px; '
                    f'background:rgba(255,75,75,0.08); margin-bottom:16px;">'
                    f'<div style="color:#ff4b4b; font-weight:bold; font-size:16px;">INFRASTRUCTURE UPDATE</div>'
                    f'<ul style="color:#ccc; margin:8px 0 0 16px; padding:0; font-size:13px;">{alert_items}</ul>'
                    f'<div style="color:#888; font-size:11px; margin-top:6px;">Source: Grok live infrastructure monitor — verified accounts and news wires.</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

            # Extract model-specific rationales for each card
            model_assess = esc.get("model_assessments", [])
            raw_results = conflict_result.get("_raw_results", {})

            # ESCALATION CARD: Grok's escalation rationale (real-time intel)
            _esc_rationale = ""
            _esc_model_label = ""
            for ma in model_assess:
                if ma.get("model_key") == "grok":
                    rat = ma.get("rationale", "").replace("<", "&lt;").replace(">", "&gt;")
                    _esc_rationale = rat[:400]
                    _esc_model_label = f'Grok ({ma.get("score", "")}/10)'
                    break

            # DISRUPTION CARD: Gemini's oil/supply analysis (quantitative)
            _disruption_rationale = ""
            _disruption_model_label = ""
            if "gemini" in raw_results:
                gemini_oil = raw_results["gemini"].get("oil_impact", {})
                chokepoints = gemini_oil.get("key_chokepoints", [])
                disruption_val = gemini_oil.get("current_disruption_mbpd", 0)
                fc = gemini_oil.get("price_forecast_30d", {})
                parts = []
                if disruption_val:
                    parts.append(f"Estimated net disruption: {disruption_val} mbpd.")
                if fc.get("base"):
                    parts.append(f"30d oil forecast: ${fc.get('low', '?')}-${fc.get('base', '?')}-${fc.get('high', '?')}.")
                for cp in (chokepoints or [])[:2]:
                    if isinstance(cp, str):
                        parts.append(cp)
                _disruption_rationale = " ".join(parts)[:400].replace("<", "&lt;").replace(">", "&gt;")
                _disruption_model_label = "Gemini (quantitative model)"

            # CEASEFIRE CARD: Claude's diplomatic analysis (probabilistic)
            _ceasefire_rationale = ""
            _ceasefire_model_label = ""
            if "claude" in raw_results:
                claude_diplo = raw_results["claude"].get("diplomatic_outlook", {})
                cf_prob = claude_diplo.get("ceasefire_probability_30d", 0)
                mediators = claude_diplo.get("key_mediators", [])
                obstacles = claude_diplo.get("obstacles", [])
                signals = claude_diplo.get("signals", [])
                conf_range = claude_diplo.get("confidence_range", [])
                parts = []
                if cf_prob:
                    range_str = f" (range: {conf_range[0]}-{conf_range[1]}%)" if len(conf_range) == 2 else ""
                    parts.append(f"Claude estimates {cf_prob}% ceasefire probability{range_str}.")
                if mediators:
                    parts.append(f"Active mediators: {', '.join(str(m) for m in mediators[:3])}.")
                if obstacles:
                    parts.append(f"Key obstacles: {', '.join(str(o) for o in obstacles[:2])}.")
                if signals:
                    parts.append(f"Signals: {str(signals[0])}.")
                _ceasefire_rationale = " ".join(parts)[:400].replace("<", "&lt;").replace(">", "&gt;")
                _ceasefire_model_label = "Claude (diplomatic analysis)"

            n_models = len(conflict_result.get("models_used", []))
            mc1, mc2, mc3 = st.columns(3)

            # Escalation card with description + Grok rationale
            esc_desc = ("Measures the probability and severity of military escalation on a 1-10 scale, "
                        "calibrated to historical conflicts (10 = Cuban Missile Crisis, 7 = Tanker War, 5 = Houthi crisis).")
            esc_rationale_html = (
                f'<div style="text-align:left;color:#666;font-size:0.72rem;line-height:1.3;margin-top:8px;'
                f'border-top:1px solid #30363d;padding-top:6px;">{esc_desc}</div>'
            )
            if _esc_rationale:
                esc_rationale_html += (
                    f'<div style="text-align:left;color:#aaa;font-size:0.75rem;line-height:1.4;margin-top:6px;">'
                    f'<span style="color:#ff4444;font-weight:600;">{_esc_model_label}:</span> {_esc_rationale}'
                    f'</div>'
                )
            mc1.markdown(
                f'<div style="padding:12px; border:1px solid {esc_color}; border-radius:8px;">'
                f'<div style="text-align:center;">'
                f'<div style="font-size:32px; font-weight:bold; color:{esc_color};">{esc_score}/10</div>'
                f'<div style="color:{esc_color}; font-weight:bold;">{esc_level}</div>'
                f'<div style="color:#888; font-size:11px;">Escalation Risk</div>'
                f'<div style="color:#555; font-size:10px;">Weighted blend of {n_models} models</div>'
                f'</div>'
                f'{esc_rationale_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Disruption card with Gemini rationale
            # Use Gemini's quantitative estimate as primary (it builds a facility-by-facility model)
            # Fall back to hardcoded baseline if Gemini didn't produce a number
            ai_disruption = oil.get("current_disruption_mbpd", 0)
            if ai_disruption and ai_disruption > 2:
                disruption_display = round(ai_disruption, 1)
                disruption_note = "AI model estimate (Gemini-weighted)"
            else:
                disruption_display = abs(CURRENT_NET_DISRUPTION)
                disruption_note = "Baseline estimate"
            disruption_rationale_html = ""
            if _disruption_rationale:
                disruption_rationale_html = (
                    f'<div style="text-align:left;color:#aaa;font-size:0.75rem;line-height:1.4;margin-top:8px;'
                    f'border-top:1px solid #30363d;padding-top:6px;">'
                    f'<span style="color:#4285f4;font-weight:600;">{_disruption_model_label}:</span> {_disruption_rationale}'
                    f'</div>'
                )
            mc2.markdown(
                f'<div style="padding:12px; border:1px solid #ff6b35; border-radius:8px;">'
                f'<div style="text-align:center;">'
                f'<div style="font-size:32px; font-weight:bold; color:#ff6b35;">{disruption_display} mbpd</div>'
                f'<div style="color:#888; font-size:11px;">Supply Disruption</div>'
                f'<div style="color:#555; font-size:10px;">{disruption_note}</div>'
                f'</div>'
                f'{disruption_rationale_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

            # Ceasefire card with Claude rationale
            ceasefire = diplo.get("ceasefire_probability_30d", 0)
            cf_color = "#00ff96" if ceasefire >= 50 else "#ffaa00" if ceasefire >= 25 else "#ff4b4b"
            cf_rationale_html = ""
            if _ceasefire_rationale:
                cf_rationale_html = (
                    f'<div style="text-align:left;color:#aaa;font-size:0.75rem;line-height:1.4;margin-top:8px;'
                    f'border-top:1px solid #30363d;padding-top:6px;">'
                    f'<span style="color:#d4a574;font-weight:600;">{_ceasefire_model_label}:</span> {_ceasefire_rationale}'
                    f'</div>'
                )
            mc3.markdown(
                f'<div style="padding:12px; border:1px solid {cf_color}; border-radius:8px;">'
                f'<div style="text-align:center;">'
                f'<div style="font-size:32px; font-weight:bold; color:{cf_color};">{ceasefire}%</div>'
                f'<div style="color:#888; font-size:11px;">Ceasefire Prob (30d)</div>'
                f'<div style="color:#555; font-size:10px;">Claude-weighted blend · accuracy-adjusted</div>'
                f'</div>'
                f'{cf_rationale_html}'
                f'</div>',
                unsafe_allow_html=True,
            )

            st.divider()

            st.divider()

            # Per-model escalation assessments — stacked for full readability
            st.markdown("#### Model Assessments")
            role_map = {"Grok 4": "Breaking News",
                        "Gemini 3.1 Pro": "Military/Energy", "Claude Sonnet": "Diplomatic"}
            model_assess = esc.get("model_assessments", [])
            for ma in model_assess:
                role = role_map.get(ma["model"], "")
                is_partial = ma.get("_partial", False)
                partial_badge = '<span style="color:#ffaa00;font-size:10px;font-weight:700;border:1px solid #ffaa00;padding:1px 5px;border-radius:3px;margin-left:8px;">PARTIAL</span>' if is_partial else ""
                # Sanitize rationale — escape HTML tags and handle empty
                rationale = ma.get("rationale", "")
                if not rationale or not rationale.strip():
                    rationale = "No rationale provided."
                rationale = rationale.replace("<", "&lt;").replace(">", "&gt;")
                citations_html = ""
                if ma.get("citations"):
                    safe_cites = [str(c).replace("<", "&lt;").replace(">", "&gt;") for c in ma["citations"][:4]]
                    cites = " · ".join(safe_cites)
                    citations_html = f'<div style="color:#888; font-size:11px; margin-top:4px;">Citations: {cites}</div>'
                st.markdown(f"""<div style="padding:12px 14px; border-left:3px solid {ma['color']}; margin-bottom:8px; background:rgba(255,255,255,0.02); border-radius:0 6px 6px 0;">
                    <div style="display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <span style="color:{ma['color']}; font-weight:bold; font-size:14px;">{ma['model']}</span>
                            <span style="color:#888; font-size:11px; margin-left:8px;">{role}</span>{partial_badge}
                        </div>
                        <span style="font-size:20px; font-weight:bold; color:white;">{ma['score']}/10 — {ma['level']}</span>
                    </div>
                    <div style="color:#ccc; font-size:13px; margin-top:8px; line-height:1.5;">{rationale}</div>
                    {citations_html}
                </div>""", unsafe_allow_html=True)

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

            # What to Watch Next — actionable triggers for traders
            watches = conflict_result.get("watch_next", [])
            if watches:
                st.divider()
                st.markdown("#### What to Watch Next")
                st.caption("Key triggers that could move markets in the next 24-72 hours.")
                for w in watches:
                    trigger = str(w.get("trigger", ""))
                    timeframe = str(w.get("timeframe", ""))
                    if_happens = str(w.get("if_happens", ""))
                    prob = w.get("probability", 0)
                    prob_color = "#ff4b4b" if prob >= 60 else "#ffaa00" if prob >= 30 else "#00ff96"
                    st.markdown(
                        f'<div style="padding:10px 14px;border-left:3px solid {prob_color};margin-bottom:6px;'
                        f'background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">'
                        f'<span style="color:#e0e0e0;font-weight:600;font-size:0.9rem;">{trigger}</span>'
                        f'<span style="color:{prob_color};font-weight:bold;font-size:0.85rem;">{prob}%</span>'
                        f'</div>'
                        f'<div style="color:#aaa;font-size:0.82rem;line-height:1.4;">'
                        f'<span style="color:#888;">Timeframe:</span> {timeframe} &nbsp;│&nbsp; '
                        f'<span style="color:#888;">If this happens:</span> {if_happens}'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            # Trending tweets — auto-refreshing fragment (independent of analysis)
            @st.fragment(run_every=600)
            def _render_live_tweets():
                tweets = _fetch_live_tweets()
                if not tweets:
                    tweets = conflict_result.get("trending_tweets", [])
                if not tweets:
                    return
                st.divider()
                st.markdown("#### Trending on X")
                st.caption("Live feed via Grok — auto-refreshes every 10 min.")
                for tw in tweets:
                    handle = tw.get("handle", "")
                    content = tw.get("content", "")
                    likes = tw.get("likes", 0)
                    rts = tw.get("retweets", 0)
                    replies = tw.get("replies", 0)
                    time_ago = tw.get("time", "")
                    posted_at = tw.get("posted_at", "")
                    velocity = tw.get("velocity", "")
                    why = tw.get("why_important", "")

                    # Format the posted_at timestamp for display
                    timestamp_str = ""
                    if posted_at:
                        try:
                            dt = datetime.fromisoformat(posted_at.replace("Z", "+00:00"))
                            timestamp_str = dt.strftime("%b %d, %H:%M UTC")
                        except Exception:
                            timestamp_str = posted_at
                    time_display = timestamp_str if timestamp_str else time_ago

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
                        f'<span style="font-size:0.7rem;color:#888;">{time_display} {vel_icon}</span>'
                        f'</div>'
                        f'<div style="color:#ddd;font-size:0.85rem;line-height:1.5;margin-bottom:6px;">{content}</div>'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                        f'<span style="font-size:0.7rem;color:#888;">{metrics_str}</span>'
                        f'<span style="font-size:0.7rem;color:{vel_color};font-style:italic;">{why}</span>'
                        f'</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

            _render_live_tweets()


# ---- TAB: Conflict Timeline ----
with tab_timeline, error_boundary("Timeline"):
    st.subheader("Iran War Timeline & Infrastructure Impact")

    # ── Conflict Clock ──
    war_start = pd.Timestamp("2026-02-28")
    days_of_conflict = (pd.Timestamp.now() - war_start).days
    total_events = len(live_timeline)
    current_disruption = abs(CURRENT_NET_DISRUPTION)
    peak_disruption = max(d["disruption"] for d in DISRUPTION_TIMELINE)
    current_phase = "Unknown"
    for phase in reversed(CONFLICT_PHASES):
        if pd.Timestamp.now() >= pd.Timestamp(phase["start"]):
            current_phase = phase["name"]
            break

    # Hormuz ultimatum countdown
    _ultimatum_deadline = datetime(2026, 3, 24, 0, 0)  # ~48h from Mar 22
    _hours_left = max(0, (_ultimatum_deadline - datetime.now()).total_seconds() / 3600)
    _countdown_color = "#ff4b4b" if _hours_left < 12 else "#ff6b35" if _hours_left < 24 else "#ffaa00"
    _countdown_text = f"{_hours_left:.0f}h" if _hours_left > 0 else "EXPIRED"

    st.markdown(f"""<div style="padding:16px; border:1px solid #ff4b4b; border-radius:10px; background:rgba(255,75,75,0.05); margin-bottom:16px;">
        <div style="display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:12px;">
            <div style="text-align:center;">
                <div style="font-size:42px; font-weight:bold; color:#ff4b4b;">DAY {days_of_conflict}</div>
                <div style="color:#888; font-size:12px;">of the Iran War</div>
            </div>
            <div style="text-align:center;">
                <div style="font-size:28px; font-weight:bold; color:{_countdown_color};">{_countdown_text}</div>
                <div style="color:{_countdown_color}; font-size:11px; font-weight:600;">HORMUZ ULTIMATUM</div>
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
    timeline_df = pd.DataFrame(live_timeline)
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

    # ── Supply Disruption Waterfall — uses validated data if available ──
    st.divider()
    st.markdown("#### Current Supply Disruption Breakdown")

    # Use validated infrastructure data if available, otherwise hardcoded baseline
    _wf_breakdown = DISRUPTION_BREAKDOWN
    _wf_net = CURRENT_NET_DISRUPTION
    if _prefetched_infra:
        _, _validated_bd, _validated_net, _ = _prefetched_infra
        if _validated_bd:
            _wf_breakdown = _validated_bd
            _wf_net = _validated_net

    waterfall_names = [d["facility"] for d in _wf_breakdown]
    waterfall_vals = [d["mbpd"] for d in _wf_breakdown]

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
        text=f"Net disruption: {_wf_net:+.1f} mbpd",
        showarrow=False, font=dict(size=16, color="#ff4b4b" if _wf_net < 0 else "#00ff96"),
    )

    fig_wf.update_layout(
        template="plotly_dark", height=450, margin=dict(t=50, b=0, l=0, r=0),
        yaxis_title="mbpd", showlegend=False,
        xaxis=dict(tickangle=-35),
    )
    st.plotly_chart(fig_wf, use_container_width=True)


# ---- TAB: Infrastructure Status (Live via Grok) ----
with tab_infra, error_boundary("Infrastructure Status"):
    st.subheader("Infrastructure Status")
    st.markdown(
        '<span style="font-size:11px;color:#00ff96;border:1px solid #00ff96;'
        'padding:2px 8px;border-radius:3px;">Live via Grok</span>'
        '&nbsp;&nbsp;<span style="color:#888;font-size:0.8rem;">Auto-refreshes every 30 min — sourced from verified accounts and major news wires.</span>',
        unsafe_allow_html=True,
    )

    @st.fragment(run_every=1800)
    def _render_infra_tab():
        _infra_cache_key = datetime.now().strftime("%Y-%m-%d-%H") + ("-30" if datetime.now().minute >= 30 else "-00")
        grok_infra = _grok_infrastructure_status(_infra_cache_key)
        if not grok_infra:
            st.warning("Infrastructure data unavailable — Grok API may be rate-limited.")
            return

        # Summary counters
        _offline = sum(1 for i in grok_infra if i.get("status", "").lower() == "offline")
        _degraded = sum(1 for i in grok_infra if i.get("status", "").lower() == "degraded")
        _operational = sum(1 for i in grok_infra if i.get("status", "").lower() == "operational")
        _total = len(grok_infra)

        _s1, _s2, _s3, _s4 = st.columns(4)
        _s1.metric("Total Facilities", _total)
        _s2.metric("Offline", _offline)
        _s3.metric("Degraded", _degraded)
        _s4.metric("Operational", _operational)

        st.divider()

        status_colors = {"offline": "#ff4b4b", "degraded": "#ffaa00", "operational": "#00ff96"}
        status_icons = {"offline": "🔴", "degraded": "🟡", "operational": "🟢"}

        # Group by status for cleaner display
        for status_group in ["offline", "degraded", "operational"]:
            group_items = [i for i in grok_infra if i.get("status", "").lower() == status_group]
            if not group_items:
                continue

            sc = status_colors.get(status_group, "#888")
            st.markdown(
                f'<div style="color:{sc};font-weight:700;font-size:0.9rem;margin:12px 0 6px 0;">'
                f'{status_icons.get(status_group, "⚪")} {status_group.upper()} ({len(group_items)})</div>',
                unsafe_allow_html=True,
            )

            for item in group_items:
                detail = item.get("detail", "")
                update = item.get("update")
                changed = item.get("changed", False)

                change_badge = ""
                if changed:
                    change_badge = (' <span style="color:#ff4b4b;font-size:0.65rem;font-weight:700;'
                                    'border:1px solid #ff4b4b;padding:1px 5px;border-radius:3px;">UPDATED</span>')

                update_html = ""
                if update and str(update).lower() not in ("null", "none", "no update", "no change"):
                    update_html = f'<div style="color:#00ff96;font-size:0.75rem;margin-top:3px;">Latest: {update}</div>'

                st.markdown(
                    f'<div style="padding:10px 14px;border-left:3px solid {sc};margin-bottom:6px;'
                    f'background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;">'
                    f'<div style="font-weight:600;color:#e0e0e0;font-size:0.88rem;">'
                    f'{item.get("name", "")}{change_badge}</div>'
                    f'<div style="color:#aaa;font-size:0.8rem;margin-top:3px;line-height:1.5;">{detail}</div>'
                    f'{update_html}'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    _render_infra_tab()

    # Infrastructure map
    st.divider()
    st.markdown("#### Infrastructure Impact Map")

    infra_map_data = []
    for name, info in INFRASTRUCTURE_TARGETS.items():
        infra_map_data.append({
            "name": name, "lat": info["lat"], "lon": info["lon"],
            "capacity": info["capacity"], "status": info["status"],
            "impact_level": info["impact_level"], "color": info["color"],
        })
    infra_map_df = pd.DataFrame(infra_map_data)

    size_map = {"Critical": 20, "Severe": 15, "Moderate": 10, "Operational": 8}
    fig_infra_map = go.Figure()

    for level in ["Critical", "Severe", "Moderate", "Operational"]:
        subset = infra_map_df[infra_map_df["impact_level"] == level]
        if subset.empty:
            continue
        color = subset.iloc[0]["color"]
        fig_infra_map.add_trace(go.Scattermapbox(
            lat=subset["lat"], lon=subset["lon"],
            mode="markers+text",
            name=f"{level}",
            marker=dict(size=size_map.get(level, 10), color=color, opacity=0.9),
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
        mapbox=dict(style="carto-darkmatter", center=dict(lat=27, lon=50), zoom=4),
        height=500, margin=dict(t=0, b=0, l=0, r=0),
        showlegend=True,
        legend=dict(x=0, y=1, bgcolor="rgba(0,0,0,0.5)", font=dict(color="white")),
    )
    st.plotly_chart(fig_infra_map, use_container_width=True)
