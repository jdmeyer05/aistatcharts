import streamlit as st
from src.auth import init_supabase

st.set_page_config(page_title="Quant Platform Login", layout="centered")

# Initialize Supabase
supabase = init_supabase()

# --- SESSION STATE INITIALIZATION ---
if 'authenticated' not in st.session_state:
    st.session_state['authenticated'] = False
if 'user_email' not in st.session_state:
    st.session_state['user_email'] = None

# --- LOCAL DEV BYPASS ---
if supabase is None:
    st.session_state['authenticated'] = True
    st.session_state['user_email'] = "local-dev@preview"
    st.switch_page("pages/01_Summary.py")

# --- FAST PASS: ALREADY LOGGED IN ---
if st.session_state['authenticated']:
    st.switch_page("pages/01_Summary.py")

# Check if Supabase client still has a valid session (survives refresh)
try:
    session = supabase.auth.get_session()
    if session:
        st.session_state['authenticated'] = True
        st.session_state['user_email'] = session.user.email
        st.switch_page("pages/01_Summary.py")
except Exception:
    pass

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
                response = supabase.auth.sign_in_with_password({"email": email, "password": password})

                st.session_state['authenticated'] = True
                st.session_state['user_email'] = email

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
                response = supabase.auth.sign_up({
                    "email": new_email,
                    "password": new_password
                })
                st.success("✅ Account created successfully! You can now switch to the **Log In** tab to access the platform.")

            except Exception as e:
                st.error(f"Registration failed: {e}")

st.divider()
st.caption("Protected by standard AES encryption and Supabase Auth routing.")
