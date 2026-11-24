import sys
import types

import pytest

import core
import or_client


def _fake_post(status_code):
    calls = []

    def post(*args, **kwargs):
        calls.append((args, kwargs))
        return types.SimpleNamespace(status_code=status_code)

    return post, calls


def test_call_raises_permission_error_on_401(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    post, calls = _fake_post(401)
    monkeypatch.setattr(or_client.requests, "post", post)
    monkeypatch.setattr(or_client.time, "sleep", lambda *_: pytest.fail("must not retry"))

    client = or_client.OpenRouterClient()
    with pytest.raises(PermissionError, match="Authentication failed"):
        client._call("model-a", [{"role": "user", "content": "hi"}])

    assert len(calls) == 1


def test_fallback_fails_fast_on_401(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    post, calls = _fake_post(401)
    monkeypatch.setattr(or_client.requests, "post", post)
    monkeypatch.setattr(or_client.time, "sleep", lambda *_: pytest.fail("must not retry"))
    monkeypatch.setattr(
        core,
        "provider_router",
        types.SimpleNamespace(guard=lambda _provider: None),
        raising=False,
    )
    monkeypatch.setitem(
        sys.modules,
        "core.provider_router",
        types.SimpleNamespace(guard=lambda _provider: None),
    )

    client = or_client.OpenRouterClient()
    with pytest.raises(PermissionError, match="Authentication failed"):
        client._call_with_fallback(
            ["model-a", "model-b"], [{"role": "user", "content": "hi"}]
        )

    assert len(calls) == 1
