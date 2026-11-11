"""Tests for Peer Management UI Widget (ADR-037 & Phase S4, core/ui_peer.py)."""

from __future__ import annotations

from pathlib import Path
import pytest
from PyQt6.QtWidgets import QApplication

from core.peer.artifacts import PeerArtifactStore
from core.peer.discovery import DiscoveredPeer
from core.peer.identity import NodeIdentity
from core.peer.pairing import PeerRegistry, PinnedPeer
from core.peer.scheduler import ReservationLedger
from core.peer.service import PeerService
from core.peer.store import PeerTaskStore
from core.ui_peer import PeerManagementWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_peer_management_widget_init_and_refresh(tmp_path: Path, qapp):
    """Verify PeerManagementWidget layout, data population, and drain mode toggle."""
    ident = NodeIdentity.create_new(machine_guid="guid-ui-1", hostname="host-ui-1")
    reg = PeerRegistry(tmp_path / "peers.json")
    ledger = ReservationLedger(global_max=4, per_node_max=2)
    art_store = PeerArtifactStore(tmp_path / "artifacts")
    task_store = PeerTaskStore(tmp_path / "tasks.db")

    service = PeerService(
        identity=ident,
        registry=reg,
        ledger=ledger,
        artifact_store=art_store,
        task_store=task_store,
        endpoint="https://127.0.0.1:8443",
    )

    # Add a pinned peer
    reg.save_peer(
        PinnedPeer(
            node_id="node-worker-alpha",
            name="Alpha Node",
            endpoint="https://10.0.0.10:8443",
            spki_sha256="cc" * 32,
            token="tok-alpha",
            profile="worker",
            paired_at="2026-01-01T00:00:00Z",
            static_pub_raw="dGVzdA==",
        )
    )

    widget = PeerManagementWidget(peer_service=service)

    # 1. Verify Node Card text
    assert ident.node_id in widget.lbl_node_id.text()
    assert ident.spki_sha256 in widget.lbl_fingerprint.text()
    assert "ACTIVE" in widget.lbl_status.text()
    assert widget.drain_btn.text() == "Drain Mode: OFF"

    # 2. Pinned peers list populated
    assert widget.pinned_container.count() == 1

    # 3. Toggle Drain Mode
    widget.drain_btn.click()
    assert service.drain_mode is True
    assert widget.drain_btn.text() == "Drain Mode: ON"
    assert "DRAINING" in widget.lbl_status.text()

    # Toggle back
    widget.drain_btn.click()
    assert service.drain_mode is False
    assert widget.drain_btn.text() == "Drain Mode: OFF"
    assert "ACTIVE" in widget.lbl_status.text()

    # 4. Discovered peer card creation
    discovered_peer = DiscoveredPeer(
        node_id="node-lan-beta",
        name="Beta Rig",
        endpoint="https://192.168.1.15:8443",
        spki_sha256="dd" * 32,
        address="192.168.1.15",
        last_seen=12345.0,
    )
    card = widget._create_discovered_peer_card(discovered_peer.to_dict())
    assert card is not None

    widget.close()
    service.close()
