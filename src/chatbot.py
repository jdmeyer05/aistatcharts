import streamlit as st
import os
from openai import OpenAI
import logging
from src.auth import get_user_tier

logger = logging.getLogger(__name__)

# Chat model config by tier
CHAT_TIERS = {
    "free": {
        "model": "gemini-2.5-flash",
        "key_name": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "max_tokens": 400,
        "daily_limit": 5,
        "label": "Gemini Flash",
    },
    "pro": {
        "model": "gemini-2.5-flash",
        "key_name": "GEMINI_API_KEY",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "max_tokens": 800,
        "daily_limit": -1,
        "label": "Gemini Flash",
    },
    "premium": {
        "model": "gpt-5",
        "key_name": "OPENAI_API_KEY",
        "base_url": None,
        "max_tokens": 800,
        "daily_limit": -1,
        "label": "GPT-5",
    },
    "platinum": {
        "model": "gpt-5",
        "key_name": "OPENAI_API_KEY",
        "base_url": None,
        "max_tokens": 1200,
        "daily_limit": -1,
        "label": "GPT-5",
    },
}


def _get_key(name: str):
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name)


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
    """Sidebar chatbot with tier-based model and rate limiting."""
    st.sidebar.divider()

    tier = get_user_tier()
    config = CHAT_TIERS.get(tier, CHAT_TIERS["free"])

    st.sidebar.markdown(
        f'<div style="display:flex; justify-content:space-between; align-items:center;">'
        f'<span style="font-size:1.1rem; font-weight:600;">Analyst Chat</span>'
        f'<span style="font-size:0.7rem; color:#888;">{config["label"]}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Check API key
    api_key = _get_key(config["key_name"])
    if not api_key:
        st.sidebar.warning(f"{config['key_name']} missing. Add to secrets.")
        return

    # Build client
    client_kwargs = {"api_key": api_key}
    if config["base_url"]:
        client_kwargs["base_url"] = config["base_url"]
    client = OpenAI(**client_kwargs)

    # Chat history (capped at 50)
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    elif len(st.session_state.chat_messages) > 50:
        st.session_state.chat_messages = st.session_state.chat_messages[-50:]

    # Display existing messages
    for message in st.session_state.chat_messages:
        with st.sidebar.chat_message(message["role"]):
            st.markdown(message["content"])

    # Rate limit check
    if not _check_chat_limit(config):
        from datetime import date
        today = date.today().isoformat()
        used = st.session_state.get(f"chat_usage_{today}", 0)
        st.sidebar.info(f"Daily chat limit reached ({used}/{config['daily_limit']}). Upgrade for unlimited access.")
        return

    # Chat input
    if prompt := st.sidebar.chat_input("Ask a question..."):
        st.session_state.chat_messages.append({"role": "user", "content": prompt})
        with st.sidebar.chat_message("user"):
            st.markdown(prompt)

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

            messages = [
                {"role": "system", "content": system_content}
            ] + st.session_state.chat_messages

            # GPT-5 uses max_completion_tokens, no temperature
            is_gpt5 = "gpt-5" in config["model"]
            call_kwargs = {
                "model": config["model"],
                "messages": messages,
                **{"max_completion_tokens" if is_gpt5 else "max_tokens": config["max_tokens"]},
                **({"temperature": 0.3} if not is_gpt5 else {}),
            }

            response = client.chat.completions.create(**call_kwargs)

            bot_reply = response.choices[0].message.content
            st.session_state.chat_messages.append({"role": "assistant", "content": bot_reply})
            _increment_chat_usage()

            with st.sidebar.chat_message("assistant"):
                st.markdown(bot_reply)

        except Exception as e:
            logger.error(f"Chatbot API Error: {e}")
            st.sidebar.error(f"API Error: {e}")

    # Clear button
    if len(st.session_state.chat_messages) > 0:
        if st.sidebar.button("Clear Chat"):
            st.session_state.chat_messages = []
            st.rerun()
