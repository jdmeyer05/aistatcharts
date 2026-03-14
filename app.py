import streamlit as st
from src.auth import init_supabase

st.set_page_config(page_title="Quant Platform | Login", layout="centered")

# --- SIDEBAR UI OVERRIDE ---
# This renames the first sidebar item without changing the file name
st.markdown(
    """
    <style>
    /* Hide the default text 'App' */
    ul[data-testid="stSidebarNavItems"] li:nth-child(1) span {
        display: none;
    }
    /* Inject 'Home Page' */
    ul[data-testid="stSidebarNavItems"] li:nth-child(1) a::after {
        content: "🏠 Home Page";
        font-weight: 400;
        margin-left: 5px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

supabase = init_supabase()

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False

# --- HANDLE URL REDIRECTS (Password Reset / Verification) ---
if "code" in st.query_params:
    code = st.query_params.get("code")
    try:
        res = supabase.auth.exchange_code_for_session({"auth_code": code})
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = res.user.email
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Link expired or invalid: {e}")
        st.query_params.clear()

# --- AUTHENTICATED STATE ---
if st.session_state['authenticated']:
    st.title("🏦 Institutional Quant Platform")
    st.success(f"Welcome back, {st.session_state.get('user_email', 'User')}!")
    st.info("💡 **If you just reset your password, please enter a new one in the Account Settings menu below.**")
    st.markdown("### System Status: **Online**")
    st.markdown("""
    Use the sidebar to navigate to your quantitative tools:
    * **Monte Carlo Dashboard:** Multi-timeframe path simulations.
    * **Algo Backtester:** Vectorized strategy testing.
    * **Options Surface:** 2x2 grid analysis of IV and Liquidity.
    * **Spread Analyzer:** Complex multi-leg PnL modeling.
    * **ML Stock Predictor:** Stochastic recursive tactical forecasts.
    """)
    
    st.divider()
    
    col1, col2 = st.columns(2)
    
    with col1:
        if st.button("Log Out", type="primary", use_container_width=True):
            supabase.auth.sign_out()
            st.session_state['authenticated'] = False
            st.session_state['user_email'] = None
            st.rerun()
            
    with col2:
        with st.expander("⚙️ Account Settings (Update Password)"):
            with st.form("update_password_form"):
                new_pw = st.text_input("Enter New Password", type="password", key="update_pw_input")
                if st.form_submit_button("Save New Password"):
                    try:
                        supabase.auth.update_user({"password": new_pw})
                        st.success("Password updated successfully!")
                    except Exception as e:
                        st.error(f"Failed to update password: {e}")

# --- UNAUTHENTICATED STATE (LANDING PAGE) ---
else:
    st.title("🏦 Institutional Quant Platform")
    st.markdown("Advanced algorithmic backtesting, deep-learning tactical forecasts, and multi-leg option spread analysis. Please sign in to access the engines.")
    
    if st.query_params.get("verified") == "true":
        st.balloons()
        st.success("🎉 **Email successfully verified!** Your account is now active. Please log in below.")
    
    st.divider()
    
    tab_login, tab_signup, tab_forgot = st.tabs(["Log In", "Sign Up", "Forgot Password"])
    
    with tab_login:
        with st.form("login_form"):
            # Added unique keys to separate from Sign Up
            email = st.text_input("Email Address", key="login_email")
            password = st.text_input("Password", type="password", key="login_pw")
            submit_login = st.form_submit_button("Log In 🔓")
            
            if submit_login:
                try:
                    res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                    st.session_state['authenticated'] = True
                    st.session_state['user_email'] = res.user.email
                    st.rerun()
                except Exception as e:
                    st.error(f"Login failed: {e}")

    with tab_signup:
        st.info("New accounts require email verification before logging in.")
        with st.form("signup_form"):
            # Added unique keys to separate from Log In
            new_email = st.text_input("Email Address", key="signup_email")
            new_password = st.text_input("Password", type="password", help="Must be at least 6 characters.", key="signup_pw")
            submit_signup = st.form_submit_button("Create Account 📝")
            
            if submit_signup:
                try:
                    res = supabase.auth.sign_up({"email": new_email, "password": new_password})
                    st.success("Account created successfully! **Please check your email for the confirmation link** before trying to log in.")
                except Exception as e:
                    st.error(f"Sign up failed: {e}")

    with tab_forgot:
        st.info("Enter your email to receive a secure password reset link.")
        with st.form("forgot_form"):
            # Added unique key
            reset_email = st.text_input("Email Address", key="forgot_email")
            submit_reset = st.form_submit_button("Send Reset Link 📧")
            
            if submit_reset:
                try:
                    supabase.auth.reset_password_for_email(reset_email)
                    st.success("Check your email for the reset link! Once you click it and log in, use Account Settings to set a new password.")
                except Exception as e:
                    st.error(f"Failed to send link: {e}")
