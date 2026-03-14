import streamlit as st
from openai import OpenAI
import os

# Initialize OpenAI Client
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def run_sidebar_chatbot(context_data=None):
    """
    context_data: A string or dict containing current page info 
    (e.g., 'Ticker: BTC-USD, Year-End Projection: $85,000')
    """
    with st.sidebar:
        st.divider()
        st.subheader("🤖 AI Market Assistant")
        
        if "messages" not in st.session_state:
            st.session_state.messages = []

        # Display history
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        if prompt := st.chat_input("Ask about this data..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # --- API CALL WITH CONTEXT ---
            with st.chat_message("assistant"):
                # We inject the current page data into the system message
                system_msg = {
                    "role": "system", 
                    "content": f"You are a power trading and financial analyst. Current market context: {context_data}"
                }
                
                # Build the message chain
                api_messages = [system_msg] + [
                    {"role": m["role"], "content": m["content"]} 
                    for m in st.session_state.messages
                ]

                # Using GPT-5 mini (2026 standard for fast, smart reasoning)
                response = client.chat.completions.create(
                    model="gpt-5-mini",
                    messages=api_messages,
                    stream=False
                )
                
                full_response = response.choices[0].message.content
                st.markdown(full_response)
            
            st.session_state.messages.append({"role": "assistant", "content": full_response})
