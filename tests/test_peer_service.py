"""Tests for PeerService lifecycle, drain mode, and status reporting (ADR-037 & Deep Dive §8.4)."""

from __future__ import annotations

from pathlib import Path
import time

from api_support import auth, build_client, make_config, mint
from core.peer.artifacts import PeerArtifactStore
from core.peer.identity import NodeIdentity
from core.peer.model import BrahmaTaskRequest
from core.peer.pairing import PeerRegistry, PinnedPeer
from core.peer.scheduler import ReservationLedger
from core.peer.service import PeerService
from core.peer.store import PeerTaskStore


def test_peer_service_lifecycle_and_drain_mode(tmp_path: Path):
    """Verify PeerService drain mode toggle and structured summary generation."""
    ident = NodeIdentity.create_new(machine_guid="guid-svc-1", hostname="host-svc-1")
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

    try:
        # 1. Initial state
        assert service.drain_mode is False
        summary = service.get_status_summary()
        assert summary["node_id"] == ident.node_id
        assert summary["drain_mode"] is False
        assert summary["active_tasks"] == 0
        assert summary["pinned_peers_count"] == 0

        # 2. Add pinned peer
        reg.save_peer(
            PinnedPeer(
                node_id="node-worker-9",
                name="Worker 9",
                endpoint="https://10.0.0.9:8443",
                spki_sha256="aa" * 32,
                token="tok-worker-9",
                profile="worker",
                paired_at="2026-01-01T00:00:00Z",
                static_pub_raw="dGVzdA==",
            )
        )
        summary2 = service.get_status_summary()
        assert summary2["pinned_peers_count"] == 1
        assert summary2["pinned_peers"][0]["node_id"] == "node-worker-9"

        # 3. Toggle drain mode
        service.drain_mode = True
        assert service.drain_mode is True
        assert service.get_status_summary()["drain_mode"] is True

        service.drain_mode = False
        assert service.drain_mode is False
    finally:
        service.close()


def test_peer_service_drain_mode_http_enforcement(tmp_path: Path):
    """Verify that drain mode rejects new tasks with 503 while allowing status/cancel."""
    ident = NodeIdentity.create_new(machine_guid="guid-svc-2", hostname="host-svc-2")
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
    )

    config = make_config(tmp_path, workspaces={"test": str(tmp_path / "ws")})
    _r, tok = mint(config, "reader")
    time.sleep(1.05)

    client = build_client(
        config,
        peer_store=task_store,
        peer_identity=ident,
        peer_registry=reg,
        peer_artifact_store=art_store,
        peer_service=service,
    )

    # 1. Normal submission works
    req1 = BrahmaTaskRequest(
        task_id="task-drain-1",
        trace_id="tr-drain-1",
        root_task_id="task-drain-1",
        origin_node="node-client",
        coordinator_node="node-client",
        workspace="test",
        prompt="Initial task before drain",
        profile="reader",
    )
    r1 = client.post(
        "/tasks",
        headers={**auth(tok), "X-Brahma-Node": "node-client"},
        json=req1.to_dict(),
    )
    assert r1.status_code == 202

    # 2. Status check on /status returns service summary
    r_stat = client.get("/status", headers=auth(tok))
    assert r_stat.status_code == 200
    stat_json = r_stat.json()
    assert stat_json["node_id"] == ident.node_id
    assert stat_json["drain_mode"] is False

    # 3. Enable drain mode
    service.drain_mode = True

    # Status check confirms drain_mode is True
    r_stat2 = client.get("/status", headers=auth(tok))
    assert r_stat2.json()["drain_mode"] is True

    # 4. New task submission is rejected with 503 peer_draining
    req2 = BrahmaTaskRequest(
        task_id="task-drain-2",
        trace_id="tr-drain-2",
        root_task_id="task-drain-2",
        origin_node="node-client",
        coordinator_node="node-client",
        workspace="test",
        prompt="Rejected task during drain",
        profile="reader",
    )
    r2 = client.post(
        "/tasks",
        headers={**auth(tok), "X-Brahma-Node": "node-client"},
        json=req2.to_dict(),
    )
    assert r2.status_code == 503
    err_json = r2.json()
    assert err_json["error"]["code"] == "peer_draining"

    # 5. Existing task status queries still succeed (Invariant S-04 / ADR-037)
    r_query = client.get(
        "/tasks/task-drain-1",
        headers={**auth(tok), "X-Brahma-Node": "node-client"},
    )
    assert r_query.status_code == 200
    assert r_query.json()["task_id"] == "task-drain-1"

    # 6. Existing task cancellation still succeeds
    r_cancel = client.post(
        "/tasks/task-drain-1/cancel",
        headers={**auth(tok), "X-Brahma-Node": "node-client"},
    )
    assert r_cancel.status_code == 200


def test_peer_service_pending_offers_pruning(tmp_path: Path):
    """Verify that expired pairing offers are pruned automatically."""
    from core.peer.pairing import PairingOffer
    ident = NodeIdentity.create_new(hostname="host-prune")
    service = PeerService(
        identity=ident,
        registry=PeerRegistry(tmp_path / "peers.json"),
        artifact_store=PeerArtifactStore(tmp_path / "artifacts"),
        task_store=PeerTaskStore(tmp_path / "tasks.db"),
    )
    try:
        fresh_offer = PairingOffer(
            v=1,
            node_id=ident.node_id,
            spki_sha256=ident.spki_sha256,
            host="127.0.0.1",
            port=8010,
            nonce="fresh_1",
            created_at=time.time(),
        )
        expired_offer = PairingOffer(
            v=1,
            node_id=ident.node_id,
            spki_sha256=ident.spki_sha256,
            host="127.0.0.1",
            port=8010,
            nonce="expired_1",
            created_at=time.time() - 300.0,
        )
        service.pending_offers["fresh_1"] = fresh_offer
        service.pending_offers["expired_1"] = expired_offer

        pruned = service.prune_pending_offers(max_age_s=120.0)
        assert pruned == 1
        assert "fresh_1" in service.pending_offers
        assert "expired_1" not in service.pending_offers
    finally:
        service.close()
