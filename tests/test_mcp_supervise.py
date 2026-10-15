"""Tests for the MCP supervisor's give-up policy and lock-consistent status.

A server whose config never connects once (bad command, missing package, wrong
URL) used to be retried forever on a 30 s cap. The supervisor now parks it in a
"failed" state after a bounded number of consecutive attempts, while a server
that reached "ready" and later dropped keeps restarting (its streak resets).
"""

import asyncio

from core import mcp_manager as mm


def _reset_manager():
    mm.MCPServerManager._status.clear()
    mm.MCPServerManager._stopping = False


def test_never_connecting_server_is_parked_as_failed(monkeypatch):
    _reset_manager()
    attempts = {"n": 0}

    async def never_connects(config):
        attempts["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(mm.MCPServerManager, "_connect_once", staticmethod(never_connects))
    monkeypatch.setattr(mm.MCPServerManager, "_max_connect_failures", 2)
    monkeypatch.setattr(mm.MCPServerManager, "unregister_server_tools",
                        classmethod(lambda cls, name: 0))

    asyncio.run(mm.MCPServerManager._supervise({"name": "Nope", "command": "run"}))

    assert attempts["n"] == 2
    assert mm.MCPServerManager._status["Nope"]["state"] == "failed"
    assert "boom" in mm.MCPServerManager._status["Nope"]["error"]


def test_a_stopped_manager_lets_the_loop_exit(monkeypatch):
    _reset_manager()

    async def one_pass(config):
        mm.MCPServerManager._stopping = True  # emulate shutdown mid-session
        return None

    monkeypatch.setattr(mm.MCPServerManager, "_connect_once", staticmethod(one_pass))
    monkeypatch.setattr(mm.MCPServerManager, "unregister_server_tools",
                        classmethod(lambda cls, name: 0))

    asyncio.run(mm.MCPServerManager._supervise({"name": "Quiet", "command": "run"}))

    assert mm.MCPServerManager._status["Quiet"]["state"] == "connecting"


def test_status_returns_an_independent_snapshot(monkeypatch):
    _reset_manager()
    mm.MCPServerManager._status["A"] = {
        "name": "A", "state": "ready", "tools": ["t"], "error": None, "transport": "stdio",
    }

    snap = mm.MCPServerManager.status()
    snap["A"]["tools"].append("mutated")
    snap["A"]["state"] = "changed"

    assert mm.MCPServerManager._status["A"]["tools"] == ["t"]
    assert mm.MCPServerManager._status["A"]["state"] == "ready"
