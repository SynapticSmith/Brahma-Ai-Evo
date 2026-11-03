"""Unit tests for modular memory connectors (ADR-018 / User Note 8)."""

from __future__ import annotations

from unittest.mock import MagicMock

from memory.connectors.factory import get_memory_connector, list_available_backends
from memory.connectors.local import LocalMemoryConnector
from memory.connectors.mem0 import Mem0MemoryConnector
from memory.connectors.letta import LettaMemoryConnector


def test_list_available_backends():
    backends = list_available_backends()
    ids = [b["id"] for b in backends]
    assert "local" in ids
    assert "mem0" in ids
    assert "letta" in ids


def test_local_memory_connector(tmp_path, monkeypatch):
    mem_file = tmp_path / "long_term.json"
    monkeypatch.setattr("memory.memory_manager.MEMORY_PATH", mem_file)

    conn = LocalMemoryConnector()
    assert conn.backend_name == "local"

    # Store
    ok = conn.store("user_timezone", "America/New_York", category="preferences")
    assert ok is True

    # List all
    entries = conn.list_all()
    assert len(entries) >= 1
    assert any(e.key == "user_timezone" for e in entries)

    # Recall
    recalled = conn.recall("timezone")
    assert len(recalled) >= 1
    assert recalled[0].key == "user_timezone"
    assert recalled[0].value == "America/New_York"

    # Status
    stat = conn.status()
    assert stat["ok"] is True
    assert stat["total_entries"] >= 1

    # Delete
    del_ok = conn.delete("user_timezone", category="preferences")
    assert del_ok is True
    assert len(conn.recall("timezone")) == 0


def test_mem0_connector_mocked():
    conn = Mem0MemoryConnector(api_key="test_key", user_id="user_test")
    assert conn.backend_name == "mem0"

    mock_client = MagicMock()
    mock_client.search.return_value = [
        {"memory": "Likes dark mode UI", "metadata": {"key": "theme_pref", "category": "preferences"}, "score": 0.95}
    ]
    mock_client.get_all.return_value = [
        {"memory": "Likes dark mode UI", "metadata": {"key": "theme_pref", "category": "preferences"}}
    ]
    conn._client = mock_client

    assert conn.store("theme_pref", "dark mode", category="preferences") is True
    mock_client.add.assert_called_once()

    results = conn.recall("dark mode")
    assert len(results) == 1
    assert results[0].key == "theme_pref"
    assert results[0].value == "Likes dark mode UI"

    assert conn.delete("mem_123") is True
    mock_client.delete.assert_called_once_with(memory_id="mem_123")


def test_letta_connector_mocked(monkeypatch):
    conn = LettaMemoryConnector(endpoint="http://mock-letta:8283", agent_id="agent-01")
    assert conn.backend_name == "letta"

    def mock_request(method, path, payload=None, timeout=5.0):
        if "/health" in path:
            return {"status": "ok"}
        elif "/archival" in path and method == "POST":
            return {"id": "arch-1", "text": payload.get("text", "")}
        elif "/archival" in path and method == "GET":
            return [{"id": "arch-1", "text": "[NOTES] project_goal: Complete release"}]
        elif method == "DELETE":
            return {"status": "deleted"}
        return None

    monkeypatch.setattr(conn, "_request", mock_request)

    # Health / Status
    stat = conn.status()
    assert stat["ok"] is True
    assert "mock-letta" in stat["endpoint"]

    # Store
    assert conn.store("project_goal", "Complete release") is True

    # Recall
    results = conn.recall("release")
    assert len(results) == 1
    assert "Complete release" in results[0].value


def test_factory_resolver(monkeypatch):
    monkeypatch.setattr("memory.config_manager.get_setting", lambda key, default=None: "local")
    conn = get_memory_connector()
    assert isinstance(conn, LocalMemoryConnector)

    conn_letta = get_memory_connector("letta")
    assert isinstance(conn_letta, LettaMemoryConnector)
