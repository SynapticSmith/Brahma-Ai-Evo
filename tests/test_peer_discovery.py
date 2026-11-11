"""Tests for Peer Discovery Protocol (ADR-034 & Deep Dive §4.2)."""

from __future__ import annotations

import socket
import time

from core.peer.discovery import (
    DiscoveredPeer,
    PeerDiscoveryBeacon,
    PeerDiscoveryListener,
)


def _find_free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("", 0))
        return int(s.getsockname()[1])


def test_discovered_peer_serialization():
    """Verify serialization of discovered peer info."""
    peer = DiscoveredPeer(
        node_id="node-test-1",
        name="Test Worker",
        endpoint="https://192.168.1.50:8443",
        spki_sha256="aa" * 32,
        address="192.168.1.50",
        last_seen=12345.67,
    )
    d = peer.to_dict()
    assert d["node_id"] == "node-test-1"
    assert d["name"] == "Test Worker"
    assert d["endpoint"] == "https://192.168.1.50:8443"
    assert d["address"] == "192.168.1.50"


def test_discovery_beacon_and_listener_loopback():
    """Verify UDP broadcast beacon transmission and listener receipt over loopback."""
    port = _find_free_udp_port()
    listener = PeerDiscoveryListener(port=port)
    listener.start()

    try:
        beacon = PeerDiscoveryBeacon(
            node_id="node-beacon-1",
            name="Beacon Test Node",
            endpoint="https://127.0.0.1:8443",
            spki_sha256="bb" * 32,
            port=port,
            target_address="127.0.0.1",
        )

        # Allow listener loop to receive packet
        deadline = time.time() + 3.0
        found = False
        while time.time() < deadline:
            beacon.broadcast_once()
            discovered = listener.list_discovered()
            if any(p.node_id == "node-beacon-1" for p in discovered):
                found = True
                break
            time.sleep(0.05)

        assert found is True
        peers = listener.list_discovered()
        target = next(p for p in peers if p.node_id == "node-beacon-1")
        assert target.name == "Beacon Test Node"
        assert target.endpoint == "https://127.0.0.1:8443"
        assert target.spki_sha256 == "bb" * 32
    finally:
        listener.stop()


def test_discovery_listener_ignores_invalid_packet():
    """Verify listener filters out non-Brahma packets gracefully."""
    port = _find_free_udp_port()
    listener = PeerDiscoveryListener(port=port)
    listener.start()

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"random-non-json-garbage", ("127.0.0.1", port))
            s.sendto(b'{"magic": "other_magic", "node_id": "bad"}', ("127.0.0.1", port))

        time.sleep(0.1)
        assert len(listener.list_discovered()) == 0
    finally:
        listener.stop()


def test_discovery_listener_filters_out_self_node_id():
    """Verify listener filters out its own beacon broadcasts to prevent loopback duplicates."""
    port = _find_free_udp_port()
    listener = PeerDiscoveryListener(port=port, self_node_id="node-self-123")
    listener.start()

    try:
        # Broadcast self beacon
        self_beacon = PeerDiscoveryBeacon(
            node_id="node-self-123",
            name="Self Node",
            endpoint="https://127.0.0.1:8443",
            spki_sha256="cc" * 32,
            port=port,
            target_address="127.0.0.1",
        )
        other_beacon = PeerDiscoveryBeacon(
            node_id="node-other-456",
            name="Other Node",
            endpoint="https://127.0.0.1:8445",
            spki_sha256="dd" * 32,
            port=port,
            target_address="127.0.0.1",
        )
        deadline = time.time() + 3.0
        found_other = False
        while time.time() < deadline:
            self_beacon.broadcast_once()
            other_beacon.broadcast_once()
            discovered = listener.list_discovered()
            if any(p.node_id == "node-other-456" for p in discovered):
                found_other = True
                break
            time.sleep(0.05)

        assert found_other is True
        discovered = listener.list_discovered()
        node_ids = {p.node_id for p in discovered}
        assert "node-other-456" in node_ids
        assert "node-self-123" not in node_ids
    finally:
        listener.stop()
