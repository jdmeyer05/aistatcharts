import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from src.data_engine import get_expiration_dates, fetch_options_chain, fetch_massive_data, format_massive_ticker, render_data_source_footer
from src.chatbot import run_sidebar_chatbot
from src.layout import setup_page
from src.styles import COLORS
from src.options_models import fill_missing_options_data

setup_page("06_Options_Analysis")

st.title("Options Surface Analysis")
st.caption("IV skew, open interest walls, gamma exposure, max pain, unusual activity, and Greeks across strikes.")

# ── Sidebar Configuration ──
with st.sidebar:
    st.subheader("Chain Settings")
    raw_ticker = st.text_input("Underlying Ticker", value="SPY")
    ticker = format_massive_ticker(raw_ticker)

    if ":" in ticker or "ERCOT" in ticker.upper():
        st.error("Equities only.")
        st.stop()

    strike_range = st.slider("Strike Range (% from spot)", 5, 30, 15)
    submit = st.button("Fetch Chain Data", type="primary", use_container_width=True)

# ── Fetch & Store ──
# Auto-load SPY on first visit
if 'options_df' not in st.session_state:
    submit = True

if submit:
    all_expirations = get_expiration_dates(ticker)
    today_str = pd.Timestamp.now().strftime("%Y-%m-%d")
    expirations = [e for e in (all_expirations or []) if e >= today_str]

    if not expirations:
        st.error("No expirations found. Check ticker.")
        st.stop()

    with st.spinner(f"Fetching options data for {ticker}..."):
        px_df = fetch_massive_data(ticker, 5)
        current_px = float(px_df['Close'].iloc[-1]) if px_df is not None and not px_df.empty else None

        # Pre-fetch nearest 8 expirations for surface/term structure
        term_data = {}
        prefetch_exps = expirations[:8]
        progress = st.progress(0, text="Loading options chain...")
        for i, texp in enumerate(prefetch_exps):
            try:
                tdf = fetch_options_chain(ticker, texp)
                if tdf is not None and not tdf.empty:
                    if current_px:
                        tdf = fill_missing_options_data(tdf, current_px)
                    term_data[texp] = tdf
            except Exception:
                pass
            progress.progress((i + 1) / len(prefetch_exps), text=f"Loading {texp}...")
        progress.empty()

        if term_data:
            first_exp = list(term_data.keys())[0]
            st.session_state['options_df'] = term_data[first_exp]
            st.session_state['options_current_px'] = current_px
            st.session_state['options_ticker'] = ticker
            st.session_state['options_exp'] = first_exp
            # ALL expirations available for selection, not just pre-fetched
            st.session_state['options_expirations'] = expirations
            st.session_state['options_strike_range'] = strike_range
            st.session_state['options_term_data'] = term_data
            st.session_state['shared_options_ticker'] = ticker
            st.session_state['shared_options_spot'] = current_px
        else:
            st.error(f"Failed to fetch options data for {ticker}.")
            st.stop()

# ── Main Dashboard ──
if 'options_df' not in st.session_state:
    st.info("Enter a ticker and click **Fetch Chain Data** to begin.")
    st.stop()

current_px = st.session_state['options_current_px']
ticker_display = st.session_state['options_ticker']
available_exps = st.session_state.get('options_expirations', [])
x_range_pct = st.session_state.get('options_strike_range', 15) / 100

# Helper: get chain for a specific expiration — fetches on-demand if not pre-loaded
def _get_chain_for_exp(exp_key):
    term_data = st.session_state.get('options_term_data', {})
    if exp_key in term_data:
        return term_data[exp_key]

    # Not pre-loaded — fetch on demand
    ticker = st.session_state.get('options_ticker', '')
    spot = st.session_state.get('options_current_px')
    if ticker and exp_key:
        try:
            tdf = fetch_options_chain(ticker, exp_key)
            if tdf is not None and not tdf.empty:
                if spot:
                    tdf = fill_missing_options_data(tdf, spot)
                # Cache it so we don't re-fetch
                term_data[exp_key] = tdf
                st.session_state['options_term_data'] = term_data
                return tdf
        except Exception:
            pass

    return st.session_state.get('options_df', pd.DataFrame())

# Default expiration and filtered data
exp_display = st.session_state['options_exp']
df = _get_chain_for_exp(exp_display)


def _exp_selector(tab_key: str):
    """Render an expiration selector inside a tab. Returns (selected_exp, calls, puts) filtered to strike range."""
    sel_exp = st.selectbox("Expiration", available_exps, key=f"exp_{tab_key}",
                           index=available_exps.index(exp_display) if exp_display in available_exps else 0)
    tab_df = _get_chain_for_exp(sel_exp).sort_values('strike_price')
    tab_calls = tab_df[tab_df['contract_type'] == 'call'].copy()
    tab_puts = tab_df[tab_df['contract_type'] == 'put'].copy()
    if current_px:
        lo = current_px * (1 - x_range_pct)
        hi = current_px * (1 + x_range_pct)
        tab_calls = tab_calls[(tab_calls['strike_price'] >= lo) & (tab_calls['strike_price'] <= hi)]
        tab_puts = tab_puts[(tab_puts['strike_price'] >= lo) & (tab_puts['strike_price'] <= hi)]
    tab_dte = max(0, (pd.to_datetime(sel_exp) - pd.Timestamp.now()).days)
    return sel_exp, tab_calls, tab_puts, tab_dte

exp_df = df.sort_values('strike_price')

calls_raw = exp_df[exp_df['contract_type'] == 'call'].copy()
puts_raw = exp_df[exp_df['contract_type'] == 'put'].copy()

# Dynamic x-axis range centered on spot
if current_px is not None:
    x_min = current_px * (1 - x_range_pct)
    x_max = current_px * (1 + x_range_pct)
else:
    x_min = exp_df['strike_price'].min()
    x_max = exp_df['strike_price'].max()

# Filter to visible range
calls = calls_raw[(calls_raw['strike_price'] >= x_min) & (calls_raw['strike_price'] <= x_max)].copy()
puts = puts_raw[(puts_raw['strike_price'] >= x_min) & (puts_raw['strike_price'] <= x_max)].copy()


# ═══════════════════════════════════════════════
# KEY METRICS ROW
# ═══════════════════════════════════════════════

# ATM IV (closest strike to spot)
atm_call_iv = 0
atm_put_iv = 0
if current_px and not calls.empty:
    atm_idx = (calls['strike_price'] - current_px).abs().idxmin()
    atm_call_iv = calls.loc[atm_idx, 'implied_volatility']
if current_px and not puts.empty:
    atm_idx = (puts['strike_price'] - current_px).abs().idxmin()
    atm_put_iv = puts.loc[atm_idx, 'implied_volatility']
atm_iv = (atm_call_iv + atm_put_iv) / 2 if atm_call_iv and atm_put_iv else atm_call_iv or atm_put_iv

# Put/Call ratios
total_call_oi = calls['open_interest'].sum()
total_put_oi = puts['open_interest'].sum()
pc_oi_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else 0

total_call_vol = calls['volume'].sum()
total_put_vol = puts['volume'].sum()
pc_vol_ratio = total_put_vol / total_call_vol if total_call_vol > 0 else 0

# Max Pain — strike where total option holder losses are maximized
def calc_max_pain(calls_df, puts_df):
    strikes = sorted(set(calls_df['strike_price'].tolist() + puts_df['strike_price'].tolist()))
    if not strikes:
        return None
    min_pain = float('inf')
    max_pain_strike = strikes[0]
    for s in strikes:
        call_pain = calls_df.apply(lambda r: max(0, s - r['strike_price']) * r['open_interest'], axis=1).sum()
        put_pain = puts_df.apply(lambda r: max(0, r['strike_price'] - s) * r['open_interest'], axis=1).sum()
        total = call_pain + put_pain
        if total < min_pain:
            min_pain = total
            max_pain_strike = s
    return max_pain_strike

max_pain = calc_max_pain(calls, puts)

# Highest OI strikes
max_call_oi_strike = calls.loc[calls['open_interest'].idxmax(), 'strike_price'] if not calls.empty and calls['open_interest'].sum() > 0 else None
max_put_oi_strike = puts.loc[puts['open_interest'].idxmax(), 'strike_price'] if not puts.empty and puts['open_interest'].sum() > 0 else None

# Days to expiration
dte = max(0, (pd.to_datetime(exp_display) - pd.Timestamp.now()).days)

# Helper: get best price (mid if available, else lastPrice)
def _best_price(row):
    mid = (row.get('bid', 0) + row.get('ask', 0)) / 2
    if mid > 0:
        return mid
    return row.get('lastPrice', row.get('ask', 0))

# Expected move from ATM straddle
expected_move = 0
expected_move_pct = 0
if current_px and not calls.empty and not puts.empty:
    atm_call = calls.iloc[(calls['strike_price'] - current_px).abs().argsort()[:1]]
    atm_put = puts.iloc[(puts['strike_price'] - current_px).abs().argsort()[:1]]
    if not atm_call.empty and not atm_put.empty:
        c_price = _best_price(atm_call.iloc[0])
        p_price = _best_price(atm_put.iloc[0])
        atm_straddle = float(c_price + p_price)
        if atm_straddle <= 0 and atm_iv > 0 and current_px and dte > 0:
            # Fallback: estimate from IV if no price data
            atm_straddle = current_px * atm_iv * np.sqrt(max(dte, 1) / 365) * 2 * 0.4  # rough approximation
        expected_move = atm_straddle * 0.85
        expected_move_pct = (expected_move / current_px) * 100 if current_px else 0

# Total notional (volume × best price × 100)
if not calls.empty:
    calls['_mid'] = calls.apply(_best_price, axis=1)
    call_notional = (calls['volume'] * calls['_mid'] * 100).sum()
else:
    call_notional = 0
if not puts.empty:
    puts['_mid'] = puts.apply(_best_price, axis=1)
    put_notional = (puts['volume'] * puts['_mid'] * 100).sum()
else:
    put_notional = 0

with st.expander("Understanding the key metrics"):
    st.markdown("""
- **ATM IV** — At-the-money implied volatility. The market's expectation of annualized price movement. 30% IV ≈ expected daily move of ~1.9%.
- **Expected Move** — The implied ±range by expiration, derived from the ATM straddle price (×0.85 for 1 SD). The market expects the stock to stay within this range ~68% of the time.
- **P/C OI Ratio** — Put/Call open interest ratio. Above 1.0 = more put OI (bearish hedging or protection). Below 0.7 = complacency.
- **P/C Vol Ratio** — Put/Call volume ratio for today. Spikes above 1.5 can signal fear. Below 0.5 = bullish sentiment.
- **Max Pain** — The strike where option sellers profit most. Price gravitates here into expiration.
- **Call / Put Notional** — Estimated dollar value of today's options volume. Shows where the money is flowing.
""")

# Display metrics — row 1
m1, m2, m3, m4 = st.columns(4)
m1.metric("Spot", f"${current_px:,.2f}" if current_px else "—")
m2.metric("ATM IV", f"{atm_iv:.1%}" if atm_iv else "—")
m3.metric("Expected Move", f"±${expected_move:.2f} (±{expected_move_pct:.1f}%)" if expected_move else "—")
m4.metric("Max Pain", f"${max_pain:,.0f}" if max_pain else "—",
          f"{((max_pain / current_px) - 1) * 100:+.1f}% from spot" if max_pain and current_px else "")

# Display metrics — row 2
m5, m6, m7, m8 = st.columns(4)
m5.metric("P/C OI Ratio", f"{pc_oi_ratio:.2f}",
          "Bearish" if pc_oi_ratio > 1.0 else "Neutral" if pc_oi_ratio > 0.7 else "Bullish",
          delta_color="inverse" if pc_oi_ratio > 1.0 else "normal")
m6.metric("P/C Vol Ratio", f"{pc_vol_ratio:.2f}")
m7.metric("Call Notional", f"${call_notional:,.0f}")
m8.metric("Put Notional", f"${put_notional:,.0f}")

st.divider()


# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════
tab_surface, tab_skew, tab_term, tab_oi, tab_gex, tab_pain, tab_unusual, tab_greeks, tab_chain = st.tabs([
    "Vol Surface", "IV Skew", "Term Structure", "Open Interest", "Gamma Exposure",
    "Max Pain", "Unusual Activity", "Greeks Heatmap", "Chain View",
])


# ── Tab: 3D Surface ──
with tab_surface:
    SURFACE_METRICS = {
        "Implied Volatility": {
            "field": "implied_volatility",
            "z_title": "Implied Volatility",
            "z_format": ".0%",
            "colorscale": [
                [0, 'rgba(0,255,150,0.8)'], [0.3, 'rgba(0,209,255,0.8)'],
                [0.5, 'rgba(255,170,0,0.8)'], [0.7, 'rgba(255,107,53,0.9)'],
                [1, 'rgba(255,68,68,0.95)'],
            ],
            "hover_fmt": "IV: %{z:.1%}",
            "description": "Market's expectation of future price movement. Peaks = expensive options, valleys = cheap.",
        },
        "Price (Mid)": {
            "field": "mid_price",
            "z_title": "Option Price ($)",
            "z_format": "$.2f",
            "colorscale": [
                [0, 'rgba(0,100,50,0.8)'], [0.3, 'rgba(0,209,255,0.7)'],
                [0.6, 'rgba(255,170,0,0.8)'], [1, 'rgba(255,255,255,0.9)'],
            ],
            "hover_fmt": "Price: $%{z:.2f}",
            "description": "Mid-market option price. Shows how value decays with distance from ATM and time.",
        },
        "Delta": {
            "field": "delta",
            "z_title": "Delta",
            "z_format": ".2f",
            "colorscale": [
                [0, 'rgba(255,68,68,0.9)'], [0.5, 'rgba(48,54,61,0.5)'],
                [1, 'rgba(0,255,150,0.9)'],
            ],
            "hover_fmt": "Delta: %{z:.3f}",
            "description": "Directional exposure per $1 move. Call deltas 0→1, put deltas -1→0. ATM = ±0.50.",
        },
        "Gamma": {
            "field": "gamma",
            "z_title": "Gamma",
            "z_format": ".4f",
            "colorscale": [
                [0, 'rgba(14,17,23,0.5)'], [0.3, 'rgba(0,100,200,0.6)'],
                [0.6, 'rgba(0,209,255,0.8)'], [1, 'rgba(255,255,255,0.95)'],
            ],
            "hover_fmt": "Gamma: %{z:.4f}",
            "description": "Rate of delta change. Peaks at ATM near expiry — where hedging risk is greatest.",
        },
        "Theta": {
            "field": "theta",
            "z_title": "Theta ($/day)",
            "z_format": ".3f",
            "colorscale": [
                [0, 'rgba(255,68,68,0.95)'], [0.5, 'rgba(255,170,0,0.7)'],
                [1, 'rgba(0,255,150,0.5)'],
            ],
            "hover_fmt": "Theta: %{z:.4f}",
            "description": "Daily time decay. Most negative at ATM near expiry. This is the cost of holding options.",
        },
        "Vega": {
            "field": "vega",
            "z_title": "Vega",
            "z_format": ".3f",
            "colorscale": [
                [0, 'rgba(14,17,23,0.5)'], [0.3, 'rgba(100,0,200,0.6)'],
                [0.6, 'rgba(173,127,255,0.8)'], [1, 'rgba(255,255,255,0.95)'],
            ],
            "hover_fmt": "Vega: %{z:.4f}",
            "description": "Sensitivity to IV changes. Highest at ATM with long DTE — where vol bets have most leverage.",
        },
    }

    metric_choice = st.selectbox("Surface Metric", list(SURFACE_METRICS.keys()), key="surface_metric")
    metric_cfg = SURFACE_METRICS[metric_choice]

    st.subheader(f"3D {metric_choice} Surface")
    st.caption(metric_cfg["description"])

    with st.expander("What is this & how to use it?"):
        st.markdown(f"""
**What it shows:** A 3D surface mapping **{metric_choice}** across **strike price** (x-axis) and **expiration date** (y-axis). The height and color represent the value at each point.

**How to read it:**
- **Peaks** = highest values. **Valleys** = lowest values.
- **Strike axis** (left-right) shows how the metric changes across moneyness (ITM → ATM → OTM).
- **Expiration axis** (front-back) shows how it changes across time horizons.
- **Drag to rotate**, scroll to zoom. Find the angle that reveals the structure best.

**Trading signals:**
- Look for **dislocations** — points where the surface is unusually high or low compared to neighbors.
- Compare the shape to what you'd expect — unusual bumps or dips may indicate mispriced options.
- The **spot price line** (yellow) shows where the underlying currently sits on the surface.
""")

    term_data = st.session_state.get('options_term_data', {})
    if term_data and current_px:
        # Build the surface grid for all metrics
        surface_rows = []
        sorted_exps = sorted(term_data.keys())

        # Choose calls for OTM above spot, puts for OTM below spot (standard convention)
        for texp in sorted_exps:
            tdf = term_data[texp]
            tdf_sorted = tdf.sort_values('strike_price')
            t_calls = tdf_sorted[tdf_sorted['contract_type'] == 'call']
            t_puts = tdf_sorted[tdf_sorted['contract_type'] == 'put']

            if t_calls.empty and t_puts.empty:
                continue

            seen_strikes = set()

            # Calls (ATM and above)
            for _, row in t_calls.iterrows():
                k = row['strike_price']
                if x_min <= k <= x_max and row.get('implied_volatility', 0) > 0:
                    mid = (row.get('bid', 0) + row.get('ask', 0)) / 2
                    if mid <= 0:
                        mid = row.get('last_price', 0)
                    surface_rows.append({
                        'strike': k, 'expiration': texp,
                        'implied_volatility': row.get('implied_volatility', 0),
                        'mid_price': mid,
                        'delta': row.get('delta', 0),
                        'gamma': row.get('gamma', 0),
                        'theta': row.get('theta', 0),
                        'vega': row.get('vega', 0),
                    })
                    seen_strikes.add(k)

            # OTM puts (below spot) for skew coverage
            for _, row in t_puts.iterrows():
                k = row['strike_price']
                if x_min <= k < current_px * 0.98 and k not in seen_strikes and row.get('implied_volatility', 0) > 0:
                    mid = (row.get('bid', 0) + row.get('ask', 0)) / 2
                    if mid <= 0:
                        mid = row.get('last_price', 0)
                    surface_rows.append({
                        'strike': k, 'expiration': texp,
                        'implied_volatility': row.get('implied_volatility', 0),
                        'mid_price': mid,
                        'delta': row.get('delta', 0),
                        'gamma': row.get('gamma', 0),
                        'theta': row.get('theta', 0),
                        'vega': row.get('vega', 0),
                    })

        surface_df = pd.DataFrame(surface_rows) if surface_rows else pd.DataFrame()

        if not surface_df.empty:
            field = metric_cfg["field"]

            # Pivot to grid
            pivot = surface_df.pivot_table(index='expiration', columns='strike', values=field, aggfunc='mean')
            pivot = pivot.sort_index()
            pivot = pivot.interpolate(axis=1, limit_direction='both')
            pivot = pivot.interpolate(axis=0, limit_direction='both')

            z_vals = pivot.values
            z_clean = z_vals[~np.isnan(z_vals)]
            z_min = float(z_clean.min()) if len(z_clean) > 0 else 0
            z_max = float(z_clean.max()) if len(z_clean) > 0 else 1
            z_pad = (z_max - z_min) * 0.1 if z_max != z_min else 0.1

            fig_surface = go.Figure(data=[go.Surface(
                x=pivot.columns.tolist(),
                y=list(range(len(pivot.index))),
                z=z_vals,
                colorscale=metric_cfg["colorscale"],
                colorbar=dict(
                    title=dict(text=metric_cfg["z_title"], font=dict(color="white")),
                    tickformat=metric_cfg["z_format"],
                    len=0.6, thickness=15, x=1.02,
                ),
                contours=dict(
                    z=dict(show=True, usecolormap=True, highlightcolor="white", project_z=True),
                ),
                lighting=dict(ambient=0.6, diffuse=0.5, specular=0.3, roughness=0.5),
                opacity=0.92,
                hovertemplate=f"Strike: $%{{x:,.0f}}<br>{metric_cfg['hover_fmt']}<extra></extra>",
            )])

            # Spot price line on the surface
            if current_px:
                spot_col_idx = (np.abs(np.array(pivot.columns) - current_px)).argmin()
                spot_vals = z_vals[:, spot_col_idx]
                fig_surface.add_trace(go.Scatter3d(
                    x=[current_px] * len(pivot.index),
                    y=list(range(len(pivot.index))),
                    z=[float(v) for v in spot_vals],
                    mode='lines+markers',
                    line=dict(color='#ffaa00', width=5),
                    marker=dict(size=3, color='#ffaa00'),
                    name=f'Spot ${current_px:,.0f}',
                ))

            # DTE labels
            exp_labels = []
            for e in pivot.index.tolist():
                d = max(0, (pd.to_datetime(e) - pd.Timestamp.now()).days)
                exp_labels.append(f"{e[5:]} ({d}d)")

            fig_surface.update_layout(
                template="plotly_dark", height=600,
                margin=dict(t=10, b=10, l=10, r=10),
                scene=dict(
                    xaxis=dict(title="Strike ($)", backgroundcolor="rgba(14,17,23,0.8)",
                               gridcolor="rgba(48,54,61,0.4)", showbackground=True),
                    yaxis=dict(title="Expiration", tickvals=list(range(len(pivot.index))),
                               ticktext=exp_labels, backgroundcolor="rgba(14,17,23,0.8)",
                               gridcolor="rgba(48,54,61,0.4)", showbackground=True),
                    zaxis=dict(title=metric_cfg["z_title"], tickformat=metric_cfg["z_format"],
                               backgroundcolor="rgba(14,17,23,0.8)", gridcolor="rgba(48,54,61,0.4)",
                               showbackground=True, range=[z_min - z_pad, z_max + z_pad]),
                    camera=dict(eye=dict(x=1.8, y=-1.4, z=0.9), up=dict(x=0, y=0, z=1)),
                    aspectratio=dict(x=1.5, y=1, z=0.6),
                ),
                legend=dict(x=0, y=1, bgcolor="rgba(0,0,0,0.5)", font=dict(color="white")),
            )
            st.plotly_chart(fig_surface, use_container_width=True)

            # Surface stats
            sc1, sc2, sc3, sc4 = st.columns(4)
            if "%" in metric_cfg["z_format"]:
                sc1.metric(f"Min", f"{z_min:.1%}")
                sc2.metric(f"Max", f"{z_max:.1%}")
                sc3.metric("Range", f"{(z_max - z_min):.1%}")
            elif "$" in metric_cfg["z_format"]:
                sc1.metric(f"Min", f"${z_min:.2f}")
                sc2.metric(f"Max", f"${z_max:.2f}")
                sc3.metric("Range", f"${(z_max - z_min):.2f}")
            else:
                sc1.metric(f"Min", f"{z_min:.4f}")
                sc2.metric(f"Max", f"{z_max:.4f}")
                sc3.metric("Range", f"{(z_max - z_min):.4f}")
            sc4.metric("Expirations", f"{len(pivot.index)}")

            # ATM values by expiration
            if current_px:
                st.divider()
                st.markdown(f"**ATM {metric_choice} by Expiration**")
                atm_vals = []
                for i, exp_name in enumerate(pivot.index):
                    val = float(z_vals[i, spot_col_idx])
                    d = max(0, (pd.to_datetime(exp_name) - pd.Timestamp.now()).days)
                    if not np.isnan(val):
                        atm_vals.append({"Expiration": exp_name, "DTE": d, metric_choice: val})
                if atm_vals:
                    atm_df = pd.DataFrame(atm_vals)
                    col_cfg = {}
                    if "%" in metric_cfg["z_format"]:
                        col_cfg[metric_choice] = st.column_config.NumberColumn(metric_choice, format="%.1%%")
                    elif "$" in metric_cfg["z_format"]:
                        col_cfg[metric_choice] = st.column_config.NumberColumn(metric_choice, format="$%.2f")
                    else:
                        col_cfg[metric_choice] = st.column_config.NumberColumn(metric_choice, format="%.4f")
                    st.dataframe(atm_df, use_container_width=True, hide_index=True, column_config=col_cfg)
        else:
            st.info("Not enough data to build the surface. Try fetching with more expirations available.")
    else:
        st.info("Volatility surface requires multiple expirations. Fetch chain data to load.")


# ── Tab: IV Smile / Skew ──
with tab_skew:
    _skew_exp, calls, puts, dte = _exp_selector("skew")
    st.subheader("Implied Volatility Smile")
    with st.expander("What is this & how to use it?"):
        st.markdown("""
**What it shows:** Implied Volatility (IV) for each strike price, plotted as a curve for both calls and puts. The "smile" shape reveals how the market prices risk at different price levels.

**How to read it:**
- **Steep left skew** (puts more expensive) = market is hedging against a crash. Common in equities.
- **Flat smile** = market sees equal risk up and down. Common in commodities.
- **Right skew** (calls more expensive) = market expects an upside breakout. Rare in equities, common in takeover targets.
- The **yellow dotted line** marks the current spot price.

**Trading signals:**
- If put skew is unusually steep vs. history, downside protection is expensive → consider selling put spreads.
- If the smile flattens suddenly, it may signal complacency before a move.
- Compare ATM IV to historical volatility — if IV >> realized vol, options are expensive (favor selling). If IV << realized vol, options are cheap (favor buying).
""")

    fig_iv = go.Figure()
    fig_iv.add_trace(go.Scatter(
        x=calls['strike_price'], y=calls['implied_volatility'],
        mode='lines+markers', name='Calls', line=dict(color='#00ff96', width=2),
        marker=dict(size=4),
    ))
    fig_iv.add_trace(go.Scatter(
        x=puts['strike_price'], y=puts['implied_volatility'],
        mode='lines+markers', name='Puts', line=dict(color='#ff4b4b', width=2),
        marker=dict(size=4),
    ))
    if current_px:
        fig_iv.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00",
                         annotation_text=f"Spot ${current_px:.2f}")

    # Dynamic axis ranges based on actual visible data
    all_iv = pd.concat([calls['implied_volatility'], puts['implied_volatility']]).dropna()
    all_iv = all_iv[all_iv > 0]
    if not all_iv.empty:
        iv_data_min = all_iv.min()
        iv_data_max = all_iv.quantile(0.98)
        iv_pad = (iv_data_max - iv_data_min) * 0.15
        iv_min = max(0, iv_data_min - iv_pad)
        iv_max = iv_data_max + iv_pad
    else:
        iv_min, iv_max = 0, 1

    all_strikes = pd.concat([calls['strike_price'], puts['strike_price']])
    x_lo = all_strikes.min() - (all_strikes.max() - all_strikes.min()) * 0.02
    x_hi = all_strikes.max() + (all_strikes.max() - all_strikes.min()) * 0.02

    fig_iv.update_layout(
        template="plotly_dark", height=420, margin=dict(t=30, b=40, l=50, r=20),
        yaxis_title="Implied Volatility", xaxis_title="Strike",
        xaxis=dict(range=[x_lo, x_hi]),
        yaxis=dict(range=[iv_min, iv_max], tickformat=".0%"),
        hovermode="x unified",
    )
    st.plotly_chart(fig_iv, use_container_width=True)

    # Skew metrics
    if not calls.empty and not puts.empty and current_px:
        otm_put_iv = puts[puts['strike_price'] < current_px * 0.95]['implied_volatility'].mean()
        otm_call_iv = calls[calls['strike_price'] > current_px * 1.05]['implied_volatility'].mean()
        if otm_put_iv and otm_call_iv and atm_iv:
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("25D Put Skew", f"{otm_put_iv - atm_iv:.1%}" if not np.isnan(otm_put_iv) else "—")
            sc2.metric("25D Call Skew", f"{otm_call_iv - atm_iv:.1%}" if not np.isnan(otm_call_iv) else "—")
            sc3.metric("Skew (Put - Call)", f"{(otm_put_iv - otm_call_iv):.1%}" if not np.isnan(otm_put_iv - otm_call_iv) else "—")


# ── Tab: IV Term Structure ──
with tab_term:
    st.subheader("IV Term Structure")
    with st.expander("What is this & how to use it?"):
        st.markdown("""
**What it shows:** ATM implied volatility plotted across multiple expiration dates. This is the "term structure" of volatility — how the market prices risk at different time horizons.

**How to read it:**
- **Upward sloping (contango)** = longer-dated IV > short-dated IV. Normal market — uncertainty grows with time.
- **Inverted (backwardation)** = short-dated IV > longer-dated IV. The market expects a near-term event (earnings, FOMC, catalyst) to cause a big move, then volatility normalizes after.
- **Kink at a specific date** = there's a known event on that date. The IV "prices in" that event.

**Trading signals:**
- **Inverted term structure before earnings:** Front-month IV is inflated. If you think the move will be smaller than implied, sell front-month straddles and hedge with back-month (calendar spread).
- **Steep contango:** Front-month options are cheap relative to back-month. Good for buying short-dated directional bets.
- **Flat term structure:** No near-term catalysts expected. Good for selling premium across the curve.
""")

    term_data = st.session_state.get('options_term_data', {})
    if term_data and current_px:
        term_ivs = []
        term_expected_moves = []
        for texp, tdf in sorted(term_data.items()):
            tdf_sorted = tdf.sort_values('strike_price')
            t_calls = tdf_sorted[tdf_sorted['contract_type'] == 'call']
            t_puts = tdf_sorted[tdf_sorted['contract_type'] == 'put']

            # ATM IV for this expiration
            if not t_calls.empty:
                atm_c = t_calls.iloc[(t_calls['strike_price'] - current_px).abs().argsort()[:1]]
                atm_c_iv = float(atm_c['implied_volatility'].iloc[0])
            else:
                atm_c_iv = 0
            if not t_puts.empty:
                atm_p = t_puts.iloc[(t_puts['strike_price'] - current_px).abs().argsort()[:1]]
                atm_p_iv = float(atm_p['implied_volatility'].iloc[0])
            else:
                atm_p_iv = 0
            avg_iv = (atm_c_iv + atm_p_iv) / 2 if atm_c_iv and atm_p_iv else atm_c_iv or atm_p_iv

            t_dte = max(1, (pd.to_datetime(texp) - pd.Timestamp.now()).days)

            # Expected move for this expiration
            t_em = 0
            if not t_calls.empty and not t_puts.empty:
                tc = t_calls.iloc[(t_calls['strike_price'] - current_px).abs().argsort()[:1]]
                tp = t_puts.iloc[(t_puts['strike_price'] - current_px).abs().argsort()[:1]]
                t_straddle = float(tc['ask'].iloc[0] + tp['ask'].iloc[0])
                t_em = t_straddle * 0.85

            if avg_iv > 0:
                term_ivs.append({"expiration": texp, "atm_iv": avg_iv, "dte": t_dte})
            if t_em > 0:
                term_expected_moves.append({"expiration": texp, "expected_move": t_em,
                                            "move_pct": (t_em / current_px) * 100, "dte": t_dte})

        if term_ivs:
            tiv_df = pd.DataFrame(term_ivs)

            fig_term = go.Figure()
            fig_term.add_trace(go.Scatter(
                x=tiv_df['expiration'], y=tiv_df['atm_iv'],
                mode='lines+markers', name='ATM IV',
                line=dict(color='#00d1ff', width=2.5),
                marker=dict(size=8),
                hovertemplate="Exp: %{x}<br>IV: %{y:.1%}<br>DTE: %{customdata}<extra></extra>",
                customdata=tiv_df['dte'],
            ))

            # Highlight selected expiration
            sel_row = tiv_df[tiv_df['expiration'] == exp_display]
            if not sel_row.empty:
                fig_term.add_trace(go.Scatter(
                    x=sel_row['expiration'], y=sel_row['atm_iv'],
                    mode='markers', name='Selected',
                    marker=dict(size=14, color='#ffaa00', symbol='star'),
                ))

            fig_term.update_layout(
                template="plotly_dark", height=380, margin=dict(t=30, b=40, l=50, r=20),
                yaxis_title="ATM Implied Volatility", xaxis_title="Expiration",
                hovermode="x unified",
            )
            st.plotly_chart(fig_term, use_container_width=True)

            # Shape assessment
            if len(tiv_df) >= 2:
                front_iv = tiv_df['atm_iv'].iloc[0]
                back_iv = tiv_df['atm_iv'].iloc[-1]
                if front_iv > back_iv * 1.05:
                    shape = "Inverted (backwardation)"
                    shape_color = "#ff4b4b"
                    shape_note = "Front-month IV elevated — near-term event expected"
                elif back_iv > front_iv * 1.05:
                    shape = "Contango (normal)"
                    shape_color = "#00ff96"
                    shape_note = "Normal upward slope — no unusual near-term risk"
                else:
                    shape = "Flat"
                    shape_color = "#ffaa00"
                    shape_note = "Similar IV across expirations — no strong catalyst priced"

                ts1, ts2 = st.columns(2)
                ts1.metric("Term Structure Shape", shape)
                ts2.markdown(f'<div style="padding:10px; border:1px solid {shape_color}; border-radius:6px; color:{shape_color};">{shape_note}</div>', unsafe_allow_html=True)

            # Expected move by expiration
            if term_expected_moves:
                st.divider()
                st.markdown("**Expected Move by Expiration**")
                em_df = pd.DataFrame(term_expected_moves)
                st.dataframe(em_df, use_container_width=True, hide_index=True,
                             column_config={
                                 "expiration": "Expiration",
                                 "expected_move": st.column_config.NumberColumn("±Move ($)", format="$%.2f"),
                                 "move_pct": st.column_config.NumberColumn("±Move (%)", format="%.1f%%"),
                                 "dte": "DTE",
                             })
    else:
        st.info("Term structure requires multiple expirations. Re-fetch to load.")


# ── Tab: Open Interest Profile ──
with tab_oi:
    _oi_exp, calls, puts, dte = _exp_selector("oi")
    st.subheader("Open Interest Profile")
    with st.expander("What is this & how to use it?"):
        st.markdown("""
**What it shows:** Open Interest (OI) at each strike — the total number of outstanding contracts. Large OI concentrations act as "walls" that influence price behavior.

**How to read it:**
- **Tall green bars (call OI)** above spot = resistance levels. Market makers who sold these calls will hedge by selling stock as price approaches, creating selling pressure.
- **Tall red bars (put OI)** below spot = support levels. Market makers who sold these puts will hedge by buying stock as price drops, creating buying pressure.
- **Max Pain (blue dotted line)** = the strike where the most options expire worthless. Price tends to drift toward max pain as expiration approaches.

**Trading signals:**
- Large OI concentrations at a strike = price is likely to be "pinned" near that level into expiration.
- If spot is between two large OI walls, expect range-bound trading.
- A break through a major OI wall often leads to an accelerated move (gamma squeeze).
""")

    fig_oi = go.Figure()
    fig_oi.add_trace(go.Bar(
        x=calls['strike_price'], y=calls['open_interest'],
        name='Call OI', marker_color='#00ff96', opacity=0.85,
    ))
    fig_oi.add_trace(go.Bar(
        x=puts['strike_price'], y=puts['open_interest'],
        name='Put OI', marker_color='#ff4b4b', opacity=0.85,
    ))
    if current_px:
        fig_oi.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
    oi_max_pain = calc_max_pain(calls, puts)
    if oi_max_pain:
        fig_oi.add_vline(x=oi_max_pain, line_dash="dash", line_color="#00d1ff", annotation_text="Max Pain")

    oi_cap = pd.concat([calls['open_interest'], puts['open_interest']]).quantile(0.95)
    fig_oi.update_layout(
        template="plotly_dark", height=420, margin=dict(t=30, b=40, l=50, r=20),
        barmode='group', yaxis_title="Open Interest", xaxis_title="Strike",
        bargap=0.1, bargroupgap=0.05,
        xaxis=dict(range=[x_min, x_max]),
        yaxis=dict(range=[0, oi_cap * 1.1] if oi_cap > 0 else None),
        hovermode="x unified",
    )
    st.plotly_chart(fig_oi, use_container_width=True)

    # OI concentration
    if max_call_oi_strike and max_put_oi_strike:
        oc1, oc2 = st.columns(2)
        oc1.metric("Highest Call OI Strike", f"${max_call_oi_strike:,.0f}",
                   f"{((max_call_oi_strike / current_px) - 1) * 100:+.1f}% from spot" if current_px else "")
        oc2.metric("Highest Put OI Strike", f"${max_put_oi_strike:,.0f}",
                   f"{((max_put_oi_strike / current_px) - 1) * 100:+.1f}% from spot" if current_px else "")


# ── Tab: Gamma Exposure (GEX) ──
with tab_gex:
    _gex_exp, calls, puts, dte = _exp_selector("gex")
    st.subheader("Net Gamma Exposure by Strike")
    with st.expander("What is this & how to use it?"):
        st.markdown("""
**What it shows:** Net Gamma Exposure (GEX) estimates how market makers (dealers) will hedge at each strike. This reveals whether dealer hedging will **dampen** or **amplify** price moves.

**How to read it:**
- **Green bars (positive GEX)** = dealers are long gamma. They sell into rallies and buy dips → price is **pinned**, low volatility expected.
- **Red bars (negative GEX)** = dealers are short gamma. They buy into rallies and sell dips → price moves are **amplified**, high volatility expected.
- **GEX Flip Point** = the strike where dealer positioning flips from long to short gamma. Above this level, moves are dampened. Below it, moves accelerate.

**Trading signals:**
- **Positive total GEX regime:** Favor mean-reversion strategies, sell premium, expect tight ranges.
- **Negative total GEX regime:** Favor breakout strategies, buy premium, expect large moves.
- If spot is near the GEX flip point, expect a regime change in volatility behavior.
- The largest GEX bar is the strongest "magnet" strike for price action.
""")

    # GEX = gamma × OI × 100 × spot^2 × 0.01
    # Calls: dealers are short gamma (negative), Puts: dealers are long gamma (positive)
    # Net GEX per strike = (call_gamma × call_OI - put_gamma × put_OI) × 100 × spot
    if current_px:
        gex_data = []
        all_strikes = sorted(set(calls['strike_price'].tolist() + puts['strike_price'].tolist()))
        for strike in all_strikes:
            if strike < x_min or strike > x_max:
                continue
            c = calls[calls['strike_price'] == strike]
            p = puts[puts['strike_price'] == strike]
            call_gex = float(c['gamma'].iloc[0] * c['open_interest'].iloc[0]) if not c.empty else 0
            put_gex = float(p['gamma'].iloc[0] * p['open_interest'].iloc[0]) if not p.empty else 0
            # Dealers short calls, long puts → net = put_gex - call_gex for dealer perspective
            net_gex = (call_gex - put_gex) * 100 * current_px * 0.01
            gex_data.append({"strike": strike, "net_gex": net_gex, "call_gex": call_gex * 100 * current_px * 0.01, "put_gex": put_gex * 100 * current_px * 0.01})

        gex_df = pd.DataFrame(gex_data)

        if not gex_df.empty:
            colors = ['#00ff96' if v >= 0 else '#ff4b4b' for v in gex_df['net_gex']]

            fig_gex = go.Figure()
            fig_gex.add_trace(go.Bar(
                x=gex_df['strike'], y=gex_df['net_gex'],
                marker_color=colors, name="Net GEX",
                hovertemplate="Strike: $%{x}<br>Net GEX: $%{y:,.0f}<extra></extra>",
            ))
            fig_gex.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
            fig_gex.add_hline(y=0, line_color="white", line_width=0.5)

            fig_gex.update_layout(
                template="plotly_dark", height=420, margin=dict(t=30, b=40, l=50, r=20),
                yaxis_title="Net Gamma Exposure ($)", xaxis_title="Strike",
                hovermode="x unified",
            )
            st.plotly_chart(fig_gex, use_container_width=True)

            # GEX flip point
            total_gex = gex_df['net_gex'].sum()
            gex_flip = None
            for i in range(1, len(gex_df)):
                if gex_df['net_gex'].iloc[i-1] * gex_df['net_gex'].iloc[i] < 0:
                    gex_flip = gex_df['strike'].iloc[i]
                    break

            gc1, gc2, gc3 = st.columns(3)
            gc1.metric("Total Net GEX", f"${total_gex:,.0f}",
                       "Positive (pinning)" if total_gex > 0 else "Negative (unstable)")
            gc2.metric("GEX Flip Point", f"${gex_flip:,.0f}" if gex_flip else "—")
            gc3.metric("Regime", "Dealer Long Gamma" if total_gex > 0 else "Dealer Short Gamma",
                       delta_color="normal" if total_gex > 0 else "inverse")
    else:
        st.warning("Spot price unavailable — cannot compute GEX.")


# ── Tab: Max Pain ──
with tab_pain:
    _pain_exp, calls, puts, dte = _exp_selector("pain")
    st.subheader("Max Pain Analysis")
    with st.expander("What is this & how to use it?"):
        st.markdown("""
**What it shows:** Max Pain is the strike price where the total dollar value of all outstanding options (calls + puts) would expire worthless, causing maximum loss to option holders and maximum profit to option sellers (typically market makers).

**How to read it:**
- **Yellow line (Total Pain)** = combined call + put holder losses at each strike. The lowest point is max pain.
- **Green area (Call Pain)** = how much call holders lose if price settles at each strike.
- **Red area (Put Pain)** = how much put holders lose if price settles at each strike.
- **Blue dotted line** = current spot. **Yellow dashed line** = max pain strike.

**Trading signals:**
- Price tends to gravitate toward max pain in the **final 2-3 days** before expiration (the "max pain magnet" effect).
- This effect is strongest on monthly OpEx (third Friday) and weakest on weekly expirations.
- If spot is far from max pain with many DTE, the magnet effect is weak. As DTE shrinks, it strengthens.
- Max pain is most useful as a **range estimate**, not a precise target. Expect price to settle within ±1-2% of max pain at expiry.
""")

    max_pain = calc_max_pain(calls, puts)
    if max_pain and current_px:
        # Calculate pain at each strike
        pain_data = []
        strikes = sorted(set(calls['strike_price'].tolist() + puts['strike_price'].tolist()))
        for s in strikes:
            if s < x_min or s > x_max:
                continue
            call_pain = calls.apply(lambda r: max(0, s - r['strike_price']) * r['open_interest'], axis=1).sum()
            put_pain = puts.apply(lambda r: max(0, r['strike_price'] - s) * r['open_interest'], axis=1).sum()
            pain_data.append({"strike": s, "call_pain": call_pain, "put_pain": put_pain, "total": call_pain + put_pain})

        pain_df = pd.DataFrame(pain_data)

        fig_pain = go.Figure()
        fig_pain.add_trace(go.Scatter(
            x=pain_df['strike'], y=pain_df['call_pain'],
            mode='lines', name='Call Holder Pain', line=dict(color='#00ff96', width=1.5),
            fill='tozeroy', fillcolor='rgba(0,255,150,0.08)',
        ))
        fig_pain.add_trace(go.Scatter(
            x=pain_df['strike'], y=pain_df['put_pain'],
            mode='lines', name='Put Holder Pain', line=dict(color='#ff4b4b', width=1.5),
            fill='tozeroy', fillcolor='rgba(255,75,75,0.08)',
        ))
        fig_pain.add_trace(go.Scatter(
            x=pain_df['strike'], y=pain_df['total'],
            mode='lines', name='Total Pain', line=dict(color='#ffaa00', width=2.5),
        ))
        fig_pain.add_vline(x=current_px, line_dash="dot", line_color="#00d1ff", annotation_text="Spot")
        fig_pain.add_vline(x=max_pain, line_dash="dash", line_color="#ffaa00", annotation_text=f"Max Pain ${max_pain:,.0f}")

        fig_pain.update_layout(
            template="plotly_dark", height=420, margin=dict(t=30, b=40, l=50, r=20),
            yaxis_title="Total Pain ($)", xaxis_title="Strike",
            hovermode="x unified",
        )
        st.plotly_chart(fig_pain, use_container_width=True)

        pc1, pc2, pc3 = st.columns(3)
        pc1.metric("Max Pain Strike", f"${max_pain:,.0f}")
        pc2.metric("Distance from Spot", f"{((max_pain / current_px) - 1) * 100:+.1f}%")
        pc3.metric("Days to Expiry", f"{dte}")
    else:
        st.warning("Cannot compute max pain — missing data.")


# ── Tab: Unusual Activity ──
with tab_unusual:
    _unusual_exp, calls, puts, dte = _exp_selector("unusual")
    st.subheader("Unusual Options Activity")
    with st.expander("What is this & how to use it?"):
        st.markdown("""
**What it shows:** Strikes where today's trading volume is significantly higher than open interest (Vol/OI > 2x). This indicates **new positions being opened**, not just existing positions being traded.

**How to read it:**
- **High Vol/OI ratio** (>2x) = more contracts traded today than existed yesterday → someone is making a large new bet.
- **Call-heavy unusual activity** above spot = bullish institutional bets.
- **Put-heavy unusual activity** below spot = hedging or bearish bets.
- **Notional value** estimates the dollar size of the bet (volume × mid price × 100 shares).

**Trading signals:**
- Unusual activity at a single strike with large notional = potential "smart money" positioning. Follow the direction.
- Cluster of unusual puts at the same strike = institutional hedging, not necessarily bearish — could mean they're protecting a long equity position.
- Unusual calls at far OTM strikes with short DTE = speculative lottery tickets. High risk, occasionally precedes takeovers or catalysts.
- Always cross-reference with IV — unusual volume with rising IV = new demand. Unusual volume with flat IV = more likely a spread or hedge.
""")

    all_opts = pd.concat([calls, puts], ignore_index=True).copy()
    all_opts['vol_oi_ratio'] = np.where(
        all_opts['open_interest'] > 0,
        all_opts['volume'] / all_opts['open_interest'],
        0
    )
    all_opts['notional'] = all_opts['volume'] * ((all_opts['bid'] + all_opts['ask']) / 2) * 100

    # Flag unusual: volume > 2x OI and volume > 100
    unusual = all_opts[(all_opts['vol_oi_ratio'] > 2) & (all_opts['volume'] > 100)].copy()
    unusual = unusual.sort_values('vol_oi_ratio', ascending=False)

    if not unusual.empty:
        uc1, uc2, uc3 = st.columns(3)
        uc1.metric("Unusual Strikes", len(unusual))
        uc2.metric("Total Unusual Volume", f"{unusual['volume'].sum():,.0f}")
        uc3.metric("Est. Notional", f"${unusual['notional'].sum():,.0f}")

        # Chart
        fig_unusual = go.Figure()
        u_calls = unusual[unusual['contract_type'] == 'call']
        u_puts = unusual[unusual['contract_type'] == 'put']

        if not u_calls.empty:
            fig_unusual.add_trace(go.Bar(
                x=u_calls['strike_price'], y=u_calls['vol_oi_ratio'],
                name='Call (unusual)', marker_color='#00ff96',
                hovertemplate="Strike: $%{x}<br>Vol/OI: %{y:.1f}x<extra></extra>",
            ))
        if not u_puts.empty:
            fig_unusual.add_trace(go.Bar(
                x=u_puts['strike_price'], y=u_puts['vol_oi_ratio'],
                name='Put (unusual)', marker_color='#ff4b4b',
                hovertemplate="Strike: $%{x}<br>Vol/OI: %{y:.1f}x<extra></extra>",
            ))
        if current_px:
            fig_unusual.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
        fig_unusual.add_hline(y=2, line_dash="dash", line_color="rgba(255,255,255,0.3)",
                              annotation_text="2x threshold")

        fig_unusual.update_layout(
            template="plotly_dark", height=350, margin=dict(t=30, b=40, l=50, r=20),
            yaxis_title="Volume / Open Interest", xaxis_title="Strike",
            barmode='group', hovermode="x unified",
        )
        st.plotly_chart(fig_unusual, use_container_width=True)

        # Table
        display_cols = ['contract_type', 'strike_price', 'volume', 'open_interest', 'vol_oi_ratio',
                        'implied_volatility', 'bid', 'ask', 'notional']
        st.dataframe(
            unusual[display_cols].head(20),
            use_container_width=True, hide_index=True,
            column_config={
                "contract_type": st.column_config.TextColumn("Type"),
                "strike_price": st.column_config.NumberColumn("Strike", format="$%.0f"),
                "vol_oi_ratio": st.column_config.NumberColumn("Vol/OI", format="%.1fx"),
                "implied_volatility": st.column_config.NumberColumn("IV", format="%.1%%"),
                "notional": st.column_config.NumberColumn("Notional", format="$%,.0f"),
            },
        )
    else:
        st.info("No unusual activity detected (volume > 2x open interest with volume > 100).")


# ── Tab: Greeks Heatmap ──
with tab_greeks:
    _greeks_exp, calls, puts, dte = _exp_selector("greeks")
    st.subheader("Greeks by Strike")
    with st.expander("What is this & how to use it?"):
        st.markdown("""
**What it shows:** The four key option Greeks at each strike, color-coded by intensity. Darker colors = larger exposure.

**The Greeks explained:**
- **Delta (Δ)** — How much the option price moves per $1 move in the stock. Call deltas range from 0 to 1, puts from -1 to 0. ATM options have ~0.50 delta.
- **Gamma (Γ)** — How fast delta changes. Highest at ATM, near zero for deep ITM/OTM. High gamma = delta shifts rapidly → position needs frequent rebalancing.
- **Theta (Θ)** — Time decay per day. Always negative for long options. Highest at ATM with short DTE. This is the cost of holding an option.
- **Vega (ν)** — Sensitivity to IV changes. A 1% rise in IV increases the option price by vega. Highest at ATM with long DTE.

**Trading signals:**
- **High gamma at ATM** = small stock moves cause large delta shifts → dangerous for sellers, valuable for buyers.
- **High theta near expiration** = time decay accelerates. Sellers benefit, buyers lose value daily.
- **High vega** = position is a vol bet. If you expect IV to rise (before earnings), buy high-vega options. If you expect IV to crush (after earnings), sell them.
- Compare call vs. put vega — asymmetry reveals market's directional vol expectations.
""")

    greeks_cols = st.columns(2)

    # Calls heatmap
    with greeks_cols[0]:
        st.markdown("**Calls**")
        if not calls.empty:
            calls_display = calls[['strike_price', 'delta', 'gamma', 'theta', 'vega', 'implied_volatility']].copy()
            calls_display.columns = ['Strike', 'Delta', 'Gamma', 'Theta', 'Vega', 'IV']
            calls_display = calls_display.set_index('Strike')

            # Style with color gradients
            def style_greeks(val, col):
                if col == 'Delta':
                    intensity = abs(val) * 200
                    return f'background-color: rgba(0,255,150,{min(intensity/255, 0.6):.2f})'
                elif col == 'Gamma':
                    intensity = val * 5000
                    return f'background-color: rgba(0,209,255,{min(intensity/255, 0.6):.2f})'
                elif col == 'Theta':
                    intensity = abs(val) * 500
                    return f'background-color: rgba(255,75,75,{min(intensity/255, 0.6):.2f})'
                elif col == 'Vega':
                    intensity = val * 200
                    return f'background-color: rgba(173,127,255,{min(intensity/255, 0.6):.2f})'
                return ''

            styled = calls_display.style.map(
                lambda v: style_greeks(v, 'Delta'), subset=['Delta']
            ).map(
                lambda v: style_greeks(v, 'Gamma'), subset=['Gamma']
            ).map(
                lambda v: style_greeks(v, 'Theta'), subset=['Theta']
            ).map(
                lambda v: style_greeks(v, 'Vega'), subset=['Vega']
            ).format({
                'Delta': '{:.3f}', 'Gamma': '{:.4f}', 'Theta': '{:.4f}',
                'Vega': '{:.4f}', 'IV': '{:.1%}',
            })
            st.dataframe(styled, use_container_width=True, height=400)

    # Puts heatmap
    with greeks_cols[1]:
        st.markdown("**Puts**")
        if not puts.empty:
            puts_display = puts[['strike_price', 'delta', 'gamma', 'theta', 'vega', 'implied_volatility']].copy()
            puts_display.columns = ['Strike', 'Delta', 'Gamma', 'Theta', 'Vega', 'IV']
            puts_display = puts_display.set_index('Strike')

            styled_p = puts_display.style.map(
                lambda v: style_greeks(abs(v), 'Delta'), subset=['Delta']
            ).map(
                lambda v: style_greeks(v, 'Gamma'), subset=['Gamma']
            ).map(
                lambda v: style_greeks(abs(v), 'Theta'), subset=['Theta']
            ).map(
                lambda v: style_greeks(v, 'Vega'), subset=['Vega']
            ).format({
                'Delta': '{:.3f}', 'Gamma': '{:.4f}', 'Theta': '{:.4f}',
                'Vega': '{:.4f}', 'IV': '{:.1%}',
            })
            st.dataframe(styled_p, use_container_width=True, height=400)

    # Greeks summary chart
    st.divider()
    st.markdown("**Delta & Gamma Curves**")
    fig_greeks = make_subplots(rows=1, cols=2, subplot_titles=["Delta by Strike", "Gamma by Strike"])

    fig_greeks.add_trace(go.Scatter(
        x=calls['strike_price'], y=calls['delta'], mode='lines', name='Call Delta',
        line=dict(color='#00ff96', width=2)), row=1, col=1)
    fig_greeks.add_trace(go.Scatter(
        x=puts['strike_price'], y=puts['delta'], mode='lines', name='Put Delta',
        line=dict(color='#ff4b4b', width=2)), row=1, col=1)
    fig_greeks.add_trace(go.Scatter(
        x=calls['strike_price'], y=calls['gamma'], mode='lines', name='Call Gamma',
        line=dict(color='#00d1ff', width=2)), row=1, col=2)
    fig_greeks.add_trace(go.Scatter(
        x=puts['strike_price'], y=puts['gamma'], mode='lines', name='Put Gamma',
        line=dict(color='#ad7fff', width=2)), row=1, col=2)

    if current_px:
        fig_greeks.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", row=1, col=1)
        fig_greeks.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", row=1, col=2)

    fig_greeks.update_layout(
        template="plotly_dark", height=350, margin=dict(t=30, b=40, l=50, r=20),
        showlegend=True, hovermode="x unified",
    )
    fig_greeks.update_xaxes(range=[x_min, x_max])
    st.plotly_chart(fig_greeks, use_container_width=True)


# ── Tab: Straddle Chain View ──
with tab_chain:
    _chain_exp, calls, puts, dte = _exp_selector("chain")
    st.subheader("Options Chain — Straddle View")
    with st.expander("What is this & how to use it?"):
        st.markdown("""
**What it shows:** The full options chain displayed in straddle format — calls on the left, puts on the right, centered at the current spot price. This is the same layout used by professional trading platforms.

**How to read it:**
- The **Strike** column in the center is the exercise price. Strikes near spot are "at the money" (ATM).
- **Bid/Ask** = the price you can sell/buy the option. Tight spreads = liquid. Wide spreads = illiquid, avoid.
- **OI** = open interest. Higher OI = more liquidity, tighter spreads, easier to enter/exit.
- **Vol** = today's volume. Compare to OI to spot unusual activity.
- **IV** = implied volatility for that strike. Compare across strikes to see the skew.
- **Delta** = directional exposure. A 0.30 delta call behaves like owning 30 shares.

**Quick strategies:**
- **Straddle** = buy both the ATM call and ATM put. Profit if the stock moves big in either direction. Cost = C Ask + P Ask.
- **Strangle** = buy OTM call + OTM put. Cheaper than straddle but needs a bigger move.
- **Iron Condor** = sell OTM call spread + OTM put spread. Profit if price stays in range. Use OI walls as wing strikes.
""")

    call_cols = calls[['strike_price', 'delta', 'implied_volatility', 'volume', 'open_interest', 'bid', 'ask']].copy()
    call_cols = call_cols.set_index('strike_price')
    call_cols.columns = ['C Delta', 'C IV', 'C Vol', 'C OI', 'C Bid', 'C Ask']

    put_cols = puts[['strike_price', 'bid', 'ask', 'open_interest', 'volume', 'implied_volatility', 'delta']].copy()
    put_cols = put_cols.set_index('strike_price')
    put_cols.columns = ['P Bid', 'P Ask', 'P OI', 'P Vol', 'P IV', 'P Delta']

    straddle_df = call_cols.join(put_cols, how='outer').reset_index()
    straddle_df = straddle_df.rename(columns={'strike_price': 'Strike'})
    straddle_df = straddle_df[['C Delta', 'C IV', 'C Vol', 'C OI', 'C Bid', 'C Ask',
                                'Strike', 'P Bid', 'P Ask', 'P OI', 'P Vol', 'P IV', 'P Delta']]

    # Center at the money
    if current_px is not None:
        atm_idx = (straddle_df['Strike'] - current_px).abs().idxmin()
        num_strikes = 20
        start = max(0, atm_idx - num_strikes)
        end = min(len(straddle_df), atm_idx + num_strikes + 1)
        straddle_df = straddle_df.iloc[start:end].reset_index(drop=True)

    # Find ATM strike
    atm_strike = None
    if current_px is not None and not straddle_df.empty:
        atm_strike = straddle_df.loc[(straddle_df['Strike'] - current_px).abs().idxmin(), 'Strike']

    # Style: highlight ATM row, bold strike column, ITM shading
    def style_chain(row):
        styles = [''] * len(row)
        strike = row['Strike']
        is_atm = atm_strike is not None and strike == atm_strike

        # Strike column always bold with accent background
        strike_idx = list(row.index).index('Strike')
        styles[strike_idx] = 'font-weight: bold; background-color: rgba(0, 209, 255, 0.12); color: #00d1ff;'

        if is_atm:
            # ATM row: strong highlight
            base = 'background-color: rgba(255, 170, 0, 0.18); border-top: 1px solid #ffaa00; border-bottom: 1px solid #ffaa00;'
            styles = [base] * len(row)
            styles[strike_idx] = base + ' font-weight: bold; color: #ffaa00;'
        elif current_px is not None:
            # ITM calls (strike < spot) get subtle green, ITM puts (strike > spot) subtle red
            if strike < current_px:
                # Calls are ITM
                for i, col in enumerate(row.index):
                    if col.startswith('C '):
                        styles[i] = 'background-color: rgba(0, 255, 150, 0.04);'
            elif strike > current_px:
                # Puts are ITM
                for i, col in enumerate(row.index):
                    if col.startswith('P '):
                        styles[i] = 'background-color: rgba(255, 75, 75, 0.04);'

        return styles

    # Format numbers — show "—" for zero bid/ask instead of 0.00
    format_dict = {
        'Strike': '${:,.0f}', 'C IV': '{:.1%}', 'P IV': '{:.1%}',
        'C Delta': '{:.3f}', 'P Delta': '{:.3f}',
        'C Vol': '{:,.0f}', 'C OI': '{:,.0f}', 'P Vol': '{:,.0f}', 'P OI': '{:,.0f}',
    }
    # Only format bid/ask if they have non-zero values
    has_prices = straddle_df[['C Bid', 'C Ask', 'P Bid', 'P Ask']].sum().sum() > 0
    if has_prices:
        format_dict.update({'C Bid': '${:.2f}', 'C Ask': '${:.2f}', 'P Bid': '${:.2f}', 'P Ask': '${:.2f}'})

    styled_chain = straddle_df.style.apply(style_chain, axis=1).format(format_dict, na_rep="—")

    # ATM label and data notes
    if atm_strike and current_px:
        st.caption(f"ATM Strike: **${atm_strike:,.0f}** (spot ${current_px:,.2f}) — highlighted in yellow. ITM calls shaded green, ITM puts shaded red.")
    model_filled_count = int(df.get('model_filled', pd.Series(dtype=bool)).sum()) if 'model_filled' in df.columns else 0
    if model_filled_count > 0:
        st.caption(f"{model_filled_count} options priced using BS-Merton Jump Diffusion blend (market data unavailable). Model prices shown with ±1% bid/ask spread.")
    elif not has_prices:
        st.caption("Bid/Ask showing 0 — market may be closed. Re-fetch during market hours for live quotes.")

    st.dataframe(styled_chain, use_container_width=True, hide_index=True, height=500)

    # Export button
    csv_data = straddle_df.to_csv(index=False)
    st.download_button(
        label="Export Chain (CSV)",
        data=csv_data,
        file_name=f"{ticker_display}_{exp_display}_chain.csv",
        mime="text/csv",
    )

    # Volume distribution chart below chain
    st.divider()
    st.markdown("**Volume Distribution**")
    fig_vol = go.Figure()
    fig_vol.add_trace(go.Bar(x=calls['strike_price'], y=calls['volume'], name='Call Vol', marker_color='#00d1ff', opacity=0.85))
    fig_vol.add_trace(go.Bar(x=puts['strike_price'], y=puts['volume'], name='Put Vol', marker_color='#ad7fff', opacity=0.85))
    if current_px:
        fig_vol.add_vline(x=current_px, line_dash="dot", line_color="#ffaa00", annotation_text="Spot")
    vol_cap = pd.concat([calls['volume'], puts['volume']]).quantile(0.95)
    fig_vol.update_layout(
        template="plotly_dark", height=300, margin=dict(t=30, b=40, l=50, r=20),
        barmode='group', yaxis_title="Volume", xaxis_title="Strike",
        xaxis=dict(range=[x_min, x_max]),
        yaxis=dict(range=[0, vol_cap * 1.1] if vol_cap > 0 else None),
        hovermode="x unified",
    )
    st.plotly_chart(fig_vol, use_container_width=True)


# ── Sidebar Chat ──
chat_parts = [f"Options Analysis for {ticker_display} expiring {exp_display} ({dte} DTE)."]
if current_px:
    chat_parts.append(f"Spot: ${current_px:.2f}.")
if atm_iv:
    chat_parts.append(f"ATM IV: {atm_iv:.1%}.")
if expected_move:
    chat_parts.append(f"Expected move: ±${expected_move:.2f} (±{expected_move_pct:.1f}%).")
chat_parts.append(f"P/C OI Ratio: {pc_oi_ratio:.2f}. P/C Vol Ratio: {pc_vol_ratio:.2f}.")
if max_pain:
    chat_parts.append(f"Max Pain: ${max_pain:,.0f}.")
if max_call_oi_strike:
    chat_parts.append(f"Highest Call OI: ${max_call_oi_strike:,.0f}.")
if max_put_oi_strike:
    chat_parts.append(f"Highest Put OI: ${max_put_oi_strike:,.0f}.")
chat_parts.append(f"Call notional: ${call_notional:,.0f}. Put notional: ${put_notional:,.0f}.")
chat_ctx = " ".join(chat_parts)
run_sidebar_chatbot(chat_ctx)

st.markdown("---")
source = st.session_state.get('current_data_source', None)
if not source and 'options_df' in st.session_state:
    source = "Massive API (Polygon)" if st.session_state.get('options_df') is not None else "Unknown"
st.caption(f"Data Source: `{source}`" if source else "No data loaded.")
