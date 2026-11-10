"""Unit tests for agent.scripts_tool (§9.9, §16 Decision 11)."""
from __future__ import annotations

import json
from pathlib import Path

from agent import spec
from agent.runtime import AgentRuntime
from agent.scripts_tool import search_scripts
from agent.spec import ToolContext


def test_search_scripts_registered():
    registered = spec.get("search_scripts")
    assert registered is not None
    assert registered.name == "search_scripts"
    assert registered.tool_class == "read"
    assert "scripts" in registered.tags


def test_search_scripts_requires_query():
    ctx = ToolContext(workspace=Path("."))
    res = search_scripts({}, ctx)
    assert "requires a 'query' parameter" in res


def test_search_scripts_finds_multimodal_ingest():
    ctx = ToolContext(workspace=Path("."))
    res_str = search_scripts({"query": "convert docx document to markdown", "limit": 3}, ctx)
    data = json.loads(res_str)
    assert data["count"] >= 1
    names = [s["name"] for s in data["scripts"]]
    assert any("ingest_multimodal" in n for n in names)
    top = data["scripts"][0]
    assert "suggested_command" in top
    assert top["safety"] in ("workspace_write", "read_only", "sandbox")


def test_search_scripts_unknown_query():
    ctx = ToolContext(workspace=Path("."))
    res_str = search_scripts({"query": "zyxwvutsrqp-nonexistent-command-12345"}, ctx)
    data = json.loads(res_str)
    assert data["count"] == 0
    assert data["scripts"] == []


def test_search_scripts_via_runtime(tmp_path: Path):
    runtime = AgentRuntime(workspace=tmp_path)
    res_text = runtime.execute(
        "search_scripts",
        {"query": "taxonomy tree linting", "limit": 2},
    )
    data = json.loads(res_text)
    assert "scripts" in data
    assert any("taxonomy" in s["name"] for s in data["scripts"])

