import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import streamlit as st

# OPEN BETA: Skip login entirely — redirect straight to Summary
st.set_page_config(page_title="AI Statcharts", layout="centered",
                   initial_sidebar_state="collapsed")
st.session_state['authenticated'] = True
st.session_state.setdefault('user_email', "guest@open-beta")
st.switch_page("pages/01_Summary.py")
