import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging

from src.layout import setup_page, get_active_ticker, set_active_ticker, error_boundary, fun_loader
from src.styles import COLORS
from src.data_engine import (
    format_massive_ticker, fetch_massive_data,
    get_expiration_dates, fetch_options_chain,
    fetch_options_surface_history,
    render_data_source_footer,
)
from src.options_models import fill_missing_options_data, implied_vol

import json
import os
from pathlib import Path

logger = logging.getLogger(__name__)

setup_page("43_Vol_Surface")

# ─── DAILY IV SURFACE SNAPSHOT CACHE ─────────────────────────────────────────
# Stores the full IV surface each day. Supabase primary, local JSON fallback.
_SURFACE_CACHE_DIR = Path("data/iv_surface_cache")
_SURFACE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _save_surface_snapshot(ticker: str, spot: float, term_data: dict, expirations: list):
    """Save today's IV surface snapshot."""
    today_str = pd.Timestamp.now().strftime("%Y-%m-%d")

    rows = []
    for exp in expirations:
        chain = term_data.get(exp)
        if chain is None:
            continue
        dte = max((pd.to_datetime(exp) - pd.Timestamp.now()).days, 0)
        if dte <= 0:
            continue
        for _, row in chain.iterrows():
            k = row.get("strike_price", 0)
            iv = row.get("implied_volatility", 0) or 0
            delta = row.get("delta", 0) or 0
            gamma = row.get("gamma", 0) or 0
            ct = row.get("contract_type", "")
            if iv > 0 and k > 0:
                rows.append({
                    "strike": float(k), "dte": int(dte), "iv": float(iv),
                    "delta": float(delta), "gamma": float(gamma),
                    "type": ct, "exp": exp,
                })

    if not rows:
        return

    payload = {"ticker": ticker, "date": today_str, "spot": float(spot), "data": rows}

    # Try Supabase first
    try:
        from src.db import get_client, get_user_id
        db = get_client()
        if db:
            db.table("iv_surface_snapshots").upsert({
                "user_id": get_user_id(), "ticker": ticker, "date": today_str,
                "spot": float(spot), "data": json.dumps(rows),
            }, on_conflict="user_id,ticker,date").execute()
            return
    except Exception as e:
        logger.warning(f"Supabase surface snapshot failed: {e}")

    # Local fallback
    cache_file = _SURFACE_CACHE_DIR / f"{ticker}_{today_str}.json"
    if not cache_file.exists():
        try:
            cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to save IV snapshot locally: {e}")


def _load_surface_snapshots(ticker: str, n_days: int = 10) -> list[dict]:
    """Load historical IV surface snapshots."""
    # Try Supabase first
    try:
        from src.db import get_client, get_user_id
        db = get_client()
        if db:
            result = db.table("iv_surface_snapshots").select("*")\
                .eq("ticker", ticker).eq("user_id", get_user_id())\
                .order("date", desc=True).limit(n_days).execute()
            rows = result.data or []
            if rows:
                snapshots = []
                for r in rows:
                    data = r.get("data")
                    if isinstance(data, str):
                        data = json.loads(data)
                    snapshots.append({
                        "ticker": r["ticker"], "date": r["date"],
                        "spot": r["spot"], "data": data,
                    })
                return list(reversed(snapshots))
    except Exception as e:
        logger.warning(f"Supabase surface snapshot load failed: {e}")

    # Local fallback
    files = sorted(_SURFACE_CACHE_DIR.glob(f"{ticker}_*.json"), reverse=True)
    snapshots = []
    for f in files[:n_days]:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            snapshots.append(data)
        except Exception:
            continue
    return list(reversed(snapshots))

st.title("Volatility Surface")
st.markdown(
    "The vol surface is the single most important tool for options traders. "
    "It shows every option's implied volatility across strikes and expirations — "
    "revealing where the market prices risk, where opportunities hide, and what events "
    "the market is bracing for."
)

PLOTLY_NOBAR = {"scrollZoom": False, "displayModeBar": False, "doubleClick": "reset"}


# ─── HELPERS ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def _get_rfr():
    try:
        from src.market_data import fetch_fred_series
        df = fetch_fred_series("DGS3MO", periods=5)
        if not df.empty:
            return df["value"].iloc[-1] / 100
    except Exception:
        pass
    return 0.045


def _dte(exp_str):
    return max((pd.to_datetime(exp_str) - pd.Timestamp.now()).days, 0)


def _atm_strike(chain, spot):
    strikes = chain["strike_price"].unique()
    return float(min(strikes, key=lambda s: abs(s - spot)))


def _get_contract(chain, strike, opt_type):
    mask = (chain["strike_price"] == strike) & (chain["contract_type"] == opt_type)
    sub = chain[mask]
    return sub.iloc[0] if not sub.empty else None


def _atm_iv(chain, spot, opt_type="call"):
    strike = _atm_strike(chain, spot)
    row = _get_contract(chain, strike, opt_type)
    if row is not None:
        iv = row.get("implied_volatility", 0) or 0
        if iv > 0:
            return iv
    return 0.25


def _find_delta_strike(chain, spot, target_delta, opt_type):
    """Find the strike closest to a target delta."""
    sub = chain[chain["contract_type"] == opt_type].copy()
    sub = sub[sub["implied_volatility"] > 0]
    if sub.empty:
        return None, None
    sub["delta_abs"] = sub["delta"].abs()
    sub["delta_dist"] = (sub["delta_abs"] - abs(target_delta)).abs()
    sub = sub.dropna(subset=["delta_dist"])
    if sub.empty:
        return None, None
    best = sub.loc[sub["delta_dist"].idxmin()]
    return float(best["strike_price"]), float(best.get("implied_volatility", 0))


# ─── CONTROLS ──────────────────────────────────────────────────────────────────

_c1, _c2 = st.columns([3, 1])
with _c1:
    raw_ticker = st.text_input("Ticker", value=get_active_ticker())
    ticker = format_massive_ticker(raw_ticker)
    set_active_ticker(ticker)
with _c2:
    st.markdown("<br>", unsafe_allow_html=True)
    submit = st.button("Load Surface", type="primary", use_container_width=True)

# ─── FETCH ─────────────────────────────────────────────────────────────────────

if submit:
    with fun_loader("data"):
        all_exps = get_expiration_dates(ticker)
        today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
        year_end = f"{pd.Timestamp.now().year}-12-31"
        valid_exps = [e for e in (all_exps or []) if today_str <= e <= year_end]
        if len(valid_exps) < 4:
            valid_exps = [e for e in (all_exps or []) if e >= today_str]

        # Monthly (3rd Friday) + nearest 6 weeklies
        monthly = [e for e in valid_exps
                   if 15 <= pd.to_datetime(e).day <= 21 and pd.to_datetime(e).weekday() == 4]
        weekly = [e for e in valid_exps if e not in monthly][:6]
        prefetch = sorted(set(monthly + weekly))
        if len(prefetch) < 4:
            prefetch = valid_exps[:12]

        px_df = fetch_massive_data(ticker, 252)
        if px_df is None or px_df.empty:
            st.error("Could not fetch price data.")
            st.stop()
        spot = float(px_df["Close"].iloc[-1])
        if pd.isna(spot) or spot <= 0:
            st.error("Invalid price data — last close is missing or zero.")
            st.stop()
        rfr = _get_rfr()

        term_data = {}
        progress = st.progress(0, text="Loading vol surface...")

        # Parallelize chain fetches — 3-5x faster than sequential
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_exp(exp):
            try:
                tdf = fetch_options_chain(ticker, exp)
                if tdf is not None and not tdf.empty:
                    tdf = fill_missing_options_data(tdf, spot, risk_free_rate=rfr)
                    return exp, tdf
            except Exception:
                pass
            return exp, None

        completed = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {executor.submit(_fetch_exp, exp): exp for exp in prefetch}
            for future in as_completed(futures):
                completed += 1
                exp, tdf = future.result()
                if tdf is not None:
                    term_data[exp] = tdf
                progress.progress(completed / len(prefetch), text=f"Loaded {completed}/{len(prefetch)} expirations...")
        progress.empty()

        if len(term_data) < 2:
            st.error("Could not load enough expiration data.")
            st.stop()

        # Save daily snapshot for historical animation
        _save_surface_snapshot(ticker, spot, term_data, sorted(term_data.keys()))

        st.session_state["vs_term"] = term_data
        st.session_state["vs_spot"] = spot
        st.session_state["vs_ticker"] = ticker
        st.session_state["vs_px"] = px_df
        st.session_state["vs_exps"] = sorted(term_data.keys())
        # Clear stale data from previous ticker
        st.session_state.pop("vs_hist_surface", None)
        st.session_state.pop("vs_comp_result", None)
        st.session_state.pop("vs_gemini_result", None)
        st.session_state.pop("vs_gemini_style_used", None)
        st.session_state.pop("_spot_cache_t4", None)

# ─── GATE ──────────────────────────────────────────────────────────────────────

if "vs_term" not in st.session_state:
    st.info("Enter a ticker and click **Load Surface** to begin.")
    st.stop()

term_data = st.session_state["vs_term"]
spot = st.session_state["vs_spot"]
ticker_display = st.session_state["vs_ticker"]
px_df = st.session_state["vs_px"]
expirations = st.session_state["vs_exps"]

# Compute realized vol once (used by Tab 3 and Tab 4)
_returns = px_df["Close"].pct_change().dropna() if px_df is not None else pd.Series(dtype=float)
_hv20_series = _returns.rolling(20).std() * np.sqrt(252) if len(_returns) > 20 else pd.Series(dtype=float)
_hv60_series = _returns.rolling(60).std() * np.sqrt(252) if len(_returns) > 60 else pd.Series(dtype=float)
current_hv20 = float(_hv20_series.dropna().iloc[-1]) if len(_hv20_series.dropna()) > 0 else None
current_hv60 = float(_hv60_series.dropna().iloc[-1]) if len(_hv60_series.dropna()) > 0 else None

# ─── PRECOMPUTE ATM IV CACHE (used across tabs 1,3,5,9) ──────────────────────
_atm_iv_cache = {}
for _exp in expirations:
    _atm_iv_cache[_exp] = _atm_iv(term_data[_exp], spot, "call")

# ─── VOL REGIME CLASSIFICATION ────────────────────────────────────────────────
_front_atm = _atm_iv_cache.get(expirations[0], 0.25) if expirations else 0.25
_back_atm = _atm_iv_cache.get(expirations[-1], 0.25) if len(expirations) >= 2 else _front_atm
_p25_s, _p25_iv = _find_delta_strike(term_data[expirations[0]], spot, 0.25, "put") if expirations else (None, None)
_put_skew_ratio = _p25_iv / _front_atm if _p25_iv and _front_atm > 0 else 1.0
_vrp = (_front_atm - current_hv20) if current_hv20 and current_hv20 > 0 else 0
_ts_shape = "Backwardation" if _front_atm > _back_atm else "Contango"

# Regime rules
_vol_level = "High" if _front_atm > 0.35 else ("Low" if _front_atm < 0.15 else "Normal")
_skew_regime = "Heavy Fear" if _put_skew_ratio > 1.18 else ("Complacent" if _put_skew_ratio < 1.05 else "Normal")
_vrp_regime = "Rich" if _vrp > 0.04 else ("Cheap" if _vrp < -0.02 else "Fair")

_regime_color = COLORS["danger"] if _vol_level == "High" or _skew_regime == "Heavy Fear" else (
    COLORS["success"] if _vol_level == "Low" and _vrp_regime == "Cheap" else COLORS["warning"]
)

st.markdown(
    f'<div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px;padding:10px 14px;'
    f'border:1px solid {_regime_color}40;border-radius:8px;background:{_regime_color}08;">'
    f'<div style="flex:1 1 120px;text-align:center;">'
    f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Vol Level</div>'
    f'<div style="font-size:1rem;font-weight:700;color:{COLORS["danger"] if _vol_level == "High" else COLORS["success"] if _vol_level == "Low" else COLORS["warning"]};">{_vol_level} ({_front_atm:.0%})</div></div>'
    f'<div style="flex:1 1 120px;text-align:center;">'
    f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">VRP</div>'
    f'<div style="font-size:1rem;font-weight:700;color:{COLORS["danger"] if _vrp_regime == "Rich" else COLORS["success"] if _vrp_regime == "Cheap" else COLORS["warning"]};">{_vrp_regime} ({_vrp:+.1%})</div></div>'
    f'<div style="flex:1 1 120px;text-align:center;">'
    f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Skew</div>'
    f'<div style="font-size:1rem;font-weight:700;color:{COLORS["danger"] if _skew_regime == "Heavy Fear" else COLORS["success"] if _skew_regime == "Complacent" else COLORS["warning"]};">{_skew_regime} ({_put_skew_ratio:.2f}x)</div></div>'
    f'<div style="flex:1 1 120px;text-align:center;">'
    f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;">Term Structure</div>'
    f'<div style="font-size:1rem;font-weight:700;color:{COLORS["danger"] if _ts_shape == "Backwardation" else COLORS["accent"]};">{_ts_shape}</div></div>'
    f'</div>',
    unsafe_allow_html=True,
)

# Write vol signal + save metrics snapshot
try:
    from src.signal_engine import write_signal
    vol_dir = "bear" if _vrp_regime == "Rich" and _skew_regime == "Heavy Fear" else (
        "bull" if _vrp_regime == "Cheap" and _skew_regime == "Complacent" else "neutral"
    )
    vol_view = "short_vol" if _vrp_regime == "Rich" else ("long_vol" if _vrp_regime == "Cheap" else "neutral")
    vol_conv = min(1.0, abs(_vrp) * 5)  # scale VRP to 0-1
    write_signal("vol_surface", ticker_display, vol_dir, vol_conv, vol_view,
                 reasoning=f"VRP {_vrp:+.1%}, skew {_skew_regime}, {_ts_shape}")
except Exception:
    pass

try:
    from src.metrics_store import save_snapshot, percentile_ranks_all
    save_snapshot(ticker_display, {
        "atm_iv": _front_atm, "put_skew": _put_skew_ratio, "vrp": _vrp,
        "hv20": current_hv20, "hv60": current_hv60,
        "iv_hv_ratio": _front_atm / current_hv20 if current_hv20 and current_hv20 > 0 else None,
        "ts_slope": _front_atm - _back_atm if len(expirations) >= 2 else 0,
        "spot": spot,
    })
    _pctiles = percentile_ranks_all(ticker_display)
except Exception:
    _pctiles = {}

# Show percentile context if history exists
if _pctiles and any(v is not None for v in _pctiles.values()):
    pct_parts = []
    for key, label in [("atm_iv", "IV"), ("put_skew", "Skew"), ("vrp", "VRP")]:
        p = _pctiles.get(key)
        if p is not None:
            color = COLORS["danger"] if p > 80 else (COLORS["success"] if p < 20 else COLORS["text_muted"])
            pct_parts.append(
                f'<span style="color:{color};font-weight:600;">{label}: {p:.0f}th</span>'
            )
    if pct_parts:
        st.markdown(
            f'<div style="text-align:center;font-size:0.75rem;color:{COLORS["text_muted"]};margin-bottom:8px;">'
            f'252-Day Percentile: {" &bull; ".join(pct_parts)}'
            f'</div>',
            unsafe_allow_html=True,
        )

# ─── TABS ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9 = st.tabs([
    "3D Surface", "IV Skew", "Term Structure", "Surface Dislocations", "Skew Metrics", "Gamma Scalping",
    "Surface Animation", "Surface Comparison", "Gemini Trade Ideas",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 3D IV SURFACE
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("3D Surface"):
        st.subheader("Implied Volatility Surface")
        with st.expander("Why this matters for your trading"):
            st.markdown("""
**What you're looking at:** Every option on this stock, plotted as a landscape. Height = implied volatility.
Peaks are expensive options, valleys are cheap ones.

**How to use it:**
- **Rotate the surface** (click and drag) to see it from different angles. Look for bumps and ridges.
- A **ridge at a specific expiration** (horizontal bump) means the market is pricing in an event at that date — probably earnings.
- A **steep left slope** (higher on the left/put side) is normal — investors pay more for downside protection.
- **Switch to Delta view** to see where options have the most directional exposure, or **Gamma** to find where dealer hedging concentrates.

**Real-world example:** If you see a spike at 30 DTE on the put side, the market expects a move before that date. If it's flatter than usual, the market is complacent — that might be a buying opportunity for protection.

**The yellow line** is where the stock trades right now. The IV at that line is the ATM vol — your baseline for whether options are cheap or expensive.
""")


        # ── Key surface metrics at a glance ──
        if not expirations:
            st.warning("No valid expirations loaded.")
            st.stop()
        front_chain = term_data[expirations[0]]
        front_dte = _dte(expirations[0])
        front_atm_iv = _atm_iv_cache.get(expirations[0], 0.25)
        p25_s, p25_iv = _p25_s, _p25_iv
        c25_s, c25_iv = _find_delta_strike(front_chain, spot, 0.25, "call")

        sm1, sm2, sm3, sm4, sm5 = st.columns(5)
        sm1.metric("Spot", f"${spot:,.2f}")
        sm2.metric(f"ATM IV ({front_dte}d)", f"{front_atm_iv:.1%}",
                   help="Front-month at-the-money implied volatility.")
        if current_hv20 and current_hv20 > 0:
            vrp = front_atm_iv - current_hv20
            vrp_label = "Rich" if vrp > 0.03 else ("Cheap" if vrp < -0.02 else "Fair")
            sm3.metric("VRP (IV − HV20)", f"{vrp:+.1%}", vrp_label,
                       delta_color="inverse" if vrp > 0.03 else "normal")
        else:
            sm3.metric("HV20", "N/A")
        if p25_iv and front_atm_iv > 0:
            put_skew_ratio = p25_iv / front_atm_iv
            sm4.metric("Put Skew (25Δ)", f"{put_skew_ratio:.2f}x",
                       help="25-delta put IV / ATM IV. >1.10 = elevated fear premium.")
        else:
            sm4.metric("Put Skew", "N/A")
        if len(expirations) >= 2:
            back_atm = _atm_iv(term_data[expirations[-1]], spot, "call")
            ts_shape = "Backwardation" if front_atm_iv > back_atm else "Contango"
            sm5.metric("Term Structure", ts_shape,
                       f"{(front_atm_iv - back_atm):+.1%}",
                       delta_color="inverse" if ts_shape == "Backwardation" else "normal")
        else:
            sm5.metric("Term Structure", "N/A")

        metric_sel = st.selectbox("Surface metric", ["IV", "Delta", "Gamma", "Vega"], key="vs_metric")
        field_map = {"IV": "implied_volatility", "Delta": "delta", "Gamma": "gamma", "Vega": "vega"}
        field = field_map[metric_sel]

        # Build surface grid
        strike_lo = spot * 0.70
        strike_hi = spot * 1.30
        surface_rows = []
        for exp in expirations:
            chain = term_data[exp]
            dte = _dte(exp)
            calls = chain[(chain["contract_type"] == "call") & (chain["strike_price"] >= spot * 0.98)]
            puts = chain[(chain["contract_type"] == "put") & (chain["strike_price"] < spot * 0.98)]
            for _, row in pd.concat([calls, puts]).iterrows():
                k = row["strike_price"]
                val = row.get(field, 0) or 0
                if strike_lo <= k <= strike_hi and val != 0:
                    iv = row.get("implied_volatility", 0) or 0
                    if iv > 0:
                        surface_rows.append({"strike": k, "expiration": exp, "dte": dte, field: val})

        if surface_rows:
            sdf = pd.DataFrame(surface_rows)
            pivot = sdf.pivot_table(index="expiration", columns="strike", values=field, aggfunc="mean")
            pivot = pivot.sort_index()
            pivot = pivot.interpolate(axis=1, limit_direction="both")
            pivot = pivot.interpolate(axis=0, limit_direction="both")

            z_vals = pivot.values
            exp_labels = [f"{e[5:]} ({_dte(e)}d)" for e in pivot.index]

            z_title = {"IV": "Implied Volatility", "Delta": "Delta", "Gamma": "Gamma", "Vega": "Vega"}[metric_sel]
            z_fmt = ".0%" if metric_sel == "IV" else ".4f"

            fig_3d = go.Figure(data=[go.Surface(
                x=pivot.columns.tolist(),
                y=list(range(len(pivot.index))),
                z=z_vals,
                colorscale="Viridis",
                colorbar=dict(title=z_title, tickformat=z_fmt, len=0.6, thickness=15),
                lighting=dict(ambient=0.6, diffuse=0.5, specular=0.3, roughness=0.5),
                opacity=0.92,
            )])

            # Spot price line
            spot_col = (np.abs(np.array(pivot.columns) - spot)).argmin()
            spot_z = z_vals[:, spot_col]
            fig_3d.add_trace(go.Scatter3d(
                x=[spot] * len(pivot.index),
                y=list(range(len(pivot.index))),
                z=[float(v) if not np.isnan(v) else 0 for v in spot_z],
                mode="lines+markers",
                line=dict(color=COLORS["warning"], width=5),
                marker=dict(size=3, color=COLORS["warning"]),
                name="Spot",
            ))

            fig_3d.update_layout(
                template="plotly_dark", height=600,
                margin=dict(t=10, b=10, l=10, r=10),
                scene=dict(
                    xaxis=dict(title="Strike ($)", backgroundcolor="rgba(14,17,23,0.8)",
                               gridcolor="rgba(48,54,61,0.4)", showbackground=True),
                    yaxis=dict(title="Expiration", tickvals=list(range(len(pivot.index))),
                               ticktext=exp_labels, backgroundcolor="rgba(14,17,23,0.8)",
                               gridcolor="rgba(48,54,61,0.4)", showbackground=True),
                    zaxis=dict(title=z_title, backgroundcolor="rgba(14,17,23,0.8)",
                               gridcolor="rgba(48,54,61,0.4)", showbackground=True),
                    camera=dict(eye=dict(x=1.8, y=-1.4, z=0.9)),
                    aspectratio=dict(x=1.5, y=1, z=0.6),
                ),
                legend=dict(x=0, y=1, bgcolor="rgba(0,0,0,0.5)"),
            )
            st.plotly_chart(fig_3d, use_container_width=True)
        else:
            st.warning("Not enough data to build surface.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — IV SKEW BY EXPIRATION
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    with error_boundary("IV Skew"):
        st.subheader("IV Skew by Expiration")
        with st.expander("Why this matters for your trading"):
            st.markdown("""
**What you're looking at:** The IV "smile" (or "smirk") for each expiration, overlaid on one chart.
The x-axis is moneyness — 1.0 is ATM, below 1.0 is OTM puts, above 1.0 is OTM calls.

**How to use it:**
- **Steep left skew** (lines curving up to the left) means puts are expensive — the market is paying up for crash protection. This is normal for equities.
- If the **front-month line is steeper than the back-month**, near-term fear is elevated — calendars spreads on the put side may be attractive (sell expensive near-term, buy cheaper far-term).
- **Flat or inverted skew** (left side not much higher) is unusual and suggests the market isn't worried about downside. This can precede complacent sell-offs.
- **Lines crossing each other** at specific moneyness levels reveal mispricings — where the same strike is relatively cheaper in one month vs another.

**For calendar traders:** Compare the front and back month lines at your strike. If the front line is above the back at that moneyness, you're selling rich IV and buying cheap — that's the edge.

**For premium sellers:** Sell options where the skew curve is highest relative to other expirations — that's where fear premium is concentrated.
""")


        fig_skew = go.Figure()
        colors = [COLORS["accent"], COLORS["success"], COLORS["warning"], COLORS["danger"],
                  "#9966ff", "#ff66cc", "#66ffcc", "#ffcc66", "#6699ff", "#ff9966", "#99ff66", "#cc66ff"]
        for i, exp in enumerate(expirations):
            chain = term_data[exp]
            calls = chain[(chain["contract_type"] == "call") & (chain["implied_volatility"] > 0)]
            puts = chain[(chain["contract_type"] == "put") & (chain["implied_volatility"] > 0)]
            # Combine: puts below spot, calls above
            below = puts[puts["strike_price"] < spot * 0.98].copy()
            above = calls[calls["strike_price"] >= spot * 0.98].copy()
            combined = pd.concat([below, above]).sort_values("strike_price")
            if combined.empty:
                continue
            combined["moneyness"] = combined["strike_price"] / spot
            combined = combined[(combined["moneyness"] >= 0.7) & (combined["moneyness"] <= 1.3)]
            fig_skew.add_trace(go.Scatter(
                x=combined["moneyness"], y=combined["implied_volatility"] * 100,
                mode="lines", name=f"{exp} ({_dte(exp)}d)",
                line=dict(color=colors[i % len(colors)], width=2),
                hovertemplate="Moneyness: %{x:.2f}<br>IV: %{y:.1f}%<extra></extra>",
            ))

        fig_skew.add_vline(x=1.0, line_dash="dash", line_color=COLORS["text_muted"],
                           annotation_text="ATM")
        fig_skew.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=500,
            xaxis_title="Moneyness (Strike / Spot)",
            yaxis_title="Implied Volatility (%)",
            margin=dict(l=50, r=20, t=30, b=50),
            legend=dict(bgcolor="rgba(0,0,0,0.5)"),
        )
        st.plotly_chart(fig_skew, use_container_width=True, config=PLOTLY_NOBAR)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — TERM STRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    with error_boundary("Term Structure"):
        st.subheader("ATM IV Term Structure")
        with st.expander("Why this matters for your trading"):
            st.markdown("""
**What you're looking at:** ATM implied volatility at each expiration date, with the stock's actual
recent volatility (horizontal lines) for comparison.

**How to use it:**

**The curve shape tells you everything:**
- **Upward slope (contango):** Normal. Uncertainty grows with time. Calendar spreads are fairly priced.
- **Inverted (backwardation):** Fear. Near-term options are unusually expensive — usually earnings,
  FOMC, or a breaking news event. Calendar spreads can be cheap here, but you're selling into an event.
- **Kink or spike at one point:** A single expiration is priced much higher than its neighbors. That's the
  market pricing in a specific event. Check if earnings or FOMC falls right before that date.

**The horizontal HV lines are your reference:**
- If the IV curve is **above both HV lines**, the market thinks future volatility will exceed recent history.
  Options are expensive. Good time to sell premium.
- If IV is **below HV**, the market is pricing in calm. Options are cheap. Good time to buy protection.
- The **IV/HV ratio table** below quantifies this: >1.2x = rich, <0.8x = cheap.

**For calendar spreads specifically:** You want the front month's IV to be high (selling rich) and the
back month's IV to be reasonable (buying fair). A steep term structure means the front is priced
proportionally — you need to check the IV/HV ratio to see if it's rich in absolute terms.
""")


        ts_data = []
        for exp in expirations:
            chain = term_data[exp]
            iv = _atm_iv(chain, spot)
            ts_data.append({"expiration": exp, "dte": _dte(exp), "atm_iv": iv})
        ts_df = pd.DataFrame(ts_data)

        fig_ts = go.Figure()
        fig_ts.add_trace(go.Scatter(
            x=ts_df["dte"], y=ts_df["atm_iv"] * 100,
            mode="lines+markers", line=dict(color=COLORS["accent"], width=2),
            marker=dict(size=8), text=ts_df["expiration"],
            hovertemplate="Exp: %{text}<br>DTE: %{x}d<br>IV: %{y:.1f}%<extra></extra>",
            name="ATM IV",
        ))
        if current_hv20:
            fig_ts.add_hline(y=current_hv20 * 100, line_dash="dot",
                             line_color=COLORS["warning"], line_width=1,
                             annotation_text=f"20d HV: {current_hv20*100:.1f}%")
        if current_hv60:
            fig_ts.add_hline(y=current_hv60 * 100, line_dash="dot",
                             line_color=COLORS["success"], line_width=1,
                             annotation_text=f"60d HV: {current_hv60*100:.1f}%")

        fig_ts.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=400,
            xaxis_title="Days to Expiration",
            yaxis_title="Implied Volatility (%)",
            margin=dict(l=50, r=20, t=30, b=50),
        )
        st.plotly_chart(fig_ts, use_container_width=True, config=PLOTLY_NOBAR)

        # Contango/Backwardation + event kink detection
        if len(ts_df) >= 2:
            slope = (ts_df["atm_iv"].iloc[-1] - ts_df["atm_iv"].iloc[0]) / max(1, ts_df["dte"].iloc[-1] - ts_df["dte"].iloc[0])
            if slope > 0.0001:
                st.success("Term structure is in **contango** (normal). Longer-dated options are more expensive.")
            elif slope < -0.0001:
                st.warning("Term structure is **inverted** (backwardation). Near-term options are unusually expensive — check for upcoming events.")
            else:
                st.info("Term structure is approximately **flat**.")

            # Detect event-driven kinks (IV spikes at specific DTEs)
            if len(ts_df) >= 3:
                ivs = ts_df["atm_iv"].values
                dtes = ts_df["dte"].values
                # Fit linear trend and find deviations
                z_fit = np.polyfit(dtes, ivs, 1)
                trend = np.polyval(z_fit, dtes)
                residuals = ivs - trend
                res_std = np.std(residuals) if len(residuals) > 2 else 0

                if res_std > 0:
                    kink_mask = residuals > 1.5 * res_std
                    if np.any(kink_mask):
                        kink_exps = ts_df.loc[ts_df.index[kink_mask]]
                        for _, kink in kink_exps.iterrows():
                            kink_premium = float(kink["atm_iv"] - np.polyval(z_fit, kink["dte"])) * 100
                            st.warning(
                                f"**Event detected at {kink['expiration']} ({kink['dte']}d):** "
                                f"ATM IV is {kink_premium:+.1f}% above the trend line. "
                                f"Check for earnings, FOMC, or ex-div near this date."
                            )

        # IV/HV ratio table with actionable thresholds
        if current_hv20 and current_hv20 > 0:
            st.markdown("#### IV / Realized Vol Ratio")
            ratio_rows = []
            for _, r in ts_df.iterrows():
                iv = r["atm_iv"]
                ratio_20 = iv / current_hv20 if current_hv20 > 0 else 0
                ratio_60 = iv / current_hv60 if current_hv60 and current_hv60 > 0 else 0

                if ratio_20 > 1.3:
                    verdict = "Rich — Sell Premium"
                elif ratio_20 > 1.1:
                    verdict = "Slightly Rich"
                elif ratio_20 < 0.8:
                    verdict = "Cheap — Buy Protection"
                elif ratio_20 < 0.95:
                    verdict = "Slightly Cheap"
                else:
                    verdict = "Fair Value"

                ratio_rows.append({
                    "Expiration": r["expiration"],
                    "DTE": r["dte"],
                    "ATM IV": f"{iv*100:.1f}%",
                    "IV/HV20": f"{ratio_20:.2f}x",
                    "IV/HV60": f"{ratio_60:.2f}x" if ratio_60 > 0 else "N/A",
                    "Premium": f"{(iv - current_hv20)*100:+.1f}%",
                    "Signal": verdict,
                })
            ratio_df = pd.DataFrame(ratio_rows)
            st.dataframe(
                ratio_df.style.apply(
                    lambda row: [
                        "background-color: rgba(0,255,150,0.08)" if "Cheap" in str(row.get("Signal", "")) else
                        "background-color: rgba(255,68,68,0.08)" if "Rich" in str(row.get("Signal", "")) else ""
                    ] * len(row), axis=1,
                ),
                use_container_width=True, hide_index=True,
            )

            # Quick summary
            _ratio_vals = [r["atm_iv"] / current_hv20 for _, r in ts_df.iterrows() if current_hv20 > 0]
            avg_ratio = np.mean(_ratio_vals) if _ratio_vals else 1.0
            if avg_ratio > 1.2:
                st.warning(f"Surface is **{(avg_ratio-1)*100:.0f}% rich** to realized vol on average. "
                           f"Favor selling strategies: iron condors, credit spreads, covered calls.")
            elif avg_ratio < 0.85:
                st.success(f"Surface is **{(1-avg_ratio)*100:.0f}% cheap** to realized vol. "
                           f"Favor buying strategies: straddles, debit spreads, protective puts.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — SURFACE DISLOCATIONS
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    with error_boundary("Surface Dislocations"):
        st.subheader("IV Dislocation Analysis")
        with st.expander("Why this matters for your trading"):
            st.markdown("""
**What you're looking at:** A heatmap showing where the market is over- or under-pricing options
relative to how the stock has actually been moving.

**This is the closest thing to a "mispricing detector" you'll find:**
- **Green cells** = the market is charging less for an option than the stock's recent behavior justifies.
  These are potentially underpriced — buying opportunities.
- **Red cells** = the market is charging more than recent movement justifies. These are potentially
  overpriced — selling opportunities.

**Baseline options explained:**
- **20d Realized Vol:** What has the stock actually done over the past month? Simple, reactive.
- **60d Realized Vol:** Same idea but smoother — less affected by a single volatile week.
- **Term-adjusted HV:** The most realistic baseline. Acknowledges that a 90-day option should have
  higher IV than a 7-day option even if HV is the same, because more things can happen in 90 days.

**Pattern recognition — what the heatmap shapes mean:**
- **Horizontal red band** at one DTE: The market is pricing in a specific event at that date. Check earnings.
- **Red on the left, green on the right:** Puts are rich vs calls. Normal in equities (crash protection costs more).
  But if it's extreme, put spreads may be overpriced.
- **All red:** The entire surface is expensive. This happens after big moves or before known events.
  Premium sellers love this environment.
- **Green pocket** surrounded by red: That specific contract is relatively cheap — could be a
  tactical buy, especially if it has decent OI (check the "Most Mispriced" table below).

**Trade ideas section** at the bottom gives you the specific contracts with the biggest dislocations
and tells you if they're liquid enough to actually trade.
""")

        if current_hv20 is None or current_hv20 <= 0:
            st.warning("Need price history to compute realized vol baseline.")
        else:
            # Controls
            dc1, dc2 = st.columns(2)
            with dc1:
                baseline_type = st.selectbox("Baseline", [
                    "20d Realized Vol (flat)",
                    "60d Realized Vol (flat)",
                    "Term-adjusted HV (sqrt scaling)",
                ], key="vs_baseline")
            with dc2:
                moneyness_range = st.slider("Moneyness range", 0.70, 1.30, (0.80, 1.20), 0.01,
                                             format="%.2f", key="vs_money_range")
                money_lo, money_hi = moneyness_range

            # Build dislocation grid
            base_hv = current_hv20 if "20d" in baseline_type else (current_hv60 if current_hv60 else current_hv20)
            use_term_adj = "Term-adjusted" in baseline_type

            _disl_spinner = st.empty()
            _disl_spinner.info("Computing dislocations across the surface...")
            disl_rows = []
            for exp in expirations:
                chain = term_data[exp]
                dte = int(_dte(exp))
                if dte <= 0:
                    continue
                calls = chain[(chain["contract_type"] == "call") & (chain["implied_volatility"] > 0)]
                puts = chain[(chain["contract_type"] == "put") & (chain["implied_volatility"] > 0)]
                below = puts[puts["strike_price"] < spot * 0.98]
                above = calls[calls["strike_price"] >= spot * 0.98]
                combined = pd.concat([below, above])

                # Term-adjusted baseline: HV * sqrt(DTE/20) approximates
                # how realized vol scales with horizon
                if use_term_adj:
                    theo_iv = float(base_hv) * np.sqrt(max(dte, 5) / 20)
                    theo_iv = min(theo_iv, float(base_hv) * 1.5)
                else:
                    theo_iv = float(base_hv)

                for _, row in combined.iterrows():
                    k = float(row["strike_price"])
                    iv = float(row["implied_volatility"])
                    moneyness = k / spot
                    if money_lo <= moneyness <= money_hi:
                        disl_rows.append({
                            "moneyness": round(moneyness, 2),
                            "dte": dte,
                            "expiration": str(exp),
                            "strike": k,
                            "market_iv": iv * 100,
                            "baseline_iv": theo_iv * 100,
                            "dislocation": (iv - theo_iv) * 100,
                            "dislocation_pct": ((iv - theo_iv) / theo_iv * 100) if theo_iv > 0 else 0.0,
                            "contract_type": str(row["contract_type"]),
                            "volume": int(row.get("volume", 0) or 0),
                            "open_interest": int(row.get("open_interest", 0) or 0),
                        })

            _disl_spinner.empty()

            if disl_rows:
                disl_df = pd.DataFrame(disl_rows)

                # ── Surface richness summary ──
                avg_disl = disl_df["dislocation"].mean()
                put_disl = disl_df[disl_df["contract_type"] == "put"]["dislocation"].mean() if len(disl_df[disl_df["contract_type"] == "put"]) > 0 else 0
                call_disl = disl_df[disl_df["contract_type"] == "call"]["dislocation"].mean() if len(disl_df[disl_df["contract_type"] == "call"]) > 0 else 0
                max_rich = disl_df.nlargest(1, "dislocation").iloc[0] if len(disl_df) > 0 else None
                max_cheap = disl_df.nsmallest(1, "dislocation").iloc[0] if len(disl_df) > 0 else None

                dm1, dm2, dm3, dm4 = st.columns(4)
                _surf_color = COLORS["danger"] if avg_disl > 2 else (COLORS["success"] if avg_disl < -2 else COLORS["warning"])
                dm1.markdown(
                    f'<div style="text-align:center;padding:8px;border:2px solid {_surf_color};border-radius:8px;">'
                    f'<div style="font-size:0.65rem;color:{COLORS["text_muted"]};">SURFACE</div>'
                    f'<div style="font-size:1.2rem;font-weight:800;color:{_surf_color};">{"RICH" if avg_disl > 2 else "CHEAP" if avg_disl < -2 else "FAIR"}</div>'
                    f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">{avg_disl:+.1f}% avg</div>'
                    f'</div>', unsafe_allow_html=True)
                dm2.metric("Put Side", f"{put_disl:+.1f}%",
                           "Rich" if put_disl > 2 else ("Cheap" if put_disl < -2 else "Fair"),
                           help="Average dislocation for put options")
                dm3.metric("Call Side", f"{call_disl:+.1f}%",
                           "Rich" if call_disl > 2 else ("Cheap" if call_disl < -2 else "Fair"),
                           help="Average dislocation for call options")
                if max_rich is not None and max_cheap is not None:
                    dm4.metric("Spread",
                               f"{max_rich['dislocation'] - max_cheap['dislocation']:.1f}%",
                               help="Richest minus cheapest — wider = more mispricings to exploit")

                st.divider()

                # ── Main heatmap ──
                pivot_disl = disl_df.pivot_table(
                    index="dte", columns="moneyness", values="dislocation", aggfunc="mean"
                )
                pivot_disl = pivot_disl.sort_index(ascending=True)
                pivot_disl = pivot_disl.interpolate(axis=1, limit_direction="both")

                fig_disl = go.Figure(go.Heatmap(
                    x=[f"{m:.2f}" for m in pivot_disl.columns],
                    y=[f"{int(d)}d" for d in pivot_disl.index],
                    z=pivot_disl.values,
                    colorscale=[[0, COLORS["success"]], [0.5, "#1c1f26"], [1, COLORS["danger"]]],
                    zmid=0,
                    colorbar=dict(title="IV - Baseline (%)"),
                    text=np.round(pivot_disl.values, 1),
                    texttemplate="%{text}",
                    textfont=dict(size=9),
                    hovertemplate="Moneyness: %{x}<br>DTE: %{y}<br>Dislocation: %{z:+.1f}%<extra></extra>",
                ))
                # ATM marker — use add_shape instead of add_vline to avoid
                # Plotly bug with annotation on categorical x-axis
                if len(pivot_disl.columns) > 0:
                    atm_col = min(pivot_disl.columns, key=lambda m: abs(m - 1.0))
                    atm_col_str = f"{atm_col:.2f}"
                    x_list = [f"{m:.2f}" for m in pivot_disl.columns]
                    if atm_col_str in x_list:
                        atm_pos = x_list.index(atm_col_str)
                        fig_disl.add_shape(
                            type="line", x0=atm_pos, x1=atm_pos, y0=0, y1=1,
                            xref="x", yref="y domain",
                            line=dict(color=COLORS["warning"], width=1, dash="dot"),
                        )
                        fig_disl.add_annotation(
                            x=atm_pos, y=1.02, xref="x", yref="y domain",
                            text="ATM", showarrow=False,
                            font=dict(color=COLORS["warning"], size=10),
                        )
                fig_disl.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    height=max(400, len(pivot_disl) * 35),
                    xaxis_title="Moneyness (Strike / Spot)",
                    yaxis_title="Days to Expiration",
                    margin=dict(l=60, r=20, t=30, b=50),
                )
                st.plotly_chart(fig_disl, use_container_width=True, config=PLOTLY_NOBAR)

                # ── Summary metrics row ──
                avg_disl = disl_df["dislocation"].mean()
                _disl_clean = disl_df.dropna(subset=["dislocation"])
                if _disl_clean.empty:
                    st.info("No valid dislocation data to summarize.")
                    st.stop()
                max_cheap = _disl_clean.loc[_disl_clean["dislocation"].idxmin()]
                max_rich = _disl_clean.loc[_disl_clean["dislocation"].idxmax()]

                sm1, sm2, sm3 = st.columns(3)
                sm1.metric("Avg Surface Dislocation", f"{avg_disl:+.1f}%",
                           help="Positive = surface is rich on average. Negative = cheap.")
                sm2.metric("Cheapest Point",
                           f"{max_cheap['dislocation']:+.1f}%",
                           help=f"${max_cheap['strike']:.0f} {max_cheap['expiration']}")
                sm3.metric("Richest Point",
                           f"{max_rich['dislocation']:+.1f}%",
                           help=f"${max_rich['strike']:.0f} {max_rich['expiration']}")
                st.caption(
                    f"Cheapest: ${max_cheap['strike']:.0f} {max_cheap['contract_type']} "
                    f"{max_cheap['expiration']} ({max_cheap['dislocation']:+.1f}%) | "
                    f"Richest: ${max_rich['strike']:.0f} {max_rich['contract_type']} "
                    f"{max_rich['expiration']} ({max_rich['dislocation']:+.1f}%)"
                )

                # ── Put vs Call skew dislocation ──
                st.markdown("#### Put vs Call Dislocation")
                _puts_d = disl_df[disl_df["contract_type"] == "put"]["dislocation"]
                _calls_d = disl_df[disl_df["contract_type"] == "call"]["dislocation"]
                put_disl = _puts_d.mean() if len(_puts_d) > 0 else 0.0
                call_disl = _calls_d.mean() if len(_calls_d) > 0 else 0.0

                pc1, pc2, pc3 = st.columns(3)
                pc1.metric("Avg Put Dislocation", f"{put_disl:+.1f}%")
                pc2.metric("Avg Call Dislocation", f"{call_disl:+.1f}%")
                skew_tilt = put_disl - call_disl
                pc3.metric("Skew Tilt (Put - Call)", f"{skew_tilt:+.1f}%",
                           help="Positive = puts are richer than calls relative to baseline")

                if skew_tilt > 3:
                    st.warning("Puts are significantly richer than calls — heavy downside hedging demand or fear.")
                elif skew_tilt < -3:
                    st.info("Calls are richer than puts — upside demand exceeds downside hedging.")

                # ── Dislocation by expiration (bar chart) ──
                st.markdown("#### Dislocation by Expiration")
                exp_disl = disl_df.groupby("expiration").agg(
                    DTE=("dte", "first"),
                    Avg_Disl=("dislocation", "mean"),
                    Contracts=("strike", "count"),
                ).sort_values("DTE")
                # Add put/call split
                _put_disl_by_exp = disl_df[disl_df["contract_type"] == "put"].groupby("expiration")["dislocation"].mean()
                _call_disl_by_exp = disl_df[disl_df["contract_type"] == "call"].groupby("expiration")["dislocation"].mean()
                exp_disl["Put_Disl"] = _put_disl_by_exp.reindex(exp_disl.index).fillna(0)
                exp_disl["Call_Disl"] = _call_disl_by_exp.reindex(exp_disl.index).fillna(0)

                fig_exp_d = go.Figure()
                fig_exp_d.add_trace(go.Bar(
                    x=[f"{int(r['DTE'])}d" for _, r in exp_disl.iterrows()],
                    y=exp_disl["Avg_Disl"],
                    marker_color=[COLORS["danger"] if v > 0 else COLORS["success"] for v in exp_disl["Avg_Disl"]],
                    text=[f"{v:+.1f}%" for v in exp_disl["Avg_Disl"]],
                    textposition="outside",
                    name="Avg Dislocation",
                ))
                fig_exp_d.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5)
                fig_exp_d.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=300,
                    xaxis_title="Days to Expiration",
                    yaxis_title="Avg Dislocation (%)",
                    margin=dict(l=50, r=20, t=30, b=50),
                    showlegend=False,
                )
                st.plotly_chart(fig_exp_d, use_container_width=True, config=PLOTLY_NOBAR)

                # Identify event-driven spikes
                if len(exp_disl) >= 3:
                    mean_disl = exp_disl["Avg_Disl"].mean()
                    std_disl = exp_disl["Avg_Disl"].std()
                    if std_disl > 0:
                        spikes = exp_disl[exp_disl["Avg_Disl"] > mean_disl + 1.5 * std_disl]
                        for _, spike in spikes.iterrows():
                            st.warning(
                                f"**{int(spike['DTE'])}d expiration** shows elevated dislocation "
                                f"({spike['Avg_Disl']:+.1f}% vs {mean_disl:+.1f}% avg) — "
                                f"likely event-driven IV spike."
                            )

                # ── Top mispriced contracts table ──
                st.markdown("#### Most Mispriced Contracts")
                st.caption("Contracts with the largest absolute dislocation from baseline. Green = cheap (buy), Red = rich (sell).")
                top_mispriced = disl_df.reindex(
                    disl_df["dislocation"].abs().sort_values(ascending=False).index
                ).head(15).copy()

                # Add liquidity and signal columns
                top_mispriced["Liquidity"] = top_mispriced["open_interest"].apply(
                    lambda oi: "High" if oi > 500 else ("Medium" if oi > 100 else "Low")
                )
                top_mispriced["Signal"] = top_mispriced["dislocation"].apply(
                    lambda d: "BUY (cheap)" if d < -3 else ("SELL (rich)" if d > 3 else "Monitor")
                )

                display_mp = top_mispriced[["expiration", "dte", "strike", "contract_type",
                                            "market_iv", "baseline_iv", "dislocation",
                                            "open_interest", "Liquidity", "Signal"]].copy()
                display_mp.columns = ["Expiration", "DTE", "Strike", "Type",
                                      "Market IV", "Baseline IV", "Dislocation",
                                      "OI", "Liquidity", "Signal"]
                display_mp["Strike"] = display_mp["Strike"].apply(lambda v: f"${v:,.0f}")
                display_mp["Market IV"] = display_mp["Market IV"].apply(lambda v: f"{v:.1f}%")
                display_mp["Baseline IV"] = display_mp["Baseline IV"].apply(lambda v: f"{v:.1f}%")
                display_mp["Dislocation"] = display_mp["Dislocation"].apply(lambda v: f"{v:+.1f}%")

                st.dataframe(
                    display_mp.style.apply(
                        lambda row: [
                            "background-color: rgba(0,255,150,0.1)" if "BUY" in str(row.get("Signal", "")) else
                            "background-color: rgba(255,68,68,0.1)" if "SELL" in str(row.get("Signal", "")) else ""
                        ] * len(row), axis=1,
                    ),
                    use_container_width=True, hide_index=True,
                )

                # ── Actionable trade ideas ──
                st.markdown("#### Trade Ideas from Dislocations")

                # Filter to liquid contracts only for trade ideas
                liquid = disl_df[disl_df["open_interest"] > 50]
                cheapest_5 = liquid.nsmallest(5, "dislocation") if not liquid.empty else disl_df.nsmallest(5, "dislocation")
                richest_5 = liquid.nlargest(5, "dislocation") if not liquid.empty else disl_df.nlargest(5, "dislocation")

                tc1, tc2 = st.columns(2)
                with tc1:
                    st.markdown(
                        f'<div style="border-left:3px solid {COLORS["success"]};padding-left:12px;margin-bottom:8px;">'
                        f'<strong style="color:{COLORS["success"]};">Cheapest Options</strong> — Buy Candidates</div>',
                        unsafe_allow_html=True,
                    )
                    for _, c in cheapest_5.iterrows():
                        oi = int(c["open_interest"])
                        liq_icon = "🟢" if oi > 500 else ("🟡" if oi > 100 else "🔴")
                        st.markdown(
                            f'{liq_icon} **${c["strike"]:.0f} {c["contract_type"]}** {c["expiration"]} ({c["dte"]}d)  \n'
                            f'IV: {c["market_iv"]:.1f}% vs baseline {c["baseline_iv"]:.1f}% → '
                            f'**{c["dislocation"]:+.1f}%** | OI: {oi:,}'
                        )
                with tc2:
                    st.markdown(
                        f'<div style="border-left:3px solid {COLORS["danger"]};padding-left:12px;margin-bottom:8px;">'
                        f'<strong style="color:{COLORS["danger"]};">Richest Options</strong> — Sell Candidates</div>',
                        unsafe_allow_html=True,
                    )
                    for _, r in richest_5.iterrows():
                        oi = int(r["open_interest"])
                        liq_icon = "🟢" if oi > 500 else ("🟡" if oi > 100 else "🔴")
                        st.markdown(
                            f'{liq_icon} **${r["strike"]:.0f} {r["contract_type"]}** {r["expiration"]} ({r["dte"]}d)  \n'
                            f'IV: {r["market_iv"]:.1f}% vs baseline {r["baseline_iv"]:.1f}% → '
                            f'**{r["dislocation"]:+.1f}%** | OI: {oi:,}'
                        )

                st.caption("🟢 High liquidity (OI > 500) | 🟡 Medium (> 100) | 🔴 Low (< 100)")

                if avg_disl > 2:
                    st.warning(f"Surface is broadly **{avg_disl:.1f}% rich** vs realized vol — favorable for selling premium.")
                elif avg_disl < -2:
                    st.success(f"Surface is broadly **{abs(avg_disl):.1f}% cheap** vs realized vol — favorable for buying premium.")
                else:
                    st.info("Surface is approximately fairly priced relative to recent realized movement.")
            else:
                st.warning("Not enough data for dislocation analysis.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — SKEW METRICS
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    with error_boundary("Skew Metrics"):
        st.subheader("Skew & Risk Reversal Metrics")
        with st.expander("Why this matters for your trading"):
            st.markdown("""
**What you're looking at:** Four metrics that distill the entire vol smile into numbers you can act on.
These are the same metrics institutional desks track daily.

**Put Skew (25-delta Put IV / ATM IV):**
- Above 1.0 = puts are more expensive than ATM (normal for equities).
- Values around 1.05-1.15 are typical. Above 1.20 = heavy fear premium — bearish hedging demand is elevated.
- **Trade idea:** When put skew is extreme (>1.20), put spreads and risk reversals that sell puts become attractive.

**Call Skew (25-delta Call IV / ATM IV):**
- Usually close to 1.0 or slightly below. Above 1.0 = unusual call demand (squeeze potential, takeover speculation).
- **Trade idea:** Elevated call skew often precedes rallies or signals short squeeze positioning. Consider call calendars.

**Risk Reversal (25d Call IV - 25d Put IV):**
- Negative = puts are more expensive than calls (the norm). Very negative (< -5%) = extreme fear.
- Positive = calls are more expensive — unusual and worth investigating (possible squeeze or upside catalyst).
- **Trade idea:** Sell the rich side, buy the cheap side. Negative RR → sell put spreads, buy call spreads.

**Butterfly (25d Put + 25d Call - 2*ATM):**
- Measures tail pricing — are the wings (deep OTM options) expensive relative to ATM?
- High butterfly = the market is pricing in fat tails (crash or squeeze). Low = complacency.
- **Trade idea:** High butterfly → sell iron condors (wings are overpriced). Low butterfly → buy straddles (ATM is rich vs wings).

**Across expirations:** If these metrics change shape at a specific DTE, there's an event at that horizon driving the shift.
""")

        _skew_spin = st.empty()
        _skew_spin.info("Computing skew metrics...")
        skew_rows = []
        for exp in expirations:
            chain = term_data[exp]
            dte = _dte(exp)
            atm_iv_val = _atm_iv(chain, spot)

            # 25-delta put
            put_strike, put_iv = _find_delta_strike(chain, spot, -0.25, "put")
            # 25-delta call
            call_strike, call_iv = _find_delta_strike(chain, spot, 0.25, "call")

            if put_iv and call_iv and atm_iv_val > 0:
                put_skew = put_iv / atm_iv_val
                call_skew = call_iv / atm_iv_val
                risk_rev = (call_iv - put_iv) * 100
                butterfly = (put_iv + call_iv - 2 * atm_iv_val) * 100

                skew_rows.append({
                    "Expiration": exp,
                    "DTE": dte,
                    "ATM IV": atm_iv_val * 100,
                    "25d Put IV": put_iv * 100,
                    "25d Call IV": call_iv * 100,
                    "Put Skew": put_skew,
                    "Call Skew": call_skew,
                    "Risk Reversal": risk_rev,
                    "Butterfly": butterfly,
                    "25d Put K": put_strike,
                    "25d Call K": call_strike,
                })

        _skew_spin.empty()
        if skew_rows:
            skew_df = pd.DataFrame(skew_rows)

            # Table
            display_df = skew_df.copy()
            for col in ["ATM IV", "25d Put IV", "25d Call IV"]:
                display_df[col] = display_df[col].apply(lambda v: f"{v:.1f}%")
            display_df["Put Skew"] = display_df["Put Skew"].apply(lambda v: f"{v:.2f}x")
            display_df["Call Skew"] = display_df["Call Skew"].apply(lambda v: f"{v:.2f}x")
            display_df["Risk Reversal"] = display_df["Risk Reversal"].apply(lambda v: f"{v:+.1f}%")
            display_df["Butterfly"] = display_df["Butterfly"].apply(lambda v: f"{v:.1f}%")
            display_df["25d Put K"] = display_df["25d Put K"].apply(lambda v: f"${v:,.0f}" if v else "—")
            display_df["25d Call K"] = display_df["25d Call K"].apply(lambda v: f"${v:,.0f}" if v else "—")
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            # Charts
            fig_skm = make_subplots(rows=2, cols=2,
                                     subplot_titles=["Put Skew", "Call Skew", "Risk Reversal", "Butterfly"],
                                     vertical_spacing=0.12, horizontal_spacing=0.08)

            fig_skm.add_trace(go.Scatter(
                x=skew_df["DTE"], y=skew_df["Put Skew"],
                mode="lines+markers", line=dict(color=COLORS["danger"], width=2),
                name="Put Skew",
            ), row=1, col=1)
            fig_skm.add_hline(y=1.0, line_dash="dot", line_color=COLORS["text_muted"], row=1, col=1)

            fig_skm.add_trace(go.Scatter(
                x=skew_df["DTE"], y=skew_df["Call Skew"],
                mode="lines+markers", line=dict(color=COLORS["success"], width=2),
                name="Call Skew",
            ), row=1, col=2)
            fig_skm.add_hline(y=1.0, line_dash="dot", line_color=COLORS["text_muted"], row=1, col=2)

            rr_colors = [COLORS["danger"] if v < 0 else COLORS["success"] for v in skew_df["Risk Reversal"]]
            fig_skm.add_trace(go.Bar(
                x=skew_df["DTE"], y=skew_df["Risk Reversal"],
                marker_color=rr_colors, name="Risk Rev",
            ), row=2, col=1)
            fig_skm.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.5, row=2, col=1)

            fig_skm.add_trace(go.Scatter(
                x=skew_df["DTE"], y=skew_df["Butterfly"],
                mode="lines+markers", line=dict(color=COLORS["warning"], width=2),
                fill="tozeroy", fillcolor="rgba(255,170,0,0.1)",
                name="Butterfly",
            ), row=2, col=2)

            fig_skm.update_xaxes(title_text="DTE")
            fig_skm.update_layout(
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", height=500,
                showlegend=False,
                margin=dict(l=50, r=20, t=40, b=40),
            )
            st.plotly_chart(fig_skm, use_container_width=True, config=PLOTLY_NOBAR)

            # Summary metrics
            skew_df = pd.DataFrame(skew_rows)
            if not skew_df.empty:
                sm1, sm2, sm3, sm4 = st.columns(4)
                sm1.metric("Avg Put Skew", f"{skew_df['Put Skew'].mean():.2f}x",
                           help="Put IV / ATM IV. >1.0 = puts are expensive.")
                sm2.metric("Avg Call Skew", f"{skew_df['Call Skew'].mean():.2f}x",
                           help="Call IV / ATM IV. >1.0 = calls are expensive.")
                sm3.metric("Avg Risk Reversal", f"{skew_df['Risk Reversal'].mean():+.1f}%",
                           help="Call IV − Put IV. Negative = puts richer than calls (normal for equities).")
                sm4.metric("Avg Butterfly", f"{skew_df['Butterfly'].mean():+.1f}%",
                           help="Measures curvature. High = wings expensive, ATM cheap.")

                # Skew slope quantification (bps per 10-delta step)
                st.divider()
                st.markdown("#### Skew Slope Quantification")
                st.caption("How fast IV changes per unit of delta — the institutional standard for skew measurement.")

                slope_rows = []
                for exp in expirations[:6]:
                    chain = term_data[exp]
                    dte = _dte(exp)
                    if dte <= 0:
                        continue
                    # Get IV at multiple delta points
                    delta_points = [0.10, 0.25, 0.50]
                    put_ivs = {}
                    for d in delta_points:
                        _, iv_at_d = _find_delta_strike(chain, spot, d, "put")
                        if iv_at_d and iv_at_d > 0:
                            put_ivs[d] = iv_at_d

                    if len(put_ivs) >= 2:
                        # Slope = (10Δ IV - 50Δ IV) / (50 - 10) × 1000 = bps per delta point
                        iv_10 = put_ivs.get(0.10)
                        iv_25 = put_ivs.get(0.25)
                        iv_50 = put_ivs.get(0.50)
                        slope_10_50 = ((iv_10 - iv_50) / 0.40 * 100) if iv_10 and iv_50 else None
                        slope_25_50 = ((iv_25 - iv_50) / 0.25 * 100) if iv_25 and iv_50 else None

                        slope_rows.append({
                            "Expiration": exp, "DTE": dte,
                            "10Δ Put IV": f"{iv_10:.1%}" if iv_10 else "N/A",
                            "25Δ Put IV": f"{iv_25:.1%}" if iv_25 else "N/A",
                            "ATM IV": f"{iv_50:.1%}" if iv_50 else "N/A",
                            "Slope (10Δ→ATM)": f"{slope_10_50:+.1f} bps/Δ" if slope_10_50 else "N/A",
                            "Slope (25Δ→ATM)": f"{slope_25_50:+.1f} bps/Δ" if slope_25_50 else "N/A",
                        })

                if slope_rows:
                    st.dataframe(pd.DataFrame(slope_rows), use_container_width=True, hide_index=True)

                    # Skew term structure insight
                    if len(slope_rows) >= 2:
                        front_slope = next((r for r in slope_rows if r.get("Slope (25Δ→ATM)") != "N/A"), None)
                        back_slope = next((r for r in reversed(slope_rows) if r.get("Slope (25Δ→ATM)") != "N/A"), None)
                        if front_slope and back_slope and front_slope != back_slope:
                            st.caption(
                                f"Front skew ({front_slope['DTE']}d): {front_slope['Slope (25Δ→ATM)']} | "
                                f"Back skew ({back_slope['DTE']}d): {back_slope['Slope (25Δ→ATM)']}. "
                                f"{'Front is steeper — sell near-term put spreads, buy far-term protection.' if 'N/A' not in front_slope['Slope (25Δ→ATM)'] else ''}"
                            )

                # Alert on extreme skew
                avg_put = skew_df["Put Skew"].mean()
                if avg_put > 1.20:
                    st.warning(f"Put skew is extreme ({avg_put:.2f}x ATM). Downside protection is heavily bid — "
                               f"consider selling put spreads or ratio spreads to monetize the skew premium.")
                elif avg_put < 0.95:
                    st.info(f"Put skew is unusually flat ({avg_put:.2f}x ATM). The market is not pricing much crash risk — "
                            f"tail hedges may be cheap here.")
        else:
            st.warning("Could not compute skew metrics — need sufficient options data with valid Greeks.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — GAMMA SCALPING
# ══════════════════════════════════════════════════════════════════════════════

with tab6:
    with error_boundary("Gamma Scalping"):
        st.subheader("Gamma Scalping Opportunities")
        with st.expander("Why this matters for your trading"):
            st.markdown("""
**What is gamma scalping?**

You buy an ATM straddle (or strangle) and delta-hedge by trading the underlying stock.
As the stock moves, the straddle's delta shifts (because of gamma). You sell shares when
the stock rallies (delta goes positive) and buy shares when it drops (delta goes negative).
Each hedge locks in a small profit from the gamma convexity.

**The key question:** Does the stock move enough to pay for the theta you're bleeding?

**Gamma scalping P&L = realized vol - implied vol.** If the stock moves more than the
options market expects (realized > implied), you profit. If it moves less, theta eats you alive.

**What this tab shows:**
- **Gamma scalp candidates** — expirations where IV is cheap relative to realized vol
  (IV/HV < 1.0). That's where gamma is underpriced.
- **Break-even daily move** — how much the stock needs to move each day just to cover theta.
  Compare this to actual recent daily moves.
- **Dollar gamma vs theta** — the raw economics: how much you earn per $1 move vs how much
  you lose per day of time decay.
- **Historical move distribution** — shows whether the stock's actual daily moves are
  large enough to make gamma scalping profitable.

**When to gamma scalp:**
- IV is below recent realized vol (cheap gamma)
- The stock has a history of large intraday moves
- You can hedge frequently (need access to real-time execution)
- Avoid right before earnings — IV will be high (expensive gamma) and crush after

**When NOT to gamma scalp:**
- IV is rich (above HV) — you're overpaying for gamma
- Low-vol stocks that don't move enough to cover daily theta
- Illiquid options with wide bid-ask spreads
""")

        # ── Build gamma scalp candidates ──
        _gs_spin = st.empty()
        _gs_spin.info("Scanning for gamma scalping opportunities...")
        gs_rows = []
        for exp in expirations:
            chain = term_data[exp]
            dte = _dte(exp)
            if dte < 3:
                continue

            atm_k = _atm_strike(chain, spot)
            call_row = _get_contract(chain, atm_k, "call")
            put_row = _get_contract(chain, atm_k, "put")
            if call_row is None or put_row is None:
                continue

            call_mid = (float(call_row.get("bid", 0) or 0) + float(call_row.get("ask", 0) or 0)) / 2
            put_mid = (float(put_row.get("bid", 0) or 0) + float(put_row.get("ask", 0) or 0)) / 2
            if call_mid <= 0:
                call_mid = float(call_row.get("last_price", 0) or 0)
            if put_mid <= 0:
                put_mid = float(put_row.get("last_price", 0) or 0)
            straddle_cost = call_mid + put_mid
            if straddle_cost <= 0:
                continue

            atm_iv = float(call_row.get("implied_volatility", 0) or 0)
            if atm_iv <= 0:
                atm_iv = float(put_row.get("implied_volatility", 0) or 0)
            if atm_iv <= 0:
                continue

            call_gamma = float(call_row.get("gamma", 0) or 0)
            call_theta = float(call_row.get("theta", 0) or 0)
            put_gamma = float(put_row.get("gamma", 0) or 0)
            put_theta = float(put_row.get("theta", 0) or 0)

            # Straddle Greeks (buy both)
            straddle_gamma = (call_gamma + put_gamma)
            straddle_theta = (call_theta + put_theta)  # theta is negative

            # Dollar gamma: P&L from a $1 move in underlying (per contract)
            dollar_gamma = straddle_gamma * 100  # per contract, per $1 move squared / 2

            # Dollar theta: daily bleed (per contract)
            dollar_theta = straddle_theta * 100  # negative

            # Break-even daily move: how far the stock must move to cover theta
            # Gamma P&L from a move of size Δ = 0.5 * gamma * Δ² * 100
            # Set 0.5 * gamma * Δ² * 100 = |theta| * 100
            # Δ = sqrt(2 * |theta| / gamma)
            if straddle_gamma > 0 and straddle_theta < 0:
                be_move = np.sqrt(2 * abs(straddle_theta) / straddle_gamma)
                be_move_pct = be_move / spot * 100
            else:
                be_move = 0
                be_move_pct = 0

            # IV/HV ratio — below 1.0 = cheap gamma
            iv_hv = atm_iv / current_hv20 if current_hv20 and current_hv20 > 0 else 0

            # Expected move from IV (daily)
            iv_daily_move = atm_iv / np.sqrt(252) * spot
            iv_daily_move_pct = atm_iv / np.sqrt(252) * 100

            # Volume and OI
            call_oi = int(call_row.get("open_interest", 0) or 0)
            put_oi = int(put_row.get("open_interest", 0) or 0)
            call_vol = int(call_row.get("volume", 0) or 0)
            put_vol = int(put_row.get("volume", 0) or 0)

            gs_rows.append({
                "expiration": exp,
                "dte": dte,
                "strike": atm_k,
                "atm_iv": atm_iv,
                "iv_hv": iv_hv,
                "straddle_cost": straddle_cost,
                "dollar_gamma": dollar_gamma,
                "dollar_theta": dollar_theta,
                "be_move": be_move,
                "be_move_pct": be_move_pct,
                "iv_daily_move": iv_daily_move,
                "iv_daily_move_pct": iv_daily_move_pct,
                "gamma_theta_ratio": abs(dollar_gamma / dollar_theta) if dollar_theta != 0 else 0,
                "total_oi": call_oi + put_oi,
                "total_vol": call_vol + put_vol,
            })

        _gs_spin.empty()
        if not gs_rows:
            st.warning("Could not compute gamma scalping data — need ATM options with valid Greeks.")
        else:
            gs_df = pd.DataFrame(gs_rows)

            # ── Candidate ranking ──
            st.markdown("#### Gamma Scalp Candidates by Expiration")
            st.caption(
                "Ranked by IV/HV ratio — lower means gamma is cheaper relative to how "
                "the stock has actually been moving. Below 1.0x is the sweet spot."
            )

            display_gs = gs_df.copy()
            display_gs = display_gs.sort_values("iv_hv")

            # Add verdict and Gamma/Theta ratio
            display_gs["g_t_ratio"] = display_gs.apply(
                lambda r: abs(r["dollar_gamma"] / r["dollar_theta"]) if r["dollar_theta"] != 0 else 0, axis=1
            )
            display_gs["verdict"] = display_gs["iv_hv"].apply(
                lambda v: "Cheap Gamma" if v < 0.9 else ("Fair" if v < 1.05 else ("Expensive" if v < 1.2 else "Very Expensive"))
            )

            display_table = pd.DataFrame({
                "Expiration": display_gs["expiration"],
                "DTE": display_gs["dte"],
                "Strike": display_gs["strike"].apply(lambda v: f"${v:,.0f}"),
                "ATM IV": display_gs["atm_iv"].apply(lambda v: f"{v*100:.1f}%"),
                "IV/HV": display_gs["iv_hv"].apply(lambda v: f"{v:.2f}x"),
                "Straddle $": display_gs["straddle_cost"].apply(lambda v: f"${v:.2f}"),
                "Γ/Θ Ratio": display_gs["g_t_ratio"].apply(lambda v: f"{v:.1f}x"),
                "BE Move": display_gs["be_move_pct"].apply(lambda v: f"{v:.2f}%"),
                "OI": display_gs["total_oi"].apply(lambda v: f"{v:,}"),
                "Verdict": display_gs["verdict"],
            })
            st.dataframe(
                display_table.style.apply(
                    lambda row: [
                        "background-color: rgba(0,255,150,0.1)" if "Cheap" in str(row.get("Verdict", "")) else
                        "background-color: rgba(255,68,68,0.08)" if "Expensive" in str(row.get("Verdict", "")) else ""
                    ] * len(row), axis=1,
                ),
                use_container_width=True, hide_index=True,
            )

            # Flag best candidates with position structure
            cheap_gamma = gs_df[gs_df["iv_hv"] < 1.0].sort_values("iv_hv")
            if len(cheap_gamma) > 0:
                best = cheap_gamma.iloc[0]
                cost_100 = best["straddle_cost"] * 100
                st.success(
                    f"**Best candidate:** {best['expiration']} ({best['dte']}d) — "
                    f"IV/HV {best['iv_hv']:.2f}x, straddle ${best['straddle_cost']:.2f}, "
                    f"break-even move {best['be_move_pct']:.2f}%/day  \n"
                    f"**Position:** Buy 1x ${best['strike']:.0f} straddle @ ${best['straddle_cost']:.2f} "
                    f"(cost: ${cost_100:.0f} per contract). Delta-hedge daily. "
                    f"Exit if straddle loses >25% or IV spikes >50%."
                )
            else:
                st.info("No expirations with IV below realized vol (IV/HV < 1.0). Gamma is currently expensive across the board.")

            # ── Break-even vs actual moves chart ──
            st.markdown("#### Break-Even Move vs Actual Daily Moves")
            st.caption(
                "The bar shows how far the stock needs to move each day to cover theta. "
                "The horizontal lines show how far it actually moves on average. "
                "If the actual moves exceed the break-even, gamma scalping is profitable."
            )

            # Actual daily move stats
            if len(_returns) > 20:
                actual_daily_abs = _returns.abs()
                avg_daily_move = float(actual_daily_abs.mean()) * 100
                median_daily_move = float(actual_daily_abs.median()) * 100
                p75_daily_move = float(actual_daily_abs.quantile(0.75)) * 100

                fig_be = go.Figure()
                fig_be.add_trace(go.Bar(
                    x=[f"{r['dte']}d" for _, r in gs_df.iterrows()],
                    y=gs_df["be_move_pct"],
                    marker_color=[COLORS["success"] if be < avg_daily_move else COLORS["danger"]
                                  for be in gs_df["be_move_pct"]],
                    text=[f"{v:.2f}%" for v in gs_df["be_move_pct"]],
                    textposition="outside",
                    name="Break-even move",
                ))
                fig_be.add_hline(y=avg_daily_move, line_dash="dash",
                                 line_color=COLORS["accent"], line_width=2,
                                 annotation_text=f"Avg daily move: {avg_daily_move:.2f}%")
                fig_be.add_hline(y=median_daily_move, line_dash="dot",
                                 line_color=COLORS["text_muted"], line_width=1,
                                 annotation_text=f"Median: {median_daily_move:.2f}%")
                fig_be.add_hline(y=p75_daily_move, line_dash="dot",
                                 line_color=COLORS["warning"], line_width=1,
                                 annotation_text=f"75th pctile: {p75_daily_move:.2f}%")
                fig_be.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=380,
                    xaxis_title="Expiration (DTE)",
                    yaxis_title="Daily Move Required (%)",
                    showlegend=False,
                    margin=dict(l=50, r=20, t=30, b=50),
                )
                st.plotly_chart(fig_be, use_container_width=True, config=PLOTLY_NOBAR)

                # ── Dollar gamma vs theta waterfall ──
                st.markdown("#### Dollar Gamma vs Dollar Theta")
                st.caption(
                    "Shows the raw economics: how much you earn per $1 underlying move "
                    "(gamma) vs how much you lose per day (theta). The ratio tells you "
                    "how many $1 moves you need per day to break even."
                )

                fig_gt = make_subplots(specs=[[{"secondary_y": True}]])
                fig_gt.add_trace(go.Bar(
                    x=[f"{r['dte']}d" for _, r in gs_df.iterrows()],
                    y=gs_df["dollar_gamma"],
                    marker_color=COLORS["success"],
                    name="$ Gamma (per $1 move)",
                    opacity=0.8,
                ), secondary_y=False)
                fig_gt.add_trace(go.Bar(
                    x=[f"{r['dte']}d" for _, r in gs_df.iterrows()],
                    y=gs_df["dollar_theta"].abs(),
                    marker_color=COLORS["danger"],
                    name="$ Theta/day (cost)",
                    opacity=0.8,
                ), secondary_y=False)
                fig_gt.add_trace(go.Scatter(
                    x=[f"{r['dte']}d" for _, r in gs_df.iterrows()],
                    y=gs_df["gamma_theta_ratio"],
                    mode="lines+markers",
                    line=dict(color=COLORS["accent"], width=2),
                    marker=dict(size=6),
                    name="Gamma/Theta ratio",
                ), secondary_y=True)
                fig_gt.update_yaxes(title_text="Dollar Amount ($)", secondary_y=False)
                fig_gt.update_yaxes(title_text="Gamma/Theta Ratio", secondary_y=True)
                fig_gt.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=380,
                    xaxis_title="Expiration (DTE)",
                    barmode="group",
                    legend=dict(x=0.02, y=0.98, bgcolor="rgba(0,0,0,0.5)"),
                    margin=dict(l=50, r=60, t=30, b=50),
                )
                st.plotly_chart(fig_gt, use_container_width=True, config=PLOTLY_NOBAR)

                # ── Historical daily move distribution ──
                st.markdown("#### Daily Move Distribution (Last 252 Days)")
                st.caption(
                    "Histogram of actual daily price moves. The vertical lines show "
                    "break-even moves for the nearest and furthest expirations. "
                    "Moves to the right of the line are profitable for gamma scalping."
                )

                daily_moves_pct = (_returns * 100).dropna().values
                fig_hist = go.Figure()
                fig_hist.add_trace(go.Histogram(
                    x=daily_moves_pct,
                    nbinsx=50,
                    marker_color=COLORS["accent"],
                    opacity=0.7,
                    name="Daily moves",
                ))

                # Add break-even lines for nearest and furthest expiration
                if len(gs_df) > 0:
                    nearest_be = gs_df.iloc[0]["be_move_pct"]
                    fig_hist.add_vline(x=nearest_be, line_dash="dash", line_color=COLORS["success"],
                                       annotation_text=f"BE {gs_df.iloc[0]['dte']}d: {nearest_be:.2f}%",
                                       annotation_font_size=10)
                    fig_hist.add_vline(x=-nearest_be, line_dash="dash", line_color=COLORS["success"])
                    if len(gs_df) > 1:
                        furthest_be = gs_df.iloc[-1]["be_move_pct"]
                        fig_hist.add_vline(x=furthest_be, line_dash="dot", line_color=COLORS["warning"],
                                           annotation_text=f"BE {gs_df.iloc[-1]['dte']}d: {furthest_be:.2f}%",
                                           annotation_font_size=10)
                        fig_hist.add_vline(x=-furthest_be, line_dash="dot", line_color=COLORS["warning"])

                fig_hist.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=350,
                    xaxis_title="Daily Move (%)",
                    yaxis_title="Frequency",
                    showlegend=False,
                    margin=dict(l=50, r=20, t=30, b=50),
                )
                st.plotly_chart(fig_hist, use_container_width=True, config=PLOTLY_NOBAR)

                # Win rate estimate
                if len(gs_df) > 0 and len(daily_moves_pct) > 0:
                    nearest_be = gs_df.iloc[0]["be_move_pct"]
                    pct_profitable = (np.abs(daily_moves_pct) > nearest_be).mean() * 100
                    st.markdown(
                        f"**Historical win rate for {gs_df.iloc[0]['dte']}d straddle:** "
                        f"**{pct_profitable:.0f}%** of days had moves exceeding the "
                        f"{nearest_be:.2f}% break-even threshold."
                    )
                    if pct_profitable > 60:
                        st.success("Historical move frequency favors gamma scalping at this expiration.")
                    elif pct_profitable > 45:
                        st.info("Borderline — moves are close to break-even. Execution quality matters.")
                    else:
                        st.warning("Historical moves are insufficient to cover theta at this expiration. Gamma is likely overpriced.")

                # ── Realized Gamma Scalp P&L Backtest ──
                if len(gs_df) > 0 and len(_returns) >= 20:
                    st.divider()
                    st.markdown("#### Gamma Scalp Backtest — What Would Have Happened?")
                    st.caption(
                        "Simulates buying an ATM straddle N days ago and delta-hedging daily. "
                        "Shows cumulative P&L using actual realized price moves."
                    )

                    best_row = gs_df.iloc[0]  # cheapest IV/HV candidate
                    bt_dte = int(best_row["dte"])
                    bt_gamma = best_row["dollar_gamma"]
                    bt_theta = best_row["dollar_theta"]
                    bt_cost = best_row["straddle_cost"] * 100

                    # Use the last bt_dte days of actual returns
                    bt_returns = _returns.tail(min(bt_dte, len(_returns)))
                    bt_moves = bt_returns.values * spot  # dollar moves

                    cum_pnl = []
                    running = 0
                    for move in bt_moves:
                        # Daily P&L ≈ 0.5 × gamma × (ΔS)² + theta
                        gamma_pnl = 0.5 * bt_gamma * (move ** 2)
                        daily = gamma_pnl + bt_theta * 100
                        running += daily
                        cum_pnl.append(running)

                    if cum_pnl:
                        fig_bt = go.Figure()
                        bt_dates = bt_returns.index
                        colors_bt = [COLORS["success"] if v >= 0 else COLORS["danger"] for v in cum_pnl]
                        fig_bt.add_trace(go.Scatter(
                            x=list(range(len(cum_pnl))), y=cum_pnl,
                            mode="lines+markers", line=dict(color=COLORS["accent"], width=2),
                            fill="tozeroy",
                            fillcolor=f"rgba(0,209,255,0.1)",
                            hovertemplate="Day %{x}<br>Cum P&L: $%{y:+,.0f}<extra></extra>",
                        ))
                        fig_bt.add_hline(y=0, line_color="white", line_width=0.5)
                        fig_bt.add_hline(y=-bt_cost, line_dash="dot", line_color=COLORS["danger"],
                                          annotation_text=f"Straddle cost (${bt_cost:.0f})")
                        fig_bt.update_layout(
                            template="plotly_dark", height=300,
                            margin=dict(l=50, r=20, t=10, b=40),
                            xaxis_title="Trading Days", yaxis_title="Cumulative P&L ($)",
                        )
                        st.plotly_chart(fig_bt, use_container_width=True, config=PLOTLY_NOBAR)

                        final_pnl = cum_pnl[-1]
                        bt1, bt2, bt3 = st.columns(3)
                        bt1.metric("Backtest P&L", f"${final_pnl:+,.0f}",
                                   "Profitable" if final_pnl > 0 else "Loss",
                                   delta_color="normal" if final_pnl > 0 else "inverse")
                        bt2.metric("Days Simulated", f"{len(cum_pnl)}")
                        bt3.metric("Daily Avg", f"${final_pnl / len(cum_pnl):+,.0f}/day")

                        if final_pnl > 0:
                            st.success(
                                f"Gamma scalping on the {best_row['dte']}d straddle would have been **profitable** "
                                f"over the last {len(cum_pnl)} days using actual price moves. "
                                f"Realized moves exceeded the break-even threshold."
                            )
                        else:
                            st.warning(
                                f"Gamma scalping would have **lost money** (${final_pnl:+,.0f}) over the last "
                                f"{len(cum_pnl)} days. Actual moves were insufficient to overcome theta decay."
                            )
            else:
                st.warning("Need 20+ days of price history for move distribution analysis.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — SURFACE ANIMATION (10-DAY TIME-LAPSE)
# ══════════════════════════════════════════════════════════════════════════════

with tab7:
    with error_boundary("Surface Animation"):
        st.subheader("IV Surface — Daily Replay")

        # Load snapshots from Supabase (pre-computed IV, no need to back out from prices)
        cached_snapshots = _load_surface_snapshots(ticker_display, n_days=10)
        n_snaps = len(cached_snapshots)

        if n_snaps < 2:
            st.info(
                f"**{n_snaps} snapshot{'s' if n_snaps != 1 else ''} available.** "
                "A snapshot is saved each time you load this page, and the background worker saves one hourly. "
                "After 2+ snapshots you'll see the surface change day-over-day."
            )
        else:
            st.caption(f"Replaying **{n_snaps} daily snapshots** — real market IV, one day at a time.")

            from scipy.interpolate import griddata as _gd
            try:
                from scipy.ndimage import gaussian_filter as _gf
            except ImportError:
                _gf = None

            GRID_M, GRID_D = 40, 20
            m_range = np.linspace(0.82, 1.18, GRID_M)

            all_z_min, all_z_max = np.inf, -np.inf
            frames = []
            frame_labels = []

            for snap in cached_snapshots:
                day_spot = snap["spot"]
                day_date = snap["date"]
                snap_data = snap["data"]

                # Snapshots already have IV — no need to back out from prices
                pts_m, pts_d, pts_iv = [], [], []
                for r in snap_data:
                    k = r.get("strike", 0)
                    dte = r.get("dte", 0)
                    iv = r.get("iv", 0)
                    if iv > 0.01 and dte > 0 and k > 0 and 0.78 * day_spot <= k <= 1.22 * day_spot:
                        pts_m.append(k / day_spot)
                        pts_d.append(dte)
                        pts_iv.append(iv)

                if len(pts_m) < 15:
                    continue

                pts_m, pts_d, pts_iv = np.array(pts_m), np.array(pts_d), np.array(pts_iv)
                d_range = np.linspace(max(pts_d.min(), 1), min(pts_d.max(), 365), GRID_D)
                gm, gd_mesh = np.meshgrid(m_range, d_range)

                z = _gd((pts_m, pts_d), pts_iv, (gm, gd_mesh), method="cubic")
                z_nn = _gd((pts_m, pts_d), pts_iv, (gm, gd_mesh), method="nearest")
                z[np.isnan(z)] = z_nn[np.isnan(z)]
                if _gf is not None:
                    z = _gf(z, sigma=0.7)

                if not np.any(~np.isnan(z)):
                    continue
                z = np.clip(z, 0.01, np.nanpercentile(z[~np.isnan(z)], 98))

                all_z_min = min(all_z_min, float(np.nanmin(z)))
                all_z_max = max(all_z_max, float(np.nanmax(z)))

                strike_grid = (m_range * day_spot).tolist()
                frame_labels.append(f"{day_date} — ${day_spot:,.2f}")
                frames.append({"x": strike_grid, "y": d_range.tolist(), "z": z.tolist(), "spot": day_spot})

            # Also store for comparison tab
            if frames:
                st.session_state["vs_hist_frames"] = frames
                st.session_state["vs_hist_labels"] = frame_labels

            if len(frames) < 2:
                st.warning("Not enough days with valid options data to animate.")
            else:
                cs = [[0, "#0d0887"], [0.15, "#3b049a"], [0.3, "#7201a8"],
                      [0.45, "#ae2891"], [0.6, "#ed6f45"], [0.8, "#fba238"], [1.0, "#f0f921"]]
                z_pad = (all_z_max - all_z_min) * 0.05

                def _surf(f, cbar=True):
                    return go.Surface(
                        x=f["x"], y=f["y"], z=f["z"], colorscale=cs,
                        cmin=all_z_min, cmax=all_z_max, showscale=cbar,
                        colorbar=dict(title="IV", tickformat=".0%", len=0.5, thickness=10) if cbar else None,
                        lighting=dict(ambient=0.5, diffuse=0.6, specular=0.35, roughness=0.4),
                        contours=dict(z=dict(show=True, usecolormap=True, width=1,
                                             size=(all_z_max - all_z_min) / 10)),
                        opacity=0.95,
                        hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>IV: %{z:.1%}<extra></extra>",
                    )

                def _spot(f):
                    s = f["spot"]
                    x_arr, z_arr = np.array(f["x"]), np.array(f["z"])
                    ci = np.argmin(np.abs(x_arr - s))
                    return go.Scatter3d(
                        x=[s] * len(f["y"]), y=f["y"], z=z_arr[:, ci].tolist(),
                        mode="lines", line=dict(color="#ffaa00", width=5), showlegend=False,
                    )

                fig = go.Figure(
                    data=[_surf(frames[0]), _spot(frames[0])],
                    frames=[
                        go.Frame(data=[_surf(f, False), _spot(f)], name=lbl)
                        for f, lbl in zip(frames, frame_labels)
                    ],
                )

                all_x = [v for f in frames for v in f["x"]]
                all_y = [v for f in frames for v in f["y"]]

                fig.update_layout(
                    template="plotly_dark", height=700,
                    margin=dict(t=10, b=80, l=10, r=10),
                    scene=dict(
                        xaxis=dict(title="Strike ($)", range=[min(all_x), max(all_x)],
                                   backgroundcolor="rgba(14,17,23,0.95)", gridcolor="rgba(48,54,61,0.3)"),
                        yaxis=dict(title="DTE", range=[min(all_y), max(all_y)],
                                   backgroundcolor="rgba(14,17,23,0.95)", gridcolor="rgba(48,54,61,0.3)"),
                        zaxis=dict(title="IV", tickformat=".0%",
                                   range=[all_z_min - z_pad, all_z_max + z_pad],
                                   backgroundcolor="rgba(14,17,23,0.95)", gridcolor="rgba(48,54,61,0.3)"),
                        camera=dict(eye=dict(x=1.5, y=-1.5, z=0.8)),
                        aspectratio=dict(x=1.5, y=1.0, z=0.55),
                    ),
                    updatemenus=[dict(
                        type="buttons", showactive=True,
                        y=-0.02, x=0.5, xanchor="center",
                        font=dict(size=13, color="#e0e0e0"),
                        bgcolor="rgba(30,35,42,0.9)", bordercolor="#00d1ff",
                        buttons=[
                            dict(label="  ▶  Play  ", method="animate",
                                 args=[None, {"frame": {"duration": 1000, "redraw": True},
                                              "fromcurrent": True,
                                              "transition": {"duration": 500, "easing": "cubic-in-out"}}]),
                            dict(label="  ⏸  Pause  ", method="animate",
                                 args=[[None], {"frame": {"duration": 0, "redraw": False},
                                                "mode": "immediate", "transition": {"duration": 0}}]),
                        ],
                    )],
                    sliders=[dict(
                        active=0, yanchor="top", xanchor="left",
                        currentvalue=dict(prefix="", visible=True, xanchor="center",
                                          font=dict(size=15, color="#00d1ff", family="monospace")),
                        transition=dict(duration=500, easing="cubic-in-out"),
                        pad=dict(b=5, t=40), len=0.88, x=0.06,
                        bgcolor="rgba(30,35,42,0.6)", activebgcolor="#00d1ff",
                        bordercolor="rgba(48,54,61,0.5)", ticklen=4,
                        font=dict(size=10, color="#aaa"),
                        steps=[
                            dict(args=[[lbl], {"frame": {"duration": 1000, "redraw": True},
                                               "mode": "immediate",
                                               "transition": {"duration": 500, "easing": "cubic-in-out"}}],
                                 label=lbl.split(" —")[0], method="animate")
                            for lbl in frame_labels
                        ],
                    )],
                )
                st.plotly_chart(fig, use_container_width=True)

                # Day-over-day change summary
                st.divider()
                first_spot, last_spot = frames[0]["spot"], frames[-1]["spot"]
                first_z, last_z = np.array(frames[0]["z"]), np.array(frames[-1]["z"])
                sc1, sc2 = st.columns(2)
                sc1.metric("Spot", f"${last_spot:.2f}", f"{(last_spot/first_spot-1)*100:+.1f}%")
                sc2.metric("Avg IV", f"{np.nanmean(last_z):.1%}",
                           f"{np.nanmean(last_z) - np.nanmean(first_z):+.1%}")

                # Diff heatmap: first day vs last day
                if first_z.shape == last_z.shape:
                    diff_z = last_z - first_z
                    fig_diff = go.Figure(data=go.Heatmap(
                        x=frames[-1]["x"], y=frames[-1]["y"], z=diff_z,
                        colorscale=[[0, "#1e3a5f"], [0.35, "#4393c3"], [0.5, "#1c1f26"],
                                    [0.65, "#d6604d"], [1.0, "#b2182b"]],
                        zmid=0,
                        colorbar=dict(title="IV Δ", tickformat=".1%", thickness=12, outlinewidth=0),
                        hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>ΔIV: %{z:+.2%}<extra></extra>",
                    ))
                    fig_diff.update_layout(
                        template="plotly_dark", height=350, margin=dict(t=30, b=0, l=0, r=0),
                        title=dict(text=f"IV Change: {frame_labels[0].split(' —')[0]} → {frame_labels[-1].split(' —')[0]}",
                                   font=dict(size=13)),
                        xaxis_title="Strike ($)", yaxis_title="DTE",
                    )
                    st.plotly_chart(fig_diff, use_container_width=True, config=PLOTLY_NOBAR)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — SURFACE COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

def _build_iv_grid(chain_data, day_spot, grid_m, grid_d):
    """Build an interpolated IV grid from raw chain rows.
    chain_data: list of dicts with strike, dte, iv (or implied_volatility), contract_type."""
    from scipy.interpolate import griddata as _gd
    try:
        from scipy.ndimage import gaussian_filter as _gf
    except ImportError:
        _gf = None

    pts_m, pts_d, pts_v = [], [], []
    for r in chain_data:
        k = r.get("strike") or r.get("strike_price", 0)
        iv = r.get("iv") or r.get("implied_volatility", 0) or 0
        dte = r.get("dte", 0)
        if iv > 0.01 and dte > 0 and 0.78 * day_spot <= k <= 1.22 * day_spot:
            pts_m.append(k / day_spot)
            pts_d.append(dte)
            pts_v.append(iv)

    if len(pts_m) < 15:
        return None

    pts_m, pts_d, pts_v = np.array(pts_m), np.array(pts_d), np.array(pts_v)
    gm, gd_mesh = np.meshgrid(grid_m, grid_d)
    z = _gd((pts_m, pts_d), pts_v, (gm, gd_mesh), method="cubic")
    z_nn = _gd((pts_m, pts_d), pts_v, (gm, gd_mesh), method="nearest")
    z[np.isnan(z)] = z_nn[np.isnan(z)]
    if _gf is not None:
        z = _gf(z, sigma=0.6)
    if not np.any(~np.isnan(z)):
        return None
    z = np.clip(z, 0.01, np.nanpercentile(z[~np.isnan(z)], 98))
    return z


def _current_chain_rows():
    """Extract current chain as list of dicts for grid building."""
    rows = []
    for exp in expirations:
        chain = term_data[exp]
        dte = _dte(exp)
        if dte <= 0:
            continue
        for _, r in chain.iterrows():
            rows.append({
                "strike": r.get("strike_price", 0),
                "dte": dte,
                "iv": r.get("implied_volatility", 0) or 0,
                "type": r.get("contract_type", ""),
            })
    return rows


with tab8:
    with error_boundary("Surface Comparison"):
        st.subheader("Surface Comparison")

        comp_mode = st.radio("Comparison Mode", [
            "Current vs N Days Ago",
            "Call vs Put Surface",
            "Cross-Ticker",
        ], horizontal=True, key="vs_comp_mode")

        grid_m = np.linspace(0.82, 1.18, 35)
        cs_a = [[0, "#0d0887"], [0.2, "#5b02a3"], [0.4, "#9c179e"],
                [0.6, "#ed7953"], [0.8, "#fba238"], [1.0, "#f0f921"]]

        # ── MODE 1: Current vs N Days Ago ──
        if comp_mode == "Current vs N Days Ago":
            st.caption("Compare today's IV surface against a historical snapshot.")

            # Load snapshots directly (not dependent on animation tab)
            _comp_snaps = _load_surface_snapshots(ticker_display, n_days=10)
            if len(_comp_snaps) < 2:
                st.info("Need at least 2 daily snapshots. Load this page on different days, or the hourly worker will build them automatically.")
            else:
                avail_dates = [s["date"] for s in _comp_snaps[:-1]]  # exclude today (compare against older)
                compare_date = st.selectbox("Compare current surface to:",
                                             avail_dates,
                                             format_func=lambda d: f"{d} (Spot: ${next((s['spot'] for s in _comp_snaps if s['date'] == d), 0):,.2f})",
                                             key="vs_comp_date")

                old_snap = next((s for s in _comp_snaps if s["date"] == compare_date), None)
                if not old_snap:
                    st.warning("Selected snapshot not found.")
                else:
                    old_spot = old_snap["spot"]
                    # Snapshots already have IV — use directly
                    old_rows = [{"strike": r["strike"], "dte": r["dte"], "iv": r.get("iv", 0)}
                                for r in old_snap["data"] if r.get("iv", 0) > 0.01]

                    # DTE grid: use current expirations as reference
                    all_dtes = sorted(set(r["dte"] for r in _current_chain_rows() if r["dte"] > 0))
                    if all_dtes:
                        grid_d = np.linspace(min(all_dtes), min(max(all_dtes), 365), 18)
                    else:
                        grid_d = np.linspace(5, 180, 18)

                    z_current = _build_iv_grid(_current_chain_rows(), spot, grid_m, grid_d)
                    z_old = _build_iv_grid(old_rows, old_spot, grid_m, grid_d)

                    if z_current is not None and z_old is not None:
                        strike_grid_now = grid_m * spot
                        strike_grid_old = grid_m * old_spot

                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.markdown(f"**Today** — Spot: ${spot:,.2f}")
                            fig_a = go.Figure(go.Surface(
                                x=strike_grid_now.tolist(), y=grid_d.tolist(), z=z_current.tolist(),
                                colorscale=cs_a, showscale=False,
                                lighting=dict(ambient=0.5, diffuse=0.6, specular=0.3),
                                hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>IV: %{z:.1%}<extra></extra>",
                            ))
                            fig_a.update_layout(
                                template="plotly_dark", height=450, margin=dict(t=5, b=5, l=5, r=5),
                                scene=dict(
                                    xaxis=dict(title="Strike"), yaxis=dict(title="DTE"),
                                    zaxis=dict(title="IV", tickformat=".0%"),
                                    camera=dict(eye=dict(x=1.5, y=-1.5, z=0.8)),
                                    aspectratio=dict(x=1.4, y=1, z=0.5)),
                            )
                            st.plotly_chart(fig_a, use_container_width=True, config=PLOTLY_NOBAR)

                        with col_b:
                            st.markdown(f"**{compare_date}** — Spot: ${old_spot:,.2f}")
                            fig_b = go.Figure(go.Surface(
                                x=strike_grid_old.tolist(), y=grid_d.tolist(), z=z_old.tolist(),
                                colorscale=cs_a, showscale=False,
                                lighting=dict(ambient=0.5, diffuse=0.6, specular=0.3),
                                hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>IV: %{z:.1%}<extra></extra>",
                            ))
                            fig_b.update_layout(
                                template="plotly_dark", height=450, margin=dict(t=5, b=5, l=5, r=5),
                                scene=dict(
                                    xaxis=dict(title="Strike"), yaxis=dict(title="DTE"),
                                    zaxis=dict(title="IV", tickformat=".0%"),
                                    camera=dict(eye=dict(x=1.5, y=-1.5, z=0.8)),
                                    aspectratio=dict(x=1.4, y=1, z=0.5)),
                            )
                            st.plotly_chart(fig_b, use_container_width=True, config=PLOTLY_NOBAR)

                        # Difference heatmap
                        if z_current.shape != z_old.shape:
                            st.info("Surface dimensions differ between dates — skipping difference heatmap.")
                        else:
                            diff = z_current - z_old
                            avg_diff = np.nanmean(diff)
                            st.subheader("IV Difference (Today − Historical)")
                            fig_diff = go.Figure(go.Heatmap(
                                x=strike_grid_now.tolist(), y=grid_d.tolist(), z=diff.tolist(),
                                colorscale=[[0, "#1e3a5f"], [0.35, "#4393c3"], [0.5, "#1c1f26"],
                                            [0.65, "#d6604d"], [1.0, "#b2182b"]],
                                zmid=0,
                                colorbar=dict(title="ΔIV", tickformat=".1%", thickness=12),
                                hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>ΔIV: %{z:+.2%}<extra></extra>",
                            ))
                            fig_diff.update_layout(template="plotly_dark", height=350,
                                                   margin=dict(t=10, b=0, l=0, r=0),
                                                   xaxis_title="Strike ($)", yaxis_title="DTE")
                            st.plotly_chart(fig_diff, use_container_width=True, config=PLOTLY_NOBAR)

                            st.caption(
                                f"Average IV change: **{avg_diff:+.1%}**. "
                                f"{'Surface expanded (options got more expensive).' if avg_diff > 0 else 'Surface compressed (options got cheaper).'}"
                            )
                    else:
                        st.warning("Not enough data points to build one or both surfaces.")

        # ── MODE 2: Call vs Put Surface ──
        elif comp_mode == "Call vs Put Surface":
            st.caption("Compare call IV vs put IV surfaces. Divergences reveal skew dynamics and hedging demand.")

            all_rows = _current_chain_rows()
            call_rows = [r for r in all_rows if r.get("type") == "call" and r["iv"] > 0]
            put_rows = [r for r in all_rows if r.get("type") == "put" and r["iv"] > 0]

            all_dtes = sorted(set(r["dte"] for r in all_rows if r["dte"] > 0))
            grid_d = np.linspace(min(all_dtes), min(max(all_dtes), 365), 18) if all_dtes else np.linspace(5, 180, 18)

            z_calls = _build_iv_grid(call_rows, spot, grid_m, grid_d)
            z_puts = _build_iv_grid(put_rows, spot, grid_m, grid_d)

            if z_calls is not None and z_puts is not None:
                strike_grid = (grid_m * spot).tolist()

                col_a, col_b = st.columns(2)
                with col_a:
                    st.markdown("**Call IV Surface**")
                    fig_c = go.Figure(go.Surface(
                        x=strike_grid, y=grid_d.tolist(), z=z_calls.tolist(),
                        colorscale=[[0, "#003f5c"], [0.5, "#00d1ff"], [1.0, "#ffffff"]],
                        showscale=False,
                        lighting=dict(ambient=0.5, diffuse=0.6, specular=0.3),
                        hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>Call IV: %{z:.1%}<extra></extra>",
                    ))
                    fig_c.update_layout(template="plotly_dark", height=450, margin=dict(t=5, b=5, l=5, r=5),
                                        scene=dict(xaxis=dict(title="Strike"), yaxis=dict(title="DTE"),
                                                   zaxis=dict(title="Call IV", tickformat=".0%"),
                                                   camera=dict(eye=dict(x=1.5, y=-1.5, z=0.8)),
                                                   aspectratio=dict(x=1.4, y=1, z=0.5)))
                    st.plotly_chart(fig_c, use_container_width=True, config=PLOTLY_NOBAR)

                with col_b:
                    st.markdown("**Put IV Surface**")
                    fig_p = go.Figure(go.Surface(
                        x=strike_grid, y=grid_d.tolist(), z=z_puts.tolist(),
                        colorscale=[[0, "#3f0000"], [0.5, "#ff4b4b"], [1.0, "#ffffff"]],
                        showscale=False,
                        lighting=dict(ambient=0.5, diffuse=0.6, specular=0.3),
                        hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>Put IV: %{z:.1%}<extra></extra>",
                    ))
                    fig_p.update_layout(template="plotly_dark", height=450, margin=dict(t=5, b=5, l=5, r=5),
                                        scene=dict(xaxis=dict(title="Strike"), yaxis=dict(title="DTE"),
                                                   zaxis=dict(title="Put IV", tickformat=".0%"),
                                                   camera=dict(eye=dict(x=1.5, y=-1.5, z=0.8)),
                                                   aspectratio=dict(x=1.4, y=1, z=0.5)))
                    st.plotly_chart(fig_p, use_container_width=True, config=PLOTLY_NOBAR)

                # Put-Call IV spread heatmap
                pc_diff = z_puts - z_calls
                st.subheader("Put − Call IV Spread")
                fig_pc = go.Figure(go.Heatmap(
                    x=strike_grid, y=grid_d.tolist(), z=pc_diff.tolist(),
                    colorscale=[[0, "#003f5c"], [0.4, "#1c1f26"], [0.5, "#1c1f26"],
                                [0.6, "#1c1f26"], [1.0, "#b2182b"]],
                    zmid=0,
                    colorbar=dict(title="Put−Call", tickformat=".1%", thickness=12),
                    hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>Spread: %{z:+.2%}<extra></extra>",
                ))
                fig_pc.update_layout(template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                                     xaxis_title="Strike ($)", yaxis_title="DTE")
                st.plotly_chart(fig_pc, use_container_width=True, config=PLOTLY_NOBAR)

                avg_spread = np.nanmean(pc_diff)
                otm_put_avg = np.nanmean(pc_diff[:, :len(grid_m)//3])  # left third = low strikes = OTM puts
                st.caption(
                    f"Avg put-call spread: **{avg_spread:+.1%}**. "
                    f"OTM put premium: **{otm_put_avg:+.1%}**. "
                    f"{'Heavy downside hedging demand.' if otm_put_avg > 0.02 else 'Normal skew levels.'}"
                )
            else:
                st.warning("Not enough call or put data to build surfaces.")

        # ── MODE 3: Cross-Ticker ──
        elif comp_mode == "Cross-Ticker":
            st.caption("Compare IV surfaces of two different underlyings side by side.")

            with st.form("cross_ticker_form"):
                ct1, ct2 = st.columns(2)
                with ct1:
                    ticker_a = st.text_input("Ticker A", value=ticker_display, key="vs_comp_a")
                with ct2:
                    ticker_b = st.text_input("Ticker B", value="QQQ", key="vs_comp_b")
                _comp_submitted = st.form_submit_button("Compare Surfaces", type="primary", use_container_width=True)

            if _comp_submitted:
                ticker_a_fmt = format_massive_ticker(ticker_a)
                ticker_b_fmt = format_massive_ticker(ticker_b)

                with fun_loader("data"):
                    # Fetch both chains
                    chain_a = fetch_options_chain(ticker_a_fmt, expiration=None, max_pages=10)
                    chain_b = fetch_options_chain(ticker_b_fmt, expiration=None, max_pages=10)

                    px_a = fetch_massive_data(ticker_a_fmt, 5)
                    px_b = fetch_massive_data(ticker_b_fmt, 5)

                    spot_a = float(px_a["Close"].iloc[-1]) if px_a is not None and not px_a.empty else None
                    spot_b = float(px_b["Close"].iloc[-1]) if px_b is not None and not px_b.empty else None

                if chain_a is not None and chain_b is not None and spot_a and spot_b:
                    # Build rows for each
                    def _chain_to_rows(chain, sp):
                        rows = []
                        for _, r in chain.iterrows():
                            k = r.get("strike_price", 0)
                            iv = r.get("implied_volatility", 0) or 0
                            exp = r.get("expiration_date", "")
                            dte = max((pd.to_datetime(exp) - pd.Timestamp.now()).days, 0) if exp else 0
                            if iv > 0 and dte > 0 and 0.78 * sp <= k <= 1.22 * sp:
                                rows.append({"strike": k, "dte": dte, "iv": iv})
                        return rows

                    rows_a = _chain_to_rows(chain_a, spot_a)
                    rows_b = _chain_to_rows(chain_b, spot_b)

                    # Shared DTE grid
                    all_d = sorted(set(r["dte"] for r in rows_a + rows_b if r["dte"] > 0))
                    grid_d = np.linspace(min(all_d), min(max(all_d), 365), 18) if all_d else np.linspace(5, 180, 18)

                    z_a = _build_iv_grid(rows_a, spot_a, grid_m, grid_d)
                    z_b = _build_iv_grid(rows_b, spot_b, grid_m, grid_d)

                    if z_a is not None and z_b is not None:
                        st.session_state["vs_comp_result"] = {
                            "z_a": z_a, "z_b": z_b,
                            "spot_a": spot_a, "spot_b": spot_b,
                            "ticker_a": ticker_a_fmt, "ticker_b": ticker_b_fmt,
                            "grid_d": grid_d,
                        }
                    else:
                        st.warning("Not enough data for one or both tickers.")
                else:
                    st.error("Could not fetch data for one or both tickers.")

            if "vs_comp_result" in st.session_state:
                cr = st.session_state["vs_comp_result"]
                z_a, z_b = cr["z_a"], cr["z_b"]
                spot_a, spot_b = cr["spot_a"], cr["spot_b"]
                grid_d = cr["grid_d"]

                strikes_a = (grid_m * spot_a).tolist()
                strikes_b = (grid_m * spot_b).tolist()

                # Shared z range (guard against all-NaN)
                z_lo = min(np.nanmin(z_a), np.nanmin(z_b)) if np.any(~np.isnan(z_a)) and np.any(~np.isnan(z_b)) else 0.05
                z_hi = max(np.nanmax(z_a), np.nanmax(z_b)) if np.any(~np.isnan(z_a)) and np.any(~np.isnan(z_b)) else 1.0
                if z_hi <= z_lo:
                    z_hi = z_lo + 0.01

                col_a, col_b = st.columns(2)
                for col, label, strikes, z_data, sp in [
                    (col_a, cr["ticker_a"], strikes_a, z_a, spot_a),
                    (col_b, cr["ticker_b"], strikes_b, z_b, spot_b),
                ]:
                    with col:
                        st.markdown(f"**{label}** — ${sp:,.2f}")
                        fig_t = go.Figure(go.Surface(
                            x=strikes, y=grid_d.tolist(), z=z_data.tolist(),
                            colorscale=cs_a, cmin=z_lo, cmax=z_hi, showscale=False,
                            lighting=dict(ambient=0.5, diffuse=0.6, specular=0.3),
                            hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>IV: %{z:.1%}<extra></extra>",
                        ))
                        fig_t.update_layout(
                            template="plotly_dark", height=450, margin=dict(t=5, b=5, l=5, r=5),
                            scene=dict(
                                xaxis=dict(title="Strike"), yaxis=dict(title="DTE"),
                                zaxis=dict(title="IV", tickformat=".0%",
                                           range=[z_lo * 0.95, z_hi * 1.05]),
                                camera=dict(eye=dict(x=1.5, y=-1.5, z=0.8)),
                                aspectratio=dict(x=1.4, y=1, z=0.5)),
                        )
                        st.plotly_chart(fig_t, use_container_width=True, config=PLOTLY_NOBAR)

                # IV difference heatmap (in moneyness space since tickers have different prices)
                diff = z_a - z_b
                st.subheader(f"IV Difference: {cr['ticker_a']} − {cr['ticker_b']}")
                fig_xd = go.Figure(go.Heatmap(
                    x=grid_m.tolist(), y=grid_d.tolist(), z=diff.tolist(),
                    colorscale=[[0, "#1e3a5f"], [0.35, "#4393c3"], [0.5, "#1c1f26"],
                                [0.65, "#d6604d"], [1.0, "#b2182b"]],
                    zmid=0,
                    colorbar=dict(title="ΔIV", tickformat=".1%", thickness=12),
                    hovertemplate="Moneyness: %{x:.2f}<br>DTE: %{y:.0f}d<br>ΔIV: %{z:+.2%}<extra></extra>",
                ))
                fig_xd.update_layout(template="plotly_dark", height=350, margin=dict(t=10, b=0, l=0, r=0),
                                     xaxis_title="Moneyness (Strike / Spot)", yaxis_title="DTE")
                st.plotly_chart(fig_xd, use_container_width=True, config=PLOTLY_NOBAR)

                # Summary metrics
                sm1, sm2, sm3 = st.columns(3)
                avg_a = np.nanmean(z_a)
                avg_b = np.nanmean(z_b)
                sm1.metric(f"{cr['ticker_a']} Avg IV", f"{avg_a:.1%}")
                sm2.metric(f"{cr['ticker_b']} Avg IV", f"{avg_b:.1%}")
                sm3.metric("IV Spread", f"{avg_a - avg_b:+.1%}",
                           help=f"Positive = {cr['ticker_a']} options are more expensive")

                if avg_a > avg_b * 1.15:
                    st.info(f"**{cr['ticker_a']}** is trading at a **{((avg_a/avg_b)-1)*100:.0f}% IV premium** to {cr['ticker_b']}. "
                            f"Consider relative value trades if the premium is historically unusual.")
                elif avg_b > avg_a * 1.15:
                    st.info(f"**{cr['ticker_b']}** is trading at a **{((avg_b/avg_a)-1)*100:.0f}% IV premium** to {cr['ticker_a']}.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 9 — GEMINI TRADE IDEAS
# ══════════════════════════════════════════════════════════════════════════════

with tab9:
    with error_boundary("Gemini Trade Ideas"):
        st.subheader("AI Trade Suggestions — Gemini 2.5 Pro")
        st.caption(
            "Sends the full vol surface profile to Google's top Gemini model for "
            "structured trade ideas based on skew, term structure, dislocations, and regime."
        )

        from src.api_keys import get_secret as _get_key_fn
        gemini_key = _get_key_fn("GEMINI_API_KEY")

        if not gemini_key:
            st.error("Gemini API key not configured. Add GEMINI_API_KEY to your secrets.")
        else:
            # Build the surface context payload
            def _build_surface_context():
                """Collect all surface metrics into a text block for the LLM."""
                lines = [f"Ticker: {ticker_display}", f"Spot: ${spot:.2f}"]

                # Vol regime summary (computed above tabs)
                lines.append(f"\n--- VOL REGIME ---")
                lines.append(f"Vol Level: {_vol_level} (front ATM IV: {_front_atm:.1%})")
                lines.append(f"VRP: {_vrp_regime} ({_vrp:+.1%})")
                lines.append(f"Skew: {_skew_regime} (put skew {_put_skew_ratio:.2f}x ATM)")
                lines.append(f"Term Structure: {_ts_shape}")

                # Historical percentile context
                if _pctiles and any(v is not None for v in _pctiles.values()):
                    lines.append("\n--- HISTORICAL PERCENTILE (252-day) ---")
                    for key, label in [("atm_iv", "ATM IV"), ("put_skew", "Put Skew"), ("vrp", "VRP"),
                                       ("iv_hv_ratio", "IV/HV Ratio"), ("hv20", "HV20")]:
                        p = _pctiles.get(key)
                        if p is not None:
                            extremity = "EXTREME HIGH" if p > 90 else ("HIGH" if p > 75 else ("LOW" if p < 25 else ("EXTREME LOW" if p < 10 else "NORMAL")))
                            lines.append(f"  {label}: {p:.0f}th percentile ({extremity})")

                if current_hv20 is not None:
                    lines.append(f"20-Day HV: {current_hv20:.1%}")
                if current_hv60 is not None:
                    lines.append(f"60-Day HV: {current_hv60:.1%}")

                # ATM IV by expiration (using cache)
                lines.append("\n--- ATM IV Term Structure ---")
                for exp in expirations[:8]:
                    dte = _dte(exp)
                    if dte <= 0:
                        continue
                    atm = _atm_iv_cache.get(exp, 0)
                    if atm > 0:
                        lines.append(f"  {exp} ({dte}d): IV={atm:.1%}")

                # Skew metrics per expiration
                lines.append("\n--- Skew Profile ---")
                for exp in expirations[:6]:
                    chain = term_data[exp]
                    dte = _dte(exp)
                    if dte <= 0:
                        continue
                    atm = _atm_iv_cache.get(exp, 0)
                    if atm <= 0:
                        continue
                    p25_strike, p25_iv = _find_delta_strike(chain, spot, 0.25, "put")
                    c25_strike, c25_iv = _find_delta_strike(chain, spot, 0.25, "call")
                    skew_str = ""
                    if p25_iv and atm > 0:
                        skew_str += f"25Δ put skew={((p25_iv/atm)-1)*100:+.0f}% "
                    if c25_iv and atm > 0:
                        skew_str += f"25Δ call skew={((c25_iv/atm)-1)*100:+.0f}%"
                    lines.append(f"  {exp} ({dte}d): ATM={atm:.1%} {skew_str}")

                # Variance risk premium (use precomputed values)
                if current_hv20 is not None and current_hv20 > 0 and _front_atm > 0:
                    lines.append(f"\nVariance Risk Premium (front IV - HV20): {_vrp:+.1%}")
                    lines.append(f"  {_vrp_regime}")

                # Term structure shape (use precomputed)
                if len(expirations) >= 2:
                    front_iv = _front_atm
                    back_iv = _back_atm
                    if front_iv > 0 and back_iv > 0:
                        shape = "Backwardation (front > back)" if front_iv > back_iv else "Contango (front < back)"
                        lines.append(f"\nTerm Structure: {shape}")
                        lines.append(f"  Front ({expirations[0]}): {front_iv:.1%}")
                        lines.append(f"  Back ({expirations[-1]}): {back_iv:.1%}")

                # Recent price action
                if len(_returns) >= 5:
                    ret_5d = float(px_df["Close"].iloc[-1] / px_df["Close"].iloc[-6] - 1) * 100 if len(px_df) > 5 else 0
                    ret_20d = float(px_df["Close"].iloc[-1] / px_df["Close"].iloc[-21] - 1) * 100 if len(px_df) > 20 else 0
                    lines.append(f"\n--- Recent Price Action ---")
                    lines.append(f"5-Day Return: {ret_5d:+.1f}%")
                    lines.append(f"20-Day Return: {ret_20d:+.1f}%")
                    recent_realized = float(_returns.tail(5).std() * np.sqrt(252) * 100)
                    lines.append(f"5-Day Realized Vol: {recent_realized:.0f}%")

                # Historical surface if available
                hist = st.session_state.get("vs_hist_surface", {})
                if hist:
                    dates = sorted(hist.keys())
                    if len(dates) >= 2:
                        old_spot = hist[dates[0]]["spot"]
                        lines.append(f"\n--- 10-Day Context ---")
                        lines.append(f"Spot {dates[0]}: ${old_spot:.2f} → Today: ${spot:.2f} ({(spot/old_spot-1)*100:+.1f}%)")

                # Cross-page signal composite
                try:
                    from src.signal_engine import compute_composite
                    comp = compute_composite(ticker_display)
                    if comp and comp["n_signals"] >= 2:
                        lines.append(f"\n--- CROSS-PAGE SIGNAL COMPOSITE ---")
                        lines.append(f"Direction: {comp['overall_direction'].upper()} (conviction {comp['overall_conviction']:.0%})")
                        lines.append(f"Vol View: {comp['vol_regime']}")
                        lines.append(f"Sources: {comp['n_signals']} ({comp['signal_agreement']:.0%} agreement)")
                        for s in comp["signals"][:5]:
                            lines.append(f"  • {s['source']}: {s['direction']} ({s['conviction']:.0%}) — {s.get('reasoning', '')[:80]}")
                except Exception:
                    pass

                return "\n".join(lines)

            # Trade idea styles
            idea_style = st.radio("Analysis focus", [
                "Full Surface Scan",
                "Income / Theta Plays",
                "Directional Bets",
                "Volatility Trades",
                "Hedging Strategies",
            ], horizontal=True, key="vs_gemini_style")

            style_prompts = {
                "Full Surface Scan": "Analyze the full surface and suggest 3-5 trades across categories (income, directional, vol, hedging). For each, specify exact strikes, expirations, and expected P&L.",
                "Income / Theta Plays": "Focus on theta-positive income strategies. Look for overpriced options to sell. Suggest covered calls, credit spreads, iron condors, or naked puts with exact strikes and expirations.",
                "Directional Bets": "Focus on directional plays. Analyze the skew and term structure for bullish or bearish setups. Suggest debit spreads, ratio spreads, or outright option buys with exact parameters.",
                "Volatility Trades": "Focus on pure volatility trades. Analyze the variance risk premium, term structure shape, and skew for straddles, strangles, calendars, or vol spread trades. Specify entries and exits.",
                "Hedging Strategies": "Focus on portfolio hedging. Suggest the most cost-effective downside protection: put spreads, collars, or tail-risk hedges. Optimize cost vs coverage.",
            }

            @st.fragment
            def _gemini_fragment():
                if st.button("Generate Trade Ideas", type="primary", use_container_width=True, key="vs_gemini_run"):
                    context = _build_surface_context()

                    system_prompt = (
                        "You are a senior options market maker and volatility strategist at a top-tier institutional desk. "
                        "You have deep expertise in SABR surface modeling, Vanna-Volga pricing, higher-order Greek management, "
                        "and systematic volatility arbitrage.\n\n"
                        "The user has loaded a live vol surface. Below is the surface profile data.\n\n"
                        "You MUST structure your response EXACTLY like this example. Follow this format precisely:\n\n"
                        '## Surface Assessment\n\n'
                        '[2-3 sentences on what the surface is telling you. Reference VRP, skew, term structure.]\n\n'
                        '---\n\n'
                        '## Trade 1: [Name]\n\n'
                        '**Strategy:** Iron Condor &nbsp;|&nbsp; **Conviction:** 4/5 &nbsp;|&nbsp; **R:R Grade:** B+\n\n'
                        '#### Legs\n'
                        '| # | Action | Type | Strike | Expiration | Est. Price |\n'
                        '|---|--------|------|--------|------------|------------|\n'
                        '| 1 | SELL | Put | $620 | 2026-03-30 | ~$1.85 |\n'
                        '| 2 | BUY | Put | $610 | 2026-03-30 | ~$0.45 |\n'
                        '| 3 | SELL | Call | $650 | 2026-03-30 | ~$1.10 |\n'
                        '| 4 | BUY | Call | $660 | 2026-03-30 | ~$0.20 |\n\n'
                        '#### P&L Profile\n'
                        '| | |\n'
                        '|---|---|\n'
                        '| **Net Credit** | $2.30 ($230/contract) |\n'
                        '| **Max Profit** | $230 (if between $620-$650 at exp) |\n'
                        '| **Max Loss** | $770 (width $10 minus credit) |\n'
                        '| **Breakevens** | $617.70 / $652.30 |\n'
                        '| **Prob. of Profit** | ~68% (based on delta) |\n\n'
                        '#### The Edge\n'
                        '[2-3 sentences explaining WHY this trade works. Reference specific surface data: '
                        'VRP magnitude, put skew steepness, term structure shape, IV vs HV ratio at this expiration.]\n\n'
                        '#### Risk Flags\n'
                        '- [Risk 1: e.g., FOMC on April 2 — could spike vol]\n'
                        '- [Risk 2: e.g., Earnings in 3 weeks — front vol could expand]\n\n'
                        '---\n\n'
                        '## Trade 2: [Name]\n'
                        '[...same format...]\n\n'
                        '---\n\n'
                        '## Portfolio Note\n'
                        '[If running all trades: net Delta, Gamma, Theta, Vega exposure. '
                        'Is the combined book long or short vol? Any correlated risks?]\n\n'
                        "RULES:\n"
                        "- Give 3-5 trades. Complete ALL of them. Do NOT truncate.\n"
                        "- Use ## for trade headers (not ###). Use #### for subsections.\n"
                        "- Every trade MUST have: Legs table, P&L Profile table, The Edge section, Risk Flags list.\n"
                        "- Separate each trade with a horizontal rule (---).\n"
                        "- Estimated prices must be realistic given the IV data.\n"
                        "- Be direct. No disclaimers. No 'this is not financial advice'. Give your real desk view.\n"
                        "- Include Prob. of Profit estimate in the P&L table (use delta as proxy).\n\n"
                        f"SURFACE DATA:\n{context}\n\n"
                        f"ANALYSIS FOCUS: {style_prompts[idea_style]}"
                    )

                    # Check AI cache first
                    from src.ai_cache import get_cached_ai, cache_ai_response, build_cache_key_from_metrics
                    _ai_key = build_cache_key_from_metrics(
                        f"vol_surface_{idea_style}", ticker_display,
                        spot=spot, iv=_front_atm, skew=_put_skew_ratio, vrp=_vrp,
                    )
                    _cached_ai = get_cached_ai(_ai_key)
                    if _cached_ai:
                        st.session_state["vs_gemini_result"] = _cached_ai
                        st.session_state["vs_gemini_style_used"] = idea_style
                        st.toast("Loaded from AI cache (same surface profile)")
                    else:
                        with fun_loader("ai"):
                            try:
                                from google import genai
                                from google.genai import types

                                client = genai.Client(api_key=gemini_key)
                                response = client.models.generate_content(
                                    model="gemini-2.5-pro",
                                    contents=system_prompt,
                                    config=types.GenerateContentConfig(
                                        max_output_tokens=20000,
                                        temperature=0.4,
                                    ),
                                )
                                result_text = response.text
                                st.session_state["vs_gemini_result"] = result_text
                                st.session_state["vs_gemini_style_used"] = idea_style
                                # Cache for 2 hours
                                cache_ai_response(_ai_key, result_text, model="gemini-2.5-pro",
                                                   source_page="vol_surface", ticker=ticker_display,
                                                   ttl_hours=2, cost_estimate=0.05,
                                                   prompt_summary=f"{idea_style} | spot={spot:.0f} IV={_front_atm:.1%} skew={_put_skew_ratio:.2f}")
                            except Exception as e:
                                err = str(e).lower()
                                if "api_key" in err or "authentication" in err or "403" in err:
                                    st.error("Gemini API key is invalid or expired. Check your GEMINI_API_KEY secret.")
                                elif "quota" in err or "rate" in err or "429" in err:
                                    st.error("Rate limit exceeded. Wait 30 seconds and try again.")
                                elif "404" in err or "not found" in err:
                                    st.error("Model not available. Check that `gemini-2.5-pro` is accessible on your API key.")
                                else:
                                    st.error(f"Gemini API error: {e}")
                                logger.error(f"Gemini trade ideas failed: {e}")

                # Display results
                if "vs_gemini_result" in st.session_state:
                    style_used = st.session_state.get("vs_gemini_style_used", "")

                    # Header
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">'
                        f'<div style="border:1px solid {COLORS["accent"]};border-radius:6px;padding:6px 14px;'
                        f'background:rgba(0,209,255,0.06);">'
                        f'<span style="color:{COLORS["accent"]};font-weight:700;font-size:0.9rem;">Gemini 2.5 Pro</span></div>'
                        f'<div style="color:{COLORS["text_muted"]};font-size:0.8rem;">'
                        f'{style_used} &bull; {ticker_display} &bull; Spot ${spot:,.2f}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    # Split on --- and render each section
                    raw = st.session_state["vs_gemini_result"]
                    sections = [s.strip() for s in raw.split("---") if s.strip() and len(s.strip()) > 30]

                    for section in sections:
                        is_trade = section.startswith("## Trade") or "#### Legs" in section or "| # |" in section
                        is_portfolio = "## Portfolio" in section

                        if is_trade:
                            # Extract trade title for the expander
                            first_line = section.split("\n")[0].strip().lstrip("#").strip()
                            with st.expander(first_line, expanded=True):
                                st.markdown(section)
                        elif is_portfolio:
                            st.markdown("---")
                            st.markdown(section)
                        else:
                            # Surface Assessment or other prose
                            st.markdown(section)

                    st.divider()
                    st.caption(
                        "AI-generated trade ideas are for educational purposes only. "
                        "Always validate with your own analysis before trading. "
                        "Options trading involves substantial risk of loss."
                    )
            _gemini_fragment()


# ─── FOOTER ────────────────────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    "Volatility surfaces are based on current market quotes and may not reflect "
    "executable prices. Not financial advice."
)
render_data_source_footer()
