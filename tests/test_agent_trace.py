"""Tests for agent/trace.py: Spans, Traces, Failure Taxonomy, and TraceSink."""

from __future__ import annotations

import os
import time

from agent.loop import AgentLoop
from agent.providers import build_completer
from agent.runtime import AgentRuntime
from agent.trace import (
    FailureTaxonomy,
    SkillSelectionSink,
    Span,
    TraceCollector,
    TraceRecord,
    TraceSink,
    classify_failure,
)


def test_span_lifecycle():
    span = Span(
        span_id="span_1",
        trace_id="trace_1",
        name="test_operation",
        kind="tool_call",
        attributes={"tool": "read_file"},
    )
    assert span.span_id == "span_1"
    assert span.trace_id == "trace_1"
    assert span.status == "ok"
    assert span.duration_ms is None

    time.sleep(0.01)
    span.add_event("progress", percent=50)
    span.finish(status="ok")

    assert span.end_time is not None
    assert span.duration_ms is not None and span.duration_ms > 0.0
    assert len(span.events) == 1
    assert span.events[0]["name"] == "progress"

    # Serialization roundtrip
    d = span.to_dict()
    reconstructed = Span.from_dict(d)
    assert reconstructed.span_id == span.span_id
    assert reconstructed.duration_ms == span.duration_ms
    assert reconstructed.events == span.events


def test_trace_record_and_serialization():
    trace = TraceRecord(
        trace_id="tr_100",
        session_id="sess_1",
        origin_node="desktop_master",
        hop=1,
        task_prompt="Audit codebase",
    )
    span = Span(
        span_id="sp_1",
        trace_id="tr_100",
        name="turn_1",
        kind="turn",
    )
    span.finish()
    trace.spans.append(span)
    trace.finish(status="completed", stop_reason="completed")

    assert trace.status == "completed"
    assert trace.duration_ms is not None
    assert trace.failure_label is None

    # Serialization roundtrip
    d = trace.to_dict()
    reconstructed = TraceRecord.from_dict(d)
    assert reconstructed.trace_id == "tr_100"
    assert reconstructed.origin_node == "desktop_master"
    assert reconstructed.hop == 1
    assert len(reconstructed.spans) == 1
    assert reconstructed.spans[0].span_id == "sp_1"


def test_failure_taxonomy_classification():
    # 1. Success case
    assert classify_failure("completed", "completed") is None

    # 2. Context limit / budget
    assert classify_failure("error", "budget") == FailureTaxonomy.CONTEXT_LIMIT
    assert classify_failure("error", "max_turns") == FailureTaxonomy.CONTEXT_LIMIT

    # 3. Permission denied
    assert classify_failure("error", "stopped", error_message="Permission denied by policy") == FailureTaxonomy.PERMISSION_DENIED

    # 4. Argument error
    assert classify_failure("error", "stopped", error_message="Invalid argument: missing path") == FailureTaxonomy.ARGUMENT_ERROR

    # 5. Environment error
    assert classify_failure("error", "stopped", error_message="No such file or directory") == FailureTaxonomy.ENVIRONMENT_ERROR
    assert classify_failure("error", "stopped", error_message="Connection refused") == FailureTaxonomy.ENVIRONMENT_ERROR

    # 6. Tool choice error
    assert classify_failure("error", "stopped", error_message="Unknown tool called: hallucinated_tool") == FailureTaxonomy.TOOL_CHOICE_ERROR

    # 7. Loop divergence
    assert classify_failure("error", "repetition") == FailureTaxonomy.LOOP_DIVERGENCE

    # 8. Unverified stop
    assert classify_failure("error", "unverified") == FailureTaxonomy.UNVERIFIED_STOP

    # 9. Fallback unknown error
    assert classify_failure("error", "unhandled_crash", error_message="Internal core bug") == FailureTaxonomy.UNKNOWN_ERROR


def test_trace_sink_persistence_and_listing(tmp_path):
    sink = TraceSink(base_dir=tmp_path)
    trace1 = TraceRecord(trace_id="trace_alpha", task_prompt="Task A")
    trace1.finish(status="completed")
    sink.save_trace(trace1)

    trace2 = TraceRecord(trace_id="trace_beta", task_prompt="Task B")
    trace2.finish(status="error", error_message="Permission denied")
    sink.save_trace(trace2)

    # Listing
    listed = sink.list_traces()
    assert len(listed) == 2
    ids = [t.trace_id for t in listed]
    assert "trace_alpha" in ids
    assert "trace_beta" in ids

    # Retrieve by ID
    fetched = sink.get_trace("trace_beta")
    assert fetched is not None
    assert fetched.trace_id == "trace_beta"
    assert fetched.failure_label == FailureTaxonomy.PERMISSION_DENIED

    assert sink.get_trace("non_existent") is None


def test_trace_sink_redacts_credentials_before_write(tmp_path):
    sink = TraceSink(base_dir=tmp_path)
    trace = TraceRecord(
        trace_id="trace_secret",
        task_prompt="deploy with password=hunter2 and key sk-abcdefghijklmnopqrstuvwx",
    )
    trace.attributes["api_key"] = "topsecretvalue"
    span = Span(
        span_id="sp_secret",
        trace_id="trace_secret",
        name="tool:run_command",
        kind="tool_call",
        attributes={
            "arguments": {
                "command": "curl -H 'Authorization: Bearer abcdefghijklmnop'",
                "token": "plaintext-token-value",
            }
        },
    )
    trace.spans.append(span)
    trace.finish(status="completed")

    target = sink.save_trace(trace)
    raw = target.read_text(encoding="utf-8")
    for secret in (
        "hunter2",
        "sk-abcdefghijklmnopqrstuvwx",
        "topsecretvalue",
        "plaintext-token-value",
        "abcdefghijklmnop",
    ):
        assert secret not in raw, f"{secret!r} survived redaction:\n{raw}"
    assert "<redacted>" in raw

    # Redaction must not break the JSONL contract.
    loaded = sink.get_trace("trace_secret")
    assert loaded is not None
    assert loaded.trace_id == "trace_secret"


def test_trace_sink_rotation_keeps_traces_readable(tmp_path):
    sink = TraceSink(base_dir=tmp_path, max_bytes=200)
    for index in range(5):
        trace = TraceRecord(trace_id=f"trace_rot_{index}", task_prompt="x" * 40)
        trace.finish(status="completed")
        sink.save_trace(trace)

    files = sorted(p.name for p in tmp_path.glob("traces_*.jsonl"))
    assert len(files) >= 2, files
    ids = {t.trace_id for t in sink.list_traces(limit=10)}
    assert ids == {f"trace_rot_{i}" for i in range(5)}
    assert sink.get_trace("trace_rot_0") is not None


def test_trace_collector_persists_once_when_error_then_stop(tmp_path):
    sink = TraceSink(base_dir=tmp_path)
    collector = TraceCollector(sink=sink, task_prompt="boom")
    collector({"type": "turn_start", "turn": 1})
    collector({"type": "error", "error": "kaboom"})
    collector({"type": "stop", "reason": "error"})

    files = list(tmp_path.glob("traces_*.jsonl"))
    assert len(files) == 1
    lines = [ln for ln in files[0].read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "kaboom" in lines[0]


def test_trace_collector_agent_loop_integration(tmp_path):
    sink = TraceSink(base_dir=tmp_path)
    collector = TraceCollector(
        sink=sink,
        task_prompt="Say hello to the agent",
        origin_node="local",
    )
    runtime = AgentRuntime(workspace=tmp_path)
    completer = build_completer("stub")

    loop = AgentLoop(
        completer,
        runtime=runtime,
        max_turns=2,
        tracer=collector,
    )
    res = loop.run("Say hello to the agent")

    assert res.ok is True
    assert res.trace_id == collector.trace_id

    # Verify trace persisted
    saved_trace = sink.get_trace(collector.trace_id)
    assert saved_trace is not None
    assert saved_trace.trace_id == collector.trace_id
    assert saved_trace.status == "completed"
    assert saved_trace.task_prompt == "Say hello to the agent"
    # Should have at least turn_1 span
    turn_spans = [s for s in saved_trace.spans if s.kind == "turn"]
    assert len(turn_spans) >= 1
    assert turn_spans[0].name == "turn_1"


# -- Skill-selection record sink -------------------------------------------


def test_skill_selection_sink_appends_and_lists_in_order(tmp_path):
    sink = SkillSelectionSink(base_dir=tmp_path)
    first = sink.save({"trace_id": "tr_a", "ts_epoch": 100.0, "selected": "alpha"})
    sink.save({"trace_id": "tr_b", "ts_epoch": 200.0, "selected": "beta"})
    assert first.parent == tmp_path

    records = sink.list_records()
    assert [record["trace_id"] for record in records] == ["tr_a", "tr_b"]
    assert [record["trace_id"] for record in sink.list_records(since=150.0)] == ["tr_b"]
    assert [record["trace_id"] for record in sink.list_records(limit=1)] == ["tr_b"]


def test_skill_selection_sink_redacts_and_can_drop_the_query(tmp_path):
    sink = SkillSelectionSink(base_dir=tmp_path)
    sink.save({"trace_id": "tr_secret", "query": "deploy password=hunter2"})

    payload = sink.list_records()[0]
    assert "hunter2" not in payload["query"]
    assert "<redacted>" in payload["query"]

    dropping = SkillSelectionSink(base_dir=tmp_path / "no_query", retain_query=False)
    dropping.save({"trace_id": "tr_nq", "query": "deploy password=hunter2"})
    assert "query" not in dropping.list_records()[0]


def test_skill_selection_sink_rotates_by_size(tmp_path):
    sink = SkillSelectionSink(base_dir=tmp_path, max_bytes=200)
    sink.save({"trace_id": "tr_1", "padding": "x" * 180})
    sink.save({"trace_id": "tr_2", "padding": "y" * 180})

    files = sorted(tmp_path.glob("skill_selections_*.jsonl"))
    assert len(files) == 2
    assert len(sink.list_records()) == 2


def test_skill_selection_sink_prunes_old_files_only(tmp_path):
    sink = SkillSelectionSink(base_dir=tmp_path)
    old = tmp_path / "skill_selections_2000-01-01.jsonl"
    old.write_text('{"trace_id": "old"}\n', encoding="utf-8")
    stale = time.time() - 90 * 86_400
    os.utime(old, (stale, stale))
    sink.save({"trace_id": "fresh"})

    assert sink.prune(keep_days=30) == 1
    assert not old.exists()
    assert [record["trace_id"] for record in sink.list_records()] == ["fresh"]


def test_skill_selection_sink_can_read_without_creating_the_directory(tmp_path):
    target = tmp_path / "missing"
    sink = SkillSelectionSink(base_dir=target, create=False)
    assert sink.list_records() == []
    assert not target.exists()

