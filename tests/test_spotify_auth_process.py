import subprocess

import pytest

from actions import spotify_controller as spotify_module


class _FakeAuthProcess:
    def __init__(self, wait_error=None, exit_code=None):
        self.events = []
        self.wait_error = wait_error
        self.exit_code = exit_code

    def poll(self):
        self.events.append("poll")
        return self.exit_code

    def terminate(self):
        self.events.append("terminate")

    def wait(self, timeout=None):
        self.events.append(("wait", timeout))
        if self.wait_error is not None:
            raise self.wait_error
        return self.exit_code

    def kill(self):
        self.events.append("kill")


class _ImmediateThread:
    def __init__(self, target, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        self.target(*self.args)


@pytest.fixture(autouse=True)
def _isolated_auth_state(tmp_path, monkeypatch):
    monkeypatch.setattr(spotify_module, "_AUTH_PROCESS", None)
    monkeypatch.setattr(spotify_module, "is_spotify_configured", lambda: True)
    monkeypatch.setattr(spotify_module, "SPOTIFY_CONFIG_PATH", tmp_path / "spotify-config.json")
    monkeypatch.setattr(spotify_module, "MCP_BUILD_AUTH", tmp_path / "auth.js")
    monkeypatch.setattr(spotify_module, "MCP_SERVER_DIR", tmp_path)
    spotify_module.MCP_BUILD_AUTH.touch()
    monkeypatch.setattr(spotify_module.threading, "Thread", _ImmediateThread)


def test_auth_stores_process_and_terminates_after_timeout(monkeypatch):
    process = _FakeAuthProcess(wait_error=subprocess.TimeoutExpired("node", 120))
    monkeypatch.setattr(spotify_module.subprocess, "Popen", lambda *args, **kwargs: process)

    result = spotify_module._spotify_mcp_action({"action": "auth"})

    assert result == "Started Spotify authorization in your browser."
    assert ("wait", 120) in process.events
    assert "terminate" in process.events
    assert ("wait", 2) in process.events
    assert spotify_module._AUTH_PROCESS is None


def test_auth_terminates_existing_process_before_starting_new(monkeypatch):
    old_process = _FakeAuthProcess()
    new_process = _FakeAuthProcess(exit_code=0)
    monkeypatch.setattr(spotify_module, "_AUTH_PROCESS", old_process)
    monkeypatch.setattr(spotify_module.subprocess, "Popen", lambda *args, **kwargs: new_process)

    spotify_module._spotify_mcp_action({"action": "login"})

    assert "terminate" in old_process.events
    assert ("wait", 2) in old_process.events
    assert ("wait", 120) in new_process.events
