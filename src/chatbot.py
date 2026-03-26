import streamlit as st
import logging
from src.auth import get_user_tier

logger = logging.getLogger(__name__)

# Chat model config by tier
CHAT_TIERS = {
    "free": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "key_name": "GEMINI_API_KEY",
        "max_tokens": 400,
        "daily_limit": 5,
        "label": "Gemini Flash",
    },
    "pro": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "key_name": "GEMINI_API_KEY",
        "max_tokens": 800,
        "daily_limit": -1,
        "label": "Gemini Flash",
    },
    "premium": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "key_name": "GEMINI_API_KEY",
        "max_tokens": 800,
        "daily_limit": -1,
        "label": "Gemini Flash",
    },
    "platinum": {
        "provider": "gemini",
        "model": "gemini-2.5-flash",
        "key_name": "GEMINI_API_KEY",
        "max_tokens": 1200,
        "daily_limit": -1,
        "label": "Gemini Flash",
    },
}


def _get_key(name: str):
    from src.api_keys import get_secret
    return get_secret(name)


def _check_chat_limit(tier_config: dict) -> bool:
    """Check if user has chat messages remaining today."""
    limit = tier_config["daily_limit"]
    if limit == -1:
        return True
    from datetime import date
    today = date.today().isoformat()
    used = st.session_state.get(f"chat_usage_{today}", 0)
    return used < limit


def _increment_chat_usage():
    from datetime import date
    today = date.today().isoformat()
    key = f"chat_usage_{today}"
    st.session_state[key] = st.session_state.get(key, 0) + 1


def run_sidebar_chatbot(context_data=""):
    """Inline analyst chatbot. Tier-based model and rate limiting."""
    tier = get_user_tier()
    config = CHAT_TIERS.get(tier, CHAT_TIERS["free"])

    # Chat history (capped at 50)
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    elif len(st.session_state.chat_messages) > 50:
        st.session_state.chat_messages = st.session_state.chat_messages[-50:]

    # Keep expander open when there's an active conversation
    has_messages = len(st.session_state.chat_messages) > 0

    with st.expander(f"Analyst Chat ({config['label']})", expanded=has_messages):
        # Check API key
        api_key = _get_key(config["key_name"])
        if not api_key:
            st.warning(f"{config['key_name']} missing. Add to secrets.")
            return

        # Display existing messages
        if st.session_state.chat_messages:
            chat_container = st.container(height=300)
            with chat_container:
                for message in st.session_state.chat_messages:
                    with st.chat_message(message["role"]):
                        st.markdown(message["content"])

        # Rate limit check
        if not _check_chat_limit(config):
            from datetime import date
            today = date.today().isoformat()
            used = st.session_state.get(f"chat_usage_{today}", 0)
            st.info(f"Daily chat limit reached ({used}/{config['daily_limit']}). Upgrade for unlimited access.")
            return

        # Chat input — use form to prevent page rerun on Enter and keep expander open
        with st.form("analyst_chat_form", clear_on_submit=True):
            prompt = st.text_input("Ask a question...", label_visibility="collapsed")
            send_clicked = st.form_submit_button("Send", use_container_width=True)

        if prompt and send_clicked:
            st.session_state.chat_messages.append({"role": "user", "content": prompt})

            try:
                system_content = (
                    "You are a concise quantitative financial analyst embedded in the AI Statcharts platform. "
                    "Answer the user's questions directly and briefly. Use professional terminology. "
                    "If the user asks about data on their screen, do your best with the context provided. "
                    "If no specific data context is available, use your general financial knowledge. "
                    "Keep responses to 2-4 sentences unless the user asks for detail."
                )
                if context_data:
                    system_content += f"\n\nCurrent dashboard context:\n{context_data}"

                with st.spinner("Thinking..."):
                    if config.get("provider") == "gemini":
                        from google import genai
                        from google.genai import types
                        client = genai.Client(api_key=api_key)
                        # Build conversation as a single string for Gemini
                        chat_text = system_content + "\n\n"
                        for msg in st.session_state.chat_messages:
                            role = "User" if msg["role"] == "user" else "Assistant"
                            chat_text += f"{role}: {msg['content']}\n\n"
                        response = client.models.generate_content(
                            model=config["model"],
                            contents=chat_text,
                            config=types.GenerateContentConfig(
                                max_output_tokens=config["max_tokens"],
                                temperature=0.3,
                            ),
                        )
                        bot_reply = response.text
                    else:
                        from openai import OpenAI
                        client = OpenAI(api_key=api_key)
                        messages = [
                            {"role": "system", "content": system_content}
                        ] + st.session_state.chat_messages
                        call_kwargs = {
                            "model": config["model"],
                            "messages": messages,
                            "max_tokens": config["max_tokens"],
                            "temperature": 0.3,
                        }
                        response = client.chat.completions.create(**call_kwargs)
                        bot_reply = response.choices[0].message.content
                st.session_state.chat_messages.append({"role": "assistant", "content": bot_reply})
                _increment_chat_usage()

                # Show the new response immediately without rerunning
                with st.chat_message("assistant"):
                    st.markdown(bot_reply)

            except Exception as e:
                logger.error(f"Chatbot API Error: {e}")
                st.error(f"API Error: {e}")

        # Clear button
        if st.session_state.chat_messages:
            if st.button("Clear Chat", key="clear_chat_btn"):
                st.session_state.chat_messages = []
                st.rerun()
