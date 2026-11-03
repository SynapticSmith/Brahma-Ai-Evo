"""Config loading: safe defaults, BOM tolerance, and no noise for absent keys."""

from __future__ import annotations

import json

from brahma_api.config import DEFAULT_PORT, BrahmaApiConfig


def _write(base, payload, *, bom=False):
    path = base / "config" / "brahma_api.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8-sig" if bom else "utf-8")
    return path


def test_a_minimal_config_loads_without_complaints(tmp_path):
    _write(tmp_path, {"enabled": True})
    config = BrahmaApiConfig.load(tmp_path)
    assert config.enabled is True
    assert config.problems == []
    assert config.port == DEFAULT_PORT
    assert config.workspaces == {"brahma": str(tmp_path.resolve())}


def test_a_utf8_bom_is_not_a_config_error(tmp_path):
    # Windows PowerShell and Notepad both write one by default.
    _write(tmp_path, {"enabled": True, "port": 8020}, bom=True)
    config = BrahmaApiConfig.load(tmp_path)
    assert config.enabled is True
    assert config.port == 8020
    assert config.problems == []


def test_a_non_loopback_host_falls_back_without_the_opt_in(tmp_path):
    _write(tmp_path, {"host": "0.0.0.0"})
    config = BrahmaApiConfig.load(tmp_path)
    assert config.host == "127.0.0.1"
    assert any("allow_remote_bind" in problem for problem in config.problems)


def test_a_non_loopback_host_is_allowed_with_the_opt_in(tmp_path):
    _write(tmp_path, {"host": "0.0.0.0", "allow_remote_bind": True})
    config = BrahmaApiConfig.load(tmp_path)
    assert config.host == "0.0.0.0"
    assert config.problems == []


def test_an_out_of_range_port_falls_back(tmp_path):
    _write(tmp_path, {"port": 80})
    config = BrahmaApiConfig.load(tmp_path)
    assert config.port == DEFAULT_PORT
    assert any("port" in problem for problem in config.problems)


def test_a_bad_workspace_alias_is_reported_not_used(tmp_path):
    _write(tmp_path, {"workspaces": {"bad alias": str(tmp_path)}})
    config = BrahmaApiConfig.load(tmp_path)
    assert "bad alias" not in config.workspaces
    assert any("alias" in problem for problem in config.problems)


def test_a_missing_directory_is_not_a_workspace(tmp_path):
    _write(tmp_path, {"workspaces": {"gone": str(tmp_path / "nope")}})
    config = BrahmaApiConfig.load(tmp_path)
    assert "gone" not in config.workspaces
    assert any("not a directory" in problem for problem in config.problems)


def test_a_broken_file_leaves_safe_defaults(tmp_path):
    path = tmp_path / "config" / "brahma_api.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    config = BrahmaApiConfig.load(tmp_path)
    assert config.enabled is False
    assert config.host == "127.0.0.1"
    assert config.problems


def test_save_then_load_round_trips(tmp_path):
    config = BrahmaApiConfig.load(tmp_path)
    config.enabled = True
    config.port = 8030
    config.workspaces = {"repo": str(tmp_path)}
    config.save()

    reloaded = BrahmaApiConfig.load(tmp_path)
    assert reloaded.enabled is True
    assert reloaded.port == 8030
    assert reloaded.workspaces == {"repo": str(tmp_path)}
    assert reloaded.problems == []
