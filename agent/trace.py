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
import re
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
    #: The turn failed and the injected task skill did not fit the task. Kept
    #: separate from ``TOOL_CHOICE_ERROR`` so the skill-selection audit (R4) can
    #: tell "the model called the wrong tool" apart from "we routed the wrong
    #: SOP to it".
    SKILL_CHOICE_ERROR = "skill_choice_error"
    UNKNOWN_ERROR = "unknown_error"

    ALL_LABELS = (
        TOOL_CHOICE_ERROR,
        ARGUMENT_ERROR,
        PERMISSION_DENIED,
        ENVIRONMENT_ERROR,
        CONTEXT_LIMIT,
        UNVERIFIED_STOP,
        LOOP_DIVERGENCE,
        SKILL_CHOICE_ERROR,
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

#: A trace stores the prompt and every tool argument verbatim. Those fields
#: routinely contain a key a user pasted into the task text or a token a tool
#: was called with, so the line is scrubbed before it reaches disk. The
#: patterns are deliberately JSON-shaped where possible so the record stays
#: valid JSONL after redaction.
_SECRET_KEY_NAMES = (
    r"api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|token|secret|"
    r"client[_-]?secret|password|passwd|authorization|auth|private[_-]?key|"
    r"session[_-]?key"
)

#: ``authorization``/``auth`` are excluded here: their value is usually
#: ``Bearer <token>``, and stopping at the first space would keep the token.
_FREETEXT_SECRET_NAMES = (
    r"api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|token|secret|"
    r"client[_-]?secret|password|passwd|private[_-]?key|session[_-]?key"
)

_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r'("(?:' + _SECRET_KEY_NAMES + r')"\s*:\s*)"(?:[^"\\]|\\.)*"',
            re.IGNORECASE,
        ),
        r'\1"<redacted>"',
    ),
    # Free text pasted into a prompt: ``password=hunter2``, ``token: abc123``.
    (
        re.compile(
            r"(?i)\b(" + _FREETEXT_SECRET_NAMES + r")\b(\s*[=:]\s*)"
            r"([^\s\"'&,;]{6,})"
        ),
        r"\1\2<redacted>",
    ),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._\-+/=]{8,}"), r"\1<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-<redacted>"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}"), "gh_<redacted>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA<redacted>"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{8,}"), "xox-<redacted>"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "<redacted-private-key>",
    ),
)

#: Rotate a daily file once it would exceed this size.
DEFAULT_MAX_TRACE_BYTES = 10 * 1024 * 1024


def redact_text(text: str) -> str:
    """Scrub credential-shaped substrings from a serialized trace line."""
    if not text:
        return text
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def _rotate_file(
    directory: Path,
    target_file: Path,
    incoming_bytes: int,
    max_bytes: int,
) -> None:
    """Move today's JSONL aside when the next line would cross the cap.

    Rotated files keep the ``<stem>.<n>.jsonl`` shape so a reverse-chronological
    glob still finds them, and sort *before* the live file so listing stays
    correct. Failure to rotate is not fatal: writing one oversized day is better
    than losing the record.
    """
    try:
        size = target_file.stat().st_size
    except OSError:
        return
    if size + incoming_bytes <= max_bytes:
        return
    stem = target_file.name[: -len(".jsonl")]
    index = 1
    while True:
        # Zero-pad so lexicographic listing stays chronological past .9.
        rotated = directory / f"{stem}.{index:03d}.jsonl"
        if not rotated.exists():
            break
        index += 1
    try:
        target_file.replace(rotated)
    except OSError:
        pass


class TraceSink:
    """Thread-safe, append-only JSONL storage for execution traces."""

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        *,
        max_bytes: int = DEFAULT_MAX_TRACE_BYTES,
        redact: bool = True,
    ) -> None:
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
        self.max_bytes = max(1, int(max_bytes))
        self.redact = bool(redact)

    @property
    def base_dir(self) -> Path:
        return self._dir

    def _trace_file(self, date_str: Optional[str] = None) -> Path:
        day = date_str or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        return self._dir / f"traces_{day}.jsonl"

    def _rotate_if_needed(self, target_file: Path, incoming_bytes: int) -> None:
        """Move today's file aside when the next line would cross the cap."""
        _rotate_file(self._dir, target_file, incoming_bytes, self.max_bytes)

    def save_trace(self, trace: TraceRecord) -> Path:
        """Append a completed trace to the daily JSONL trace log."""
        data = trace.to_dict()
        line = json.dumps(data, ensure_ascii=False) + "\n"
        if self.redact:
            line = redact_text(line)
        target_file = self._trace_file()
        with self._lock:
            self._rotate_if_needed(target_file, len(line.encode("utf-8")))
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


# -- Skill-Selection Record Sink --------------------------------------------

#: One record per task is a few KB, so a much smaller cap than a trace file.
DEFAULT_MAX_SKILL_RECORD_BYTES = 2 * 1024 * 1024
DEFAULT_SKILL_RECORD_RETENTION_DAYS = 30


class SkillSelectionSink:
    """Append-only JSONL store for one skill-selection record per task.

    Separate from :class:`TraceSink` because the two have different lifetimes
    and sizes: a trace carries every tool argument, a selection record carries
    the routing decision and its outcome. The ``trace_id`` field joins them.

    ``prefix`` selects the stream: ``skill_selections`` for routing decisions
    and ``skill_loads`` for model-initiated loads (see :class:`SkillLoadSink`).
    Both use the same directory and rotation rules, so one retention pass covers
    the pair and an operator has one place to look.
    """

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        *,
        prefix: str = "skill_selections",
        max_bytes: int = DEFAULT_MAX_SKILL_RECORD_BYTES,
        redact: bool = True,
        retain_query: bool = True,
        create: bool = True,
    ) -> None:
        if base_dir:
            self._dir = Path(base_dir).resolve()
        else:
            local_appdata = os.environ.get("LOCALAPPDATA")
            if local_appdata:
                self._dir = Path(local_appdata) / "BrahmaAI" / "skill_selections"
            else:
                self._dir = Path(".brahma") / "skill_selections"
        if create:
            self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.prefix = str(prefix or "skill_selections")
        self.max_bytes = max(1, int(max_bytes))
        self.redact = bool(redact)
        self.retain_query = bool(retain_query)

    @property
    def base_dir(self) -> Path:
        return self._dir

    def _record_file(self, date_str: Optional[str] = None) -> Path:
        day = date_str or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        return self._dir / f"{self.prefix}_{day}.jsonl"

    def save(self, record: Dict[str, Any]) -> Path:
        """Append one record; returns the file it landed in."""
        payload = dict(record)
        if not self.retain_query:
            payload.pop("query", None)
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        if self.redact:
            line = redact_text(line)
        target_file = self._record_file()
        with self._lock:
            _rotate_file(self._dir, target_file, len(line.encode("utf-8")), self.max_bytes)
            with open(target_file, "a", encoding="utf-8") as handle:
                handle.write(line)
        return target_file

    def list_records(
        self,
        *,
        since: Optional[float] = None,
        limit: int = 500,
    ) -> List[Dict[str, Any]]:
        """Recent records, oldest first, optionally filtered by epoch seconds."""
        collected: List[Dict[str, Any]] = []
        with self._lock:
            files = sorted(self._dir.glob(f"{self.prefix}_*.jsonl"))
            for fpath in files:
                try:
                    with open(fpath, "r", encoding="utf-8") as handle:
                        for line in handle:
                            if not line.strip():
                                continue
                            try:
                                data = json.loads(line)
                            except ValueError:
                                continue
                            if not isinstance(data, dict):
                                continue
                            if since is not None and float(data.get("ts_epoch") or 0.0) < since:
                                continue
                            collected.append(data)
                except OSError:
                    continue
        collected.sort(key=lambda item: float(item.get("ts_epoch") or 0.0))
        if limit and len(collected) > limit:
            collected = collected[-limit:]
        return collected

    def prune(self, keep_days: float = DEFAULT_SKILL_RECORD_RETENTION_DAYS) -> int:
        """Delete day-files older than ``keep_days``. Returns files removed.

        Retention is an explicit operator action (``skills audit --prune``), not
        a background timer: a silent deleter is indistinguishable from data loss.
        """
        cutoff = time.time() - max(1.0, float(keep_days)) * 86_400
        removed = 0
        with self._lock:
            for fpath in sorted(self._dir.glob(f"{self.prefix}_*.jsonl")):
                try:
                    if fpath.stat().st_mtime < cutoff:
                        fpath.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed


class SkillLoadSink(SkillSelectionSink):
    """Append-only JSONL store for model-initiated skill loads (design M5).

    A *load* is not a decision: the router already chose (or chose nothing), and
    this is the model reaching for a skill by name. Written to its own stream so
    a miss is visible even on a turn where nothing was injected — the case the
    selection record cannot describe, because it is only written when a skill
    was actually shortlisted. Both streams share a directory and a retention
    pass; ``trace_id`` joins them.
    """

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        *,
        max_bytes: int = DEFAULT_MAX_SKILL_RECORD_BYTES,
        redact: bool = True,
        retain_query: bool = True,
        create: bool = True,
    ) -> None:
        super().__init__(
            base_dir,
            prefix="skill_loads",
            max_bytes=max_bytes,
            redact=redact,
            retain_query=retain_query,
            create=create,
        )


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
        # A failed run emits ``error`` and then ``stop``; both finish the
        # record, but only the first may write it or every failure is logged
        # twice.
        self._saved = False

    def _persist(self) -> None:
        if self.sink is not None and not self._saved:
            self.sink.save_trace(self.record)
            self._saved = True

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
                self._persist()

            elif etype == "stop":
                reason = str(event.get("reason") or "completed")
                if self._current_turn_span and self._current_turn_span.end_time is None:
                    self._current_turn_span.finish(status="ok")
                status = "completed" if reason == "completed" else ("cancelled" if reason == "cancelled" else "error")
                self.record.finish(status=status, stop_reason=reason)
                self._persist()
