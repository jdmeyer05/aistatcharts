import streamlit as st
import extra_streamlit_components as stx
import datetime
from src.auth import init_supabase

st.set_page_config(page_title="Quant Platform | Login", layout="centered")

st.markdown(
    """<style>ul[data-testid="stSidebarNavItems"] li:nth-child(1) { display: none; }</style>""",
    unsafe_allow_html=True
)

supabase = init_supabase()

# --- INITIALIZE COOKIE MANAGER ---
cookie_manager = stx.CookieManager()

if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'password_reset_mode' not in st.session_state:
    st.session_state['password_reset_mode'] = False

# --- AUTO-LOGIN VIA COOKIE ---
cached_email = cookie_manager.get(cookie="quant_user_session")
if cached_email and not st.session_state['authenticated']:
    st.session_state['authenticated'] = True
    st.session_state['user_email'] = cached_email
    st.switch_page("pages/01_Summary.py")

# --- URL REDIRECT CATCHER ---
if "code" in st.query_params:
    code = st.query_params.get("code")
    try:
        res = supabase.auth.exchange_code_for_session({"auth_code": code})
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = res.user.email
        st.session_state['password_reset_mode'] = True 
        st.query_params.clear()
        st.rerun()
    except Exception as e:
        st.error(f"Link expired or invalid: {e}")
        st.query_params.clear()

# --- ROUTER LOGIC ---
if st.session_state.get('password_reset_mode'):
    st.title("🔐 Set New Password")
    with st.form("mandatory_reset_form"):
        new_pw = st.text_input("New Password", type="password", key="new_pw_1", autocomplete="new-password")
        confirm_pw = st.text_input("Confirm Password", type="password", key="new_pw_2", autocomplete="new-password")
        if st.form_submit_button("Update Password & Enter App", type="primary"):
            if new_pw == confirm_pw and len(new_pw) >= 6:
                try:
                    res = supabase.auth.update_user({"password": new_pw})
                    
                    # 🍪 FIX: Use datetime for the expires_at argument
                    expiration_date = datetime.datetime.now() + datetime.timedelta(days=30)
                    cookie_manager.set("quant_user_session", st.session_state['user_email'], expires_at=expiration_date)
                    
                    st.session_state['password_reset_mode'] = False
                    st.switch_page("pages/01_Summary.py")
                except Exception as e:
                    st.error(f"Update failed: {e}")
            else:
                st.error("Passwords must match and be at least 6 characters.")

elif st.session_state['authenticated']:
    st.switch_page("pages/01_Summary.py")

else:
    st.title("🏦 Institutional Quant Platform")
    st.markdown("Advanced algorithmic backtesting, deep-learning tactical forecasts, and multi-leg option spread analysis.")
    st.divider()
    
    tab_login, tab_signup, tab_forgot = st.tabs(["Log In", "Sign Up", "Forgot Password"])
    
    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email Address", key="login_email", autocomplete="username")
            password = st.text_input("Password", type="password", key="login_pw", autocomplete="current-password")
            if st.form_submit_button("Log In 🔓"):
                try:
                    res = supabase.auth.sign_in_with_password({"email": email, "password": password})
                    
                    # 🍪 FIX: Use datetime for the expires_at argument
                    expiration_date = datetime.datetime.now() + datetime.timedelta(days=30)
                    cookie_manager.set("quant_user_session", res.user.email, expires_at=expiration_date)
                    
                    st.session_state['authenticated'] = True
                    st.session_state['user_email'] = res.user.email
                    st.switch_page("pages/01_Summary.py")
                except Exception as e:
                    st.error(f"Login failed: {e}")

    with tab_signup:
        with st.form("signup_form"):
            new_email = st.text_input("Email Address", key="signup_email", autocomplete="username")
            new_password = st.text_input("Password", type="password", key="signup_pw", autocomplete="new-password")
            if st.form_submit_button("Create Account 📝"):
                try:
                    res = supabase.auth.sign_up({"email": new_email, "password": new_password})
                    st.success("Account created! Check your email for the confirmation link.")
                except Exception as e:
                    st.error(f"Sign up failed: {e}")

    with tab_forgot:
        with st.form("forgot_form"):
            reset_email = st.text_input("Email Address", key="forgot_email", autocomplete="email")
            if st.form_submit_button("Send Reset Link 📧"):
                try:
                    supabase.auth.reset_password_for_email(reset_email)
                    st.success("Check your email for the reset link!")
                except Exception as e:
                    st.error(f"Failed to send link: {e}")
