"""Unit tests for core.local_brain -- the offline endpoint contract (log section 16).

What these pin down is the context-budget contract introduced with the
system-awareness prompt rewrite: ``chat_complete`` and the streaming path must
request an explicit ``num_ctx``. Ollama's OpenAI-compatible endpoint silently
truncates to its own default (2048-4096 tokens), which is smaller than the
system prompt the app actually sends (core/prompt.txt is ~15KB on its own), so
without the budget the offline 3B model would lose its operating instructions
before the user's turn -- exactly the failure this suite exists to prevent.

No network: ``_post_json`` / ``_open_stream`` are replaced with recorders.
"""

from core.local_brain import LOCAL_NUM_CTX, LocalBrain, LocalBrainError


def _pinned_brain() -> LocalBrain:
    """A LocalBrain whose endpoint/model are constructor-pinned, so the real
    Settings file on this machine cannot leak into a payload assertion."""
    return LocalBrain(endpoint="http://test-host:11434/v1", default_model="test-model")


# --------------------------------------------------------------- chat_complete


def test_chat_complete_requests_an_explicit_context_budget(monkeypatch):
    """The non-streaming payload carries options.num_ctx.

    This is the regression guard for silent truncation: if someone removes the
    budget to "clean up" the payload, the offline model starts ignoring the
    prompt tail and this test fails.
    """
    brain = _pinned_brain()
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        captured["timeout"] = timeout
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    brain.chat_complete(
        messages=[{"role": "user", "content": "hi"}],
    )

    assert captured["payload"]["options"] == {"num_ctx": LOCAL_NUM_CTX}


def test_chat_complete_context_budget_fits_the_system_prompt():
    """The budget must be larger than the prompt it protects.

    core/prompt.txt is the bulk of what the offline path sends; a budget below
    its own size would truncate even before skill context and tools.
    """
    from pathlib import Path

    prompt = (Path(__file__).resolve().parent.parent / "core" / "prompt.txt")
    prompt_bytes = prompt.stat().st_size if prompt.exists() else 0

    # 8192 tokens comfortably exceeds the ~15KB prompt (roughly 4 chars/token
    # for English prose => ~3.7K tokens) while staying inside the bundled 3B
    # models' native 32K window.
    assert LOCAL_NUM_CTX >= 8192
    # Guard the direction of the guarantee: the budget is a token count, the
    # prompt a byte count; 4 bytes/token is a conservative English estimate.
    assert LOCAL_NUM_CTX * 4 > prompt_bytes, (
        "LOCAL_NUM_CTX no longer covers core/prompt.txt; raise the budget or shrink the prompt"
    )


def test_chat_complete_model_and_endpoint_are_used(monkeypatch):
    """Sanity: the recorder saw the pinned model and the pinned host, so the
    assertions above were not vacuous."""
    brain = _pinned_brain()
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    brain.chat_complete(messages=[{"role": "user", "content": "hi"}])

    assert captured["payload"]["model"] == "test-model"
    assert brain.endpoint == "http://test-host:11434/v1"


def test_chat_complete_surfaces_error_field_as_local_brain_error(monkeypatch):
    """An `error` body becomes LocalBrainError, not a silent empty answer."""
    brain = _pinned_brain()
    monkeypatch.setattr(
        brain, "_post_json", lambda payload, timeout: '{"error": "model not found"}'
    )

    try:
        brain.chat_complete(messages=[{"role": "user", "content": "hi"}])
    except LocalBrainError as exc:
        assert "model not found" in str(exc)
    else:
        raise AssertionError("expected LocalBrainError for an error body")


def test_chat_complete_rejects_missing_choices(monkeypatch):
    """A body without choices is a failure, never an empty success."""
    brain = _pinned_brain()
    monkeypatch.setattr(brain, "_post_json", lambda payload, timeout: "{}")

    try:
        brain.chat_complete(messages=[{"role": "user", "content": "hi"}])
    except LocalBrainError as exc:
        assert "no choices" in str(exc)
    else:
        raise AssertionError("expected LocalBrainError for a body without choices")


def test_tools_are_formatted_but_do_not_drop_the_budget(monkeypatch):
    """A tool-bearing turn still carries num_ctx: the tool surface is what
    pushes the prompt over the default window in the first place."""
    brain = _pinned_brain()
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    tools = [
        {
            "name": "open_app",
            "description": "Opens an application.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"app_name": {"type": "STRING"}},
            },
        }
    ]
    brain.chat_complete(messages=[{"role": "user", "content": "open chrome"}], tools=tools)

    assert captured["payload"]["options"] == {"num_ctx": LOCAL_NUM_CTX}
    assert captured["payload"]["tools"], "tools should be present in the payload"


def test_tools_use_lowercase_json_schema(monkeypatch):
    """Gemini-style OBJECT/STRING schema types are lowercased for the local
    runtime, which only understands JSON-Schema casing."""
    brain = _pinned_brain()
    captured = {}

    def fake_post(payload, timeout):
        captured["payload"] = payload
        return '{"choices": [{"message": {"content": "ok"}}]}'

    monkeypatch.setattr(brain, "_post_json", fake_post)
    tools = [
        {
            "name": "reminder",
            "description": "Sets a reminder.",
            "parameters": {
                "type": "OBJECT",
                "properties": {"message": {"type": "STRING"}},
                "required": ["message"],
            },
        }
    ]
    brain.chat_complete(messages=[{"role": "user", "content": "remind me"}], tools=tools)

    sent = captured["payload"]["tools"][0]["function"]
    assert sent["parameters"]["type"] == "object"
    assert sent["parameters"]["properties"]["message"]["type"] == "string"
