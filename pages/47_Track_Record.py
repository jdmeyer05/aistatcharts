"""Track Record — Prediction Accuracy & Performance Attribution Dashboard

Institutional-grade accountability dashboard. Tracks every prediction, AI score,
ML forecast, signal scanner ranking, and position P&L — then evaluates against
actual outcomes with proper statistical rigor.

Tabs:
1. Platform Scorecard — overall accuracy, calibration, rolling windows
2. Tool Breakdown — per-source accuracy, confusion matrix, return attribution
3. Signal Engine — composite signal performance, agreement vs accuracy
4. Position Performance — closed position P&L, Greeks attribution, best/worst
5. Prediction Log — filterable table of every prediction with outcomes
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging
from datetime import datetime, timedelta, date

from src.layout import setup_page, error_boundary
from src.styles import COLORS
from src.data_engine import render_data_source_footer

logger = logging.getLogger(__name__)
setup_page("47_Track_Record")

st.title("Track Record")
st.markdown(
    "Every prediction, every signal, every trade — measured against reality. "
    "The most important page on the platform."
)

PLOTLY_NOBAR = {"displayModeBar": False}

SOURCE_LABELS = {
    "stock_analysis": "Stock Analysis (AI)",
    "signal_scanner": "Signal Scanner",
    "scenario_analysis": "Scenario Analysis",
    "calendar_scanner": "Calendar Spread",
    "rl_trading": "RL Trading Agent",
    "analyst_consensus": "Analyst Consensus",
    "vol_surface": "Vol Surface",
    "market_expectations": "Market Expectations",
    "correlation": "Cross-Asset Corr",
}

SOURCE_COLORS = {
    "stock_analysis": "#00d1ff",
    "signal_scanner": "#00ff88",
    "scenario_analysis": "#ff2277",
    "calendar_scanner": "#ffdd00",
    "rl_trading": "#a855f7",
    "analyst_consensus": "#06b6d4",
    "vol_surface": "#f97316",
    "market_expectations": "#eab308",
    "correlation": "#8b5cf6",
}

# ═══════════════════════════════════════════════
# CONTROLS
# ═══════════════════════════════════════════════

ctrl1, ctrl2, ctrl3 = st.columns([2, 1, 1])
with ctrl1:
    if st.button("Evaluate Pending Predictions", type="primary", use_container_width=True,
                  help="Fetches actual prices for predictions that are now 30+ days old"):
        try:
            from src.prediction_tracker import evaluate_pending
            with st.spinner("Evaluating predictions against actual outcomes..."):
                evaluate_pending()
            st.success("Evaluation complete — results updated.")
        except Exception as e:
            st.error(f"Evaluation failed: {e}")
with ctrl2:
    horizon = st.selectbox("Horizon", [30, 60, 90],
                            format_func=lambda d: f"{d}-Day", index=0)
with ctrl3:
    min_predictions = st.number_input("Min predictions", 1, 50, 3, help="Minimum predictions to show a source")

# ═══════════════════════════════════════════════
# LOAD DATA
# ═══════════════════════════════════════════════

try:
    from src.prediction_tracker import get_track_record, get_recent_predictions, get_all_sources
except ImportError:
    st.error("Prediction tracker module not found.")
    st.stop()

sources = get_all_sources()

if not sources:
    st.info(
        "No predictions recorded yet. Predictions are automatically saved when you:\n\n"
        "- Run **Stock Analysis** (AI consensus scores + price targets)\n"
        "- Run **ML Tactical Forecast** (predicted return + direction)\n"
        "- Run **Signal Scanner** (top/bottom ranked tickers)\n"
        "- Run **Scenario Analysis** (regime predictions)\n\n"
        "Come back after using these tools — your track record builds over time."
    )
    st.stop()

# Pre-fetch all data
overall = get_track_record(horizon=horizon)
by_source = {s: get_track_record(source=s, horizon=horizon) for s in sources}
all_predictions = get_recent_predictions(limit=500)

# ═══════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════

tab_scorecard, tab_tools, tab_signals, tab_positions, tab_log = st.tabs([
    "Platform Scorecard",
    "Tool Breakdown",
    "Signal Engine",
    "Position Performance",
    "Prediction Log",
])


# ═══════════════════════════════════════════════
# TAB 1: PLATFORM SCORECARD
# ═══════════════════════════════════════════════

with tab_scorecard, error_boundary("Platform Scorecard"):
    st.subheader("Platform Scorecard")

    # ── Hero metrics ──
    hm1, hm2, hm3, hm4, hm5 = st.columns(5)
    hm1.metric("Total Predictions", f"{overall['total_predictions']:,}")
    hm2.metric("Evaluated", f"{overall['evaluated']:,}")

    if overall["accuracy"] is not None:
        _acc = overall["accuracy"] * 100
        _acc_color = COLORS["success"] if _acc > 55 else (COLORS["warning"] if _acc > 50 else COLORS["danger"])
        hm3.markdown(
            f'<div style="text-align:center;padding:8px;border:2px solid {_acc_color};border-radius:8px;">'
            f'<div style="font-size:0.6rem;color:#888;">{horizon}d DIRECTION</div>'
            f'<div style="font-size:1.4rem;font-weight:800;color:{_acc_color};">{_acc:.1f}%</div>'
            f'<div style="font-size:0.55rem;color:#888;">{"Above Random" if _acc > 50 else "Below Random"}</div>'
            f'</div>', unsafe_allow_html=True)
    else:
        hm3.metric(f"{horizon}d Direction", "—")

    if overall["avg_actual_return"] is not None:
        _ret = overall["avg_actual_return"]
        _ret_color = COLORS["success"] if _ret > 0 else COLORS["danger"]
        hm4.metric(f"Avg {horizon}d Return", f"{_ret:+.1f}%",
                   delta="Profitable" if _ret > 0 else "Unprofitable",
                   delta_color="normal" if _ret > 0 else "inverse")
    else:
        hm4.metric(f"Avg {horizon}d Return", "—")

    _pending = overall["total_predictions"] - overall["evaluated"]
    hm5.metric("Pending Eval", f"{_pending:,}",
               help="Predictions not yet old enough to evaluate")

    # ── Accuracy by Source Bar Chart ──
    st.divider()
    st.markdown("#### Accuracy by Tool")

    acc_data = []
    for s, stats in by_source.items():
        if stats["evaluated"] >= min_predictions and stats["accuracy"] is not None:
            acc_data.append({
                "source": s,
                "label": SOURCE_LABELS.get(s, s),
                "accuracy": stats["accuracy"] * 100,
                "n": stats["evaluated"],
                "avg_return": stats["avg_actual_return"],
                "avg_predicted": stats["avg_predicted_return"],
            })

    if acc_data:
        acc_data.sort(key=lambda x: x["accuracy"], reverse=True)

        fig_acc = go.Figure()
        fig_acc.add_trace(go.Bar(
            x=[a["label"] for a in acc_data],
            y=[a["accuracy"] for a in acc_data],
            marker_color=[SOURCE_COLORS.get(a["source"], "#888") for a in acc_data],
            text=[f"{a['accuracy']:.1f}%<br>(n={a['n']})" for a in acc_data],
            textposition="outside",
            textfont=dict(size=10),
        ))
        fig_acc.add_hline(y=50, line_dash="dash", line_color=COLORS["text_muted"],
                           annotation_text="50% (random)", annotation_position="bottom right")
        fig_acc.update_layout(
            template="plotly_dark", height=380,
            yaxis_title=f"{horizon}-Day Direction Accuracy (%)",
            yaxis=dict(range=[max(0, min(a["accuracy"] for a in acc_data) - 10),
                              min(100, max(a["accuracy"] for a in acc_data) + 15)]),
            margin=dict(l=50, r=20, t=10, b=100),
            xaxis_tickangle=-30,
        )
        st.plotly_chart(fig_acc, use_container_width=True, config=PLOTLY_NOBAR)
    else:
        st.caption("Not enough evaluated predictions to chart accuracy.")

    # ── Return Attribution: Predicted vs Actual ──
    if acc_data:
        _has_returns = [a for a in acc_data if a["avg_return"] is not None and a["avg_predicted"] is not None]
        if _has_returns:
            st.divider()
            st.markdown("#### Predicted vs Actual Returns")
            st.caption("How well each tool's return forecasts match reality. "
                       "Points on the diagonal = perfectly calibrated.")

            fig_cal = go.Figure()
            for a in _has_returns:
                fig_cal.add_trace(go.Scatter(
                    x=[a["avg_predicted"]], y=[a["avg_return"]],
                    mode="markers+text", text=[a["label"]],
                    textposition="top center", textfont=dict(size=9),
                    marker=dict(size=12, color=SOURCE_COLORS.get(a["source"], "#888")),
                    showlegend=False,
                ))

            # Perfect calibration line
            _min_v = min(min(a["avg_predicted"] for a in _has_returns),
                         min(a["avg_return"] for a in _has_returns)) - 2
            _max_v = max(max(a["avg_predicted"] for a in _has_returns),
                         max(a["avg_return"] for a in _has_returns)) + 2
            fig_cal.add_trace(go.Scatter(
                x=[_min_v, _max_v], y=[_min_v, _max_v],
                mode="lines", line=dict(color="#555", dash="dash", width=1),
                name="Perfect Calibration", showlegend=True,
            ))
            fig_cal.add_hline(y=0, line_color="#333", line_width=0.5)
            fig_cal.add_vline(x=0, line_color="#333", line_width=0.5)
            fig_cal.update_layout(
                template="plotly_dark", height=350,
                xaxis_title=f"Avg Predicted {horizon}d Return (%)",
                yaxis_title=f"Avg Actual {horizon}d Return (%)",
                margin=dict(l=50, r=20, t=10, b=50),
            )
            st.plotly_chart(fig_cal, use_container_width=True, config=PLOTLY_NOBAR)

            # Calibration summary
            _over = [a for a in _has_returns if a["avg_predicted"] is not None and a["avg_return"] is not None
                     and abs(a["avg_predicted"]) > 0.1 and abs(a["avg_return"]) < abs(a["avg_predicted"])
                     and (a["avg_predicted"] > 0) == (a["avg_return"] > 0)]  # same direction but weaker
            _under = [a for a in _has_returns if a["avg_predicted"] is not None and a["avg_return"] is not None
                      and abs(a["avg_predicted"]) > 0.1 and abs(a["avg_return"]) > abs(a["avg_predicted"])
                      and (a["avg_predicted"] > 0) == (a["avg_return"] > 0)]  # same direction but stronger
            if _over:
                st.caption(f"**Overconfident tools** (predicted > actual): {', '.join(a['label'] for a in _over)}")
            if _under:
                st.caption(f"**Underconfident tools** (actual > predicted): {', '.join(a['label'] for a in _under)}")

    # ── Rolling Accuracy Over Time ──
    if all_predictions and len(all_predictions) >= 10:
        st.divider()
        st.markdown("#### Accuracy Over Time (Rolling 20-Prediction Window)")

        _eval_preds = []
        for p in all_predictions:
            outcomes = p.get("outcomes", {})
            _hd = outcomes.get(f"{horizon}d", {})
            if _hd.get("correct") is not None:
                _eval_preds.append({
                    "date": p.get("timestamp", "")[:10],
                    "correct": 1 if _hd["correct"] else 0,
                    "source": p.get("source", "unknown"),
                })

        if len(_eval_preds) >= 10:
            _eval_df = pd.DataFrame(_eval_preds).sort_values("date")
            _eval_df["rolling_acc"] = _eval_df["correct"].rolling(20, min_periods=5).mean() * 100

            fig_roll = go.Figure()
            fig_roll.add_trace(go.Scatter(
                x=_eval_df["date"], y=_eval_df["rolling_acc"],
                mode="lines", name="Rolling 20 Accuracy",
                line=dict(color=COLORS["accent"], width=2),
                fill="tonexty" if False else None,
            ))
            fig_roll.add_hline(y=50, line_dash="dash", line_color=COLORS["text_muted"],
                               annotation_text="Random (50%)")
            fig_roll.update_layout(
                template="plotly_dark", height=280,
                yaxis_title="Accuracy %", yaxis=dict(range=[20, 90]),
                margin=dict(l=50, r=20, t=10, b=40),
                hovermode="x unified",
            )
            st.plotly_chart(fig_roll, use_container_width=True, config=PLOTLY_NOBAR)

            # Per-source rolling (if enough data)
            _source_counts = _eval_df["source"].value_counts()
            _multi_sources = _source_counts[_source_counts >= 10].index.tolist()
            if len(_multi_sources) >= 2:
                st.markdown("##### By Tool")
                fig_roll_src = go.Figure()
                for src in _multi_sources:
                    _src_df = _eval_df[_eval_df["source"] == src].copy()
                    _src_df["rolling_acc"] = _src_df["correct"].rolling(10, min_periods=3).mean() * 100
                    fig_roll_src.add_trace(go.Scatter(
                        x=_src_df["date"], y=_src_df["rolling_acc"],
                        mode="lines", name=SOURCE_LABELS.get(src, src),
                        line=dict(color=SOURCE_COLORS.get(src, "#888"), width=2),
                    ))
                fig_roll_src.add_hline(y=50, line_dash="dash", line_color="#555")
                fig_roll_src.update_layout(
                    template="plotly_dark", height=300,
                    yaxis_title="Accuracy %", yaxis=dict(range=[20, 90]),
                    margin=dict(l=50, r=20, t=10, b=40),
                    hovermode="x unified", legend=dict(orientation="h", y=-0.2),
                )
                st.plotly_chart(fig_roll_src, use_container_width=True, config=PLOTLY_NOBAR)

    # ── Win Streaks & Cold Streaks ──
    if all_predictions:
        _eval_list = []
        for p in all_predictions:
            outcomes = p.get("outcomes", {})
            _hd = outcomes.get(f"{horizon}d", {})
            if _hd.get("correct") is not None:
                _eval_list.append(_hd["correct"])

        if len(_eval_list) >= 5:
            # Calculate streaks
            _current_streak = 0
            _current_type = None
            _max_win = 0
            _max_loss = 0
            for c in reversed(_eval_list):
                if _current_type is None:
                    _current_type = c
                    _current_streak = 1
                elif c == _current_type:
                    _current_streak += 1
                else:
                    break

            _s = 0
            for c in _eval_list:
                if c:
                    _s = max(0, _s) + 1
                    _max_win = max(_max_win, _s)
                else:
                    _s = min(0, _s) - 1
                    _max_loss = max(_max_loss, abs(_s))

            st.divider()
            sk1, sk2, sk3 = st.columns(3)
            _streak_label = f"{'Win' if _current_type else 'Loss'} Streak"
            _streak_color = "normal" if _current_type else "inverse"
            sk1.metric("Current Streak", f"{_current_streak} {'Wins' if _current_type else 'Losses'}",
                       delta=_streak_label, delta_color=_streak_color)
            sk2.metric("Best Win Streak", f"{_max_win} in a row")
            sk3.metric("Worst Loss Streak", f"{_max_loss} in a row")


# ═══════════════════════════════════════════════
# TAB 2: TOOL BREAKDOWN
# ═══════════════════════════════════════════════

with tab_tools, error_boundary("Tool Breakdown"):
    st.subheader("Tool-by-Tool Analysis")

    for src in sorted(sources, key=lambda s: by_source[s].get("evaluated", 0), reverse=True):
        stats = by_source[src]
        if stats["total_predictions"] == 0:
            continue

        label = SOURCE_LABELS.get(src, src)
        color = SOURCE_COLORS.get(src, "#888")

        with st.expander(f"{label} — {stats['evaluated']} evaluated, "
                         f"{'%.1f' % (stats['accuracy']*100) + '%' if stats['accuracy'] is not None else 'N/A'} accuracy",
                         expanded=False):

            # Source metrics
            sm1, sm2, sm3, sm4 = st.columns(4)
            sm1.metric("Total", stats["total_predictions"])
            sm2.metric("Evaluated", stats["evaluated"])
            if stats["accuracy"] is not None:
                sm3.metric("Direction Accuracy", f"{stats['accuracy']*100:.1f}%")
            else:
                sm3.metric("Direction Accuracy", "—")
            if stats["avg_actual_return"] is not None:
                sm4.metric("Avg Return", f"{stats['avg_actual_return']:+.1f}%")
            else:
                sm4.metric("Avg Return", "—")

            # Confusion matrix
            _src_preds = [p for p in all_predictions if p.get("source", "") == src]
            _tp = _fp = _tn = _fn = 0
            _returns_bull = []
            _returns_bear = []

            for p in _src_preds:
                pred = p.get("prediction", {})
                outcomes = p.get("outcomes", {})
                _hd = outcomes.get(f"{horizon}d", {})
                direction = pred.get("direction", "").lower()
                actual_ret = _hd.get("return_pct")

                if actual_ret is None:
                    continue

                if "bull" in direction or "buy" in direction or "long" in direction:
                    if actual_ret > 0:
                        _tp += 1
                    else:
                        _fp += 1
                    _returns_bull.append(actual_ret)
                elif "bear" in direction or "sell" in direction or "short" in direction:
                    if actual_ret < 0:
                        _tn += 1
                    else:
                        _fn += 1
                    _returns_bear.append(actual_ret)

            _total_cm = _tp + _fp + _tn + _fn
            if _total_cm >= 3:
                st.markdown("##### Confusion Matrix")
                cm1, cm2 = st.columns(2)
                with cm1:
                    fig_cm = go.Figure(go.Heatmap(
                        z=[[_tp, _fp], [_fn, _tn]],
                        x=["Actual Up", "Actual Down"],
                        y=["Predicted Up", "Predicted Down"],
                        colorscale=[[0, "#1a1a2e"], [1, "#00d1ff"]],
                        text=[[f"TP\n{_tp}", f"FP\n{_fp}"], [f"FN\n{_fn}", f"TN\n{_tn}"]],
                        texttemplate="%{text}",
                        textfont=dict(size=14),
                        showscale=False,
                        hoverinfo="skip",
                    ))
                    fig_cm.update_layout(
                        template="plotly_dark", height=200,
                        margin=dict(l=80, r=20, t=10, b=40),
                    )
                    st.plotly_chart(fig_cm, use_container_width=True, config=PLOTLY_NOBAR)

                with cm2:
                    _precision = _tp / (_tp + _fp) if (_tp + _fp) > 0 else 0
                    _recall = _tp / (_tp + _fn) if (_tp + _fn) > 0 else 0
                    _f1 = 2 * _precision * _recall / (_precision + _recall) if (_precision + _recall) > 0 else 0
                    _spec = _tn / (_tn + _fp) if (_tn + _fp) > 0 else 0

                    st.markdown(f"""
| Metric | Value |
|--------|-------|
| **Precision** (bull calls correct) | {_precision:.1%} |
| **Recall** (caught actual ups) | {_recall:.1%} |
| **F1 Score** | {_f1:.2f} |
| **Specificity** (bear calls correct) | {_spec:.1%} |
| **Total Evaluated** | {_total_cm} |
""")

            # Return distribution
            if _returns_bull or _returns_bear:
                st.markdown("##### Return Distribution by Predicted Direction")
                fig_ret = go.Figure()
                if _returns_bull:
                    fig_ret.add_trace(go.Histogram(
                        x=_returns_bull, name="Bullish Calls",
                        marker_color=COLORS["success"], opacity=0.7,
                        nbinsx=20,
                    ))
                if _returns_bear:
                    fig_ret.add_trace(go.Histogram(
                        x=_returns_bear, name="Bearish Calls",
                        marker_color=COLORS["danger"], opacity=0.7,
                        nbinsx=20,
                    ))
                fig_ret.add_vline(x=0, line_color="white", line_width=1)
                fig_ret.update_layout(
                    template="plotly_dark", height=220, barmode="overlay",
                    xaxis_title=f"Actual {horizon}d Return (%)", yaxis_title="Count",
                    margin=dict(l=50, r=20, t=10, b=40),
                    legend=dict(orientation="h", y=-0.25),
                )
                st.plotly_chart(fig_ret, use_container_width=True, config=PLOTLY_NOBAR)

            # Best and worst calls
            _src_with_returns = []
            for p in _src_preds:
                outcomes = p.get("outcomes", {})
                _hd = outcomes.get(f"{horizon}d", {})
                if _hd.get("return_pct") is not None:
                    _src_with_returns.append({
                        "ticker": p["ticker"],
                        "date": p.get("timestamp", "")[:10],
                        "direction": p.get("prediction", {}).get("direction", "—"),
                        "return": _hd["return_pct"],
                        "correct": _hd.get("correct", False),
                    })

            if _src_with_returns:
                _src_with_returns.sort(key=lambda x: x["return"], reverse=True)
                bw1, bw2 = st.columns(2)
                with bw1:
                    st.markdown("##### Best Calls")
                    _best = _src_with_returns[:5]
                    for b in _best:
                        _icon = "+" if b["correct"] else "x"
                        st.caption(f"[{_icon}] **{b['ticker']}** {b['date']} → {b['return']:+.1f}% ({b['direction']})")
                with bw2:
                    st.markdown("##### Worst Calls")
                    _worst = _src_with_returns[-5:]
                    for w in reversed(_worst):
                        _icon = "+" if w["correct"] else "x"
                        st.caption(f"[{_icon}] **{w['ticker']}** {w['date']} → {w['return']:+.1f}% ({w['direction']})")


# ═══════════════════════════════════════════════
# TAB 3: SIGNAL ENGINE
# ═══════════════════════════════════════════════

with tab_signals, error_boundary("Signal Engine Performance"):
    st.subheader("Signal Engine Performance")
    st.caption("Evaluates the Unified Signal Engine — how well do composite signals "
               "and multi-source agreement predict outcomes?")

    try:
        from src.signal_engine import get_signal_summary, get_top_trade_ideas, SOURCE_WEIGHTS, compute_composite

        summary = get_signal_summary()

        # Current signal state
        sg1, sg2, sg3, sg4 = st.columns(4)
        sg1.metric("Active Signals", summary["n_tickers"])
        sg2.metric("Bullish", summary["n_bullish"])
        sg3.metric("Bearish", summary["n_bearish"])
        sg4.metric("Avg Conviction", f"{summary['avg_conviction']:.0%}")

        # Source weights visualization
        st.divider()
        st.markdown("#### Source Weights")
        st.caption("Higher-weighted sources have more influence on composite signals. "
                   "Weights are assigned by expected edge strength.")

        _sw_sorted = sorted(SOURCE_WEIGHTS.items(), key=lambda x: x[1], reverse=True)
        fig_sw = go.Figure(go.Bar(
            x=[SOURCE_LABELS.get(s, s) for s, _ in _sw_sorted],
            y=[w for _, w in _sw_sorted],
            marker_color=[SOURCE_COLORS.get(s, "#888") for s, _ in _sw_sorted],
            text=[f"{w:.1f}x" for _, w in _sw_sorted],
            textposition="outside",
        ))
        fig_sw.update_layout(
            template="plotly_dark", height=300,
            yaxis_title="Weight Multiplier",
            margin=dict(l=50, r=20, t=10, b=100),
            xaxis_tickangle=-30,
        )
        st.plotly_chart(fig_sw, use_container_width=True, config=PLOTLY_NOBAR)

        # Top trade ideas
        st.divider()
        st.markdown("#### Current Top Trade Ideas")
        ideas = get_top_trade_ideas(10)
        if ideas:
            _idea_rows = []
            for idea in ideas:
                _dir_color = COLORS["success"] if idea["overall_direction"] == "bull" else (
                    COLORS["danger"] if idea["overall_direction"] == "bear" else COLORS["text_muted"])
                _idea_rows.append({
                    "Ticker": idea["ticker"],
                    "Direction": idea["overall_direction"].upper(),
                    "Conviction": f"{idea['overall_conviction']:.0%}",
                    "Agreement": f"{idea['signal_agreement']:.0%}",
                    "Sources": idea["n_signals"],
                    "Vol View": idea["vol_regime"],
                })
            st.dataframe(pd.DataFrame(_idea_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("No active trade ideas. Run analysis pages to generate signals.")

        # Agreement vs accuracy analysis
        st.divider()
        st.markdown("#### Agreement vs Outcome")
        st.caption("Do predictions with higher cross-source agreement tend to be more accurate?")

        _agree_data = []
        for p in all_predictions:
            outcomes = p.get("outcomes", {})
            _hd = outcomes.get(f"{horizon}d", {})
            pred = p.get("prediction", {})
            if _hd.get("correct") is not None and pred.get("conviction"):
                _agree_data.append({
                    "conviction": float(pred["conviction"]) if pred.get("conviction") else 0.5,
                    "correct": 1 if _hd["correct"] else 0,
                    "return": _hd.get("return_pct", 0),
                })

        if len(_agree_data) >= 10:
            _ag_df = pd.DataFrame(_agree_data)
            # Bin by conviction
            _ag_df["conv_bin"] = pd.cut(_ag_df["conviction"], bins=[0, 0.3, 0.5, 0.7, 0.9, 1.0],
                                         labels=["0-30%", "30-50%", "50-70%", "70-90%", "90-100%"])
            _bin_acc = _ag_df.groupby("conv_bin", observed=True).agg(
                accuracy=("correct", "mean"),
                count=("correct", "count"),
                avg_return=("return", "mean"),
            ).reset_index()

            fig_conv = make_subplots(specs=[[{"secondary_y": True}]])
            fig_conv.add_trace(go.Bar(
                x=_bin_acc["conv_bin"].astype(str), y=_bin_acc["accuracy"] * 100,
                name="Accuracy %", marker_color=COLORS["accent"],
                text=[f"{a:.0f}%" for a in _bin_acc["accuracy"] * 100],
                textposition="outside",
            ), secondary_y=False)
            fig_conv.add_trace(go.Scatter(
                x=_bin_acc["conv_bin"].astype(str), y=_bin_acc["count"],
                name="# Predictions", mode="lines+markers",
                line=dict(color=COLORS["text_muted"], width=2),
            ), secondary_y=True)
            fig_conv.add_hline(y=50, line_dash="dash", line_color="#555", secondary_y=False)
            fig_conv.update_layout(
                template="plotly_dark", height=300,
                margin=dict(l=50, r=50, t=10, b=40),
                legend=dict(orientation="h", y=-0.2),
            )
            fig_conv.update_yaxes(title_text="Accuracy %", secondary_y=False)
            fig_conv.update_yaxes(title_text="Count", secondary_y=True)
            st.plotly_chart(fig_conv, use_container_width=True, config=PLOTLY_NOBAR)

            _high_conv = _ag_df[_ag_df["conviction"] >= 0.7]
            _low_conv = _ag_df[_ag_df["conviction"] < 0.5]
            if not _high_conv.empty and not _low_conv.empty:
                _hc_acc = _high_conv["correct"].mean() * 100
                _lc_acc = _low_conv["correct"].mean() * 100
                _edge = _hc_acc - _lc_acc
                if _edge > 5:
                    st.success(f"High-conviction signals ({_hc_acc:.0f}%) outperform low-conviction ({_lc_acc:.0f}%) "
                               f"by **{_edge:.0f}pp** — conviction is a reliable edge indicator.")
                elif _edge < -5:
                    st.warning(f"High-conviction signals ({_hc_acc:.0f}%) underperform low-conviction ({_lc_acc:.0f}%) "
                               f"by **{abs(_edge):.0f}pp** — overconfidence may be an issue.")
                else:
                    st.info(f"Conviction has minimal effect on accuracy ({_hc_acc:.0f}% vs {_lc_acc:.0f}%).")
        else:
            st.caption("Need 10+ evaluated predictions with conviction scores to analyze agreement vs accuracy.")

    except ImportError:
        st.info("Signal engine module not available.")
    except Exception as e:
        st.warning(f"Could not load signal engine data: {e}")


# ═══════════════════════════════════════════════
# TAB 4: POSITION PERFORMANCE
# ═══════════════════════════════════════════════

with tab_positions, error_boundary("Position Performance"):
    st.subheader("Position Performance")
    st.caption("Tracks actual P&L from the Position Book — not predictions, real trades.")

    try:
        from src.position_book import get_positions, get_portfolio_summary

        # Closed positions
        closed = get_positions(status="closed")
        open_pos = get_positions(status="open")

        pc1, pc2, pc3, pc4 = st.columns(4)
        pc1.metric("Open Positions", len(open_pos))
        pc2.metric("Closed Positions", len(closed))

        if closed:
            _total_pnl = 0
            _wins = 0
            _pnl_list = []
            for pos in closed:
                if pos.get("entry_price") and pos.get("close_price"):
                    _pnl = (pos["close_price"] - pos["entry_price"]) * pos.get("qty", 1)
                    if pos.get("type", "").lower() in ("put", "short"):
                        _pnl = -_pnl
                    _total_pnl += _pnl
                    _pnl_list.append(_pnl)
                    if _pnl > 0:
                        _wins += 1

            _win_rate = _wins / len(_pnl_list) * 100 if _pnl_list else 0
            pc3.metric("Win Rate", f"{_win_rate:.0f}%",
                       delta="Above 50%" if _win_rate > 50 else "Below 50%",
                       delta_color="normal" if _win_rate > 50 else "inverse")
            _pnl_color = "normal" if _total_pnl > 0 else "inverse"
            pc4.metric("Total Realized P&L", f"${_total_pnl:+,.2f}",
                       delta_color=_pnl_color)

            # P&L distribution
            if _pnl_list:
                st.divider()
                st.markdown("#### P&L Distribution (Closed Positions)")

                fig_pnl = go.Figure()
                _pnl_colors = [COLORS["success"] if p > 0 else COLORS["danger"] for p in _pnl_list]
                fig_pnl.add_trace(go.Histogram(
                    x=_pnl_list, nbinsx=20,
                    marker_color=COLORS["accent"], opacity=0.8,
                ))
                fig_pnl.add_vline(x=0, line_color="white", line_width=1)
                _avg_pnl = np.mean(_pnl_list)
                fig_pnl.add_vline(x=_avg_pnl, line_dash="dash", line_color=COLORS["warning"],
                                  annotation_text=f"Avg: ${_avg_pnl:.2f}")
                fig_pnl.update_layout(
                    template="plotly_dark", height=250,
                    xaxis_title="P&L ($)", yaxis_title="Count",
                    margin=dict(l=50, r=20, t=10, b=40),
                )
                st.plotly_chart(fig_pnl, use_container_width=True, config=PLOTLY_NOBAR)

                # Win/loss stats
                _wins_pnl = [p for p in _pnl_list if p > 0]
                _losses_pnl = [p for p in _pnl_list if p < 0]
                wl1, wl2, wl3 = st.columns(3)
                if _wins_pnl:
                    wl1.metric("Avg Win", f"${np.mean(_wins_pnl):+,.2f}")
                if _losses_pnl:
                    wl2.metric("Avg Loss", f"${np.mean(_losses_pnl):+,.2f}")
                if _wins_pnl and _losses_pnl:
                    _profit_factor = abs(sum(_wins_pnl) / sum(_losses_pnl)) if sum(_losses_pnl) != 0 else float("inf")
                    _pf_color = COLORS["success"] if _profit_factor > 1.5 else (
                        COLORS["warning"] if _profit_factor > 1.0 else COLORS["danger"])
                    wl3.markdown(
                        f'<div style="text-align:center;padding:8px;border:1px solid {_pf_color};border-radius:6px;">'
                        f'<div style="font-size:0.6rem;color:#888;">PROFIT FACTOR</div>'
                        f'<div style="font-size:1.2rem;font-weight:700;color:{_pf_color};">{_profit_factor:.2f}</div>'
                        f'</div>', unsafe_allow_html=True)

            # Closed positions table
            st.divider()
            st.markdown("#### Closed Positions Detail")
            _closed_rows = []
            for pos in sorted(closed, key=lambda p: p.get("close_date", ""), reverse=True):
                _pnl_val = None
                if pos.get("entry_price") and pos.get("close_price"):
                    _pnl_val = (pos["close_price"] - pos["entry_price"]) * pos.get("qty", 1)
                    if pos.get("type", "").lower() in ("put", "short"):
                        _pnl_val = -_pnl_val

                _closed_rows.append({
                    "Ticker": pos.get("ticker", "—"),
                    "Type": pos.get("type", "—"),
                    "Entry": f"${pos['entry_price']:.2f}" if pos.get("entry_price") else "—",
                    "Exit": f"${pos['close_price']:.2f}" if pos.get("close_price") else "—",
                    "Qty": pos.get("qty", 1),
                    "P&L": f"${_pnl_val:+,.2f}" if _pnl_val is not None else "—",
                    "Entry Date": pos.get("entry_date", "—")[:10],
                    "Exit Date": pos.get("close_date", "—")[:10] if pos.get("close_date") else "—",
                })
            if _closed_rows:
                st.dataframe(pd.DataFrame(_closed_rows), use_container_width=True, hide_index=True, height=300)

        else:
            st.info("No closed positions yet. Close positions in the Position Book to see P&L analysis here.")

        # Open positions summary
        if open_pos:
            st.divider()
            st.markdown("#### Open Positions (Unrealized)")
            _open_rows = []
            for pos in open_pos:
                _thesis = pos.get("journal", {}).get("entry_thesis", "—")
                _open_rows.append({
                    "Ticker": pos.get("ticker", "—"),
                    "Type": pos.get("type", "—"),
                    "Entry": f"${pos['entry_price']:.2f}" if pos.get("entry_price") else "—",
                    "Qty": pos.get("qty", 1),
                    "Entry Date": pos.get("entry_date", "—")[:10],
                    "Thesis": _thesis[:60] + "..." if len(_thesis) > 60 else _thesis,
                })
            st.dataframe(pd.DataFrame(_open_rows), use_container_width=True, hide_index=True)

    except ImportError:
        st.info("Position book module not available.")
    except Exception as e:
        st.warning(f"Could not load position data: {e}")


# ═══════════════════════════════════════════════
# TAB 5: PREDICTION LOG
# ═══════════════════════════════════════════════

with tab_log, error_boundary("Prediction Log"):
    st.subheader("Full Prediction Log")

    # Filters
    fl1, fl2, fl3 = st.columns(3)
    with fl1:
        _filter_source = st.selectbox("Tool", ["All"] + [SOURCE_LABELS.get(s, s) for s in sources], key="log_src")
    with fl2:
        _filter_status = st.selectbox("Status", ["All", "Evaluated", "Pending"], key="log_status")
    with fl3:
        _filter_direction = st.selectbox("Direction", ["All", "Bullish", "Bearish", "Neutral"], key="log_dir")

    # Apply filters
    _filtered = list(all_predictions)

    if _filter_source != "All":
        _src_key = next((s for s, l in SOURCE_LABELS.items() if l == _filter_source), _filter_source)
        _filtered = [p for p in _filtered if p.get("source", "") == _src_key]

    if _filter_status == "Evaluated":
        _filtered = [p for p in _filtered if p.get("outcomes", {}).get(f"{horizon}d", {}).get("correct") is not None]
    elif _filter_status == "Pending":
        _filtered = [p for p in _filtered if p.get("outcomes", {}).get(f"{horizon}d", {}).get("correct") is None]

    if _filter_direction != "All":
        _dir_lower = _filter_direction.lower()[:4]
        _filtered = [p for p in _filtered
                     if _dir_lower in p.get("prediction", {}).get("direction", "").lower()]

    st.caption(f"Showing {len(_filtered)} predictions")

    if _filtered:
        rows = []
        for p in reversed(_filtered):
            pred = p.get("prediction", {})
            outcomes = p.get("outcomes", {})
            _hd = outcomes.get(f"{horizon}d", {})

            _correct = _hd.get("correct")
            if _correct is True:
                _status = "Correct"
            elif _correct is False:
                _status = "Wrong"
            else:
                _status = "Pending"

            rows.append({
                "Date": p.get("timestamp", "")[:10],
                "Tool": SOURCE_LABELS.get(p.get("source", ""), p.get("source", "")),
                "Ticker": p["ticker"],
                "Direction": pred.get("direction", "—"),
                "Conviction": f"{float(pred['conviction']):.0%}" if pred.get("conviction") else "—",
                "Score": pred.get("score", "—"),
                "Spot": f"${p['spot_at_prediction']:,.2f}" if p.get("spot_at_prediction") else "—",
                f"{horizon}d Return": f"{_hd['return_pct']:+.1f}%" if _hd.get("return_pct") is not None else "—",
                "Result": _status,
            })

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=500)

        # Summary stats for filtered set
        _eval_filtered = [r for r in rows if r["Result"] in ("Correct", "Wrong")]
        if _eval_filtered:
            _n_correct = sum(1 for r in _eval_filtered if r["Result"] == "Correct")
            st.caption(
                f"**Filtered accuracy:** {_n_correct}/{len(_eval_filtered)} = "
                f"{_n_correct/len(_eval_filtered)*100:.1f}%"
            )
    else:
        st.info("No predictions match the selected filters.")


# ═══════════════════════════════════════════════
# FOOTER
# ═══════════════════════════════════════════════

st.markdown("---")
st.caption(
    "Track record builds over time as you use the platform's analysis tools. "
    "Predictions are automatically recorded and evaluated against actual outcomes. "
    "A tool consistently below 50% accuracy is worse than random — this page helps you know which tools to trust."
)
render_data_source_footer()
