import streamlit as st
import os
from openai import OpenAI
import logging

# Initialize logger for this file
logger = logging.getLogger(__name__)

def run_sidebar_chatbot(context_data=""):
    """
    Initializes a sidebar chatbot that uses the specific page's context
    to answer quantitative and fundamental analysis questions.
    """
    st.sidebar.divider()
    st.sidebar.subheader("🤖 Analyst Chat")
    
    # Check for API Key
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        st.sidebar.warning("OpenAI API key missing. Please add to secrets.")
        return

    client = OpenAI(api_key=api_key)
    
    # Initialize chat history in session state
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []

    # Display existing chat messages
    for message in st.session_state.chat_messages:
        with st.sidebar.chat_message(message["role"]):
            st.markdown(message["content"])

    # Chat input box
    if prompt := st.sidebar.chat_input("Ask a question..."):
        # Add user message to state and display
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.sidebar.chat_message("user"):
            st.markdown(prompt)

        try:
            # Build the message payload with the hidden system context
            messages = [
                {
                    "role": "system", 
                    "content": (
                        "You are a world-class quantitative financial analyst. "
                        "Provide concise, institutional-grade insights based on the "
                        "provided dashboard data. Use professional terminology. "
                        f"Context for the current dashboard view: {context_data}"
                    )
                }
            ] + st.session_state.chat_messages

            # Using 'gpt-4o-mini' which has the widest availability
            response = client.chat.completions.create(
                model="gpt-4o-mini", 
                messages=messages,
                max_completion_tokens=800
            )
            
            # Extract and display the response
            bot_reply = response.choices[0].message.content
            st.session_state.chat_messages.append({"role": "assistant", "content": bot_reply})
            
            with st.sidebar.chat_message("assistant"):
                st.markdown(bot_reply)
                
        except Exception as e:
            logger.error(f"Chatbot API Error: {e}")
            st.sidebar.error(f"API Error: {e}")

    # Add a clear button if chat gets too long
    if len(st.session_state.chat_messages) > 0:
        if st.sidebar.button("Clear Chat"):
            st.session_state.chat_messages = []
            st.rerun()
