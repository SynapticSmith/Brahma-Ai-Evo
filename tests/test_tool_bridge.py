"""Integration tests for the tool bridge and its wiring into main.py.

These exercise the real code path that broke the Live handshake: catalog
population, session-payload assembly, and the three discovery meta-tools. They
import ``main`` lazily and skip the GUI when it cannot load headless, so they
stay runnable in CI.
"""

import asyncio
import re
from pathlib import Path

import pytest

from core import tool_bridge
from core import tool_index as ti
from core.tool_index import GEMINI_MAX_TOOLS, META_DECLARATIONS, ToolEntry

ROOT = Path(__file__).resolve().parent.parent


def _native_declarations():
    """Reads the real TOOL_DECLARATIONS out of main.py without importing it."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    start = src.index("TOOL_DECLARATIONS = [")
    end = src.index("\n]", start)
    block = src[start:end]
    out = []
    for m in re.finditer(
        r'"name":\s*"([a-z0-9_]+)"\s*,\s*"description":\s*(\(.*?\)|"[^"]*")',
        block,
        re.S,
    ):
        try:
            desc = eval(m.group(2)) if m.group(2).startswith("(") else __import__("json").loads(m.group(2))
        except Exception:
            desc = m.group(2)
        out.append({"name": m.group(1), "description": " ".join(str(desc).split()),
                    "parameters": {"type": "OBJECT", "properties": {}}})
    return out


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    """A bridge wired to an isolated index and a recording executor."""
    ti.reset_index()
    idx = ti.ToolIndex(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(tool_bridge, "get_index", lambda: idx)
    monkeypatch.setattr(ti, "get_index", lambda: idx)

    calls = []

    def _executor(name, args):
        calls.append((name, args))
        return f"executed {name} with {args}"

    tool_bridge.configure(settings={"tool_index_enabled": True, "tool_index_budget": 20}, executor=_executor)
    yield idx, calls
    ti.reset_index()


# --------------------------------------------------------------------------- #
# The handshake
# --------------------------------------------------------------------------- #


def test_session_payload_is_within_the_gemini_limit(bridge):
    """The regression test for the reported 1007 / Invalid Argument crash."""
    idx, _ = bridge
    natives = _native_declarations()
    tool_bridge.sync_native(natives)
    # Simulate the two servers that pushed the catalogue past the ceiling.
    for i in range(31):
        idx.upsert(ToolEntry(name=f"calyx_t{i}", description="Calyx op.", source="mcp:Calyx"))
    for i in range(82):
        idx.upsert(ToolEntry(name=f"synapse_t{i}", description="Synapse op.", source="mcp:Synapse"))

    payload = tool_bridge.build_session_payload("")
    assert len(natives) + 113 > GEMINI_MAX_TOOLS, "fixture must exceed the limit"
    assert len(payload["declarations"]) <= GEMINI_MAX_TOOLS
    assert payload["indexed"] is True
    assert payload["deferred"] > 0


def test_payload_always_carries_the_discovery_tools(bridge):
    tool_bridge.sync_native(_native_declarations())
    names = {d["name"] for d in tool_bridge.build_session_payload("control the mouse")["declarations"]}
    assert {d["name"] for d in META_DECLARATIONS} <= names


def test_payload_is_deterministic_for_the_same_intent(bridge):
    tool_bridge.sync_native(_native_declarations())
    a = [d["name"] for d in tool_bridge.build_session_payload("send an email")["declarations"]]
    b = [d["name"] for d in tool_bridge.build_session_payload("send an email")["declarations"]]
    assert a == b


def test_budget_is_honoured_and_clamped(bridge):
    tool_bridge.sync_native(_native_declarations())
    tool_bridge.configure(settings={"tool_index_budget": 10})
    assert len(tool_bridge.build_session_payload("")["declarations"]) <= 10

    tool_bridge.configure(settings={"tool_index_budget": 100000})
    assert len(tool_bridge.build_session_payload("")["declarations"]) <= GEMINI_MAX_TOOLS

    tool_bridge.configure(settings={"tool_index_budget": "nonsense"})
    assert tool_bridge.budget() >= 6


def test_disabled_index_falls_back_to_the_legacy_surface(bridge):
    """Escape hatch exists, and warns in the log that it will be rejected."""
    tool_bridge.prime(_native_declarations(), _FakeRegistry())
    tool_bridge.configure(settings={"tool_index_enabled": False})
    payload = tool_bridge.build_session_payload("")
    assert payload["indexed"] is False
    assert len(payload["declarations"]) >= len(_native_declarations())
    # The discovery layer must NOT be present when the index is off.
    assert not any(d["name"] in ("search_tools", "call_tool") for d in payload["declarations"])


class _FakeRegistry:
    def __init__(self, tools=None):
        self._tools = tools or []

    def get_tool_declarations(self):
        return list(self._tools)

    def get_skill(self, name):
        class S:
            manifest = {"source": "mcp:Test"}
        return S()


# --------------------------------------------------------------------------- #
# Meta-tool routing
# --------------------------------------------------------------------------- #


def test_is_meta_tool(bridge):
    assert tool_bridge.is_meta_tool("search_tools")
    assert tool_bridge.is_meta_tool("call_tool")
    assert tool_bridge.is_meta_tool("get_skill_manual")
    assert not tool_bridge.is_meta_tool("web_search")


def test_search_tools_returns_schemas_the_model_can_use(bridge):
    idx, _ = bridge
    idx.upsert(ToolEntry(
        name="mcp_Calyx_search",
        description="Search a Calyx vault for grounded memories.",
        parameters={"type": "OBJECT", "properties": {"query": {"type": "STRING"}},
                    "required": ["query"]},
        source="mcp:Calyx",
    ))
    out = asyncio.run(tool_bridge.execute_meta("search_tools", {"intent": "search my vault", "limit": 5}))
    assert "mcp_Calyx_search" in out
    assert "query" in out
    assert "call_tool" in out  # tells the model how to use the result


def test_search_tools_requires_an_intent(bridge):
    out = asyncio.run(tool_bridge.execute_meta("search_tools", {}))
    assert out.startswith("Error:")


def test_search_tools_reports_an_empty_result_honestly(bridge):
    idx, _ = bridge
    idx.upsert(ToolEntry(name="only_tool", description="Does one thing.", source="native"))
    out = asyncio.run(tool_bridge.execute_meta("search_tools", {"intent": "quantum teleportation"}))
    assert "No tool matches" in out


def test_call_tool_executes_through_the_injected_executor(bridge):
    idx, calls = bridge
    idx.upsert(ToolEntry(name="mcp_Calyx_search", description="Search a vault.", source="mcp:Calyx"))
    out = asyncio.run(tool_bridge.execute_meta(
        "call_tool", {"tool_name": "mcp_Calyx_search", "arguments": {"query": "notes"}}))
    assert "executed mcp_Calyx_search" in out
    assert calls == [("mcp_Calyx_search", {"query": "notes"})]


def test_call_tool_rejects_an_unknown_tool_with_suggestions(bridge):
    idx, calls = bridge
    idx.upsert(ToolEntry(name="calyx_search", description="Search a vault.", source="mcp:Calyx"))
    out = asyncio.run(tool_bridge.execute_meta("call_tool", {"tool_name": "calyx_serch"}))
    assert out.startswith("Error:")
    assert "calyx_search" in out  # suggests the near-miss
    assert calls == []  # and does not execute anything


def test_call_tool_tolerates_missing_arguments(bridge):
    idx, calls = bridge
    idx.upsert(ToolEntry(name="some_tool", description="x", source="native"))
    out = asyncio.run(tool_bridge.execute_meta("call_tool", {"tool_name": "some_tool"}))
    assert "executed some_tool" in out
    assert calls[0][1] == {}


def test_get_skill_manual_serves_the_registered_guide(bridge):
    idx, _ = bridge
    idx.put_manual(ti.SkillManual(skill_id="mcp:Synapse", title="Synapse", body="Observe before acting."))
    out = asyncio.run(tool_bridge.execute_meta("get_skill_manual", {"skill_id": "Synapse"}))
    assert "Observe before acting" in out
    assert "SKILL MANUAL" in out


def test_get_skill_manual_lists_registered_groups_when_unknown(bridge):
    idx, _ = bridge
    idx.put_manual(ti.SkillManual(skill_id="mcp:Synapse", body="x"))
    out = asyncio.run(tool_bridge.execute_meta("get_skill_manual", {"skill_id": "nope"}))
    assert "No manual registered" in out
    assert "mcp:Synapse" in out


def test_meta_tools_never_raise(bridge):
    """Whatever happens, the model must get a readable string back."""
    for name, args in (
        ("search_tools", {"intent": None}),
        ("call_tool", {"tool_name": None}),
        ("call_tool", {"tool_name": 123, "arguments": "not a dict"}),
        ("get_skill_manual", {"skill_id": ["a", "b"]}),
        ("search_tools", {"intent": "x", "limit": "lots"}),
        ("search_tools", {"intent": "x", "limit": 9999}),
    ):
        out = asyncio.run(tool_bridge.execute_meta(name, args))
        assert isinstance(out, str) and out


def test_unknown_meta_tool_is_reported(bridge):
    out = asyncio.run(tool_bridge.execute_meta("not_a_meta_tool", {}))
    assert "unknown meta-tool" in out


# --------------------------------------------------------------------------- #
# JIT skill context
# --------------------------------------------------------------------------- #


def test_skill_context_only_includes_advertised_groups(bridge):
    """A group's manual is injected if and only if one of its tools is advertised."""
    idx, _ = bridge
    idx.put_manual(ti.SkillManual(skill_id="mcp:Unused", body="UNUSED-GROUP-RULES."))
    idx.put_manual(ti.SkillManual(skill_id="mcp:Synapse", body="SYNAPSE-RULES."))
    idx.upsert(ToolEntry(name="mcp_Synapse_observe", description="Observe the desktop screen.", source="mcp:Synapse"))
    idx.upsert(ToolEntry(name="web_search", description="Search the public web for news.", source="native", pinned=True))
    idx.upsert(ToolEntry(name="send_email", description="Send an email message.", source="native", pinned=True))

    payload = tool_bridge.build_session_payload("send an email")
    advertised = {d["name"] for d in payload["declarations"]}
    groups = {
        idx.get(n).skill_id for n in advertised if idx.get(n) and idx.get(n).source.startswith("mcp:")
    }

    if "mcp:Synapse" in groups:
        assert "SYNAPSE-RULES." in payload["skill_context"]
    else:
        assert "SYNAPSE-RULES." not in payload["skill_context"]

    # A group with no advertised tools must never leak its rules into the prompt.
    assert "mcp:Unused" not in groups
    assert "UNUSED-GROUP-RULES." not in payload["skill_context"]


def test_skill_context_appears_when_the_group_is_used(bridge):
    idx, _ = bridge
    idx.upsert(ToolEntry(
        name="mcp_Synapse_observe", description="Observe the desktop accessibility tree and screenshots.",
        source="mcp:Synapse", domain="desktop",
    ))
    idx.put_manual(ti.SkillManual(skill_id="mcp:Synapse", title="Synapse", body="Synapse safety rules."))
    payload = tool_bridge.build_session_payload("look at what is on my screen")
    assert "Synapse safety rules" in payload["skill_context"]


# --------------------------------------------------------------------------- #
# Sync / snapshot
# --------------------------------------------------------------------------- #


def test_sync_registry_indexes_mcp_tools_with_their_source(bridge):
    idx, _ = bridge
    registry = _FakeRegistry([
        {"name": "mcp_Calyx_search", "description": "Search a vault.", "parameters": {}},
        {"name": "custom_skill", "description": "A local skill.", "parameters": {}},
    ])
    count = tool_bridge.sync_registry(registry)
    assert count == 2
    assert idx.get("mcp_Calyx_search").source == "mcp:Test"


def test_snapshot_reports_the_numbers_the_settings_ui_needs(bridge):
    idx, _ = bridge
    tool_bridge.sync_native(_native_declarations())
    tool_bridge.build_session_payload("do something")
    snap = tool_bridge.snapshot()
    assert snap["tools"] > 50
    assert snap["advertised"] <= snap["gemini_limit"]
    assert snap["deferred"] == snap["tools"] - snap["advertised"]
    assert "fts5" in snap["backends"]


def test_explain_reports_ranking_detail(bridge):
    tool_bridge.sync_native(_native_declarations())
    rows = tool_bridge.explain("play some music", k=5)
    assert rows and all({"name", "score", "source"} <= set(r) for r in rows)


# --------------------------------------------------------------------------- #
# Static checks against main.py
# --------------------------------------------------------------------------- #


def test_main_no_longer_advertises_every_tool_unconditionally():
    """`_build_config` must go through the bridge, not splice the full list."""
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    body = src[src.index("def _build_config"):src.index("def _build_config") + 4000]
    assert "tool_bridge.build_session_payload" in body
    assert "DynamicToolRegistry.get_tool_declarations()" not in body, (
        "the unindexed full-tool splice is the bug that produced 173 declarations"
    )


def test_meta_tools_route_before_the_normal_dispatch():
    """The executor (P4-19: moved out of main.py) handles discovery meta-tools
    before the HUD/speech noise that follows the normal dispatch."""
    src = (ROOT / "core" / "tool_exec.py").read_text(encoding="utf-8")
    body = src[src.index("async def execute(self, fc)"):]
    head = body[:body.index("self._host.speak(")]
    assert "tool_bridge.is_meta_tool" in head
    assert "tool_bridge.execute_meta" in head


def test_main_installs_the_bridge_executor():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "tool_bridge.configure(executor=" in src
    assert "def _execute_tool_via_bridge" in src


# --------------------------------------------------------------------------- #
# Disabled tools (Settings: "Tools & MCP Access")
# --------------------------------------------------------------------------- #


def test_configure_applies_persisted_disabled_tools(bridge):
    """A disabled tool persisted in app_settings must be gone from the session."""
    tool_bridge.sync_native(_native_declarations())
    tool_bridge.configure(settings={"tool_index_enabled": True, "tool_index_budget": 24,
                                    "disabled_tools": ["web_search"]})
    names = {d["name"] for d in tool_bridge.build_session_payload("search the web")["declarations"]}
    assert "web_search" not in names
    assert "web_search" in [e.name for e in bridge[0].all_entries()]
    assert tool_bridge.snapshot()["disabled"] == 1


def test_set_disabled_tools_takes_effect_immediately(bridge):
    tool_bridge.sync_native(_native_declarations())
    tool_bridge.set_disabled_tools(["open_app"])
    names = {d["name"] for d in tool_bridge.build_session_payload("open an app")["declarations"]}
    assert "open_app" not in names
    assert tool_bridge.disabled_tools() == ["open_app"]

    tool_bridge.set_disabled_tools([])
    assert "open_app" in {e.name for e in bridge[0].entries()}


def test_disabled_tool_cannot_be_called(bridge):
    idx, calls = bridge
    idx.upsert(ToolEntry(name="send_email", description="Send an email.", source="native"))
    tool_bridge.set_disabled_tools(["send_email"])
    out = asyncio.run(tool_bridge.execute_meta("call_tool", {"tool_name": "send_email"}))
    assert out.startswith("Error:")
    assert calls == []  # never dispatched


def test_tool_catalog_groups_default_tools_and_mcp_servers(bridge):
    idx, _ = bridge
    idx.upsert(ToolEntry(name="web_search", description="Search the web.", source="native"))
    idx.upsert(ToolEntry(name="calyx_search", description="Search a vault.", source="mcp:Calyx"))
    idx.upsert(ToolEntry(name="synapse_observe", description="Look at the screen.", source="mcp:Synapse"))
    tool_bridge.set_disabled_tools(["calyx_search"])

    catalog = tool_bridge.tool_catalog()
    groups = {g["label"]: g for g in catalog["groups"]}
    assert groups["Default tools"]["kind"] == "default"
    assert [t["name"] for t in groups["Default tools"]["tools"]] == ["web_search"]
    assert groups["Calyx"]["kind"] == "mcp"
    assert groups["Calyx"]["tools"][0]["enabled"] is False   # disabled still listed
    assert groups["Synapse"]["tools"][0]["enabled"] is True
    assert catalog["disabled"] == ["calyx_search"]


def test_tool_catalog_shows_a_configured_but_tool_less_server(bridge):
    """A server added before it connects must still get a section (not blank)."""
    tool_bridge.configure(settings={
        "tool_index_enabled": True,
        "custom_mcp_servers": [{"name": "Filesystem", "command": "npx"}],
    })
    catalog = tool_bridge.tool_catalog()
    labels = [g["label"] for g in catalog["groups"]]
    assert "Filesystem" in labels
    filesystem = next(g for g in catalog["groups"] if g["label"] == "Filesystem")
    assert filesystem["tools"] == []