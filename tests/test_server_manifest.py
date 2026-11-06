"""R3: the resident payload names the servers it is hiding tools from.

Without a manifest the model asks `search_tools(intent="vault")` and gets an
ambiguity block it could have avoided, because nothing told it that both Calyx
and Obsidian were connected. The manifest is context, not a router: it is built
from the live catalogue, bounded, and never executes anything
(TOOL-RETRIEVAL-DESIGN §4.6).
"""

from __future__ import annotations

import pytest

from core import tool_bridge
from core import tool_index as ti
from core.tool_index import ToolEntry


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setattr(ti._EmbedBackend, "load", lambda self: False)
    ti.reset_index()
    idx = ti.ToolIndex(cache_dir=tmp_path / "cache")
    monkeypatch.setattr(tool_bridge, "get_index", lambda: idx)
    monkeypatch.setattr(ti, "get_index", lambda: idx)
    tool_bridge.configure(
        settings={"tool_index_enabled": True, "tool_index_budget": 20}
    )
    yield idx
    tool_bridge.configure(settings={"tool_index_enabled": True})
    ti.reset_index()


def _add(index, server: str, count: int, *, domain: str = "", prefix: str = "op") -> None:
    for i in range(count):
        index.upsert(
            ToolEntry(
                name=f"mcp_{server}_{prefix}{i}",
                description=f"An operation on {server}.",
                source=f"mcp:{server}",
                domain=domain,
            )
        )


def test_the_payload_lists_each_connected_server_once(index):
    _add(index, "Calyx", 3, domain="kernel constellation / memory vault")
    _add(index, "Obsidian", 2, domain="notes vault")
    index.put_manual(ti.SkillManual(skill_id="mcp:Calyx", title="Calyx", body="Rules."))

    payload = tool_bridge.build_session_payload("")
    manifest = payload["server_manifest"]

    assert manifest.startswith("Connected tool servers")
    assert manifest.count("- Calyx") == 1
    assert manifest.count("- Obsidian") == 1
    assert "(3 tools; manual: mcp:Calyx)" in manifest
    assert "kernel constellation / memory vault" in manifest
    # The manifest is part of the instruction text, ahead of the JIT manuals.
    assert payload["skill_context"].startswith(manifest)


def test_servers_are_ordered_by_tool_count_then_name(index):
    _add(index, "Bravo", 3)
    _add(index, "Alpha", 3)
    _add(index, "Charlie", 10)

    manifest = index.server_manifest()

    assert manifest.index("- Charlie") < manifest.index("- Alpha") < manifest.index("- Bravo")


def test_the_manifest_is_capped_by_server_count(index):
    for n in range(20):
        _add(index, f"S{n:02d}", 2)

    manifest = index.server_manifest()

    listed = [line for line in manifest.splitlines() if line.startswith("- S")]
    assert len(listed) == ti.SERVER_MANIFEST_MAX_SERVERS
    assert "+8 more" in manifest
    assert "list_tools()" in manifest


def test_the_manifest_is_capped_by_characters(index):
    long_domain = "a domain label that is deliberately long enough to matter"
    for n in range(ti.SERVER_MANIFEST_MAX_SERVERS):
        _add(index, f"S{n:02d}" + "x" * 80, 2, domain=long_domain)

    manifest = index.server_manifest()

    assert len(manifest) <= ti.SERVER_MANIFEST_MAX_CHARS
    assert manifest.startswith("Connected tool servers")


def test_a_dropped_server_disappears_from_the_manifest(index):
    _add(index, "Calyx", 2)
    _add(index, "Obsidian", 2)
    assert "- Obsidian" in index.server_manifest()

    index.remove_source("mcp:Obsidian")

    manifest = index.server_manifest()
    assert "- Calyx" in manifest
    assert "Obsidian" not in manifest


def test_disabled_tools_do_not_inflate_a_count(index):
    _add(index, "Calyx", 3)
    index.set_disabled(["mcp_Calyx_op0"])

    assert "(2 tools)" in index.server_manifest()


def test_the_manifest_yields_to_the_legacy_payload_when_the_index_is_off(index):
    _add(index, "Calyx", 2)

    tool_bridge.configure(settings={"tool_index_enabled": False})
    payload = tool_bridge.build_session_payload("")

    assert payload["indexed"] is False
    assert payload["server_manifest"] == ""
    assert payload["skill_context"] == ""
