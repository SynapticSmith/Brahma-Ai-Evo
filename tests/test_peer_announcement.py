"""Announced-endpoint tests: a wildcard bind must never be advertised (S-07)."""

from __future__ import annotations

import json
from pathlib import Path

from api_support import build_client, make_config
from brahma_api.config import BrahmaApiConfig
from core.peer.endpoint import (
    announced_host,
    format_host,
    is_wildcard_host,
    normalize_endpoint,
)


def _write_config(base: Path, payload: dict) -> None:
    config_dir = base / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "brahma_api.json").write_text(json.dumps(payload), encoding="utf-8")


def test_wildcard_detection():
    for host in ("0.0.0.0", "::", "[::]", "", "*", None):
        assert is_wildcard_host(host), host
    for host in ("127.0.0.1", "::1", "192.168.1.20", "node.local"):
        assert not is_wildcard_host(host), host


def test_config_wildcard_bind_announces_configured_host(tmp_path: Path):
    _write_config(
        tmp_path,
        {
            "host": "0.0.0.0",
            "allow_remote_bind": True,
            "peer_tls": True,
            "announced_host": "10.1.2.3",
        },
    )
    config = BrahmaApiConfig.load(tmp_path)
    assert config.host == "0.0.0.0"
    assert config.advertised_host == "10.1.2.3"
    assert config.peer_endpoint == "https://10.1.2.3:8443"


def test_config_wildcard_bind_without_override_is_not_advertised(tmp_path: Path):
    _write_config(tmp_path, {"host": "0.0.0.0", "allow_remote_bind": True})
    config = BrahmaApiConfig.load(tmp_path)
    assert config.host == "0.0.0.0"
    assert config.advertised_host != "0.0.0.0"
    assert "0.0.0.0" not in config.peer_endpoint


def test_config_rejects_wildcard_announced_host(tmp_path: Path):
    _write_config(
        tmp_path,
        {"host": "0.0.0.0", "allow_remote_bind": True, "announced_host": "0.0.0.0"},
    )
    config = BrahmaApiConfig.load(tmp_path)
    assert config.announced_host is None
    assert any("announced_host" in p for p in config.problems)
    assert "0.0.0.0" not in config.peer_endpoint


def test_loopback_bind_is_announced_unchanged(tmp_path: Path):
    _write_config(tmp_path, {"host": "127.0.0.1", "peer_tls": False})
    config = BrahmaApiConfig.load(tmp_path)
    assert config.peer_endpoint == "http://127.0.0.1:8443"


def test_normalize_endpoint_only_rewrites_wildcard_authority(monkeypatch):
    monkeypatch.setattr("core.peer.endpoint.primary_lan_ip", lambda: "192.168.0.42")
    assert announced_host("0.0.0.0") == "192.168.0.42"
    assert announced_host("10.0.0.5") == "10.0.0.5"
    assert announced_host("0.0.0.0", "10.9.9.9") == "10.9.9.9"
    assert format_host("fe80::1") == "[fe80::1]"
    assert (
        normalize_endpoint("https://0.0.0.0:8443/v1/peer?x=1")
        == "https://192.168.0.42:8443/v1/peer?x=1"
    )
    assert (
        normalize_endpoint("https://10.0.0.5:8443/v1/peer")
        == "https://10.0.0.5:8443/v1/peer"
    )


def test_a2a_card_announces_dialable_endpoint(tmp_path: Path):
    config = make_config(
        tmp_path,
        host="0.0.0.0",
        allow_remote_bind=True,
        peer_tls=True,
        announced_host="10.1.2.3",
    )
    client = build_client(config)
    card = client.get("/.well-known/agent-card.json").json()
    assert card["url"] == "https://10.1.2.3:8443"
