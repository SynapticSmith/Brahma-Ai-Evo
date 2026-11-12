"""Tests for Peer Management UI Widget (ADR-037 & Phase S4, core/ui_peer.py)."""

from __future__ import annotations

from pathlib import Path
import time

import pytest
from PyQt6.QtWidgets import QApplication, QInputDialog, QMessageBox

from core.peer.artifacts import PeerArtifactStore
from core.peer.discovery import DiscoveredPeer
from core.peer.identity import NodeIdentity
from core.peer.pairing import PairingOffer, PeerRegistry, PinnedPeer
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
    from core.peer.service import set_active_peer_service

    set_active_peer_service(service)

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

    # 5. In-flight tasks container population and cancellation
    from core.peer.model import BrahmaTaskRequest
    req = BrahmaTaskRequest(
        task_id="task-ui-active-1",
        trace_id="tr-ui-1",
        root_task_id="task-ui-active-1",
        origin_node="node-remote",
        coordinator_node="node-remote",
        workspace="test",
        prompt="UI active task test",
        profile="reader",
    )
    task_store.record_task(req, peer_node=ident.node_id)
    task_store.update_state("node-remote", "task-ui-active-1", "working")
    widget.refresh_data()
    assert widget.tasks_container.count() == 1

    # Cancel task via UI action
    widget._cancel_task("node-remote", "task-ui-active-1")
    t_after = task_store.get_task("node-remote", "task-ui-active-1")
    assert t_after is not None
    assert t_after["cancel_requested_at"] is not None

    # 6. Embedding entrypoint picks up active service
    from core.ui_peer import build_peer_management_card
    from core.peer.service import get_active_peer_service

    assert get_active_peer_service() is service
    embedded = build_peer_management_card()
    assert embedded is not None
    assert embedded.service is service
    embedded.close()

    widget.close()
    service.close()


def test_lan_discovery_toggle_starts_and_stops_the_listener(tmp_path: Path, qapp):
    """The GUI starts brahma_api without discovery; this button is the only way in.

    Stubbed at the service boundary so the test never binds UDP 8444.
    """
    ident = NodeIdentity.create_new(machine_guid="guid-ui-disc", hostname="host-ui-disc")
    service = PeerService(
        identity=ident,
        registry=PeerRegistry(tmp_path / "peers_disc.json"),
        ledger=ReservationLedger(global_max=2, per_node_max=1),
        artifact_store=PeerArtifactStore(tmp_path / "artifacts_disc"),
        task_store=PeerTaskStore(tmp_path / "tasks_disc.db"),
        endpoint="https://127.0.0.1:8443",
    )
    from core.peer.service import set_active_peer_service

    set_active_peer_service(service)

    class _FakeListener:
        def list_discovered(self):
            return []

        def stop(self):
            pass

    def fake_start():
        service.listener = _FakeListener()

    def fake_stop():
        service.listener = None

    service.start_discovery = fake_start  # type: ignore[method-assign]
    service.stop_discovery = fake_stop  # type: ignore[method-assign]

    widget = PeerManagementWidget(peer_service=service)
    try:
        assert widget.discovery_btn.text() == "LAN Discovery: OFF"
        assert service.listener is None

        widget.discovery_btn.click()
        assert service.listener is not None
        assert widget.discovery_btn.text() == "LAN Discovery: ON"

        widget.discovery_btn.click()
        assert service.listener is None
        assert widget.discovery_btn.text() == "LAN Discovery: OFF"

        # The button reflects the listener, not the last click: a listener
        # stopped elsewhere must not leave a stale "ON".
        service.listener = _FakeListener()
        widget.refresh_data()
        assert widget.discovery_btn.text() == "LAN Discovery: ON"
    finally:
        widget.close()
        service.close()


def _make_service(
    tmp_path: Path,
    name: str,
    endpoint: str = "https://192.168.1.50:8443",
) -> PeerService:
    ident = NodeIdentity.create_new(machine_guid=f"guid-{name}", hostname=f"host-{name}")
    return PeerService(
        identity=ident,
        registry=PeerRegistry(tmp_path / f"peers_{name}.json"),
        ledger=ReservationLedger(global_max=2, per_node_max=1),
        artifact_store=PeerArtifactStore(tmp_path / f"artifacts_{name}"),
        task_store=PeerTaskStore(tmp_path / f"tasks_{name}.db"),
        endpoint=endpoint,
    )


def test_peer_card_adopts_a_service_that_starts_after_construction(
    tmp_path: Path, qapp, monkeypatch
):
    """The API thread registers its service after the Settings page is built.

    Caching the ``None`` seen at construction made every button a silent no-op
    for the life of the process; refresh_data must re-resolve.
    """
    from core.peer.service import set_active_peer_service

    set_active_peer_service(None)
    fallback_ident = NodeIdentity.create_new(
        machine_guid="guid-ui-late", hostname="host-ui-late"
    )
    monkeypatch.setattr(NodeIdentity, "load_or_create", staticmethod(lambda **_: fallback_ident))
    monkeypatch.setattr(
        "core.ui_peer.PeerRegistry",
        lambda: PeerRegistry(tmp_path / "fallback_peers.json"),
    )

    widget = PeerManagementWidget(peer_service=None)
    service = None
    try:
        assert widget.service is None
        assert "OFFLINE" in widget.lbl_status.text()

        service = _make_service(tmp_path, "late")
        set_active_peer_service(service)
        widget.refresh_data()

        assert widget.service is service
        assert service.identity.node_id in widget.lbl_node_id.text()
        assert "192.168.1.50" in widget.lbl_endpoint.text()

        widget.drain_btn.click()
        assert service.drain_mode is True
        assert widget.drain_btn.text() == "Drain Mode: ON"
    finally:
        widget.close()
        if service is not None:
            service.close()


def test_endpoint_text_flags_a_loopback_only_peer_port(qapp):
    assert "loopback only" in PeerManagementWidget._endpoint_text("https://127.0.0.1:8443")
    assert "loopback only" not in PeerManagementWidget._endpoint_text("https://192.168.1.50:8443")
    assert "not running" in PeerManagementWidget._endpoint_text("")


def test_pairing_endpoint_prefers_the_offer_over_a_loopback_card(qapp):
    offer = PairingOffer(
        node_id="node-b",
        spki_sha256="ab" * 32,
        host="192.168.1.20",
        port=8443,
        nonce="n-pair",
        created_at=time.time(),
    )
    assert PeerManagementWidget._pairing_endpoint(offer) == "https://192.168.1.20:8443"
    assert (
        PeerManagementWidget._pairing_endpoint(offer, {"endpoint": "https://10.0.0.9:8443"})
        == "https://10.0.0.9:8443"
    )
    # A discovered card that advertises loopback is not dialable; the offer wins.
    assert (
        PeerManagementWidget._pairing_endpoint(offer, {"endpoint": "https://127.0.0.1:8443"})
        == "https://192.168.1.20:8443"
    )


def test_pairing_endpoint_never_dials_a_wildcard(qapp, monkeypatch):
    monkeypatch.setattr("core.peer.endpoint.primary_lan_ip", lambda: "192.168.5.5")
    offer = PairingOffer(
        node_id="node-b",
        spki_sha256="ab" * 32,
        host="0.0.0.0",
        port=8443,
        nonce="n-wild",
        created_at=time.time(),
    )
    assert PeerManagementWidget._pairing_endpoint(offer) == "https://192.168.5.5:8443"


def test_local_offer_authority_uses_the_service_endpoint(tmp_path: Path, qapp):
    service = _make_service(tmp_path, "offer")
    try:
        assert PeerManagementWidget._local_offer_authority(service) == ("192.168.1.50", 8443)
    finally:
        service.close()


def test_pair_by_offer_refuses_a_loopback_offer_before_dialing(
    tmp_path: Path, qapp, monkeypatch
):
    from core.peer.service import set_active_peer_service

    service = _make_service(tmp_path, "loop")
    set_active_peer_service(service)
    widget = PeerManagementWidget(peer_service=service)
    offer = PairingOffer(
        node_id="node-remote",
        spki_sha256="ab" * 32,
        host="127.0.0.1",
        port=8443,
        nonce="n-loop",
        created_at=time.time(),
    )
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: (offer.to_uri(), True))
    )
    seen = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: seen.append(a))
    )
    try:
        widget._pair_by_offer()
        assert seen and seen[0][1] == "Offer Is Loopback-Only"
    finally:
        widget.close()
        service.close()


def test_pair_by_offer_refuses_an_offer_created_here(tmp_path: Path, qapp, monkeypatch):
    from core.peer.service import set_active_peer_service

    service = _make_service(tmp_path, "own")
    set_active_peer_service(service)
    widget = PeerManagementWidget(peer_service=service)
    offer = PairingOffer(
        node_id=service.identity.node_id,
        spki_sha256="ab" * 32,
        host="192.168.1.50",
        port=8443,
        nonce="n-own",
        created_at=time.time(),
    )
    monkeypatch.setattr(
        QInputDialog, "getText", staticmethod(lambda *a, **k: (offer.to_uri(), True))
    )
    seen = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: seen.append(a))
    )
    try:
        widget._pair_by_offer()
        assert seen and seen[0][1] == "Offer Belongs to This Node"
    finally:
        widget.close()
        service.close()
