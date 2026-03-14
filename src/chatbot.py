import streamlit as st
from openai import OpenAI
import os
import logging

logger = logging.getLogger(__name__)

def run_sidebar_chatbot(context_data=""):
    """
    Run a context-aware chatbot in the sidebar with isolated session state per page.
    """
    try:
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        
        with st.sidebar:
            st.divider()
            st.subheader("🤖 Analyst Chat")
            
            # Use page-specific session state key to prevent message history bleed
            chat_key = f"chatbot_messages_{st.session_state.get('page_id', 'default')}"
            
            if chat_key not in st.session_state:
                st.session_state[chat_key] = []

            for m in st.session_state[chat_key]:
                with st.chat_message(m["role"]): 
                    st.markdown(m["content"])

            if prompt := st.chat_input("Ask a question..."):
                st.session_state[chat_key].append({"role": "user", "content": prompt})
                with st.chat_message("user"): 
                    st.markdown(prompt)

                with st.chat_message("assistant"):
                    try:
                        # Use GPT-4 mini (cost/speed optimized)
                        response = client.chat.completions.create(
                            model="gpt-4-mini",
                            messages=[
                                {"role": "system", "content": f"You are a trading expert. Context: {context_data}"}
                            ] + [{"role": m["role"], "content": m["content"]} for m in st.session_state[chat_key]]
                        )
                        txt = response.choices[0].message.content
                        st.markdown(txt)
                    except Exception as api_err:
                        error_msg = f"API Error: {str(api_err)}"
                        st.error(error_msg)
                        logger.error(f"OpenAI API error: {str(api_err)}")
                        txt = error_msg
                        
                st.session_state[chat_key].append({"role": "assistant", "content": txt})
    except Exception as e:
        logger.error(f"Chatbot initialization error: {str(e)}")
        st.error(f"Chatbot error: {str(e)}")
