"""Gate tests for Invariant S-05 and S-07: Lease Expiry, UNKNOWN state, and Mutation Retry Guards."""

from __future__ import annotations

from pathlib import Path
import time

from api_support import CaptureCompleter, auth, build_client, make_config, mint
from core.peer.model import BrahmaTaskRequest
from core.peer.state import WireState, is_retry_permitted
from core.peer.store import PeerTaskStore


def test_lease_expiry_and_reconciliation_to_unknown(tmp_path: Path):
    """Invariant S-07: Lease expiry transitions working task to UNKNOWN, not failed."""
    db_file = tmp_path / "test_leases.db"
    store = PeerTaskStore(db_file)

    task = BrahmaTaskRequest(
        task_id="task-lease-1",
        trace_id="tr-1",
        root_task_id="task-lease-1",
        origin_node="node-a",
        coordinator_node="node-a",
        prompt="Simulate background work",
        effect="mutating",
    )
    store.record_task(task, peer_node="node-b")

    # Start task with very short lease TTL (0.1s)
    store.update_state("node-a", "task-lease-1", WireState.WORKING.value, started_at="2026-11-10T00:00:00Z", lease_ttl_s=0.1)

    row_working = store.get_task("node-a", "task-lease-1")
    assert row_working["state"] == WireState.WORKING.value
    assert row_working["lease_expires_at"] is not None

    # Wait for lease to expire
    time.sleep(0.2)

    # Reconcile expired leases
    reconciled = store.reconcile_expired_leases()
    assert len(reconciled) == 1
    assert reconciled[0]["task_id"] == "task-lease-1"
    assert reconciled[0]["state"] == WireState.UNKNOWN.value
    assert reconciled[0]["error_code"] == "lease_expired"

    # Subsequent fetch reflects UNKNOWN state
    row_updated = store.get_task("node-a", "task-lease-1")
    assert row_updated["state"] == WireState.UNKNOWN.value


def test_heartbeat_prevents_lease_expiry(tmp_path: Path):
    """Actively heartbeating task does not expire."""
    db_file = tmp_path / "test_leases.db"
    store = PeerTaskStore(db_file)

    task = BrahmaTaskRequest(
        task_id="task-lease-2",
        trace_id="tr-2",
        root_task_id="task-lease-2",
        origin_node="node-a",
        coordinator_node="node-a",
        prompt="Simulate long job",
    )
    store.record_task(task, peer_node="node-b")

    # Start task with 0.3s lease
    store.update_state("node-a", "task-lease-2", WireState.WORKING.value, started_at="2026-11-10T00:00:00Z", lease_ttl_s=0.3)

    # Heartbeat at 0.1s
    time.sleep(0.1)
    ok = store.heartbeat("node-a", "task-lease-2", lease_ttl_s=0.5)
    assert ok is True

    # At 0.2s from start, original lease would have been close, but heartbeat extended it
    time.sleep(0.1)
    reconciled = store.reconcile_expired_leases()
    assert len(reconciled) == 0

    row = store.get_task("node-a", "task-lease-2")
    assert row["state"] == WireState.WORKING.value


def test_s05_and_s07_mutation_retry_rules():
    """Invariant S-05: Mutating tasks cannot be retried once started or in UNKNOWN state."""
    # 1. Read-only (effect='none') can always be retried from unknown or failed
    assert is_retry_permitted(effect="none", state=WireState.UNKNOWN.value, started_at="2026-11-10T00:00:00Z") is True
    assert is_retry_permitted(effect="idempotent", state=WireState.UNKNOWN.value, started_at="2026-11-10T00:00:00Z") is True

    # 2. Mutating task in UNKNOWN state (lease expired) MUST NOT be retried
    assert is_retry_permitted(effect="mutating", state=WireState.UNKNOWN.value, started_at="2026-11-10T00:00:00Z") is False

    # 3. Mutating task in WORKING state MUST NOT be retried
    assert is_retry_permitted(effect="mutating", state=WireState.WORKING.value, started_at="2026-11-10T00:00:00Z") is False

    # 4. Mutating task in SUBMITTED state that never started CAN be retried safely
    assert is_retry_permitted(effect="mutating", state=WireState.SUBMITTED.value, started_at=None) is True


def test_server_status_query_triggers_lease_reconciliation(tmp_path: Path):
    """Server GET /tasks/{task_id} reconciles expired leases and surfaces WireState.UNKNOWN."""
    db_file = tmp_path / "server_leases.db"
    store = PeerTaskStore(db_file)

    config = make_config(
        tmp_path,
        node_name="Workshop PC",
        workspaces={"test": str(tmp_path / "workspace")},
    )
    _rec, reader_token = mint(config, "reader")
    completer = CaptureCompleter("Test reply")
    client = build_client(config, factory=lambda *_args, **_kwargs: completer, peer_store=store)

    task = BrahmaTaskRequest(
        task_id="task-server-lease",
        trace_id="tr-lease",
        root_task_id="task-server-lease",
        origin_node="node-a",
        coordinator_node="node-a",
        workspace="test",
        prompt="Mutating command",
        effect="mutating",
    )
    store.record_task(task, peer_node="workshop")
    store.update_state("node-a", "task-server-lease", WireState.WORKING.value, started_at="2026-11-10T00:00:00Z", lease_ttl_s=0.1)

    time.sleep(0.2)

    # Status query with X-Brahma-Node: node-a
    resp = client.get(
        "/tasks/task-server-lease",
        headers={**auth(reader_token), "X-Brahma-Node": "node-a"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["state"] == WireState.UNKNOWN.value
    assert data["error"]["code"] == "lease_expired"
