import streamlit as st
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from src.auth import check_auth

st.set_page_config(page_title="Spread Analyzer", layout="wide")
check_auth() # The firewall

st.title("🕸️ Multi-Leg Spread Analyzer")
st.markdown("Build complex options structures and visualize payout profiles at expiration.")

# --- SIDEBAR: STRATEGY BUILDER ---
with st.sidebar:
    st.header("Strategy Configuration")
    spot_price = st.number_input("Current Underlying Spot Price ($)", min_value=1.0, value=100.0, step=1.0)
    
    strategy = st.selectbox("Select Strategy Template", [
        "Custom (Manual Entry)",
        "Long Straddle",
        "Long Strangle",
        "Bull Call Spread",
        "Bear Put Spread",
        "Iron Condor"
    ])
    
    st.divider()
    st.caption("Leg Parameters (Auto-populated by Template)")

    # Define leg structures based on standard quant logic
    legs = []
    
    if strategy == "Long Straddle":
        legs = [
            {"type": "Call", "strike": spot_price, "premium": 3.0, "position": 1},
            {"type": "Put", "strike": spot_price, "premium": 3.0, "position": 1}
        ]
    elif strategy == "Long Strangle":
        legs = [
            {"type": "Call", "strike": spot_price * 1.05, "premium": 1.5, "position": 1},
            {"type": "Put", "strike": spot_price * 0.95, "premium": 1.5, "position": 1}
        ]
    elif strategy == "Bull Call Spread":
        legs = [
            {"type": "Call", "strike": spot_price, "premium": 3.5, "position": 1},      # Long leg
            {"type": "Call", "strike": spot_price * 1.05, "premium": 1.5, "position": -1} # Short leg
        ]
    elif strategy == "Bear Put Spread":
        legs = [
            {"type": "Put", "strike": spot_price, "premium": 3.5, "position": 1},       # Long leg
            {"type": "Put", "strike": spot_price * 0.95, "premium": 1.5, "position": -1} # Short leg
        ]
    elif strategy == "Iron Condor":
        legs = [
            {"type": "Put", "strike": spot_price * 0.90, "premium": 0.5, "position": 1},  # Long Put (Wing)
            {"type": "Put", "strike": spot_price * 0.95, "premium": 1.5, "position": -1}, # Short Put (Body)
            {"type": "Call", "strike": spot_price * 1.05, "premium": 1.5, "position": -1},# Short Call (Body)
            {"type": "Call", "strike": spot_price * 1.10, "premium": 0.5, "position": 1}  # Long Call (Wing)
        ]
    else: # Custom
        legs = [
            {"type": "Call", "strike": spot_price, "premium": 2.0, "position": 1}
        ]
        
    # Generate UI for the legs
    active_legs = []
    for i, leg in enumerate(legs):
        with st.expander(f"Leg {i+1}: {leg['type']}", expanded=True):
            col1, col2 = st.columns(2)
            action = col1.selectbox(f"Action", ["Buy", "Sell"], index=0 if leg['position'] == 1 else 1, key=f"act_{i}")
            opt_type = col2.selectbox(f"Type", ["Call", "Put"], index=0 if leg['type'] == "Call" else 1, key=f"typ_{i}")
            strike = col1.number_input(f"Strike", value=float(leg['strike']), step=1.0, key=f"str_{i}")
            premium = col2.number_input(f"Premium", value=float(leg['premium']), step=0.1, key=f"prm_{i}")
            qty = st.number_input(f"Contracts", min_value=1, value=1, key=f"qty_{i}")
            
            direction = 1 if action == "Buy" else -1
            active_legs.append({
                "type": opt_type,
                "strike": strike,
                "premium": premium,
                "position": direction * qty
            })

# --- MATH ENGINE ---
# Simulate a price range +/- 20% around the spot price
prices = np.linspace(spot_price * 0.8, spot_price * 1.2, 500)
total_pnl = np.zeros_like(prices)
net_debit_credit = 0

# Calculate vectorized PnL for each leg
for leg in active_legs:
    if leg["type"] == "Call":
        intrinsic_val = np.maximum(prices - leg["strike"], 0)
    else: # Put
        intrinsic_val = np.maximum(leg["strike"] - prices, 0)
        
    # (Value at expiry - Premium paid/received) * direction * 100 multiplier
    leg_pnl = (intrinsic_val - leg["premium"]) * leg["position"] * 100
    total_pnl += leg_pnl
    
    # Calculate initial cash flow
    net_debit_credit -= leg["premium"] * leg["position"] * 100

# Calculate risk metrics
max_profit = np.max(total_pnl)
max_loss = np.min(total_pnl)

# Find Breakevens (where PnL crosses 0)
zero_crossings = np.where(np.diff(np.sign(total_pnl)))[0]
breakevens = [prices[i] for i in zero_crossings]

# --- RENDER DASHBOARD ---
st.subheader("Expiration PnL Profile")

col1, col2, col3, col4 = st.columns(4)
col1.metric("Net Premium Flow", f"${net_debit_credit:,.2f}", "Credit" if net_debit_credit > 0 else "Debit", delta_color="inverse" if net_debit_credit < 0 else "normal")
col2.metric("Max Profit", "Unlimited" if max_profit > 10000 else f"${max_profit:,.2f}")
col3.metric("Max Loss", "Unlimited" if max_loss < -10000 else f"${max_loss:,.2f}")
if breakevens:
    bes_str = " | ".join([f"${be:.2f}" for be in breakevens])
    col4.metric("Breakeven(s)", bes_str)
else:
    col4.metric("Breakeven(s)", "None")

# --- PLOTLY CHART ---
fig = go.Figure()

# Plot the Zero Line
fig.add_hline(y=0, line_dash="dash", line_color="white", opacity=0.5)

# Plot the Spot Price Line
fig.add_vline(x=spot_price, line_dash="dot", line_color="yellow", annotation_text="Spot", annotation_position="top right")

# Fill green for profit, red for loss
fig.add_trace(go.Scatter(
    x=prices, y=np.where(total_pnl > 0, total_pnl, 0),
    fill='tozeroy', fillcolor='rgba(0, 255, 0, 0.2)', line=dict(color='rgba(255,255,255,0)'),
    showlegend=False, hoverinfo='skip'
))

fig.add_trace(go.Scatter(
    x=prices, y=np.where(total_pnl < 0, total_pnl, 0),
    fill='tozeroy', fillcolor='rgba(255, 0, 0, 0.2)', line=dict(color='rgba(255,255,255,0)'),
    showlegend=False, hoverinfo='skip'
))

# Plot the actual PnL line
fig.add_trace(go.Scatter(
    x=prices, y=total_pnl,
    mode='lines', name='Total PnL', line=dict(color='#00d1ff', width=3)
))

fig.update_layout(
    template="plotly_dark", height=500, margin=dict(t=30, b=0, l=0, r=0),
    xaxis_title="Underlying Price at Expiration ($)",
    yaxis_title="Profit / Loss ($)",
    hovermode='x unified'
)

st.plotly_chart(fig, use_container_width=True)
