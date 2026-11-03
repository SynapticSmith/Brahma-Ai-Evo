"""The HTTP contract: auth, the error envelope, and one non-streaming answer."""

from __future__ import annotations

import os

from api_support import auth, build_client, make_config, mint

BODY = {"model": "brahma-evo", "messages": [{"role": "user", "content": "hello"}]}


def _client(tmp_path, **config_overrides):
    config = make_config(tmp_path, **config_overrides)
    _record, token = mint(config, "reader")
    return build_client(config), token, config


def test_health_needs_no_token(tmp_path):
    client, _token, _config = _client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "brahma_api"


def test_missing_and_bad_tokens_are_401_envelopes(tmp_path):
    client, _token, _config = _client(tmp_path)
    for headers in ({}, auth("not-a-real-token")):
        response = client.post("/v1/chat/completions", json=BODY, headers=headers)
        assert response.status_code == 401
        error = response.json()["error"]
        assert error["code"] == "invalid_api_key"
        assert error["type"] == "invalid_request_error"


def test_models_lists_what_the_key_can_use(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.get("/v1/models", headers=auth(token))
    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "list"
    assert [row["id"] for row in payload["data"]] == [
        "brahma-evo",
        "brahma-evo:read",
        "brahma-evo:plan",
    ]
    assert payload["data"][0]["brahma"]["policy"] == "reader"


def test_a_non_streaming_completion_has_the_openai_shape(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.post("/v1/chat/completions", json=BODY, headers=auth(token))
    assert response.status_code == 200
    payload = response.json()

    assert payload["object"] == "chat.completion"
    assert payload["model"] == "brahma-evo"
    assert payload["id"].startswith("chatcmpl-")
    choice = payload["choices"][0]
    assert choice["index"] == 0
    assert choice["message"]["role"] == "assistant"
    assert "[stub provider]" in choice["message"]["content"]
    assert choice["finish_reason"] == "stop"

    # The stub reports no counts, so usage must be null rather than zeros (I-22).
    assert payload["usage"] is None
    assert payload["brahma"]["tokens_reported"] is False
    assert payload["brahma"]["lane"] == "agent"
    assert payload["brahma"]["stop_reason"] == "completed"
    assert payload["system_fingerprint"].startswith("brahma-evo/")


def test_advisory_parameters_are_echoed_not_dropped(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        json={**BODY, "max_tokens": 50, "user": "someone"},
        headers=auth(token),
    )
    assert response.status_code == 200
    assert set(response.json()["brahma"]["ignored_params"]) == {"max_tokens", "user"}


def test_unfakeable_parameters_are_refused(tmp_path):
    client, token, _config = _client(tmp_path)
    cases = [
        ({"n": 2}, "n"),
        ({"logprobs": True}, "logprobs"),
        ({"stop": ["\n"]}, "stop"),
        ({"response_format": {"type": "json_object"}}, "response_format"),
        ({"tools": [{"type": "function", "function": {"name": "x"}}]}, "tools"),
    ]
    for extra, param in cases:
        response = client.post(
            "/v1/chat/completions", json={**BODY, **extra}, headers=auth(token)
        )
        assert response.status_code == 400, extra
        error = response.json()["error"]
        assert error["param"] == param
        assert error["code"] in ("invalid_value", "tools_not_supported")


def test_unknown_model_is_404(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        json={**BODY, "model": "gpt-4o"},
        headers=auth(token),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "model_not_found"


def test_a_chat_key_gets_no_tools_and_no_workspace(tmp_path):
    config = make_config(tmp_path)
    _record, token = mint(config, "chat")
    client = build_client(config)
    response = client.post("/v1/chat/completions", json=BODY, headers=auth(token))
    assert response.status_code == 200
    assert "tools=0" in response.json()["choices"][0]["message"]["content"]
    assert response.json()["brahma"]["tool_calls"] == 0


def test_a_reader_key_gets_tools(tmp_path):
    config = make_config(tmp_path)
    _record, token = mint(config, "reader")
    client = build_client(config)
    response = client.post("/v1/chat/completions", json=BODY, headers=auth(token))
    assert response.status_code == 200
    text = response.json()["choices"][0]["message"]["content"]
    assert "tools=0" not in text


def test_scope_is_enforced_per_route(tmp_path):
    config = make_config(tmp_path)
    _record, token = mint(config, "reader", scopes=("models",))
    client = build_client(config)
    assert client.get("/v1/models", headers=auth(token)).status_code == 200
    response = client.post("/v1/chat/completions", json=BODY, headers=auth(token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "insufficient_scope"


def test_a_revoked_token_stops_working(tmp_path, monkeypatch):
    from brahma_api import auth as auth_module
    from brahma_api.auth import TokenRegistry

    config = make_config(tmp_path)
    record, token = mint(config, "reader")
    client = build_client(config)
    assert client.get("/v1/models", headers=auth(token)).status_code == 200

    TokenRegistry(config.tokens_path).revoke(record.id)
    # Windows can hand two writes the same timestamp; make the change visible.
    stat = config.tokens_path.stat()
    os.utime(config.tokens_path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    monkeypatch.setattr(auth_module, "RELOAD_INTERVAL_S", 0.0)
    client.app.state.registry._checked_at = 0.0
    assert client.get("/v1/models", headers=auth(token)).status_code == 401


def test_a_large_body_is_413_before_parsing(tmp_path):
    client, token, config = _client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        content=b"x" * (config.limits.max_body_bytes + 1),
        headers={**auth(token), "content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"


def test_an_unknown_route_keeps_the_envelope(tmp_path):
    client, _token, _config = _client(tmp_path)
    response = client.get("/v1/nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_route"


def test_empty_messages_are_a_400(tmp_path):
    client, token, _config = _client(tmp_path)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "brahma-evo", "messages": []},
        headers=auth(token),
    )
    assert response.status_code == 400
    assert response.json()["error"]["param"] == "messages"


def test_auth_can_be_disabled_on_purpose(tmp_path):
    config = make_config(tmp_path, auth_required=False)
    client = build_client(config)
    response = client.post("/v1/chat/completions", json=BODY)
    assert response.status_code == 200
    assert response.json()["brahma"]["lane"] == "agent"
