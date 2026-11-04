"""Tests for per-token request rate limiting (design doc §5.12)."""

from __future__ import annotations

from pathlib import Path
from api_support import auth, build_client, make_config, mint


def test_per_token_rate_limiting_exceeded_yields_429(tmp_path: Path):
    config = make_config(tmp_path)
    config.limits.requests_per_minute = 3

    _rec1, token1 = mint(config, "reader", label="token1")
    _rec2, token2 = mint(config, "reader", label="token2")

    client = build_client(config)

    # 3 allowed requests for token1
    for i in range(3):
        r = client.post(
            "/v1/chat/completions",
            headers=auth(token1),
            json={
                "model": "brahma-evo",
                "messages": [{"role": "user", "content": f"msg {i}"}],
            },
        )
        assert r.status_code == 200, r.text

    # 4th request from token1 is rate-limited
    r4 = client.post(
        "/v1/chat/completions",
        headers=auth(token1),
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "msg 4"}],
        },
    )
    assert r4.status_code == 429
    assert r4.json()["error"]["code"] == "rate_limit_exceeded"
    assert "Retry-After" in r4.headers

    # Token2 has its own bucket and should succeed
    r_token2 = client.post(
        "/v1/chat/completions",
        headers=auth(token2),
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "from token2"}],
        },
    )
    assert r_token2.status_code == 200
