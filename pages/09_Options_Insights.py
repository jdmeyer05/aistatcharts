import streamlit as st
import numpy as np
import plotly.graph_objects as go
from scipy.stats import norm
from datetime import datetime, date
import matplotlib.pyplot as plt
import seaborn as sns
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
    purchase_price = st.number_input("Purchase Price (Premium Paid)", min_value=0.0, value=0.0, step=0.1, help="Used for PnL tracking and Contour Line.")
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

# --- TOOL 1: THE DECAY HEATMAP (SEABORN) ---
st.subheader(f"Time & Price Decay Matrix ({option_type.capitalize()})")

if days_to_expiration > 0:
    lower_bound = S * (1 - (price_range_pct / 100))
    upper_bound = S * (1 + (price_range_pct / 100))
    # Using 20 steps to keep the cell annotations clean and readable
    price_steps = np.linspace(lower_bound, upper_bound, 20) 
    
    # Days array: From DTE down to 0
    days_array = np.arange(days_to_expiration, -1, -1)
    
    z_matrix = np.zeros((len(price_steps), len(days_array)))
    
    for i, p in enumerate(price_steps):
        for j, d in enumerate(days_array):
            T_step = d / 365.0
            price, _, _, _, _, _ = black_scholes_and_greeks(p, K, T_step, risk_free_rate, volatility, option_type)
            z_matrix[i, j] = price

    # Adjust width dynamically so the squares don't get squished if DTE is high
    plot_width = max(14, len(days_array) // 2)
    fig, ax = plt.subplots(figsize=(plot_width, 8))
    
    cmap = sns.diverging_palette(10, 150, as_cmap=True)
    
    # Use purchase price as contour center if provided, otherwise use current theoretical value
    center_val = purchase_price if purchase_price > 0 else current_price

    # Plot the Seaborn Heatmap
    sns.heatmap(z_matrix, cmap=cmap, center=center_val, 
                xticklabels=days_array, 
                yticklabels=np.round(price_steps, 2), 
                annot=True, fmt=".1f", cbar=False, ax=ax,
                annot_kws={"size": 9})
                
    ax.set_title(f'{option_type.capitalize()} Option Price Heatmap', color='white', pad=20, size=14)
    ax.set_xlabel('Days to Expiration', color='white', size=12)
    ax.set_ylabel('Stock Price ($)', color='white', size=12)
    
    # Style ticks for dark mode
    ax.tick_params(colors='white')
    plt.setp(ax.get_xticklabels(), color="white")
    plt.setp(ax.get_yticklabels(), color="white")

    # Add the dashed contour line
    X = np.arange(len(days_array)) + 0.5
    Y = np.arange(len(price_steps)) + 0.5
    contour = ax.contour(X, Y, z_matrix, levels=[center_val], colors='black', linewidths=2.5, linestyles='dashed')
    ax.clabel(contour, inline=True, fontsize=12, colors='black')

    # Force transparent background so it blends natively into Streamlit's dark theme
    fig.patch.set_facecolor('#0E1117')
    ax.set_facecolor('#0E1117')

    st.pyplot(fig)
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
    mode='lines', line=dict(color='white', width=2),
    fill='tozeroy', name="PnL"
))

fig_payoff.add_hline(y=0, line_dash="solid", line_color="gray")
fig_payoff.add_vline(x=S, line_dash="dash", line_color="#00d1ff", annotation_text="Current Spot")

fig_payoff.update_layout(
    template="plotly_dark",
    xaxis_title="Price at Expiration ($)",
    yaxis_title="Net Profit / Loss ($)",
    height=400, showlegend=False
)
st.plotly_chart(fig_payoff, use_container_width=True)
