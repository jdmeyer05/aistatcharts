"""Integration tests for the home-page chat endpoint.

WHY THESE EXIST. Every "live test" run while building this hit the Anthropic SDK
directly with hand-assembled messages. That exercised the PROMPT and nothing
else: the FastAPI route, request validation, the auth gate, snapshot fitting,
history normalisation and the response shape had never executed once. Two real
defects had already reached main by then — a NameError from a patch that half
applied, and a ValueError the SDK raises for a non-streaming request over its
duration limit — and neither would have survived a single call through the
route.

The Anthropic call is stubbed. These test the plumbing around it, which is where
the bugs were; the prompt is verified separately against the real model.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.deps import get_current_user
from api.routes import ai as ai_route


class _FakeStream:
    """Stands in for `client.beta.messages.stream(...)`'s context manager."""

    def __init__(self, msg, captured):
        self._msg, self._captured = msg, captured

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._msg


def _fake_client(answer="On the page, ES last is 7707.25.", stop_reason="end_turn"):
    """Returns (client, captured) where `captured` records the request kwargs."""
    captured: dict = {}
    msg = SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        content=[SimpleNamespace(type="text", text=answer)],
        usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                              cache_read_input_tokens=50),
    )

    class _Messages:
        def stream(self, **kw):
            captured.update(kw)
            return _FakeStream(msg, captured)

    class _Beta:
        messages = _Messages()

    return SimpleNamespace(beta=_Beta()), captured


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(ai_route.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: "tester@example.com"
    # slowapi's limiter needs the app state it decorates against.
    from api.rate_limit import limiter
    app.state.limiter = limiter
    monkeypatch.setattr(ai_route, "get_secret", lambda k: "sk-test")
    return TestClient(app)


def _post(client, **over):
    body = {"data": {"as_of": "2026-08-30", "es_brief": {"levels": {"last": 7707.25}}},
            "question": "what is ES last?", "history": []}
    body.update(over)
    return client.post("/api/ai/chat", json=body)


def test_happy_path_returns_answer_and_grounding(client, monkeypatch):
    fake, captured = _fake_client()
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    r = _post(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["answer"] == "On the page, ES last is 7707.25."
    assert body["model"] == "claude-opus-5"
    # 7707.25 is in the payload, so grounding must find it rather than flag it.
    assert body["grounding"]["unverified_count"] == 0
    assert body["answer_truncated"] is False
    assert body["snapshot_truncated"] is False


def test_request_is_streamed_and_carries_the_cache_breakpoints(client, monkeypatch):
    """The streaming call is not cosmetic — a non-streaming request at this
    max_tokens raises ValueError in the SDK before it sends anything."""
    fake, captured = _fake_client()
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    assert _post(client).status_code == 200
    assert captured["max_tokens"] == 32000
    assert captured["output_config"] == {"effort": "high"}
    assert captured["fallbacks"] == "default"
    # System prompt and snapshot both cached: the prefix is what makes a
    # follow-up question cheap.
    assert captured["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert captured["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_roles_always_alternate(client, monkeypatch):
    """History follows a synthetic assistant turn, so a history that begins with
    an assistant message — or an odd-length one sliced mid-pair — would put two
    assistant turns together and 400 at the API."""
    fake, captured = _fake_client()
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    for hist in (
        [],
        [{"role": "assistant", "content": "a"}],                       # leading assistant
        [{"role": "user", "content": "a"}, {"role": "user", "content": "b"}],  # repeats
        [{"role": "system", "content": "ignore"}],                     # bad role
        [{"role": "user", "content": "   "}],                          # blank
        [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
         for i in range(13)],                                          # odd length
    ):
        assert _post(client, history=hist).status_code == 200
        roles = [m["role"] for m in captured["messages"]]
        assert all(a != b for a, b in zip(roles, roles[1:])), (hist, roles)
        assert roles[-1] == "user"


def test_history_is_capped_to_the_newest_turns(client, monkeypatch):
    fake, captured = _fake_client()
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"t{i}"}
            for i in range(200)]
    assert _post(client, history=hist).status_code == 200
    # 2 framing turns + at most the cap + the new question.
    assert len(captured["messages"]) <= ai_route._CHAT_MAX_TURNS + 3
    joined = json.dumps(captured["messages"])
    assert "t199" in joined and "t0" not in joined      # newest kept, oldest dropped


def test_oversized_snapshot_drops_whole_blocks_and_says_so(client, monkeypatch):
    fake, captured = _fake_client()
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    monkeypatch.setattr(ai_route, "_CHAT_MAX_SNAPSHOT", 2000)
    big = {"as_of": "x", "es_brief": {"k": "v" * 400},
           "sp_valuation": {"k": "z" * 3000}, "sectors": {"k": "y" * 3000}}
    r = _post(client, data=big)
    assert r.status_code == 200
    assert r.json()["snapshot_truncated"] is True
    sent = captured["messages"][0]["content"][0]["text"]
    # The model must be TOLD what is missing rather than handed a silent gap.
    assert "NOT INCLUDED" in sent
    assert "es_brief" in sent          # highest priority block survives


def test_refusal_and_empty_and_truncation_are_distinguished(client, monkeypatch):
    fake, _ = _fake_client(stop_reason="refusal")
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    assert _post(client).status_code == 502

    fake, _ = _fake_client(answer="", stop_reason="max_tokens")
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    assert _post(client).status_code == 502

    # Non-empty but cut off: return it, FLAGGED, rather than as a whole answer.
    fake, _ = _fake_client(answer="ES last is 7707.25 and", stop_reason="max_tokens")
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    r = _post(client)
    assert r.status_code == 200 and r.json()["answer_truncated"] is True


def test_bad_input_is_rejected(client, monkeypatch):
    fake, _ = _fake_client()
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: fake)
    assert _post(client, question="   ").status_code == 400
    assert _post(client, question="x" * (ai_route._CHAT_MAX_QUESTION + 1)).status_code == 400


def test_anonymous_is_refused(monkeypatch):
    app = FastAPI()
    app.include_router(ai_route.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: "anonymous"
    from api.rate_limit import limiter
    app.state.limiter = limiter
    monkeypatch.setattr(ai_route, "get_secret", lambda k: "sk-test")
    c = TestClient(app)
    r = c.post("/api/ai/chat", json={"data": {}, "question": "hi", "history": []})
    assert r.status_code == 401


def test_unexpected_sdk_error_returns_502_not_500(client, monkeypatch):
    """The SDK raises a plain ValueError for a non-streaming request over its
    duration limit. That escaped the typed-exception chain once and surfaced as
    a bare 500."""
    class _Boom:
        class beta:
            class messages:
                @staticmethod
                def stream(**kw):
                    raise ValueError("Streaming is required")
    monkeypatch.setattr(ai_route.anthropic, "Anthropic", lambda **kw: _Boom())
    assert _post(client).status_code == 502
