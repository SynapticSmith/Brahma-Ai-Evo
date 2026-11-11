"""Stdlib-only unit tests for BTE-1 models, state transitions, recursion guards, and SQLite store.

Collected under CI tests/test_agent_*.py.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import pytest

from core.peer.model import (
    BrahmaTaskRequest,
    canonical_json_bytes,
    canonical_request_hash,
)
from core.peer.recursion import (
    RecursionDeniedError,
    check_recursion,
    filter_leaf_tools,
)
from core.peer.state import (
    TERMINAL_STATES,
    WireState,
    is_terminal,
    validate_transition,
)
from core.peer.store import (
    IdempotencyConflictError,
    PeerTaskStore,
)


def test_bte_request_validation():
    req = BrahmaTaskRequest(
        task_id="task-123",
        trace_id="4bf9c1a27d3e4f508192a3b4c5d6e7f8",
        root_task_id="task-123",
        origin_node="node-a",
        coordinator_node="node-a",
        prompt="Review codebase",
        effect="none",
        profile="reader",
        workspace="brahma",
    )
    req.validate()
    d = req.to_dict()
    assert d["task_id"] == "task-123"
    assert d["effect"] == "none"
    assert d["workspace"] == "brahma"

    # Invalid workspace traversal
    with pytest.raises(ValueError, match="workspace must be a simple alias"):
        BrahmaTaskRequest(
            task_id="task-bad",
            trace_id="trace",
            root_task_id="task-bad",
            origin_node="node-a",
            coordinator_node="node-a",
            prompt="foo",
            workspace="../secrets",
        ).validate()

    # Invalid effect
    with pytest.raises(ValueError, match="effect must be one of"):
        BrahmaTaskRequest(
            task_id="task-bad",
            trace_id="trace",
            root_task_id="task-bad",
            origin_node="node-a",
            coordinator_node="node-a",
            prompt="foo",
            effect="unsafe_mutation",
        ).validate()


def test_canonical_json_and_hashing():
    d1 = {"b": 2, "a": 1, "nested": {"z": 9, "y": 8}}
    d2 = {"a": 1, "nested": {"y": 8, "z": 9}, "b": 2}
    assert canonical_json_bytes(d1) == canonical_json_bytes(d2)

    h1 = canonical_request_hash(d1)
    h2 = canonical_request_hash(d2)
    assert h1 == h2
    assert len(h1) == 64


def test_wire_state_machine_transitions():
    assert validate_transition("", WireState.SUBMITTED.value) is True
    assert validate_transition(WireState.SUBMITTED.value, WireState.WORKING.value) is True
    assert validate_transition(WireState.WORKING.value, WireState.COMPLETED.value) is True
    assert validate_transition(WireState.WORKING.value, WireState.FAILED.value) is True
    assert validate_transition(WireState.WORKING.value, WireState.CANCELED.value) is True

    # Terminal immutability: terminal states cannot transition to anything
    for t in TERMINAL_STATES:
        assert is_terminal(t) is True
        assert validate_transition(t, WireState.WORKING.value) is False
        assert validate_transition(t, WireState.COMPLETED.value) is False
        assert validate_transition(t, WireState.FAILED.value) is False


def test_recursion_and_origin_guards():
    req = BrahmaTaskRequest(
        task_id="t1",
        trace_id="tr1",
        root_task_id="t1",
        origin_node="node-a",
        coordinator_node="node-a",
        prompt="foo",
        hop=0,
    )

    # Node A cannot send a task to Node A
    with pytest.raises(RecursionDeniedError, match="task originated from self"):
        check_recursion(req, self_node_id="node-a", max_hops=0)

    # Node B accepts hop=0 from Node A
    check_recursion(req, self_node_id="node-b", max_hops=0)

    # Hop > max_hops rejected
    req_hop1 = BrahmaTaskRequest(
        task_id="t2",
        trace_id="tr2",
        root_task_id="t2",
        origin_node="node-a",
        coordinator_node="node-a",
        prompt="foo",
        hop=1,
    )
    with pytest.raises(RecursionDeniedError, match="hop count 1 exceeds limit 0"):
        check_recursion(req_hop1, self_node_id="node-b", max_hops=0)

    # Trace history cycle rejection
    req_cycle = BrahmaTaskRequest(
        task_id="t3",
        trace_id="tr3",
        root_task_id="t3",
        origin_node="node-a",
        coordinator_node="node-a",
        prompt="foo",
        hop=0,
        labels={"trace_nodes": ["node-a", "node-b", "node-c"]},
    )
    with pytest.raises(RecursionDeniedError, match="already present in trace history"):
        check_recursion(req_cycle, self_node_id="node-b", max_hops=1)


def test_filter_leaf_tools():
    class DummyTool:
        def __init__(self, name: str, peer_id: str | None = None):
            self.name = name
            self.peer_id = peer_id

    tools = [
        DummyTool("read_file"),
        DummyTool("grep_files"),
        DummyTool("run_subagent"),
        DummyTool("run_subagent_batch"),
        DummyTool("agent_node_c_run_task", peer_id="node-c"),
        DummyTool("agent_node_c_query_status"),
        DummyTool("agent_node_c_cancel_task"),
    ]

    filtered = filter_leaf_tools(tools)
    names = [t.name for t in filtered]
    assert names == ["read_file", "grep_files"]


def test_sqlite_peer_task_store_idempotency_and_leases():
    with tempfile.TemporaryDirectory() as td:
        db_file = Path(td) / "tasks.db"
        store = PeerTaskStore(db_file)

        req1 = BrahmaTaskRequest(
            task_id="0192f4c1-7a2b-7c3d-9e4f-1a2b3c4d5e6f",
            trace_id="4bf9c1a27d3e4f508192a3b4c5d6e7f8",
            root_task_id="0192f4c1-7a2b-7c3d-9e4f-1a2b3c4d5e6f",
            origin_node="node-a",
            coordinator_node="node-a",
            prompt="Analyze logs",
            effect="none",
            profile="reader",
            workspace="brahma",
        )

        # 1. First record submission
        is_existing, row = store.record_task(req1, peer_node="node-b")
        assert is_existing is False
        assert row["state"] == WireState.SUBMITTED.value
        assert row["task_id"] == req1.task_id

        # 2. Duplicate submission with same body -> returns existing (Invariant S-04)
        is_existing, row2 = store.record_task(req1, peer_node="node-b")
        assert is_existing is True
        assert row2["task_id"] == req1.task_id

        # 3. Duplicate submission with different body -> raises IdempotencyConflictError
        req1_modified = BrahmaTaskRequest(
            task_id="0192f4c1-7a2b-7c3d-9e4f-1a2b3c4d5e6f",
            trace_id="4bf9c1a27d3e4f508192a3b4c5d6e7f8",
            root_task_id="0192f4c1-7a2b-7c3d-9e4f-1a2b3c4d5e6f",
            origin_node="node-a",
            coordinator_node="node-a",
            prompt="A completely different prompt!",
            effect="none",
            profile="reader",
            workspace="brahma",
        )
        with pytest.raises(IdempotencyConflictError, match="idempotency_conflict"):
            store.record_task(req1_modified, peer_node="node-b")

        # 4. State updates & leases
        ok = store.update_state("node-a", req1.task_id, WireState.WORKING.value, lease_ttl_s=30.0)
        assert ok is True
        t = store.get_task("node-a", req1.task_id)
        assert t["state"] == WireState.WORKING.value
        assert t["lease_expires_at"] is not None

        # Heartbeat extension
        assert store.heartbeat("node-a", req1.task_id, lease_ttl_s=60.0) is True

        # Complete task
        res_json = json.dumps({"text": "done"})
        ok = store.update_state("node-a", req1.task_id, WireState.COMPLETED.value, result_json=res_json)
        assert ok is True
        t = store.get_task("node-a", req1.task_id)
        assert t["state"] == WireState.COMPLETED.value
        assert t["finished_at"] is not None
        assert t["result_json"] == res_json

        # Terminal state cannot be overwritten
        assert store.update_state("node-a", req1.task_id, WireState.FAILED.value) is False
        t_after = store.get_task("node-a", req1.task_id)
        assert t_after["state"] == WireState.COMPLETED.value
