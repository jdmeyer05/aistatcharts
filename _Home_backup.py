import streamlit as st
from src.auth import init_supabase

# Centered layout looks much better for a landing/login page
st.set_page_config(page_title="Quant Platform | Login", layout="centered")

supabase = init_supabase()

if supabase is None:
    st.error("Supabase credentials not configured. Set SUPABASE_URL and SUPABASE_KEY as environment variables or in .streamlit/secrets.toml.")
    st.stop()

# Initialize session state for auth
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

# --- AUTHENTICATED STATE ---
if st.session_state['authenticated']:
    st.title("🏦 Institutional Quant Platform")
    st.success(f"Welcome back, {st.session_state.get('user_email', 'User')}!")
    st.markdown("### System Status: **Online**")
    st.markdown("""
    Use the sidebar to navigate to your quantitative tools:
    * **Monte Carlo Dashboard:** Multi-timeframe path simulations.
    * **Algo Backtester:** Vectorized strategy testing.
    * **Options Surface:** 2x2 grid analysis of IV and Liquidity.
    * **Spread Analyzer:** Complex multi-leg PnL modeling.
    """)
    
    st.divider()
    if st.button("Log Out", type="primary"):
        supabase.auth.sign_out()
        st.session_state['authenticated'] = False
        st.session_state['user_email'] = None
        st.rerun()

# --- UNAUTHENTICATED STATE (LANDING PAGE) ---
else:
    st.title("🏦 Institutional Quant Platform")
    st.markdown("Advanced algorithmic backtesting, deep-learning tactical forecasts, and multi-leg option spread analysis. Please sign in to access the engines.")
    
    st.divider()
    
    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])
    
    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email Address")
            password = st.text_input("Password", type="password")
            submit_login = st.form_submit_button("Log In 🔓")
            
            if submit_login:
                try:
                    res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                    st.session_state['authenticated'] = True
                    st.session_state['user_email'] = res.user.email
                    st.rerun()
                except Exception as e:
                    # Supabase returns specific error messages we can display
                    st.error(f"Login failed: Invalid credentials or email not confirmed.")

    with tab_signup:
        st.info("New accounts require email verification before logging in.")
        with st.form("signup_form"):
            new_email = st.text_input("Email Address")
            new_password = st.text_input("Password", type="password", help="Must be at least 6 characters.")
            submit_signup = st.form_submit_button("Create Account 📝")
            
            if submit_signup:
                try:
                    res = supabase.auth.sign_up({"email": new_email, "password": new_password})
                    st.success("Account created successfully! **Please check your email for the confirmation link** before trying to log in.")
                except Exception as e:
                    st.error(f"Sign up failed. Ensure your password is strong enough and the email isn't already registered.")
