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

def safe_page_link(col, path, label, icon, description):
    """Safely attempts to load a page link, catching errors if the filename doesn't match."""
    with col:
        st.info(description)
        try:
            st.page_link(path, label=label, icon=icon)
        except Exception:
            # If the file name is wrong, it shows this clean warning instead of crashing the app
            st.error(f"Missing File: `{path}`")
            st.caption("Update the filename in `01_Summary.py` to match your actual file.")

# NOTE: If any of these buttons show the red warning, just change the string 
# inside the quotes below to match whatever you actually named the file in your /pages folder!
safe_page_link(c1, "pages/02_Algo_Backtester.py", "Launch Engine", "⚡", "**Algo Backtester**\n\nVectorized historical strategy testing.")
safe_page_link(c2, "pages/03_Historical_Analysis.py", "Launch Engine", "📈", "**Historical Analysis**\n\nDeep-dive market data visualization.")
safe_page_link(c3, "pages/04_Spread_Analyzer.py", "Launch Engine", "🕸️", "**Spread Analyzer**\n\nMulti-leg options payoff mapping.")
safe_page_link(c4, "pages/05_ML_Stock_Predictor.py", "Launch Engine", "🤖", "**ML Predictor**\n\nStochastic Random Forest forecasts.")

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
        st.switch_page("app.py")
