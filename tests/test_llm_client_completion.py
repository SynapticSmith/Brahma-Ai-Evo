"""The OpenAI-compatible completion path: `chat_as_completion` message assembly.

`chat_as_completion` is what lets the custom endpoint run an agentic turn, so
the shape of the `messages` array it sends is a contract, not an implementation
detail:

* a bare call wraps `system` + optional history + the prompt;
* a tool-loop follow-up passes `full_messages` and must be sent VERBATIM --
  no second system message and no empty user turn, both of which some
  OpenAI-compatible servers reject.

These tests stub the transport (`_openai_chat_completion`) and assert the
messages, so no network and no server are involved.
"""

import pytest

from llm_client import UnifiedAIClient


@pytest.fixture
def client(monkeypatch):
    """A client whose transport is captured instead of sent."""
    c = UnifiedAIClient()
    captured = {}

    monkeypatch.setattr(c, "reload_settings", lambda: None)
    monkeypatch.setattr(c, "_require_custom", lambda: None)
    monkeypatch.setattr(
        c, "_openai_chat_completion",
        lambda messages, **kw: captured.update(messages=messages, kwargs=kw)
        or {"content": "ok"},
    )
    import core.provider_router as pr
    monkeypatch.setattr(pr, "guard", lambda pid: None)
    return c, captured


def test_bare_call_wraps_system_history_and_prompt(client):
    c, captured = client
    c.chat_as_completion(
        "custom", "hello", system="SYS", history=[{"role": "user", "content": "prev"}]
    )
    assert captured["messages"] == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "prev"},
        {"role": "user", "content": "hello"},
    ]


def test_full_messages_are_sent_verbatim(client):
    c, captured = client
    convo = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "name": "t", "content": "done"},
    ]
    c.chat_as_completion("custom", "", full_messages=convo, tools=[])
    sent = captured["messages"]
    # Verbatim: no prepended system message, no appended empty user turn.
    assert sent == convo
    assert sum(1 for m in sent if m["role"] == "system") == 1
    assert not any(m["role"] == "user" and m["content"] == "" for m in sent)


def test_full_messages_is_copied_not_aliased(client):
    c, captured = client
    convo = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}]
    c.chat_as_completion("custom", "", full_messages=convo)
    captured["messages"].append({"role": "user", "content": "mutated"})
    assert len(convo) == 2, "the caller's list must not be mutated through the alias"


def test_tools_ride_the_request(client):
    c, captured = client
    c.chat_as_completion(
        "custom", "hi", tools=[{"type": "function", "function": {"name": "t"}}]
    )
    assert captured["kwargs"]["tools"] == [
        {"type": "function", "function": {"name": "t"}}
    ]


def test_chat_as_text_wrapper_returns_content(monkeypatch):
    """`chat_as` stays a text wrapper so every existing caller is unchanged."""
    c = UnifiedAIClient()
    monkeypatch.setattr(c, "reload_settings", lambda: None)
    monkeypatch.setattr(
        c, "chat_as_completion", lambda pid, prompt, **kw: {"content": "  reply  "}
    )
    assert c.chat_as("custom", "hi") == "reply"


def test_chat_as_reports_tool_calls_instead_of_an_empty_reply(monkeypatch):
    """A tool-call-only message must not look like a failed empty attempt."""
    c = UnifiedAIClient()
    monkeypatch.setattr(c, "reload_settings", lambda: None)
    monkeypatch.setattr(
        c, "chat_as_completion",
        lambda pid, prompt, **kw: {"content": "", "tool_calls": [{"id": "1"}]},
    )
    out = c.chat_as("custom", "hi")
    assert out and "tool" in out.lower()
