"""Tests for Invariant S-03: Parallel Peer Fan-out and Fencing in agent/loop.py."""

from __future__ import annotations

from pathlib import Path

from agent.loop import _effects_conflict
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import ToolSpec, register as register_spec, unregister as unregister_spec


def test_effects_conflict_invariant_s03():
    """Verify S-03 conflict matrix for peer calls vs local tools and distinct nodes."""
    peer_a = ("peer", Path("//peer/nodea"))
    peer_b = ("peer", Path("//peer/nodeb"))
    peer_a_dup = ("peer", Path("//peer/nodea"))

    local_read = ("read", Path("C:/project/file.txt"))
    local_write = ("write", Path("C:/project/file.txt"))

    # 1. Distinct peer nodes do NOT conflict (can overlap/fan-out)
    assert _effects_conflict(peer_a, peer_b) is False
    assert _effects_conflict(peer_b, peer_a) is False

    # 2. Same peer node CONFLICTS (serialized per node)
    assert _effects_conflict(peer_a, peer_a_dup) is True

    # 3. Peer calls are strictly fenced from local tools
    assert _effects_conflict(peer_a, local_read) is True
    assert _effects_conflict(local_read, peer_a) is True
    assert _effects_conflict(peer_a, local_write) is True
    assert _effects_conflict(local_write, peer_a) is True


def test_call_effect_peer_read_vs_write(tmp_path: Path):
    """Verify _call_effect identifies read-only peer tools and fences mutating ones."""
    runtime = AgentRuntime(tmp_path, PermissionPolicy())

    # Register mock peer tools
    spec_read = ToolSpec(
        name="agent_worker1_query_status",
        description="Query worker status",
        parameters={"type": "object", "properties": {"task_id": {"type": "string"}}},
        tool_class="read",
        handler=lambda _ctx, _args: "status ok",
    )
    spec_write = ToolSpec(
        name="agent_worker1_run_task",
        description="Run task on worker",
        parameters={"type": "object", "properties": {"prompt": {"type": "string"}}},
        tool_class="write",
        handler=lambda _ctx, _args: "task started",
    )
    register_spec(spec_read)
    register_spec(spec_write)

    try:
        from agent.loop import AgentLoop

        class DummyCompleter:
            def complete(self, *_args, **_kwargs):
                return ""

        loop = AgentLoop(DummyCompleter(), runtime=runtime, max_parallel_tools=4)

        # 1. Read-only peer call produces ("peer", Path("//peer/worker1"))
        item_read = {
            "name": "agent_worker1_query_status",
            "args": {"task_id": "123"},
            "attempts": 1,
        }
        effect_read = loop._call_effect(item_read)
        assert effect_read is not None
        assert effect_read[0] == "peer"
        assert "worker1" in str(effect_read[1])

        # 2. Mutating peer call produces None (fence)
        item_write = {
            "name": "agent_worker1_run_task",
            "args": {"prompt": "do work"},
            "attempts": 1,
        }
        effect_write = loop._call_effect(item_write)
        assert effect_write is None

    finally:
        unregister_spec("agent_worker1_query_status")
        unregister_spec("agent_worker1_run_task")


def test_call_effect_real_remote_adapter_specs(tmp_path: Path):
    """Verify real RemoteAgentAdapter specs (tool_class='network') are scheduled correctly:
    read-only query_status produces ('peer', Path) while mutating run_task is fenced.
    """
    from core.agent_bridge.adapters.remote import RemoteAgentAdapter
    from core.agent_bridge.models import AgentRecord

    record = AgentRecord(
        id="worker2",
        display_name="Worker 2",
        kind="remote_http",
        tier=3,
        endpoint="https://192.168.1.50:8000",
        tier3={"allow_insecure": True},
    )
    adapter = RemoteAgentAdapter(record)
    specs = adapter.to_tool_specs()
    for s in specs:
        register_spec(s)

    try:
        from agent.loop import AgentLoop

        class DummyCompleter:
            def complete(self, *_args, **_kwargs):
                return ""

        policy = PermissionPolicy.from_dict({
            "classes": {"network": "allow"},
            "headless": {"network": "allow"},
        })
        runtime = AgentRuntime(tmp_path, policy)
        loop = AgentLoop(DummyCompleter(), runtime=runtime, max_parallel_tools=4)

        # 1. Real read-only peer tool (tool_class='network', requires_approval=False)
        item_query = {
            "name": "agent_worker2_query_status",
            "args": {"task_id": "t-456"},
            "attempts": 1,
        }
        effect_query = loop._call_effect(item_query)
        assert effect_query is not None
        assert effect_query[0] == "peer"
        assert "worker2" in str(effect_query[1])

        # 2. Real mutating peer tool (tool_class='network', requires_approval=True)
        item_run = {
            "name": "agent_worker2_run_task",
            "args": {"prompt": "run simulation"},
            "attempts": 1,
        }
        effect_run = loop._call_effect(item_run)
        assert effect_run is None  # Strictly fenced!
    finally:
        for s in specs:
            unregister_spec(s.name)

