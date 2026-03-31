import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging

from src.layout import setup_page, error_boundary, fun_loader, get_active_ticker, set_active_ticker
from src.styles import COLORS
from src.data_engine import fetch_massive_data, get_expiration_dates, fetch_options_chain, format_massive_ticker
from src.options_models import fill_missing_options_data, bs_all_greeks, bs_higher_greeks, vanna_volga_decomposition
from src.api_keys import get_secret as _get_key

setup_page("49_Higher_Greeks")
logger = logging.getLogger(__name__)
PLOTLY_NOBAR = {"displayModeBar": False}

_card = (f'background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
         f'border-radius:10px;padding:16px 20px;margin-bottom:12px;')

# ─── HEADER ───────────────────────────────────────────────────────────────────

st.markdown(
    f'<div style="font-size:1.6rem;font-weight:800;color:#e6edf3;margin-bottom:2px;">Higher-Order Greeks</div>'
    f'<div style="font-size:0.85rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
    f'2nd and 3rd order options sensitivities. Vanna, charm, volga, speed, zomma, and more.</div>',
    unsafe_allow_html=True)

# ─── CONTROLS + DATA LOADING ─────────────────────────────────────────────────

_c1, _c2 = st.columns([3, 1])
with _c1:
    raw_ticker = st.text_input("Ticker", value=get_active_ticker("SPY"))
    ticker = format_massive_ticker(raw_ticker)
    set_active_ticker(ticker)
with _c2:
    st.markdown("<br>", unsafe_allow_html=True)
    submit = st.button("Load Chain", type="primary", use_container_width=True)

# ── Shared data: reuse Vol Surface chain if same ticker is loaded there ──
_vs_available = (st.session_state.get("vs_ticker") == ticker and
                  "vs_term" in st.session_state and "vs_spot" in st.session_state)

def _compute_higher_greeks(chains, spot_val, exps_list, rfr_val):
    """Compute all 12 Greeks for every contract in the chain."""
    hg_rows = []
    for exp in exps_list:
        chain = chains[exp]
        dte = max((pd.to_datetime(exp) - pd.Timestamp.now()).days, 1)
        T = dte / 365
        for _, row in chain.iterrows():
            K = row["strike_price"]
            iv = row.get("implied_volatility", 0) or 0
            otype = row["contract_type"]
            if iv <= 0 or K <= 0:
                continue
            greeks = bs_all_greeks(spot_val, K, T, rfr_val, iv, otype)
            greeks["strike"] = K
            greeks["expiration"] = exp
            greeks["dte"] = dte
            greeks["type"] = otype
            greeks["iv"] = iv
            greeks["oi"] = row.get("open_interest", 0) or 0
            hg_rows.append(greeks)
    return pd.DataFrame(hg_rows)

if submit or _vs_available:
    if _vs_available and not submit:
        # Reuse Vol Surface data — instant, no API calls
        chains = st.session_state["vs_term"]
        spot_val = st.session_state["vs_spot"]
        exps_list = st.session_state["vs_exps"]
        px_df = st.session_state.get("vs_px")
        rfr_val = 0.045
        try:
            from src.market_data import fetch_fred_series
            _fr = fetch_fred_series("DGS3MO", periods=5)
            if not _fr.empty:
                rfr_val = _fr["value"].iloc[-1] / 100
        except Exception:
            pass
        hg_df = _compute_higher_greeks(chains, spot_val, exps_list, rfr_val)
        st.session_state["hg_data"] = {"spot": spot_val, "chains": chains, "exps": exps_list,
                                        "hg_df": hg_df, "rfr": rfr_val, "px_df": px_df}
        st.session_state["hg_ticker"] = ticker
        st.toast("Loaded from Vol Surface (same ticker)")
    else:
        # Fresh fetch
        with fun_loader("data"):
            px_df = fetch_massive_data(ticker, 60)
            if px_df is None or px_df.empty:
                st.error("Could not fetch price data.")
                st.stop()
            spot_val = float(px_df["Close"].iloc[-1])

            all_exps = get_expiration_dates(ticker)
            today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
            valid_exps = [e for e in (all_exps or []) if e >= today_str]
            monthly = [e for e in valid_exps if 15 <= pd.to_datetime(e).day <= 21 and pd.to_datetime(e).weekday() == 4][:4]
            weekly = [e for e in valid_exps if e not in monthly][:2]
            prefetch = sorted(set(monthly + weekly))
            if len(prefetch) < 3:
                prefetch = valid_exps[:6]

            chains = {}
            rfr_val = 0.045
            try:
                from src.market_data import fetch_fred_series
                _fr = fetch_fred_series("DGS3MO", periods=5)
                if not _fr.empty:
                    rfr_val = _fr["value"].iloc[-1] / 100
            except Exception:
                pass

            from concurrent.futures import ThreadPoolExecutor, as_completed
            def _fetch(exp):
                try:
                    cdf = fetch_options_chain(ticker, exp)
                    if cdf is not None and not cdf.empty:
                        cdf = fill_missing_options_data(cdf, spot_val, risk_free_rate=rfr_val)
                        return exp, cdf
                except Exception:
                    pass
                return exp, None

            with ThreadPoolExecutor(max_workers=5) as pool:
                for fut in as_completed({pool.submit(_fetch, e): e for e in prefetch}):
                    exp, cdf = fut.result()
                    if cdf is not None:
                        chains[exp] = cdf

            if len(chains) < 1:
                st.error("Could not load chain data.")
                st.stop()

            exps_list = sorted(chains.keys())
            hg_df = _compute_higher_greeks(chains, spot_val, exps_list, rfr_val)
            st.session_state["hg_data"] = {"spot": spot_val, "chains": chains, "exps": exps_list,
                                            "hg_df": hg_df, "rfr": rfr_val, "px_df": px_df}
            st.session_state["hg_ticker"] = ticker

elif _vs_available and "hg_data" not in st.session_state:
    # Auto-load from Vol Surface on first visit if same ticker
    chains = st.session_state["vs_term"]
    spot_val = st.session_state["vs_spot"]
    exps_list = st.session_state["vs_exps"]
    px_df = st.session_state.get("vs_px")
    rfr_val = 0.045
    hg_df = _compute_higher_greeks(chains, spot_val, exps_list, rfr_val)
    st.session_state["hg_data"] = {"spot": spot_val, "chains": chains, "exps": exps_list,
                                    "hg_df": hg_df, "rfr": rfr_val, "px_df": px_df}
    st.session_state["hg_ticker"] = st.session_state["vs_ticker"]
    st.caption("Auto-loaded from Vol Surface data")

if "hg_data" not in st.session_state:
    st.info("Enter a ticker and click **Load Chain**, or load a surface on the **Vol Surface** page first.")
    st.stop()

_d = st.session_state["hg_data"]
spot = _d["spot"]
chains = _d["chains"]
exps = _d["exps"]
hg_df = _d["hg_df"]
rfr = _d["rfr"]
ticker_display = st.session_state["hg_ticker"]

if hg_df.empty:
    st.error("No Greeks data computed.")
    st.stop()

_info_c1, _info_c2 = st.columns([3, 1])
with _info_c1:
    st.caption(f"{ticker_display} | Spot: ${spot:,.2f} | {len(exps)} expirations | {len(hg_df)} contracts")
with _info_c2:
    st.markdown(f'<a href="/43_Vol_Surface?ticker={ticker_display}" target="_self" '
                f'style="font-size:0.8rem;color:{COLORS["accent"]};text-decoration:none;">'
                f'\u2190 Vol Surface</a>', unsafe_allow_html=True)

# ─── TABS ─────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Overview", "Vanna Profile", "Charm & Time", "Gamma Risk", "Vega Convexity", "VV Pricing", "Portfolio", "AI Analyst"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — OVERVIEW
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("Overview"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:8px;">Greek Family Tree</div>',
                    unsafe_allow_html=True)

        # Family tree
        _tree = [
            ("1st Order", [
                ("Delta (\u0394)", "Directional exposure per $1 move", COLORS["accent"]),
                ("Gamma (\u0393)", "Rate of delta change", COLORS["accent"]),
                ("Theta (\u0398)", "Time decay per day", COLORS["accent"]),
                ("Vega (\u03bd)", "Sensitivity to IV changes", COLORS["accent"]),
            ]),
            ("2nd Order", [
                ("Vanna", "\u2202\u0394/\u2202\u03c3 \u2014 How delta changes when IV moves. Drives the 'Post-FOMC Squeeze' \u2014 dealers unwind short futures as IV crushes.", COLORS["warning"]),
                ("Volga", "\u2202\u03bd/\u2202\u03c3 \u2014 How vega changes when IV moves. A short strangle book with high volga is a 'vomma bomb' in a vol spike.", COLORS["warning"]),
                ("Charm", "\u2202\u0394/\u2202t \u2014 Delta decay per day. Causes the 'Afternoon Melt-up' as OTM put deltas bleed and dealers cover shorts.", COLORS["warning"]),
                ("Veta", "\u2202\u03bd/\u2202t \u2014 How vega decays with time. Why long-dated options lose IV sensitivity as they age.", COLORS["warning"]),
            ]),
            ("3rd Order", [
                ("Speed", "\u2202\u0393/\u2202S \u2014 Gamma acceleration. High speed = pin risk near expiration. 0DTE options have extreme speed.", COLORS["danger"]),
                ("Zomma", "\u2202\u0393/\u2202\u03c3 \u2014 How gamma shifts when IV spikes. Ignoring zomma in a crisis = catastrophic under-hedging.", COLORS["danger"]),
                ("Color", "\u2202\u0393/\u2202t \u2014 Gamma decay. As expiration nears, gamma concentrates into a narrow ATM peak. Color tracks this acceleration.", COLORS["danger"]),
                ("Ultima", "\u2202\u00b3P/\u2202\u03c3\u00b3 \u2014 The 'vol of vol of vol.' Dictates structural integrity of a portfolio during binary events.", COLORS["danger"]),
            ]),
        ]
        for _order, _greeks in _tree:
            st.markdown(f'<div style="font-size:0.9rem;font-weight:700;color:#e6edf3;margin:10px 0 6px 0;">{_order}</div>', unsafe_allow_html=True)
            _html = '<div style="display:flex;gap:8px;flex-wrap:wrap;">'
            for _name, _desc, _color in _greeks:
                _html += (f'<div style="background:{COLORS["card_bg"]};border:1px solid {_color};border-radius:8px;'
                          f'padding:8px 12px;flex:1;min-width:180px;">'
                          f'<div style="font-size:0.85rem;font-weight:700;color:{_color};">{_name}</div>'
                          f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-top:2px;">{_desc}</div></div>')
            _html += '</div>'
            st.markdown(_html, unsafe_allow_html=True)

        # Interactive calculator
        st.divider()
        st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin-bottom:8px;">Single-Strike Calculator</div>',
                    unsafe_allow_html=True)
        _cc1, _cc2, _cc3, _cc4, _cc5 = st.columns(5)
        _calc_s = _cc1.number_input("Spot", value=float(spot), step=1.0, key="hg_calc_s")
        _calc_k = _cc2.number_input("Strike", value=float(round(spot)), step=1.0, key="hg_calc_k")
        _calc_t = _cc3.number_input("DTE", value=30, step=1, key="hg_calc_t")
        _calc_iv = _cc4.number_input("IV (%)", value=25.0, step=1.0, key="hg_calc_iv")
        _calc_type = _cc5.selectbox("Type", ["call", "put"], key="hg_calc_type")

        if _calc_s > 0 and _calc_k > 0 and _calc_t > 0 and _calc_iv > 0:
            _all = bs_all_greeks(_calc_s, _calc_k, _calc_t / 365, rfr, _calc_iv / 100, _calc_type)
            _cols = st.columns(4)
            _idx = 0
            for _gn, _gv in _all.items():
                _order_color = COLORS["accent"] if _gn in ("delta", "gamma", "theta", "vega") else (COLORS["warning"] if _gn in ("vanna", "volga", "charm", "veta") else COLORS["danger"])
                _cols[_idx % 4].markdown(
                    f'<div style="background:{COLORS["card_bg"]};border:1px solid {_order_color};border-radius:6px;'
                    f'padding:6px 10px;text-align:center;margin-bottom:6px;">'
                    f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">{_gn}</div>'
                    f'<div style="font-size:1rem;font-weight:700;color:{_order_color};">{_gv:.6f}</div></div>',
                    unsafe_allow_html=True)
                _idx += 1


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — VANNA PROFILE
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    with error_boundary("Vanna Profile"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Vanna Profile</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'How delta changes when IV moves. Large vanna = delta is highly sensitive to vol shifts. '
                    f'Critical before earnings or any event that will move IV.</div>',
                    unsafe_allow_html=True)

        _exp_sel = st.selectbox("Expiration", exps, key="hg_vanna_exp")
        _vdf = hg_df[hg_df["expiration"] == _exp_sel].copy()
        _dte = _vdf["dte"].iloc[0] if not _vdf.empty else 0
        _strike_lo, _strike_hi = spot * 0.85, spot * 1.15
        _vdf = _vdf[(_vdf["strike"] >= _strike_lo) & (_vdf["strike"] <= _strike_hi)]

        if not _vdf.empty:
            fig_vanna = go.Figure()
            for otype, color, name in [("call", COLORS["success"], "Call Vanna"), ("put", COLORS["danger"], "Put Vanna")]:
                _sub = _vdf[_vdf["type"] == otype].sort_values("strike")
                if not _sub.empty:
                    fig_vanna.add_trace(go.Scatter(
                        x=_sub["strike"], y=_sub["vanna"], mode="lines",
                        line=dict(color=color, width=2), name=name,
                        hovertemplate="K=$%{x:.0f}<br>Vanna=%{y:.6f}<extra></extra>"))
            fig_vanna.add_vline(x=spot, line_dash="dash", line_color=COLORS["warning"], annotation_text=f"Spot ${spot:,.0f}")
            fig_vanna.update_layout(template="plotly_dark", height=400, margin=dict(l=0, r=0, t=10, b=0),
                                     xaxis_title="Strike ($)", yaxis_title="Vanna", legend=dict(orientation="h", y=-0.15))
            st.plotly_chart(fig_vanna, use_container_width=True, config=PLOTLY_NOBAR)

            # Hotspots
            _top_vanna = _vdf.nlargest(3, "vanna")[["strike", "type", "vanna", "iv"]]
            _bot_vanna = _vdf.nsmallest(3, "vanna")[["strike", "type", "vanna", "iv"]]
            _hc1, _hc2 = st.columns(2)
            with _hc1:
                st.markdown(f'<div style="{_card}border-left:3px solid {COLORS["success"]};padding:10px 14px;">'
                            f'<div style="font-size:0.82rem;font-weight:600;color:{COLORS["success"]};">Highest Vanna (long gamma gains from IV rise)</div>', unsafe_allow_html=True)
                for _, r in _top_vanna.iterrows():
                    st.markdown(f'<div style="font-size:0.78rem;color:#ccc;">${r["strike"]:.0f} {r["type"]} \u2014 vanna={r["vanna"]:.6f} IV={r["iv"]:.1%}</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)
            with _hc2:
                st.markdown(f'<div style="{_card}border-left:3px solid {COLORS["danger"]};padding:10px 14px;">'
                            f'<div style="font-size:0.82rem;font-weight:600;color:{COLORS["danger"]};">Most Negative Vanna (delta drops if IV rises)</div>', unsafe_allow_html=True)
                for _, r in _bot_vanna.iterrows():
                    st.markdown(f'<div style="font-size:0.78rem;color:#ccc;">${r["strike"]:.0f} {r["type"]} \u2014 vanna={r["vanna"]:.6f}</div>', unsafe_allow_html=True)
                st.markdown('</div>', unsafe_allow_html=True)

            # Vanna trade signal
            st.divider()
            _put_vanna = _vdf[_vdf["type"] == "put"]["vanna"].sum()
            _call_vanna = _vdf[_vdf["type"] == "call"]["vanna"].sum()
            _net_vanna = _put_vanna + _call_vanna
            _dte_v = _vdf["dte"].iloc[0]

            _vanna_signals = []
            if abs(_put_vanna) > abs(_call_vanna) * 1.5:
                _vanna_signals.append(("Put Skew Vanna Dominant", "#ff4444",
                    f"Put-side vanna ({_put_vanna:.4f}) vastly exceeds call-side ({_call_vanna:.4f}). "
                    f"If IV spikes (earnings, macro shock), OTM put deltas will shift aggressively. "
                    f"Trade: Sell OTM put spreads to harvest the overpriced vanna premium, or buy the high-vanna puts as crash protection."))
            if _dte_v <= 7 and abs(_net_vanna) > 0.01:
                _vanna_signals.append(("Near-Term Vanna Risk", "#ffaa00",
                    f"With only {_dte_v} DTE and net vanna of {_net_vanna:.4f}, any IV move will cause rapid delta shifts. "
                    f"Trade: Roll to longer-dated options to reduce vanna exposure, or flatten delta before any catalyst."))
            if not _vanna_signals:
                _vanna_signals.append(("Vanna Balanced", "#e6edf3",
                    "Put and call vanna are roughly balanced. No extreme skew sensitivity detected."))

            for _title, _color, _desc in _vanna_signals:
                st.markdown(f'<div style="{_card}border-left:4px solid {_color};padding:10px 14px;">'
                            f'<div style="font-size:0.85rem;font-weight:700;color:{_color};">{_title}</div>'
                            f'<div style="font-size:0.78rem;color:#ccc;margin-top:4px;">{_desc}</div></div>',
                            unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — CHARM & TIME RISK
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    with error_boundary("Charm"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Charm & Time Risk</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'How delta changes overnight. If you are delta-hedged at 4pm, charm tells you how far off you will be at 9:30am.</div>',
                    unsafe_allow_html=True)

        _exp_sel3 = st.selectbox("Expiration", exps, key="hg_charm_exp")
        _cdf = hg_df[hg_df["expiration"] == _exp_sel3].copy()
        _cdf = _cdf[(_cdf["strike"] >= spot * 0.85) & (_cdf["strike"] <= spot * 1.15)]

        if not _cdf.empty:
            fig_charm = go.Figure()
            for otype, color in [("call", COLORS["success"]), ("put", COLORS["danger"])]:
                _sub = _cdf[_cdf["type"] == otype].sort_values("strike")
                if not _sub.empty:
                    fig_charm.add_trace(go.Scatter(
                        x=_sub["strike"], y=_sub["charm"], mode="lines",
                        line=dict(color=color, width=2), name=f"{otype.title()} Charm",
                        hovertemplate="K=$%{x:.0f}<br>Charm=%{y:.6f}/day<extra></extra>"))
            fig_charm.add_vline(x=spot, line_dash="dash", line_color=COLORS["warning"])
            fig_charm.add_hline(y=0, line_dash="dot", line_color=COLORS["text_muted"])
            fig_charm.update_layout(template="plotly_dark", height=350, margin=dict(l=0, r=0, t=10, b=0),
                                     xaxis_title="Strike ($)", yaxis_title="Charm (\u2202\u0394/\u2202t per day)",
                                     legend=dict(orientation="h", y=-0.15))
            st.plotly_chart(fig_charm, use_container_width=True, config=PLOTLY_NOBAR)

            # Forward delta projection for ATM
            st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin:12px 0 6px 0;">Forward Delta Projection (ATM Call)</div>',
                        unsafe_allow_html=True)
            _atm_calls = _cdf[(_cdf["type"] == "call") & ((_cdf["strike"] - spot).abs() < spot * 0.02)]
            if not _atm_calls.empty:
                _atm = _atm_calls.iloc[0]
                _days_fwd = [0, 1, 2, 3, 5, 7]
                _deltas = []
                _dte_now = _atm["dte"]
                for d in _days_fwd:
                    _T_fwd = max((_dte_now - d) / 365, 0.001)
                    _fwd = bs_all_greeks(spot, _atm["strike"], _T_fwd, rfr, _atm["iv"], "call")
                    _deltas.append(_fwd["delta"])

                fig_fwd = go.Figure(go.Scatter(
                    x=_days_fwd, y=_deltas, mode="lines+markers",
                    line=dict(color=COLORS["accent"], width=2), marker=dict(size=8),
                    hovertemplate="Day +%{x}<br>Delta: %{y:.4f}<extra></extra>"))
                fig_fwd.update_layout(template="plotly_dark", height=250, margin=dict(l=0, r=0, t=10, b=0),
                                       xaxis_title="Days Forward", yaxis_title="Delta",
                                       xaxis=dict(tickvals=_days_fwd))
                st.plotly_chart(fig_fwd, use_container_width=True, config=PLOTLY_NOBAR)
                _drift_1d = _deltas[1] - _deltas[0]
                _drift_shares = round(_drift_1d * 100)  # per contract
                st.markdown(f'<div style="{_card}border-left:3px solid {COLORS["accent"]};padding:10px 14px;">'
                            f'<div style="font-size:0.82rem;color:#ccc;">'
                            f'ATM ${_atm["strike"]:.0f} call: delta today = {_atm["delta"]:.4f}. '
                            f'Tomorrow = {_deltas[1]:.4f} (drift of {_drift_1d:+.4f}). '
                            f'In 7 days = {_deltas[-1]:.4f}.</div></div>', unsafe_allow_html=True)

                # Hedge recommendation
                if abs(_drift_shares) >= 1:
                    _hedge_action = "sell" if _drift_shares > 0 else "buy"
                    st.markdown(f'<div style="{_card}border-left:4px solid {COLORS["warning"]};padding:10px 14px;">'
                                f'<div style="font-size:0.85rem;font-weight:700;color:{COLORS["warning"]};">Overnight Hedge</div>'
                                f'<div style="font-size:0.82rem;color:#ccc;margin-top:4px;">'
                                f'Per contract: delta will drift {_drift_1d:+.4f} overnight. '
                                f'To stay delta-neutral, <b>{_hedge_action} {abs(_drift_shares)} shares</b> '
                                f'at close (~${spot * abs(_drift_shares):,.0f} notional). '
                                f'Over a weekend (2 days): {_hedge_action} ~{abs(_drift_shares * 2)} shares.</div></div>',
                                unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — GAMMA RISK MAP
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    with error_boundary("Gamma Risk"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Gamma Risk Map</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Speed shows where gamma accelerates (pin risk). Zomma shows how gamma shifts when IV spikes.</div>',
                    unsafe_allow_html=True)

        _exp_sel4 = st.selectbox("Expiration", exps, key="hg_grisk_exp")
        _gdf = hg_df[(hg_df["expiration"] == _exp_sel4) & (hg_df["type"] == "call")].copy()
        _gdf = _gdf[(_gdf["strike"] >= spot * 0.85) & (_gdf["strike"] <= spot * 1.15)].sort_values("strike")

        if not _gdf.empty:
            # Speed chart
            fig_speed = go.Figure(go.Bar(
                x=_gdf["strike"], y=_gdf["speed"],
                marker_color=[COLORS["danger"] if abs(v) > _gdf["speed"].abs().quantile(0.8) else COLORS["accent"] for v in _gdf["speed"]],
                hovertemplate="K=$%{x:.0f}<br>Speed=%{y:.8f}<extra></extra>"))
            fig_speed.add_vline(x=spot, line_dash="dash", line_color=COLORS["warning"])
            fig_speed.update_layout(template="plotly_dark", height=280, margin=dict(l=0, r=0, t=10, b=0),
                                     xaxis_title="Strike ($)", yaxis_title="Speed (\u2202\u0393/\u2202S)")
            st.plotly_chart(fig_speed, use_container_width=True, config=PLOTLY_NOBAR)

            # Zomma heatmap: strike x IV scenario
            st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin:12px 0 6px 0;">Gamma Under IV Scenarios (via Zomma)</div>',
                        unsafe_allow_html=True)
            _iv_shifts = np.arange(-0.10, 0.11, 0.02)  # -10% to +10% IV shift
            _strikes = _gdf["strike"].values
            _dte_val = _gdf["dte"].iloc[0]
            _T_val = _dte_val / 365

            _gamma_grid = np.zeros((len(_iv_shifts), len(_strikes)))
            for i, dv in enumerate(_iv_shifts):
                for j, K in enumerate(_strikes):
                    _base_iv = _gdf[_gdf["strike"] == K]["iv"].values[0]
                    _shifted_iv = max(0.01, _base_iv + dv)
                    _g = bs_all_greeks(spot, K, _T_val, rfr, _shifted_iv, "call")
                    _gamma_grid[i, j] = _g["gamma"]

            fig_zg = go.Figure(go.Heatmap(
                z=_gamma_grid, x=[f"${k:.0f}" for k in _strikes],
                y=[f"{v:+.0%}" for v in _iv_shifts],
                colorscale="Viridis", colorbar=dict(title="Gamma", len=0.6),
                hovertemplate="Strike: %{x}<br>IV Shift: %{y}<br>Gamma: %{z:.6f}<extra></extra>"))
            fig_zg.update_layout(template="plotly_dark", height=350, margin=dict(l=0, r=0, t=10, b=30),
                                  xaxis_title="Strike", yaxis_title="IV Shift")
            st.plotly_chart(fig_zg, use_container_width=True, config=PLOTLY_NOBAR)

            # Pin risk zone
            _pin_dte = _gdf["dte"].iloc[0]
            if _pin_dte <= 5:
                _atm_speed = _gdf.loc[(_gdf["strike"] - spot).abs().idxmin(), "speed"]
                st.markdown(f'<div style="{_card}border-left:4px solid #ff4444;padding:10px 14px;">'
                            f'<div style="font-size:0.85rem;font-weight:700;color:#ff4444;">PIN RISK ZONE ({_pin_dte} DTE)</div>'
                            f'<div style="font-size:0.78rem;color:#ccc;">ATM speed = {_atm_speed:.8f}. '
                            f'Gamma is accelerating near spot. Small moves cause outsized delta shifts.</div></div>',
                            unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — VEGA CONVEXITY
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    with error_boundary("Vega Convexity"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Vega Convexity</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Volga = how vega itself changes when IV moves. Veta = how vega decays with time.</div>',
                    unsafe_allow_html=True)

        _exp_sel5 = st.selectbox("Expiration", exps, key="hg_vconv_exp")
        _vvdf = hg_df[(hg_df["expiration"] == _exp_sel5) & (hg_df["type"] == "call")].copy()
        _vvdf = _vvdf[(_vvdf["strike"] >= spot * 0.85) & (_vvdf["strike"] <= spot * 1.15)].sort_values("strike")

        if not _vvdf.empty:
            _vc1, _vc2 = st.columns(2)
            with _vc1:
                fig_volga = go.Figure(go.Scatter(
                    x=_vvdf["strike"], y=_vvdf["volga"], mode="lines",
                    line=dict(color=COLORS["warning"], width=2),
                    hovertemplate="K=$%{x:.0f}<br>Volga=%{y:.6f}<extra></extra>"))
                fig_volga.add_vline(x=spot, line_dash="dash", line_color=COLORS["text_muted"])
                fig_volga.update_layout(template="plotly_dark", height=280, margin=dict(l=0, r=0, t=30, b=0),
                                         title="Volga (\u2202\u03bd/\u2202\u03c3)", xaxis_title="Strike", yaxis_title="Volga")
                st.plotly_chart(fig_volga, use_container_width=True, config=PLOTLY_NOBAR)

            with _vc2:
                fig_veta = go.Figure(go.Scatter(
                    x=_vvdf["strike"], y=_vvdf["veta"], mode="lines",
                    line=dict(color=COLORS["accent"], width=2),
                    hovertemplate="K=$%{x:.0f}<br>Veta=%{y:.6f}/day<extra></extra>"))
                fig_veta.add_vline(x=spot, line_dash="dash", line_color=COLORS["text_muted"])
                fig_veta.update_layout(template="plotly_dark", height=280, margin=dict(l=0, r=0, t=30, b=0),
                                         title="Veta (\u2202\u03bd/\u2202t)", xaxis_title="Strike", yaxis_title="Veta/day")
                st.plotly_chart(fig_veta, use_container_width=True, config=PLOTLY_NOBAR)

            # Vol shock simulator
            st.divider()
            st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin-bottom:6px;">Vol Shock Simulator</div>',
                        unsafe_allow_html=True)
            st.session_state["_hg_vshock_data"] = {"strikes": _vvdf["strike"].tolist(), "volga": _vvdf["volga"].tolist()}

            @st.fragment
            def _vol_shock_frag():
                _vsd = st.session_state.get("_hg_vshock_data", {})
                if not _vsd:
                    return
                _vs = st.slider("IV Shift (%)", -15.0, 15.0, 5.0, step=0.5, key="hg_vshock") / 100
                _strikes = np.array(_vsd["strikes"])
                _volga = np.array(_vsd["volga"])
                _chg = _volga * _vs
                fig_s = go.Figure(go.Bar(
                    x=_strikes, y=_chg,
                    marker_color=[COLORS["success"] if v > 0 else COLORS["danger"] for v in _chg],
                    hovertemplate="K=$%{x:.0f}<br>\u0394Vega=%{y:.6f}<extra></extra>"))
                fig_s.add_vline(x=spot, line_dash="dash", line_color=COLORS["warning"])
                fig_s.update_layout(template="plotly_dark", height=250, margin=dict(l=0, r=0, t=10, b=0),
                                     xaxis_title="Strike", yaxis_title=f"\u0394 Vega from {_vs:+.0%} IV shock")
                st.plotly_chart(fig_s, use_container_width=True, config=PLOTLY_NOBAR)
            _vol_shock_frag()


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — VANNA-VOLGA PRICING
# ══════════════════════════════════════════════════════════════════════════════

with tab6:
    with error_boundary("VV Pricing"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Vanna-Volga Price Decomposition</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Every OTM option costs more than Black-Scholes predicts. The extra cost is the "smile premium" '
                    f'\u2014 split into vanna cost (skew premium) and volga cost (convexity premium).</div>',
                    unsafe_allow_html=True)

        _exp_sel6 = st.selectbox("Expiration", exps, key="hg_vv_exp")
        _chain6 = chains[_exp_sel6]
        _dte6 = max((pd.to_datetime(_exp_sel6) - pd.Timestamp.now()).days, 1)
        _T6 = _dte6 / 365

        # Get ATM IV
        _atm_calls6 = _chain6[_chain6["contract_type"] == "call"].reset_index(drop=True)
        _atm_iv6 = 0.25
        if not _atm_calls6.empty:
            _atm_row6 = _atm_calls6.loc[(_atm_calls6["strike_price"] - spot).abs().idxmin()]
            _atm_iv6 = _atm_row6.get("implied_volatility", 0.25) or 0.25

        # Decompose for OTM calls and puts
        _vv_rows = []
        for _, row in _chain6.iterrows():
            K = row["strike_price"]
            iv = row.get("implied_volatility", 0) or 0
            otype = row["contract_type"]
            if iv <= 0 or K <= 0 or not (spot * 0.85 <= K <= spot * 1.15):
                continue
            vv = vanna_volga_decomposition(spot, K, _T6, rfr, _atm_iv6, iv, otype)
            vv["strike"] = K
            vv["type"] = otype
            vv["market_iv"] = iv
            _vv_rows.append(vv)

        if _vv_rows:
            _vv_df = pd.DataFrame(_vv_rows)
            _vv_calls = _vv_df[_vv_df["type"] == "call"].sort_values("strike")

            if not _vv_calls.empty:
                fig_vv = go.Figure()
                fig_vv.add_trace(go.Bar(x=_vv_calls["strike"], y=_vv_calls["bs_price"],
                                         name="BS Base", marker_color=COLORS["accent"]))
                fig_vv.add_trace(go.Bar(x=_vv_calls["strike"], y=_vv_calls["vanna_cost"],
                                         name="Vanna Cost", marker_color=COLORS["warning"]))
                fig_vv.add_trace(go.Bar(x=_vv_calls["strike"], y=_vv_calls["volga_cost"],
                                         name="Volga Cost", marker_color="#9966ff"))
                fig_vv.update_layout(template="plotly_dark", height=350, barmode="stack",
                                      margin=dict(l=0, r=0, t=10, b=0),
                                      xaxis_title="Strike ($)", yaxis_title="Price ($)",
                                      legend=dict(orientation="h", y=-0.15))
                fig_vv.add_vline(x=spot, line_dash="dash", line_color=COLORS["text_muted"])
                st.plotly_chart(fig_vv, use_container_width=True, config=PLOTLY_NOBAR)

                # Smile premium table
                st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin:12px 0 6px 0;">Smile Premium by Strike</div>',
                            unsafe_allow_html=True)
                _disp_vv = _vv_calls[["strike", "bs_price", "vanna_cost", "volga_cost", "vv_price", "smile_premium_pct"]].copy()
                _disp_vv.columns = ["Strike", "BS Price", "Vanna Cost", "Volga Cost", "VV Price", "Smile %"]
                _disp_vv["Strike"] = _disp_vv["Strike"].apply(lambda v: f"${v:.0f}")
                for c in ["BS Price", "Vanna Cost", "Volga Cost", "VV Price"]:
                    _disp_vv[c] = _disp_vv[c].apply(lambda v: f"${v:.3f}")
                _disp_vv["Smile %"] = _disp_vv["Smile %"].apply(lambda v: f"{v:.1f}%")
                st.dataframe(_disp_vv.set_index("Strike"), use_container_width=True)

            # Mispricing signals — compare market price to VV fair value
            if not _vv_df.empty:
                st.divider()
                st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin-bottom:6px;">Mispricing Signals</div>'
                            f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:8px;">'
                            f'Options where market price deviates from Vanna-Volga fair value. '
                            f'Cheap = buy candidate. Rich = sell candidate.</div>',
                            unsafe_allow_html=True)

                # Get actual market prices for comparison
                _misprice = []
                for _, row in _chain6.iterrows():
                    K = row["strike_price"]
                    otype = row["contract_type"]
                    mid = ((row.get("bid", 0) or 0) + (row.get("ask", 0) or 0)) / 2
                    if mid <= 0:
                        mid = row.get("last_price", 0) or 0
                    if mid <= 0 or K <= 0:
                        continue
                    _vv_match = _vv_df[(_vv_df["strike"] == K) & (_vv_df["type"] == otype)]
                    if _vv_match.empty:
                        continue
                    vv_price = _vv_match.iloc[0]["vv_price"]
                    if vv_price <= 0:
                        continue
                    deviation = (mid / vv_price - 1) * 100
                    oi = row.get("open_interest", 0) or 0
                    if abs(deviation) > 3 and oi > 10:  # >3% deviation with OI
                        _misprice.append({"strike": K, "type": otype, "market": mid,
                                           "vv_fair": vv_price, "deviation": deviation, "oi": oi})

                if _misprice:
                    _mp_df = pd.DataFrame(_misprice).sort_values("deviation")
                    _cheap = _mp_df[_mp_df["deviation"] < -3].head(3)
                    _rich = _mp_df[_mp_df["deviation"] > 3].tail(3).sort_values("deviation", ascending=False)

                    _mc1, _mc2 = st.columns(2)
                    with _mc1:
                        if not _cheap.empty:
                            st.markdown(f'<div style="{_card}border-left:4px solid #00ff96;padding:10px 14px;">'
                                        f'<div style="font-size:0.85rem;font-weight:700;color:#00ff96;">Cheap vs VV Fair (Buy Candidates)</div>',
                                        unsafe_allow_html=True)
                            for _, r in _cheap.iterrows():
                                st.markdown(f'<div style="font-size:0.78rem;color:#ccc;padding:2px 0;">'
                                            f'${r["strike"]:.0f} {r["type"]} \u2014 Mkt ${r["market"]:.2f} vs VV ${r["vv_fair"]:.2f} '
                                            f'(<span style="color:#00ff96;">{r["deviation"]:+.1f}%</span>) OI={r["oi"]:,.0f}</div>',
                                            unsafe_allow_html=True)
                            st.markdown('</div>', unsafe_allow_html=True)
                    with _mc2:
                        if not _rich.empty:
                            st.markdown(f'<div style="{_card}border-left:4px solid #ff4444;padding:10px 14px;">'
                                        f'<div style="font-size:0.85rem;font-weight:700;color:#ff4444;">Rich vs VV Fair (Sell Candidates)</div>',
                                        unsafe_allow_html=True)
                            for _, r in _rich.iterrows():
                                st.markdown(f'<div style="font-size:0.78rem;color:#ccc;padding:2px 0;">'
                                            f'${r["strike"]:.0f} {r["type"]} \u2014 Mkt ${r["market"]:.2f} vs VV ${r["vv_fair"]:.2f} '
                                            f'(<span style="color:#ff4444;">{r["deviation"]:+.1f}%</span>) OI={r["oi"]:,.0f}</div>',
                                            unsafe_allow_html=True)
                            st.markdown('</div>', unsafe_allow_html=True)

                    if not _cheap.empty or not _rich.empty:
                        st.caption("Relative value signals based on Vanna-Volga fair value. Verify with your own analysis before trading.")
                else:
                    st.caption("No significant mispricings detected (all options within 3% of VV fair value).")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — PORTFOLIO HIGHER GREEKS
# ══════════════════════════════════════════════════════════════════════════════

with tab7:
    with error_boundary("Portfolio"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Portfolio Higher-Order Greeks</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Aggregate higher-order exposures from your Position Book. Shows overnight delta drift and vol sensitivity.</div>',
                    unsafe_allow_html=True)

        # Try to read positions from page 44
        _positions = None
        try:
            from src.position_book import get_positions
            _pos_list = get_positions()
            if _pos_list:
                _positions = [p for p in _pos_list if p.get("type") == "option" and p.get("ticker") == ticker_display]
        except Exception:
            pass

        if not _positions:
            st.info(f"No option positions found for {ticker_display}. Add positions on the **Portfolio Greeks** page, or use the single-strike calculator on the Overview tab.")
        else:
            _port_greeks = {"vanna": 0, "volga": 0, "charm": 0, "veta": 0,
                            "speed": 0, "zomma": 0, "color": 0, "ultima": 0}
            _port_delta = 0
            for pos in _positions:
                K = pos.get("strike", 0)
                exp = pos.get("expiration", "")
                otype = pos.get("option_type", "call")
                qty = pos.get("qty", 0)
                dte = max((pd.to_datetime(exp) - pd.Timestamp.now()).days, 1) if exp else 30
                T = dte / 365
                # Find IV from chain
                iv = 0.25
                if exp in chains:
                    _c = chains[exp]
                    _match = _c[(_c["strike_price"] == K) & (_c["contract_type"] == otype)]
                    if not _match.empty:
                        iv = _match.iloc[0].get("implied_volatility", 0.25) or 0.25
                _g = bs_all_greeks(spot, K, T, rfr, iv, otype)
                _mult = qty * 100
                for gk in _port_greeks:
                    _port_greeks[gk] += _g[gk] * _mult
                _port_delta += _g["delta"] * _mult

            # Display
            _pc = st.columns(4)
            _pg_items = [
                ("Net Vanna $", _port_greeks["vanna"], COLORS["warning"],
                 f"A 1% IV move shifts your delta by ${abs(_port_greeks['vanna'] * 0.01):.2f}"),
                ("Net Charm $/day", _port_greeks["charm"], COLORS["accent"],
                 f"Your delta drifts by {_port_greeks['charm']:.4f} overnight"),
                ("Net Speed $", _port_greeks["speed"], COLORS["danger"],
                 "Gamma acceleration exposure"),
                ("Net Zomma $", _port_greeks["zomma"], COLORS["danger"],
                 "Gamma sensitivity to IV changes"),
            ]
            for i, (_label, _val, _color, _desc) in enumerate(_pg_items):
                _pc[i].markdown(
                    f'<div style="background:{COLORS["card_bg"]};border:1px solid {_color};border-radius:8px;'
                    f'padding:10px;text-align:center;">'
                    f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">{_label}</div>'
                    f'<div style="font-size:1.1rem;font-weight:700;color:{_color};">{_val:.4f}</div>'
                    f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">{_desc}</div></div>',
                    unsafe_allow_html=True)

            # Overnight risk report
            st.divider()
            st.markdown(f'<div style="{_card}">'
                        f'<div style="font-size:0.9rem;font-weight:700;color:#e6edf3;">Overnight Risk Report</div>'
                        f'<div style="font-size:0.82rem;color:#ccc;margin-top:4px;">'
                        f'Current portfolio delta: {_port_delta:.2f}<br>'
                        f'Delta drift by tomorrow (charm): {_port_greeks["charm"]:+.4f}<br>'
                        f'Delta shift from +1% IV (vanna): {_port_greeks["vanna"] * 0.01:+.4f}<br>'
                        f'Delta shift from -1% IV (vanna): {_port_greeks["vanna"] * -0.01:+.4f}</div></div>',
                        unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — AI GREEK ANALYST
# ══════════════════════════════════════════════════════════════════════════════

with tab8:
    with error_boundary("AI Analyst"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">AI Greek Analyst</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Gemini reads all higher-order Greeks and tells you what to do.</div>',
                    unsafe_allow_html=True)

        _gemini_key = _get_key("GEMINI_API_KEY")
        if not _gemini_key:
            st.error("Gemini API key not configured.")
        else:
            def _build_greeks_context():
                lines = [f"HIGHER-ORDER GREEKS ANALYSIS: {ticker_display}", f"Spot: ${spot:.2f}", ""]
                # Per-expiration summary
                for exp in exps[:4]:
                    _edf = hg_df[(hg_df["expiration"] == exp) & (hg_df["type"] == "call")]
                    _edf = _edf[(_edf["strike"] >= spot * 0.9) & (_edf["strike"] <= spot * 1.1)]
                    if _edf.empty:
                        continue
                    _dte = _edf["dte"].iloc[0]
                    _atm = _edf.loc[(_edf["strike"] - spot).abs().idxmin()] if not _edf.empty else None
                    if _atm is not None:
                        lines.append(f"--- {exp} ({_dte}d) ATM Call ${_atm['strike']:.0f} ---")
                        lines.append(f"  Delta={_atm['delta']:.4f} Gamma={_atm['gamma']:.6f} Vega={_atm['vega']:.4f}")
                        lines.append(f"  Vanna={_atm['vanna']:.6f} Volga={_atm['volga']:.6f} Charm={_atm['charm']:.6f}")
                        lines.append(f"  Speed={_atm['speed']:.8f} Zomma={_atm['zomma']:.6f}")

                    # Extremes at this expiration
                    _max_vanna = _edf.nlargest(1, "vanna")
                    _min_vanna = _edf.nsmallest(1, "vanna")
                    _max_speed = _edf.loc[_edf["speed"].abs().idxmax()] if not _edf.empty else None
                    if not _max_vanna.empty:
                        _r = _max_vanna.iloc[0]
                        lines.append(f"  Peak vanna: ${_r['strike']:.0f} = {_r['vanna']:.6f}")
                    if _max_speed is not None:
                        lines.append(f"  Peak speed: ${_max_speed['strike']:.0f} = {_max_speed['speed']:.8f}")
                    lines.append("")

                # Pin risk
                _short_dte = hg_df[hg_df["dte"] <= 5]
                if not _short_dte.empty:
                    lines.append("--- PIN RISK (<=5 DTE) ---")
                    _pin = _short_dte.loc[_short_dte["speed"].abs().idxmax()]
                    lines.append(f"  Highest speed: ${_pin['strike']:.0f} {_pin['type']} ({_pin['dte']}d) speed={_pin['speed']:.8f}")

                return "\n".join(lines)

            st.caption("~\\$0.05 per generation")

            @st.fragment
            def _ai_greeks():
                if st.button("Analyze Greeks", type="primary", use_container_width=True, key="hg_ai_run"):
                    from src.ai_validation import ACCURACY_CHECK_LIGHT, VOL_SURFACE_EXPERT_CONTEXT, HIGHER_GREEKS_EXPERT_CONTEXT
                    _ctx = _build_greeks_context()
                    _prompt = (
                        "You are a senior options risk manager and market microstructure expert analyzing "
                        "higher-order Greeks for a derivatives portfolio.\n\n"
                        f"{HIGHER_GREEKS_EXPERT_CONTEXT}\n\n"
                        "Read the Greeks data below and give ACTIONABLE recommendations.\n\n"
                        "## Risk Summary\n"
                        "What are the 2-3 biggest risks embedded in this Greek profile? Reference specific numbers.\n\n"
                        "## Dealer Flow Analysis\n"
                        "Based on the vanna and charm profiles, what mechanical flows will dealers generate? "
                        "Is this a short-gamma or long-gamma setup? Will there be a post-event squeeze or afternoon melt-up?\n\n"
                        "## Vanna Exposure\n"
                        "How will delta shift if IV moves? Is this a problem? What to trade to manage it.\n\n"
                        "## Overnight Risk (Charm)\n"
                        "How much will delta drift overnight? Should the trader hedge at close? Exact share count.\n\n"
                        "## Pin Risk (Speed + Zomma)\n"
                        "Are there strikes with dangerous gamma acceleration? How will gamma shift if IV spikes (zomma)? "
                        "What is the 0DTE risk if near expiration?\n\n"
                        "## Recommended Trades\n"
                        "3-5 specific, actionable trades to improve the Greek profile. For each: instrument, direction, rationale. "
                        "Include at least one vanna-based trade and one charm-based hedge.\n\n"
                        "RULES: Reference dealer mechanics. Specific numbers. Direct. Each section 2-4 sentences. Under 500 words.\n\n"
                        f"DATA:\n{_ctx}\n\n{ACCURACY_CHECK_LIGHT}"
                    )
                    with fun_loader("ai"):
                        try:
                            from google import genai
                            from google.genai import types
                            client = genai.Client(api_key=_gemini_key)
                            resp = client.models.generate_content(
                                model="gemini-3.1-pro-preview", contents=_prompt,
                                config=types.GenerateContentConfig(max_output_tokens=3000, temperature=0.3))
                            st.session_state["hg_ai_result"] = resp.text
                        except Exception as e:
                            st.error(f"AI analysis failed: {e}")

                if "hg_ai_result" in st.session_state:
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
                        f'<div style="border:1px solid {COLORS["accent"]};border-radius:4px;padding:2px 8px;'
                        f'font-size:0.7rem;color:{COLORS["accent"]};font-weight:600;">GEMINI 3.1 PRO</div>'
                        f'<span style="font-size:0.75rem;color:{COLORS["text_muted"]};">Greek Risk Analyst</span></div>',
                        unsafe_allow_html=True)
                    st.markdown(st.session_state["hg_ai_result"].replace("$", "\\$"))
                    st.caption("AI-generated. Not financial advice.")

            _ai_greeks()


# ─── FOOTER ───────────────────────────────────────────────────────────────────

st.markdown(
    f'<div style="margin-top:24px;padding:14px 20px;border-top:1px solid {COLORS["card_border"]};'
    f'display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">'
    f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">'
    f'Greeks computed using closed-form Black-Scholes formulas. Not financial advice.</div>'
    f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};opacity:0.7;">'
    f'{ticker_display} | {len(hg_df)} contracts</div></div>', unsafe_allow_html=True)
