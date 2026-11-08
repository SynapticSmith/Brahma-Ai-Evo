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

import pytest

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


# --------------------------------------------------------------------------- #
# Gemini: the harness adapter is the OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #

def test_the_gemini_adapter_builds_without_the_google_sdk():
    completer = providers.build_completer("gemini")

    assert callable(completer)
    assert callable(completer.stream)
    # A single-provider chain still reports which provider answered.
    assert completer.last_provider == "gemini"


def test_gemini_complete_posts_the_openai_shape(monkeypatch):
    captured = {}

    def fake_post(url, payload, headers, timeout, *, label=""):
        captured.update(url=url, payload=payload, headers=headers, timeout=timeout)
        return {
            "choices": [
                {
                    "message": {
                        "content": [{"text": "he"}, {"text": "llo"}],
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "read_file", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        }

    monkeypatch.setattr(providers, "_post_json", fake_post)
    monkeypatch.setattr(providers, "_gemini_key", lambda: "gem-key")

    message = providers._gemini_complete(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "read_file"}}],
        model=None,
        temperature=0.4,
    )

    assert message["role"] == "assistant"
    assert message["content"] == "hello"
    assert message["tool_calls"][0]["function"]["name"] == "read_file"
    assert captured["url"] == providers.GEMINI_OPENAI_URL
    assert captured["headers"]["Authorization"] == "Bearer gem-key"
    payload = captured["payload"]
    assert payload["model"] == providers.DEFAULT_GEMINI_MODEL
    assert payload["temperature"] == 0.4
    assert payload["stream"] is False
    assert payload["tools"][0]["function"]["name"] == "read_file"


def test_gemini_stream_uses_the_shared_transport(monkeypatch):
    captured = _capture_stream(monkeypatch, chunks=("a", "b"))
    monkeypatch.setattr(providers, "_gemini_key", lambda: "gem-key")
    deltas = []

    message = providers._gemini_stream(
        [{"role": "user", "content": "hi"}],
        [],
        deltas.append,
        model="gemini-test",
        temperature=0.1,
    )

    assert message["content"] == "ab"
    assert [delta["text"] for delta in deltas] == ["a", "b"]
    assert captured["url"] == providers.GEMINI_OPENAI_URL
    assert captured["payload"]["model"] == "gemini-test"
    assert captured["payload"]["stream"] is True
    assert captured["headers"]["Authorization"] == "Bearer gem-key"


def test_gemini_rejects_a_response_without_choices():
    with pytest.raises(providers.ProviderUnavailable):
        providers._gemini_message({"choices": []})
    with pytest.raises(providers.ProviderUnavailable):
        providers._gemini_message("not a dict")


def test_gemini_key_prefers_the_environment(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "env-key")
    assert providers._gemini_key() == "env-key"


def test_gemini_key_reports_a_missing_key(monkeypatch):
    import pytest

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    fake = types.ModuleType("core.secret_store")
    fake.load_api_keys = lambda: {}
    monkeypatch.setitem(sys.modules, "core.secret_store", fake)

    with pytest.raises(providers.ProviderUnavailable):
        providers._gemini_key()


# --------------------------------------------------------------------------- #
# auto chain and failover
# --------------------------------------------------------------------------- #

def test_the_app_chain_puts_the_preferred_provider_first(monkeypatch):
    import core.provider_router as router

    monkeypatch.setattr(
        providers,
        "_load_app_settings",
        lambda: {"default_ai_provider": "OpenRouter"},
    )
    monkeypatch.setattr(
        router,
        "chain",
        lambda only_configured=False: ["local", "openrouter", "custom"],
    )

    assert providers.app_provider_chain() == ["openrouter", "local", "custom"]


def test_offline_mode_removes_cloud_providers_from_auto(monkeypatch):
    import core.provider_router as router

    monkeypatch.setattr(
        providers,
        "_load_app_settings",
        lambda: {
            "default_ai_provider": "Google Gemini",
            "offline_mode_enabled": True,
        },
    )
    monkeypatch.setattr(
        router,
        "chain",
        lambda only_configured=False: ["gemini", "local", "openrouter"],
    )

    assert providers.app_provider_chain() == ["local"]


def test_no_preferred_provider_keeps_the_historical_probe(monkeypatch):
    import core.provider_router as router

    monkeypatch.setattr(providers, "_load_app_settings", lambda: {})
    monkeypatch.setattr(
        router,
        "chain",
        lambda only_configured=False: ["gemini", "local"],
    )

    assert providers.app_provider_chain() == []


def test_an_explicit_provider_is_never_rewritten_by_settings(monkeypatch):
    # A token pin / --provider is a direct request: no kill-switch guard and no
    # chain substitution. The GUI toggle only governs `auto`.
    monkeypatch.setattr(
        providers,
        "app_provider_chain",
        lambda: ["gemini", "openrouter"],
    )

    assert providers.resolve_chain("custom") == ["custom"]
    assert providers.resolve_chain("openrouter") == ["openrouter"]


def test_auto_with_nothing_reachable_is_unavailable(monkeypatch):
    # A fresh install has no settings pin, no key and no local server, so
    # `auto` has nothing to probe. This is the branch that turns into HTTP 503
    # on a CI runner: a test that wants 200 must pin its provider instead of
    # relying on whatever the host machine happens to have running.
    import core.local_brain as local_brain

    monkeypatch.setattr(providers, "_load_app_settings", lambda: {})
    monkeypatch.setattr(local_brain.local_brain, "is_available", lambda: False)
    monkeypatch.setattr(providers, "_has_custom", lambda: False)
    monkeypatch.setattr(providers, "_has_openrouter", lambda: False)
    monkeypatch.delenv("BRAHMA_AGENT_PROVIDER", raising=False)

    with pytest.raises(providers.ProviderUnavailable):
        providers.resolve_chain("auto")


def test_an_unknown_provider_is_rejected():
    with pytest.raises(providers.ProviderUnavailable):
        providers.resolve_chain("not-a-provider")


def test_the_chain_fails_over_before_the_first_output():
    calls = []

    def down(*_args):
        calls.append("down")
        raise RuntimeError("down")

    def up(*_args):
        calls.append("up")
        return {"role": "assistant", "content": "ok"}

    completer = providers._ChainCompleter([("local", down), ("stub", up)])
    message = completer([], [])

    assert message["content"] == "ok"
    assert calls == ["down", "up"]
    assert completer.last_provider == "stub"


def test_the_streaming_chain_fails_over_before_a_delta():
    def down(messages, tools, on_delta):
        raise RuntimeError("down")

    def up(messages, tools, on_delta):
        on_delta({"kind": "text", "text": "ok"})
        return {"role": "assistant", "content": "ok"}

    down.stream = down
    up.stream = up
    completer = providers._ChainCompleter([("local", down), ("stub", up)])
    deltas = []
    message = completer.stream([], [], deltas.append)

    assert message["content"] == "ok"
    assert [delta["text"] for delta in deltas] == ["ok"]
    assert completer.last_provider == "stub"


def test_the_streaming_chain_does_not_switch_after_a_delta():
    def partial(messages, tools, on_delta):
        on_delta({"kind": "text", "text": "already seen"})
        raise RuntimeError("mid-stream")

    def up(messages, tools, on_delta):  # pragma: no cover - must not be called
        raise AssertionError("a second provider must not answer after a delta")

    partial.stream = partial
    up.stream = up
    completer = providers._ChainCompleter([("local", partial), ("stub", up)])
    deltas = []

    with pytest.raises(RuntimeError):
        completer.stream([], [], deltas.append)
    assert [delta["text"] for delta in deltas] == ["already seen"]
