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
    render_data_source_footer,
)
from src.options_models import black_scholes, bs_greeks, fill_missing_options_data

logger = logging.getLogger(__name__)

setup_page("44_Portfolio_Greeks")

st.title("Portfolio Greeks")
st.markdown("Unified Greeks dashboard — enter positions, see aggregate risk, model scenarios.")

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


def _T(exp_str):
    return max(_dte(exp_str) / 365.0, 0.001)


def _mid(row):
    bid = row.get("bid", 0) or 0
    ask = row.get("ask", 0) or 0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return row.get("last_price", 0) or 0


def _get_contract(chain, strike, opt_type):
    mask = (chain["strike_price"] == strike) & (chain["contract_type"] == opt_type)
    sub = chain[mask]
    return sub.iloc[0] if not sub.empty else None


# ─── POSITION STORAGE ─────────────────────────────────────────────────────────

if "pg_positions" not in st.session_state:
    st.session_state["pg_positions"] = []

positions = st.session_state["pg_positions"]
rfr = _get_rfr()

# ─── POSITION ENTRY ───────────────────────────────────────────────────────────

st.subheader("Position Entry")

# Import from Position Book
try:
    from src.position_book import get_positions as _get_book_positions
    book_positions = _get_book_positions(status="open")
    if book_positions and not positions:
        with st.expander("Import from Position Book", expanded=True):
            st.caption(f"Found **{len(book_positions)}** open positions in your Position Book.")
            if st.button("Import All Positions", type="secondary", key="pg_import"):
                for bp in book_positions:
                    pos_type_map = {"stock": "Stock", "call": "Call", "put": "Put"}
                    bp_type = pos_type_map.get(bp.get("type", "stock").lower(), "Stock")
                    bp_ticker = format_massive_ticker(bp.get("ticker", ""))
                    bp_qty = bp.get("qty", 0)
                    bp_entry = bp.get("entry_price", 0)
                    bp_details = bp.get("details", {})
                    bp_strike = bp_details.get("strike")
                    bp_exp = bp_details.get("expiration")

                    delta = gamma = theta = vega = rho = iv = 0.0
                    cur_price = bp_entry

                    if bp_type == "Stock":
                        delta = 1.0
                        try:
                            px = fetch_massive_data(bp_ticker, 5)
                            if px is not None and not px.empty:
                                cur_price = float(px["Close"].iloc[-1])
                        except Exception:
                            pass
                    elif bp_strike and bp_exp:
                        try:
                            chain = fetch_options_chain(bp_ticker, bp_exp)
                            if chain is not None and not chain.empty:
                                spot_px = bp_strike
                                try:
                                    _fp = fetch_massive_data(bp_ticker, 5)
                                    if _fp is not None and not _fp.empty:
                                        spot_px = float(_fp["Close"].iloc[-1])
                                except Exception:
                                    pass
                                if spot_px > 0:
                                    chain = fill_missing_options_data(chain, spot_px, risk_free_rate=rfr)
                                row = _get_contract(chain, bp_strike, bp_type.lower())
                                if row is not None:
                                    cur_price = _mid(row)
                                    delta = row.get("delta", 0) or 0
                                    gamma = row.get("gamma", 0) or 0
                                    theta = row.get("theta", 0) or 0
                                    vega = row.get("vega", 0) or 0
                                    rho = row.get("rho", 0) or 0
                                    iv = row.get("implied_volatility", 0) or 0
                        except Exception:
                            pass

                    positions.append({
                        "ticker": bp_ticker, "type": bp_type, "qty": bp_qty,
                        "strike": bp_strike, "expiration": bp_exp,
                        "entry_price": bp_entry, "current_price": cur_price,
                        "delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "rho": rho, "iv": iv,
                    })
                st.session_state["pg_positions"] = positions
                st.success(f"Imported {len(book_positions)} positions from Position Book.")
                st.rerun()
except ImportError:
    pass

with st.expander("Add Position", expanded=len(positions) == 0):
    pc1, pc2, pc3, pc4 = st.columns(4)
    with pc1:
        pos_ticker = st.text_input("Ticker", value=get_active_ticker(), key="pg_ticker")
    with pc2:
        pos_type = st.selectbox("Type", ["Stock", "Call", "Put"], key="pg_type")
    with pc3:
        pos_qty = st.number_input("Quantity", value=100, step=1, key="pg_qty",
                                   help="Positive = long, Negative = short")
    with pc4:
        pos_entry = st.number_input("Entry Price", value=0.0, step=0.01, key="pg_entry",
                                     help="Your cost basis per share (stock) or per contract (options)")

    if pos_type != "Stock":
        oc1, oc2 = st.columns(2)
        with oc1:
            pos_strike = st.number_input("Strike", value=0.0, step=1.0, key="pg_strike")
        with oc2:
            pos_exp = st.text_input("Expiration (YYYY-MM-DD)", key="pg_exp",
                                     help="e.g., 2026-06-19")
    else:
        pos_strike = None
        pos_exp = None

    if st.button("Add Position", type="primary", key="pg_add"):
        ticker_fmt = format_massive_ticker(pos_ticker)

        # Fetch current price and Greeks
        cur_price = 0.0
        delta = gamma = theta = vega = rho = iv = 0.0

        if pos_type == "Stock":
            try:
                px = fetch_massive_data(ticker_fmt, 5)
                if px is not None and not px.empty:
                    cur_price = float(px["Close"].iloc[-1])
            except Exception:
                pass
            delta = 1.0
        else:
            # Option — fetch chain
            opt_type_lower = pos_type.lower()
            if pos_exp and pos_strike > 0:
                try:
                    chain = fetch_options_chain(ticker_fmt, pos_exp)
                    if chain is not None and not chain.empty:
                        # Fetch spot for fill — pos_strike as fallback
                        _fs = pos_strike
                        try:
                            _fp = fetch_massive_data(ticker_fmt, 5)
                            if _fp is not None and not _fp.empty:
                                _fs = float(_fp["Close"].iloc[-1])
                        except Exception:
                            pass
                        if _fs > 0:
                            chain = fill_missing_options_data(chain, _fs, risk_free_rate=rfr)
                        row = _get_contract(chain, pos_strike, opt_type_lower)
                        if row is not None:
                            cur_price = _mid(row)
                            delta = row.get("delta", 0) or 0
                            gamma = row.get("gamma", 0) or 0
                            theta = row.get("theta", 0) or 0
                            vega = row.get("vega", 0) or 0
                            rho = row.get("rho", 0) or 0
                            iv = row.get("implied_volatility", 0) or 0
                except Exception as e:
                    st.warning(f"Could not fetch Greeks: {e}")

                # Fallback to BS if no market Greeks
                if abs(delta) < 1e-10 and iv > 0 and pos_strike > 0 and pos_exp:
                    try:
                        px = fetch_massive_data(ticker_fmt, 5)
                        s = float(px["Close"].iloc[-1]) if px is not None else pos_strike
                        T = _T(pos_exp)
                        g = bs_greeks(s, pos_strike, T, rfr, iv, opt_type_lower)
                        delta = g["delta"]
                        gamma = g["gamma"]
                        theta = g["theta"]
                        vega = g["vega"]
                    except Exception:
                        pass

        positions.append({
            "ticker": ticker_fmt,
            "type": pos_type,
            "qty": pos_qty,
            "strike": pos_strike,
            "expiration": pos_exp,
            "entry_price": pos_entry if pos_entry > 0 else cur_price,
            "current_price": cur_price,
            "delta": delta,
            "gamma": gamma,
            "theta": theta,
            "vega": vega,
            "rho": rho,
            "iv": iv,
        })
        st.session_state["pg_positions"] = positions
        st.success(f"Added: {pos_qty:+d} {ticker_fmt} {pos_type}" +
                   (f" ${pos_strike:.0f} {pos_exp}" if pos_type != "Stock" else ""))

# ─── DISPLAY POSITIONS ────────────────────────────────────────────────────────

if not positions:
    st.info("Add positions above to see portfolio Greeks.")
    st.stop()

pos_df = pd.DataFrame(positions)

# Compute dollar Greeks
pos_df["multiplier"] = pos_df["type"].apply(lambda t: 100 if t != "Stock" else 1)
pos_df["delta_$"] = pos_df["delta"] * pos_df["qty"] * pos_df["multiplier"]
pos_df["gamma_$"] = pos_df["gamma"] * pos_df["qty"] * pos_df["multiplier"]
pos_df["theta_$"] = pos_df["theta"] * pos_df["qty"] * pos_df["multiplier"]
pos_df["vega_$"] = pos_df["vega"] * pos_df["qty"] * pos_df["multiplier"]
pos_df["market_value"] = pos_df["current_price"] * pos_df["qty"] * pos_df["multiplier"]
pos_df["pnl"] = (pos_df["current_price"] - pos_df["entry_price"]) * pos_df["qty"] * pos_df["multiplier"]

# Position table
st.markdown("#### Current Positions")
display_pos = pos_df[["ticker", "type", "qty", "strike", "expiration",
                       "entry_price", "current_price", "delta", "gamma", "theta", "vega"]].copy()
display_pos.columns = ["Ticker", "Type", "Qty", "Strike", "Exp", "Entry", "Current",
                        "Delta", "Gamma", "Theta", "Vega"]
st.dataframe(display_pos, use_container_width=True, hide_index=True)

# Remove / Clear
rc1, rc2 = st.columns(2)
with rc1:
    if len(positions) > 0:
        rm_idx = st.selectbox("Remove position #", range(len(positions)),
                                format_func=lambda i: f"{positions[i]['qty']:+d} {positions[i]['ticker']} {positions[i]['type']}",
                                key="pg_rm_idx")
        if st.button("Remove Selected", key="pg_rm"):
            positions.pop(rm_idx)
            st.session_state["pg_positions"] = positions
            st.rerun()
with rc2:
    if st.button("Clear All Positions", key="pg_clear"):
        st.session_state["pg_positions"] = []
        st.rerun()

# ─── TABS ──────────────────────────────────────────────────────────────────────

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "Portfolio Summary", "Risk Scenarios", "Greeks by Expiration", "Greeks Over Time", "Delta Hedging",
])


# ══════════════════════════════════════════════════════════════════════════════
# TAB 1 — PORTFOLIO SUMMARY
# ══════════════════════════════════════════════════════════════════════════════

with tab1:
    with error_boundary("Portfolio Summary"):
        st.subheader("Aggregate Portfolio Greeks")

        total_delta = pos_df["delta_$"].sum()
        total_gamma = pos_df["gamma_$"].sum()
        total_theta = pos_df["theta_$"].sum()
        total_vega = pos_df["vega_$"].sum()
        total_rho = (pos_df["rho"] * pos_df["qty"] * 100).sum() if "rho" in pos_df.columns else 0
        total_value = pos_df["market_value"].sum()
        total_pnl = pos_df["pnl"].sum()

        m1, m2, m3, m4, m5 = st.columns(5)
        delta_color = COLORS["success"] if total_delta >= 0 else COLORS["danger"]
        m1.metric("Net Delta ($)", f"${total_delta:+,.0f}",
                  help="Dollar exposure to a $1 move in underlying. Positive = bullish.")
        m2.metric("Net Gamma ($)", f"${total_gamma:+,.2f}",
                  help="How delta changes per $1 underlying move.")
        m3.metric("Net Theta ($/day)", f"${total_theta:+,.2f}",
                  help="Daily P&L from time decay alone. Positive = earning theta.")
        m4.metric("Net Vega ($/1%)", f"${total_vega:+,.2f}",
                  help="P&L per 1% IV change. Positive = long vol.")
        m5.metric("Net Rho ($)", f"${total_rho:+,.2f}",
                  help="P&L per 1% interest rate change. Positive = benefits from rate hikes.")

        v1, v2, v3 = st.columns(3)
        v1.metric("Portfolio Value", f"${total_value:+,.0f}")
        v2.metric("Unrealized P&L", f"${total_pnl:+,.0f}")
        bias = "Bullish" if total_delta > 0 else ("Bearish" if total_delta < 0 else "Neutral")
        v3.metric("Directional Bias", bias)

        # Per-position breakdown
        st.markdown("#### Position Detail")
        detail = pos_df[["ticker", "type", "qty", "delta_$", "gamma_$", "theta_$", "vega_$", "market_value", "pnl"]].copy()
        detail.columns = ["Ticker", "Type", "Qty", "Delta $", "Gamma $", "Theta $/day", "Vega $/1%", "Mkt Value", "P&L"]
        st.dataframe(
            detail.style.format({
                "Delta $": "${:+,.0f}", "Gamma $": "${:+,.2f}",
                "Theta $/day": "${:+,.2f}", "Vega $/1%": "${:+,.2f}",
                "Mkt Value": "${:+,.0f}", "P&L": "${:+,.0f}",
            }),
            use_container_width=True, hide_index=True,
        )

        # Greeks pie chart
        if len(pos_df) > 1:
            st.markdown("#### Delta Contribution by Position")
            delta_abs = pos_df["delta_$"].abs()
            if delta_abs.sum() > 0:
                fig_pie = go.Figure(go.Pie(
                    labels=[f"{r['ticker']} {r['type']}" for _, r in pos_df.iterrows()],
                    values=delta_abs.values,
                    hole=0.4,
                    textinfo="label+percent",
                    textfont=dict(size=10),
                ))
                fig_pie.update_layout(
                    template="plotly_dark", height=350,
                    margin=dict(l=0, r=0, t=10, b=10), showlegend=False,
                )
                st.plotly_chart(fig_pie, use_container_width=True, config=PLOTLY_NOBAR)


# ══════════════════════════════════════════════════════════════════════════════
# TAB 2 — RISK SCENARIOS
# ══════════════════════════════════════════════════════════════════════════════

with tab2:
    with error_boundary("Risk Scenarios"):
        st.subheader("What-If Scenario Analysis")
        with st.expander("How to read this"):
            st.markdown(
                "The heatmap shows portfolio P&L under different combinations of "
                "underlying price moves and IV shifts. For stock positions, P&L is "
                "linear with price. For options, BS repricing captures non-linear effects."
            )

        spot_moves = [-10, -5, -2, -1, 0, 1, 2, 5, 10]
        iv_shifts = [-10, -5, 0, 5, 10]

        pnl_grid = np.zeros((len(iv_shifts), len(spot_moves)))

        # Pre-fetch spot prices once per unique ticker (avoid repeated API calls)
        _spot_cache = {}
        for _, pos in pos_df.iterrows():
            tk = pos["ticker"]
            if tk not in _spot_cache:
                if pos["type"] == "Stock":
                    _spot_cache[tk] = pos["current_price"]
                else:
                    try:
                        px = fetch_massive_data(tk, 5)
                        _spot_cache[tk] = float(px["Close"].iloc[-1]) if px is not None and not px.empty else pos.get("strike", 100)
                    except Exception:
                        _spot_cache[tk] = pos.get("strike", 100)

        for i, iv_shift in enumerate(iv_shifts):
            for j, spot_move in enumerate(spot_moves):
                total_pnl_scenario = 0
                for _, pos in pos_df.iterrows():
                    if pos["type"] == "Stock":
                        new_price = pos["current_price"] * (1 + spot_move / 100)
                        pnl_pos = (new_price - pos["current_price"]) * pos["qty"]
                    else:
                        opt_type = pos["type"].lower()
                        iv = pos.get("iv", 0) or 0.25
                        if pos["strike"] and pos["expiration"] and iv > 0:
                            s = _spot_cache.get(pos["ticker"], pos["strike"])
                            new_s = s * (1 + spot_move / 100)
                            new_iv = max(iv + iv_shift / 100, 0.01)
                            T = _T(pos["expiration"])
                            new_price = black_scholes(new_s, pos["strike"], T, rfr, new_iv, opt_type)
                            pnl_pos = (new_price - pos["current_price"]) * pos["qty"] * 100
                        else:
                            pnl_pos = 0
                    total_pnl_scenario += pnl_pos
                pnl_grid[i, j] = total_pnl_scenario

        fig_scenario = go.Figure(go.Heatmap(
            x=[f"{m:+d}%" for m in spot_moves],
            y=[f"{s:+d}% IV" for s in iv_shifts],
            z=pnl_grid,
            colorscale=[[0, COLORS["danger"]], [0.5, "#1c1f26"], [1, COLORS["success"]]],
            zmid=0,
            colorbar=dict(title="P&L ($)"),
            text=np.round(pnl_grid, 0).astype(int),
            texttemplate="$%{text}",
            textfont=dict(size=9),
            hovertemplate="Spot: %{x}<br>IV: %{y}<br>P&L: $%{z:,.0f}<extra></extra>",
        ))
        fig_scenario.update_layout(
            template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)", height=400,
            xaxis_title="Underlying Price Move",
            yaxis_title="IV Shift",
            margin=dict(l=80, r=20, t=30, b=50),
        )
        st.plotly_chart(fig_scenario, use_container_width=True, config=PLOTLY_NOBAR)

        # Worst / best case
        worst = pnl_grid.min()
        best = pnl_grid.max()
        st.caption(f"Worst case: **${worst:+,.0f}** | Best case: **${best:+,.0f}**")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 3 — GREEKS BY EXPIRATION
# ══════════════════════════════════════════════════════════════════════════════

with tab3:
    with error_boundary("Greeks by Expiration"):
        st.subheader("Greeks Grouped by Expiration")

        options_only = pos_df[pos_df["type"] != "Stock"].copy()
        if options_only.empty:
            st.info("No option positions to display. Add options to see expiration breakdown.")
        else:
            options_only["exp_label"] = options_only["expiration"].apply(
                lambda e: f"{e} ({_dte(e)}d)" if e else "N/A"
            )

            greek_cols = ["delta_$", "gamma_$", "theta_$", "vega_$"]
            greek_labels = ["Delta $", "Gamma $", "Theta $/day", "Vega $/1%"]
            greek_colors = [COLORS["accent"], COLORS["warning"], COLORS["danger"], COLORS["success"]]

            grouped = options_only.groupby("exp_label")[greek_cols].sum().sort_index()

            fig_exp = make_subplots(rows=2, cols=2, subplot_titles=greek_labels,
                                    vertical_spacing=0.12, horizontal_spacing=0.08)

            for idx, (col, label, color) in enumerate(zip(greek_cols, greek_labels, greek_colors)):
                r, c = idx // 2 + 1, idx % 2 + 1
                bar_colors = [COLORS["success"] if v >= 0 else COLORS["danger"] for v in grouped[col]]
                fig_exp.add_trace(go.Bar(
                    x=grouped.index, y=grouped[col],
                    marker_color=bar_colors, name=label, showlegend=False,
                ), row=r, col=c)
                fig_exp.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.3, row=r, col=c)

            fig_exp.update_layout(
                template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)", height=500,
                margin=dict(l=50, r=20, t=40, b=80),
            )
            fig_exp.update_xaxes(tickangle=-45)
            st.plotly_chart(fig_exp, use_container_width=True, config=PLOTLY_NOBAR)

            # Next expiry countdown
            exp_dates = [e for e in options_only["expiration"].dropna().unique() if e]
            if exp_dates:
                next_exp = min(exp_dates)
                days_left = _dte(next_exp)
                if days_left <= 7:
                    st.error(f"Next expiry: **{next_exp}** ({days_left} days) — review gamma risk!")
                elif days_left <= 21:
                    st.warning(f"Next expiry: **{next_exp}** ({days_left} days) — approaching roll window.")
                else:
                    st.info(f"Next expiry: **{next_exp}** ({days_left} days)")


# ══════════════════════════════════════════════════════════════════════════════
# TAB 4 — GREEKS OVER TIME
# ══════════════════════════════════════════════════════════════════════════════

with tab4:
    with error_boundary("Greeks Over Time"):
        st.subheader("Greeks Evolution — Next 30 Days")
        with st.expander("How to read this"):
            st.markdown(
                "Shows how your portfolio's aggregate Greeks change as time passes, "
                "assuming the underlying stays at the current price and IV is unchanged. "
                "Theta typically accelerates as options approach expiry."
            )

        options_only = pos_df[pos_df["type"] != "Stock"].copy()
        if options_only.empty:
            st.info("No option positions — Greeks don't change over time for stock-only portfolios.")
        else:
            # Find the earliest expiry to cap simulation
            exp_dates = [e for e in options_only["expiration"].dropna().unique() if e]
            if not exp_dates:
                st.warning("No valid expiration dates in positions.")
            else:
                earliest = min(exp_dates)
                max_days = min(30, _dte(earliest))
                if max_days < 1:
                    max_days = 1

                days_forward = list(range(0, max_days + 1))
                greeks_ot = {"day": [], "delta": [], "gamma": [], "theta": [], "vega": []}

                for d in days_forward:
                    day_delta = day_gamma = day_theta = day_vega = 0

                    # Add stock positions (constant)
                    for _, pos in pos_df[pos_df["type"] == "Stock"].iterrows():
                        day_delta += pos["qty"]

                    # Build spot cache for repricing
                    if "_spot_cache_t4" not in st.session_state:
                        _sc = {}
                        for _, _p in options_only.iterrows():
                            _tk = _p["ticker"]
                            if _tk not in _sc:
                                try:
                                    _px = fetch_massive_data(_tk, 5)
                                    _sc[_tk] = float(_px["Close"].iloc[-1]) if _px is not None and not _px.empty else _p["strike"]
                                except Exception:
                                    _sc[_tk] = _p["strike"]
                        st.session_state["_spot_cache_t4"] = _sc
                    _spot_cache = st.session_state["_spot_cache_t4"]

                    for _, pos in options_only.iterrows():
                        if not pos["expiration"] or not pos["strike"]:
                            continue
                        opt_type = pos["type"].lower()
                        iv = pos.get("iv", 0) or 0.25
                        remaining_dte = _dte(pos["expiration"]) - d
                        if remaining_dte <= 0:
                            continue
                        T = max(remaining_dte / 365.0, 0.001)

                        s = _spot_cache.get(pos["ticker"], pos["strike"])
                        g = bs_greeks(s, pos["strike"], T, rfr, iv, opt_type)
                        mult = pos["qty"] * 100
                        day_delta += g["delta"] * mult
                        day_gamma += g["gamma"] * mult
                        day_theta += g["theta"] * mult
                        day_vega += g["vega"] * mult

                    greeks_ot["day"].append(d)
                    greeks_ot["delta"].append(day_delta)
                    greeks_ot["gamma"].append(day_gamma)
                    greeks_ot["theta"].append(day_theta)
                    greeks_ot["vega"].append(day_vega)

                gt_df = pd.DataFrame(greeks_ot)

                fig_gt = make_subplots(rows=2, cols=2,
                                       subplot_titles=["Delta ($)", "Gamma ($)", "Theta ($/day)", "Vega ($/1%)"],
                                       vertical_spacing=0.12, horizontal_spacing=0.08)

                for idx, (col, color) in enumerate([
                    ("delta", COLORS["accent"]),
                    ("gamma", COLORS["warning"]),
                    ("theta", COLORS["danger"]),
                    ("vega", COLORS["success"]),
                ]):
                    r, c = idx // 2 + 1, idx % 2 + 1
                    fig_gt.add_trace(go.Scatter(
                        x=gt_df["day"], y=gt_df[col],
                        line=dict(color=color, width=2),
                        fill="tozeroy", fillcolor=f"rgba({','.join(str(int(color.lstrip('#')[i:i+2], 16)) for i in (0,2,4))},0.1)",
                        name=col.title(), showlegend=False,
                    ), row=r, col=c)
                    fig_gt.add_hline(y=0, line_color=COLORS["text_muted"], line_width=0.3, row=r, col=c)

                fig_gt.update_xaxes(title_text="Days Forward")
                fig_gt.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=500,
                    margin=dict(l=50, r=20, t=40, b=40),
                )
                st.plotly_chart(fig_gt, use_container_width=True, config=PLOTLY_NOBAR)

                st.caption(
                    "Simulation holds spot and IV constant. In reality, both move — "
                    "use the Risk Scenarios tab for combined spot + IV + time analysis."
                )


# ══════════════════════════════════════════════════════════════════════════════
# TAB 5 — DELTA HEDGING CALCULATOR
# ══════════════════════════════════════════════════════════════════════════════

with tab5:
    with error_boundary("Delta Hedging"):
        st.subheader("Delta Hedging Calculator")
        st.markdown(
            "Calculate the shares needed to neutralize your portfolio's delta exposure. "
            "A **delta-neutral** portfolio profits from volatility and time decay regardless of direction."
        )

        total_delta = pos_df["delta_$"].sum()
        total_gamma = pos_df["gamma_$"].sum()

        # Current delta summary
        dh1, dh2, dh3 = st.columns(3)
        dh1.metric("Current Net Delta ($)", f"${total_delta:+,.0f}")
        dh2.metric("Current Net Gamma ($)", f"${total_gamma:+,.2f}")

        # Find dominant underlying for hedging
        tickers_in_portfolio = pos_df["ticker"].unique().tolist()
        hedge_ticker = st.selectbox("Hedge with", tickers_in_portfolio, key="dh_ticker")

        # Get hedge instrument price
        hedge_spot = 0.0
        try:
            _hp = fetch_massive_data(hedge_ticker, 5)
            if _hp is not None and not _hp.empty:
                hedge_spot = float(_hp["Close"].iloc[-1])
        except Exception:
            pass

        if hedge_spot > 0:
            # Shares needed to neutralize delta
            shares_to_hedge = -total_delta / 1.0  # Each share has delta of $1
            shares_rounded = int(round(shares_to_hedge / 100) * 100)  # Round to nearest 100

            dh3.metric("Hedge Spot Price", f"${hedge_spot:,.2f}")

            st.divider()

            hm1, hm2, hm3, hm4 = st.columns(4)
            direction = "BUY" if shares_rounded > 0 else "SELL"
            dir_color = COLORS["success"] if shares_rounded > 0 else COLORS["danger"]
            hm1.markdown(
                f'<div style="text-align:center;padding:12px;border:2px solid {dir_color};border-radius:8px;">'
                f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">HEDGE ACTION</div>'
                f'<div style="font-size:1.5rem;font-weight:800;color:{dir_color};">{direction}</div>'
                f'<div style="font-size:1.1rem;color:{COLORS["text_primary"]};">{abs(shares_rounded):,} shares</div>'
                f'<div style="font-size:0.7rem;color:{COLORS["text_muted"]};">{hedge_ticker}</div>'
                f'</div>', unsafe_allow_html=True,
            )

            hedge_cost = abs(shares_rounded) * hedge_spot
            hm2.metric("Hedge Capital Required", f"${hedge_cost:,.0f}")
            hm3.metric("Residual Delta (exact)", f"${total_delta + shares_to_hedge:+,.2f}")
            hm4.metric("Residual Delta (rounded)", f"${total_delta + shares_rounded:+,.0f}")

            # Dynamic hedge table — show how hedge changes at different spot levels
            st.divider()
            st.subheader("Dynamic Hedge Schedule")
            st.caption(
                "As spot moves, your options delta changes (gamma effect). "
                "This table shows the hedge adjustment needed at each price level."
            )

            spot_levels = np.linspace(hedge_spot * 0.95, hedge_spot * 1.05, 11)
            hedge_schedule = []

            for s_new in spot_levels:
                new_total_delta = 0
                for _, pos in pos_df.iterrows():
                    if pos["type"] == "Stock":
                        new_total_delta += pos["qty"]
                    elif pos["strike"] and pos["expiration"]:
                        opt_type = pos["type"].lower()
                        iv = pos.get("iv", 0) or 0.25
                        T = _T(pos["expiration"])
                        if T > 0 and pos["strike"] > 0:
                            # Only reprice options on the hedge ticker; others keep current delta
                            if pos["ticker"] == hedge_ticker:
                                g = bs_greeks(s_new, pos["strike"], T, rfr, iv, opt_type)
                                new_total_delta += g["delta"] * pos["qty"] * 100
                            else:
                                new_total_delta += pos["delta"] * pos["qty"] * 100

                shares_needed = -new_total_delta
                hedge_schedule.append({
                    "Spot": f"${s_new:.2f}",
                    "Spot Move": f"{(s_new / hedge_spot - 1) * 100:+.1f}%",
                    "Portfolio Delta ($)": f"${new_total_delta:+,.0f}",
                    "Shares to Hedge": f"{int(round(shares_needed / 100) * 100):+,d}",
                    "Adjustment from Current": f"{int(round(shares_needed / 100) * 100) - shares_rounded:+,d}",
                })

            st.dataframe(pd.DataFrame(hedge_schedule), use_container_width=True, hide_index=True)

            # Gamma scalping P&L projection
            st.divider()
            st.subheader("Gamma Scalping P&L Projection")
            st.caption(
                "If you delta-hedge and the stock moves by a certain amount, "
                "your gamma generates a P&L. This projection shows the expected gamma P&L "
                "vs theta cost for different daily move sizes."
            )

            daily_moves = [0.5, 1.0, 1.5, 2.0, 3.0, 5.0]
            gamma_pnl_data = []
            daily_theta = pos_df["theta_$"].sum()

            for move_pct in daily_moves:
                move_dollar = hedge_spot * move_pct / 100
                # Gamma P&L ≈ 0.5 × Gamma × (ΔS)²
                gamma_pnl = 0.5 * total_gamma * (move_dollar ** 2)
                net_pnl = gamma_pnl + daily_theta
                gamma_pnl_data.append({
                    "Daily Move (%)": f"±{move_pct:.1f}%",
                    "Daily Move ($)": f"${move_dollar:.2f}",
                    "Gamma P&L": f"${gamma_pnl:+,.2f}",
                    "Theta Cost": f"${daily_theta:+,.2f}",
                    "Net P&L": f"${net_pnl:+,.2f}",
                    "Profitable": "Yes" if net_pnl > 0 else "No",
                })

            gp_df = pd.DataFrame(gamma_pnl_data)
            st.dataframe(gp_df, use_container_width=True, hide_index=True)

            # Breakeven move
            if abs(total_gamma) > 1e-6 and daily_theta < 0:
                breakeven_move = np.sqrt(abs(2 * daily_theta / total_gamma))
                breakeven_pct = breakeven_move / hedge_spot * 100
                st.info(
                    f"**Breakeven daily move:** ${breakeven_move:.2f} ({breakeven_pct:.2f}%). "
                    f"The underlying must move at least this much daily for gamma scalping to offset theta decay."
                )
            elif daily_theta >= 0:
                st.success(
                    "Portfolio is **earning theta** — any gamma scalping profit is pure bonus."
                )
        else:
            st.warning(f"Could not fetch spot price for {hedge_ticker}.")


# ─── FOOTER ────────────────────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    "Portfolio Greeks are theoretical and based on Black-Scholes assumptions. "
    "Actual P&L may differ due to bid-ask spreads, early exercise, and model limitations. "
    "Not financial advice."
)
render_data_source_footer()
