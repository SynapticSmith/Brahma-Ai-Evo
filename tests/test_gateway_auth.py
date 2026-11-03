"""Tests for Gateway Auth, SAS Pairing, and Execute Protection (WS-8)."""

import time
import pytest
from fastapi.testclient import TestClient

from brahma_connect.gateway.server import BrahmaGateway, BrahmaGatewayConfig
from core.gateway_auth import (
    SCOPE_DEVICE_COMMAND,
    SCOPE_DEVICE_READ,
)


@pytest.fixture
def gateway_client(tmp_path):
    gateway = BrahmaGateway(tmp_path, BrahmaGatewayConfig(enabled=True))
    client = TestClient(gateway.app)
    return gateway, client


def test_gateway_pair_sas_flow_success(gateway_client):
    """Verifies 6-digit SAS pairing request and verification flow."""
    gateway, client = gateway_client

    # 1. Start pairing
    start_resp = client.post(
        "/gateway/pair/start",
        json={"device_name": "Galaxy S24", "platform": "android"},
    )
    assert start_resp.status_code == 200
    data = start_resp.json()
    assert data.get("ok") is True
    pending_id = data.get("pending_id")
    code = data.get("pairing_code")

    assert len(code) == 6
    assert code.isdigit()

    # 2. Verify with correct 6-digit code
    verify_resp = client.post(
        "/gateway/pair/verify",
        json={"pending_id": pending_id, "pairing_code": code},
    )
    assert verify_resp.status_code == 200
    verify_data = verify_resp.json()
    assert verify_data.get("ok") is True
    device_token = verify_data.get("device_token")
    assert device_token is not None

    # 3. Access authenticated route with the new bearer token
    info_resp = client.get(
        "/gateway/info",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert info_resp.status_code == 200
    assert info_resp.json().get("ok") is True


def test_gateway_pair_lockout_after_three_failures(gateway_client):
    """Verifies that 3 consecutive invalid pairing code attempts lock pairing out."""
    gateway, client = gateway_client

    start_resp = client.post(
        "/gateway/pair/start",
        json={"device_name": "Attacker Phone", "platform": "android"},
    )
    pending_id = start_resp.json().get("pending_id")

    # 1st failure
    r1 = client.post("/gateway/pair/verify", json={"pending_id": pending_id, "pairing_code": "000000"})
    assert r1.status_code == 400
    assert "2 attempt(s) remaining" in r1.json().get("error", "")

    # 2nd failure
    r2 = client.post("/gateway/pair/verify", json={"pending_id": pending_id, "pairing_code": "111111"})
    assert r2.status_code == 400
    assert "1 attempt(s) remaining" in r2.json().get("error", "")

    # 3rd failure -> Lockout triggered
    r3 = client.post("/gateway/pair/verify", json={"pending_id": pending_id, "pairing_code": "222222"})
    assert r3.status_code == 429
    assert "locked out" in r3.json().get("error", "").lower()

    # 4th attempt is locked out immediately
    r4 = client.post("/gateway/pair/start", json={"device_name": "Attacker Phone 2", "platform": "android"})
    assert r4.status_code == 429
    assert "locked" in r4.json().get("error", "").lower()


def test_gateway_execute_command_success(gateway_client):
    """Verifies authenticated execute command with timestamp and nonce."""
    gateway, client = gateway_client

    # Pair device
    record, secret = gateway.device_manager.create_from_pairing(
        name="Command Device",
        platform="android",
        permissions=[SCOPE_DEVICE_COMMAND, SCOPE_DEVICE_READ],
    )
    token = f"{record.device_id}:{secret}"

    payload = {
        "command": "device.ping",
        "args": {},
        "nonce": "unique_nonce_abc12345",
        "ts": time.time(),
    }

    resp = client.post(
        "/gateway/execute",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("ok") is True
    assert data.get("result", {}).get("status") == "pong"
    assert data.get("nonce") == "unique_nonce_abc12345"


def test_gateway_execute_rejects_unauthenticated(gateway_client):
    """Verifies /gateway/execute rejects unauthenticated requests with 401."""
    gateway, client = gateway_client

    payload = {
        "command": "device.ping",
        "args": {},
        "nonce": "nonce_no_auth_12345",
        "ts": time.time(),
    }
    resp = client.post("/gateway/execute", json=payload)
    assert resp.status_code == 401


def test_gateway_execute_replay_prevention(gateway_client):
    """Verifies duplicate nonces are rejected as replay attacks."""
    gateway, client = gateway_client

    record, secret = gateway.device_manager.create_from_pairing(
        name="Replay Test",
        platform="ios",
    )
    token = f"{record.device_id}:{secret}"

    payload = {
        "command": "system.status",
        "args": {},
        "nonce": "replayed_nonce_99999",
        "ts": time.time(),
    }

    # First attempt succeeds
    resp1 = client.post(
        "/gateway/execute",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp1.status_code == 200

    # Second attempt with same nonce fails
    resp2 = client.post(
        "/gateway/execute",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp2.status_code == 400
    assert "replay attack" in resp2.json().get("error", "").lower()


def test_gateway_execute_timestamp_skew_rejection(gateway_client):
    """Verifies timestamp skew > 60s is rejected."""
    gateway, client = gateway_client

    record, secret = gateway.device_manager.create_from_pairing(
        name="Skew Test",
        platform="ios",
    )
    token = f"{record.device_id}:{secret}"

    payload = {
        "command": "device.ping",
        "args": {},
        "nonce": "skewed_nonce_88888",
        "ts": time.time() - 120.0,  # 2 minutes in the past
    }

    resp = client.post(
        "/gateway/execute",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp.status_code == 400
    assert "skewed" in resp.json().get("error", "").lower()


def test_gateway_execute_unallowlisted_command_rejection(gateway_client):
    """Verifies commands not in the allowlist are rejected."""
    gateway, client = gateway_client

    record, secret = gateway.device_manager.create_from_pairing(
        name="Allowlist Test",
        platform="ios",
    )
    token = f"{record.device_id}:{secret}"

    payload = {
        "command": "system.rm_rf_everything",
        "args": {},
        "nonce": "dangerous_cmd_77777",
        "ts": time.time(),
    }

    resp = client.post(
        "/gateway/execute",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert resp.status_code == 400
    assert "not allowed" in resp.json().get("error", "").lower()
