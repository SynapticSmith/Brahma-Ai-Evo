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


def test_live_surface_advertises_the_library_lookup_but_not_dispatch():
    """A voice session can say what the library contains; it cannot run a child."""
    names = {d["name"] for d in main.TOOL_DECLARATIONS if "name" in d}

    assert "list_subagents" in names
    assert "run_subagent" not in names
    assert "run_subagent_batch" not in names


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
