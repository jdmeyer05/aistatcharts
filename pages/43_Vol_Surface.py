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
    """Find the strike closest to a target delta. Prefers contracts with OI > 0."""
    sub = chain[chain["contract_type"] == opt_type].copy()
    sub = sub[sub["implied_volatility"] > 0]
    if sub.empty:
        return None, None
    # Prefer contracts with open interest (filter out 0-OI noise)
    if "open_interest" in sub.columns:
        sub_liquid = sub[sub["open_interest"].fillna(0) > 0]
        if len(sub_liquid) >= 3:
            sub = sub_liquid
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
    _load_container = st.container()
    with _load_container:
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

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(_fetch_exp, exp): exp for exp in prefetch}
                for future in as_completed(futures):
                    exp, tdf = future.result()
                    if tdf is not None:
                        term_data[exp] = tdf

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


# ── Shared AI context builder (used by tab1 narrator and tab9 trade ideas) ──
def _build_surface_context():
    """Collect all surface metrics into a text block for the LLM."""
    lines = [f"Ticker: {ticker_display}", f"Spot: ${spot:.2f}"]

    # Vol regime summary
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

    # ATM IV by expiration
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
            skew_str += f"25\u0394 put skew={((p25_iv/atm)-1)*100:+.0f}% "
        if c25_iv and atm > 0:
            skew_str += f"25\u0394 call skew={((c25_iv/atm)-1)*100:+.0f}%"
        lines.append(f"  {exp} ({dte}d): ATM={atm:.1%} {skew_str}")

    # Butterfly & Risk Reversal per expiration
    _has_fly = False
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
        if p25_iv and c25_iv and atm > 0:
            if not _has_fly:
                lines.append("\n--- BUTTERFLY & RISK REVERSAL ---")
                _has_fly = True
            _fly = (p25_iv + c25_iv - 2 * atm) * 100
            _rr = (c25_iv - p25_iv) * 100
            _fly_read = "fat tails priced" if _fly > 2 else ("thin tails" if _fly < -1 else "normal")
            _rr_read = "calls richer (unusual)" if _rr > 1 else ("puts richer (normal)" if _rr < -1 else "balanced")
            lines.append(f"  {exp} ({dte}d): Butterfly={_fly:+.1f}bps ({_fly_read}) | RiskRev={_rr:+.1f}bps ({_rr_read})")
    if not _has_fly:
        lines.append("\n--- BUTTERFLY & RISK REVERSAL ---")
        lines.append("  [Insufficient delta data to compute]")

    # Variance risk premium
    if current_hv20 is not None and current_hv20 > 0 and _front_atm > 0:
        lines.append(f"\n--- VARIANCE RISK PREMIUM ---")
        lines.append(f"Front IV - HV20: {_vrp:+.1%} ({_vrp_regime})")
        lines.append(f"IV/HV Ratio: {_front_atm / current_hv20:.2f}x {'(cheap gamma)' if _front_atm < current_hv20 else ('(expensive gamma)' if _front_atm / current_hv20 > 1.3 else '(fair)')}")
    else:
        lines.append(f"\n--- VARIANCE RISK PREMIUM ---")
        lines.append("  [HV20 unavailable]")

    # Term structure shape
    if len(expirations) >= 2:
        front_iv = _front_atm
        back_iv = _back_atm
        if front_iv > 0 and back_iv > 0:
            shape = "Backwardation (front > back)" if front_iv > back_iv else "Contango (front < back)"
            lines.append(f"\nTerm Structure: {shape}")
            lines.append(f"  Front ({expirations[0]}): {front_iv:.1%}")
            lines.append(f"  Back ({expirations[-1]}): {back_iv:.1%}")
        else:
            lines.append(f"\nTerm Structure: [Insufficient IV data]")
    else:
        lines.append(f"\nTerm Structure: [Only 1 expiration loaded]")

    # Gamma scalping metrics
    lines.append(f"\n--- GAMMA SCALP METRICS ---")
    try:
        _gs_exp = expirations[0]
        _gs_chain = term_data[_gs_exp]
        _gs_atm_iv = _atm_iv_cache.get(_gs_exp, 0)
        if _gs_atm_iv > 0 and current_hv20 is not None and current_hv20 > 0:
            _gs_iv_hv = _gs_atm_iv / current_hv20
            _gs_calls = _gs_chain[(_gs_chain["contract_type"] == "call") & (_gs_chain["implied_volatility"] > 0)].reset_index(drop=True)
            _gs_puts = _gs_chain[(_gs_chain["contract_type"] == "put") & (_gs_chain["implied_volatility"] > 0)].reset_index(drop=True)
            if "open_interest" in _gs_calls.columns:
                _gs_calls_liq = _gs_calls[_gs_calls["open_interest"].fillna(0) > 0]
                if len(_gs_calls_liq) >= 1:
                    _gs_calls = _gs_calls_liq.reset_index(drop=True)
            if "open_interest" in _gs_puts.columns:
                _gs_puts_liq = _gs_puts[_gs_puts["open_interest"].fillna(0) > 0]
                if len(_gs_puts_liq) >= 1:
                    _gs_puts = _gs_puts_liq.reset_index(drop=True)
            if not _gs_calls.empty and not _gs_puts.empty:
                _gs_call = _gs_calls.loc[(_gs_calls["strike_price"] - spot).abs().idxmin()]
                _gs_put = _gs_puts.loc[(_gs_puts["strike_price"] - spot).abs().idxmin()]
                _gs_gamma = (_gs_call.get("gamma", 0) or 0) + (_gs_put.get("gamma", 0) or 0)
                _gs_theta = (_gs_call.get("theta", 0) or 0) + (_gs_put.get("theta", 0) or 0)
                _gs_d_gamma = _gs_gamma * 100
                _gs_d_theta = _gs_theta * 100
                lines.append(f"  IV/HV Ratio: {_gs_iv_hv:.2f}x {'— CHEAP gamma (buy)' if _gs_iv_hv < 1.0 else ('— EXPENSIVE gamma (sell)' if _gs_iv_hv > 1.3 else '— fair')}")
                if _gs_gamma > 0 and _gs_theta != 0:
                    _gs_be = np.sqrt(2 * abs(_gs_theta) / _gs_gamma)
                    _gs_be_pct = _gs_be / spot * 100
                    _gs_ratio = abs(_gs_d_gamma / _gs_d_theta) if _gs_d_theta != 0 else 0
                    lines.append(f"  Dollar Gamma: ${_gs_d_gamma:.2f}/1% move | Dollar Theta: ${_gs_d_theta:.2f}/day")
                    lines.append(f"  Break-Even Daily Move: ${_gs_be:.2f} ({_gs_be_pct:.1f}%)")
                    lines.append(f"  Gamma/Theta Ratio: {_gs_ratio:.2f}x {'— scalp profitable if realized > implied' if _gs_ratio > 1 else ''}")
                else:
                    lines.append("  [Greeks unavailable for gamma scalp calc]")
            else:
                lines.append(f"  IV/HV: {_gs_iv_hv:.2f}x | [No ATM contracts for Greeks]")
        else:
            lines.append("  [ATM IV or HV20 unavailable]")
    except Exception:
        lines.append("  [Gamma scalp data unavailable]")

    # Surface dislocations
    lines.append(f"\n--- SURFACE DISLOCATIONS (Richest/Cheapest vs Baseline) ---")
    try:
        _disl_rows = []
        for exp in expirations[:6]:
            chain = term_data[exp]
            dte = _dte(exp)
            if dte <= 0:
                continue
            theo_iv = _atm_iv_cache.get(exp, 0)
            if not theo_iv or theo_iv <= 0:
                continue
            for _, row in chain.iterrows():
                iv = row.get("implied_volatility", 0) or 0
                oi = row.get("open_interest", 0) or 0
                if iv > 0 and oi > 0:
                    disl = (iv - theo_iv) * 100
                    _disl_rows.append({"strike": row["strike_price"], "type": row["contract_type"],
                                       "exp": exp, "dte": dte, "iv": iv * 100, "disl": disl, "oi": oi})
        if _disl_rows:
            _disl_sorted = sorted(_disl_rows, key=lambda x: x["disl"])
            lines.append("  CHEAPEST (most underpriced vs baseline):")
            for _d in _disl_sorted[:3]:
                _liq = "HIGH" if _d["oi"] > 5000 else ("MED" if _d["oi"] > 500 else "LOW")
                lines.append(f"    ${_d['strike']:.0f} {_d['type']} {_d['exp']} ({_d['dte']}d): IV={_d['iv']:.1f}% disl={_d['disl']:+.1f}bps OI={_d['oi']:,} ({_liq})")
            lines.append("  RICHEST (most overpriced vs baseline):")
            for _d in _disl_sorted[-3:]:
                _liq = "HIGH" if _d["oi"] > 5000 else ("MED" if _d["oi"] > 500 else "LOW")
                lines.append(f"    ${_d['strike']:.0f} {_d['type']} {_d['exp']} ({_d['dte']}d): IV={_d['iv']:.1f}% disl={_d['disl']:+.1f}bps OI={_d['oi']:,} ({_liq})")
            _avg_disl = sum(d["disl"] for d in _disl_rows) / len(_disl_rows)
            _rich_count = sum(1 for d in _disl_rows if d["disl"] > 2)
            _cheap_count = sum(1 for d in _disl_rows if d["disl"] < -2)
            lines.append(f"  Surface avg dislocation: {_avg_disl:+.1f}bps | {_rich_count} rich / {_cheap_count} cheap contracts")
        else:
            lines.append("  [No dislocation data — baseline IV unavailable]")
    except Exception:
        lines.append("  [Dislocation computation failed]")

    # Earnings date context
    try:
        import yfinance as _yf_earn
        from datetime import datetime as _dt_earn
        _yf_info = _yf_earn.Ticker(ticker_display).info or {}
        _earn_ts = _yf_info.get("earningsTimestampStart")
        if _earn_ts and _earn_ts > 0:
            _ed_date = _dt_earn.utcfromtimestamp(_earn_ts).date()
            _ed_days = (_ed_date - _dt_earn.now().date()).days
            if 0 < _ed_days <= 60:
                lines.append(f"\n--- EARNINGS ---")
                lines.append(f"  Next earnings: {_ed_date.isoformat()} ({_ed_days} days)")
                lines.append(f"  WARNING: Options expiring after this date carry earnings vol premium.")
                for _eexp in expirations[:8]:
                    try:
                        _eexp_date = _dt_earn.strptime(_eexp, "%Y-%m-%d").date()
                        if _eexp_date >= _ed_date:
                            lines.append(f"  {_eexp} expiration INCLUDES earnings risk")
                            break
                    except Exception:
                        continue
    except Exception:
        pass

    # Recent price action
    if len(_returns) >= 5:
        ret_5d = float(px_df["Close"].iloc[-1] / px_df["Close"].iloc[-6] - 1) * 100 if len(px_df) > 5 else 0
        ret_20d = float(px_df["Close"].iloc[-1] / px_df["Close"].iloc[-21] - 1) * 100 if len(px_df) > 20 else 0
        lines.append(f"\n--- Recent Price Action ---")
        lines.append(f"5-Day Return: {ret_5d:+.1f}%")
        lines.append(f"20-Day Return: {ret_20d:+.1f}%")
        recent_realized = float(_returns.tail(5).std() * np.sqrt(252) * 100)
        lines.append(f"5-Day Realized Vol: {recent_realized:.0f}%")
    else:
        lines.append(f"\n--- Recent Price Action ---")
        lines.append("  [Insufficient price history]")

    # Historical surface if available
    try:
        hist = st.session_state.get("vs_hist_surface", {})
        if hist:
            dates = sorted(hist.keys())
            if len(dates) >= 2:
                old_spot = hist[dates[0]].get("spot", 0)
                if old_spot > 0:
                    lines.append(f"\n--- 10-Day Context ---")
                    lines.append(f"Spot {dates[0]}: ${old_spot:.2f} -> Today: ${spot:.2f} ({(spot/old_spot-1)*100:+.1f}%)")
    except Exception:
        pass

    # Cross-page signal composite
    try:
        from src.signal_engine import compute_composite
        comp = compute_composite(ticker_display)
        if comp and comp["n_signals"] >= 2:
            lines.append(f"\n--- CROSS-PAGE SIGNAL COMPOSITE ---")
            lines.append(f"Direction: {comp['overall_direction'].upper()} (conviction {comp['overall_conviction']:.0%})")
            lines.append(f"Vol View: {comp.get('vol_regime', 'N/A')}")
            lines.append(f"Sources: {comp['n_signals']} ({comp['signal_agreement']:.0%} agreement)")
            for s in comp["signals"][:5]:
                lines.append(f"  - {s['source']}: {s['direction']} ({s['conviction']:.0%}) — {s.get('reasoning', '')[:80]}")
    except Exception:
        pass

    # Liquidity map
    lines.append(f"\n--- LIQUIDITY MAP (Open Interest by Strike) ---")
    try:
        front_chain = term_data[expirations[0]]
        _oi_data = front_chain[front_chain["open_interest"] > 0]\
            .groupby("strike_price")["open_interest"].sum()\
            .sort_values(ascending=False).head(15)
        if len(_oi_data) > 0:
            for strike, oi in _oi_data.items():
                liq = "HIGH" if oi > 5000 else ("MEDIUM" if oi > 500 else "LOW")
                lines.append(f"  ${strike:.0f}: OI={oi:,} ({liq})")
        else:
            lines.append("  [No open interest data — exercise caution on fills]")
    except Exception:
        lines.append("  [Liquidity data unavailable — exercise caution on fills]")

    # Iran war context for energy/defense tickers
    _energy_syms = {"USO", "XLE", "XOP", "OIH", "CL", "BZ", "CVX", "XOM", "COP",
                    "GLD", "GC", "SLV", "SI", "UNG", "NG", "LMT", "RTX", "NOC", "GD",
                    "HAL", "MPC", "VLO", "PSX", "OXY", "EOG", "DVN", "FANG",
                    "ITA", "XAR", "PPA", "DFEN", "HII", "LHX", "TDG", "BA"}
    _tk_check = ticker_display.upper().replace("=F", "")
    if _tk_check in _energy_syms:
        try:
            from src.db import get_client
            _db_ctx = get_client()
            if _db_ctx:
                _ca = _db_ctx.table("conflict_analysis").select("situation_summary, escalation_risk")\
                    .eq("region", "iran").order("timestamp", desc=True).limit(1).execute()
                if _ca.data:
                    import json as _jctx
                    _esc = _ca.data[0].get("escalation_risk", {})
                    if isinstance(_esc, str):
                        _esc = _jctx.loads(_esc)
                    _score = _esc.get("score", "?")
                    _level = _esc.get("level", "unknown")
                    _summ = _ca.data[0].get("situation_summary", "")
                    lines.append(f"\n--- CRITICAL: ACTIVE GEOPOLITICAL CONFLICT ---")
                    lines.append(f"Escalation: {_score}/10 ({_level})")
                    lines.append(f"This asset is DIRECTLY affected by the ongoing conflict.")
                    if _summ:
                        lines.append(f"Latest assessment: {_summ[:400]}")
                    else:
                        lines.append("No detailed assessment available.")
        except Exception:
            pass

    return "\n".join(lines)


# Pre-build context once (shared between narrator and trade ideas)
if "vs_surface_context" not in st.session_state or st.session_state.get("vs_context_ticker") != ticker_display:
    st.session_state["vs_surface_context"] = _build_surface_context()
    st.session_state["vs_context_ticker"] = ticker_display


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — 3D IV SURFACE
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("3D Surface"):
        st.markdown(
            f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;">Implied Volatility Surface</div>'
            f'<div style="font-size:0.85rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
            f'Every option on {ticker_display} plotted as a 3D landscape. Height = implied volatility. '
            f'Peaks are expensive options, valleys are cheap ones. The <span style="color:#ffaa00;">yellow line</span> marks the current stock price.</div>',
            unsafe_allow_html=True)

        with st.expander("How to read this surface", expanded=False):
            st.markdown(
                f'<div style="font-size:0.85rem;color:#ccc;line-height:1.6;">'
                f'<b>Rotate</b> (click + drag) to explore. Look for bumps, ridges, and asymmetries.<br><br>'
                f'<b>Left slope (puts)</b> — Steeper = market paying more for downside protection. This is normal for equities. '
                f'A very steep slope signals fear; a flat slope signals complacency.<br><br>'
                f'<b>Ridges at specific expirations</b> — A horizontal bump means the market is pricing an event at that date '
                f'(earnings, FOMC, ex-div). Options before vs after this date will have very different IV levels.<br><br>'
                f'<b>Metric selector</b> — Switch to <b>Delta</b> for directional exposure, <b>Gamma</b> for where dealer hedging concentrates '
                f'(high gamma = potential pin risk), or <b>Vega</b> for vol sensitivity.<br><br>'
                f'<b>Date slider</b> — Scrub backward in time to see how the surface has changed. '
                f'If IV has collapsed over the past week, short vol may be late; if it has expanded, long vol may be expensive.</div>',
                unsafe_allow_html=True)


        # ── Key surface metrics at a glance ──
        if not expirations:
            st.warning("No valid expirations loaded.")
            st.stop()
        front_chain = term_data[expirations[0]]
        front_dte = _dte(expirations[0])
        front_atm_iv = _atm_iv_cache.get(expirations[0], 0.25)
        p25_s, p25_iv = _p25_s, _p25_iv
        c25_s, c25_iv = _find_delta_strike(front_chain, spot, 0.25, "call")

        # Key surface metrics — styled card row
        _card_sm_vs = (f'background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
                       f'border-radius:8px;padding:10px 14px;text-align:center;')
        _vrp_val = front_atm_iv - current_hv20 if current_hv20 and current_hv20 > 0 else None
        _vrp_label = "Rich" if _vrp_val and _vrp_val > 0.03 else ("Cheap" if _vrp_val and _vrp_val < -0.02 else "Fair")
        _vrp_color = "#ff4444" if _vrp_val and _vrp_val > 0.03 else ("#00ff96" if _vrp_val and _vrp_val < -0.02 else "#e6edf3")
        _skew_ratio = p25_iv / front_atm_iv if p25_iv and front_atm_iv > 0 else None
        _skew_color = "#ff4444" if _skew_ratio and _skew_ratio > 1.10 else ("#00ff96" if _skew_ratio and _skew_ratio < 1.02 else "#e6edf3")
        _back_atm_val = _atm_iv(term_data[expirations[-1]], spot, "call") if len(expirations) >= 2 else None
        _ts_shape = "Backwardation" if _back_atm_val and front_atm_iv > _back_atm_val else ("Contango" if _back_atm_val else "N/A")
        _ts_color = "#ff4444" if _ts_shape == "Backwardation" else ("#00ff96" if _ts_shape == "Contango" else "#888")

        # Activity metrics from new fields
        _total_trades = int(front_chain["trade_count"].sum()) if "trade_count" in front_chain.columns else 0
        _total_vol = int(front_chain["volume"].sum()) if "volume" in front_chain.columns else 0
        _avg_trade_size = round(_total_vol / max(_total_trades, 1)) if _total_trades > 0 else 0

        _sm_items = [
            ("SPOT", f"${spot:,.2f}", "#e6edf3", ""),
            (f"ATM IV ({front_dte}d)", f"{front_atm_iv:.1%}", "#e6edf3", "Front-month at-the-money"),
            ("VRP (IV-HV20)", f"{_vrp_val:+.1%}" if _vrp_val is not None else "N/A", _vrp_color, _vrp_label),
            ("PUT SKEW (25\u0394)", f"{_skew_ratio:.2f}x" if _skew_ratio else "N/A", _skew_color,
             "Fear premium" if _skew_ratio and _skew_ratio > 1.10 else ("Complacent" if _skew_ratio and _skew_ratio < 1.02 else "")),
            ("TERM STRUCTURE", _ts_shape, _ts_color,
             f"{(front_atm_iv - _back_atm_val):+.1%}" if _back_atm_val else ""),
            ("TRADES", f"{_total_trades:,}" if _total_trades > 0 else "N/A", "#e6edf3",
             f"Avg {_avg_trade_size} contracts" if _avg_trade_size > 0 else ""),
        ]
        _sm_html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;">'
        for _sl, _sv, _sc, _ssub in _sm_items:
            _sub_html = f'<div style="font-size:0.65rem;color:{_sc};">{_ssub}</div>' if _ssub else ""
            _sm_html += (f'<div style="{_card_sm_vs}flex:1;min-width:100px;">'
                         f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{_sl}</div>'
                         f'<div style="font-size:1.1rem;font-weight:700;color:{_sc};">{_sv}</div>'
                         f'{_sub_html}</div>')
        _sm_html += '</div>'
        st.markdown(_sm_html, unsafe_allow_html=True)

        metric_sel = st.selectbox("Surface metric", ["IV", "Delta", "Gamma", "Vega"], key="vs_metric")

        # Date slider — scrub through historical snapshots
        _snaps_for_slider = _load_surface_snapshots(ticker_display, n_days=30)
        _snap_dates = [s["date"] for s in _snaps_for_slider]

        if _snap_dates:
            _snap_dates_display = _snap_dates + ["Today"]
            _date_idx = st.slider(
                "Surface Date", 0, len(_snap_dates_display) - 1,
                value=len(_snap_dates_display) - 1,
                format="",
                key="vs_date_slider",
            )
            _selected_date = _snap_dates_display[_date_idx]
            if _selected_date != "Today":
                _snap_for_label = next((s for s in _snaps_for_slider if s["date"] == _selected_date), None)
                st.caption(f"Viewing **{_selected_date}** — Spot: ${_snap_for_label['spot']:,.2f}" if _snap_for_label else "")
            else:
                st.caption("Viewing **live** surface data")
        else:
            _selected_date = "Today"

        field_map = {"IV": "implied_volatility", "Delta": "delta", "Gamma": "gamma", "Vega": "vega"}
        field = field_map[metric_sel]

        # If historical date selected, build surface from snapshot
        _using_snapshot = _selected_date != "Today" and _snaps_for_slider
        _snap_surface_rows = None
        _sel_snap = None

        if _using_snapshot:
            _sel_snap = next((s for s in _snaps_for_slider if s["date"] == _selected_date), None)
            if _sel_snap:
                _snap_spot = _sel_snap["spot"]
                _snap_surface_rows = []
                for r in _sel_snap["data"]:
                    k = r.get("strike", 0)
                    dte = r.get("dte", 0)
                    iv = r.get("iv", 0)
                    delta = r.get("delta", 0)
                    gamma = r.get("gamma", 0)
                    vega = gamma * _snap_spot * 0.01 if gamma else 0  # approximate vega
                    val_map = {"implied_volatility": iv, "delta": abs(delta) if delta else 0, "gamma": gamma, "vega": vega}
                    val = val_map.get(field, iv)
                    if val != 0 and dte > 0 and 0.70 * _snap_spot <= k <= 1.30 * _snap_spot:
                        _snap_surface_rows.append({
                            "strike": k, "expiration": r.get("exp", ""), "dte": dte, field: val,
                        })

        # Build surface grid
        strike_lo = spot * 0.70
        strike_hi = spot * 1.30
        surface_rows = []

        if _snap_surface_rows is not None:
            surface_rows = _snap_surface_rows
        else:
            for exp in expirations:
                chain = term_data[exp]
                dte = _dte(exp)
                # Use OTM options for each side: puts below ATM, calls at/above ATM
                # Meet at spot (no gap) — both sides include ATM for smooth interpolation
                calls = chain[(chain["contract_type"] == "call") & (chain["strike_price"] >= spot)]
                puts = chain[(chain["contract_type"] == "put") & (chain["strike_price"] <= spot)]
                for _, row in pd.concat([calls, puts]).iterrows():
                    k = row["strike_price"]
                    val = row.get(field, 0) or 0
                    iv = row.get("implied_volatility", 0) or 0
                    # For delta: use absolute value so surface is continuous (no sign flip at ATM)
                    if field == "delta":
                        val = abs(val) if val else 0
                    if strike_lo <= k <= strike_hi and val != 0 and iv > 0:
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

            # Spot price line (use snapshot spot if viewing historical)
            _display_spot = _sel_snap["spot"] if _sel_snap else spot
            spot_col = (np.abs(np.array(pivot.columns) - _display_spot)).argmin()
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
                uirevision="main_surface",  # preserves camera position
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

            # ── Surface Analysis: What does this mean for your trading? ──
            st.markdown(f'<div style="font-size:1rem;font-weight:700;color:#e6edf3;margin-top:16px;margin-bottom:8px;">Surface Reading</div>',
                        unsafe_allow_html=True)

            _readings = []
            # VRP reading
            if _vrp_val is not None:
                if _vrp_val > 0.05:
                    _readings.append(("VRP is significantly rich", f"Options are pricing {abs(_vrp_val)*100:.0f}% more vol than realized. Premium sellers have an edge — consider selling straddles, strangles, or iron condors.", "#ff4444"))
                elif _vrp_val > 0.02:
                    _readings.append(("VRP is mildly rich", f"IV exceeds realized by {abs(_vrp_val)*100:.1f}%. Slight edge for premium sellers, but not extreme. Credit spreads preferred over naked short vol.", "#ffaa00"))
                elif _vrp_val < -0.05:
                    _readings.append(("VRP is deeply cheap", f"Realized vol exceeds IV by {abs(_vrp_val)*100:.0f}%. Options are underpriced. Long vol via straddles or ratio backspreads is attractive. Gamma buyers have the edge.", "#00ff96"))
                elif _vrp_val < -0.02:
                    _readings.append(("VRP is cheap", f"IV is below realized by {abs(_vrp_val)*100:.1f}%. Gamma is cheap to own — consider buying protection or long straddles.", "#00ff96"))
                else:
                    _readings.append(("VRP is fair", "No clear edge from the variance risk premium alone. Look to skew and term structure for trade ideas.", "#e6edf3"))

            # Skew reading
            if _skew_ratio is not None:
                if _skew_ratio > 1.15:
                    _readings.append(("Put skew is steep", f"25\u0394 puts trade at {_skew_ratio:.2f}x ATM — the market is paying a heavy fear premium. Consider put spreads (buy ATM, sell OTM) to capture skew, or sell the expensive wing.", "#ff4444"))
                elif _skew_ratio > 1.05:
                    _readings.append(("Put skew is normal", f"25\u0394 put skew at {_skew_ratio:.2f}x is typical. No unusual fear or complacency in the skew.", "#e6edf3"))
                elif _skew_ratio < 1.02:
                    _readings.append(("Put skew is flat", f"25\u0394 puts at only {_skew_ratio:.2f}x ATM — the market is complacent about downside risk. Cheap to buy puts or put spreads here.", "#00ff96"))

            # Term structure reading
            if _back_atm_val and front_atm_iv > 0:
                if _ts_shape == "Backwardation":
                    _spread = (front_atm_iv - _back_atm_val) * 100
                    _readings.append(("Term structure is inverted", f"Front IV is {_spread:.1f}% above back months — the market is bracing for a near-term event. Calendar spreads (sell front, buy back) capture this premium. Avoid selling front-month naked.", "#ff4444"))
                else:
                    _spread = (_back_atm_val - front_atm_iv) * 100
                    if _spread > 5:
                        _readings.append(("Steep contango", f"Back months are {_spread:.1f}% above front. No near-term event premium. Front-month credit spreads are relatively safe. LEAPS are expensive.", "#e6edf3"))
                    else:
                        _readings.append(("Normal contango", "Term structure is gently upward-sloping — normal market conditions. No unusual event pricing visible.", "#e6edf3"))

            if _readings:
                _read_html = ""
                for _rtitle, _rdesc, _rcolor in _readings:
                    _read_html += (
                        f'<div style="padding:10px 14px;margin-bottom:8px;border-left:3px solid {_rcolor};'
                        f'background:{COLORS["card_bg"]};border-radius:0 8px 8px 0;">'
                        f'<div style="font-size:0.85rem;font-weight:700;color:{_rcolor};">{_rtitle}</div>'
                        f'<div style="font-size:0.82rem;color:#ccc;margin-top:2px;">{_rdesc}</div></div>')
                st.markdown(_read_html, unsafe_allow_html=True)

            # ── Higher-Order Greeks Summary ──
            st.divider()
            st.markdown(f'<div style="font-size:1rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">Higher-Order Greeks Snapshot</div>'
                        f'<div style="font-size:0.72rem;color:{COLORS["text_muted"]};margin-bottom:8px;">'
                        f'ATM front-month vanna, charm, speed from the loaded surface.</div>',
                        unsafe_allow_html=True)
            try:
                from src.options_models import bs_higher_greeks as _hg_fn
                _hg_exp0 = expirations[0]
                _hg_dte = max((pd.to_datetime(_hg_exp0) - pd.Timestamp.now()).days, 1)
                _hg_T = _hg_dte / 365
                _hg_atm_iv = _atm_iv_cache.get(_hg_exp0, 0.25)
                _hg_rfr = 0.045
                _hg = _hg_fn(spot, spot, _hg_T, _hg_rfr, _hg_atm_iv, "call")

                _hg_items = [
                    ("VANNA", f"{_hg['vanna']:.5f}", COLORS["warning"],
                     f"{'Delta drops if IV rises' if _hg['vanna'] < 0 else 'Delta rises with IV'}"),
                    ("CHARM", f"{_hg['charm']:.5f}/d", COLORS["warning"],
                     f"Delta drifts {_hg['charm']:+.5f} overnight"),
                    ("SPEED", f"{_hg['speed']:.7f}", COLORS["danger"],
                     f"{'High pin risk' if abs(_hg['speed']) > 0.001 else 'Normal'}"),
                    ("ZOMMA", f"{_hg['zomma']:.5f}", COLORS["danger"],
                     f"{'Gamma spikes if IV rises' if _hg['zomma'] > 0 else 'Gamma drops with IV'}"),
                ]
                _hg_html = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:8px;">'
                for _hl, _hv, _hc, _hd in _hg_items:
                    _hg_html += (f'<div style="background:{COLORS["card_bg"]};border:1px solid {_hc};'
                                 f'border-radius:8px;padding:8px 12px;flex:1;min-width:120px;text-align:center;">'
                                 f'<div style="font-size:0.55rem;color:{COLORS["text_muted"]};text-transform:uppercase;letter-spacing:0.5px;">{_hl}</div>'
                                 f'<div style="font-size:0.95rem;font-weight:700;color:{_hc};">{_hv}</div>'
                                 f'<div style="font-size:0.6rem;color:{COLORS["text_muted"]};">{_hd}</div></div>')
                _hg_html += '</div>'
                st.markdown(_hg_html, unsafe_allow_html=True)

                # Deep dive link
                st.markdown(f'<a href="/49_Higher_Greeks?ticker={ticker_display}" target="_self" '
                            f'style="font-size:0.8rem;color:{COLORS["accent"]};text-decoration:none;">'
                            f'Deep Dive: Full Higher-Order Greeks Analysis \u2192</a>',
                            unsafe_allow_html=True)
            except Exception:
                pass

            # ── AI Surface Narrator ──
            st.divider()
            st.markdown(f'<div style="font-size:1rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">AI Surface Analysis</div>'
                        f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:10px;">'
                        f'Gemini reads the full surface data and explains what it means in plain English.</div>',
                        unsafe_allow_html=True)

            @st.fragment
            def _surface_narrator():
                _narrator_key = f"vs_narrator_{ticker_display}"
                if st.button("Analyze Surface", key="vs_narrate_btn", use_container_width=True):
                    from src.api_keys import get_secret as _gk
                    _nar_key = _gk("GEMINI_API_KEY")
                    if not _nar_key:
                        st.error("Gemini API key not configured.")
                        return

                    # Use pre-built context (no redundant API calls)
                    _nar_context = st.session_state.get("vs_surface_context", "")

                    from src.ai_validation import ACCURACY_CHECK_LIGHT, VOL_SURFACE_EXPERT_CONTEXT
                    _nar_prompt = (
                        "You are a veteran options trading mentor explaining a volatility surface to a trader "
                        "who understands options but wants to know what THIS specific surface is telling them right now.\n\n"
                        f"{VOL_SURFACE_EXPERT_CONTEXT}\n\n"
                        "Below is the full surface profile data for this ticker. Write a clear, structured analysis "
                        "that a trader can act on. NO trade recommendations — just describe what you see.\n\n"
                        "Structure your response EXACTLY like this:\n\n"
                        "## What the Surface is Telling You\n"
                        "2-3 sentences: the single most important thing about this surface right now. "
                        "Is vol cheap or expensive? Is the market bracing for something? Is there complacency?\n\n"
                        "## Skew Story\n"
                        "What does the put/call skew reveal about market positioning? Is downside protection expensive or cheap? "
                        "Are calls bid (unusual demand)? Reference the butterfly and risk reversal numbers if available.\n\n"
                        "## Term Structure Story\n"
                        "What does the shape of IV across expirations reveal? Is there an event kink? "
                        "Is the market pricing near-term risk differently from long-term? Which expirations stand out?\n\n"
                        "## Where the Dislocations Are\n"
                        "Which specific contracts are rich or cheap vs the surface? Are there pockets of mispricing "
                        "the trader should know about? Reference the dislocation data if available.\n\n"
                        "## The Gamma Picture\n"
                        "Is gamma cheap or expensive to own? What does the IV/HV ratio say about whether to buy or sell vol? "
                        "What daily move does the stock need to make for a long straddle to break even?\n\n"
                        "## Key Risks on This Surface\n"
                        "What could change this surface overnight? Earnings, FOMC, geopolitics? "
                        "What would make the current vol regime snap?\n\n"
                        "RULES:\n"
                        "- Write for a trader, not an academic. Be direct.\n"
                        "- Reference specific numbers from the data (IV levels, percentiles, ratios).\n"
                        "- Do NOT suggest trades — that is for the Trade Ideas tab.\n"
                        "- If a section has [unavailable] data, say so briefly and move on.\n"
                        "- Keep each section to 2-4 sentences. Total response under 500 words.\n\n"
                        f"SURFACE DATA:\n{_nar_context}\n\n"
                        f"{ACCURACY_CHECK_LIGHT}"
                    )

                    with fun_loader("ai"):
                        try:
                            from google import genai
                            from google.genai import types
                            client = genai.Client(api_key=_nar_key)
                            response = client.models.generate_content(
                                model="gemini-3.1-pro-preview",
                                contents=_nar_prompt,
                                config=types.GenerateContentConfig(max_output_tokens=3000, temperature=0.3),
                            )
                            st.session_state[_narrator_key] = response.text
                        except Exception as e:
                            st.error(f"Analysis failed: {e}")

                if _narrator_key in st.session_state:
                    _nar_text = st.session_state[_narrator_key].replace("$", "\\$")
                    st.markdown(
                        f'<div style="border:1px solid {COLORS["card_border"]};border-radius:10px;'
                        f'padding:16px 20px;background:{COLORS["card_bg"]};margin-top:8px;">'
                        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
                        f'<div style="border:1px solid {COLORS["accent"]};border-radius:4px;padding:2px 8px;'
                        f'font-size:0.7rem;color:{COLORS["accent"]};font-weight:600;">GEMINI 3.1 PRO</div>'
                        f'<span style="font-size:0.75rem;color:{COLORS["text_muted"]};">Surface Narrator</span></div>'
                        f'</div>', unsafe_allow_html=True)
                    st.markdown(_nar_text)

            _surface_narrator()

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
                    text=np.round(pivot_disl.values, 0).astype(int),
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
                                     vertical_spacing=0.22, horizontal_spacing=0.12)

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
                plot_bgcolor="rgba(0,0,0,0)", height=600,
                showlegend=False,
                margin=dict(l=60, r=20, t=50, b=50),
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
        st.markdown(f'<div style="font-size:1.15rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">IV Surface \u2014 Daily Replay</div>'
                    f'<div style="font-size:0.8rem;color:{COLORS["text_muted"]};margin-bottom:12px;">'
                    f'Scrub through the last 10 days of historical IV surfaces built from Polygon options data.</div>',
                    unsafe_allow_html=True)

        _anim_days = st.slider("Days of history", 5, 30, 10, key="vs_anim_days_sel")

        # Primary: fetch historical options prices from Polygon + compute IV
        # Fallback: pre-saved snapshots from Supabase
        _anim_key = f"vs_anim_hist_{ticker_display}_{_anim_days}"
        if _anim_key not in st.session_state:
            with fun_loader("data"):
                try:
                    _hist_raw = fetch_options_surface_history(ticker_display, days=_anim_days, max_contracts=150)
                except Exception as _he:
                    logger.warning(f"Options history fetch failed: {_he}")
                    _hist_raw = {}

                if _hist_raw and len(_hist_raw) >= 2:
                    # Compute IV from close prices for each day
                    from src.options_models import implied_vol as _iv_solver
                    _hist_snaps = []
                    for _dt, _day_data in sorted(_hist_raw.items()):
                        _day_spot = _day_data["spot"]
                        _rfr_anim = 0.045
                        _iv_rows = []
                        for _c in _day_data["data"]:
                            _K = _c["strike"]
                            _dte_c = _c["dte"]
                            _T_c = _dte_c / 365
                            _price = _c["close"]
                            _otype = _c["type"]
                            if _price <= 0 or _T_c <= 0 or _K <= 0:
                                continue
                            _computed_iv = _iv_solver(_price, _day_spot, _K, _T_c, _rfr_anim, _otype)
                            if 0.01 < _computed_iv < 3.0:
                                _iv_rows.append({
                                    "strike": _K, "dte": _dte_c, "iv": _computed_iv,
                                    "type": _otype, "exp": _c.get("exp", ""),
                                })
                        if len(_iv_rows) >= 15:
                            _hist_snaps.append({"date": _dt, "spot": _day_spot, "data": _iv_rows})
                    st.session_state[_anim_key] = _hist_snaps
                else:
                    # Fallback to saved snapshots
                    _fallback = _load_surface_snapshots(ticker_display, n_days=_anim_days)
                    st.session_state[_anim_key] = _fallback

        cached_snapshots = st.session_state.get(_anim_key, [])
        n_snaps = len(cached_snapshots)

        if n_snaps < 2:
            st.info(f"**{n_snaps} day{'s' if n_snaps != 1 else ''} of data available.** "
                    "Polygon Options Starter provides daily close prices. Try increasing the days slider or check API key.")
        else:
            st.caption(f"**{n_snaps} trading days** of historical IV surface data")
            from scipy.interpolate import griddata as _gd
            try:
                from scipy.ndimage import gaussian_filter as _gf
            except ImportError:
                _gf = None

            GRID_M, GRID_D = 40, 20
            m_range = np.linspace(0.82, 1.18, GRID_M)

            # Pre-compute all frames
            all_z_min, all_z_max = np.inf, -np.inf
            frames = []
            frame_labels = []

            for snap in cached_snapshots:
                day_spot = snap["spot"]
                day_date = snap["date"]
                pts_m, pts_d, pts_iv = [], [], []
                for r in snap["data"]:
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
                frame_labels.append(f"{day_date} \u2014 ${day_spot:,.2f}")
                frames.append({"x": strike_grid, "y": d_range.tolist(), "z": z.tolist(), "spot": day_spot})

            if frames:
                st.session_state["vs_hist_frames"] = frames
                st.session_state["vs_hist_labels"] = frame_labels

            if len(frames) < 2:
                st.warning("Not enough days with valid data.")
            else:
                # Store precomputed data for the fragment
                st.session_state["vs_anim_frames"] = frames
                st.session_state["vs_anim_labels"] = frame_labels
                st.session_state["vs_anim_zrange"] = (all_z_min, all_z_max)

                @st.fragment
                def _anim_viewer():
                    _frames = st.session_state.get("vs_anim_frames", [])
                    _labels = st.session_state.get("vs_anim_labels", [])
                    _zmin, _zmax = st.session_state.get("vs_anim_zrange", (0, 1))
                    if not _frames:
                        return

                    _sc1, _sc2 = st.columns([3, 1])
                    with _sc1:
                        _day_idx = st.slider("Day", 0, len(_frames) - 1, len(_frames) - 1,
                                              format="", key="vs_anim_day")
                    with _sc2:
                        _view_mode = st.radio("View", ["Heatmap", "3D"], horizontal=True, key="vs_anim_view")

                    _sel = _frames[_day_idx]
                    _lbl = _labels[_day_idx]
                    st.markdown(f'<div style="text-align:center;font-size:0.95rem;font-weight:700;color:{COLORS["accent"]};margin-bottom:8px;">'
                                f'{_lbl}</div>', unsafe_allow_html=True)

                    cs = [[0, "#0d0887"], [0.15, "#3b049a"], [0.3, "#7201a8"],
                          [0.45, "#ae2891"], [0.6, "#ed6f45"], [0.8, "#fba238"], [1.0, "#f0f921"]]

                    if _view_mode == "Heatmap":
                        # 2D heatmap — no camera reset on slider interaction
                        fig_a = go.Figure(go.Heatmap(
                            x=_sel["x"], y=_sel["y"], z=_sel["z"],
                            colorscale=cs, zmin=_zmin, zmax=_zmax,
                            colorbar=dict(title="IV", tickformat=".0%", len=0.6, thickness=12),
                            hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>IV: %{z:.1%}<extra></extra>",
                        ))
                        fig_a.add_vline(x=_sel["spot"], line_dash="dash", line_color="#ffaa00",
                                        annotation_text=f"Spot ${_sel['spot']:,.0f}")
                        fig_a.update_layout(
                            template="plotly_dark", height=500,
                            margin=dict(t=10, b=30, l=0, r=0),
                            xaxis_title="Strike ($)", yaxis_title="DTE",
                        )
                    else:
                        # 3D surface — camera resets on slider change (Streamlit limitation)
                        _zpad = (_zmax - _zmin) * 0.05
                        fig_a = go.Figure()
                        fig_a.add_trace(go.Surface(
                            x=_sel["x"], y=_sel["y"], z=_sel["z"],
                            colorscale=cs, cmin=_zmin, cmax=_zmax,
                            colorbar=dict(title="IV", tickformat=".0%", len=0.5, thickness=10),
                            lighting=dict(ambient=0.5, diffuse=0.6, specular=0.35, roughness=0.4),
                            opacity=0.95,
                            hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>IV: %{z:.1%}<extra></extra>",
                        ))
                        _s = _sel["spot"]
                        _xa = np.array(_sel["x"])
                        _za = np.array(_sel["z"])
                        _ci = np.argmin(np.abs(_xa - _s))
                        fig_a.add_trace(go.Scatter3d(
                            x=[_s] * len(_sel["y"]), y=_sel["y"], z=_za[:, _ci].tolist(),
                            mode="lines", line=dict(color="#ffaa00", width=5), showlegend=False))
                        fig_a.update_layout(
                            template="plotly_dark", height=600,
                            margin=dict(t=10, b=10, l=10, r=10),
                            scene=dict(
                                xaxis=dict(title="Strike ($)", backgroundcolor="rgba(14,17,23,0.95)", gridcolor="rgba(48,54,61,0.3)"),
                                yaxis=dict(title="DTE", backgroundcolor="rgba(14,17,23,0.95)", gridcolor="rgba(48,54,61,0.3)"),
                                zaxis=dict(title="IV", tickformat=".0%", range=[_zmin - _zpad, _zmax + _zpad],
                                           backgroundcolor="rgba(14,17,23,0.95)", gridcolor="rgba(48,54,61,0.3)"),
                                camera=dict(eye=dict(x=1.5, y=-1.5, z=0.8)),
                                aspectratio=dict(x=1.5, y=1.0, z=0.55),
                            ),
                        )
                    st.plotly_chart(fig_a, use_container_width=True, config=PLOTLY_NOBAR)

                    st.divider()
                    _cmp_to = st.radio("Compare to", ["Previous Day", "First Day"], horizontal=True, key="vs_anim_cmp")
                    _ci2 = max(0, _day_idx - 1) if _cmp_to == "Previous Day" else 0
                    _cf = _frames[_ci2]
                    _cl = _labels[_ci2]

                    _mc1, _mc2, _mc3 = st.columns(3)
                    _mc1.metric("Spot", f"${_sel['spot']:.2f}",
                                f"{(_sel['spot']/_cf['spot']-1)*100:+.1f}%")
                    _sz = np.array(_sel["z"])
                    _cz = np.array(_cf["z"])
                    _mc2.metric("Avg IV", f"{np.nanmean(_sz):.1%}",
                                f"{np.nanmean(_sz) - np.nanmean(_cz):+.1%}")
                    _mc3.metric("vs", _cl.split(" \u2014")[0])

                    if _sz.shape == _cz.shape and _day_idx != _ci2:
                        _dz = _sz - _cz
                        fig_d = go.Figure(go.Heatmap(
                            x=_sel["x"], y=_sel["y"], z=_dz,
                            colorscale=[[0, "#1e3a5f"], [0.35, "#4393c3"], [0.5, "#1c1f26"],
                                        [0.65, "#d6604d"], [1.0, "#b2182b"]],
                            zmid=0,
                            colorbar=dict(title="\u0394IV", tickformat=".1%", thickness=12),
                            hovertemplate="Strike: $%{x:.0f}<br>DTE: %{y:.0f}d<br>\u0394IV: %{z:+.2%}<extra></extra>",
                        ))
                        fig_d.update_layout(
                            template="plotly_dark", height=300, margin=dict(t=30, b=0, l=0, r=0),
                            title=f"IV Change: {_cl.split(' \u2014')[0]} \u2192 {_lbl.split(' \u2014')[0]}",
                            xaxis_title="Strike ($)", yaxis_title="DTE",
                        )
                        st.plotly_chart(fig_d, use_container_width=True, config=PLOTLY_NOBAR)

                _anim_viewer()

            # ── AI Surface Evolution Analysis (outside fragment — button triggers full rerun which is fine) ──
            if len(frames) >= 2:
                st.divider()
                st.markdown(f'<div style="font-size:1rem;font-weight:700;color:#e6edf3;margin-bottom:4px;">AI Surface Evolution Analysis</div>'
                            f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};margin-bottom:8px;">'
                            f'Analyzes how the vol surface changed over the loaded period and suggests trades.</div>',
                            unsafe_allow_html=True)

                @st.fragment
                def _anim_ai():
                    if st.button("Analyze Surface Changes", key="vs_anim_ai_btn", use_container_width=True):
                        from src.api_keys import get_secret as _gk_anim
                        _ak = _gk_anim("GEMINI_API_KEY")
                        if not _ak:
                            st.error("Gemini API key not configured.")
                            return

                        from src.ai_validation import ACCURACY_CHECK_LIGHT, HIGHER_GREEKS_EXPERT_CONTEXT
                        _fr = st.session_state.get("vs_anim_frames", [])
                        _lb = st.session_state.get("vs_anim_labels", [])
                        if len(_fr) < 2:
                            st.warning("Need at least 2 days of data.")
                            return

                        # Build context: first day, last day, key changes
                        _first = _fr[0]
                        _last = _fr[-1]
                        _fz = np.array(_first["z"])
                        _lz = np.array(_last["z"])

                        _ctx_lines = [
                            f"SURFACE EVOLUTION ANALYSIS: {ticker_display}",
                            f"Period: {_lb[0].split(' —')[0] if _lb else '?'} to {_lb[-1].split(' —')[0] if _lb else '?'} ({len(_fr)} trading days)",
                            "",
                            f"FIRST DAY: Spot ${_first['spot']:,.2f} | Avg IV {np.nanmean(_fz):.1%}",
                            f"LAST DAY:  Spot ${_last['spot']:,.2f} | Avg IV {np.nanmean(_lz):.1%}",
                            f"SPOT CHANGE: {(_last['spot']/_first['spot']-1)*100:+.1f}%",
                            f"AVG IV CHANGE: {np.nanmean(_lz) - np.nanmean(_fz):+.1%}",
                            "",
                        ]

                        # Per-day summary
                        _ctx_lines.append("--- DAY-BY-DAY ---")
                        for i, (_f, _l) in enumerate(zip(_fr, _lb)):
                            _z = np.array(_f["z"])
                            _prev_z = np.array(_fr[i-1]["z"]) if i > 0 else _z
                            _chg = np.nanmean(_z) - np.nanmean(_prev_z) if i > 0 else 0
                            _ctx_lines.append(f"  {_l}: Avg IV {np.nanmean(_z):.1%} (d/d {_chg:+.1%})")

                        # Surface shape changes
                        if _fz.shape == _lz.shape:
                            _diff = _lz - _fz
                            _ctx_lines.append("")
                            _ctx_lines.append("--- SURFACE SHAPE CHANGES ---")
                            _ctx_lines.append(f"Max IV increase: {np.nanmax(_diff):+.1%}")
                            _ctx_lines.append(f"Max IV decrease: {np.nanmin(_diff):+.1%}")
                            _ctx_lines.append(f"Avg change: {np.nanmean(_diff):+.1%}")
                            # Where did IV change most? Front vs back
                            _front_chg = np.nanmean(_diff[:len(_diff)//3])
                            _back_chg = np.nanmean(_diff[2*len(_diff)//3:])
                            _ctx_lines.append(f"Front-month IV change: {_front_chg:+.1%}")
                            _ctx_lines.append(f"Back-month IV change: {_back_chg:+.1%}")
                            _ts_shift = "Flattened (front rose more)" if _front_chg > _back_chg + 0.005 else ("Steepened (back rose more)" if _back_chg > _front_chg + 0.005 else "Parallel shift")
                            _ctx_lines.append(f"Term structure: {_ts_shift}")
                            # Left vs right (puts vs calls)
                            _put_chg = np.nanmean(_diff[:, :len(_diff[0])//3])
                            _call_chg = np.nanmean(_diff[:, 2*len(_diff[0])//3:])
                            _skew_shift = "Skew steepened (puts rose more)" if _put_chg > _call_chg + 0.005 else ("Skew flattened (calls rose more)" if _call_chg > _put_chg + 0.005 else "Symmetric shift")
                            _ctx_lines.append(f"Skew: {_skew_shift}")

                        # Add cross-page context
                        try:
                            ctx_text = st.session_state.get("vs_surface_context", "")
                            if ctx_text:
                                _ctx_lines.append(f"\n--- CURRENT SURFACE METRICS ---\n{ctx_text[:1500]}")
                        except Exception:
                            pass

                        _ctx = "\n".join(_ctx_lines)
                        _prompt = (
                            "You are a senior volatility trader analyzing how an options surface evolved over the past trading week.\n\n"
                            f"{HIGHER_GREEKS_EXPERT_CONTEXT}\n\n"
                            "Read the surface evolution data and provide actionable analysis:\n\n"
                            "## What Changed\n"
                            "The most important surface change over this period. Did vol expand or contract? "
                            "Did the term structure steepen or flatten? Did skew shift? Reference specific numbers.\n\n"
                            "## Why It Changed\n"
                            "Based on the spot move and vol dynamics, what likely drove the surface shift? "
                            "Was it an event (earnings, macro)? Dealer repositioning? Mean reversion? Vanna/charm flows?\n\n"
                            "## What It Means for Positioning\n"
                            "Given how the surface evolved, is the current regime favoring long or short vol? "
                            "Is skew rich or cheap relative to the move? Is term structure pricing an event correctly?\n\n"
                            "## Trade Ideas\n"
                            "3-5 specific trades that exploit the surface evolution. For each: strategy, strikes, expiration, rationale. "
                            "Include at least one trade that fades the recent move and one that rides the trend.\n\n"
                            "RULES: Reference specific numbers from the data. Be direct. Each section 2-4 sentences. Under 500 words.\n\n"
                            f"DATA:\n{_ctx}\n\n{ACCURACY_CHECK_LIGHT}"
                        )

                        with fun_loader("ai"):
                            try:
                                from google import genai
                                from google.genai import types
                                client = genai.Client(api_key=_ak)
                                resp = client.models.generate_content(
                                    model="gemini-3.1-pro-preview", contents=_prompt,
                                    config=types.GenerateContentConfig(max_output_tokens=4000, temperature=0.3))
                                st.session_state["vs_anim_ai_result"] = resp.text
                            except Exception as e:
                                st.error(f"Analysis failed: {e}")

                    if "vs_anim_ai_result" in st.session_state:
                        st.markdown(
                            f'<div style="display:flex;align-items:center;gap:8px;margin:10px 0;">'
                            f'<div style="border:1px solid {COLORS["accent"]};border-radius:4px;padding:2px 8px;'
                            f'font-size:0.7rem;color:{COLORS["accent"]};font-weight:600;">GEMINI 3.1 PRO</div>'
                            f'<span style="font-size:0.75rem;color:{COLORS["text_muted"]};">Surface Evolution Analyst</span></div>',
                            unsafe_allow_html=True)
                        st.markdown(st.session_state["vs_anim_ai_result"].replace("$", "\\$"))
                        st.caption("AI-generated. Not financial advice.")

                _anim_ai()


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
        st.subheader("AI Trade Suggestions — Gemini 3.1 Pro")
        st.caption(
            "Sends the full vol surface profile to Google's top Gemini model for "
            "structured trade ideas based on skew, term structure, dislocations, and regime."
        )

        from src.api_keys import get_secret as _get_key_fn
        gemini_key = _get_key_fn("GEMINI_API_KEY")

        if not gemini_key:
            st.error("Gemini API key not configured. Add GEMINI_API_KEY to your secrets.")
        else:
            # #7: Clear stale results when ticker changes
            if st.session_state.get("vs_gemini_ticker") != ticker_display:
                for _k in ["vs_gemini_result", "vs_gemini_style_used", "vs_gemini_context",
                            "vs_gemini_conversation", "vs_gemini_ticker"]:
                    st.session_state.pop(_k, None)
                st.session_state["vs_gemini_ticker"] = ticker_display

            # Controls row: style + account size + cost
            _gc1, _gc2 = st.columns([3, 1])
            with _gc1:
                idea_style = st.radio("Analysis focus", [
                    "Full Surface Scan",
                    "Income / Theta Plays",
                    "Directional Bets",
                    "Volatility Trades",
                    "Hedging Strategies",
                ], horizontal=True, key="vs_gemini_style")
            with _gc2:
                # #3: Position sizing context
                _acct_size = st.number_input("Account size ($)", min_value=0, value=0, step=10000,
                                             key="vs_acct_size", help="Optional. Enables position sizing in trade ideas.")

            style_prompts = {
                "Full Surface Scan": "Analyze the full surface and suggest 3-5 trades across categories (income, directional, vol, hedging). For each, specify exact strikes, expirations, and expected P&L.",
                "Income / Theta Plays": "Focus on theta-positive income strategies. Look for overpriced options to sell. Suggest covered calls, credit spreads, iron condors, or naked puts with exact strikes and expirations.",
                "Directional Bets": "Focus on directional plays. Analyze the skew and term structure for bullish or bearish setups. Suggest debit spreads, ratio spreads, or outright option buys with exact parameters.",
                "Volatility Trades": "Focus on pure volatility trades. Analyze the variance risk premium, term structure shape, and skew for straddles, strangles, calendars, or vol spread trades. Specify entries and exits.",
                "Hedging Strategies": "Focus on portfolio hedging. Suggest the most cost-effective downside protection: put spreads, collars, or tail-risk hedges. Optimize cost vs coverage.",
            }

            # #6: Data freshness indicator
            _chain_age_min = 0
            try:
                _now = datetime.now()
                _load_ts = st.session_state.get("vs_chain_loaded_at")
                if not _load_ts:
                    st.session_state["vs_chain_loaded_at"] = _now
                    _load_ts = _now
                _chain_age_min = (_now - _load_ts).total_seconds() / 60
                if _chain_age_min > 30 and _now.weekday() < 5 and 9 <= _now.hour <= 16:
                    st.warning(f"Options data is {_chain_age_min:.0f} min old. Prices may have moved. Consider refreshing the page.")
                elif _chain_age_min > 5:
                    st.caption(f"Options data loaded {_chain_age_min:.0f} min ago")
            except Exception:
                pass

            # #8: Cost estimate
            st.caption(f"Estimated cost: ~\\$0.05 per generation")

            # Build the system prompt (shared between generate and refine)
            def _build_system_prompt(context, style, acct_size=0, refine_msg=None):
                from src.ai_validation import ACCURACY_CHECK as _acc, VOL_SURFACE_EXPERT_CONTEXT as _vol_expert
                _acct_str = ""
                if acct_size and acct_size > 0:
                    _acct_str = (f"\nACCOUNT SIZE: ${acct_size:,.0f}. For each trade, include a POSITION SIZING section: "
                                 f"recommended number of contracts, total max risk in dollars, and percentage of account at risk. "
                                 f"Target max 2-5% of account per trade.\n")

                base = (
                    "You are a senior options market maker and volatility strategist at a top-tier institutional desk. "
                    "You have deep expertise in SABR surface modeling, Vanna-Volga pricing, higher-order Greek management, "
                    "and systematic volatility arbitrage.\n\n"
                    f"{_vol_expert}\n\n"
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
                    '| 1 | SELL | Put | $620 | [FRONT_EXP] | ~$1.85 |\n'
                    '| 2 | BUY | Put | $610 | [FRONT_EXP] | ~$0.45 |\n'
                    '| 3 | SELL | Call | $650 | [FRONT_EXP] | ~$1.10 |\n'
                    '| 4 | BUY | Call | $660 | [FRONT_EXP] | ~$0.20 |\n\n'
                    '#### P&L Profile\n'
                    '| | |\n'
                    '|---|---|\n'
                    '| **Net Credit** | $2.30 ($230/contract) |\n'
                    '| **Max Profit** | $230 (if between $620-$650 at exp) |\n'
                    '| **Max Loss** | $770 (width $10 minus credit) |\n'
                    '| **Breakevens** | $617.70 / $652.30 |\n'
                    '| **Prob. of Profit** | ~68% (based on delta) |\n\n'
                    '#### The Edge\n'
                    '[2-3 sentences explaining WHY this trade works. Reference specific surface data.]\n\n'
                    '#### Risk Flags\n'
                    '- [Risk 1]\n- [Risk 2]\n\n'
                    '---\n\n'
                    '## Portfolio Note\n'
                    '[Net Delta, Gamma, Theta, Vega if running all trades.]\n\n'
                    "RULES:\n"
                    "- Give 3-5 trades. Complete ALL of them. Do NOT truncate.\n"
                    "- Use ## for trade headers. Use #### for subsections.\n"
                    "- Every trade MUST have: Legs table, P&L Profile table, The Edge, Risk Flags.\n"
                    "- Separate trades with horizontal rules (---).\n"
                    "- Estimated prices must be realistic given the IV data.\n"
                    "- Be direct. No disclaimers. Give your real desk view.\n"
                    "- Include Prob. of Profit in P&L table (delta as proxy).\n"
                    "- LIQUIDITY CHECK: Only suggest strikes with MEDIUM+ OI. Warn on LOW OI.\n"
                    "- UNLIMITED LOSS WARNING: Flag unlimited loss positions prominently.\n"
                    "- DISLOCATIONS: Sell rich contracts, buy cheap ones.\n"
                    "- BUTTERFLY/RISK REVERSAL: Reference for tail risk and skew asymmetry.\n"
                    "- GAMMA SCALP: IV/HV < 1.0 = buy gamma; > 1.3 = sell gamma.\n"
                    "- EARNINGS: Flag if within 30 days. Avoid selling vol into earnings.\n"
                    "- GEOPOLITICAL CONFLICT: Address impact on every trade if noted.\n"
                    "- [unavailable] data: acknowledge gap, reduce confidence.\n\n"
                    f"{_acct_str}"
                    f"SURFACE DATA:\n{context}\n\n"
                    f"ANALYSIS FOCUS: {style_prompts[style]}\n\n"
                    f"{_acc}"
                )

                if refine_msg:
                    base += f"\n\nUSER FOLLOW-UP REQUEST:\n{refine_msg}\n\nAdjust your previous trade ideas based on this request. Keep the same format."

                return base

            @st.fragment
            def _gemini_fragment():
                if st.button("Generate Trade Ideas", type="primary", use_container_width=True, key="vs_gemini_run"):
                    # Use pre-built context if available, otherwise build fresh
                    context = st.session_state.get("vs_surface_context") or _build_surface_context()
                    system_prompt = _build_system_prompt(context, idea_style, _acct_size)

                    # Store context for "View AI context" expander
                    st.session_state["vs_gemini_context"] = context
                    st.session_state["vs_gemini_conversation"] = [system_prompt]

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
                                    model="gemini-3.1-pro-preview",
                                    contents=system_prompt,
                                    config=types.GenerateContentConfig(max_output_tokens=20000, temperature=0.4),
                                )
                                result_text = response.text
                                st.session_state["vs_gemini_result"] = result_text
                                st.session_state["vs_gemini_style_used"] = idea_style
                                cache_ai_response(_ai_key, result_text, model="gemini-3.1-pro-preview",
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
                                    st.error("Model not available. Check that `gemini-3.1-pro-preview` is accessible on your API key.")
                                else:
                                    st.error(f"Gemini API error: {e}")
                                logger.error(f"Gemini trade ideas failed: {e}")

                # Display results
                if "vs_gemini_result" in st.session_state:
                    style_used = st.session_state.get("vs_gemini_style_used", "")

                    # Header badge
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">'
                        f'<div style="border:1px solid {COLORS["accent"]};border-radius:6px;padding:6px 14px;'
                        f'background:rgba(0,209,255,0.06);">'
                        f'<span style="color:{COLORS["accent"]};font-weight:700;font-size:0.9rem;">Gemini 3.1 Pro</span></div>'
                        f'<div style="color:{COLORS["text_muted"]};font-size:0.8rem;">'
                        f'{style_used} &bull; {ticker_display} &bull; Spot ${spot:,.2f}</div>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

                    # Render full response
                    _display_text = st.session_state["vs_gemini_result"].replace("$", "\\$")
                    st.markdown(_display_text)

                    # Parse legs from AI response (shared by P&L diagram and CSV download)
                    _legs = []
                    try:
                        _resp = st.session_state["vs_gemini_result"]
                        import re as _re_pnl
                        _leg_pattern = _re_pnl.compile(
                            r'\|\s*\d+\s*\|\s*(SELL|BUY)\s*\|\s*(Put|Call)\s*\|\s*\$?([\d,.]+)\s*\|\s*([^\|]+)\|\s*~?\$?([\d,.]+)',
                            _re_pnl.IGNORECASE)
                        _legs = _leg_pattern.findall(_resp)
                        if _legs and len(_legs) >= 2:
                            _strikes = np.linspace(
                                min(float(l[2].replace(",", "")) for l in _legs) * 0.95,
                                max(float(l[2].replace(",", "")) for l in _legs) * 1.05, 200)
                            _pnl = np.zeros_like(_strikes)
                            _leg_data = []
                            for _action, _otype, _strike_s, _exp_s, _price_s in _legs:
                                _k = float(_strike_s.replace(",", ""))
                                _p = float(_price_s.replace(",", ""))
                                _sign = -1 if _action.upper() == "SELL" else 1
                                _leg_data.append({"action": _action, "type": _otype, "strike": _k,
                                                   "exp": _exp_s.strip(), "price": _p})
                                if _otype.lower() == "call":
                                    _pnl += _sign * (np.maximum(_strikes - _k, 0) - _p) * 100
                                else:
                                    _pnl += _sign * (np.maximum(_k - _strikes, 0) - _p) * 100

                            _fig_pnl = go.Figure()
                            _fig_pnl.add_trace(go.Scatter(
                                x=_strikes, y=_pnl, mode="lines", line=dict(color="white", width=2),
                                fill="tozeroy",
                                fillcolor="rgba(0,255,150,0.1)" if _pnl.mean() > 0 else "rgba(255,68,68,0.1)",
                                name="P&L"))
                            _fig_pnl.add_hline(y=0, line_dash="dot", line_color=COLORS["text_muted"])
                            _fig_pnl.add_vline(x=spot, line_dash="dash", line_color=COLORS["warning"],
                                               annotation_text=f"Spot ${spot:,.0f}")
                            for _ld in _leg_data:
                                _fig_pnl.add_vline(x=_ld["strike"], line_dash="dot",
                                                   line_color="#555", line_width=1)
                            _fig_pnl.update_layout(
                                template="plotly_dark", height=250,
                                margin=dict(l=0, r=0, t=30, b=0),
                                title=f"Combined P&L at Expiration ({len(_leg_data)} legs)",
                                xaxis_title="Stock Price ($)", yaxis_title="P&L ($)",
                            )
                            with st.expander(f"P&L Payoff Diagram ({len(_leg_data)} legs parsed)", expanded=True):
                                st.plotly_chart(_fig_pnl, use_container_width=True, config={"displayModeBar": False})
                    except Exception:
                        pass

                    # #5: Download trades as CSV
                    try:
                        if _legs and len(_legs) >= 2:
                            _csv_rows = "Action,Type,Strike,Expiration,Est. Price\n"
                            for _action, _otype, _strike_s, _exp_s, _price_s in _legs:
                                _csv_rows += f"{_action},{_otype},${_strike_s},{_exp_s.strip()},~${_price_s}\n"
                            st.download_button("Download Trades (CSV)", data=_csv_rows,
                                               file_name=f"{ticker_display}_trades_{datetime.now().strftime('%Y%m%d')}.csv",
                                               mime="text/csv", key="vs_dl_trades")
                    except Exception:
                        pass

                    # #2: Refine follow-up
                    st.divider()
                    _refine = st.text_input("Refine trades (e.g., 'wider strikes on Trade 2', 'show me a calendar instead')",
                                            key="vs_refine_input", placeholder="Ask for adjustments...")
                    if _refine and st.button("Refine", key="vs_refine_btn"):
                        context = st.session_state.get("vs_surface_context") or _build_surface_context()
                        _refine_prompt = _build_system_prompt(context, idea_style, _acct_size, refine_msg=_refine)
                        with fun_loader("ai"):
                            try:
                                from google import genai
                                from google.genai import types
                                client = genai.Client(api_key=gemini_key)
                                response = client.models.generate_content(
                                    model="gemini-3.1-pro-preview",
                                    contents=f"PREVIOUS ANALYSIS:\n{st.session_state['vs_gemini_result'][:3000]}\n\n{_refine_prompt}",
                                    config=types.GenerateContentConfig(max_output_tokens=20000, temperature=0.4),
                                )
                                st.session_state["vs_gemini_result"] = response.text
                                st.session_state["vs_gemini_style_used"] = f"{idea_style} (refined)"
                                st.rerun()
                            except Exception as e:
                                st.error(f"Refine failed: {e}")

                    # #1: View AI context
                    _saved_ctx = st.session_state.get("vs_gemini_context", "")
                    if _saved_ctx:
                        with st.expander("View AI Context (data sent to model)", expanded=False):
                            st.code(_saved_ctx, language="text")

                    st.caption(
                        "AI-generated trade ideas are for educational purposes only. "
                        "Always validate with your own analysis before trading. "
                        "Options trading involves substantial risk of loss."
                    )
            _gemini_fragment()


# ─── FOOTER ────────────────────────────────────────────────────────────────────

_source = st.session_state.get('current_data_source', 'Polygon Options API')
st.markdown(
    f'<div style="margin-top:24px;padding:14px 20px;border-top:1px solid {COLORS["card_border"]};'
    f'display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">'
    f'<div style="font-size:0.75rem;color:{COLORS["text_muted"]};">'
    f'Volatility surfaces are based on current market quotes and may not reflect executable prices. Not financial advice.</div>'
    f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};opacity:0.7;">'
    f'Data: {_source}</div>'
    f'</div>', unsafe_allow_html=True)
