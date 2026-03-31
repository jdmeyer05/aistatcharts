"""
Market Expectations Engine — Cross-Asset Options Intelligence

Aggregates options data across sectors, asset classes, and macro instruments
to reveal what the market is pricing in: where vol is cheap/rich, which sectors
have the most fear, what dates matter, and what common factors drive vol.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging

from src.layout import setup_page, error_boundary, fun_loader
from src.styles import COLORS
from src.data_engine import (
    format_massive_ticker, fetch_massive_data,
    get_expiration_dates, fetch_options_chain,
    render_data_source_footer,
)
from src.options_models import fill_missing_options_data
from src.cross_context import write_context

logger = logging.getLogger(__name__)
setup_page("46_Market_Expectations")

st.title("Market Expectations")
st.markdown(
    "Cross-asset options intelligence — aggregates IV, skew, and term structure "
    "across sectors and asset classes to reveal what the market is pricing in and when."
)

PLOTLY_NOBAR = {"displayModeBar": False}

# Universe: sector ETFs + key macro assets
SCAN_UNIVERSE = {
    "Sectors": {
        "XLE": "Energy", "XLF": "Financials", "XLK": "Technology",
        "XLV": "Healthcare", "XLI": "Industrials", "XLC": "Communication",
        "XLY": "Consumer Disc", "XLP": "Consumer Staples", "XLU": "Utilities",
        "XLB": "Materials", "XLRE": "Real Estate",
    },
    "Macro": {
        "SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000",
        "TLT": "Long Bonds", "GLD": "Gold", "USO": "Crude Oil",
        "EFA": "Intl Developed", "EEM": "Emerging Mkts", "HYG": "High Yield",
    },
}

ALL_TICKERS = {}
for group in SCAN_UNIVERSE.values():
    ALL_TICKERS.update(group)


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


def _atm_iv(chain, spot):
    strike = _atm_strike(chain, spot)
    for ot in ["call", "put"]:
        row = _get_contract(chain, strike, ot)
        if row is not None:
            iv = row.get("implied_volatility", 0) or 0
            if iv > 0:
                return iv
    return None


def _find_delta_strike(chain, spot, target_delta, opt_type):
    sub = chain[(chain["contract_type"] == opt_type) & (chain["implied_volatility"] > 0)].copy()
    if sub.empty:
        return None, None
    sub["delta_dist"] = (sub["delta"].abs() - abs(target_delta)).abs()
    sub = sub.dropna(subset=["delta_dist"])
    if sub.empty:
        return None, None
    best = sub.loc[sub["delta_dist"].idxmin()]
    return float(best["strike_price"]), float(best.get("implied_volatility", 0))


# ─── CONTROLS ──────────────────────────────────────────────────────────────────

run = st.button("Scan Market Expectations", type="primary", use_container_width=True,
                 help="Fetches options data for ~20 ETFs across sectors and macro. Takes 30-60 seconds.")

if run:
    st.session_state["me_loaded"] = True

if not st.session_state.get("me_loaded"):
    st.info(
        f"Click **Scan Market Expectations** to fetch options data for "
        f"{len(ALL_TICKERS)} ETFs across sectors and macro instruments."
    )
    st.stop()


# ─── DATA LOADING (cached in session state) ──────────────────────────────────

rfr = _get_rfr()

if run or "me_ticker_data" not in st.session_state:
    today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
    ticker_data = {}

    with fun_loader("data"):
        progress = st.progress(0, text="Scanning market...")
        tickers = list(ALL_TICKERS.keys())
        n = len(tickers)

        def _load_one_ticker(tk):
            """Load price + options data for a single ticker."""
            try:
                px = fetch_massive_data(tk, 252)
                if px is None or px.empty:
                    return None
                spot = float(px["Close"].iloc[-1])
                if pd.isna(spot) or spot <= 0:
                    return None

                all_exps = get_expiration_dates(tk)
                valid_exps = [e for e in (all_exps or []) if e >= today_str]
                monthly = [e for e in valid_exps
                           if 15 <= pd.to_datetime(e).day <= 21 and pd.to_datetime(e).weekday() == 4][:3]
                if len(monthly) < 2:
                    monthly = valid_exps[:3]

                chains = {}
                for exp in monthly:
                    try:
                        cdf = fetch_options_chain(tk, exp)
                        if cdf is not None and not cdf.empty:
                            cdf = fill_missing_options_data(cdf, spot, risk_free_rate=rfr)
                            chains[exp] = cdf
                    except Exception:
                        pass

                if chains:
                    rets = px["Close"].pct_change().dropna()
                    hv20 = float((rets.rolling(20).std() * np.sqrt(252)).dropna().iloc[-1]) if len(rets) > 20 else None
                    return {
                        "tk": tk, "spot": spot, "chains": chains,
                        "expirations": sorted(chains.keys()),
                        "hv20": hv20, "px_df": px, "label": ALL_TICKERS[tk],
                    }
            except Exception as e:
                logger.warning(f"Failed to load {tk}: {e}")
            return None

        # Parallel fetch — ~5x faster than sequential
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(_load_one_ticker, tk): tk for tk in tickers}
            done = 0
            for future in as_completed(futures):
                done += 1
                progress.progress(done / n, text=f"Loading ({done}/{n})...")
                result = future.result()
                if result:
                    ticker_data[result["tk"]] = {
                        k: v for k, v in result.items() if k != "tk"
                    }

        progress.empty()

    st.session_state["me_ticker_data"] = ticker_data
else:
    ticker_data = st.session_state["me_ticker_data"]

if len(ticker_data) < 3:
    st.error("Could not load enough data. Check API keys and network.")
    st.stop()

st.success(f"Loaded options data for **{len(ticker_data)}/{len(ALL_TICKERS)}** instruments.")

# ─── VIX TERM STRUCTURE (real CBOE data) ──────────────────────────────────────
vix_snap = {}
try:
    from src.macro_data import get_vix_snapshot, get_fed_liquidity_snapshot
    vix_snap = get_vix_snapshot()
    fed_liq = get_fed_liquidity_snapshot()
except Exception:
    fed_liq = {}

if vix_snap:
    _vix_keys = [k for k in ["VIX9D", "VIX", "VIX3M", "VIX6M", "VIX1Y", "SKEW"] if k in vix_snap]
    _vts_cols = st.columns(len(_vix_keys) + 1)
    for i, k in enumerate(_vix_keys):
        _vts_cols[i].metric(k, f"{vix_snap[k]:.1f}")
    if "regime" in vix_snap:
        _vts_cols[len(_vix_keys)].metric("VIX Regime", vix_snap["regime"])

    # SKEW-VIX divergence alert
    if vix_snap.get("divergence"):
        _div_msg = vix_snap["divergence"]
        _div_color = COLORS["warning"] if "calm surface" in _div_msg or "Extreme" in _div_msg else (
            COLORS["success"] if "bottom" in _div_msg else COLORS["accent"])
        st.markdown(
            f'<div style="background:{COLORS["card_bg"]};border:1px solid {_div_color};'
            f'border-radius:6px;padding:8px 14px;margin:8px 0;font-size:0.85rem;">'
            f'<b style="color:{_div_color};">VIX-SKEW Signal:</b> {_div_msg}</div>',
            unsafe_allow_html=True,
        )

if fed_liq:
    with st.expander("Fed Liquidity", expanded=False):
        fl1, fl2, fl3, fl4 = st.columns(4)
        fl1.metric("Fed Total Assets", f"${fed_liq.get('total_assets', '?')}T")
        fl2.metric("Treasury Account", f"${fed_liq.get('tga', '?')}B")
        fl3.metric("Reverse Repo", f"${fed_liq.get('rrp', '?')}B")
        fl4.metric("Net Liquidity", f"${fed_liq.get('net_liquidity', '?')}T",
                    delta=f"${fed_liq.get('net_liq_change', 0):+.0f}B monthly" if fed_liq.get('net_liq_change') else None)
        if fed_liq.get("draining"):
            st.warning("Net liquidity is declining — historically bearish for risk assets.")

# ─── COMPUTE METRICS ───────────────────────────────────────────────────────────

metrics = []
for tk, td in ticker_data.items():
    spot = td["spot"]
    exps = td["expirations"]
    hv20 = td["hv20"]
    label = td["label"]

    # Front-month ATM IV
    front_chain = td["chains"][exps[0]]
    front_iv = _atm_iv(front_chain, spot)
    front_dte = _dte(exps[0])

    # Back-month ATM IV (if available)
    back_iv = None
    back_dte = None
    if len(exps) >= 2:
        back_chain = td["chains"][exps[-1]]
        back_iv = _atm_iv(back_chain, spot)
        back_dte = _dte(exps[-1])

    # Put/Call ratio (computed from chain data)
    pc_ratio = None
    if "volume" in front_chain.columns:
        _put_vol = front_chain[front_chain["contract_type"] == "put"]["volume"].sum()
        _call_vol = front_chain[front_chain["contract_type"] == "call"]["volume"].sum()
        pc_ratio = float(_put_vol / _call_vol) if _call_vol > 0 else None

    # IV/HV ratio
    iv_hv = front_iv / hv20 if front_iv and hv20 and hv20 > 0 else None

    # Term structure slope
    ts_slope = None
    if front_iv and back_iv and back_dte and front_dte and back_dte > front_dte:
        ts_slope = (back_iv - front_iv) / (back_dte - front_dte) * 30  # per month

    # 25-delta put skew
    put_k, put_iv = _find_delta_strike(front_chain, spot, -0.25, "put")
    call_k, call_iv = _find_delta_strike(front_chain, spot, 0.25, "call")
    put_skew = put_iv / front_iv if put_iv and front_iv and front_iv > 0 else None
    risk_rev = (call_iv - put_iv) * 100 if call_iv and put_iv else None

    # Straddle-implied move
    atm_k = _atm_strike(front_chain, spot)
    c_row = _get_contract(front_chain, atm_k, "call")
    p_row = _get_contract(front_chain, atm_k, "put")
    straddle = None
    impl_move_pct = None
    if c_row is not None and p_row is not None:
        c_mid = ((c_row.get("bid", 0) or 0) + (c_row.get("ask", 0) or 0)) / 2
        p_mid = ((p_row.get("bid", 0) or 0) + (p_row.get("ask", 0) or 0)) / 2
        if c_mid <= 0:
            c_mid = c_row.get("last_price", 0) or 0
        if p_mid <= 0:
            p_mid = p_row.get("last_price", 0) or 0
        straddle = c_mid + p_mid
        if straddle > 0 and spot > 0:
            impl_move_pct = straddle * 0.798 / spot * 100

    # IV Percentile Rank (vs 1-year HV distribution)
    # Historical IV percentile from options_history is too slow for 20-ticker batch scans
    iv_pctile = None
    if front_iv and td["px_df"] is not None and len(td["px_df"]) > 60:
        _rets = td["px_df"]["Close"].pct_change().dropna()
        _hv_dist = (_rets.rolling(20).std() * np.sqrt(252)).dropna().values
        if len(_hv_dist) > 10:
            iv_pctile = float(((_hv_dist < front_iv).sum() / len(_hv_dist)) * 100)

    # Vol Risk Premium: IV² - subsequent RV² (annualized, use trailing 20d RV as proxy)
    vrp = None
    if front_iv and hv20:
        vrp = (front_iv ** 2 - hv20 ** 2) * 100  # variance points

    # Incremental implied moves between expirations
    incr_moves = []
    prev_impl_sq = 0
    for exp in exps:
        chain = td["chains"][exp]
        dte = _dte(exp)
        if dte <= 0:
            continue
        atm_k_e = _atm_strike(chain, spot)
        c_e = _get_contract(chain, atm_k_e, "call")
        p_e = _get_contract(chain, atm_k_e, "put")
        if c_e is not None and p_e is not None:
            c_m = ((c_e.get("bid", 0) or 0) + (c_e.get("ask", 0) or 0)) / 2
            p_m = ((p_e.get("bid", 0) or 0) + (p_e.get("ask", 0) or 0)) / 2
            if c_m <= 0: c_m = c_e.get("last_price", 0) or 0
            if p_m <= 0: p_m = p_e.get("last_price", 0) or 0
            strad = c_m + p_m
            if strad > 0 and spot > 0:
                impl_move_sq = (strad * 0.798 / spot * 100) ** 2
                incr_sq = max(0, impl_move_sq - prev_impl_sq)
                incr_moves.append({"exp": exp, "dte": dte, "incr_move": np.sqrt(incr_sq)})
                prev_impl_sq = impl_move_sq
    td["incr_moves"] = incr_moves

    # Determine group
    group = "Macro"
    for gname, gtickers in SCAN_UNIVERSE.items():
        if tk in gtickers:
            group = gname
            break

    metrics.append({
        "Ticker": tk,
        "Name": label,
        "Group": group,
        "Spot": spot,
        "Front IV": front_iv,
        "Front DTE": front_dte,
        "Back IV": back_iv,
        "IV/HV": iv_hv,
        "IV Pctile": iv_pctile,
        "VRP": vrp,
        "TS Slope": ts_slope,
        "Put Skew": put_skew,
        "Risk Rev": risk_rev,
        "Impl Move %": impl_move_pct,
        "HV20": hv20,
        "P/C Ratio": pc_ratio,
    })

mdf = pd.DataFrame(metrics)

# ─── COMPUTE IMPLIED CORRELATION ──────────────────────────────────────────────
# Index IV² ≈ Σwᵢwⱼ ρᵢⱼ σᵢσⱼ → implied ρ = (σ_index² - Σwᵢ²σᵢ²) / (Σᵢ≠ⱼ wᵢwⱼσᵢσⱼ)
# Simplified: ρ_impl ≈ (σ_SPY / σ_avg_component)²  (equal-weighted approx)
implied_corr = None
avg_sector_iv = None
spy_iv = mdf.loc[mdf["Ticker"] == "SPY", "Front IV"].iloc[0] if "SPY" in mdf["Ticker"].values else None
sector_ivs = mdf[mdf["Group"] == "Sectors"]["Front IV"].dropna()
if spy_iv and spy_iv > 0 and len(sector_ivs) >= 5:
    avg_sector_iv = sector_ivs.mean()
    n_sectors = len(sector_ivs)
    sum_iv_sq = (sector_ivs ** 2).sum()
    avg_iv_sq = sum_iv_sq / n_sectors
    if avg_iv_sq > 0 and n_sectors > 1:
        # Implied correlation: ρ = (N*σ_index² - Σσᵢ²) / ((N-1) * avg(σᵢ²) * N)
        # Simplified for equal-weighted: ρ = (σ_index² - avg(σᵢ²)/N) / (avg(σᵢ²) * (1 - 1/N))
        numerator = spy_iv ** 2 - avg_iv_sq / n_sectors
        denominator = avg_iv_sq * (1 - 1 / n_sectors)
        implied_corr = float(np.clip(numerator / denominator, 0, 1)) if denominator > 0 else None

# ─── COMPUTE BETA-ADJUSTED RELATIVE IV ───────────────────────────────────────
# For each sector, compute how much its IV deviates from its typical relationship to SPY IV
spy_hv = mdf.loc[mdf["Ticker"] == "SPY", "HV20"].iloc[0] if "SPY" in mdf["Ticker"].values else None
mdf["IV Beta"] = np.nan
mdf["Expected IV"] = np.nan
mdf["IV Residual"] = np.nan
if spy_iv and spy_iv > 0 and spy_hv and spy_hv > 0:
    for i, row in mdf.iterrows():
        if pd.notna(row["Front IV"]) and pd.notna(row["HV20"]) and row["HV20"] > 0:
            iv_beta = row["HV20"] / spy_hv
            expected_iv = spy_iv * iv_beta
            mdf.loc[i, "IV Beta"] = iv_beta
            mdf.loc[i, "Expected IV"] = expected_iv
            mdf.loc[i, "IV Residual"] = (row["Front IV"] - expected_iv) * 100

try:
    from src.metrics_store import auto_save_from_page
    auto_save_from_page(mdf)
except Exception:
    pass

if mdf.empty or mdf["Front IV"].notna().sum() == 0:
    st.warning("No valid IV data computed. Options chains may be unavailable or market is closed.")
    st.stop()

# ─── MARKET REGIME SUMMARY (ties all tabs together) ──────────────────────────

_avg_iv = mdf["Front IV"].mean() * 100 if mdf["Front IV"].notna().any() else 0
_avg_ivhv = mdf["IV/HV"].mean() if mdf["IV/HV"].notna().any() else 1.0
_avg_pctile = mdf["IV Pctile"].mean() if "IV Pctile" in mdf.columns and mdf["IV Pctile"].notna().any() else 50
_n_inverted = (mdf["TS Slope"].fillna(0) < 0).sum()
_n_steep_skew = (mdf["Put Skew"].fillna(1) > 1.10).sum()
_skew_n = mdf["Put Skew"].notna().sum()

# Determine regime
if _avg_ivhv > 1.2 and _avg_pctile > 65:
    _regime = "Elevated Vol — Rich Premiums"
    _regime_color = COLORS["danger"]
    _regime_action = "Options are broadly expensive. Favor selling premium, iron condors, and covered calls."
elif _avg_ivhv < 0.85 and _avg_pctile < 35:
    _regime = "Low Vol — Cheap Protection"
    _regime_color = COLORS["success"]
    _regime_action = "Options are historically cheap. Buy protective puts and long straddles."
elif _n_inverted >= 3:
    _regime = "Event-Driven — Near-Term Fear"
    _regime_color = COLORS["warning"]
    _regime_action = "Multiple assets in backwardation. Calendar spreads and event straddles in focus."
elif _skew_n > 0 and _n_steep_skew > _skew_n * 0.5:
    _regime = "Broad Fear — Steep Skew"
    _regime_color = COLORS["danger"]
    _regime_action = "Put skew elevated across sectors. Index puts are efficient hedges. Sell put spreads selectively."
else:
    _regime = "Normal Conditions"
    _regime_color = COLORS["accent"]
    _regime_action = "No extreme signals. Standard vol selling and tactical positioning."

# Write cross-page context for other pages to consume
_inverted_tickers = mdf[mdf["TS Slope"].fillna(0) < 0]["Ticker"].tolist() if "TS Slope" in mdf.columns else []
_rich_tickers = mdf[mdf["IV/HV"].fillna(0) > 1.2]["Ticker"].tolist() if "IV/HV" in mdf.columns else []
_cheap_tickers = mdf[mdf["IV/HV"].fillna(999) < 0.8]["Ticker"].tolist() if "IV/HV" in mdf.columns else []
write_context("market_expectations", {
    "regime": _regime,
    "regime_action": _regime_action,
    "avg_iv": round(_avg_iv, 1),
    "avg_ivhv": round(_avg_ivhv, 2),
    "avg_pctile": round(_avg_pctile, 0),
    "implied_corr": round(implied_corr, 2) if implied_corr is not None else None,
    "inverted_assets": _inverted_tickers,
    "richest_iv": _rich_tickers[:5],
    "cheapest_iv": _cheap_tickers[:5],
    "n_steep_skew": int(_n_steep_skew),
    "vix_term": vix_snap,
    "fed_liquidity": fed_liq,
})

st.markdown(
    f'<div style="background:{COLORS["card_bg"]};border:1px solid {_regime_color};'
    f'border-radius:8px;padding:16px 20px;margin-bottom:16px;">'
    f'<div style="display:flex;justify-content:space-between;align-items:center;">'
    f'<div>'
    f'<div style="color:{_regime_color};font-weight:700;font-size:1.1rem;">{_regime}</div>'
    f'<div style="color:{COLORS["text_muted"]};font-size:0.85rem;margin-top:4px;">{_regime_action}</div>'
    f'</div>'
    f'<div style="text-align:right;font-size:0.8rem;color:{COLORS["text_muted"]};">'
    f'Avg IV: {_avg_iv:.1f}% | IV/HV: {_avg_ivhv:.2f}x | '
    f'IV Pctile: {_avg_pctile:.0f}% | '
    f'Inverted: {_n_inverted} | Steep Skew: {_n_steep_skew}'
    f'{f" | Impl Corr: {implied_corr:.2f}" if implied_corr is not None else ""}'
    f'</div></div></div>',
    unsafe_allow_html=True,
)


# ─── TABS ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs([
    "Vol Dashboard", "Term Structure", "Skew Landscape",
    "PCA / Factor Analysis", "Implied vs Realized", "Event Timing",
    "Implied Correlation", "Trade Ideas",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — VOL DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("Vol Dashboard"):
        st.subheader("Cross-Asset Volatility Dashboard")
        with st.expander("Why this matters"):
            st.markdown("""
This is the single most important view for understanding market expectations.

**What you're seeing:** Every major sector and macro asset's implied volatility,
ranked and color-coded. This tells you at a glance:
- **Which sectors are pricing in the most uncertainty** (high IV)
- **Where vol is cheap vs expensive** relative to how the asset actually moves (IV/HV ratio)
- **The expected move** for each asset over the front-month period
- **Whether the term structure is normal or inverted** (contango vs backwardation)

**How to use it:**
- IV/HV > 1.2 = options are expensive relative to recent movement (sell premium)
- IV/HV < 0.8 = options are cheap (buy protection or straddles)
- Inverted term structure (negative TS Slope) = near-term event risk
""")

        # Heatmap: IV by sector
        valid = mdf[mdf["Front IV"].notna()].copy()
        valid["IV %"] = valid["Front IV"] * 100
        valid = valid.sort_values("IV %", ascending=False)

        fig_dash = go.Figure(go.Bar(
            y=valid["Ticker"] + " (" + valid["Name"] + ")",
            x=valid["IV %"],
            orientation="h",
            marker_color=[
                COLORS["danger"] if v > 30 else (COLORS["warning"] if v > 20 else COLORS["accent"])
                for v in valid["IV %"]
            ],
            text=[f"{v:.1f}%" for v in valid["IV %"]],
            textposition="outside",
        ))
        fig_dash.update_layout(
            template="plotly_dark", height=max(400, len(valid) * 25),
            xaxis_title="ATM Implied Volatility (%)",
            margin=dict(l=0, r=60, t=10, b=0),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig_dash, use_container_width=True, config=PLOTLY_NOBAR)

        # Summary table
        st.markdown("#### Detail Table")
        display = valid.copy()
        fmt_cols = {
            "Front IV": lambda v: f"{v*100:.1f}%" if pd.notna(v) else "—",
            "Back IV": lambda v: f"{v*100:.1f}%" if pd.notna(v) else "—",
            "IV/HV": lambda v: f"{v:.2f}x" if pd.notna(v) else "—",
            "IV Pctile": lambda v: f"{v:.0f}%" if pd.notna(v) else "—",
            "VRP": lambda v: f"{v:+.1f}" if pd.notna(v) else "—",
            "IV Residual": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
            "TS Slope": lambda v: f"{v*100:+.2f}%/mo" if pd.notna(v) else "—",
            "Put Skew": lambda v: f"{v:.2f}x" if pd.notna(v) else "—",
            "Risk Rev": lambda v: f"{v:+.1f}%" if pd.notna(v) else "—",
            "Impl Move %": lambda v: f"{v:.1f}%" if pd.notna(v) else "—",
            "HV20": lambda v: f"{v*100:.1f}%" if pd.notna(v) else "—",
            "P/C Ratio": lambda v: f"{v:.2f}" if pd.notna(v) else "—",
            "Spot": lambda v: f"${v:,.2f}",
        }
        for col, fn in fmt_cols.items():
            if col in display.columns:
                display[col] = display[col].apply(fn)
        display = display.drop(columns=["IV %"], errors="ignore")
        st.dataframe(display, use_container_width=True, hide_index=True)

        # Sector vs Macro comparison
        st.markdown("#### Sectors vs Macro — Average IV")
        for gname in SCAN_UNIVERSE:
            g_tickers = list(SCAN_UNIVERSE[gname].keys())
            g_data = mdf[mdf["Ticker"].isin(g_tickers) & mdf["Front IV"].notna()]
            if not g_data.empty:
                avg_iv = g_data["Front IV"].mean() * 100
                avg_ivhv = g_data["IV/HV"].mean() if g_data["IV/HV"].notna().any() else 0
                st.caption(
                    f"**{gname}:** Avg IV {avg_iv:.1f}% | "
                    f"Avg IV/HV {avg_ivhv:.2f}x | "
                    f"{len(g_data)} instruments"
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — TERM STRUCTURE COMPARISON
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    with error_boundary("Term Structure"):
        st.subheader("Term Structure Comparison")
        with st.expander("Why this matters"):
            st.markdown("""
**Every line is a different asset's ATM IV across expirations.** Comparing them reveals:

- **Which sectors have event-driven kinks** (spikes at specific dates = earnings, FOMC)
- **Which assets are in backwardation** (inverted = near-term fear exceeds long-term)
- **Relative vol levels** — is energy vol always higher than utilities? If not, something changed.
- **Convergence points** — when multiple assets' term structures converge at a date, the market
  expects a correlated event (like FOMC or a macro release) to move everything together.
""")

        fig_ts = go.Figure()
        colors = [COLORS["accent"], COLORS["success"], COLORS["warning"], COLORS["danger"],
                  "#9966ff", "#ff66cc", "#66ffcc", "#ffcc66", "#6699ff", "#ff9966",
                  "#99ff66", "#cc66ff", "#00e0d0", "#ff00ff", "#66aacc",
                  "#88ccff", "#ffaa00", "#00ff88", "#ff4444", "#888888"]

        for i, (tk, td) in enumerate(ticker_data.items()):
            exps = td["expirations"]
            spot = td["spot"]
            ts_points = []
            for exp in exps:
                chain = td["chains"][exp]
                iv = _atm_iv(chain, spot)
                if iv:
                    ts_points.append({"dte": _dte(exp), "iv": iv * 100})
            if ts_points:
                tsdf = pd.DataFrame(ts_points)
                fig_ts.add_trace(go.Scatter(
                    x=tsdf["dte"], y=tsdf["iv"],
                    mode="lines+markers", name=f"{tk} ({td['label']})",
                    line=dict(color=colors[i % len(colors)], width=2),
                    marker=dict(size=6),
                ))

        fig_ts.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=500,
            xaxis_title="Days to Expiration",
            yaxis_title="ATM IV (%)",
            legend=dict(bgcolor="rgba(0,0,0,0.5)", font=dict(size=9)),
            margin=dict(l=50, r=20, t=10, b=50),
        )
        st.plotly_chart(fig_ts, use_container_width=True, config=PLOTLY_NOBAR)

        # Term structure slope ranking
        st.markdown("#### Term Structure Slope Ranking")
        ts_valid = mdf[mdf["TS Slope"].notna()].sort_values("TS Slope")
        if not ts_valid.empty:
            ts_colors = [COLORS["danger"] if v < 0 else COLORS["success"] for v in ts_valid["TS Slope"]]
            fig_tsr = go.Figure(go.Bar(
                y=ts_valid["Ticker"], x=ts_valid["TS Slope"] * 100,
                orientation="h", marker_color=ts_colors,
                text=[f"{v*100:+.2f}%/mo" for v in ts_valid["TS Slope"]],
                textposition="outside",
            ))
            fig_tsr.add_vline(x=0, line_color=COLORS["text_muted"], line_width=0.5)
            fig_tsr.update_layout(
                template="plotly_dark", height=max(350, len(ts_valid) * 22),
                xaxis_title="Term Structure Slope (%/month)",
                margin=dict(l=0, r=60, t=10, b=0),
            )
            st.plotly_chart(fig_tsr, use_container_width=True, config=PLOTLY_NOBAR)
            inverted = ts_valid[ts_valid["TS Slope"] < 0]
            if not inverted.empty:
                st.warning(
                    f"**{len(inverted)} assets in backwardation:** "
                    f"{', '.join(inverted['Ticker'].tolist())} — "
                    f"near-term IV exceeds back-month, suggesting imminent event risk."
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — SKEW LANDSCAPE
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    with error_boundary("Skew Landscape"):
        st.subheader("Cross-Asset Skew Analysis")
        with st.expander("Why this matters"):
            st.markdown("""
**Skew tells you where the market sees asymmetric risk.**

- **Steep put skew** (>1.10) = heavy demand for downside protection. The market is scared.
- **Flat put skew** (~1.0) = market sees symmetric risk or is complacent.
- **Negative risk reversal** = puts cost more than calls (normal for equities).
- **Very negative risk reversal** (< -5%) = extreme fear in that specific sector.

**Cross-asset comparison reveals:**
- If ALL sectors have steep skew simultaneously → systemic fear (macro event)
- If ONE sector has steep skew → sector-specific catalyst (earnings, regulation)
- Energy + Materials steep skew → commodity supply disruption fears
- Financials steep skew → credit/rate risk concerns
""")

        # Put skew comparison
        skew_valid = mdf[mdf["Put Skew"].notna()].sort_values("Put Skew", ascending=False)
        if not skew_valid.empty:
            st.markdown("#### Put Skew by Asset (25d Put IV / ATM IV)")
            skew_colors = [COLORS["danger"] if v > 1.15 else (COLORS["warning"] if v > 1.05 else COLORS["success"])
                           for v in skew_valid["Put Skew"]]
            fig_skew = go.Figure(go.Bar(
                y=skew_valid["Ticker"] + " (" + skew_valid["Name"] + ")",
                x=skew_valid["Put Skew"],
                orientation="h", marker_color=skew_colors,
                text=[f"{v:.2f}x" for v in skew_valid["Put Skew"]],
                textposition="outside",
            ))
            fig_skew.add_vline(x=1.0, line_dash="dash", line_color=COLORS["text_muted"],
                                annotation_text="Flat skew")
            fig_skew.update_layout(
                template="plotly_dark", height=max(400, len(skew_valid) * 25),
                xaxis_title="Put Skew (25d / ATM)",
                margin=dict(l=0, r=60, t=10, b=0),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig_skew, use_container_width=True, config=PLOTLY_NOBAR)

        # Risk reversal comparison
        rr_valid = mdf[mdf["Risk Rev"].notna()].sort_values("Risk Rev")
        if not rr_valid.empty:
            st.markdown("#### Risk Reversal by Asset (25d Call IV - 25d Put IV)")
            rr_colors = [COLORS["danger"] if v < -3 else (COLORS["warning"] if v < 0 else COLORS["success"])
                          for v in rr_valid["Risk Rev"]]
            fig_rr = go.Figure(go.Bar(
                y=rr_valid["Ticker"], x=rr_valid["Risk Rev"],
                orientation="h", marker_color=rr_colors,
                text=[f"{v:+.1f}%" for v in rr_valid["Risk Rev"]],
                textposition="outside",
            ))
            fig_rr.add_vline(x=0, line_color=COLORS["text_muted"], line_width=0.5)
            fig_rr.update_layout(
                template="plotly_dark", height=max(350, len(rr_valid) * 22),
                xaxis_title="Risk Reversal (%)",
                margin=dict(l=0, r=60, t=10, b=0),
            )
            st.plotly_chart(fig_rr, use_container_width=True, config=PLOTLY_NOBAR)

        # Fear gauge
        st.markdown("#### Market Fear Gauge")
        _skew_valid_count = mdf["Put Skew"].notna().sum()
        avg_skew = mdf["Put Skew"].mean() if _skew_valid_count > 0 else 1.0
        avg_rr = mdf["Risk Rev"].mean() if mdf["Risk Rev"].notna().any() else 0
        steep_count = (mdf["Put Skew"] > 1.10).sum()

        fc1, fc2, fc3 = st.columns(3)
        fc1.metric("Avg Put Skew", f"{avg_skew:.2f}x",
                    help=">1.10 = broad fear premium")
        fc2.metric("Avg Risk Reversal", f"{avg_rr:+.1f}%",
                    help="< -3% = significant bearish skew")
        fc3.metric("Assets w/ Steep Skew", f"{steep_count}/{_skew_valid_count}",
                    help="Put skew > 1.10x")

        if _skew_valid_count > 0 and steep_count > _skew_valid_count * 0.6:
            st.error("Broad-based fear — majority of assets have steep put skew. Systemic risk event likely priced in.")
        elif _skew_valid_count > 0 and steep_count > _skew_valid_count * 0.3:
            st.warning("Elevated but selective fear — check which sectors are driving it.")
        else:
            st.success("Skew is moderate across most assets — no broad-based panic.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — PCA / FACTOR ANALYSIS
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    with error_boundary("PCA"):
        st.subheader("PCA on Cross-Asset Returns")
        with st.expander("Why this matters"):
            st.markdown("""
**PCA decomposes cross-asset return correlations into common factors.** This tells you:

- **PC1 (typically 50-70% of variance):** The "level" factor — when vol rises everywhere, PC1 is high.
  This is your systemic risk gauge.
- **PC2 (10-20%):** Usually the "sector rotation" or "risk-on/risk-off" factor.
  Sectors loading positively are in one camp, negatively in another.
- **PC3 (5-10%):** Often an "event" or "idiosyncratic" factor.

**How to trade it:**
- If PC1 explains >70% of variance → market is highly correlated, vol is moving in lockstep.
  This happens before systemic events. Diversification won't help.
- If PC1 explains <50% → idiosyncratic risk dominates. Sector picking matters more.
- Assets with high PC2 loading in opposite directions → natural pairs for spread trades.
""")

        # PCA on daily return correlations — reveals which assets move together
        # and what common factors drive the cross-asset return structure
        ret_data = {}
        for tk, td in ticker_data.items():
            px = td["px_df"]
            if px is not None and len(px) > 60:
                ret_data[tk] = px["Close"].pct_change().dropna()

        if len(ret_data) >= 5:
            ret_df = pd.DataFrame(ret_data).dropna()

            if len(ret_df) > 60:
                # Standardize
                std_ret = (ret_df - ret_df.mean()) / ret_df.std()

                # PCA via eigendecomposition of correlation matrix
                corr = std_ret.corr().values
                eigenvalues, eigenvectors = np.linalg.eigh(corr)
                # Sort descending
                idx = np.argsort(eigenvalues)[::-1]
                eigenvalues = eigenvalues[idx]
                eigenvectors = eigenvectors[:, idx]
                # Clamp negative eigenvalues from numerical noise
                eigenvalues = np.maximum(eigenvalues, 0)

                # Variance explained
                total_var = eigenvalues.sum()
                if total_var <= 0:
                    st.warning("Correlation matrix has no positive eigenvalues — data may be degenerate.")
                    st.stop()
                var_explained = eigenvalues / total_var * 100
                cum_var = np.cumsum(var_explained)

                # Top 5 PCs
                n_pcs = min(5, len(eigenvalues))

                st.markdown("#### Variance Explained")
                pc_c1, pc_c2 = st.columns(2)
                with pc_c1:
                    fig_var = go.Figure()
                    fig_var.add_trace(go.Bar(
                        x=[f"PC{i+1}" for i in range(n_pcs)],
                        y=var_explained[:n_pcs],
                        marker_color=COLORS["accent"],
                        text=[f"{v:.1f}%" for v in var_explained[:n_pcs]],
                        textposition="outside",
                    ))
                    fig_var.update_layout(
                        template="plotly_dark", height=300,
                        yaxis_title="Variance Explained (%)",
                        margin=dict(l=50, r=20, t=10, b=50),
                    )
                    st.plotly_chart(fig_var, use_container_width=True, config=PLOTLY_NOBAR)

                with pc_c2:
                    fig_cum = go.Figure()
                    fig_cum.add_trace(go.Scatter(
                        x=[f"PC{i+1}" for i in range(n_pcs)],
                        y=cum_var[:n_pcs],
                        mode="lines+markers",
                        line=dict(color=COLORS["success"], width=2),
                        text=[f"{v:.1f}%" for v in cum_var[:n_pcs]],
                    ))
                    fig_cum.add_hline(y=80, line_dash="dot", line_color=COLORS["warning"],
                                      annotation_text="80% threshold")
                    fig_cum.update_layout(
                        template="plotly_dark", height=300,
                        yaxis_title="Cumulative Variance (%)",
                        margin=dict(l=50, r=20, t=10, b=50),
                    )
                    st.plotly_chart(fig_cum, use_container_width=True, config=PLOTLY_NOBAR)

                # Interpretation — cross-reference with implied correlation
                pc1_var = var_explained[0]
                _ic_note = ""
                if implied_corr is not None:
                    if pc1_var < 50 and implied_corr > 0.5:
                        _ic_note = (
                            f" Note: implied correlation ({implied_corr:.2f}) is higher than "
                            f"realized return correlation suggests. The options market is pricing "
                            f"in MORE correlation going forward than has occurred recently — "
                            f"possible upcoming macro event."
                        )
                    elif pc1_var > 60 and implied_corr < 0.4:
                        _ic_note = (
                            f" Note: implied correlation ({implied_corr:.2f}) is lower than "
                            f"realized correlation suggests. The options market expects sectors "
                            f"to DIVERGE going forward, even though they've moved together recently."
                        )

                if pc1_var > 70:
                    st.error(f"PC1 explains {pc1_var:.0f}% of realized return variance — assets have been moving in lockstep.{_ic_note}")
                elif pc1_var > 50:
                    st.warning(f"PC1 explains {pc1_var:.0f}% — moderate realized correlation. Market factor matters but some sectors diverge.{_ic_note}")
                else:
                    st.success(f"PC1 explains {pc1_var:.0f}% — low realized correlation over the past 60 days. Sector-level analysis matters more than broad market calls.{_ic_note}")

                # Factor loadings
                st.markdown("#### Factor Loadings (PC1 vs PC2)")
                tickers_pca = list(ret_df.columns)
                loadings = pd.DataFrame(
                    eigenvectors[:, :n_pcs],
                    index=tickers_pca,
                    columns=[f"PC{i+1}" for i in range(n_pcs)],
                )

                fig_load = go.Figure()
                for ti, tk in enumerate(tickers_pca):
                    group = "Macro"
                    for gn, gt in SCAN_UNIVERSE.items():
                        if tk in gt:
                            group = gn
                            break
                    color = COLORS["accent"] if group == "Sectors" else COLORS["warning"]
                    fig_load.add_trace(go.Scatter(
                        x=[loadings.loc[tk, "PC1"]],
                        y=[loadings.loc[tk, "PC2"]],
                        mode="markers+text",
                        marker=dict(size=10, color=color),
                        text=[tk], textposition="top center",
                        textfont=dict(size=9),
                        showlegend=False,
                    ))
                fig_load.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.3)
                fig_load.add_vline(x=0, line_color=COLORS["text_muted"], line_width=0.3)
                fig_load.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=450,
                    xaxis_title=f"PC1 Loading ({var_explained[0]:.0f}% var)",
                    yaxis_title=f"PC2 Loading ({var_explained[1]:.0f}% var)",
                    margin=dict(l=50, r=20, t=10, b=50),
                )
                st.plotly_chart(fig_load, use_container_width=True, config=PLOTLY_NOBAR)
                st.caption(
                    "Assets close together move similarly. Assets on opposite sides of the origin "
                    "move inversely — natural hedging or spread trading pairs."
                )

                # Loadings table
                with st.expander("Full Factor Loadings Table"):
                    ld = loadings.round(3)
                    st.dataframe(ld, use_container_width=True)
            else:
                st.warning("Need 60+ days of overlapping return data for PCA.")
        else:
            st.warning(f"Need at least 5 instruments for PCA — only {len(ret_data)} loaded.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — IMPLIED VS REALIZED
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    with error_boundary("Implied vs Realized"):
        st.subheader("IV / HV Landscape")
        with st.expander("Why this matters"):
            st.markdown("""
**The IV/HV ratio is the single best indicator of whether options are cheap or expensive.**

- **IV/HV > 1.2** = market expects MORE volatility than has occurred → options are expensive → sell premium
- **IV/HV < 0.8** = market expects LESS volatility → options are cheap → buy protection
- **IV/HV ≈ 1.0** = fairly priced

**Cross-asset view reveals:**
- If ALL assets have high IV/HV → broad fear, vol event coming
- If ONE asset has high IV/HV → event-specific (earnings, sector catalyst)
- Consistent low IV/HV across sectors → complacency → contrarian buy signal for protection
""")

        ivhv_valid = mdf[mdf["IV/HV"].notna()].sort_values("IV/HV", ascending=False)
        if not ivhv_valid.empty:
            ivhv_colors = [
                COLORS["danger"] if v > 1.2 else (COLORS["warning"] if v > 1.0 else COLORS["success"])
                for v in ivhv_valid["IV/HV"]
            ]
            fig_ivhv = go.Figure(go.Bar(
                y=ivhv_valid["Ticker"] + " (" + ivhv_valid["Name"] + ")",
                x=ivhv_valid["IV/HV"],
                orientation="h", marker_color=ivhv_colors,
                text=[f"{v:.2f}x" for v in ivhv_valid["IV/HV"]],
                textposition="outside",
            ))
            fig_ivhv.add_vline(x=1.0, line_dash="dash", line_color=COLORS["text_muted"],
                                annotation_text="Fair value")
            fig_ivhv.update_layout(
                template="plotly_dark", height=max(400, len(ivhv_valid) * 25),
                xaxis_title="IV / HV Ratio",
                margin=dict(l=0, r=60, t=10, b=0),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig_ivhv, use_container_width=True, config=PLOTLY_NOBAR)

            # Scatter: IV vs HV
            st.markdown("#### IV vs HV Scatter")
            _sc_iv = ivhv_valid["Front IV"].fillna(0) * 100
            _sc_hv = ivhv_valid["HV20"].fillna(0) * 100
            _sc_colors = [COLORS["danger"] if v > 1.2 else (COLORS["success"] if v < 0.8 else COLORS["accent"])
                          for v in ivhv_valid["IV/HV"]]
            fig_scatter = go.Figure()
            fig_scatter.add_trace(go.Scatter(
                x=_sc_hv.values, y=_sc_iv.values,
                mode="markers+text",
                marker=dict(size=12, color=_sc_colors),
                text=ivhv_valid["Ticker"].values,
                textposition="top center", textfont=dict(size=9),
                showlegend=False,
            ))
            # 45-degree line (IV = HV)
            _max_iv = _sc_iv.max() if len(_sc_iv) > 0 else 30
            _max_hv = _sc_hv.max() if len(_sc_hv) > 0 else 30
            max_val = max(_max_iv, _max_hv, 30)
            fig_scatter.add_trace(go.Scatter(
                x=[0, max_val], y=[0, max_val],
                mode="lines", line=dict(color=COLORS["text_muted"], dash="dash", width=1),
                name="IV = HV", showlegend=False,
            ))
            fig_scatter.update_layout(
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", height=450,
                xaxis_title="20-Day Realized Vol (%)",
                yaxis_title="Front-Month ATM IV (%)",
                margin=dict(l=50, r=20, t=10, b=50),
            )
            st.plotly_chart(fig_scatter, use_container_width=True, config=PLOTLY_NOBAR)
            st.caption(
                "Above the line = IV > HV (expensive). Below = IV < HV (cheap). "
                "Distance from the line = magnitude of mispricing."
            )

            # Summary
            rich = ivhv_valid[ivhv_valid["IV/HV"] > 1.2]
            cheap = ivhv_valid[ivhv_valid["IV/HV"] < 0.8]
            if len(rich) > 0:
                st.warning(f"**Richest (sell premium):** {', '.join(rich['Ticker'].tolist())}")
            if len(cheap) > 0:
                st.success(f"**Cheapest (buy protection):** {', '.join(cheap['Ticker'].tolist())}")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 6 — EVENT TIMING
# ══════════════════════════════════════════════════════════════════════════════

with tab6:
    with error_boundary("Event Timing"):
        st.subheader("Market Event Calendar from Options")
        with st.expander("Why this matters"):
            st.markdown("""
**Options prices reveal what dates the market is bracing for.**

This tab shows the **implied move** for each asset at each expiration. Large implied moves
at specific dates indicate the market expects a significant event.

**How to read it:**
- **Horizontal clusters** of high implied moves at one DTE = market expects a correlated event
  (FOMC, CPI, jobs report) to move everything at once
- **Single-asset spike** = company-specific event (earnings)
- **Declining implied moves** across expirations = no events expected, normal time-value decay
""")

        # Build implied move matrix: tickers x expirations
        event_rows = []
        all_dtes = set()
        for tk, td in ticker_data.items():
            spot = td["spot"]
            for exp in td["expirations"]:
                chain = td["chains"][exp]
                dte = _dte(exp)
                if dte <= 0:
                    continue
                all_dtes.add(dte)
                atm_k = _atm_strike(chain, spot)
                c = _get_contract(chain, atm_k, "call")
                p = _get_contract(chain, atm_k, "put")
                if c is not None and p is not None:
                    c_mid = ((c.get("bid", 0) or 0) + (c.get("ask", 0) or 0)) / 2
                    p_mid = ((p.get("bid", 0) or 0) + (p.get("ask", 0) or 0)) / 2
                    if c_mid <= 0:
                        c_mid = c.get("last_price", 0) or 0
                    if p_mid <= 0:
                        p_mid = p.get("last_price", 0) or 0
                    straddle = c_mid + p_mid
                    impl_move = straddle * 0.798 / spot * 100 if spot > 0 and straddle > 0 else 0
                    event_rows.append({
                        "Ticker": tk,
                        "DTE": dte,
                        "Expiration": exp,
                        "Impl Move %": round(impl_move, 1),
                    })

        if event_rows:
            ev_df = pd.DataFrame(event_rows)
            pivot_ev = ev_df.pivot_table(index="Ticker", columns="DTE", values="Impl Move %", aggfunc="mean")
            pivot_ev = pivot_ev.sort_index()

            fig_ev = go.Figure(go.Heatmap(
                z=pivot_ev.values,
                x=[f"{int(d)}d" for d in pivot_ev.columns],
                y=pivot_ev.index.tolist(),
                colorscale=[[0, "#1a1a2e"], [0.5, COLORS["warning"]], [1, COLORS["danger"]]],
                colorbar=dict(title="Impl Move %"),
                text=[[f"{v:.1f}%" if pd.notna(v) and v > 0 else "" for v in row] for row in pivot_ev.values],
                texttemplate="%{text}",
                textfont=dict(size=9),
                hovertemplate="Ticker: %{y}<br>DTE: %{x}<br>Impl Move: %{z:.1f}%<extra></extra>",
            ))
            fig_ev.update_layout(
                template="plotly_dark", height=max(400, len(pivot_ev) * 25),
                xaxis_title="Days to Expiration",
                margin=dict(l=0, r=0, t=10, b=50),
            )
            st.plotly_chart(fig_ev, use_container_width=True, config=PLOTLY_NOBAR)

            # Highest implied moves
            st.markdown("#### Largest Expected Moves")
            top_moves = ev_df.sort_values("Impl Move %", ascending=False).head(10)
            for _, row in top_moves.iterrows():
                st.caption(
                    f"**{row['Ticker']}** ({ALL_TICKERS.get(row['Ticker'], '')}) — "
                    f"{row['Impl Move %']:.1f}% implied move by {row['Expiration']} ({row['DTE']}d)"
                )
            # Incremental implied moves
            st.markdown("#### Incremental Event Moves")
            st.caption(
                "The *incremental* move between expirations isolates event premium at each horizon. "
                "A large incremental move at one DTE means the market expects a specific event between "
                "the prior and that expiration."
            )
            incr_rows = []
            for tk, td in ticker_data.items():
                for im in td.get("incr_moves", []):
                    if im["incr_move"] > 0.1:
                        incr_rows.append({"Ticker": tk, "Name": ALL_TICKERS.get(tk, ""),
                                           "Expiration": im["exp"], "DTE": im["dte"],
                                           "Incr Move %": round(im["incr_move"], 2)})
            if incr_rows:
                incr_df = pd.DataFrame(incr_rows).sort_values("Incr Move %", ascending=False)
                st.dataframe(
                    incr_df.head(15).style.format({"Incr Move %": "{:.2f}%"}),
                    use_container_width=True, hide_index=True,
                )
                # Flag clustered dates — cross-reference with centralized macro calendar
                if len(incr_df) > 3:
                    from src.economic_calendar import find_events_near_date

                    date_counts = incr_df.groupby("Expiration")["Ticker"].count()
                    hot_dates = date_counts[date_counts >= 3]
                    for exp_date, count in hot_dates.items():
                        macro_events = find_events_near_date(exp_date, window_days=5)
                        if macro_events:
                            event_str = ", ".join(e["name"] for e in macro_events)
                            st.warning(
                                f"**{exp_date}** — {count} assets show elevated moves. "
                                f"Confirmed macro events near this date: **{event_str}**."
                            )
                        else:
                            st.info(
                                f"**{exp_date}** — {count} assets show elevated moves. "
                                f"No major macro event identified — may be sector earnings clustering "
                                f"or options expiration positioning."
                            )
        else:
            st.warning("Could not compute implied moves — insufficient options data.")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 7 — IMPLIED CORRELATION
# ══════════════════════════════════════════════════════════════════════════════

with tab7:
    with error_boundary("Implied Correlation"):
        st.subheader("Implied Correlation & Dispersion")
        with st.expander("Why this matters"):
            st.markdown("""
**Implied correlation is the market's expectation of how correlated asset returns will be.**

It's derived from comparing index IV to component IVs:
- **High implied corr (>0.7):** Market expects a systemic move — everything drops or rallies together.
  Diversification won't help. Hedging with index puts is efficient.
- **Low implied corr (<0.4):** Market expects idiosyncratic moves — stock picking matters.
  Dispersion trades (sell index vol, buy single-stock vol) are attractive.

**Variance Risk Premium (VRP):** The difference between implied variance (IV²) and realized
variance (HV²). Positive VRP = selling vol has been profitable. This is the core edge
in systematic options strategies.

**Beta-Adjusted IV Residual:** After controlling for each asset's structural relationship to
SPY vol, what's left? Positive residual = asset's IV is richer than its beta would predict.
Negative = cheaper.
""")

        # Implied correlation gauge
        if implied_corr is not None:
            ic_color = COLORS["danger"] if implied_corr > 0.7 else (
                COLORS["warning"] if implied_corr > 0.5 else COLORS["success"])

            st.markdown(
                f'<div style="text-align:center;padding:24px;border:2px solid {ic_color};'
                f'border-radius:12px;margin-bottom:20px;">'
                f'<div style="font-size:14px;color:{COLORS["text_muted"]};margin-bottom:8px;">'
                f'IMPLIED CORRELATION</div>'
                f'<div style="font-size:48px;font-weight:bold;color:{ic_color};">'
                f'{implied_corr:.2f}</div>'
                f'<div style="font-size:12px;color:{COLORS["text_muted"]};margin-top:8px;">'
                f'SPY IV: {spy_iv*100:.1f}%'
                f'{f" | Avg Sector IV: {avg_sector_iv*100:.1f}%" if avg_sector_iv else ""}'
                f'</div></div>',
                unsafe_allow_html=True,
            )

            # Cross-reference with PCA realized correlation
            _pca_note = ""
            try:
                if 'pc1_var' in dir() and pc1_var is not None:
                    if implied_corr > 0.5 and pc1_var < 50:
                        _pca_note = (
                            " However, realized return PCA (Tab 4) shows lower correlation — "
                            "the options market is pricing MORE correlation than has recently occurred. "
                            "This divergence often precedes a macro catalyst."
                        )
                    elif implied_corr < 0.4 and pc1_var > 60:
                        _pca_note = (
                            " However, realized return PCA (Tab 4) shows higher correlation — "
                            "the options market expects sectors to diverge more than they have been."
                        )
            except Exception:
                pass

            if implied_corr > 0.7:
                st.error(f"High implied correlation — options market pricing systemic risk. Index hedges are efficient.{_pca_note}")
            elif implied_corr > 0.5:
                st.warning(f"Moderate implied correlation — options market expects some co-movement but not full lockstep.{_pca_note}")
            elif implied_corr < 0.35:
                st.success(f"Low implied correlation — options market expects sectors to diverge. Dispersion and stock-picking strategies favored.{_pca_note}")
            else:
                st.info(f"Normal implied correlation range — no extreme signal.{_pca_note}")
        else:
            st.info("Need SPY and 5+ sector ETFs to compute implied correlation.")

        # Variance Risk Premium
        st.markdown("#### Variance Risk Premium (IV² - HV²)")
        st.caption(
            "Positive = selling vol has been profitable (IV overstates future moves). "
            "Negative = vol has been underpriced (rare, happens before events)."
        )
        vrp_valid = mdf[mdf["VRP"].notna()].sort_values("VRP", ascending=False)
        if not vrp_valid.empty:
            vrp_colors = [COLORS["success"] if v > 0 else COLORS["danger"] for v in vrp_valid["VRP"]]
            fig_vrp = go.Figure(go.Bar(
                y=vrp_valid["Ticker"] + " (" + vrp_valid["Name"] + ")",
                x=vrp_valid["VRP"],
                orientation="h", marker_color=vrp_colors,
                text=[f"{v:+.1f}" for v in vrp_valid["VRP"]],
                textposition="outside",
            ))
            fig_vrp.add_vline(x=0, line_color=COLORS["text_muted"], line_width=0.5)
            fig_vrp.update_layout(
                template="plotly_dark", height=max(400, len(vrp_valid) * 25),
                xaxis_title="Variance Risk Premium (IV² - HV²) × 100",
                margin=dict(l=0, r=60, t=10, b=0),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig_vrp, use_container_width=True, config=PLOTLY_NOBAR)

            pos_vrp = (vrp_valid["VRP"] > 0).sum()
            st.caption(
                f"{pos_vrp}/{len(vrp_valid)} assets have positive VRP — "
                f"{'selling vol is broadly favorable' if pos_vrp > len(vrp_valid) * 0.6 else 'mixed; selective vol selling'}"
            )

        # Beta-adjusted IV residual
        st.markdown("#### Beta-Adjusted IV Residual")
        st.caption(
            "After adjusting for each asset's structural vol relationship to SPY, "
            "positive residual = IV is rich vs beta. Negative = IV is cheap."
        )
        resid_valid = mdf[mdf["IV Residual"].notna()].sort_values("IV Residual", ascending=False) if "IV Residual" in mdf.columns else pd.DataFrame()
        if not resid_valid.empty:
            res_colors = [COLORS["danger"] if v > 1 else (COLORS["success"] if v < -1 else COLORS["accent"])
                          for v in resid_valid["IV Residual"]]
            fig_res = go.Figure(go.Bar(
                y=resid_valid["Ticker"] + " (" + resid_valid["Name"] + ")",
                x=resid_valid["IV Residual"],
                orientation="h", marker_color=res_colors,
                text=[f"{v:+.1f}%" for v in resid_valid["IV Residual"]],
                textposition="outside",
            ))
            fig_res.add_vline(x=0, line_color=COLORS["text_muted"], line_width=0.5)
            fig_res.update_layout(
                template="plotly_dark", height=max(400, len(resid_valid) * 25),
                xaxis_title="IV Residual (% vs beta-adjusted expectation)",
                margin=dict(l=0, r=60, t=10, b=0),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(fig_res, use_container_width=True, config=PLOTLY_NOBAR)

            richest = resid_valid.head(3)
            cheapest = resid_valid.tail(3)
            rc1, rc2 = st.columns(2)
            with rc1:
                st.markdown("**Richest vs Beta:**")
                for _, r in richest.iterrows():
                    st.caption(f"{r['Ticker']} ({r['Name']}): {r['IV Residual']:+.1f}%")
            with rc2:
                st.markdown("**Cheapest vs Beta:**")
                for _, r in cheapest.iterrows():
                    st.caption(f"{r['Ticker']} ({r['Name']}): {r['IV Residual']:+.1f}%")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 8 — TRADE IDEAS
# ══════════════════════════════════════════════════════════════════════════════

with tab8:
    with error_boundary("Trade Ideas"):
        st.subheader("Actionable Trade Synthesis")
        with st.expander("How trade ideas are generated"):
            st.markdown("""
These are **systematic, data-driven trade ideas** synthesized from all the analysis on this page.
Each idea has a clear rationale based on the metrics we've computed:

- **Premium selling:** Assets with high IV/HV, high IV percentile, positive VRP
- **Protection buying:** Assets with low IV/HV, low IV percentile
- **Calendar spreads:** Assets with inverted term structure (sell rich front, buy cheap back)
- **Pairs/dispersion:** Assets on opposite sides of PCA loadings or with divergent skew
- **Event plays:** Assets with large incremental moves at specific dates

These are analytical signals, not recommendations. Always validate with your own research.
""")

        ideas = []

        # 1. Premium selling candidates
        _sell_candidates = mdf[
            (mdf["IV/HV"] > 1.15) &
            (mdf["IV Pctile"].fillna(0) > 60) &
            (mdf["VRP"].fillna(0) > 0)
        ].sort_values("IV/HV", ascending=False)
        for _, r in _sell_candidates.head(3).iterrows():
            _ivhv = r["IV/HV"] if pd.notna(r["IV/HV"]) else 0
            _pctile = r["IV Pctile"] if pd.notna(r["IV Pctile"]) else 0
            _vrp = r["VRP"] if pd.notna(r["VRP"]) else 0
            ideas.append({
                "Type": "Sell Premium",
                "Ticker": r["Ticker"],
                "Name": r["Name"],
                "Trade": f"Sell {r['Ticker']} ATM straddle or iron condor",
                "Rationale": (
                    f"IV/HV {_ivhv:.2f}x (rich), "
                    f"IV percentile {_pctile:.0f}%, "
                    f"VRP {_vrp:+.1f} (selling vol profitable)"
                ),
                "Strength": "Strong" if _ivhv > 1.3 else "Moderate",
            })

        # 2. Protection buying candidates
        _buy_candidates = mdf[
            (mdf["IV/HV"] < 0.85) &
            (mdf["IV Pctile"].fillna(100) < 30)
        ].sort_values("IV/HV")
        for _, r in _buy_candidates.head(3).iterrows():
            _ivhv = r["IV/HV"] if pd.notna(r["IV/HV"]) else 0
            _pctile = r["IV Pctile"] if pd.notna(r["IV Pctile"]) else 0
            ideas.append({
                "Type": "Buy Protection",
                "Ticker": r["Ticker"],
                "Name": r["Name"],
                "Trade": f"Buy {r['Ticker']} puts or put spreads",
                "Rationale": (
                    f"IV/HV {_ivhv:.2f}x (cheap), "
                    f"IV percentile {_pctile:.0f}% — "
                    f"protection is historically cheap"
                ),
                "Strength": "Strong" if _ivhv < 0.75 else "Moderate",
            })

        # 3. Calendar spread candidates (inverted term structure)
        _cal_candidates = mdf[mdf["TS Slope"].fillna(0) < -0.001].sort_values("TS Slope")
        for _, r in _cal_candidates.head(2).iterrows():
            ideas.append({
                "Type": "Calendar Spread",
                "Ticker": r["Ticker"],
                "Name": r["Name"],
                "Trade": f"Sell {r['Ticker']} front-month, buy back-month (calendar spread)",
                "Rationale": (
                    f"Term structure inverted ({r['TS Slope']*100:+.2f}%/mo). "
                    f"Front IV is richer than back — sell the rich month."
                ),
                "Strength": "Moderate",
            })

        # 4. Pairs from beta-adjusted residuals
        if "IV Residual" in mdf.columns:
            _resid = mdf[mdf["IV Residual"].notna()].sort_values("IV Residual")
            if len(_resid) >= 4:
                cheapest_pair = _resid.head(1).iloc[0]
                richest_pair = _resid.tail(1).iloc[0]
                if richest_pair["IV Residual"] - cheapest_pair["IV Residual"] > 3:
                    ideas.append({
                        "Type": "Relative Value",
                        "Ticker": f"{richest_pair['Ticker']}/{cheapest_pair['Ticker']}",
                        "Name": f"{richest_pair['Name']} vs {cheapest_pair['Name']}",
                        "Trade": (
                            f"Sell {richest_pair['Ticker']} vol, buy {cheapest_pair['Ticker']} vol "
                            f"(straddle spread)"
                        ),
                        "Rationale": (
                            f"{richest_pair['Ticker']} is {richest_pair['IV Residual']:+.1f}% rich vs beta, "
                            f"{cheapest_pair['Ticker']} is {cheapest_pair['IV Residual']:+.1f}% cheap. "
                            f"Spread: {richest_pair['IV Residual'] - cheapest_pair['IV Residual']:.1f}%."
                        ),
                        "Strength": "Strong" if richest_pair["IV Residual"] - cheapest_pair["IV Residual"] > 5 else "Moderate",
                    })

        # 5. Dispersion trade
        if implied_corr is not None and implied_corr < 0.40:
            ideas.append({
                "Type": "Dispersion",
                "Ticker": "SPY vs Sectors",
                "Name": "Index vs Components",
                "Trade": "Sell SPY straddle, buy sector ETF straddles (equal-vega weighted)",
                "Rationale": (
                    f"Implied correlation {implied_corr:.2f} is low — "
                    f"market expects sectors to diverge. Dispersion is historically profitable "
                    f"when implied corr < 0.40."
                ),
                "Strength": "Strong" if implied_corr < 0.30 else "Moderate",
            })

        if ideas:
            ideas_df = pd.DataFrame(ideas)

            # Summary counts
            ic1, ic2, ic3 = st.columns(3)
            ic1.metric("Total Ideas", len(ideas))
            ic2.metric("Strong Signals", len([i for i in ideas if i["Strength"] == "Strong"]))
            ic3.metric("Categories", len(ideas_df["Type"].unique()))

            # Display by type
            for trade_type in ideas_df["Type"].unique():
                st.markdown(f"#### {trade_type}")
                type_ideas = ideas_df[ideas_df["Type"] == trade_type]
                for _, idea in type_ideas.iterrows():
                    strength_color = COLORS["success"] if idea["Strength"] == "Strong" else COLORS["warning"]
                    st.markdown(
                        f'<div style="background:{COLORS["card_bg"]};border:1px solid {COLORS["card_border"]};'
                        f'border-radius:8px;padding:14px 18px;margin-bottom:10px;">'
                        f'<div style="display:flex;justify-content:space-between;align-items:center;">'
                        f'<span style="font-weight:700;color:{COLORS["text_primary"]};">'
                        f'{idea["Ticker"]} — {idea["Name"]}</span>'
                        f'<span style="color:{strength_color};font-size:0.8rem;font-weight:600;">'
                        f'{idea["Strength"]}</span></div>'
                        f'<div style="color:{COLORS["accent"]};margin:6px 0;font-size:0.9rem;">'
                        f'{idea["Trade"]}</div>'
                        f'<div style="color:{COLORS["text_muted"]};font-size:0.8rem;">'
                        f'{idea["Rationale"]}</div></div>',
                        unsafe_allow_html=True,
                    )
        else:
            st.info("No strong trade signals detected in current market conditions. "
                     "This is normal — not every scan produces actionable ideas.")


# ─── FOOTER ────────────────────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    "Cross-asset options analysis based on current market quotes. Implied volatility "
    "reflects market expectations, not predictions. Trade ideas are analytical signals "
    "derived from quantitative metrics, not recommendations. Not financial advice."
)
render_data_source_footer()
