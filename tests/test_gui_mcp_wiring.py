"""The GUI text path must advertise MCP tools on its first model call.

The core manager is covered in ``test_agent_mcp.py``. This file checks the
wiring that only exists in ``main.py``: the merge into the pre-loop tool list
and the graceful degradation of a broken config. It is deliberately *not*
named ``test_agent_*`` so the stdlib-only harness CI job (pytest only, no PyQt)
does not collect it.
"""
from __future__ import annotations

import types

from agent.mcp import WorkspaceMCP
from agent.spec import ToolSpec, register, unregister


def _spec() -> ToolSpec:
    return ToolSpec(
        name="mcp_gui_echo",
        description="Echo through the GUI surface.",
        parameters={"type": "object", "properties": {}},
        tool_class="mcp",
        handler=lambda arguments, context: "ok",
    )


def test_the_gui_merges_mcp_tools_into_the_first_surface(tmp_path, monkeypatch):
    import main as gui

    monkeypatch.setattr(
        "agent.runtime.current", lambda: types.SimpleNamespace(workspace=tmp_path)
    )
    register(_spec())
    try:
        dummy = types.SimpleNamespace(_agent_event=lambda event: None)
        merged = gui.BrahmaLive._with_agent_mcp(dummy, [{"name": "read_file"}])

        names = [
            (tool.get("function") or {}).get("name") or tool.get("name")
            for tool in merged
        ]
        assert "read_file" in names
        assert "mcp_gui_echo" in names
    finally:
        unregister("mcp_gui_echo")
        gui._harness_mcp_manager().close()


def test_a_broken_mcp_config_does_not_break_the_turn(tmp_path, monkeypatch):
    import main as gui

    monkeypatch.setattr(
        "agent.runtime.current", lambda: types.SimpleNamespace(workspace=tmp_path)
    )
    path = tmp_path / ".brahma" / "mcp.json"
    path.parent.mkdir()
    path.write_text("{not json", encoding="utf-8")
    events: list = []

    try:
        dummy = types.SimpleNamespace(_agent_event=events.append)
        tools = [{"name": "read_file"}]
        merged = gui.BrahmaLive._with_agent_mcp(dummy, tools)

        assert [tool["name"] for tool in merged] == ["read_file"]
        assert events and events[0]["type"] == "error"
        assert "invalid JSON" in events[0]["error"]
    finally:
        gui._harness_mcp_manager().close()


def test_the_gui_manager_is_process_wide(tmp_path, monkeypatch):
    import main as gui

    monkeypatch.setattr(
        "agent.runtime.current", lambda: types.SimpleNamespace(workspace=tmp_path)
    )
    try:
        first = gui._harness_mcp_manager()
        second = gui._harness_mcp_manager()
        assert isinstance(first, WorkspaceMCP)
        assert first is second
        assert first.generation == 0
    finally:
        gui._harness_mcp_manager().close()
