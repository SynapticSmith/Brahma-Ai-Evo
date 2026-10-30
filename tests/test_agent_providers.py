"""Provider adapters: the custom and OpenRouter paths actually build.

These used to import ``ai_client`` from ``llm_client``, a name that module never
defined, so every non-stub CLI run failed at build time with an ImportError.
The fakes below mirror the shipped module's public name (``client``) and nothing
else, so the regression cannot come back unnoticed. Stdlib-only: the harness CI
job installs just pytest.
"""
from __future__ import annotations

import sys
import types

import agent.providers as providers


class FakeLLMClient:
    _custom_url = "http://cfg.test/v1"
    _custom_model = "custom-model"
    _custom_api_key = "secret"

    def __init__(self):
        self.reloads = 0
        self.completions = []

    def reload_settings(self):
        self.reloads += 1

    def _auth_headers(self, api_key):
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    def chat_as_completion(self, provider_id, prompt, **kwargs):
        self.completions.append((provider_id, prompt, kwargs))
        return {"role": "assistant", "content": "plain custom"}

    def _openai_chat_completion(self, messages, **kwargs):
        self.completions.append(("openrouter", "", kwargs))
        return {"role": "assistant", "content": "plain openrouter"}


def _fake_llm(monkeypatch, client=None):
    client = client or FakeLLMClient()
    module = types.ModuleType("llm_client")
    module.client = client  # the shipped name; `ai_client` deliberately absent
    monkeypatch.setitem(sys.modules, "llm_client", module)
    return client


def _capture_stream(monkeypatch, chunks=("hi",)):
    captured = {}

    def fake_openai_stream(url, payload, *, headers=None):
        captured.update(url=url, payload=payload, headers=headers)
        for text in chunks:
            yield {"choices": [{"index": 0, "delta": {"content": text}}]}

    monkeypatch.setattr(providers, "openai_stream", fake_openai_stream)
    return captured


def test_the_custom_adapter_builds_against_the_client_name(monkeypatch):
    _fake_llm(monkeypatch)
    completer = providers.build_completer("custom")

    assert callable(completer)
    assert callable(completer.stream)


def test_the_openrouter_adapter_builds_against_the_client_name(monkeypatch):
    _fake_llm(monkeypatch)
    or_client = types.SimpleNamespace(
        API_URL="https://openrouter.test/api/v1",
        TEXT_MODELS=["or-model"],
        _load_api_key=lambda: "or-key",
    )
    monkeypatch.setitem(sys.modules, "or_client", or_client)

    completer = providers.build_completer("openrouter")

    assert callable(completer)
    assert callable(completer.stream)


def test_custom_stream_posts_one_openai_streaming_request(monkeypatch):
    client = _fake_llm(monkeypatch)
    captured = _capture_stream(monkeypatch, chunks=("he", "llo"))
    deltas = []

    message = providers.custom_stream(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "read_file"}}],
        deltas.append,
        temperature=0.3,
        max_tokens=4096,
    )

    assert message["content"] == "hello"
    assert [delta["text"] for delta in deltas] == ["he", "llo"]
    assert captured["url"] == "http://cfg.test/v1/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer secret"}
    payload = captured["payload"]
    assert payload["stream"] is True
    assert payload["model"] == "custom-model"
    assert payload["temperature"] == 0.3
    assert payload["max_tokens"] == 4096
    assert payload["tools"][0]["function"]["name"] == "read_file"
    # Settings are re-read per call, like the non-streaming transport does.
    assert client.reloads == 1


def test_custom_stream_does_not_double_the_chat_path(monkeypatch):
    client = _fake_llm(monkeypatch)
    client._custom_url = "http://cfg.test/v1/chat/completions"
    captured = _capture_stream(monkeypatch)

    providers.custom_stream([], [], None)

    assert captured["url"] == "http://cfg.test/v1/chat/completions"


def test_describe_custom_reports_the_configured_endpoint(monkeypatch):
    _fake_llm(monkeypatch)

    detail = providers.describe("custom")

    assert detail == {
        "provider": "custom",
        "endpoint": "http://cfg.test/v1",
        "model": "custom-model",
    }
