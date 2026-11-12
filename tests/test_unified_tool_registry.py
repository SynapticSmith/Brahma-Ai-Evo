"""Tests for the 100% Unified Tool Registry (ADR-019 Waves 1-4, Architectural Question 1).

Validates:
1. Every tool in main.TOOL_DECLARATIONS has a registered ToolSpec in agent.spec.
2. Canonical lowercase parameter schemas across all registered tools.
3. Every ToolSpec is classified into a canonical capability class.
4. Execution routes through AgentRuntime.execute with permission policy enforcement.
"""

from __future__ import annotations

import main
from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import all_specs
from core import desktop_tools


def test_the_shared_declaration_pool_carries_the_dispatch_tools():
    """The index pool must be complete; the Live surface drops what it cannot ask.

    ``main.TOOL_DECLARATIONS`` is both the Live declaration list and the pool
    ``core/tool_bridge`` indexes for a GUI text turn. Excluding the dispatch
    tools here (the old behaviour) also hid them from text, where the system
    prompt promises they exist, so the model answered "not an available tool".
    """
    names = {d["name"] for d in main.TOOL_DECLARATIONS if "name" in d}

    assert {"list_subagents", "list_peers", "peer_status"} <= names
    assert {"run_subagent", "run_subagent_batch", "dispatch_peer_task"} <= names


def test_the_live_surface_drops_the_dispatch_tools():
    """A voice turn has no approval banner, so it must not be offered them."""
    from core.session_lifecycle import voice_tool_declarations

    names = {d["name"] for d in voice_tool_declarations(main.TOOL_DECLARATIONS)}

    assert "list_subagents" in names
    assert "list_peers" in names
    assert "peer_status" in names
    assert "run_subagent" not in names
    assert "run_subagent_batch" not in names
    assert "dispatch_peer_task" not in names


def test_a_peer_dispatch_request_advertises_the_dispatch_tool(tmp_path, monkeypatch):
    """The first text call retrieves the tool the prompt says is available."""
    from core import tool_bridge
    from core import tool_index as ti

    ti.reset_index()
    index = ti.ToolIndex(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(tool_bridge, "get_index", lambda: index)
    monkeypatch.setattr(ti, "get_index", lambda: index)
    tool_bridge.configure(
        settings={"tool_index_enabled": True, "tool_index_budget": 24}
    )
    try:
        tool_bridge.sync_native(main.TOOL_DECLARATIONS)
        payload = tool_bridge.build_session_payload(
            "dispatch a task to the other machine"
        )
        names = [d["name"] for d in payload["declarations"]]
        assert "dispatch_peer_task" in names
        assert "list_peers" in names
    finally:
        tool_bridge.reset_session_learning()


def test_all_main_declarations_have_registered_tool_specs():
    """Every native tool declared in main.TOOL_DECLARATIONS must map to a ToolSpec."""
    desktop_tools.register(main.TOOL_DECLARATIONS)
    specs = {s.name: s for s in all_specs()}

    declared_names = [d["name"] for d in main.TOOL_DECLARATIONS if "name" in d]
    assert len(declared_names) > 30

    missing = []
    for name in declared_names:
        if name not in specs:
            missing.append(name)

    assert missing == [], f"Tools in main.TOOL_DECLARATIONS lacking ToolSpecs: {missing}"


def test_canonical_schemas_and_classes_across_all_specs():
    """All registered ToolSpecs must possess valid schemas and valid classes."""
    desktop_tools.register(main.TOOL_DECLARATIONS)
    specs = all_specs()
    assert len(specs) >= 100

    valid_classes = {"read", "write", "exec", "network", "device", "mcp", "native"}

    for s in specs:
        assert s.name, "ToolSpec must have a non-empty name"
        assert s.tool_class in valid_classes, f"ToolSpec '{s.name}' has invalid class '{s.tool_class}'"
        assert isinstance(s.parameters, dict), f"ToolSpec '{s.name}' must have a parameters dict"
        assert s.parameters.get("type") == "object", f"ToolSpec '{s.name}' root schema must be 'object'"


def test_agent_runtime_dispatches_registered_desktop_tool(tmp_path):
    """Invoking a registered desktop tool goes through AgentRuntime.execute with policy gating."""
    desktop_tools.register(main.TOOL_DECLARATIONS)

    policy = PermissionPolicy.from_dict({
        "classes": {"read": "allow", "exec": "deny"}
    })
    runtime = AgentRuntime(policy=policy, workspace=tmp_path)

    # Allowed read tool executes handler
    allowed = runtime.execute("get_sensorium_telemetry", {})
    assert isinstance(allowed, str)
    assert "permission denied" not in allowed.lower()

    # Denied exec tool (action="kill" dynamically classified as exec) returns hard denial
    denied = runtime.execute("system_manager", {"action": "kill", "process_name": "notepad.exe"})
    assert "permission denied" in denied.lower()
    assert "classes.exec" in denied.lower()
