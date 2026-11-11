"""Execution tracing, span recording, and failure taxonomy for AgentLoop runs.

Standard-library only: imports nothing from PyQt, provider SDKs, or Windows COM.
Carries trace_id, origin_node, and hop to cleanly integrate with peer federation
and local multi-turn execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, Dict, List, Optional
import uuid


# -- Failure Taxonomy (ADR-030 & Capability Maximization §7 A5) -------------

class FailureTaxonomy:
    """Canonical failure labels for automated trace classification."""

    TOOL_CHOICE_ERROR = "tool_choice_error"
    ARGUMENT_ERROR = "argument_error"
    PERMISSION_DENIED = "permission_denied"
    ENVIRONMENT_ERROR = "environment_error"
    CONTEXT_LIMIT = "context_limit"
    UNVERIFIED_STOP = "unverified_stop"
    LOOP_DIVERGENCE = "loop_divergence"
    UNKNOWN_ERROR = "unknown_error"

    ALL_LABELS = (
        TOOL_CHOICE_ERROR,
        ARGUMENT_ERROR,
        PERMISSION_DENIED,
        ENVIRONMENT_ERROR,
        CONTEXT_LIMIT,
        UNVERIFIED_STOP,
        LOOP_DIVERGENCE,
        UNKNOWN_ERROR,
    )


def classify_failure(
    status: str,
    stop_reason: Optional[str] = None,
    spans: Optional[List[Span]] = None,
    error_message: Optional[str] = None,
) -> Optional[str]:
    """Classify an agent run failure into a canonical FailureTaxonomy label."""
    if status == "completed" and (stop_reason in ("completed", None)) and not error_message:
        return None

    combined_text = (error_message or "").lower()
    span_list = spans or []
    for sp in span_list:
        if sp.error:
            combined_text += " " + sp.error.lower()
        for ev in sp.events:
            if "error" in ev:
                combined_text += " " + str(ev["error"]).lower()

    # 1. Budget / context limit
    if stop_reason in ("budget", "max_turns", "context_limit", "token_limit"):
        return FailureTaxonomy.CONTEXT_LIMIT
    if "context window" in combined_text or "token limit" in combined_text:
        return FailureTaxonomy.CONTEXT_LIMIT

    # 2. Permission / authorization denial
    if any(k in combined_text for k in ("permission", "denied", "forbidden", "unauthorized", "insufficient_scope")):
        return FailureTaxonomy.PERMISSION_DENIED

    # 3. Argument error
    if any(k in combined_text for k in ("invalid argument", "required argument", "type error", "validation error", "malformed")):
        return FailureTaxonomy.ARGUMENT_ERROR

    # 4. Loop divergence / thrashing
    if stop_reason == "repetition" or any(k in combined_text for k in ("loop divergence", "repeated call", "repetition detected")):
        return FailureTaxonomy.LOOP_DIVERGENCE

    # 5. Tool choice error
    if any(k in combined_text for k in ("unknown tool", "tool not found", "not registered", "invalid tool")):
        return FailureTaxonomy.TOOL_CHOICE_ERROR

    # 6. Environmental error
    if any(k in combined_text for k in ("no such file", "not found", "connection refused", "timeout", "timed out", "econnrefused")):
        return FailureTaxonomy.ENVIRONMENT_ERROR

    # 7. Unverified stop
    if stop_reason in ("unverified", "unverified_stop", "plan_incomplete") or "unverified" in combined_text:
        return FailureTaxonomy.UNVERIFIED_STOP

    return FailureTaxonomy.UNKNOWN_ERROR if (status in ("error", "failed") or error_message) else None


# -- Span and Trace Data Structures -----------------------------------------

@dataclass
class Span:
    """A single timed operation or step within a trace (turn, tool_call, model_call)."""

    span_id: str
    trace_id: str
    name: str
    kind: str  # "turn", "tool_call", "model_call", "verification", "session"
    parent_span_id: Optional[str] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    status: str = "ok"  # "ok", "error", "cancelled"
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    def finish(self, status: str = "ok", error: Optional[str] = None) -> None:
        self.end_time = time.time()
        self.duration_ms = max(0.0, (self.end_time - self.start_time) * 1000.0)
        self.status = status
        if error:
            self.error = error

    def add_event(self, name: str, **data: Any) -> None:
        self.events.append({
            "name": name,
            "timestamp": time.time(),
            **data,
        })

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Span:
        clean = dict(data)
        return cls(**clean)


@dataclass
class TraceRecord:
    """Complete trace record of an AgentLoop execution."""

    trace_id: str
    session_id: Optional[str] = None
    origin_node: str = "local"
    hop: int = 0
    task_prompt: str = ""
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    status: str = "running"  # "running", "completed", "error", "cancelled"
    stop_reason: Optional[str] = None
    failure_label: Optional[str] = None
    spans: List[Span] = field(default_factory=list)
    attributes: Dict[str, Any] = field(default_factory=dict)

    def finish(
        self,
        status: str = "completed",
        stop_reason: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        self.end_time = time.time()
        self.duration_ms = max(0.0, (self.end_time - self.start_time) * 1000.0)
        self.status = status
        self.stop_reason = stop_reason
        self.failure_label = classify_failure(
            status=status,
            stop_reason=stop_reason,
            spans=self.spans,
            error_message=error_message,
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["spans"] = [s.to_dict() for s in self.spans]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TraceRecord:
        clean = dict(data)
        spans_raw = clean.pop("spans", [])
        spans = [Span.from_dict(s) for s in spans_raw]
        rec = cls(**clean)
        rec.spans = spans
        return rec


# -- Persistent Trace Sink --------------------------------------------------

class TraceSink:
    """Thread-safe, append-only JSONL storage for execution traces."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        if base_dir:
            self._dir = Path(base_dir).resolve()
        else:
            # Default to local app data under BrahmaAI/traces or local traces dir
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                self._dir = Path(local_appdata) / "BrahmaAI" / "traces"
            else:
                self._dir = Path(".brahma") / "traces"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    @property
    def base_dir(self) -> Path:
        return self._dir

    def _trace_file(self, date_str: Optional[str] = None) -> Path:
        day = date_str or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        return self._dir / f"traces_{day}.jsonl"

    def save_trace(self, trace: TraceRecord) -> Path:
        """Append a completed trace to the daily JSONL trace log."""
        data = trace.to_dict()
        line = json.dumps(data, ensure_ascii=False) + "\n"
        target_file = self._trace_file()
        with self._lock:
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(line)
        return target_file

    def list_traces(self, limit: int = 50) -> List[TraceRecord]:
        """List recent traces in reverse chronological order."""
        traces: List[TraceRecord] = []
        with self._lock:
            files = sorted(self._dir.glob("traces_*.jsonl"), reverse=True)
            for fpath in files:
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                        for line in reversed(lines):
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                                traces.append(TraceRecord.from_dict(data))
                                if len(traces) >= limit:
                                    return traces
                            except Exception:
                                continue
                except Exception:
                    continue
        return traces

    def get_trace(self, trace_id: str) -> Optional[TraceRecord]:
        """Retrieve a trace record by trace_id."""
        with self._lock:
            for fpath in sorted(self._dir.glob("traces_*.jsonl"), reverse=True):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        for line in reversed(f.readlines()):
                            if trace_id in line:
                                try:
                                    data = json.loads(line)
                                    if data.get("trace_id") == trace_id:
                                        return TraceRecord.from_dict(data)
                                except Exception:
                                    continue
                except Exception:
                    continue
        return None


# -- Real-Time Event Collector ----------------------------------------------

class TraceCollector:
    """Consumes dict events from AgentLoop._emit and records a TraceRecord with spans."""

    def __init__(
        self,
        sink: Optional[TraceSink] = None,
        task_prompt: str = "",
        origin_node: str = "local",
        hop: int = 0,
        session_id: Optional[str] = None,
        next_sink: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.sink = sink
        self.next_sink = next_sink
        self.trace_id = uuid.uuid4().hex
        self.record = TraceRecord(
            trace_id=self.trace_id,
            session_id=session_id,
            origin_node=origin_node,
            hop=hop,
            task_prompt=task_prompt,
        )
        self._current_turn_span: Optional[Span] = None
        self._active_tool_spans: Dict[str, Span] = {}
        self._lock = threading.Lock()

    def __call__(self, event: Dict[str, Any]) -> None:
        self.handle_event(event)
        if self.next_sink:
            self.next_sink(event)

    def handle_event(self, event: Dict[str, Any]) -> None:
        etype = event.get("type", "")
        with self._lock:
            if etype == "session_start":
                if not self.record.session_id:
                    self.record.session_id = event.get("session_id")
                self.record.attributes["workspace"] = event.get("cwd")

            elif etype == "turn_start":
                turn_num = event.get("turn", 1)
                self._current_turn_span = Span(
                    span_id=uuid.uuid4().hex[:12],
                    trace_id=self.trace_id,
                    name=f"turn_{turn_num}",
                    kind="turn",
                    attributes={"turn": turn_num, "context_chars": event.get("chars", 0)},
                )
                self.record.spans.append(self._current_turn_span)

            elif etype == "assistant":
                if self._current_turn_span:
                    self._current_turn_span.add_event(
                        "model_response",
                        text_length=len(str(event.get("text") or "")),
                        tool_calls_count=len(event.get("tool_calls") or []),
                    )

            elif etype == "tool_call":
                call_id = str(event.get("call_id") or uuid.uuid4().hex[:8])
                parent_id = self._current_turn_span.span_id if self._current_turn_span else None
                tool_span = Span(
                    span_id=call_id,
                    trace_id=self.trace_id,
                    name=f"tool:{event.get('name', 'unknown')}",
                    kind="tool_call",
                    parent_span_id=parent_id,
                    attributes={
                        "tool_name": event.get("name"),
                        "call_id": call_id,
                        "arguments": event.get("arguments", {}),
                        "repeat": event.get("repeat", 0),
                    },
                )
                self._active_tool_spans[call_id] = tool_span
                self.record.spans.append(tool_span)

            elif etype == "tool_result":
                call_id = str(event.get("call_id") or "")
                tool_span = self._active_tool_spans.pop(call_id, None)
                output = str(event.get("output") or "")
                is_err = bool(event.get("error")) or output.startswith("Tool error:")
                if tool_span:
                    tool_span.attributes["output_chars"] = len(output)
                    tool_span.finish(
                        status="error" if is_err else "ok",
                        error=output[:200] if is_err else None,
                    )

            elif etype == "error":
                err_msg = str(event.get("error") or "")
                if self._current_turn_span:
                    self._current_turn_span.finish(status="error", error=err_msg)
                self.record.finish(status="error", error_message=err_msg)
                if self.sink:
                    self.sink.save_trace(self.record)

            elif etype == "stop":
                reason = str(event.get("reason") or "completed")
                if self._current_turn_span and self._current_turn_span.end_time is None:
                    self._current_turn_span.finish(status="ok")
                status = "completed" if reason == "completed" else ("cancelled" if reason == "cancelled" else "error")
                self.record.finish(status=status, stop_reason=reason)
                if self.sink:
                    self.sink.save_trace(self.record)
