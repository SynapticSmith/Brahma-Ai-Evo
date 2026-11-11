"""Recursion, cycle guards, and leaf execution filters (ADR-031, Invariant S-08)."""

from __future__ import annotations

from typing import Any, Iterable, List, Optional, Set
from core.peer.model import BrahmaTaskRequest


class RecursionDeniedError(ValueError):
    """Raised when a task violates recursion, hop, or origin invariants."""


def check_recursion(
    request: BrahmaTaskRequest,
    self_node_id: str,
    *,
    max_hops: int = 0,
    allowed_relays: Optional[Set[str]] = None,
) -> None:
    """Validate task against recursion, origin, and trace cycle invariants.

    In v1:
    - coordinator originates at hop=0.
    - callee worker accepts hop <= max_hops (default max_hops=0 on worker, meaning leaf).
    - origin_node cannot be self_node_id.
    - self_node_id cannot appear in visited nodes history.
    """
    clean_self = (self_node_id or "").strip().lower()

    # 1. Origin check: A node cannot receive a task it originated (A -> ... -> A)
    if request.origin_node.strip().lower() == clean_self:
        raise RecursionDeniedError(
            f"recursion_denied: task originated from self ({request.origin_node})"
        )

    # 2. Hop limit check (workers cannot originate hops)
    if request.hop > max_hops:
        if not (allowed_relays and request.origin_node in allowed_relays):
            raise RecursionDeniedError(
                f"recursion_denied: hop count {request.hop} exceeds limit {max_hops}"
            )

    # 3. Trace cycle check: inspect trace labels / context for visited nodes
    visited: List[str] = []
    if isinstance(request.labels, dict) and "trace_nodes" in request.labels:
        nodes = request.labels.get("trace_nodes")
        if isinstance(nodes, (list, tuple)):
            visited.extend(str(n).strip().lower() for n in nodes)

    if clean_self in visited:
        raise RecursionDeniedError(
            f"recursion_denied: node {clean_self} already present in trace history {visited}"
        )


def is_peer_tool(tool: Any) -> bool:
    """Check if a tool is a peer-call tool."""
    # Check ToolSpec peer_id attribute
    if getattr(tool, "peer_id", None):
        return True
    name = getattr(tool, "name", "")
    if isinstance(tool, dict):
        name = tool.get("name", "")
        if tool.get("peer_id"):
            return True
    # Bridge-published peer tools follow agent_<node_id>_<verb>
    if name.startswith("agent_") and any(name.endswith(v) for v in ("_run_task", "_query_status", "_cancel_task")):
        return True
    return False


def is_subagent_dispatch_tool(tool: Any) -> bool:
    """Check if a tool is a subagent dispatch tool."""
    name = getattr(tool, "name", "")
    if isinstance(tool, dict):
        name = tool.get("name", "")
    return name in ("run_subagent", "run_subagent_batch", "list_subagents")


def filter_leaf_tools(tools: Iterable[Any]) -> List[Any]:
    """Filter out all peer tools and subagent dispatch tools (Invariant S-02).

    Ensures that a worker's AgentLoop is strictly a leaf: it can perform local
    work within its policy, but cannot fan out or recurse to other peers or subagents.
    """
    filtered = []
    for t in tools:
        if is_peer_tool(t) or is_subagent_dispatch_tool(t):
            continue
        filtered.append(t)
    return filtered
