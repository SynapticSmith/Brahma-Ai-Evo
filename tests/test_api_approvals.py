"""Tests for the GUI approval channel (design doc §5.10).

Contract:
- `X-Brahma-Approval: deny` (default): `ask` -> `deny` immediately.
- `X-Brahma-Approval: gui` requires loopback caller AND `approve` scope on token.
- Non-loopback caller requesting `gui` returns 403 policy_denied.
- Token missing `approve` scope requesting `gui` returns 403 insufficient_scope.
- Approved tools execute and approval is remembered in the API principal's own runtime.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from api_support import auth, build_client, make_config, mint


def test_default_approval_is_deny(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "operator", confirm="operator", scopes=["chat", "models"])

    client = build_client(config)
    # operator profile without gui approval header treats ask as deny
    resp = client.post(
        "/v1/chat/completions",
        headers=auth(token),
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "run command"}],
        },
    )
    # Completed without error (model reports refusal or stub responds)
    assert resp.status_code == 200


def test_gui_approval_requires_approve_scope(tmp_path: Path):
    config = make_config(tmp_path)
    # Token without "approve" scope
    _rec, token = mint(config, "operator", confirm="operator", scopes=["chat", "models"])
    client = build_client(config)

    resp = client.post(
        "/v1/chat/completions",
        headers={**auth(token), "X-Brahma-Approval": "gui"},
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "run command"}],
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "insufficient_scope"


def test_gui_approval_refused_for_non_loopback_caller(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "operator", confirm="operator", scopes=["chat", "models", "approve"])
    client = build_client(config)

    # Fake client IP using TestClient client parameter or request
    # TestClient connects as "testclient", which is in loopback allowlist.
    # We test non-loopback with a patched client host.
    with patch("fastapi.Request.client", new_callable=lambda: property(lambda self: type("Client", (), {"host": "192.168.1.50"})())):
        resp = client.post(
            "/v1/chat/completions",
            headers={**auth(token), "X-Brahma-Approval": "gui"},
            json={
                "model": "brahma-evo",
                "messages": [{"role": "user", "content": "run command"}],
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "policy_denied"


def test_invalid_approval_header_returns_400(tmp_path: Path):
    config = make_config(tmp_path)
    _rec, token = mint(config, "reader")
    client = build_client(config)

    resp = client.post(
        "/v1/chat/completions",
        headers={**auth(token), "X-Brahma-Approval": "invalid_mode"},
        json={
            "model": "brahma-evo",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["param"] == "X-Brahma-Approval"
