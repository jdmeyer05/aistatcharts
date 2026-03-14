import streamlit as st
from src.auth import init_supabase, check_auth

st.set_page_config(page_title="Platform Summary", layout="wide")
check_auth() # The firewall

supabase = init_supabase()

st.title("📊 Platform Summary")
st.success(f"Welcome back, {st.session_state.get('user_email', 'User')}!")
st.divider()

# --- QUICK NAVIGATION ---
st.markdown("### 🚀 Quantitative Engines")
c1, c2, c3, c4 = st.columns(4)

with c1:
    st.info("**Algo Backtester**\n\nVectorized historical strategy testing.")
    st.page_link("pages/02_Algo_Backtester.py", label="Launch Engine", icon="⚡")

with c2:
    st.info("**Historical Analysis**\n\nDeep-dive market data visualization.")
    # Failsafe: only show the link if you actually created this file earlier!
    try: st.page_link("pages/03_Historical_Analysis.py", label="Launch Engine", icon="📈")
    except: st.caption("Module pending...")

with c3:
    st.info("**Spread Analyzer**\n\nMulti-leg options payoff mapping.")
    st.page_link("pages/04_Spread_Analyzer.py", label="Launch Engine", icon="🕸️")

with c4:
    st.info("**ML Predictor**\n\nStochastic Random Forest forecasts.")
    st.page_link("pages/05_ML_Stock_Predictor.py", label="Launch Engine", icon="🤖")

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
        supabase.auth.sign_out()
        st.session_state['authenticated'] = False
        st.session_state['user_email'] = None
        st.switch_page("app.py") # Send them back to the login screen
