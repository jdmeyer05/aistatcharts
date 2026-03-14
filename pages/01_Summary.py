import streamlit as st
import extra_streamlit_components as stx
import yfinance as yf
import plotly.graph_objects as go
from src.auth import init_supabase, check_auth

st.set_page_config(page_title="Platform Summary", layout="wide")
check_auth() # The firewall

supabase = init_supabase()

st.title("📊 Platform Summary")
st.success(f"Welcome back, {st.session_state.get('user_email', 'User')}!")
st.divider()

# --- MARKET OVERVIEW (SPARKLINES) ---
st.markdown("### 🌐 Macro Environment")

@st.cache_data(ttl=900) # Caches data for 15 minutes to keep the app lightning fast
def get_market_sparkline(ticker, color):
    """Fetches 3-month data and builds a clean sparkline chart."""
    try:
        hist = yf.Ticker(ticker).history(period="3mo")
        if hist.empty: return None, None, None
        
        last_price = hist['Close'].iloc[-1]
        prev_price = hist['Close'].iloc[-2]
        change_pct = (last_price - prev_price) / prev_price * 100
        
        fig = go.Figure(go.Scatter(
            x=hist.index, y=hist['Close'], 
            mode='lines', line=dict(color=color, width=2.5),
            fill='tozeroy', fillcolor=f'{color.replace("rgb", "rgba").replace(")", ", 0.1)")}'
        ))
        
        # Strip all axes and background for a clean "sparkline" look
        fig.update_layout(
            template="plotly_dark", margin=dict(l=0, r=0, t=0, b=0), height=120,
            xaxis=dict(visible=False, showgrid=False), yaxis=dict(visible=False, showgrid=False),
            showlegend=False, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            hovermode="x unified"
        )
        return last_price, change_pct, fig
    except Exception:
        return None, None, None

m1, m2, m3, m4 = st.columns(4)

markets = [
    ("QQQ", "Invesco QQQ Trust", "rgb(0, 209, 255)", m1),
    ("BTC-USD", "Bitcoin", "rgb(255, 153, 0)", m2),
    ("CL=F", "Crude Oil (WTI)", "rgb(255, 75, 75)", m3),
    ("NG=F", "Natural Gas", "rgb(170, 0, 255)", m4)
]

for ticker, name, color, col in markets:
    with col:
        st.caption(f"**{name}**")
        price, change, fig = get_market_sparkline(ticker, color)
        if price is not None:
            # Color the text green or red based on daily performance
            delta_color = "green" if change >= 0 else "red"
            st.markdown(f"<h3 style='margin-top:-15px;'>${price:,.2f} <span style='font-size:16px; color:{delta_color};'>({change:+.2f}%)</span></h3>", unsafe_allow_html=True)
            st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        else:
            st.error("Market data unavailable.")

st.divider()

# --- SUBSCRIPTION STATUS ---
st.markdown("### 💎 Subscription Status: **Free Tier**")
st.info("Upgrade to the Basic Tier to unlock the Vectorized Algo Backtester and Monte Carlo Simulator.")

# st.link_button(
#     "🔒 Upgrade to Basic ($19.99/mo)", 
#     url="https://buy.stripe.com/YOUR_LIVE_LINK_HERE", 
#     type="primary"
# )
st.divider()

# --- QUICK NAVIGATION ---
st.markdown("### 🚀 Quantitative Engines")

def safe_page_link(col, path, label, icon, description):
    """Safely attempts to load a page link, catching errors if the filename doesn't match."""
    with col:
        st.info(description)
        try:
            st.page_link(path, label=label, icon=icon)
        except Exception:
            st.error(f"Missing File: `{path}`")
            st.caption("Update the filename to match your actual file.")

# Organized into two clean rows of 3
row1_col1, row1_col2, row1_col3 = st.columns(3)
safe_page_link(row1_col1, "pages/08_Algo_Backtester.py", "Launch Engine", "⚡", "**Algo Backtester**\n\nVectorized historical strategy testing.")
safe_page_link(row1_col2, "pages/03_Historical_Analysis.py", "Launch Engine", "📈", "**Historical Analysis**\n\nDeep-dive market data visualization.")
safe_page_link(row1_col3, "pages/07_Option_Spread_Analyzer.py", "Launch Engine", "🕸️", "**Spread Analyzer**\n\nMulti-leg options payoff mapping.")

st.markdown("<br>", unsafe_allow_html=True) # Adds a little vertical breathing room

row2_col1, row2_col2, row2_col3 = st.columns(3)
safe_page_link(row2_col1, "pages/05_ML_Stock_Predictor.py", "Launch Engine", "🤖", "**ML Predictor**\n\nStochastic Random Forest forecasts.")
safe_page_link(row2_col2, "pages/10_Oil_Fundamentals.py", "Launch Engine", "🛢️", "**Oil Fundamentals**\n\nLive EIA crude macro data.")
safe_page_link(row2_col3, "pages/11_NatGas_Fundamentals.py", "Launch Engine", "🔥", "**Nat Gas Fundamentals**\n\nLive EIA storage & flow data.")

st.divider()

# --- ACCOUNT MANAGEMENT ---
st.markdown("### ⚙️ Account & Security")
col1, col2 = st.columns(2)

with col1:
    with st.expander("Update Password"):
        with st.form("update_password_form"):
            new_pw = st.text_input("Enter New Password", type="password")
            if st.form_submit_button("Save New Password"):
                try:
                    supabase.auth.update_user({"password": new_pw})
                    st.success("Password updated successfully!")
                except Exception as e:
                    st.error(f"Failed to update password: {e}")

with col2:
    if st.button("Log Out of Platform", type="primary", use_container_width=True):
        cookie_manager = stx.CookieManager()
        supabase.auth.sign_out()
        cookie_manager.delete("quant_user_session")
        st.session_state['authenticated'] = False
        st.session_state['user_email'] = None
        st.switch_page("app.py")
