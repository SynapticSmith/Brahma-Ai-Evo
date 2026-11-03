"""Tests covering the complete error envelope matrix from design doc §5.13."""

from __future__ import annotations

from api_support import auth, build_client, make_config, mint
from brahma_api.errors import (
    conflict,
    forbidden,
    invalid,
    not_found,
    rate_limited,
    server_error,
    too_large,
    unauthorized,
    unavailable,
)


def _client(tmp_path, **config_overrides):
    config = make_config(tmp_path, **config_overrides)
    _record, token = mint(config, "reader")
    return build_client(config), token, config


def test_error_helper_shapes():
    err400 = invalid("bad param", param="n", code="invalid_value")
    assert err400.status == 400
    assert err400.body()["error"]["code"] == "invalid_value"
    assert err400.body()["error"]["param"] == "n"

    err401 = unauthorized()
    assert err401.status == 401
    assert err401.body()["error"]["code"] == "invalid_api_key"

    err403 = forbidden("no scope", code="insufficient_scope")
    assert err403.status == 403
    assert err403.body()["error"]["type"] == "permission_denied"

    err404 = not_found("no model", code="model_not_found")
    assert err404.status == 404
    assert err404.body()["error"]["code"] == "model_not_found"

    err409 = conflict("session busy", code="session_busy")
    assert err409.status == 409
    assert err409.body()["error"]["code"] == "session_busy"

    err413 = too_large("too big")
    assert err413.status == 413
    assert err413.body()["error"]["code"] == "payload_too_large"

    err429 = rate_limited("rate limit", retry_after=10)
    assert err429.status == 429
    assert err429.body()["error"]["code"] == "rate_limit_exceeded"
    assert err429.body()["error"]["type"] == "rate_limit_error"
    assert err429.headers.get("Retry-After") == "10"

    err500 = server_error("internal fail")
    assert err500.status == 500
    assert err500.body()["error"]["type"] == "server_error"

    err503 = unavailable("provider down")
    assert err503.status == 503
    assert err503.body()["error"]["code"] == "provider_unavailable"


def test_400_tools_in_agent_lane(tmp_path):
    client, token, _config = _client(tmp_path)
    res = client.post(
        "/v1/chat/completions",
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "custom"}}],
        },
        headers=auth(token),
    )
    assert res.status_code == 400
    err = res.json()["error"]
    assert err["code"] == "tools_not_supported"
    assert err["type"] == "invalid_request_error"


def test_401_bad_token(tmp_path):
    client, _token, _config = _client(tmp_path)
    res = client.post(
        "/v1/chat/completions",
        json={"model": "brahma-evo", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth("invalid-token-1234"),
    )
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_api_key"


def test_403_insufficient_scope(tmp_path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader", scopes=["models"])  # missing "chat" scope
    client = build_client(config)

    res = client.post(
        "/v1/chat/completions",
        json={"model": "brahma-evo", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(token),
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "insufficient_scope"
    assert res.json()["error"]["type"] == "permission_denied"


def test_404_unknown_model_and_run(tmp_path):
    client, token, _config = _client(tmp_path)

    res_model = client.post(
        "/v1/chat/completions",
        json={"model": "brahma-evo:nonexistent", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(token),
    )
    assert res_model.status_code == 404
    assert res_model.json()["error"]["code"] == "model_not_found"

    res_run = client.get("/v1/brahma/runs/nonexistent-run-id", headers=auth(token))
    assert res_run.status_code == 404
    assert res_run.json()["error"]["code"] == "run_not_found"


def test_413_payload_too_large(tmp_path):
    # Set limit to 100 bytes
    config = make_config(tmp_path)
    config.limits.max_body_bytes = 100
    _rec, token = mint(config, "reader")
    client = build_client(config)

    huge_payload = {"model": "brahma-evo", "messages": [{"role": "user", "content": "x" * 200}]}
    res = client.post("/v1/chat/completions", json=huge_payload, headers=auth(token))
    assert res.status_code == 413
    assert res.json()["error"]["code"] == "payload_too_large"


def test_429_concurrency_limit_hit(tmp_path):
    config = make_config(tmp_path)
    # Set concurrent runs to 1
    config.limits.concurrent_runs = 1
    config.limits.concurrent_runs_per_token = 1
    _rec, token = mint(config, "reader")

    client = build_client(config)

    # Acquire the semaphore artificially to simulate saturation
    limiter = client.app.state.limiter
    import asyncio
    asyncio.run(limiter._global_sem.acquire())
    try:
        res = client.post(
            "/v1/chat/completions",
            json={"model": "brahma-evo", "messages": [{"role": "user", "content": "hi"}]},
            headers=auth(token),
        )
        assert res.status_code == 429
        err = res.json()["error"]
        assert err["code"] == "rate_limit_exceeded"
        assert err["type"] == "rate_limit_error"
        assert res.headers.get("retry-after") == "5"
    finally:
        limiter._global_sem.release()


def test_503_provider_unavailable(tmp_path):
    from agent.providers import ProviderUnavailable

    def failing_factory(*_args, **_kwargs):
        raise ProviderUnavailable("Test model provider is currently offline.")

    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")
    client = build_client(config, factory=failing_factory)

    res = client.post(
        "/v1/chat/completions",
        json={"model": "brahma-evo", "messages": [{"role": "user", "content": "hi"}]},
        headers=auth(token),
    )
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "provider_unavailable"
    assert res.json()["error"]["type"] == "server_error"
