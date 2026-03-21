import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st
from src.auth import init_supabase, set_auth_cookie, set_auth_cookie_session
from src.styles import inject_global_css, COLORS

st.set_page_config(page_title="Quant Platform Login", layout="centered",
                   initial_sidebar_state="collapsed")

# Hide sidebar completely on login page
st.markdown("""<style>
    section[data-testid="stSidebar"] { display: none !important; }
    [data-testid="stSidebarCollapseButton"],
    [data-testid="collapsedControl"] { display: none !important; }
</style>""", unsafe_allow_html=True)

inject_global_css()

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

# Recover from browser cookie (mobile wake-up / server restart)
try:
    refresh_token = st.context.cookies.get("sb_refresh")
    if refresh_token:
        response = supabase.auth.refresh_session(refresh_token)
        if response and response.session:
            st.session_state['authenticated'] = True
            st.session_state['user_email'] = response.session.user.email
            set_auth_cookie(response.session.refresh_token)
            st.switch_page("pages/01_Summary.py")
except Exception:
    pass

# --- UI RENDERING ---
st.markdown(
    f'<div style="text-align:center; padding:2rem 0 1rem 0;">'
    f'<div style="font-size:2.2rem; font-weight:700; color:{COLORS["accent"]}; letter-spacing:1px;">'
    f'AI Statcharts</div>'
    f'<div style="color:{COLORS["text_muted"]}; font-size:0.95rem; margin-top:6px;">'
    f'Institutional-grade backtesting, options matrix, and macro charting.</div>'
    f'</div>',
    unsafe_allow_html=True,
)
st.markdown(f'<hr style="border:none; border-top:1px solid {COLORS["card_border"]}; margin:0 0 1rem 0;">',
            unsafe_allow_html=True)

tab1, tab2 = st.tabs(["🔒 Log In", "📝 Register"])

# --- LOG IN TAB ---
with tab1:
    with st.form("login_form"):
        st.subheader("Access Your Account")
        email = st.text_input("Email Address")
        password = st.text_input("Password", type="password")
        remember_me = st.checkbox("Remember me for 30 days", value=True)
        submit_login = st.form_submit_button("Log In", type="primary", use_container_width=True)

        if submit_login:
            try:
                response = supabase.auth.sign_in_with_password({"email": email, "password": password})

                st.session_state['authenticated'] = True
                st.session_state['user_email'] = email
                st.session_state['_auth_timestamp'] = __import__("datetime").datetime.now()

                # Persist refresh token in browser cookie for mobile session recovery
                if response.session and response.session.refresh_token:
                    if remember_me:
                        set_auth_cookie(response.session.refresh_token)
                    else:
                        set_auth_cookie_session(response.session.refresh_token)

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

        st.divider()
        st.markdown("##### User Agreement")
        st.markdown(
            '<div style="max-height:200px;overflow-y:auto;padding:10px;border:1px solid #30363d;'
            'border-radius:6px;font-size:0.78rem;color:#aaa;background:#0e1117;">'
            '<p><strong>AI Statcharts — Terms of Use (Summary)</strong></p>'
            '<p>This platform provides AI-generated analysis for <strong>informational and educational purposes only</strong>. '
            'It is <strong>not financial advice</strong>. All AI model outputs (scores, recommendations, price targets, '
            'sentiment analysis) are probabilistic estimates that may be inaccurate or misleading.</p>'
            '<p>You agree to: (1) conduct your own due diligence before making investment decisions, '
            '(2) not rely solely on this platform\'s outputs, (3) consult a qualified financial advisor, '
            '(4) understand that past performance and backtests do not guarantee future results, '
            '(5) accept that reinforcement learning strategies are research tools only and should not be '
            'deployed without independent validation.</p>'
            '<p>The platform aggregates data from third-party sources (FRED, yfinance, Polymarket, StockTwits, X/Twitter) '
            'that may be delayed, incomplete, or incorrect. AI models (Grok, Gemini, Claude) can hallucinate.</p>'
            '<p>To the maximum extent permitted by law, the creators shall not be liable for any losses '
            'resulting from use of this platform.</p>'
            '<p><em>Full agreement: USER_AGREEMENT.md in the project repository.</em></p>'
            '</div>',
            unsafe_allow_html=True,
        )
        accept_terms = st.checkbox("I have read and agree to the User Agreement and Terms of Use",
                                   value=False)

        submit_register = st.form_submit_button("Register", type="primary", use_container_width=True)

        if submit_register:
            if not accept_terms:
                st.error("You must accept the User Agreement to create an account.")
            elif len(new_password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                try:
                    response = supabase.auth.sign_up({
                        "email": new_email,
                        "password": new_password,
                        "options": {
                            "data": {
                                "terms_accepted": True,
                                "terms_accepted_date": __import__("datetime").datetime.now().isoformat(),
                            }
                        }
                    })
                    st.success("✅ Account created! You agreed to the Terms of Use. "
                              "Switch to the **Log In** tab to access the platform.")

                except Exception as e:
                    st.error(f"Registration failed: {e}")

st.markdown(f'<hr style="border:none; border-top:1px solid {COLORS["card_border"]}; margin:1rem 0 0.5rem 0;">',
            unsafe_allow_html=True)
st.markdown(f'<div style="text-align:center; color:{COLORS["text_muted"]}; font-size:0.75rem;">'
            f'Protected by standard AES encryption and Supabase Auth routing.</div>',
            unsafe_allow_html=True)
