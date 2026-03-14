import streamlit as st
import extra_streamlit_components as stx
from src.auth import init_supabase

st.set_page_config(page_title="Quant Platform Login", layout="centered")

# Initialize Supabase and Cookie Manager
supabase = init_supabase()
cookie_manager = stx.CookieManager()

# --- SESSION STATE INITIALIZATION ---
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'user_email' not in st.session_state:
    st.session_state['user_email'] = None

# --- FAST PASS: ALREADY LOGGED IN ---
# If they wander back to the login page but already have a valid session, boot them to the dashboard.
if st.session_state['authenticated'] or cookie_manager.get(cookie="quant_user_session"):
    st.session_state['authenticated'] = True
    st.switch_page("pages/01_Summary.py")

# --- UI RENDERING ---
st.title("⚡ Quantitative Analysis Platform")
st.markdown("Institutional-grade backtesting, options matrix, and macro charting.")
st.divider()

tab1, tab2 = st.tabs(["🔒 Log In", "📝 Register"])

# --- LOG IN TAB ---
with tab1:
    with st.form("login_form"):
        st.subheader("Access Your Account")
        email = st.text_input("Email Address")
        password = st.text_input("Password", type="password")
        submit_login = st.form_submit_button("Log In", type="primary", use_container_width=True)

        if submit_login:
            try:
                # 1. Authenticate with Supabase
                response = supabase.auth.sign_in_with_password({"email": email, "password": password})
                
                # 2. Set the secure browser cookie (expires in 30 days)
                cookie_manager.set("quant_user_session", email, max_age=30*24*60*60)
                
                # 3. Update short-term memory
                st.session_state['authenticated'] = True
                st.session_state['user_email'] = email
                
                # 4. Route to the dashboard
                st.success("Authentication successful! Rerouting...")
                st.switch_page("pages/01_Summary.py")
                
            except Exception as e:
                st.error(f"Login failed: Invalid email or password.")

# --- REGISTER TAB ---
with tab2:
    with st.form("register_form"):
        st.subheader("Create a New Account")
        new_email = st.text_input("Email Address")
        new_password = st.text_input("Password", type="password", help="Must be at least 6 characters.")
        submit_register = st.form_submit_button("Register", type="primary", use_container_width=True)

        if submit_register:
            try:
                # Create the user in Supabase
                response = supabase.auth.sign_up({
                    "email": new_email, 
                    "password": new_password
                })
                
                # 🚨 THE NEW SUCCESS MESSAGE: No email verification required!
                st.success("✅ Account created successfully! You can now switch to the **Log In** tab to access the platform.")
                
            except Exception as e:
                # Supabase returns specific error messages (like "Password should be at least 6 characters")
                st.error(f"Registration failed: {e}")

st.divider()
st.caption("Protected by standard AES encryption and Supabase Auth routing.")
