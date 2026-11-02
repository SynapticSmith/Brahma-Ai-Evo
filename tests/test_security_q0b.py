"""Phase 0 (Q0-B) regressions: gateway exposure and unsigned self-update.

Scope is deliberately narrow: loopback/off-by-default and removal of the
git-reset updater paths. Route authentication, DPAPI secrets, and plugin
allowlists are separate, still-open decisions.
"""

import asyncio
import json
from pathlib import Path

from brahma_connect.gateway.server import BrahmaGateway, BrahmaGatewayConfig

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "docs", "tests"}


def _source_files():
    for path in ROOT.rglob("*.py"):
        if path.resolve() == SELF:
            continue
        if SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        yield path


def test_gateway_defaults_to_loopback_and_off(tmp_path):
    config = BrahmaGatewayConfig.load(tmp_path)

    assert config.host == "127.0.0.1"
    assert config.enabled is False
    assert config.advertise is False


def test_gateway_config_can_still_opt_in_to_the_lan(tmp_path):
    config_path = tmp_path / "config" / "brahma_connect.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"host": "0.0.0.0", "enabled": True, "advertise": True}),
        encoding="utf-8",
    )

    config = BrahmaGatewayConfig.load(tmp_path)

    assert config.host == "0.0.0.0"
    assert config.enabled is True
    assert config.advertise is True


def test_disabled_gateway_never_constructs_a_server(tmp_path, monkeypatch):
    import brahma_connect.gateway.server as server

    def _explode(*args, **kwargs):
        raise AssertionError("a disabled gateway must not start uvicorn")

    monkeypatch.setattr(server.uvicorn, "Config", _explode)
    gateway = BrahmaGateway(tmp_path, BrahmaGatewayConfig.load(tmp_path))

    asyncio.run(gateway.serve())

    assert gateway.is_running() is False


def test_no_unsigned_self_update_path_exists():
    for name in ("updater.py", "core/updater.py", "core/updater_ota.py"):
        assert not (ROOT / name).exists(), f"{name} is an unsigned update path"


def test_no_hardcoded_upstream_or_hard_reset():
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        if "Brahma---personal" in text:
            offenders.append(f"{path.relative_to(ROOT)} -> hardcoded upstream")
        if "reset" in text and "--hard" in text:
            offenders.append(f"{path.relative_to(ROOT)} -> git reset --hard")
    assert offenders == []


def test_startup_does_not_call_an_updater():
    source = (ROOT / "main.py").read_text(encoding="utf-8")

    assert "update_from_github" not in source
    assert "restart_application" not in source


def test_gateway_health_is_public(tmp_path):
    from fastapi.testclient import TestClient

    gateway = BrahmaGateway(tmp_path, BrahmaGatewayConfig(enabled=True))
    client = TestClient(gateway.app)

    response = client.get("/health")
    assert response.status_code == 200
    assert response.json().get("ok") is True


def test_gateway_control_routes_require_authentication(tmp_path):
    from fastapi.testclient import TestClient

    gateway = BrahmaGateway(tmp_path, BrahmaGatewayConfig(enabled=True))
    client = TestClient(gateway.app)

    # All unauthenticated management/control calls must be rejected with 401
    endpoints = [
        ("GET", "/gateway/info"),
        ("GET", "/gateway/pair"),
        ("GET", "/gateway/devices"),
        ("GET", "/gateway/logs"),
        ("GET", "/gateway/pending"),
        ("POST", "/gateway/pending/req_1/approve"),
        ("POST", "/gateway/pending/req_1/reject"),
        ("POST", "/gateway/devices/dev_1/revoke"),
        ("POST", "/gateway/devices/dev_1/forget"),
    ]

    for method, path in endpoints:
        resp = client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} returned {resp.status_code}, expected 401"
        assert "Authentication required" in resp.json().get("detail", "")


def test_gateway_control_routes_reject_invalid_and_revoked_token(tmp_path):
    from fastapi.testclient import TestClient

    gateway = BrahmaGateway(tmp_path, BrahmaGatewayConfig(enabled=True))
    client = TestClient(gateway.app)

    # Invalid token format / unknown secret -> 401
    resp = client.get("/gateway/info", headers={"Authorization": "Bearer not-a-valid-token"})
    assert resp.status_code == 401

    resp = client.get("/gateway/info", headers={"Authorization": "Bearer device_1:wrong_secret"})
    assert resp.status_code == 401

    # Create a real device and then revoke it
    record, secret = gateway.device_manager.create_from_pairing(
        name="Revoked Phone",
        platform="android",
    )
    gateway.device_manager.revoke(record.device_id)

    # Revoked device must receive 403 Forbidden
    resp = client.get(
        "/gateway/info",
        headers={"Authorization": f"Bearer {record.device_id}:{secret}"},
    )
    assert resp.status_code == 403
    assert "revoked" in resp.json().get("detail", "").lower()

    # Secret-only form for revoked device also gets 403
    resp = client.get(
        "/gateway/info",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert resp.status_code == 403


def test_gateway_control_routes_accept_valid_paired_device(tmp_path):
    from fastapi.testclient import TestClient

    gateway = BrahmaGateway(tmp_path, BrahmaGatewayConfig(enabled=True))
    client = TestClient(gateway.app)

    record, secret = gateway.device_manager.create_from_pairing(
        name="Pixel 9",
        platform="android",
        capabilities=["camera"],
    )

    # 1. Bearer device_id:secret
    resp = client.get(
        "/gateway/info",
        headers={"Authorization": f"Bearer {record.device_id}:{secret}"},
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True

    # 2. Bearer secret-only
    resp = client.get(
        "/gateway/devices",
        headers={"Authorization": f"Bearer {secret}"},
    )
    assert resp.status_code == 200
    assert len(resp.json().get("devices", [])) == 1

    # 3. Headers X-Device-Id + X-Device-Secret
    resp = client.get(
        "/gateway/logs",
        headers={"X-Device-Id": record.device_id, "X-Device-Secret": secret},
    )
    assert resp.status_code == 200
    assert resp.json().get("ok") is True
