import streamlit as st
import openai # Or your preferred LLM

def run_sidebar_chatbot():
    with st.sidebar:
        st.divider()
        st.subheader("🤖 AI Market Assistant")
        
        # Initialize chat history in session state
        if "messages" not in st.session_state:
            st.session_state.messages = []

        # Display chat messages from history on app rerun
        for message in st.session_state.messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])

        # Accept user input
        if prompt := st.chat_input("Ask about ERCOT or MC sims..."):
            # Add user message to chat history
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"):
                st.markdown(prompt)

            # Generate Assistant Response
            with st.chat_message("assistant"):
                # Placeholder for LLM Logic (e.g., OpenAI API call)
                response = f"You asked about: {prompt}. I'm processing that data now..."
                st.markdown(response)
            
            st.session_state.messages.append({"role": "assistant", "content": response})
