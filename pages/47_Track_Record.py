"""Track Record — Prediction Accuracy Dashboard

Shows how accurate every prediction tool on the platform has been.
This is the single most important page for building trust in the platform's signals.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import logging

from src.layout import setup_page, error_boundary
from src.styles import COLORS
from src.data_engine import render_data_source_footer

logger = logging.getLogger(__name__)
setup_page("47_Track_Record")

st.title("Track Record")
st.markdown(
    "How accurate are our predictions? This page tracks every AI score, ML forecast, "
    "and signal scanner ranking — then compares to what actually happened."
)

PLOTLY_NOBAR = {"displayModeBar": False}

SOURCE_LABELS = {
    "stock_analysis": "Stock Analysis (AI Consensus)",
    "ml_predictor": "ML Tactical Forecast",
    "signal_scanner": "Signal Scanner (Top/Bottom Picks)",
    "scenario_analysis": "Scenario Analysis (Regime)",
    "calendar_scanner": "Calendar Spread Scanner",
}

# ─── CONTROLS ──────────────────────────────────────────────────────────────────

ec1, ec2 = st.columns(2)
with ec1:
    if st.button("Evaluate Pending Predictions", type="primary", use_container_width=True,
                  help="Fetches actual prices for predictions that are now 30+ days old"):
        try:
            from src.prediction_tracker import evaluate_pending
            with st.spinner("Evaluating predictions against actual outcomes..."):
                evaluate_pending()
            st.success("Evaluation complete.")
        except Exception as e:
            st.error(f"Evaluation failed: {e}")

with ec2:
    horizon = st.selectbox("Evaluation Horizon", [30, 60, 90],
                            format_func=lambda d: f"{d}-Day Outcome", index=0)

# ─── LOAD DATA ─────────────────────────────────────────────────────────────────

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
        "- Run **Signal Scanner** (top/bottom ranked tickers)\n\n"
        "Come back after using these tools — your track record builds over time."
    )
    st.stop()


# ─── OVERALL ACCURACY ─────────────────────────────────────────────────────────

with error_boundary("Overall Accuracy"):
    st.subheader("Overall Platform Accuracy")

    overall = get_track_record(horizon=horizon)
    by_source = {s: get_track_record(source=s, horizon=horizon) for s in sources}

    # Summary metrics
    oc1, oc2, oc3, oc4 = st.columns(4)
    oc1.metric("Total Predictions", overall["total_predictions"])
    oc2.metric("Evaluated", overall["evaluated"])
    if overall["accuracy"] is not None:
        _acc = overall["accuracy"] * 100
        _acc_color = COLORS["success"] if _acc > 55 else (COLORS["warning"] if _acc > 50 else COLORS["danger"])
        oc3.metric(f"{horizon}d Direction Accuracy", f"{_acc:.1f}%")
    else:
        oc3.metric(f"{horizon}d Direction Accuracy", "—")
    if overall["avg_actual_return"] is not None:
        oc4.metric(f"Avg {horizon}d Return (all picks)", f"{overall['avg_actual_return']:+.1f}%")
    else:
        oc4.metric(f"Avg {horizon}d Return", "—")

    # Accuracy by source
    if len(by_source) > 1:
        st.markdown("#### Accuracy by Tool")
        source_rows = []
        for s, stats in by_source.items():
            if stats["total_predictions"] == 0:
                continue
            source_rows.append({
                "Tool": SOURCE_LABELS.get(s, s),
                "Predictions": stats["total_predictions"],
                "Evaluated": stats["evaluated"],
                "Accuracy": f"{stats['accuracy']*100:.1f}%" if stats["accuracy"] is not None else "—",
                "Avg Return": f"{stats['avg_actual_return']:+.1f}%" if stats["avg_actual_return"] is not None else "—",
                "Avg Predicted": f"{stats['avg_predicted_return']:+.1f}%" if stats["avg_predicted_return"] is not None else "—",
            })
        if source_rows:
            st.dataframe(pd.DataFrame(source_rows), use_container_width=True, hide_index=True)

    # Accuracy bar chart
    acc_data = [(SOURCE_LABELS.get(s, s), stats["accuracy"])
                 for s, stats in by_source.items()
                 if stats["accuracy"] is not None]
    if acc_data:
        fig_acc = go.Figure(go.Bar(
            x=[a[0] for a in acc_data],
            y=[a[1] * 100 for a in acc_data],
            marker_color=[COLORS["success"] if a[1] > 0.55 else
                          (COLORS["warning"] if a[1] > 0.50 else COLORS["danger"])
                          for a in acc_data],
            text=[f"{a[1]*100:.1f}%" for a in acc_data],
            textposition="outside",
        ))
        fig_acc.add_hline(y=50, line_dash="dash", line_color=COLORS["text_muted"],
                           annotation_text="50% (random)")
        fig_acc.update_layout(
            template="plotly_dark", height=350,
            yaxis_title=f"{horizon}-Day Direction Accuracy (%)",
            yaxis=dict(range=[30, 80]),
            margin=dict(l=50, r=20, t=10, b=80),
        )
        st.plotly_chart(fig_acc, use_container_width=True, config=PLOTLY_NOBAR)


# ─── RECENT PREDICTIONS ───────────────────────────────────────────────────────

with error_boundary("Recent Predictions"):
    st.subheader("Recent Predictions")

    selected_source = st.selectbox("Filter by tool", ["All"] + [SOURCE_LABELS.get(s, s) for s in sources])
    _source_filter = None
    if selected_source != "All":
        _source_filter = next((s for s, l in SOURCE_LABELS.items() if l == selected_source), None)
        if not _source_filter:
            _source_filter = selected_source

    recent = get_recent_predictions(source=_source_filter, limit=50)

    if recent:
        rows = []
        for p in reversed(recent):
            pred = p.get("prediction", {})
            outcomes = p.get("outcomes", {})
            _30d = outcomes.get("30d", {})

            rows.append({
                "Date": p["timestamp"][:10],
                "Tool": SOURCE_LABELS.get(p["source"], p["source"]),
                "Ticker": p["ticker"],
                "Direction": pred.get("direction", "—"),
                "Score": pred.get("score", "—"),
                "Spot": f"${p['spot_at_prediction']:,.2f}" if p.get("spot_at_prediction") else "—",
                "30d Return": f"{_30d['return_pct']:+.1f}%" if _30d.get("return_pct") is not None else "pending",
                "Correct": "Yes" if _30d.get("correct") is True else ("No" if _30d.get("correct") is False else "—"),
            })

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=400)
    else:
        st.info("No predictions to display.")


# ─── FOOTER ────────────────────────────────────────────────────────────────────

st.markdown("---")
st.caption(
    "Track record builds over time as you use the platform's analysis tools. "
    "Predictions are automatically recorded and evaluated against actual outcomes. "
    "A tool that's consistently below 50% accuracy is worse than random — "
    "this page helps you identify which tools to trust."
)
render_data_source_footer()
