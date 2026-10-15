"""Tests for the "Tools & MCP Access" card.

Covers the two promises that are easy to regress:
  1. per-source grouping — default tools and one section per MCP server, each
     tool a toggle;
  2. dynamic population — MCP tools arrive asynchronously as servers connect,
     so the list must refresh itself without a page reopen or the Refresh button.
"""

import os

import pytest

from core import tool_bridge


def _catalog(*, default_enabled=True, mcp_tools=("read", "write"), mcp_state="connected"):
    return {
        "groups": [
            {
                "id": "__default__", "label": "Default tools", "kind": "default", "state": "",
                "tools": [{"name": "open_app", "enabled": default_enabled}],
            },
            {
                "id": "Calyx", "label": "Calyx", "kind": "mcp", "state": mcp_state,
                "tools": [{"name": n, "enabled": True} for n in mcp_tools],
            },
        ],
        "disabled": [],
        "default_count": 1,
    }


def _sig(catalog):
    import ui

    return ui.SystemConnectivityPage._tool_visibility_signature(catalog)


@pytest.fixture(scope="session")
def _app():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture(scope="session")
def page(_app):
    import ui

    # Built once: construction is heavy (imports, audio probe). Tests drive the
    # rebuild directly, so the catalog read at construction time is irrelevant.
    return ui.SystemConnectivityPage()


def test_signature_ignores_the_enabled_flag():
    assert _sig(_catalog(default_enabled=True)) == _sig(_catalog(default_enabled=False))


def test_signature_changes_when_tools_or_state_change():
    base = _sig(_catalog())
    assert _sig(_catalog(mcp_tools=("read", "write", "delete"))) != base
    assert _sig(_catalog(mcp_state="connecting")) != base


def test_card_renders_one_toggle_per_tool(page, monkeypatch):
    monkeypatch.setattr(tool_bridge, "tool_catalog", lambda: _catalog())
    page._rebuild_tool_visibility()
    names = sorted(box.text() for box in page._tool_visibility_boxes)
    assert names == ["open_app", "read", "write"]


def test_default_and_mcp_tools_are_grouped_separately(page, monkeypatch):
    seen = {}

    def fake_catalog():
        cat = _catalog()
        seen["groups"] = cat["groups"]
        return cat

    monkeypatch.setattr(tool_bridge, "tool_catalog", fake_catalog)
    page._rebuild_tool_visibility()

    assert [g["kind"] for g in seen["groups"]] == ["default", "mcp"]
    mcp = next(g for g in seen["groups"] if g["kind"] == "mcp")
    assert mcp["state"] == "connected"
    assert sorted(t["name"] for t in mcp["tools"]) == ["read", "write"]


def test_poll_rebuilds_only_when_the_inventory_changes(page, monkeypatch):
    calls = {"n": 0}
    real = page._rebuild_tool_visibility

    def wrapped():
        calls["n"] += 1
        real()

    monkeypatch.setattr(page, "_rebuild_tool_visibility", wrapped)
    monkeypatch.setattr(page, "isVisible", lambda: True)
    monkeypatch.setattr(tool_bridge, "tool_catalog", lambda: _catalog())

    page._rebuild_tool_visibility()  # baseline signature
    assert calls["n"] == 1

    page._poll_tool_visibility()  # unchanged inventory -> no redraw
    assert calls["n"] == 1

    monkeypatch.setattr(
        tool_bridge, "tool_catalog", lambda: _catalog(mcp_tools=("read", "write", "delete"))
    )
    page._poll_tool_visibility()  # a server gained a tool -> redraw
    assert calls["n"] == 2
    assert "delete" in [box.text() for box in page._tool_visibility_boxes]


def test_poll_is_a_noop_while_the_page_is_hidden(page, monkeypatch):
    calls = {"n": 0}
    real = page._rebuild_tool_visibility

    def wrapped():
        calls["n"] += 1
        real()

    monkeypatch.setattr(page, "_rebuild_tool_visibility", wrapped)
    monkeypatch.setattr(page, "isVisible", lambda: False)
    monkeypatch.setattr(tool_bridge, "tool_catalog", lambda: _catalog(mcp_tools=("changed",)))

    page._rebuild_tool_visibility()
    page._poll_tool_visibility()
    assert calls["n"] == 1  # hidden page: no work done
