import streamlit as st
import numpy as np
import plotly.graph_objects as go
from scipy.stats import norm
from datetime import datetime, date
from src.auth import check_auth

st.set_page_config(page_title="Options Insights", layout="wide")
check_auth() # The firewall

st.title("🔮 Options Pricing & Greeks Insights")
st.markdown("Black-Scholes theoretical pricing matrix, Greek exposures, and payoff profiling.")

# --- MATH ENGINE ---
def black_scholes_and_greeks(S, K, T, r, sigma, option_type='call'):
    """Calculates BS Price and the 5 major Greeks."""
    if T <= 0: # Handle expiration
        price = max(S - K, 0) if option_type == 'call' else max(K - S, 0)
        return price, 0.0, 0.0, 0.0, 0.0, 0.0

    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    
    # Standard Normal PDF for Gamma/Vega
    N_prime_d1 = norm.pdf(d1) 

    if option_type == 'call':
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        delta = norm.cdf(d1)
        theta = (- (S * sigma * N_prime_d1) / (2 * np.sqrt(T)) - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
        rho = (K * T * np.exp(-r * T) * norm.cdf(d2)) / 100
    else:
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = norm.cdf(d1) - 1
        theta = (- (S * sigma * N_prime_d1) / (2 * np.sqrt(T)) + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365
        rho = (-K * T * np.exp(-r * T) * norm.cdf(-d2)) / 100

    gamma = N_prime_d1 / (S * sigma * np.sqrt(T))
    vega = (S * np.sqrt(T) * N_prime_d1) / 100

    return price, delta, gamma, theta, vega, rho

# --- SIDEBAR CONFIGURATION ---
# Notice how we replaced input() with st.number_input() and st.selectbox()!
with st.sidebar:
    st.header("Option Parameters")
    
    option_type = st.selectbox("Option Type", ["Call", "Put"]).lower()
    
    S = st.number_input("Current Spot Price ($)", min_value=0.01, value=100.00, step=1.0)
    K = st.number_input("Strike Price ($)", min_value=0.01, value=100.00, step=1.0)
    
    expiration_date = st.date_input("Expiration Date", value=date.today().replace(month=date.today().month % 12 + 1))
    
    volatility = st.number_input("Implied Volatility (%)", min_value=0.1, value=20.0, step=1.0) / 100
    risk_free_rate = st.number_input("Risk-Free Rate (%)", value=4.5, step=0.1) / 100
    
    st.divider()
    st.caption("Visual Configuration")
    purchase_price = st.number_input("Purchase Price (Premium Paid)", min_value=0.0, value=0.0, step=0.1, help="Used for PnL tracking and Break-even.")
    price_range_pct = st.slider("Heatmap Price Range (+/- %)", 5, 50, 15, step=5)

# --- CALCULATIONS ---
days_to_expiration = max((expiration_date - date.today()).days, 0)
T_years = days_to_expiration / 365.0

current_price, delta, gamma, theta, vega, rho = black_scholes_and_greeks(S, K, T_years, risk_free_rate, volatility, option_type)

# --- TOP DASHBOARD: GREEKS ---
st.subheader("Theoretical Pricing & The Greeks")
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Theoretical Value", f"${current_price:.2f}")
c2.metric("Delta (Δ)", f"{delta:.3f}")
c3.metric("Gamma (Γ)", f"{gamma:.4f}")
c4.metric("Theta (Θ)", f"${theta:.3f}", help="Daily decay")
c5.metric("Vega (v)", f"${vega:.3f}", help="Per 1% IV change")
c6.metric("Rho (ρ)", f"${rho:.3f}")

st.divider()

# --- TOOL 1: THE DECAY HEATMAP ---
st.subheader(f"Time & Price Decay Matrix ({option_type.capitalize()})")

if days_to_expiration > 0:
    lower_bound = S * (1 - (price_range_pct / 100))
    upper_bound = S * (1 + (price_range_pct / 100))
    price_steps = np.linspace(lower_bound, upper_bound, 25)
    
    days_array = np.arange(days_to_expiration, -1, -1)
    
    z_matrix = np.zeros((len(price_steps), len(days_array)))
    
    for i, p in enumerate(price_steps):
        for j, d in enumerate(days_array):
            T_step = d / 365.0
            price, _, _, _, _, _ = black_scholes_and_greeks(p, K, T_step, risk_free_rate, volatility, option_type)
            z_matrix[i, j] = price

    fig_heat = go.Figure(data=go.Heatmap(
        z=z_matrix,
        x=days_array,
        y=price_steps,
        colorscale='RdYlGn' if option_type == 'call' else 'RdYlGn_r',
        hovertemplate="Days to Exp: %{x}<br>Spot Price: $%{y:.2f}<br>Option Value: $%{z:.2f}<extra></extra>"
    ))
    
    fig_heat.add_hline(y=S, line_dash="dash", line_color="white", annotation_text="Current Spot", annotation_position="bottom right")

    fig_heat.update_layout(
        template="plotly_dark",
        xaxis_title="Days to Expiration",
        yaxis_title="Underlying Spot Price ($)",
        xaxis=dict(autorange="reversed"),
        height=500
    )
    st.plotly_chart(fig_heat, use_container_width=True)
else:
    st.warning("Option has expired. Matrix requires DTE > 0.")

# --- TOOL 2: EXPIRATION PAYOFF ---
st.subheader("Expiration PnL Profile")

payoff_prices = np.linspace(S * 0.5, S * 1.5, 100)
if option_type == 'call':
    payoff = np.maximum(payoff_prices - K, 0) - purchase_price
else:
    payoff = np.maximum(K - payoff_prices, 0) - purchase_price

fig_payoff = go.Figure()

colors = ['#00FF00' if p >= 0 else '#FF0000' for p in payoff]

fig_payoff.add_trace(go.Scatter(
    x=payoff_prices, y=payoff, 
    mode='lines', 
    line=dict(color='white', width=2),
    fill='tozeroy',
    name="PnL"
))

fig_payoff.add_hline(y=0, line_dash="solid", line_color="gray")
fig_payoff.add_vline(x=S, line_dash="dash", line_color="#00d1ff", annotation_text="Current Spot")

fig_payoff.update_layout(
    template="plotly_dark",
    xaxis_title="Price at Expiration ($)",
    yaxis_title="Net Profit / Loss ($)",
    height=400,
    showlegend=False
)
st.plotly_chart(fig_payoff, use_container_width=True)
