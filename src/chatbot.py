import streamlit as st
from openai import OpenAI
import os

def run_sidebar_chatbot(context_data=""):
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    with st.sidebar:
        st.divider()
        st.subheader("🤖 Analyst Chat")
        
        if "messages" not in st.session_state:
            st.session_state.messages = []

        for m in st.session_state.messages:
            with st.chat_message(m["role"]): st.markdown(m["content"])

        if prompt := st.chat_input("Ask a question..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): st.markdown(prompt)

            with st.chat_message("assistant"):
                # Use GPT-5 mini (cost/speed optimized for 2026)
                response = client.chat.completions.create(
                    model="gpt-5-mini",
                    messages=[
                        {"role": "system", "content": f"You are a trading expert. Context: {context_data}"}
                    ] + [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages]
                )
                txt = response.choices[0].message.content
                st.markdown(txt)
            st.session_state.messages.append({"role": "assistant", "content": txt})
