"""Unit tests for AgentRecord data model and AgentRegistry (ADR-018, Q2)."""

import time

from core.agent_bridge.models import AgentRecord, AgentTransport
from core.agent_bridge.registry import AgentRegistry


def test_agent_record_serialization():
    record = AgentRecord(
        id="test-agent",
        display_name="Test Agent",
        kind="cli_agent",
        tier=1,
        status="found",
        executable="C:\\bin\\agent.exe",
        transport=AgentTransport(mode="stdio", argv=["agent", "run"], env={"API_KEY": "API_KEY"}),
        sources=["path"],
    )

    d = record.to_dict()
    assert d["id"] == "test-agent"
    assert d["transport"]["mode"] == "stdio"
    assert d["transport"]["argv"] == ["agent", "run"]

    restored = AgentRecord.from_dict(d)
    assert restored.id == record.id
    assert restored.executable == record.executable
    assert restored.transport.mode == "stdio"
    assert restored.transport.env == {"API_KEY": "API_KEY"}


def test_agent_registry_load_empty_on_missing_file(tmp_path):
    registry = AgentRegistry(store_path=tmp_path / "nonexistent.json")
    assert registry.load() == {}
    assert registry.list_all() == []
    assert registry.get("claude-code") is None


def test_agent_registry_save_and_load(tmp_path):
    store_file = tmp_path / "agents.json"
    registry = AgentRegistry(store_path=store_file)

    rec = AgentRecord(
        id="ollama",
        display_name="Ollama",
        kind="local_model",
        endpoint="http://127.0.0.1:11434/v1",
        sources=["local_port"],
    )

    registry.save({"ollama": rec})
    assert store_file.exists()

    loaded = registry.load()
    assert len(loaded) == 1
    assert "ollama" in loaded
    assert loaded["ollama"].endpoint == "http://127.0.0.1:11434/v1"


def test_agent_registry_register_or_update_merge_policy(tmp_path):
    store_file = tmp_path / "agents.json"
    registry = AgentRegistry(store_path=store_file)

    # 1. Initial discovery from PATH
    t0 = time.time() - 100
    rec1 = AgentRecord(
        id="claude-code",
        display_name="Claude Code",
        kind="cli_agent",
        executable="C:\\bin\\claude.exe",
        sources=["path"],
        last_seen=t0,
    )
    registry.register_or_update(rec1)

    r1 = registry.get("claude-code")
    assert r1 is not None
    assert r1.sources == ["path"]
    assert r1.executable == "C:\\bin\\claude.exe"

    # 2. Subsequent discovery hits config layer with a version
    rec2 = AgentRecord(
        id="claude-code",
        display_name="Claude Code",
        kind="cli_agent",
        executable="C:\\bin\\claude.exe",
        version="1.0.4",
        sources=["vendor_config"],
    )
    registry.register_or_update(rec2)

    r2 = registry.get("claude-code")
    assert r2 is not None
    # Sources must be unioned
    assert sorted(r2.sources) == ["path", "vendor_config"]
    assert r2.version == "1.0.4"
    assert r2.last_seen > t0


def test_agent_registry_preserves_user_edits(tmp_path):
    store_file = tmp_path / "agents.json"
    registry = AgentRegistry(store_path=store_file)

    # Initial record
    rec = AgentRecord(
        id="my-agent",
        display_name="My Agent",
        kind="cli_agent",
        sources=["path"],
    )
    registry.register_or_update(rec)

    # User disables the agent
    registry.set_enabled("my-agent", False)
    assert registry.get("my-agent").enabled is False
    assert registry.get("my-agent").user_edited is True

    # Re-scan attempts to register as enabled
    rescan = AgentRecord(
        id="my-agent",
        display_name="My Agent Updated",
        kind="cli_agent",
        sources=["path", "config"],
        enabled=True,
    )
    merged = registry.register_or_update(rescan)

    # User edit (enabled=False) must be preserved
    assert merged.enabled is False
    assert merged.user_edited is True
    # But sources still update
    assert sorted(merged.sources) == ["config", "path"]


def test_agent_registry_delete_and_clear(tmp_path):
    store_file = tmp_path / "agents.json"
    registry = AgentRegistry(store_path=store_file)

    rec1 = AgentRecord(id="agent1", display_name="A1", kind="cli_agent")
    rec2 = AgentRecord(id="agent2", display_name="A2", kind="cli_agent")

    registry.register_or_update(rec1)
    registry.register_or_update(rec2)
    assert len(registry.list_all()) == 2

    assert registry.delete("agent1") is True
    assert registry.get("agent1") is None
    assert len(registry.list_all()) == 1

    registry.clear()
    assert len(registry.list_all()) == 0
