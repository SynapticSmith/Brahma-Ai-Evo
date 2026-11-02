"""Tests for AgentBridge facade: discovery, lifecycle, tool publishing, and shutdown (ADR-018, Q2)."""

from __future__ import annotations

from pathlib import Path

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import get as get_spec
from core.agent_bridge.adapter import AgentAdapter
from core.agent_bridge.bridge import AgentBridge, close_agent_bridge, get_agent_bridge
from core.agent_bridge.detectors import DetectionContext
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    Capability,
    ProbeResult,
)
from core.agent_bridge.registry import AgentRegistry


class _MockTestAdapter(AgentAdapter):
    AGENT_ID = "mock_agent"

    def __init__(self, record: AgentRecord):
        super().__init__(record)
        self.closed = False
        self.connected = False

    @classmethod
    def detect(cls, ctx: DetectionContext):
        return None

    def probe(self) -> ProbeResult:
        return ProbeResult(ok=True, stage="capability", detail="Mock probe OK", version="1.0.0")

    def connect(self) -> None:
        self.connected = True
        self.record.status = "connected"

    def verbs(self):
        return [Capability(name="analyze", description="Analyze data payload", mutating=False)]

    def call(self, verb: str, args: dict):
        return AgentCallResult(ok=True, output=f"Mock analysis output for {verb}")

    def close(self):
        self.closed = True
        self.record.status = "found"


def test_bridge_discover_and_adapter_sync(tmp_path: Path, monkeypatch):
    reg_file = tmp_path / "registry.json"
    registry = AgentRegistry(store_path=reg_file)
    bridge = AgentBridge(registry=registry, artifacts_dir=tmp_path / "artifacts")

    # Mock detectors.run_all to return fixed records
    fake_records = [
        AgentRecord(id="ollama", display_name="Ollama", kind="local_model"),
        AgentRecord(id="fake_cli", display_name="Fake CLI", kind="cli_agent"),
    ]
    monkeypatch.setattr("core.agent_bridge.bridge.run_all", lambda ctx: fake_records)

    discovered = bridge.discover(workspace=tmp_path)
    assert len(discovered) == 2
    assert "ollama" in bridge._adapters
    assert "fake_cli" in bridge._adapters

    # Now simulate disabling fake_cli via registry.set_enabled
    registry.set_enabled("fake_cli", False)

    bridge.discover(workspace=tmp_path)
    # fake_cli adapter must be closed and removed from active adapters
    assert "fake_cli" not in bridge._adapters
    assert "ollama" in bridge._adapters

    bridge.close()


def test_bridge_probe_all(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    registry = AgentRegistry(store_path=reg_file)
    bridge = AgentBridge(registry=registry, artifacts_dir=tmp_path / "artifacts")

    rec = AgentRecord(id="mock1", display_name="Mock 1", kind="local_model")
    registry.save(rec)
    adapter = _MockTestAdapter(rec)
    bridge._adapters["mock1"] = adapter

    probe_results = bridge.probe_all()
    assert "mock1" in probe_results
    assert probe_results["mock1"].ok is True
    assert probe_results["mock1"].version == "1.0.0"

    # Status must be saved in registry
    saved_rec = registry.get("mock1")
    assert saved_rec is not None
    assert saved_rec.status == "connected"

    bridge.close()


def test_bridge_publish_tools_and_execution_and_unpublish(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    registry = AgentRegistry(store_path=reg_file)
    bridge = AgentBridge(registry=registry, artifacts_dir=tmp_path / "artifacts")

    rec = AgentRecord(
        id="mock_worker",
        display_name="Mock Worker",
        kind="local_model",
        capabilities=[{"name": "analyze", "description": "Analyze data payload"}],
    )
    registry.save(rec)
    adapter = _MockTestAdapter(rec)
    bridge._adapters["mock_worker"] = adapter

    published = bridge.publish_tools()
    assert "read_agent_artifact" in published
    assert "agent_mock_worker_analyze" in published

    # Verify both specs exist in agent.spec
    artifact_spec = get_spec("read_agent_artifact")
    assert artifact_spec is not None
    worker_spec = get_spec("agent_mock_worker_analyze")
    assert worker_spec is not None

    # Test runtime execution through AgentRuntime
    runtime = AgentRuntime(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict({"classes": {"network": "allow", "read": "allow"}}),
    )
    res = runtime.execute("agent_mock_worker_analyze", {})
    assert "Mock analysis output for analyze" in res

    # Unpublish tools
    bridge.unpublish_tools()
    assert get_spec("read_agent_artifact") is None
    assert get_spec("agent_mock_worker_analyze") is None
    assert len(bridge._published_tool_names) == 0

    bridge.close()
    assert adapter.closed is True


def test_bridge_close_is_idempotent(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    registry = AgentRegistry(store_path=reg_file)
    bridge = AgentBridge(registry=registry, artifacts_dir=tmp_path / "artifacts")

    rec = AgentRecord(id="mock2", display_name="Mock 2", kind="local_model")
    adapter = _MockTestAdapter(rec)
    bridge._adapters["mock2"] = adapter
    bridge.publish_tools()

    bridge.close()
    assert adapter.closed is True
    assert len(bridge._adapters) == 0

    # Second close must not raise
    bridge.close()


def test_global_bridge_singleton(tmp_path: Path):
    reg_file = tmp_path / "registry.json"
    registry = AgentRegistry(store_path=reg_file)

    b1 = get_agent_bridge(registry=registry, artifacts_dir=tmp_path)
    b2 = get_agent_bridge()
    assert b1 is b2

    close_agent_bridge()

    b3 = get_agent_bridge(registry=registry, artifacts_dir=tmp_path)
    assert b3 is not b1

    close_agent_bridge()
