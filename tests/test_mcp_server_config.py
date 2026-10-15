"""Tests for MCP server config loading and the shipped-template seed.

The promise: the per-user ``app_settings.json`` is the single source of truth,
and the tracked (credentials-redacted) ``config/app_settings.json`` template is
copied into it exactly once on a fresh install. This is the regression guard for
the bug where the manager read the template directly, so a server added in
Settings never loaded and the template's own servers vanished once it stopped.
"""

import json

import pytest

from core import mcp_manager as mm


def _server(name):
    return {"name": name, "command": "run"}


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Redirects the user config and template to isolated temp files."""
    settings = tmp_path / "app_settings.json"
    template = tmp_path / "template.json"
    monkeypatch.setattr(mm, "SETTINGS_PATH", settings)
    monkeypatch.setattr(mm, "TEMPLATE_PATH", template)
    return settings, template


def _write(path, data):
    path.write_text(json.dumps(data, indent=4), encoding="utf-8")


def test_seeds_a_fresh_user_config_from_the_template(paths):
    settings, template = paths
    _write(settings, {"custom_mcp_servers": [], "other": 1})
    _write(template, {"custom_mcp_servers": [_server("Calyx")]})

    configs = mm.MCPServerManager.load_server_configs()

    assert [c["name"] for c in configs] == ["Calyx"]
    written = json.loads(settings.read_text(encoding="utf-8"))
    assert written["custom_mcp_servers"][0]["name"] == "Calyx"
    assert written["custom_mcp_servers_migrated"] is True
    assert written["other"] == 1  # unrelated keys preserved


def test_user_servers_win_over_the_template(paths):
    settings, template = paths
    _write(settings, {"custom_mcp_servers": [_server("Mine")]})
    _write(template, {"custom_mcp_servers": [_server("Template")]})

    configs = mm.MCPServerManager.load_server_configs()

    assert [c["name"] for c in configs] == ["Mine"]


def test_seeding_happens_only_once(paths):
    settings, template = paths
    _write(settings, {"custom_mcp_servers": []})
    _write(template, {"custom_mcp_servers": [_server("Seed")]})

    assert [c["name"] for c in mm.MCPServerManager.load_server_configs()] == ["Seed"]

    # The user deliberately clears the list: it must stay cleared, not re-seed.
    data = json.loads(settings.read_text(encoding="utf-8"))
    data["custom_mcp_servers"] = []
    _write(settings, data)
    assert mm.MCPServerManager.load_server_configs() == []


def test_invalid_entries_are_skipped(paths):
    settings, _template = paths
    _write(settings, {"custom_mcp_servers": [
        {"name": "", "command": "x"},          # no name
        {"name": "NoTarget"},                  # neither command nor url
        {"name": "Ok", "command": "run"},      # valid
        {"name": "Remote", "url": "http://h"},  # valid
    ]})

    names = sorted(c["name"] for c in mm.MCPServerManager.load_server_configs())
    assert names == ["Ok", "Remote"]


def test_missing_user_config_is_not_an_error(paths):
    settings, template = paths
    template.unlink(missing_ok=True)
    assert mm.MCPServerManager.load_server_configs() == []


def test_standard_mcp_servers_key_is_read(paths):
    settings, _template = paths
    _write(settings, {
        "custom_mcp_servers": [],
        "mcpServers": {
            "Stdio": {"command": "run", "args": ["--x"]},
            "Remote": {
                "url": "http://127.0.0.1:7700/mcp",
                "headers": {"Authorization": "Bearer token"},
            },
        },
    })

    configs = mm.MCPServerManager.load_server_configs()
    by = {c["name"]: c for c in configs}

    assert sorted(by) == ["Remote", "Stdio"]
    assert by["Stdio"]["command"] == "run"
    assert by["Stdio"]["args"] == ["--x"]
    assert by["Remote"]["headers"]["Authorization"] == "Bearer token"


def test_custom_mcp_servers_wins_over_standard_key(paths):
    settings, _template = paths
    _write(settings, {
        "custom_mcp_servers": [_server("Mine")],
        "mcpServers": {"Std": {"command": "run"}},
    })

    assert [c["name"] for c in mm.MCPServerManager.load_server_configs()] == ["Mine"]
