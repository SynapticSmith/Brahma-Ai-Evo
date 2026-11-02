"""Tests for durable agent output artifact store and retrieval (ADR-015, Q2)."""

from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime
from agent.spec import register, unregister
from core.agent_bridge.adapter import AgentAdapter
from core.agent_bridge.artifacts import (
    clean_expired_artifacts,
    create_read_artifact_spec,
    list_artifacts,
    parse_artifact_ref,
    persist_call_result,
    read_artifact,
    save_artifact,
)
from core.agent_bridge.detectors import DetectionContext
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    Capability,
    ProbeResult,
)


def test_save_artifact_and_read_back(tmp_path: Path):
    text = "Hello world! This is a test output from an external agent."
    ref = save_artifact(
        agent_id="test-agent",
        verb="run_task",
        output=text,
        ok=True,
        call_id="call_test_001",
        artifacts_dir=tmp_path,
    )

    assert ref == "agent-artifact://test-agent/call_test_001"
    assert (tmp_path / "test-agent" / "call_test_001.json").is_file()
    assert (tmp_path / "test-agent" / "manifest.jsonl").is_file()

    # Read back full output
    res = read_artifact(ref, artifacts_dir=tmp_path)
    assert res["ref"] == ref
    assert res["agent_id"] == "test-agent"
    assert res["call_id"] == "call_test_001"
    assert res["verb"] == "run_task"
    assert res["ok"] is True
    assert res["output"] == text
    assert res["chars_total"] == len(text)
    assert res["has_more"] is False
    assert len(res["sha256"]) == 64


def test_read_artifact_pagination(tmp_path: Path):
    long_text = "ABCDEFGHIJ" * 1000  # 10,000 characters
    ref = save_artifact(
        agent_id="test-agent",
        verb="generate_report",
        output=long_text,
        call_id="call_pages",
        artifacts_dir=tmp_path,
    )

    # Chunk 1: [0:4000]
    p1 = read_artifact(ref, offset=0, limit=4000, artifacts_dir=tmp_path)
    assert p1["offset"] == 0
    assert p1["slice_chars"] == 4000
    assert p1["has_more"] is True
    assert p1["output"] == long_text[0:4000]

    # Chunk 2: [4000:8000]
    p2 = read_artifact(ref, offset=4000, limit=4000, artifacts_dir=tmp_path)
    assert p2["offset"] == 4000
    assert p2["slice_chars"] == 4000
    assert p2["has_more"] is True
    assert p2["output"] == long_text[4000:8000]

    # Chunk 3: [8000:12000] (remaining 2000)
    p3 = read_artifact(ref, offset=8000, limit=4000, artifacts_dir=tmp_path)
    assert p3["offset"] == 8000
    assert p3["slice_chars"] == 2000
    assert p3["has_more"] is False
    assert p3["output"] == long_text[8000:10000]


def test_read_artifact_path_traversal_guards(tmp_path: Path):
    with pytest.raises(ValueError):
        parse_artifact_ref("agent-artifact://../../evil/call_1")

    with pytest.raises(ValueError):
        parse_artifact_ref("agent-artifact://ollama/../../call_1")

    with pytest.raises(ValueError):
        parse_artifact_ref("ftp://ollama/call_1")

    with pytest.raises(ValueError):
        parse_artifact_ref("agent-artifact://only_one_part")


def test_persist_call_result_truncation(tmp_path: Path):
    large_output = "Line content\n" * 500  # ~6,500 bytes
    original_result = AgentCallResult(ok=True, output=large_output)

    budget = 1000  # 1000 bytes limit
    processed = persist_call_result(
        agent_id="claude-code",
        verb="exec",
        result=original_result,
        artifacts_dir=tmp_path,
        max_output_bytes=budget,
    )

    assert processed.truncated is True
    assert processed.artifact_ref is not None
    assert processed.artifact_ref.startswith("agent-artifact://claude-code/call_")
    assert "Output truncated" in processed.output
    assert processed.artifact_ref in processed.output

    # The persisted artifact on disk must contain the full, untruncated content!
    stored = read_artifact(processed.artifact_ref, limit=20000, artifacts_dir=tmp_path)
    assert stored["output"] == large_output
    assert stored["chars_total"] == len(large_output)


def test_list_artifacts_ordering(tmp_path: Path):
    save_artifact("agent1", "verb1", "out1", call_id="c1", artifacts_dir=tmp_path)
    time.sleep(0.01)
    save_artifact("agent2", "verb2", "out2", call_id="c2", artifacts_dir=tmp_path)
    time.sleep(0.01)
    save_artifact("agent1", "verb3", "out3", call_id="c3", artifacts_dir=tmp_path)

    all_items = list_artifacts(artifacts_dir=tmp_path)
    assert len(all_items) == 3
    # Newest first
    assert all_items[0]["call_id"] == "c3"
    assert all_items[1]["call_id"] == "c2"
    assert all_items[2]["call_id"] == "c1"

    agent1_items = list_artifacts(agent_id="agent1", artifacts_dir=tmp_path)
    assert len(agent1_items) == 2
    assert agent1_items[0]["call_id"] == "c3"
    assert agent1_items[1]["call_id"] == "c1"


def test_clean_expired_artifacts(tmp_path: Path):
    save_artifact("agent1", "verb1", "out1", call_id="old_call", artifacts_dir=tmp_path)
    save_artifact("agent1", "verb2", "out2", call_id="new_call", artifacts_dir=tmp_path)

    old_file = tmp_path / "agent1" / "old_call.json"
    assert old_file.is_file()

    # Set mtime to 10 days ago
    ten_days_ago = time.time() - (10 * 86400)
    os.utime(old_file, (ten_days_ago, ten_days_ago))

    deleted = clean_expired_artifacts(max_days=7, artifacts_dir=tmp_path)
    assert deleted == 1
    assert not old_file.is_file()
    assert (tmp_path / "agent1" / "new_call.json").is_file()


def test_read_agent_artifact_tool_spec_and_runtime(tmp_path: Path):
    long_content = "Antigravity Agent Framework output.\n" * 150
    ref = save_artifact(
        agent_id="test_worker",
        verb="survey",
        output=long_content,
        call_id="call_spec_test",
        artifacts_dir=tmp_path,
    )

    spec = create_read_artifact_spec(artifacts_dir=tmp_path)
    assert spec.name == "read_agent_artifact"
    assert spec.tool_class == "read"
    assert spec.requires_approval is False

    register(spec)
    try:
        runtime = AgentRuntime(
            workspace=tmp_path,
            policy=PermissionPolicy.from_dict({"classes": {"read": "allow"}}),
        )
        # Execute tool via AgentRuntime
        result_text = runtime.execute(
            spec.name,
            {"ref": ref, "offset": 0, "limit": 200},
        )
        assert "Artifact: agent-artifact://test_worker/call_spec_test" in result_text
        assert "Antigravity Agent Framework" in result_text
        assert "chars remaining" in result_text
    finally:
        unregister(spec.name)


class _DummyTestAdapter(AgentAdapter):
    AGENT_ID = "dummy_adapter"

    @classmethod
    def detect(cls, ctx: DetectionContext):
        return None

    def probe(self):
        return ProbeResult(ok=True, stage="capability", detail="OK")

    def connect(self):
        pass

    def verbs(self):
        return [Capability(name="synthesize", description="Synthesize data")]

    def call(self, verb: str, args: dict):
        return AgentCallResult(ok=True, output="Generated comprehensive synthesis.")

    def close(self):
        pass


def test_adapter_to_tool_specs_auto_persists_artifact(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("BRAHMA_AGENT_ARTIFACTS_DIR", str(tmp_path))

    record = AgentRecord(
        id="dummy_adapter",
        display_name="Dummy Adapter",
        kind="local_model",
        capabilities=[{"name": "synthesize", "description": "Synthesize data"}],
    )
    adapter = _DummyTestAdapter(record)
    specs = adapter.to_tool_specs()
    assert len(specs) == 1
    synth_spec = specs[0]

    register(synth_spec)
    try:
        runtime = AgentRuntime(
            workspace=tmp_path,
            policy=PermissionPolicy.from_dict({"classes": {"network": "allow"}}),
        )
        out = runtime.execute(synth_spec.name, {})
        assert out == "Generated comprehensive synthesis."

        # Verify artifact store has the call persisted
        manifest = tmp_path / "dummy_adapter" / "manifest.jsonl"
        assert manifest.is_file()
        items = list_artifacts(agent_id="dummy_adapter", artifacts_dir=tmp_path)
        assert len(items) == 1
        assert items[0]["verb"] == "synthesize"
        assert items[0]["artifact_ref"].startswith("agent-artifact://dummy_adapter/call_")
    finally:
        unregister(synth_spec.name)
