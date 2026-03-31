import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import logging
from datetime import datetime

from src.layout import setup_page, error_boundary, fun_loader
from src.styles import COLORS
from src.cross_asset_vol import (
    SCAN_UNIVERSE, ALL_TICKERS, get_rfr,
    load_universe_data, compute_cross_asset_metrics,
    interpolate_smile, atm_iv, compute_implied_correlation,
    detect_divergences, compute_metric_changes,
    compute_correlation_matrix, fetch_earnings_dates, compute_benchmark_context,
)

setup_page("48_Vol_Landscape")
logger = logging.getLogger(__name__)
PLOTLY_NOBAR = {"displayModeBar": False}

_card = (f'background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
         f'border-radius:10px;padding:16px 20px;margin-bottom:12px;')
_card_sm = (f'background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
            f'border-radius:8px;padding:10px 14px;text-align:center;')

# ─── HEADER ───────────────────────────────────────────────────────────────────

st.markdown(
    f'<div style="font-size:1.6rem;font-weight:800;color:#e6edf3;margin-bottom:2px;">Vol Landscape</div>'
    f'<div style="font-size:0.85rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
    f'Cross-asset volatility surface analysis across 20 ETFs. What is the options market telling you?</div>',
    unsafe_allow_html=True)

with st.expander("How to read this page", expanded=False):
    st.markdown(
        f'<div style="font-size:0.85rem;color:#ccc;line-height:1.6;">'
        f'This page scans options chains across 11 sector ETFs and 9 macro assets to build a complete picture of market risk pricing.<br><br>'
        f'<b>Moneyness</b>: 1.00 = at-the-money. 0.90 = 10% OTM puts (crash protection). 1.10 = 10% OTM calls.<br>'
        f'<b>IV/HV</b>: >1.2 = options overpriced (sell premium). <0.85 = options cheap (buy protection).<br>'
        f'<b>Put Skew</b>: >1.10x = heavy fear premium. <1.03x = complacent.<br>'
        f'<b>TS Slope</b>: Negative = backwardation (near-term event pricing).<br>'
        f'<b>VRP</b>: IV minus HV. Positive = selling vol profitable. Negative = buying vol profitable.</div>',
        unsafe_allow_html=True)

# ─── DATA LOADING ─────────────────────────────────────────────────────────────

_reused = False
if "me_ticker_data" in st.session_state and len(st.session_state["me_ticker_data"]) >= 3:
    ticker_data = st.session_state["me_ticker_data"]
    _reused = True
    if "vl_loaded_at" not in st.session_state:
        st.session_state["vl_loaded_at"] = datetime.now()
elif "vl_ticker_data" in st.session_state:
    ticker_data = st.session_state["vl_ticker_data"]
else:
    _run = st.button("Scan Market", type="primary", use_container_width=True)
    if _run:
        rfr = get_rfr()
        with fun_loader("data"):
            ticker_data = load_universe_data(ALL_TICKERS, rfr)
        st.session_state["vl_ticker_data"] = ticker_data
        st.session_state["vl_loaded_at"] = datetime.now()
    else:
        st.info("Click **Scan Market** to load options data across 20 ETFs, or visit **Market Expectations** first.")
        st.stop()

if len(ticker_data) < 3:
    st.error("Could not load enough data. Check API keys.")
    st.stop()

# Data freshness + refresh button
_loaded_at = st.session_state.get("vl_loaded_at", datetime.now())
_age_min = (datetime.now() - _loaded_at).total_seconds() / 60
_now = datetime.now()
_is_stale = _age_min > 30 and _now.weekday() < 5 and 9 <= _now.hour <= 16
_fr1, _fr2 = st.columns([3, 1])
with _fr1:
    if _is_stale:
        st.warning(f"Data is {_age_min:.0f} min old. Prices may have moved.")
    elif _age_min > 5:
        st.caption(f"Data loaded {_age_min:.0f} min ago | {len(ticker_data)} tickers")
    else:
        st.caption(f"{len(ticker_data)} tickers loaded")
with _fr2:
    if st.button("Refresh", key="vl_refresh", use_container_width=True):
        _rfr = get_rfr()
        with fun_loader("data"):
            ticker_data = load_universe_data(ALL_TICKERS, _rfr)
        st.session_state["vl_ticker_data"] = ticker_data
        st.session_state["vl_loaded_at"] = datetime.now()
        # Clear stale caches so they recompute on fresh data
        st.session_state.pop("vl_earnings", None)
        st.session_state.pop("vl_ai_result", None)
        st.rerun()

# ─── COMPUTE METRICS ──────────────────────────────────────────────────────────

rfr = get_rfr()
mdf = compute_cross_asset_metrics(ticker_data, rfr)
if mdf.empty:
    st.error("No metrics computed.")
    st.stop()

# Historical change tracking
# vl_current_metrics always holds the CURRENT scan's raw metrics
# vl_prev_metrics holds the PREVIOUS scan's metrics (only updated on new scan)
_loaded_ts_str = str(st.session_state.get("vl_loaded_at", ""))
_last_ts_str = st.session_state.get("vl_metrics_ts", "")
_is_new_scan = _loaded_ts_str != _last_ts_str

if _is_new_scan:
    # Promote current → previous before overwriting current
    _old_current = st.session_state.get("vl_current_metrics")
    if _old_current is not None:
        st.session_state["vl_prev_metrics"] = _old_current
    # Save new current
    _save_cols = [c for c in ["Ticker", "Front_IV", "Put_Skew", "IV_HV", "TS_Slope", "VRP_Vol"] if c in mdf.columns]
    st.session_state["vl_current_metrics"] = mdf[_save_cols].copy()
    st.session_state["vl_metrics_ts"] = _loaded_ts_str

_prev_mdf = st.session_state.get("vl_prev_metrics")
mdf = compute_metric_changes(mdf, _prev_mdf)

# Implied correlation
sector_tickers = [tk for tk in ticker_data if tk in SCAN_UNIVERSE.get("Sectors", {})]
spy_iv = mdf.loc[mdf["Ticker"] == "SPY", "Front_IV"].values[0] if "SPY" in mdf["Ticker"].values else 0
sector_ivs = [mdf.loc[mdf["Ticker"] == tk, "Front_IV"].values[0] for tk in sector_tickers if tk in mdf["Ticker"].values]
impl_corr = compute_implied_correlation(spy_iv, sector_ivs)

# Divergences
divergences = detect_divergences(mdf)

# Correlation matrix
corr_df, corr_tickers = compute_correlation_matrix(ticker_data)

# Earnings dates — cached to avoid repeated yfinance calls
if "vl_earnings" not in st.session_state:
    try:
        st.session_state["vl_earnings"] = fetch_earnings_dates(list(ticker_data.keys()))
    except Exception:
        st.session_state["vl_earnings"] = {}
_earnings_dates = st.session_state["vl_earnings"]

# Benchmark context (IV vs 30-day avg)
_benchmarks = compute_benchmark_context(ticker_data, mdf)

# Net premium flow (dollar-weighted P/C)
_net_premium = {}
for _tk, _td in ticker_data.items():
    try:
        _fc = _td["chains"][_td["expirations"][0]]
        _puts = _fc[_fc["contract_type"] == "put"]
        _calls = _fc[_fc["contract_type"] == "call"]
        _put_prem = ((_puts["volume"].fillna(0) * ((_puts["bid"].fillna(0) + _puts["ask"].fillna(0)) / 2) * 100)).sum()
        _call_prem = ((_calls["volume"].fillna(0) * ((_calls["bid"].fillna(0) + _calls["ask"].fillna(0)) / 2) * 100)).sum()
        if _call_prem > 0:
            _net_premium[_tk] = {"put_prem": _put_prem, "call_prem": _call_prem,
                                  "net_pc": _put_prem / _call_prem, "net_flow": _put_prem - _call_prem}
    except Exception:
        continue

# VIX context
_vix_snap = None
try:
    from src.macro_data import get_vix_snapshot
    _vix_snap = get_vix_snapshot()
except Exception:
    pass

# ─── REGIME CLASSIFICATION ────────────────────────────────────────────────────

avg_iv = mdf["Front_IV"].mean()
avg_ivhv = mdf["IV_HV"].dropna().mean() if mdf["IV_HV"].notna().any() else 1.0
avg_pctile = mdf["IV_Pctile"].dropna().mean() if mdf["IV_Pctile"].notna().any() else 50
avg_skew = mdf["Put_Skew"].mean()
n_inverted = (mdf["TS_Slope"] < 0).sum()
n_steep_skew = (mdf["Put_Skew"] > 1.10).sum()
pct_inverted = n_inverted / len(mdf) * 100
avg_vrp_vol = mdf["VRP_Vol"].dropna().mean() if "VRP_Vol" in mdf.columns and mdf["VRP_Vol"].notna().any() else 0

if avg_ivhv > 1.2 and avg_pctile > 65:
    vol_regime, regime_color = "Elevated Vol \u2014 Rich Premiums", "#ff4444"
    regime_action = "Sell premium. Short straddles, iron condors, credit spreads."
elif avg_ivhv < 0.85 and avg_pctile < 35:
    vol_regime, regime_color = "Low Vol \u2014 Cheap Protection", "#00ff96"
    regime_action = "Buy protection. Long puts, ratio backspreads, tail hedges."
elif n_inverted >= 3:
    vol_regime, regime_color = "Event-Driven \u2014 Near-Term Fear", "#ffaa00"
    regime_action = "Calendar spreads. Sell elevated front-month, buy cheaper back-month."
elif n_steep_skew > len(mdf) * 0.5:
    vol_regime, regime_color = "Broad Fear \u2014 Steep Skew", "#ff4444"
    regime_action = "Sell overpriced put wings via put spreads or risk reversals."
else:
    vol_regime, regime_color = "Normal Conditions", "#e6edf3"
    regime_action = "No broad signal. Focus on single-name relative value."

# Regime change detection
_prev_regime = st.session_state.get("vl_prev_regime")
_regime_changed = _prev_regime is not None and _prev_regime != vol_regime
st.session_state["vl_prev_regime"] = vol_regime

# Write cross-context
try:
    from src.cross_context import write_context
    write_context("vol_landscape", {
        "regime": vol_regime, "regime_action": regime_action,
        "avg_iv": avg_iv, "avg_ivhv": avg_ivhv, "avg_pctile": avg_pctile,
        "implied_corr": impl_corr, "n_inverted": n_inverted, "n_steep_skew": n_steep_skew,
        "richest": mdf.nlargest(1, "Front_IV")["Ticker"].values[0] if not mdf.empty else "",
        "cheapest": mdf.nsmallest(1, "Front_IV")["Ticker"].values[0] if not mdf.empty else "",
    })
except Exception:
    pass

# ─── REGIME BANNER ────────────────────────────────────────────────────────────

_pills = [
    ("AVG IV", f"{avg_iv:.1%}", "#e6edf3"),
    ("IV/HV", f"{avg_ivhv:.2f}x", "#ff4444" if avg_ivhv > 1.2 else ("#00ff96" if avg_ivhv < 0.85 else "#e6edf3")),
    ("VRP", f"{avg_vrp_vol:+.1%}" if avg_vrp_vol else "N/A", "#ff4444" if avg_vrp_vol and avg_vrp_vol > 0.03 else ("#00ff96" if avg_vrp_vol and avg_vrp_vol < -0.02 else "#e6edf3")),
    ("SKEW", f"{avg_skew:.2f}x", "#ff4444" if avg_skew > 1.10 else ("#00ff96" if avg_skew < 1.03 else "#e6edf3")),
    ("INVERTED", f"{n_inverted}/{len(mdf)}", "#ff4444" if n_inverted >= 3 else "#e6edf3"),
]
if impl_corr is not None:
    _pills.append(("CORR", f"{impl_corr:.2f}", "#ff4444" if impl_corr > 0.7 else ("#00ff96" if impl_corr < 0.4 else "#e6edf3")))
if _vix_snap and _vix_snap.get("VIX"):
    _vix_v = _vix_snap["VIX"]
    _pills.append(("VIX", f"{_vix_v:.1f}", "#ff4444" if _vix_v > 25 else ("#00ff96" if _vix_v < 15 else "#e6edf3")))

_pill_html = ""
for _pl, _pv, _pc in _pills:
    _pill_html += (f'<div style="background:rgba({int(_pc[1:3],16)},{int(_pc[3:5],16)},{int(_pc[5:7],16)},0.1);'
                   f'border:1px solid {_pc};border-radius:6px;padding:4px 10px;text-align:center;">'
                   f'<div style="font-size:0.55rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{_pl}</div>'
                   f'<div style="font-size:0.9rem;font-weight:700;color:{_pc};">{_pv}</div></div>')

_regime_chg_html = ""
if _regime_changed:
    _regime_chg_html = f'<div style="font-size:0.75rem;color:#ffaa00;margin-top:4px;">Changed from: {_prev_regime}</div>'

st.markdown(
    f'<div style="{_card}border-color:{regime_color};">'
    f'<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;">'
    f'<div>'
    f'<div style="font-size:1.1rem;font-weight:700;color:{regime_color};">{vol_regime}</div>'
    f'<div style="font-size:0.8rem;color:#ccc;margin-top:2px;">{regime_action}</div>'
    f'{_regime_chg_html}'
    f'</div>'
    f'<div style="display:flex;gap:8px;flex-wrap:wrap;">{_pill_html}</div>'
    f'</div></div>', unsafe_allow_html=True)

# ─── TABS ─────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Vol Landscape", "Market Environment", "Metrics & Changes", "Signals & Alerts", "AI Analysis"
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — VOL LANDSCAPE HEATMAPS
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("Vol Landscape"):
        _gc1, _gc2, _gc3 = st.columns([2, 1, 1])
        with _gc1:
            _group_filter = st.selectbox("Group", ["All", "Sectors", "Macro"], key="vl_group")
        with _gc2:
            _norm_mode = st.radio("Scale", ["Relative", "Absolute"], horizontal=True, key="vl_norm")
        with _gc3:
            _sort_by = st.selectbox("Sort by", ["Group", "Front IV", "Skew", "IV/HV"], key="vl_sort")

        if _group_filter == "All":
            _display_tickers = list(mdf["Ticker"])
        else:
            _display_tickers = [tk for tk in mdf["Ticker"] if mdf.loc[mdf["Ticker"] == tk, "Group"].values[0] == _group_filter]

        # Apply sort
        if _sort_by == "Front IV":
            _sorted = mdf[mdf["Ticker"].isin(_display_tickers)].sort_values("Front_IV", ascending=False)
            _display_tickers = list(_sorted["Ticker"])
        elif _sort_by == "Skew":
            _sorted = mdf[mdf["Ticker"].isin(_display_tickers)].sort_values("Put_Skew", ascending=False)
            _display_tickers = list(_sorted["Ticker"])
        elif _sort_by == "IV/HV":
            _sorted = mdf[mdf["Ticker"].isin(_display_tickers)].sort_values("IV_HV", ascending=False, na_position="last")
            _display_tickers = list(_sorted["Ticker"])

        # ── Heatmap A: Smile ──
        st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin:8px 0 4px 0;">Volatility Smile (Front Month)</div>'
                    f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                    f'Left = OTM puts (crash protection). Right = OTM calls. Center = ATM.</div>',
                    unsafe_allow_html=True)

        moneyness_pts = [0.90, 0.95, 0.98, 1.00, 1.02, 1.05, 1.10]
        smile_matrix, smile_labels = [], []
        for tk in _display_tickers:
            td = ticker_data.get(tk)
            if not td or not td.get("chains"):
                continue
            smile = interpolate_smile(td["chains"][td["expirations"][0]], td["spot"], moneyness_pts)
            if smile:
                row = [smile.get(m) for m in moneyness_pts]
                if any(v is not None for v in row):
                    smile_matrix.append([v if v is not None else np.nan for v in row])
                    smile_labels.append(tk)

        if smile_matrix:
            z = np.array(smile_matrix) * 100
            if _norm_mode == "Relative":
                rm = np.nanmean(z, axis=1, keepdims=True)
                rs = np.nanstd(z, axis=1, keepdims=True); rs[rs == 0] = 1
                z = (z - rm) / rs
            fig_smile = go.Figure(go.Heatmap(
                z=z, x=[f"{m:.0%}" for m in moneyness_pts], y=smile_labels,
                colorscale="RdYlBu_r", text=np.round(z, 1), texttemplate="%{text}",
                textfont=dict(size=11), colorbar=dict(title="IV %" if _norm_mode == "Absolute" else "Z", len=0.6),
                hoverongaps=False, hovertemplate="<b>%{y}</b> | %{x}<br>IV: %{z:.1f}<extra></extra>",
            ))
            fig_smile.update_layout(template="plotly_dark", height=max(350, len(smile_labels) * 30 + 80),
                                     margin=dict(l=0, r=0, t=10, b=30), xaxis_title="Moneyness", yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_smile, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Heatmap B: Term Structure ──
        st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin:16px 0 4px 0;">ATM IV Term Structure</div>'
                    f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                    f'Hotter left than right = backwardation (near-term fear).</div>',
                    unsafe_allow_html=True)

        ts_matrix, ts_labels, ts_dte_labels = [], [], []
        for tk in _display_tickers:
            td = ticker_data.get(tk)
            if not td or not td.get("chains"):
                continue
            row, dte_row = [], []
            for exp in td["expirations"][:5]:
                chain = td["chains"].get(exp)
                if chain is not None:
                    iv = atm_iv(chain, td["spot"])
                    dte = max((pd.to_datetime(exp) - pd.Timestamp.now()).days, 1)
                    row.append(iv * 100); dte_row.append(f"{dte}d")
            if row:
                ts_matrix.append(row); ts_labels.append(tk)
                if not ts_dte_labels or len(dte_row) > len(ts_dte_labels):
                    ts_dte_labels = dte_row

        if ts_matrix:
            max_cols = max(len(r) for r in ts_matrix)
            for r in ts_matrix:
                while len(r) < max_cols: r.append(np.nan)
            z_ts = np.array(ts_matrix)
            while len(ts_dte_labels) < max_cols: ts_dte_labels.append("")
            fig_ts = go.Figure(go.Heatmap(
                z=z_ts, x=ts_dte_labels[:max_cols], y=ts_labels,
                colorscale="Viridis", text=np.round(z_ts, 1), texttemplate="%{text}",
                textfont=dict(size=11), colorbar=dict(title="ATM IV %", len=0.6),
                hoverongaps=False, hovertemplate="<b>%{y}</b> | %{x}<br>IV: %{z:.1f}%<extra></extra>",
            ))
            fig_ts.update_layout(template="plotly_dark", height=max(350, len(ts_labels) * 30 + 80),
                                  margin=dict(l=0, r=0, t=10, b=30), xaxis_title="DTE", yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_ts, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Heatmap C: IV Change Since Last Scan ──
        _has_chg = "Front_IV_chg" in mdf.columns and mdf["Front_IV_chg"].notna().any()
        if _has_chg:
            st.divider()
            st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin:8px 0 4px 0;">IV Change Since Last Scan</div>'
                        f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                        f'Green = IV dropped (vol contracting). Red = IV rose (vol expanding).</div>',
                        unsafe_allow_html=True)
            _chg_matrix, _chg_labels = [], []
            for tk in _display_tickers:
                _r = mdf[mdf["Ticker"] == tk]
                if _r.empty:
                    continue
                _iv_chg = _r.iloc[0].get("Front_IV_chg")
                _skew_chg = _r.iloc[0].get("Put_Skew_chg")
                _ivhv_chg = _r.iloc[0].get("IV_HV_chg")
                _ts_chg = _r.iloc[0].get("TS_Slope_chg")
                if pd.notna(_iv_chg):
                    _chg_matrix.append([
                        _iv_chg * 100 if pd.notna(_iv_chg) else 0,
                        _skew_chg * 100 if pd.notna(_skew_chg) else 0,
                        _ivhv_chg * 100 if pd.notna(_ivhv_chg) else 0,
                        _ts_chg * 10000 if pd.notna(_ts_chg) else 0,
                    ])
                    _chg_labels.append(tk)
            if _chg_matrix:
                _chg_z = np.array(_chg_matrix)
                _chg_cols = ["\u0394 IV (%)", "\u0394 Skew (bps)", "\u0394 IV/HV (bps)", "\u0394 TS (bps)"]
                fig_chg = go.Figure(go.Heatmap(
                    z=_chg_z, x=_chg_cols, y=_chg_labels,
                    colorscale="RdYlGn_r", text=np.round(_chg_z, 2), texttemplate="%{text}",
                    textfont=dict(size=11), zmid=0,
                    colorbar=dict(title="Change", len=0.6),
                    hovertemplate="<b>%{y}</b> | %{x}<br>Change: %{z:.2f}<extra></extra>",
                ))
                fig_chg.update_layout(template="plotly_dark", height=max(300, len(_chg_labels) * 28 + 60),
                                       margin=dict(l=0, r=0, t=10, b=30), yaxis=dict(autorange="reversed"))
                st.plotly_chart(fig_chg, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Expected Move Rankings (with earnings flags) ──
        st.divider()
        st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin-bottom:6px;">Expected Move Rankings</div>'
                    f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                    f'Straddle-implied move for front month. Stars = earnings within front expiration.</div>',
                    unsafe_allow_html=True)
        _move_df = mdf[mdf["Impl_Move"] > 0][["Ticker", "Impl_Move", "Front_DTE"]].sort_values("Impl_Move", ascending=False)
        if not _move_df.empty:
            # Flag tickers with earnings in front expiration
            _move_colors = []
            _move_text = []
            for _, _mr in _move_df.iterrows():
                _has_earn = _mr["Ticker"] in _earnings_dates and _earnings_dates[_mr["Ticker"]]["days"] <= _mr["Front_DTE"]
                _move_colors.append("#ff66ff" if _has_earn else (COLORS["danger"] if _mr["Impl_Move"] > 8 else (COLORS["warning"] if _mr["Impl_Move"] > 4 else COLORS["accent"])))
                _move_text.append(f"{_mr['Impl_Move']:.1f}%{'*' if _has_earn else ''}")

            fig_move = go.Figure(go.Bar(
                x=_move_df["Ticker"], y=_move_df["Impl_Move"],
                marker_color=_move_colors, text=_move_text, textposition="outside", textfont=dict(size=9),
                hovertemplate="<b>%{x}</b><br>Impl Move: %{y:.1f}%<br>Front DTE: %{customdata}d<extra></extra>",
                customdata=_move_df["Front_DTE"],
            ))
            fig_move.update_layout(template="plotly_dark", height=250, margin=dict(l=0, r=0, t=10, b=0),
                                    yaxis_title="Implied Move (%)", showlegend=False)
            st.plotly_chart(fig_move, use_container_width=True, config=PLOTLY_NOBAR)
            if _earnings_dates:
                _earn_list = [f"{tk} ({v['date']}, {v['days']}d)" for tk, v in _earnings_dates.items() if tk in _move_df["Ticker"].values]
                if _earn_list:
                    st.caption(f"Earnings within front expiration: {', '.join(_earn_list)}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — MARKET ENVIRONMENT
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    with error_boundary("Market Environment"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Market Environment</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Is vol rich or cheap? Is the market scared or complacent? Where is risk concentrated?</div>',
                    unsafe_allow_html=True)

        with st.expander("What each panel tells you", expanded=False):
            st.markdown(
                f'<div style="font-size:0.83rem;color:#ccc;line-height:1.6;">'
                f'<b>VIX Term Structure</b> \u2014 The CBOE VIX family shows market-wide fear pricing across time horizons. '
                f'VIX9D is 9-day (immediate fear), VIX is 30-day (standard), VIX3M/6M are longer-term. '
                f'When VIX9D > VIX > VIX3M the structure is inverted (backwardation) = acute near-term stress.<br><br>'
                f'<b>IV/HV Ranking</b> \u2014 The single most actionable chart. Compares what the market <i>expects</i> (implied vol) '
                f'to what actually <i>happened</i> (realized vol). Above 1.2x = options are expensive, premium sellers have the edge. '
                f'Below 0.85x = options are cheap, buy protection while it is underpriced.<br><br>'
                f'<b>Term Structure</b> \u2014 Shows the slope between front-month and back-month IV for each ticker. '
                f'Red (negative) = backwardation = the market is pricing a near-term event that will resolve. '
                f'Green (positive) = normal contango = no unusual event premium.<br><br>'
                f'<b>Put Skew & P/C Ratio</b> \u2014 Put skew measures how much more expensive OTM puts are vs ATM (the "fear premium"). '
                f'>1.10x = elevated fear, institutions are paying up for crash protection. '
                f'P/C ratio (diamonds) shows put vs call volume \u2014 >1.0 = more put activity = defensive positioning.<br><br>'
                f'<b>VRP Scatter</b> \u2014 Each dot is one ticker plotted by its IV percentile (x-axis) vs Variance Risk Premium (y-axis). '
                f'Top-right quadrant = high percentile + positive VRP = richest premium-selling targets. '
                f'Bottom-left = low percentile + negative VRP = cheapest protection-buying targets.<br><br>'
                f'<b>Implied Correlation</b> \u2014 Derived from the relationship between index vol (SPY) and sector vol. '
                f'High (>0.7) = everything moving together, systemic risk \u2014 index hedges work well. '
                f'Low (<0.4) = sectors diverging \u2014 dispersion trades (sell index vol, buy sector vol) are attractive.</div>',
                unsafe_allow_html=True)

        # ── VIX Context (if available) ──
        if _vix_snap and _vix_snap.get("VIX"):
            _vix_items = []
            for _k, _l in [("VIX9D", "VIX 9D"), ("VIX", "VIX"), ("VIX3M", "3M"), ("VIX6M", "6M")]:
                _v = _vix_snap.get(_k)
                if _v:
                    _vix_items.append((_l, f"{_v:.1f}"))
            if _vix_snap.get("regime"):
                _vix_items.append(("Structure", _vix_snap["regime"]))
            if _vix_items:
                _vix_html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">'
                for _vl, _vv in _vix_items:
                    _vix_html += (f'<div style="{_card_sm}flex:1;min-width:80px;">'
                                  f'<div style="font-size:0.55rem;color:{COLORS["text_muted"]};text-transform:uppercase;">{_vl}</div>'
                                  f'<div style="font-size:1rem;font-weight:700;color:#e6edf3;">{_vv}</div></div>')
                _vix_html += '</div>'
                st.markdown(_vix_html, unsafe_allow_html=True)

        # ── IV/HV Ranking (full width) ──
        st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Implied vs Realized Volatility</div>'
                    f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                    f'Are options overpriced or underpriced? Red bars (>1.2x) = sell premium. Green bars (<0.85x) = buy protection.</div>',
                    unsafe_allow_html=True)
        _ivhv_df = mdf[["Ticker", "IV_HV", "Group"]].dropna().sort_values("IV_HV", ascending=False)
        if not _ivhv_df.empty:
            fig_ivhv = go.Figure(go.Bar(
                x=_ivhv_df["Ticker"], y=_ivhv_df["IV_HV"],
                marker_color=[COLORS["danger"] if v > 1.2 else (COLORS["success"] if v < 0.85 else COLORS["accent"]) for v in _ivhv_df["IV_HV"]],
                hovertemplate="<b>%{x}</b><br>IV/HV: %{y:.2f}x<extra></extra>",
            ))
            fig_ivhv.add_hline(y=1.2, line_dash="dash", line_color=COLORS["danger"], annotation_text="Rich")
            fig_ivhv.add_hline(y=1.0, line_dash="dot", line_color=COLORS["text_muted"])
            fig_ivhv.add_hline(y=0.85, line_dash="dash", line_color=COLORS["success"], annotation_text="Cheap")
            fig_ivhv.update_layout(template="plotly_dark", height=260, margin=dict(l=0, r=0, t=10, b=0),
                                    yaxis_title="IV / HV20", showlegend=False)
            st.plotly_chart(fig_ivhv, use_container_width=True, config=PLOTLY_NOBAR)

            _n_rich = (_ivhv_df["IV_HV"] > 1.2).sum()
            _n_cheap = (_ivhv_df["IV_HV"] < 0.85).sum()
            _richest_tk = _ivhv_df.iloc[0]["Ticker"] if not _ivhv_df.empty else ""
            _cheapest_tk = _ivhv_df.iloc[-1]["Ticker"] if not _ivhv_df.empty else ""
            _ivhv_rc = COLORS["danger"] if _n_rich > _n_cheap else (COLORS["success"] if _n_cheap > _n_rich else "#e6edf3")
            st.markdown(f'<div style="{_card}border-left:3px solid {_ivhv_rc};padding:10px 14px;">'
                        f'<div style="font-size:0.82rem;color:#ccc;">'
                        f'<b>{_n_rich}</b> tickers with rich gamma (sell premium) | <b>{_n_cheap}</b> with cheap gamma (buy protection). '
                        f'Richest: <b>{_richest_tk}</b> ({_ivhv_df.iloc[0]["IV_HV"]:.2f}x). '
                        f'Cheapest: <b>{_cheapest_tk}</b> ({_ivhv_df.iloc[-1]["IV_HV"]:.2f}x).'
                        f'</div></div>', unsafe_allow_html=True)

        st.divider()
        _e1, _e2 = st.columns(2)

        # ── Term Structure ──
        with _e1:
            st.markdown(f'<div style="font-size:0.9rem;font-weight:600;color:#e6edf3;margin-bottom:4px;">Term Structure</div>'
                        f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                        f'Red = backwardation (front IV > back IV). Event risk is priced in the near-term.</div>',
                        unsafe_allow_html=True)
            _ts_df = mdf[["Ticker", "TS_Slope"]].dropna().sort_values("TS_Slope")
            fig_ts = go.Figure(go.Bar(
                x=_ts_df["Ticker"], y=_ts_df["TS_Slope"] * 100,
                marker_color=[COLORS["danger"] if v < 0 else COLORS["success"] for v in _ts_df["TS_Slope"]],
                hovertemplate="<b>%{x}</b><br>TS: %{y:.2f}%/mo<extra></extra>",
            ))
            fig_ts.add_hline(y=0, line_dash="dot", line_color=COLORS["text_muted"])
            fig_ts.update_layout(template="plotly_dark", height=230, margin=dict(l=0, r=0, t=10, b=0),
                                  yaxis_title="%/mo", showlegend=False)
            st.plotly_chart(fig_ts, use_container_width=True, config=PLOTLY_NOBAR)
            _ts_rc = COLORS["danger"] if pct_inverted > 50 else (COLORS["warning"] if pct_inverted > 20 else COLORS["success"])
            _inv_names = ", ".join(mdf[mdf["TS_Slope"] < 0]["Ticker"].tolist()[:5])
            _ts_label = "Broad Backwardation" if pct_inverted > 50 else ("Mixed" if pct_inverted > 20 else "Normal Contango")
            _ts_advice = ("Near-term event risk is widespread. Calendar spreads (sell front, buy back) exploit this."
                          if pct_inverted > 30 else
                          "No unusual near-term event pricing. Term structure is healthy.")
            st.markdown(f'<div style="{_card}border-left:3px solid {_ts_rc};padding:10px 14px;">'
                        f'<div style="font-size:0.82rem;font-weight:600;color:{_ts_rc};">{_ts_label}</div>'
                        f'<div style="font-size:0.78rem;color:{COLORS["text_muted"]};margin-top:2px;">'
                        f'{n_inverted}/{len(mdf)} tickers inverted'
                        f'{" (" + _inv_names + ")" if _inv_names else ""}. {_ts_advice}</div></div>',
                        unsafe_allow_html=True)

        # ── Skew + P/C Ratio ──
        with _e2:
            st.markdown(f'<div style="font-size:0.9rem;font-weight:600;color:#e6edf3;margin-bottom:4px;">Put Skew & P/C Ratio</div>'
                        f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                        f'Bars = put skew (fear premium). Diamonds = put/call volume ratio (positioning).</div>',
                        unsafe_allow_html=True)
            _sk_df = mdf[["Ticker", "Put_Skew", "PC_Ratio"]].dropna().sort_values("Put_Skew", ascending=False)
            fig_sk = go.Figure()
            fig_sk.add_trace(go.Bar(
                x=_sk_df["Ticker"], y=_sk_df["Put_Skew"], name="Put Skew",
                marker_color=[COLORS["danger"] if v > 1.10 else COLORS["accent"] for v in _sk_df["Put_Skew"]],
                hovertemplate="<b>%{x}</b><br>Skew: %{y:.2f}x<extra></extra>",
            ))
            fig_sk.add_trace(go.Scatter(
                x=_sk_df["Ticker"], y=_sk_df["PC_Ratio"], name="P/C Ratio",
                mode="markers", marker=dict(size=8, color=COLORS["warning"], symbol="diamond"),
                yaxis="y2", hovertemplate="<b>%{x}</b><br>P/C: %{y:.2f}<extra></extra>",
            ))
            fig_sk.add_hline(y=1.10, line_dash="dash", line_color=COLORS["danger"], annotation_text="Fear")
            fig_sk.update_layout(template="plotly_dark", height=230, margin=dict(l=0, r=0, t=10, b=0),
                                  yaxis_title="Skew", yaxis2=dict(title="P/C", overlaying="y", side="right"),
                                  legend=dict(orientation="h", y=-0.2), showlegend=True)
            st.plotly_chart(fig_sk, use_container_width=True, config=PLOTLY_NOBAR)
            _steep_names = ", ".join(mdf[mdf["Put_Skew"] > 1.10]["Ticker"].tolist()[:5])
            _skew_label = "Broad Fear" if avg_skew > 1.10 else ("Complacent" if avg_skew < 1.03 else "Normal")
            _skew_rc = COLORS["danger"] if avg_skew > 1.10 else (COLORS["success"] if avg_skew < 1.03 else "#e6edf3")
            _skew_advice = ("Puts are expensive. Sell overpriced put wings via spreads or risk reversals."
                            if n_steep_skew > len(mdf)*0.4 else
                            ("Skew is flat \u2014 cheap to buy crash protection." if avg_skew < 1.03 else
                             "Normal fear premium. No extreme signal."))
            st.markdown(f'<div style="{_card}border-left:3px solid {_skew_rc};padding:10px 14px;">'
                        f'<div style="font-size:0.82rem;font-weight:600;color:{_skew_rc};">{_skew_label} (avg {avg_skew:.2f}x)</div>'
                        f'<div style="font-size:0.78rem;color:{COLORS["text_muted"]};margin-top:2px;">'
                        f'{n_steep_skew} tickers with steep skew'
                        f'{" (" + _steep_names + ")" if _steep_names else ""}. {_skew_advice}</div></div>',
                        unsafe_allow_html=True)

        st.divider()
        _e3, _e4 = st.columns(2)

        # ── VRP Scatter with K-Means Clustering ──
        with _e3:
            st.markdown(f'<div style="font-size:0.9rem;font-weight:600;color:#e6edf3;margin-bottom:4px;">VRP Concentration (K-Means Clustered)</div>'
                        f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                        f'3 clusters: Sell Premium (rich), Neutral, Buy Protection (cheap). Shaped by IV percentile + VRP.</div>',
                        unsafe_allow_html=True)
            _vrp_df = mdf[["Ticker", "VRP_Vol", "IV_Pctile", "Group"]].dropna() if "VRP_Vol" in mdf.columns else pd.DataFrame()
            if not _vrp_df.empty and len(_vrp_df) >= 3:
                from sklearn.cluster import KMeans
                from sklearn.preprocessing import StandardScaler

                _X_vrp = _vrp_df[["IV_Pctile", "VRP_Vol"]].values.copy()
                _X_vrp[:, 1] = _X_vrp[:, 1] * 100  # scale VRP to % for clustering
                _X_scaled = StandardScaler().fit_transform(_X_vrp)
                _km = KMeans(n_clusters=min(3, len(_vrp_df)), random_state=42, n_init=10)
                _vrp_df = _vrp_df.copy()
                _vrp_df["Cluster"] = _km.fit_predict(_X_scaled)

                # Label clusters by their centroid position (highest VRP centroid = "Sell Premium")
                _centroids = _km.cluster_centers_  # in scaled space
                # Unscale centroids to interpret
                _scaler = StandardScaler().fit(_X_vrp)
                _centroids_raw = _scaler.inverse_transform(_centroids)
                # Sort clusters by VRP (column 1): highest = sell, lowest = buy
                _cluster_order = np.argsort(_centroids_raw[:, 1])  # ascending VRP
                _cluster_labels = {}
                _cluster_colors = {}
                _label_names = ["Buy Protection", "Neutral", "Sell Premium"]
                _label_colors = [COLORS["success"], COLORS["warning"], COLORS["danger"]]
                for i, cidx in enumerate(_cluster_order):
                    _cluster_labels[cidx] = _label_names[i] if i < len(_label_names) else "Other"
                    _cluster_colors[cidx] = _label_colors[i] if i < len(_label_colors) else "#888"

                fig_vrp = go.Figure()
                for cidx in sorted(_cluster_labels.keys()):
                    _sub = _vrp_df[_vrp_df["Cluster"] == cidx]
                    if _sub.empty:
                        continue
                    _cl = _cluster_labels[cidx]
                    _cc = _cluster_colors[cidx]
                    fig_vrp.add_trace(go.Scatter(
                        x=_sub["IV_Pctile"], y=_sub["VRP_Vol"] * 100,
                        mode="markers+text", text=_sub["Ticker"], textposition="top center",
                        textfont=dict(size=9), marker=dict(size=12, color=_cc, line=dict(width=1, color="#333")),
                        name=_cl,
                        hovertemplate="<b>%{text}</b><br>Pctile: %{x:.0f}<br>VRP: %{y:.1f}%<br>Cluster: " + _cl + "<extra></extra>",
                    ))

                # Draw cluster boundaries as ellipses (approximate with shapes)
                fig_vrp.add_hline(y=0, line_dash="dot", line_color=COLORS["text_muted"])
                fig_vrp.add_vline(x=50, line_dash="dot", line_color=COLORS["text_muted"])
                fig_vrp.update_layout(template="plotly_dark", height=320, margin=dict(l=0, r=0, t=10, b=0),
                                      xaxis_title="IV Percentile (IV/HV ranked)", yaxis_title="VRP (IV \u2212 HV, %)",
                                      legend=dict(orientation="h", y=-0.18))
                st.plotly_chart(fig_vrp, use_container_width=True, config=PLOTLY_NOBAR)

                # Cluster summary
                _cl_summary = []
                for cidx in sorted(_cluster_labels.keys()):
                    _sub = _vrp_df[_vrp_df["Cluster"] == cidx]
                    if _sub.empty:
                        continue
                    _cl = _cluster_labels[cidx]
                    _cc = _cluster_colors[cidx]
                    _tks = ", ".join(_sub["Ticker"].tolist())
                    _cl_summary.append(f'<span style="color:{_cc};font-weight:600;">{_cl}</span>: {_tks}')
                st.markdown(f'<div style="{_card}padding:10px 14px;">'
                            f'<div style="font-size:0.78rem;color:#ccc;">' + " &nbsp;|&nbsp; ".join(_cl_summary) + '</div></div>',
                            unsafe_allow_html=True)
            elif not _vrp_df.empty:
                st.caption("Need at least 3 tickers with VRP data for clustering.")

        # ── Implied Correlation + Gauge ──
        with _e4:
            st.markdown(f'<div style="font-size:0.9rem;font-weight:600;color:#e6edf3;margin-bottom:4px;">Implied Correlation</div>'
                        f'<div style="font-size:0.68rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                        f'Low (<0.4) = dispersion opportunity. High (>0.7) = systemic risk.</div>',
                        unsafe_allow_html=True)
            if impl_corr is not None:
                _cc = COLORS["danger"] if impl_corr > 0.7 else (COLORS["success"] if impl_corr < 0.4 else COLORS["warning"])
                fig_corr = go.Figure(go.Indicator(
                    mode="gauge+number", value=impl_corr, number=dict(font=dict(size=36), valueformat=".2f"),
                    gauge=dict(axis=dict(range=[0, 1]), bar=dict(color=_cc),
                               steps=[dict(range=[0, 0.4], color="rgba(0,255,150,0.08)"),
                                      dict(range=[0.4, 0.7], color="rgba(255,170,0,0.08)"),
                                      dict(range=[0.7, 1.0], color="rgba(255,68,68,0.08)")]),
                ))
                fig_corr.update_layout(template="plotly_dark", height=220, margin=dict(l=30, r=30, t=10, b=0))
                st.plotly_chart(fig_corr, use_container_width=True, config=PLOTLY_NOBAR)
                _dl = "Dispersion Opportunity" if impl_corr < 0.4 else ("Systemic Risk" if impl_corr > 0.7 else "Normal Range")
                _corr_advice = ("Sectors diverging. Sell SPY vol, buy sector vol."
                                if impl_corr < 0.4 else
                                ("Everything correlated. Index hedges most efficient."
                                 if impl_corr > 0.7 else "Normal range."))
                st.markdown(f'<div style="{_card}border-left:3px solid {_cc};padding:10px 14px;">'
                            f'<div style="font-size:0.82rem;font-weight:600;color:{_cc};">{_dl}</div>'
                            f'<div style="font-size:0.78rem;color:{COLORS["text_muted"]};margin-top:2px;">{_corr_advice}</div></div>',
                            unsafe_allow_html=True)

        # ── Correlation Matrix (full width) ──
        if corr_df is not None and len(corr_tickers) >= 5:
            st.divider()
            st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Return Correlation Matrix</div>'
                        f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                        f'60-day pairwise return correlations. Red = highly correlated. Blue = inversely correlated.</div>',
                        unsafe_allow_html=True)
            fig_cm = go.Figure(go.Heatmap(
                z=corr_df.values, x=corr_tickers, y=corr_tickers,
                colorscale="RdBu_r", zmid=0, zmin=-1, zmax=1,
                text=np.round(corr_df.values, 2), texttemplate="%{text}",
                textfont=dict(size=9),
                colorbar=dict(title="Corr", len=0.6),
                hovertemplate="<b>%{x}</b> vs <b>%{y}</b><br>Corr: %{z:.2f}<extra></extra>",
            ))
            fig_cm.update_layout(template="plotly_dark",
                                  height=max(400, len(corr_tickers) * 24 + 80),
                                  margin=dict(l=0, r=0, t=10, b=30),
                                  yaxis=dict(autorange="reversed"))
            st.plotly_chart(fig_cm, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Vol Change Scatter ──
        _iv_chg_col = "Front_IV_chg"
        if _iv_chg_col in mdf.columns and mdf[_iv_chg_col].notna().any():
            st.divider()
            st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Vol Momentum</div>'
                        f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:6px;">'
                        f'X = IV change since last scan. Y = current IV level. Top-right = high and rising.</div>',
                        unsafe_allow_html=True)
            _vc_df = mdf[["Ticker", "Front_IV", _iv_chg_col, "Group"]].dropna()
            if len(_vc_df) >= 3:
                fig_vc = go.Figure()
                for grp, gc in [("Sectors", COLORS["accent"]), ("Macro", COLORS["warning"])]:
                    _sub = _vc_df[_vc_df["Group"] == grp]
                    if not _sub.empty:
                        fig_vc.add_trace(go.Scatter(
                            x=_sub[_iv_chg_col] * 100, y=_sub["Front_IV"] * 100,
                            mode="markers+text", text=_sub["Ticker"], textposition="top center",
                            textfont=dict(size=9), marker=dict(size=10, color=gc), name=grp,
                            hovertemplate="<b>%{text}</b><br>\u0394IV: %{x:+.1f}%<br>IV: %{y:.1f}%<extra></extra>",
                        ))
                fig_vc.add_vline(x=0, line_dash="dot", line_color=COLORS["text_muted"])
                fig_vc.update_layout(template="plotly_dark", height=280, margin=dict(l=0, r=0, t=10, b=0),
                                      xaxis_title="\u0394 IV Since Last Scan (%)", yaxis_title="Current IV (%)",
                                      legend=dict(orientation="h", y=-0.2))
                st.plotly_chart(fig_vc, use_container_width=True, config=PLOTLY_NOBAR)

        # ── Sectors vs Macro ──
        st.divider()
        st.markdown(f'<div style="font-size:0.9rem;font-weight:600;color:#e6edf3;margin-bottom:6px;">Sectors vs Macro</div>',
                    unsafe_allow_html=True)
        _comp_rows = []
        for grp in ["Sectors", "Macro"]:
            _g = mdf[mdf["Group"] == grp]
            if not _g.empty:
                _comp_rows.append({
                    "": grp, "Avg IV": f"{_g['Front_IV'].mean():.1%}",
                    "Avg IV/HV": f"{_g['IV_HV'].dropna().mean():.2f}x" if _g['IV_HV'].notna().any() else "N/A",
                    "Avg Skew": f"{_g['Put_Skew'].mean():.2f}x",
                    "Inverted": f"{(_g['TS_Slope'] < 0).sum()}/{len(_g)}",
                    "Steep Skew": f"{(_g['Put_Skew'] > 1.10).sum()}/{len(_g)}",
                })
        if _comp_rows:
            st.dataframe(pd.DataFrame(_comp_rows).set_index(""), use_container_width=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — METRICS TABLE WITH CHANGES
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    with error_boundary("Metrics"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Full Metrics</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Click column headers to sort. Change columns show delta since last scan.</div>',
                    unsafe_allow_html=True)

        _table_cols = {
            "Ticker": "Ticker", "Label": "Name", "Group": "Group", "Spot": "Spot",
            "Front_IV": "Front IV", "Front_IV_chg": "\u0394 IV",
            "Back_IV": "Back IV", "IV_HV": "IV/HV", "IV_HV_chg": "\u0394 IV/HV",
            "Put_Skew": "Skew", "Put_Skew_chg": "\u0394 Skew",
            "Risk_Rev": "RR", "Butterfly": "Fly",
            "TS_Slope": "TS", "TS_Slope_chg": "\u0394 TS",
            "VRP_Vol": "VRP", "VRP_Vol_chg": "\u0394 VRP",
            "Impl_Move": "Move%", "HV20": "HV20", "PC_Ratio": "P/C", "IV_Pctile": "Pctile",
        }
        _available = [c for c in _table_cols if c in mdf.columns]
        _disp = mdf[_available].copy()
        _disp.columns = [_table_cols[c] for c in _available]

        # Format
        _fmt = _disp.copy()
        for c in ["Front IV", "Back IV", "HV20", "VRP"]:
            if c in _fmt.columns:
                _fmt[c] = _fmt[c].apply(lambda v: f"{v:.1%}" if pd.notna(v) else "")
        for c in ["\u0394 IV", "\u0394 VRP"]:
            if c in _fmt.columns:
                _fmt[c] = _fmt[c].apply(lambda v: f"{v:+.1%}" if pd.notna(v) else "")
        for c in ["IV/HV", "Skew", "P/C"]:
            if c in _fmt.columns:
                _fmt[c] = _fmt[c].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "")
        for c in ["\u0394 IV/HV", "\u0394 Skew"]:
            if c in _fmt.columns:
                _fmt[c] = _fmt[c].apply(lambda v: f"{v:+.3f}" if pd.notna(v) else "")
        if "Spot" in _fmt.columns:
            _fmt["Spot"] = _fmt["Spot"].apply(lambda v: f"${v:.2f}")
        for c in ["RR", "Fly"]:
            if c in _fmt.columns:
                _fmt[c] = _fmt[c].apply(lambda v: f"{v:+.1f}" if pd.notna(v) and v != 0 else "")
        if "TS" in _fmt.columns:
            _fmt["TS"] = _fmt["TS"].apply(lambda v: f"{v*100:+.2f}" if pd.notna(v) else "")
        if "\u0394 TS" in _fmt.columns:
            _fmt["\u0394 TS"] = _fmt["\u0394 TS"].apply(lambda v: f"{v*100:+.3f}" if pd.notna(v) else "")
        if "Move%" in _fmt.columns:
            _fmt["Move%"] = _fmt["Move%"].apply(lambda v: f"{v:.1f}%" if v else "")
        if "Pctile" in _fmt.columns:
            _fmt["Pctile"] = _fmt["Pctile"].apply(lambda v: f"{v:.0f}" if pd.notna(v) else "")

        st.dataframe(_fmt.set_index("Ticker"), use_container_width=True, height=min(600, len(_fmt) * 35 + 40))
        st.download_button("Download CSV", data=mdf.to_csv(index=False),
                           file_name=f"vol_landscape_{datetime.now().strftime('%Y%m%d')}.csv",
                           mime="text/csv", key="vl_dl")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SIGNALS & ALERTS (with divergences)
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    with error_boundary("Signals"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:10px;">Signals & Alerts</div>',
                    unsafe_allow_html=True)

        # Regime
        st.markdown(f'<div style="{_card}border-left:4px solid {regime_color};">'
                    f'<div style="font-size:0.95rem;font-weight:700;color:{regime_color};">{vol_regime}</div>'
                    f'<div style="font-size:0.82rem;color:#ccc;margin-top:4px;">{regime_action}</div></div>',
                    unsafe_allow_html=True)

        # Divergences
        if divergences:
            st.markdown(f'<div style="font-size:0.95rem;font-weight:700;color:#e6edf3;margin:12px 0 8px 0;">Cross-Asset Divergences</div>',
                        unsafe_allow_html=True)
            for dv in divergences[:5]:
                _dv_color = COLORS["warning"]
                st.markdown(f'<div style="{_card}border-left:4px solid {_dv_color};padding:10px 14px;">'
                            f'<div style="font-size:0.85rem;font-weight:700;color:{_dv_color};">{dv["pair"]} \u2014 {dv["metric"]} Divergence</div>'
                            f'<div style="font-size:0.78rem;color:{COLORS["text_muted"]};">{dv["description"]}</div>'
                            f'<div style="font-size:0.82rem;color:#ccc;margin-top:4px;">{dv["signal"]}</div></div>',
                            unsafe_allow_html=True)

        # Richest / Cheapest
        _s1, _s2 = st.columns(2)
        with _s1:
            st.markdown(f'<div style="{_card}border-left:4px solid #ff4444;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:#ff4444;">Richest Vol</div>', unsafe_allow_html=True)
            for _, r in mdf.nlargest(3, "Front_IV").iterrows():
                _iv = f"{r['IV_HV']:.2f}x" if pd.notna(r.get('IV_HV')) else ""
                st.markdown(f'<div style="font-size:0.82rem;color:#ccc;padding:1px 0;"><b>{r["Ticker"]}</b> IV:{r["Front_IV"]:.1%} {_iv}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)
        with _s2:
            st.markdown(f'<div style="{_card}border-left:4px solid #00ff96;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:#00ff96;">Cheapest Vol</div>', unsafe_allow_html=True)
            for _, r in mdf.nsmallest(3, "Front_IV").iterrows():
                _iv = f"{r['IV_HV']:.2f}x" if pd.notna(r.get('IV_HV')) else ""
                st.markdown(f'<div style="font-size:0.82rem;color:#ccc;padding:1px 0;"><b>{r["Ticker"]}</b> IV:{r["Front_IV"]:.1%} {_iv}</div>', unsafe_allow_html=True)
            st.markdown('</div>', unsafe_allow_html=True)

        # Correlation risk
        if impl_corr is not None and impl_corr > 0.7 and n_inverted >= 3:
            st.markdown(f'<div style="{_card}border-left:4px solid #ff4444;">'
                        f'<div style="font-size:0.95rem;font-weight:700;color:#ff4444;">SYSTEMIC EVENT RISK</div>'
                        f'<div style="font-size:0.82rem;color:#ccc;margin-top:4px;">'
                        f'Corr {impl_corr:.2f} + {n_inverted} inverted = correlated shock. Index hedges efficient.</div></div>', unsafe_allow_html=True)
        elif impl_corr is not None and impl_corr < 0.35:
            st.markdown(f'<div style="{_card}border-left:4px solid #00ff96;">'
                        f'<div style="font-size:0.95rem;font-weight:700;color:#00ff96;">DISPERSION OPPORTUNITY</div>'
                        f'<div style="font-size:0.82rem;color:#ccc;margin-top:4px;">'
                        f'Corr {impl_corr:.2f} = sell index vol, buy sector vol.</div></div>', unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — AI ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    with error_boundary("AI Analysis"):
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">AI Market Vol Analysis</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Gemini 3.1 Pro reads the full landscape and delivers a comprehensive briefing.</div>',
                    unsafe_allow_html=True)

        from src.api_keys import get_secret as _get_key
        _gemini_key = _get_key("GEMINI_API_KEY")

        if not _gemini_key:
            st.error("Gemini API key not configured.")
        else:
            # Group selector for focused analysis
            _ai_focus = st.radio("Focus", ["Full Landscape", "Sectors Only", "Macro Only"], horizontal=True, key="vl_ai_focus")

            def _build_context():
                _focus_mdf = mdf if _ai_focus == "Full Landscape" else mdf[mdf["Group"] == _ai_focus.replace(" Only", "")]
                lines = [f"CROSS-ASSET OPTIONS LANDSCAPE ({len(_focus_mdf)} tickers, {_ai_focus})", ""]
                lines.append(f"REGIME: {vol_regime} | ACTION: {regime_action}")
                lines.append(f"Avg IV: {avg_iv:.1%} | IV/HV: {avg_ivhv:.2f}x | Pctile: {avg_pctile:.0f}th | Skew: {avg_skew:.2f}x")
                lines.append(f"Inverted: {n_inverted}/{len(mdf)} | Steep: {n_steep_skew}/{len(mdf)}")
                if impl_corr is not None: lines.append(f"Impl Corr: {impl_corr:.2f}")
                if _vix_snap and _vix_snap.get("VIX"):
                    lines.append(f"VIX: {_vix_snap['VIX']:.1f} | VIX3M: {_vix_snap.get('VIX3M', 0):.1f} | Structure: {_vix_snap.get('regime', 'N/A')}")
                lines.append("")
                for _, r in _focus_mdf.iterrows():
                    parts = [f"{r['Ticker']} ({r['Label']}) [{r['Group']}] ${r['Spot']:.2f}"]
                    parts.append(f"IV={r['Front_IV']:.1%}")
                    if pd.notna(r.get('IV_HV')): parts.append(f"IV/HV={r['IV_HV']:.2f}x")
                    parts.append(f"Skew={r['Put_Skew']:.2f}x RR={r['Risk_Rev']:+.1f}")
                    if pd.notna(r.get('Butterfly')) and r.get('Butterfly') != 0: parts.append(f"Fly={r['Butterfly']:+.1f}")
                    parts.append(f"TS={r['TS_Slope']*100:+.1f}%/mo")
                    if pd.notna(r.get('VRP_Vol')): parts.append(f"VRP={r['VRP_Vol']:+.1%}")
                    if r.get('Impl_Move') and r['Impl_Move'] > 0: parts.append(f"Move={r['Impl_Move']:.1f}%")
                    if pd.notna(r.get('HV20')): parts.append(f"HV20={r['HV20']:.1%}")
                    if pd.notna(r.get('IV_Pctile')): parts.append(f"Pctile={r['IV_Pctile']:.0f}")
                    # Changes
                    for _cc in ["Front_IV_chg", "IV_HV_chg", "Put_Skew_chg"]:
                        if _cc in r.index and pd.notna(r.get(_cc)):
                            _cn = _cc.replace("_chg", "").replace("Front_", "").replace("Put_", "")
                            parts.append(f"d{_cn}={r[_cc]:+.3f}")
                    lines.append("  " + " | ".join(parts))
                if divergences:
                    lines.append("\n--- DIVERGENCES ---")
                    for dv in divergences[:3]:
                        lines.append(f"  {dv['pair']} ({dv['metric']}): {dv['signal']}")
                if _earnings_dates:
                    lines.append("\n--- EARNINGS WITHIN FRONT EXPIRATION ---")
                    for _etk, _ev in _earnings_dates.items():
                        lines.append(f"  {_etk}: {_ev['date']} ({_ev['days']} days)")
                if _net_premium:
                    _top_put = sorted(_net_premium.items(), key=lambda x: x[1].get("put_prem", 0), reverse=True)[:3]
                    if _top_put:
                        lines.append("\n--- NET PREMIUM FLOW (top put $ flow) ---")
                        for _ptk, _pv in _top_put:
                            lines.append(f"  {_ptk}: Put${_pv['put_prem']/1e6:.1f}M Call${_pv['call_prem']/1e6:.1f}M Net P/C={_pv['net_pc']:.2f}")
                if _benchmarks:
                    _above = [(tk, b) for tk, b in _benchmarks.items() if b.get("iv_vs_hv30d") and b["iv_vs_hv30d"] > 30]
                    _below = [(tk, b) for tk, b in _benchmarks.items() if b.get("iv_vs_hv30d") and b["iv_vs_hv30d"] < -20]
                    if _above or _below:
                        lines.append("\n--- BENCHMARK CONTEXT (IV vs 30d avg HV) ---")
                        for tk, b in sorted(_above, key=lambda x: x[1]["iv_vs_hv30d"], reverse=True)[:3]:
                            lines.append(f"  {tk}: IV is {b['iv_vs_hv30d']:+.0f}% ABOVE 30d avg ({b['hv_30d_avg']:.1%})")
                        for tk, b in sorted(_below, key=lambda x: x[1]["iv_vs_hv30d"])[:3]:
                            lines.append(f"  {tk}: IV is {b['iv_vs_hv30d']:+.0f}% BELOW 30d avg ({b['hv_30d_avg']:.1%})")
                try:
                    from src.cross_context import build_ai_context
                    _xctx = build_ai_context()
                    if _xctx: lines.append(f"\n--- CROSS-PAGE ---\n{_xctx}")
                except Exception: pass
                return "\n".join(lines)

            st.caption("~\\$0.08 per generation")

            @st.fragment
            def _ai_frag():
                if st.button("Generate Analysis", type="primary", use_container_width=True, key="vl_ai_run"):
                    from src.ai_validation import ACCURACY_CHECK, VOL_SURFACE_EXPERT_CONTEXT
                    _ctx = _build_context()
                    _prompt = (
                        "You are the head of cross-asset volatility research at a multi-strategy hedge fund.\n\n"
                        f"{VOL_SURFACE_EXPERT_CONTEXT}\n\n"
                        "## Executive Summary\nThe most important thing about the vol landscape. 3 sentences.\n\n"
                        "## What the Market is Pricing\nFear, complacency, or event? Reference IV, VRP, skew, VIX.\n\n"
                        "## Sector Rotation Signal\nRichest vs cheapest vol by sector. Where is hedging demand?\n\n"
                        "## Divergence Alerts\nWhich correlated pairs are diverging? What does it signal?\n\n"
                        "## Term Structure Message\nContango vs backwardation pattern. Event timing.\n\n"
                        "## Opportunity Map\nTop 3-5 trades with specific tickers and types.\n\n"
                        "## Portfolio Positioning\nNet long/short vol? Sector weights? One paragraph.\n\n"
                        "RULES: Specific tickers and numbers. Direct. Each section 2-4 sentences. Under 600 words.\n\n"
                        f"DATA:\n{_ctx}\n\n{ACCURACY_CHECK}"
                    )
                    with fun_loader("ai"):
                        try:
                            from google import genai
                            from google.genai import types
                            client = genai.Client(api_key=_gemini_key)
                            resp = client.models.generate_content(
                                model="gemini-3.1-pro-preview", contents=_prompt,
                                config=types.GenerateContentConfig(max_output_tokens=5000, temperature=0.3))
                            st.session_state["vl_ai_result"] = resp.text
                            st.session_state["vl_ai_focus_used"] = _ai_focus
                        except Exception as e:
                            st.error(f"AI failed: {e}")

                if "vl_ai_result" in st.session_state:
                    _used = st.session_state.get("vl_ai_focus_used", "")
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
                        f'<div style="border:1px solid {COLORS["accent"]};border-radius:4px;padding:2px 8px;'
                        f'font-size:0.7rem;color:{COLORS["accent"]};font-weight:600;">GEMINI 3.1 PRO</div>'
                        f'<span style="font-size:0.75rem;color:{COLORS["text_muted"]};">{_used}</span></div>',
                        unsafe_allow_html=True)
                    st.markdown(st.session_state["vl_ai_result"].replace("$", "\\$"))

                    # Download briefing
                    st.download_button("Download Briefing", data=st.session_state["vl_ai_result"],
                                       file_name=f"vol_briefing_{datetime.now().strftime('%Y%m%d')}.md",
                                       mime="text/markdown", key="vl_dl_brief")

                    with st.expander("View AI Context", expanded=False):
                        st.code(_build_context(), language="text")
                    st.caption("AI-generated. Not financial advice.")

            _ai_frag()


# ─── FOOTER ───────────────────────────────────────────────────────────────────

st.markdown(
    f'<div style="margin-top:24px;padding:14px 20px;border-top:1px solid {COLORS["card_border"]};'
    f'display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">'
    f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">'
    f'Cross-asset options data from Polygon API. Not financial advice.</div>'
    f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};opacity:0.7;">'
    f'{len(ticker_data)} tickers | {len(mdf)} metrics</div></div>', unsafe_allow_html=True)
