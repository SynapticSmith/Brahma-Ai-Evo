"""Tests for Peer Capability Scheduling, Reservation Ledger, and Verifier Matrix (ADR-035, ADR-036)."""

from __future__ import annotations

import pytest

from core.peer.capabilities import (
    PeerCapabilityCard,
    matches_requirements,
    score_peer,
    select_best_peer,
)
from core.peer.model import BrahmaTaskRequest, BrahmaTaskResult
from core.peer.scheduler import (
    ConcurrencyLimitExceededError,
    ReservationLedger,
    build_verifier_task,
)


def test_peer_capability_card_and_matching():
    """Verify capability card filtering against hard task requirements."""
    card = PeerCapabilityCard(
        node_id="node-gpu",
        name="GPU Rig",
        profiles=["reader", "worker"],
        workspaces=["brahma", "cuda-project"],
        labels={"gpu:present", "os:windows", "vram:24gb"},
        verbs=["run_task", "query_status", "cancel_task", "fetch_artifact"],
        active_tasks=0,
        max_concurrent_tasks=2,
    )

    # 1. Matches when all requirements are satisfied
    assert matches_requirements(
        card,
        required_profile="reader",
        required_workspace="brahma",
        required_labels=["gpu:present"],
        required_verbs=["run_task"],
    ) is True

    # 2. Rejects if node lacks required label
    assert matches_requirements(
        card,
        required_labels=["browser:playwright"],
    ) is False

    # 3. Rejects if node lacks workspace alias
    assert matches_requirements(
        card,
        required_workspace="secret-repo",
    ) is False

    # 4. Rejects if node profile ceiling is insufficient
    assert matches_requirements(
        card,
        required_profile="operator",
    ) is False

    # 5. Rejects when node capacity is exhausted
    full_card = PeerCapabilityCard(
        node_id="node-full",
        active_tasks=2,
        max_concurrent_tasks=2,
    )
    assert matches_requirements(full_card) is False


def test_peer_advisory_scoring_and_selection():
    """Verify advisory placement ranking and human operator override."""
    node_a = PeerCapabilityCard(
        node_id="node-a",
        name="Worker Alpha",
        active_tasks=1,
        max_concurrent_tasks=2,
        labels={"tool:compiler"},
        workspaces=["brahma"],
    )
    node_b = PeerCapabilityCard(
        node_id="node-b",
        name="Worker Beta",
        active_tasks=0,
        max_concurrent_tasks=2,
        labels={"tool:compiler", "gpu:present"},
        workspaces=["brahma"],
    )

    # Node B has 0 active tasks and extra GPU label -> higher score
    assert score_peer(node_b, preferred_labels=["gpu:present"]) > score_peer(node_a, preferred_labels=["gpu:present"])
    best = select_best_peer(
        [node_a, node_b],
        required_workspace="brahma",
        preferred_labels=["gpu:present"],
    )
    assert best is not None
    assert best.node_id == "node-b"

    # Operator pin overrides score (Idea I-02)
    pinned = select_best_peer(
        [node_a, node_b],
        required_workspace="brahma",
        preferred_node="node-a",
    )
    assert pinned is not None
    assert pinned.node_id == "node-a"


def test_reservation_ledger_concurrency_bounds():
    """Verify global and per-node concurrency limits in reservation ledger."""
    ledger = ReservationLedger(global_max=3, per_node_max=2)

    # 1. Acquire tasks on node-a up to limit
    ledger.acquire("node-a", "t-1")
    ledger.acquire("node-a", "t-2")
    assert ledger.active_tasks_for_node("node-a") == 2
    assert ledger.total_active_tasks() == 2

    # 2. Third task on node-a rejected (per-node bound)
    with pytest.raises(ConcurrencyLimitExceededError, match="concurrency limit"):
        ledger.acquire("node-a", "t-3")

    # 3. Third task allowed on node-b (global bound = 3)
    ledger.acquire("node-b", "t-3")
    assert ledger.total_active_tasks() == 3

    # 4. Fourth task rejected globally
    with pytest.raises(ConcurrencyLimitExceededError, match="Global peer task limit"):
        ledger.acquire("node-c", "t-4")

    # 5. Release slots and verify reuse
    ledger.release("t-1")
    assert ledger.active_tasks_for_node("node-a") == 1
    assert ledger.total_active_tasks() == 2

    # Now node-a can take a new task
    ledger.acquire("node-a", "t-4")
    assert ledger.active_tasks_for_node("node-a") == 2


def test_build_verifier_task_matrix():
    """Verify construction of read-only verifier tasks with untrusted delimiter."""
    orig_req = BrahmaTaskRequest(
        task_id="t-orig-100",
        trace_id="tr-100",
        root_task_id="t-orig-100",
        origin_node="node-worker",
        coordinator_node="node-coordinator",
        workspace="brahma",
        prompt="Find memory leaks in buffer pool",
        effect="mutating",
        profile="worker",
    )
    orig_res = BrahmaTaskResult(
        task_id="t-orig-100",
        state="completed",
        result={"text": "Found memory leak in pool.py line 45; fixed by clearing dict."},
    )

    verifier_req = build_verifier_task(
        orig_req,
        orig_res,
        verifier_node="node-verifier",
    )

    # 1. Verifier properties: strictly read-only leaf
    assert verifier_req.effect == "none"
    assert verifier_req.profile == "reader"
    assert verifier_req.workspace == "brahma"
    assert verifier_req.parent_task_id == "t-orig-100"
    assert verifier_req.root_task_id == "t-orig-100"
    assert verifier_req.labels.get("purpose") == "verification"

    # 2. Untrusted delimiter wraps original output
    assert "--- BEGIN UNTRUSTED PEER RESULT" in verifier_req.prompt
    assert "Found memory leak in pool.py line 45" in verifier_req.prompt
    assert "--- END UNTRUSTED PEER RESULT ---" in verifier_req.prompt
    assert "Cite exact file paths and line numbers as evidence" in verifier_req.prompt
