"""Tests for PluginManager opt-in allowlisting and lifecycle (Finding #2 / WS-4)."""

import json
from pathlib import Path
import sys

from plugin_manager import PluginManager


def test_empty_allowlist_rejects_unapproved_plugins(tmp_path: Path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    evil_script = plugins_dir / "evil.py"
    evil_script.write_text("executed = True\nplugin = {'name': 'evil'}", encoding="utf-8")

    pm = PluginManager(tmp_path)
    loaded = pm.load_plugins()

    assert loaded == []
    assert len(pm.plugins) == 0
    assert "evil" not in sys.modules


def test_explicit_allowlist_loads_approved_plugin(tmp_path: Path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    good_script = plugins_dir / "good_plugin.py"
    good_script.write_text("plugin = {'name': 'good_plugin', 'ok': True}", encoding="utf-8")

    unapproved_script = plugins_dir / "unapproved.py"
    unapproved_script.write_text("plugin = {'name': 'unapproved'}", encoding="utf-8")

    pm = PluginManager(tmp_path, allowlist=["good_plugin"])
    loaded = pm.load_plugins()

    assert len(loaded) == 1
    assert loaded[0] == {"name": "good_plugin", "ok": True}
    assert "unapproved" not in sys.modules


def test_allowlist_loaded_from_plugin_policy_json(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    policy_file = config_dir / "plugin_policy.json"
    policy_file.write_text(json.dumps({"enabled_plugins": ["config_allowed"]}), encoding="utf-8")

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    allowed_script = plugins_dir / "config_allowed.py"
    allowed_script.write_text("plugin = 'loaded_via_policy'", encoding="utf-8")

    denied_script = plugins_dir / "denied.py"
    denied_script.write_text("plugin = 'should_not_load'", encoding="utf-8")

    pm = PluginManager(tmp_path)
    loaded = pm.load_plugins()

    assert len(loaded) == 1
    assert loaded[0] == "loaded_via_policy"
    assert "denied" not in sys.modules


def test_allowlist_loaded_from_app_settings_json(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True)
    settings_file = config_dir / "app_settings.json"
    settings_file.write_text(json.dumps({"enabled_plugins": ["settings_allowed"]}), encoding="utf-8")

    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    allowed_script = plugins_dir / "settings_allowed.py"
    allowed_script.write_text("plugin = 'loaded_via_settings'", encoding="utf-8")

    pm = PluginManager(tmp_path)
    loaded = pm.load_plugins()

    assert len(loaded) == 1
    assert loaded[0] == "loaded_via_settings"


def test_plugin_dispatch_and_brahma_registration(tmp_path: Path):
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()

    script = plugins_dir / "hook_plugin.py"
    script.write_text(
        """
events = []

def on_startup(brahma=None):
    events.append("started")

def on_text_command(text, source="local", brahma=None):
    events.append(f"cmd:{text}")
    if text == "stop_here":
        return True
    return False

class HookPlugin:
    on_startup = staticmethod(on_startup)
    on_text_command = staticmethod(on_text_command)

plugin = HookPlugin()
""",
        encoding="utf-8",
    )

    pm = PluginManager(tmp_path, allowlist=["hook_plugin"])
    pm.load_plugins()

    assert len(pm.plugins) == 1
    pm.register_brahma({"name": "mock_brahma"})

    # Dispatch on_startup
    pm.dispatch("on_startup")
    # Dispatch on_text_command
    stopped = pm.dispatch("on_text_command", "stop_here", "local")
    assert stopped is True
    continued = pm.dispatch("on_text_command", "hello", "local")
    assert continued is False
