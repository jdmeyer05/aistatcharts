import streamlit as st
from src.auth import init_supabase

st.set_page_config(page_title="Quant Platform | Login", layout="centered")

# --- SIDEBAR UI OVERRIDE ---
st.markdown(
    """
    <style>
    ul[data-testid="stSidebarNavItems"] li:nth-child(1) span { display: none; }
    ul[data-testid="stSidebarNavItems"] li:nth-child(1) a::after {
        content: "🏠 Home Page"; font-weight: 400; margin-left: 5px;
    }
    </style>
    """,
    unsafe_allow_html=True
)

supabase = init_supabase()

# --- INITIALIZE SESSION STATES ---
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'password_reset_mode' not in st.session_state:
    st.session_state['password_reset_mode'] = False

# --- HANDLE URL REDIRECTS (Password Reset) ---
if "code" in st.query_params:
    code = st.query_params.get("code")
    try:
        # Exchange the code to log the user in temporarily
        res = supabase.auth.exchange_code_for_session({"auth_code": code})
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = res.user.email
        
        # Trigger the dedicated Password Reset Screen
        st.session_state['password_reset_mode'] = True 
        
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Link expired or invalid: {e}")
        st.query_params.clear()

# ==========================================
# UI ROUTING
# ==========================================

# 1. DEDICATED PASSWORD RESET SCREEN
if st.session_state.get('password_reset_mode'):
    st.title("🔐 Set New Password")
    st.markdown("Please enter a new, secure password for your account.")
    
    with st.form("mandatory_reset_form"):
        new_pw = st.text_input("New Password", type="password", key="new_pw_1")
        confirm_pw = st.text_input("Confirm Password", type="password", key="new_pw_2")
        submit_new_pw = st.form_submit_button("Update Password & Enter App", type="primary")
        
        if submit_new_pw:
            if new_pw != confirm_pw:
                st.error("Passwords do not match. Please try again.")
            elif len(new_pw) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                try:
                    supabase.auth.update_user({"password": new_pw})
                    st.success("Password updated successfully!")
                    st.session_state['password_reset_mode'] = False # Turn off reset mode
                    st.rerun() # Drop them into the main dashboard
                except Exception as e:
                    st.error(f"Failed to update password: {e}")

# 2. NORMAL AUTHENTICATED DASHBOARD
elif st.session_state['authenticated']:
    st.title("🏦 Institutional Quant Platform")
    st.success(f"Welcome back, {st.session_state.get('user_email', 'User')}!")
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
        with st.expander("⚙️ Account Settings"):
            st.info("Your account is active and secured.")

# 3. UNAUTHENTICATED LANDING PAGE
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
            reset_email = st.text_input("Email Address", key="forgot_email")
            submit_reset = st.form_submit_button("Send Reset Link 📧")
            
            if submit_reset:
                try:
                    supabase.auth.reset_password_for_email(reset_email)
                    st.success("Check your email for the reset link! It will open a secure page to set your new password.")
                except Exception as e:
                    st.error(f"Failed to send link: {e}")
