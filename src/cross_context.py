"""Cross-page context sharing — lightweight session-state bridge.

Pages WRITE their key outputs after computation.
Pages READ available context to enrich their own analysis.

Usage:
    from src.cross_context import write_context, read_context, build_ai_context

    # Producer page (e.g., Market Expectations):
    write_context("market_expectations", {
        "regime": "Elevated Vol",
        "avg_ivhv": 1.25,
        "implied_corr": 0.62,
        ...
    })

    # Consumer page (e.g., Stock Analysis):
    ctx = read_context("market_expectations")
    if ctx:
        prompt += f"\\nMarket vol regime: {ctx['regime']}"

    # Or get everything as a formatted string for AI prompts:
    ai_context = build_ai_context(ticker="AAPL")
"""
import streamlit as st
import pandas as pd
from datetime import datetime


_CONTEXT_KEY = "_cross_page_context"


def write_context(source: str, data: dict):
    """Write context from a producer page. Overwrites previous data for this source."""
    if _CONTEXT_KEY not in st.session_state:
        st.session_state[_CONTEXT_KEY] = {}
    st.session_state[_CONTEXT_KEY][source] = {
        "data": data,
        "updated_at": datetime.now().isoformat(),
    }


def read_context(source: str) -> dict | None:
    """Read context from a producer page. Returns None if not available."""
    store = st.session_state.get(_CONTEXT_KEY, {})
    entry = store.get(source)
    if entry:
        return entry["data"]
    return None


def read_context_age(source: str) -> float | None:
    """How many minutes since this context was last updated. None if not available."""
    store = st.session_state.get(_CONTEXT_KEY, {})
    entry = store.get(source)
    if entry:
        updated = datetime.fromisoformat(entry["updated_at"])
        return (datetime.now() - updated).total_seconds() / 60
    return None


def list_available() -> list[str]:
    """List all context sources that have been written this session."""
    store = st.session_state.get(_CONTEXT_KEY, {})
    return list(store.keys())


def build_ai_context(ticker: str = None) -> str:
    """Build a formatted context string for AI model prompts.

    Aggregates all available cross-page context into a concise summary
    that can be appended to any AI prompt for richer analysis.
    """
    lines = []
    store = st.session_state.get(_CONTEXT_KEY, {})
    if not store:
        return ""

    lines.append("CROSS-PLATFORM CONTEXT (from other analysis pages this session):")

    # Market Expectations
    me = read_context("market_expectations")
    if me:
        lines.append(f"\nMARKET VOL REGIME: {me.get('regime', 'Unknown')}")
        lines.append(f"  Avg IV/HV: {me.get('avg_ivhv', '?')}x | Avg IV Pctile: {me.get('avg_pctile', '?')}%")
        if me.get("implied_corr") is not None:
            lines.append(f"  Implied Correlation: {me['implied_corr']:.2f}")
        inverted = me.get("inverted_assets", [])
        if inverted:
            lines.append(f"  Assets in backwardation (near-term fear): {', '.join(inverted)}")
        rich = me.get("richest_iv", [])
        cheap = me.get("cheapest_iv", [])
        if rich:
            lines.append(f"  Richest IV (sell premium): {', '.join(rich)}")
        if cheap:
            lines.append(f"  Cheapest IV (buy protection): {', '.join(cheap)}")
        vix_term = me.get("vix_term", {})
        if vix_term.get("VIX"):
            _vix_line = f"  VIX: {vix_term['VIX']} | VIX3M: {vix_term.get('VIX3M', '?')}"
            if vix_term.get("SKEW"):
                _vix_line += f" | SKEW: {vix_term['SKEW']}"
            _vix_line += f" | Regime: {vix_term.get('regime', '?')}"
            lines.append(_vix_line)
            if vix_term.get("divergence"):
                lines.append(f"  VIX-SKEW Signal: {vix_term['divergence']}")
        fed_liq = me.get("fed_liquidity", {})
        if fed_liq.get("net_liquidity"):
            lines.append(f"  Fed Net Liquidity: ${fed_liq['net_liquidity']}T "
                          f"({'draining' if fed_liq.get('draining') else 'expanding'})")

    # Fed & Macro Drivers
    fed = read_context("fed_macro")
    if fed:
        lines.append(f"\nMACRO SIGNAL MATRIX:")
        for signal_name, signal_val in fed.get("signals", {}).items():
            lines.append(f"  {signal_name}: {signal_val}")
        if fed.get("policy_stance"):
            lines.append(f"  Fed Policy Stance: {fed['policy_stance']}")

    # Signal Scanner (ticker-specific)
    scanner = read_context("signal_scanner")
    if scanner and ticker:
        scores = scanner.get("scores", {}).get(ticker)
        if scores:
            lines.append(f"\nSIGNAL SCANNER ({ticker}):")
            for factor, score in scores.items():
                lines.append(f"  {factor}: {score}")

    # Correlation context
    corr = read_context("correlation")
    if corr:
        breakdowns = corr.get("breakdowns", [])
        if breakdowns:
            lines.append(f"\nCORRELATION ALERTS: {len(breakdowns)} breakdown(s) detected")
            for bd in breakdowns[:3]:
                lines.append(f"  {bd}")

    # Iran Conflict
    iran = read_context("iran_conflict")
    if iran:
        lines.append(f"\nGEOPOLITICAL CONTEXT:")
        if iran.get("escalation") is not None:
            lines.append(f"  Escalation Score: {iran['escalation']}/10")
        if iran.get("oil_disruption") is not None:
            lines.append(f"  Oil Disruption: {iran['oil_disruption']} mbpd")
        if iran.get("ceasefire_prob") is not None:
            lines.append(f"  Ceasefire Probability: {iran['ceasefire_prob']}%")

    # Smart Money (ticker-specific)
    smart = read_context("smart_money")
    if smart and ticker:
        insider = smart.get("insider", {}).get(ticker)
        if insider:
            lines.append(f"\nINSIDER ACTIVITY ({ticker}):")
            lines.append(f"  {insider}")

    # Scenario Analysis
    scenario = read_context("scenario_analysis")
    if scenario:
        regime = scenario.get("top_regime")
        if regime:
            lines.append(f"\nMACRO REGIME (Grok): {regime.get('name', '?')} ({regime.get('probability', '?')}%)")

    # COT Positioning (if available via macro_data)
    try:
        from src.macro_data import get_cot_positioning_snapshot
        cot = get_cot_positioning_snapshot()
        if cot:
            lines.append(f"\nMANAGED MONEY POSITIONING (CFTC COT):")
            for contract, pos in cot.items():
                lines.append(f"  {contract}: {pos['direction']} ({pos['net']:+,} contracts, "
                              f"{pos['net_pct_oi']:+.1f}% of OI, Δ{pos['change']:+,} weekly)")
    except Exception:
        pass

    # Growth Proxies
    try:
        from src.macro_data import fetch_growth_proxies
        _gp = fetch_growth_proxies()
        if _gp.get("copper_gold"):
            lines.append(f"\nGROWTH PROXIES: Copper/Gold ratio {_gp['copper_gold']:.1f} ({_gp.get('copper_gold_trend', '?')}) — "
                          f"{'expansion signal' if _gp.get('copper_gold_trend') == 'rising' else 'contraction signal'}")
    except Exception:
        pass

    # OECD Leading Indicators
    try:
        from src.macro_data import fetch_oecd_cli
        _cli = fetch_oecd_cli(["USA", "OECD"])
        if not _cli.empty and "USA" in _cli.columns:
            _us_cli = _cli["USA"].dropna()
            if len(_us_cli) > 0:
                _val = float(_us_cli.iloc[-1])
                _trend = "rising" if len(_us_cli) > 1 and _us_cli.iloc[-1] > _us_cli.iloc[-2] else "falling"
                lines.append(f"\nOECD LEADING INDICATOR: US CLI {_val:.1f} ({_trend}) — "
                              f"{'above trend (expansion)' if _val > 100 else 'below trend (contraction risk)'}")
    except Exception:
        pass

    if len(lines) <= 1:
        return ""

    return "\n".join(lines)
