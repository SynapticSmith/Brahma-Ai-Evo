"""Peer Task Scheduler, Concurrency Bounds, and Verifier Matrix (ADR-036 & Deep Dive §3.5, §8.2).

Enforces concurrency limits across peer nodes and provides verifier peer task creation.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, Optional, Set

from core.peer.model import BrahmaTaskRequest, BrahmaTaskResult, new_task_id

logger = logging.getLogger("brahma.peer.scheduler")

DEFAULT_GLOBAL_MAX_PEER_CONCURRENCY = 4
DEFAULT_PER_NODE_MAX_CONCURRENCY = 2


class ConcurrencyLimitExceededError(Exception):
    """Raised when peer concurrency limits are exceeded."""


class ReservationLedger:
    """Thread-safe ledger tracking in-flight peer tasks and node reservations."""

    def __init__(
        self,
        global_max: int = DEFAULT_GLOBAL_MAX_PEER_CONCURRENCY,
        per_node_max: int = DEFAULT_PER_NODE_MAX_CONCURRENCY,
    ) -> None:
        self.global_max = max(1, global_max)
        self.per_node_max = max(1, per_node_max)
        self._lock = threading.Lock()
        self._node_tasks: Dict[str, Set[str]] = {}
        self._task_node_map: Dict[str, str] = {}

    def acquire(self, node_id: str, task_id: str) -> None:
        """Reserve execution slot on a node. Raises ConcurrencyLimitExceededError if full."""
        node_clean = node_id.strip().lower()
        with self._lock:
            # Check global concurrency bound
            total_active = len(self._task_node_map)
            if total_active >= self.global_max:
                raise ConcurrencyLimitExceededError(
                    f"Global peer task limit ({self.global_max}) reached."
                )

            # Check per-node concurrency bound
            current = self._node_tasks.setdefault(node_clean, set())
            if len(current) >= self.per_node_max:
                raise ConcurrencyLimitExceededError(
                    f"Peer node '{node_clean}' concurrency limit ({self.per_node_max}) reached."
                )

            current.add(task_id)
            self._task_node_map[task_id] = node_clean

    def release(self, task_id: str) -> None:
        """Release reservation for a task."""
        with self._lock:
            node_id = self._task_node_map.pop(task_id, None)
            if node_id and node_id in self._node_tasks:
                self._node_tasks[node_id].discard(task_id)
                if not self._node_tasks[node_id]:
                    del self._node_tasks[node_id]

    def active_tasks_for_node(self, node_id: str) -> int:
        node_clean = node_id.strip().lower()
        with self._lock:
            return len(self._node_tasks.get(node_clean, set()))

    def total_active_tasks(self) -> int:
        with self._lock:
            return len(self._task_node_map)

    def is_task_in_flight(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._task_node_map


def build_verifier_task(
    original_task: BrahmaTaskRequest,
    original_result: BrahmaTaskResult,
    verifier_node: str,
    *,
    coordinator_node: Optional[str] = None,
) -> BrahmaTaskRequest:
    """Construct a read-only verifier task checking a worker's findings against workspace evidence.
    
    Implements Idea I-08 (Verifier Peer):
    - Effect is strictly 'none' (read-only).
    - Profile is strictly 'reader'.
    - Untrusted delimiter isolates original worker output.
    """
    coord = coordinator_node or original_task.coordinator_node
    v_task_id = f"verify-{new_task_id()[:8]}"
    
    raw_result_text = ""
    if original_result.result:
        raw_result_text = str(original_result.result.get("text", ""))

    verification_prompt = (
        f"You are a designated verifier node performing an independent cross-check of a peer's task outcome.\n\n"
        f"ORIGINAL TASK PROMPT:\n"
        f"{original_task.prompt}\n\n"
        f"--- BEGIN UNTRUSTED PEER RESULT (Node: {original_task.origin_node}) ---\n"
        f"{raw_result_text}\n"
        f"--- END UNTRUSTED PEER RESULT ---\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Check the claims made above against the code and files in workspace '{original_task.workspace}'.\n"
        f"2. Cite exact file paths and line numbers as evidence.\n"
        f"3. Return a verification report stating whether the result is CONFIRMED, DISPUTED, or INCOMPLETE."
    )

    return BrahmaTaskRequest(
        task_id=v_task_id,
        trace_id=original_task.trace_id,
        root_task_id=original_task.root_task_id or original_task.task_id,
        parent_task_id=original_task.task_id,
        origin_node=coord,
        coordinator_node=coord,
        workspace=original_task.workspace,
        prompt=verification_prompt,
        effect="none",
        profile="reader",
        deadline_s=min(original_task.deadline_s, 180.0),
        labels={"purpose": "verification", "original_node": original_task.origin_node},
    )
