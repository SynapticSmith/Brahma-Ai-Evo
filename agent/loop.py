"""The agent loop: complete -> call tools -> observe -> repeat, until done.

The old text path ran every tool call and then took exactly one follow-up
(``main.py:_finish_tool_turn``), which means the model could never react to a
*second* observation. That single line is the difference between "assistant
with tools" and "agent".

Design constraints this loop takes seriously:

* **Bounded.** ``max_turns`` is not a suggestion. A model that will not stop
  must not bill the user forever.
* **Repeat-aware.** Identical calls are detected by name + canonicalised
  arguments. After ``repeat_limit`` the call is short-circuited with an
  instruction to change approach, and a turn made entirely of repeats ends the
  run. Prompt rules alone do not stop this failure mode.
* **Protocol-exact.** Every assistant ``tool_calls`` message is followed by
  exactly one ``tool`` message per call, in order. Compaction cuts only at a
  safe boundary (see :mod:`agent.context`).
* **Observable.** Each step is emitted as a dict, in order, whether or not a
  UI is attached. That stream is what the CLI's ``--json`` prints and what a
  future TUI or subagent coordinator subscribes to.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

if TYPE_CHECKING:
    from .trace import TraceCollector

from .context import ContextBudget, ConversationContext, SUMMARY_MARKER, truncate_tool_output
from .prompts import (
    MAX_SUBDIR_INSTRUCTION_CHARS,
    PATH_ARG_KEYS,
    build_system_prompt,
    load_directory_instructions,
)
from .retry import Cancelled, call_with_retry, is_transient
from .runtime import AgentRuntime, current as current_runtime, policy_note
from .session import AgentSession
from .spec import ToolSpec, all_specs, get as get_spec, lowercase_schema
from .subagent import (
    DEFAULT_BATCH_WORKERS,
    DEFAULT_SUBAGENT_TURNS,
    MAX_BATCH_TASKS,
    SUBAGENT_SYSTEM,
    clamp_batch_workers,
    clamp_turns,
    select_batch_tools,
    select_tools,
    tool_names,
)
from .usage import add_usage, extract_usage

Completer = Callable[[List[Dict[str, Any]], List[Dict[str, Any]]], Dict[str, Any]]
NativeDispatch = Callable[[str, Dict[str, Any]], str]
EventSink = Callable[[Dict[str, Any]], None]
Summarizer = Callable[[str], str]

STOP_COMPLETED = "completed"
STOP_MAX_TURNS = "max_turns"
STOP_REPEAT = "repeat_loop"
STOP_CANCELLED = "cancelled"
STOP_ERROR = "error"
STOP_PERMISSION = "permission_denied"
STOP_UNVERIFIED = "unverified_stop"
STOP_PLAN_INCOMPLETE = "plan_incomplete"

#: Written as the tool result for calls that were never dispatched because the
#: user cancelled. Every `tool_call` must get exactly one `tool` message or the
#: next request (and any resume) is rejected by OpenAI-compatible endpoints.
CANCELLED_TOOL_OUTPUT = "Not run: the run was cancelled before this call."


@dataclass
class AgentResult:
    text: str
    turns: int
    stop_reason: str
    messages: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    verified: bool = False
    usage: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.stop_reason == STOP_COMPLETED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "turns": self.turns,
            "stop_reason": self.stop_reason,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "verified": self.verified,
            "usage": self.usage,
        }


def _remember_turn_for_retrieval(text: str) -> None:
    """Hand the user's words to the tool bridge, when the host app has it.

    ``agent/`` must import with only pytest installed, so the bridge is imported
    lazily and a missing or broken bridge is never fatal to a turn.
    """
    try:
        from core.tool_bridge import set_turn_context

        set_turn_context(text)
    except Exception:  # noqa: BLE001 - retrieval context is optional
        pass


def _nested(first: Path, second: Path) -> bool:
    """True when one path is the other or sits inside it."""
    try:
        first.relative_to(second)
        return True
    except ValueError:
        pass
    try:
        second.relative_to(first)
        return True
    except ValueError:
        return False


def _effects_conflict(
    first: Tuple[str, Path], second: Tuple[str, Path]
) -> bool:
    """Whether two ``(kind, path)`` effects must keep model order.

    Reads never conflict with each other. Writes always conflict with each
    other, because the undo stack and every editor assume one writer. A read
    and a write conflict when the paths are equal or nested: a write inside a
    directory that `glob_files`/`grep_files` is walking would otherwise race
    the scan.

    Invariant S-03: Peer calls are fenced from local tools. Read-only peer calls
    to distinct nodes may overlap.
    """
    first_kind, first_path = first
    second_kind, second_path = second

    if first_kind == "peer" or second_kind == "peer":
        if first_kind == "peer" and second_kind == "peer":
            return first_path == second_path
        return True

    if first_kind == "read" and second_kind == "read":
        return False
    if first_kind == "write" and second_kind == "write":
        return True
    return _nested(first_path, second_path)


def _canonical_args(raw: Any) -> str:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return raw.strip()
    try:
        return json.dumps(raw or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(raw)


def _normalise_call(call: Mapping[str, Any], index: int) -> Dict[str, Any]:
    """Accept OpenAI, loose ``{name, args}`` and Gemini-ish shapes."""
    if not isinstance(call, Mapping):
        return {
            "id": f"call_{index}",
            "type": "function",
            "function": {"name": "unknown", "arguments": "{}"},
        }
    if "function" in call and isinstance(call.get("function"), Mapping):
        fn = dict(call["function"])
        name = str(fn.get("name") or call.get("name") or "unknown")
        arguments = fn.get("arguments", fn.get("args", {}))
    else:
        name = str(call.get("name") or "unknown")
        arguments = call.get("arguments", call.get("args", {}))
    if not isinstance(arguments, str):
        try:
            arguments = json.dumps(arguments or {}, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            arguments = "{}"
    return {
        "id": str(call.get("id") or f"call_{index}"),
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def normalise_assistant_message(response: Any, turn: int = 0) -> Dict[str, Any]:
    """Turn any provider's response into one OpenAI assistant message."""
    message: Mapping[str, Any]
    if response is None:
        message = {}
    elif isinstance(response, Mapping):
        if isinstance(response.get("choices"), list) and response["choices"]:
            message = response["choices"][0].get("message") or {}
        elif isinstance(response.get("message"), Mapping):
            message = response["message"]
        else:
            message = response
    else:  # pragma: no cover - a non-mapping completer result is a bug
        message = {"content": str(response)}

    out: Dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
    }
    calls = message.get("tool_calls") or []
    if not calls and message.get("function_call"):
        calls = [message["function_call"]]
    if isinstance(calls, Mapping):
        calls = [calls]
    if calls:
        out["tool_calls"] = [
            _normalise_call(call, turn * 100 + index)
            for index, call in enumerate(calls)
        ]
    return out


class AgentLoop:
    """One bounded agent run."""

    def __init__(
        self,
        complete: Completer,
        *,
        tools: Optional[Sequence[Any]] = None,
        native_dispatch: Optional[NativeDispatch] = None,
        runtime: Optional[AgentRuntime] = None,
        system: Optional[str] = None,
        session: Optional[AgentSession] = None,
        max_turns: int = 40,
        repeat_limit: int = 3,
        max_parallel_tools: int = 4,
        retry_attempts: int = 3,
        retry_base_delay: float = 0.8,
        retry_max_delay: float = 8.0,
        budget: Optional[ContextBudget] = None,
        on_event: Optional[EventSink] = None,
        cancel: Optional[threading.Event] = None,
        summarizer: Optional[Summarizer] = None,
        context: Optional[ConversationContext] = None,
        stream: bool = False,
        allow_subagents: bool = True,
        batch_readonly: bool = False,
        retrieval_budget: Optional[int] = None,
        retrieval: Optional[str] = None,
        retrieve: bool = True,
        skill_budget: Optional[int] = None,
        skill: Optional[str] = None,
        include_skills: bool = True,
        max_tool_calls: Optional[int] = None,
        trace_collector: Optional[TraceCollector] = None,
        tracer: Optional[TraceCollector] = None,
        verify_policy: Optional[Any] = None,
        plan: Optional[Any] = None,
        skill_log: Optional[Any] = None,
        skill_load_log: Optional[Any] = None,
    ):
        self._complete = complete
        self._native_dispatch = native_dispatch
        self._runtime = runtime or current_runtime()
        self._tracer = trace_collector or tracer
        if self._tracer is not None:
            user_sink = on_event

            def _chained_sink(event: Dict[str, Any]) -> None:
                if self._tracer is not None:
                    try:
                        self._tracer(event)
                    except Exception:
                        pass
                if user_sink is not None:
                    user_sink(event)

            self._on_event = _chained_sink
        else:
            self._on_event = on_event
        self._tools: List[Any] = (
            list(tools)
            if tools is not None
            else [spec for spec in all_specs() if "desktop" not in spec.tags]
        )
        if tools is None:
            # A profile that denies ``network`` must not be advertised a web
            # tool it can only be denied on: the API's ``reader``/``chat``
            # presets stay visibly offline, while ``worker``/``operator`` and
            # the CLI keep both tools. Other classes keep their historical
            # surface (advertised, then decided at dispatch).
            self._tools = [
                item
                for item in self._tools
                if getattr(item, "tool_class", "") != "network"
                or self._network_allowed(item)
            ]
        self._session = session
        self.max_turns = max(1, int(max_turns))
        self.max_tool_calls = int(max_tool_calls) if max_tool_calls is not None else None
        self.repeat_limit = max(1, int(repeat_limit))
        #: 1 disables parallel dispatch. Reads and writes may overlap only when
        #: the scheduler can prove they touch different paths; see
        #: ``_effects_conflict``.
        self.max_parallel_tools = max(1, int(max_parallel_tools))
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self.retry_max_delay = max(0.0, float(retry_max_delay))
        # Default to the runtime's event so ``runtime.cancel()`` always reaches
        # the loop, even when a caller forgets to pass the event explicitly.
        self._cancel = cancel if cancel is not None else self._runtime.cancel_event
        self._summarizer = summarizer
        #: Streaming is opt-in. A completer advertises support with a callable
        #: ``stream`` attribute; without one the run still works, just without
        #: incremental output (announced once by ``stream_fallback``).
        self.stream = bool(stream)
        self._streamer = getattr(complete, "stream", None)
        if not callable(self._streamer):
            self._streamer = None
        #: Set by :meth:`_on_delta` when the current attempt produced output.
        #: A retry needs a ``reset`` so a UI drops the partial text.
        self._delta_sent = False
        self._turn = 0
        #: A child loop is built by :meth:`_execute_child_subagent`; its task is
        #: not the user's turn, so it must not replace the parent's retrieval
        #: context (see :func:`_remember_turn_for_retrieval`).
        self._is_child = False
        self._stream_fallback_reported = False
        self._events: List[Dict[str, Any]] = []
        self._repeat_counts: Dict[str, int] = {}
        self._tool_calls_made = 0
        self._retries = 0
        #: A child loop is constructed with this false, so even a surface that
        #: somehow still advertises `run_subagent` cannot recurse.
        self._allow_subagents = bool(allow_subagents)
        #: Set on children spawned by `run_subagent_batch` (O-2): enforces
        #: read-only, workspace-confined, approval-free execution in the shared
        #: runtime.
        self._batch_readonly = bool(batch_readonly)
        self._subagent_runs = 0
        self._subagent_lock = threading.Lock()
        #: The most recent child report. Used only as a fallback when the
        #: parent is cancelled after a child finished but before the model
        #: turned that tool result into an answer; without it the API would
        #: return an empty ``cancelled`` body even though real work happened.
        self._last_subagent_report = ""
        #: Sum of provider-reported counts. Empty until a response carries
        #: usage; ``estimated_tokens`` stays as the local fallback.
        self._tokens: Dict[str, int] = {}
        self._usage_reports = 0
        #: Absolute paths of directory instruction files already in the system
        #: prompt, and the characters still available for new ones. Per run:
        #: a resumed session starts from a freshly built prompt.
        self._seen_instruction_files: Set[str] = set()
        self._instruction_budget = MAX_SUBDIR_INSTRUCTION_CHARS
        #: Automatic second-brain retrieval is on by default. ``retrieve=False``
        #: exists for hosts that already injected their own context, and
        #: ``retrieval_budget`` caps the rendered block (a 1.7B model on an 8K
        #: window needs a much smaller block than a 200K Gemini session).
        #: ``retrieval`` is a block the host already rendered for its first
        #: model call; reusing it avoids a second index query and keeps the
        #: telemetry event identical.
        self._retrieve = bool(retrieve)
        self._retrieval_budget = retrieval_budget
        self._retrieval_override = str(retrieval or "").strip() or None
        self._retrieval_sources: List[str] = []
        self._include_skills = bool(include_skills)
        self._skill_budget = skill_budget
        self._skill_override = str(skill or "").strip() or None
        #: The plan compiled for this run, kept so the end-of-task record can
        #: describe the choice that was actually injected.
        self._turn_plan: Optional[Any] = None
        self._turn_query = ""
        #: Optional ``SkillSelectionSink``. Without one the record still rides
        #: on the trace attributes, so a run is never unjoinable.
        self._skill_log = skill_log
        #: Optional ``SkillLoadSink`` for model-initiated loads (design M5).
        #: Written even when nothing was injected, which is why it is a
        #: separate stream from the selection record.
        self._skill_load_log = skill_load_log

        if context is not None:
            self._context = context
            if system and not self._context.system:
                self._context.system = system
        else:
            self._context = ConversationContext(
                system=system,
                budget=budget or ContextBudget(),
            )
            if self._session is not None:
                try:
                    stored = self._session.messages()
                    stored_summary = self._session.get_summary()
                    clean_summary = str(stored_summary or "").strip()
                    has_marker = any(
                        m.get("role") == "user"
                        and str(m.get("content") or "").startswith(SUMMARY_MARKER)
                        for m in stored
                    )
                    if clean_summary and not has_marker:
                        self._context.append(
                            {"role": "user", "content": f"{SUMMARY_MARKER}\n{clean_summary}"}
                        )
                        self._context.summary = clean_summary
                    for m in stored:
                        if m.get("role") == "system":
                            continue
                        self._context.append(dict(m))
                        if not self._context.summary and m.get("role") == "user":
                            c = str(m.get("content") or "")
                            if c.startswith(SUMMARY_MARKER):
                                self._context.summary = c[len(SUMMARY_MARKER):].strip()
                except Exception:
                    pass

        self._verify_policy = verify_policy
        if (
            self._verify_policy is None
            and hasattr(self._runtime, "policy")
            and hasattr(self._runtime.policy, "verify")
        ):
            from .verify import VerifyPolicy

            self._verify_policy = VerifyPolicy.from_dict(self._runtime.policy.verify)

        self._plan = plan
        if self._plan is None and self._session is not None and hasattr(self._session, "load_plan_steps"):
            try:
                steps = self._session.load_plan_steps()
                if steps:
                    from .plan import Plan

                    self._plan = Plan(session_id=self._session.id, steps=steps)
            except Exception:
                pass

        self._mutations_occurred = False
        self._last_verdict: Optional[Any] = None

    # -- plumbing ----------------------------------------------------------

    @property
    def tracer(self) -> Optional[Any]:
        return self._tracer

    @property
    def context(self) -> ConversationContext:
        return self._context

    def _emit(self, event_type: str, **data: Any) -> None:
        event = {"type": event_type, **data}
        with self._subagent_lock:
            if len(self._events) < 4000:
                self._events.append(event)
        if self._session is not None:
            self._session.log_event(event_type, data)
        sink = self._on_event or self._runtime.event_sink
        if sink is not None:
            try:
                sink(event)
            except Exception:
                pass

    def _openai_schemas(self) -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        for item in self._tools:
            if isinstance(item, ToolSpec):
                schemas.append(item.openai())
            elif isinstance(item, str):
                spec = get_spec(item)
                if spec is not None:
                    schemas.append(spec.openai())
            elif isinstance(item, Mapping):
                if "function" in item:
                    schemas.append(dict(item))
                else:
                    # A Gemini declaration (``name`` at the top level). A
                    # registered harness tool converts through its spec;
                    # a native/action tool has no spec here, so convert the
                    # schema shape directly rather than sending uppercase
                    # types to an OpenAI endpoint.
                    name = str(item.get("name") or "")
                    if not name:
                        continue
                    spec = get_spec(name)
                    if spec is not None:
                        schemas.append(spec.openai())
                    else:
                        schemas.append(
                            {
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "description": str(item.get("description") or ""),
                                    "parameters": lowercase_schema(
                                        item.get("parameters")
                                        or {"type": "object", "properties": {}}
                                    ),
                                },
                            }
                        )
        return schemas

    def _cancelled(self) -> bool:
        return self._cancel is not None and self._cancel.is_set()

    def _network_allowed(self, item: Any) -> bool:
        """True unless the runtime policy explicitly denies ``network``.

        An ``ask`` still advertises the tool (the CLI/HUD can approve it); only
        a hard ``deny`` hides it. A broken policy fails open here because the
        dispatch boundary is still the real gate.
        """
        name = str(getattr(item, "name", "") or "")
        if not name:
            return False
        try:
            decision = self._runtime.policy.evaluate(
                name, {}, tool_class="network", headless=False
            )
        except Exception:
            return True
        return decision.decision != "deny"

    def _request(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
    ) -> Any:
        """One model call, retried while the failure looks transient.

        Classification lives in :mod:`agent.retry`; this method only turns a
        retry into an event so the user can see the run is stalling on the
        provider rather than thinking.

        With ``stream=True`` and a completer that exposes ``stream``, the call
        goes through the streaming transport instead. A failure *after* the
        first delta means the UI is holding text that will no longer arrive, so
        the retry emits a ``delta`` of kind ``reset`` first; the assembler
        starts from empty on the next attempt.
        """

        def on_retry(attempt: int, delay: float, exc: BaseException) -> None:
            self._retries += 1
            if self._delta_sent:
                self._delta_sent = False
                self._emit("delta", turn=self._turn, kind="reset", reason="retry")
            self._emit(
                "retry",
                attempt=attempt,
                delay_s=round(delay, 2),
                error=f"{type(exc).__name__}: {exc}"[:300],
            )

        streamer = self._streamer if self.stream else None
        if streamer is not None:
            def attempt() -> Any:
                self._delta_sent = False
                active = self._streamer
                if active is None:
                    # A previous attempt already fell back; stay plain for the
                    # rest of this run (including a retry after this point).
                    return self._complete(messages, tools)
                try:
                    return active(messages, tools, self._on_delta)
                except Exception as exc:
                    # A stream that fails before its first delta is usually a
                    # server that rejects `stream: true` (a 400, not a flaky
                    # network). Transient errors belong to the retry wrapper;
                    # a terminal one gets one plain request, then this run
                    # stays on the non-streaming path.
                    if self._delta_sent or is_transient(exc):
                        raise
                    self._streamer = None
                    self._emit(
                        "stream_fallback",
                        reason=(
                            "streaming failed before any output; using a single "
                            f"request ({type(exc).__name__}: {exc})"
                        )[:300],
                    )
                    return self._complete(messages, tools)
        else:
            def attempt() -> Any:
                return self._complete(messages, tools)

        return call_with_retry(
            attempt,
            attempts=self.retry_attempts,
            base_delay=self.retry_base_delay,
            max_delay=self.retry_max_delay,
            cancel=self._cancel,
            on_retry=on_retry,
        )

    def _on_delta(self, delta: Any) -> None:
        """Forward one incremental update as a ``delta`` event."""
        if not isinstance(delta, Mapping):
            return
        kind = delta.get("kind")
        self._delta_sent = True
        if kind == "text":
            text = delta.get("text")
            if isinstance(text, str) and text:
                self._emit("delta", turn=self._turn, kind="text", text=text)
            return
        if kind == "tool_call":
            payload: Dict[str, Any] = {"turn": self._turn, "kind": "tool_call"}
            index = delta.get("index")
            if isinstance(index, int):
                payload["index"] = index
            for key in ("id", "name", "arguments"):
                value = delta.get(key)
                if isinstance(value, str) and value:
                    payload[key] = value
            self._emit("delta", **payload)

    def _announce_stream(self) -> None:
        if not self.stream or self._stream_fallback_reported:
            return
        if self._streamer is None:
            self._stream_fallback_reported = True
            self._emit(
                "stream_fallback",
                reason="this provider has no streaming transport; using a single request",
            )

    def _call_effect(
        self, item: Dict[str, Any]
    ) -> Optional[Tuple[str, Path]]:
        """The ``(kind, path)`` a call touches, or ``None`` when it is a fence.

        Only ``read`` and ``write`` calls the policy would allow without asking
        are schedulable. A write with no resolvable path, `exec`, a native call,
        a denied call, an approval prompt and a repeated call are all fences
        (a repeat is skipped by the caller): they run alone in model order and
        everything after them waits, so a banner never stacks and a command
        never interleaves with a file change.

        A blank read path resolves to the workspace, which is exactly what
        `glob_files`/`grep_files` mean by "scan here".
        """
        if self.max_parallel_tools <= 1:
            return None
        if item["attempts"] > self.repeat_limit:
            return None
        spec = get_spec(item["name"])
        if spec is None or spec.classifier is not None or spec.tool_class not in ("read", "write", "network"):
            return None
        _, decision = self._runtime.decide(
            item["name"], item["args"], batch_readonly=self._batch_readonly
        )
        if not decision.allowed:
            return None
        if spec.name.startswith("agent_"):
            # Invariant S-03: Read-only peer queries overlap; mutating peer calls are fenced.
            if spec.tool_class in ("network", "read") and not spec.requires_approval:
                parts = spec.name.split("_")
                peer_node = parts[1] if len(parts) >= 3 else "default"
                return ("peer", Path(f"//peer/{peer_node}"))
            return None
        if spec.tool_class == "network":
            return None
        if item["name"] in ("glob_files", "grep_files") and ".." in str(
            item["args"].get("pattern") or ""
        ):
            return None
        raw_paths = [
            val
            for key in PATH_ARG_KEYS
            if isinstance((val := item["args"].get(key)), str) and val.strip()
        ]
        if len(raw_paths) > 1:
            return None
        raw = raw_paths[0] if raw_paths else None
        if spec.tool_class == "write" and (
            not isinstance(raw, str) or not raw.strip()
        ):
            return None
        return (spec.tool_class, self._runtime.context().resolve_path(raw))

    def _repeat_output(self, item: Dict[str, Any]) -> str:
        return (
            f"Identical call skipped: '{item['name']}' with these arguments has already "
            f"run {item['attempts'] - 1} times. Change the arguments or the approach, "
            "or explain to the user why you are blocked."
        )

    def _run_one(self, item: Dict[str, Any]) -> str:
        """Dispatch one prepared call. Never raises: the loop owns the boundary."""
        item["ran"] = True
        extra: Dict[str, Any] = {}
        if self._allow_subagents:
            extra["subagent_runner"] = self._run_subagent
            extra["subagent_batch_runner"] = self._run_subagent_batch
        if self._batch_readonly:
            extra["batch_readonly"] = True
        if self._plan is not None:
            extra["plan"] = self._plan
        if self._session is not None:
            extra["session"] = self._session
        try:
            res = self._runtime.execute(
                item["name"],
                item["args"],
                native_dispatch=self._native_dispatch,
                call_id=item["call"]["id"],
                extra=extra or None,
            )
            if "plan" in extra and self._plan is None:
                self._plan = extra["plan"]
            if "last_verdict" in extra:
                self._last_verdict = extra["last_verdict"]

            spec = get_spec(item["name"])
            tool_cls = (
                spec.class_for(item.get("args") or {})
                if spec is not None
                else ""
            )
            if tool_cls in ("write", "exec") and not res.startswith("Permission denied") and not res.startswith("Tool '"):
                self._mutations_occurred = True

            return res
        except Exception as exc:  # noqa: BLE001 - a worker must not kill the turn
            return f"Tool '{item['name']}' failed: {type(exc).__name__}: {exc}"

    def _next_subagent_label(self) -> str:
        with self._subagent_lock:
            self._subagent_runs += 1
            return f"subagent-{self._subagent_runs}"

    def _execute_child_subagent(
        self,
        *,
        label: str,
        task: str,
        context_text: str,
        chosen: Sequence[Any],
        turns: int,
        batch_readonly: bool = False,
        template: str = "",
        template_name: str = "",
    ) -> str:
        prompt = task if not context_text else f"{task}\n\nContext:\n{context_text}"
        names = tool_names(chosen)
        self._emit(
            "subagent_start",
            subagent=label,
            task=task,
            tools=names,
            max_turns=turns,
            template=template_name or None,
        )

        extra = SUBAGENT_SYSTEM
        if template:
            extra = (
                f"{SUBAGENT_SYSTEM}\n\n"
                f"You are running the '{template_name or 'specialist'}' template "
                "from the subagent library. Its tool names may not match this "
                "runtime's names; use the equivalent tool from the surface you "
                "were given, and ignore any instruction that requires a tool you "
                "do not have.\n\n"
                "--- template ---\n"
                f"{template}"
            )

        child = AgentLoop(
            self._complete,
            tools=chosen,
            native_dispatch=self._native_dispatch,
            runtime=self._runtime,
            system=build_system_prompt(
                self._runtime.workspace,
                permission_note=policy_note(self._runtime),
                extra=extra,
                include_subagents=False,
                include_brain=False,
                include_federation=False,
            ),
            session=None,
            max_turns=turns,
            repeat_limit=self.repeat_limit,
            max_parallel_tools=self.max_parallel_tools,
            retry_attempts=self.retry_attempts,
            retry_base_delay=self.retry_base_delay,
            retry_max_delay=self.retry_max_delay,
            on_event=lambda event: self._emit(
                "subagent_event", subagent=label, event=event
            ),
            cancel=self._cancel,
            summarizer=self._summarizer,
            stream=False,
            allow_subagents=False,
            batch_readonly=batch_readonly,
        )
        child._is_child = True

        try:
            result = child.run(prompt)
        except Exception as exc:  # noqa: BLE001 - a child must not kill the parent
            self._emit(
                "subagent_end",
                subagent=label,
                stop_reason=STOP_ERROR,
                turns=0,
                chars=0,
            )
            return f"Subagent failed: {type(exc).__name__}: {exc}"

        self._emit(
            "subagent_end",
            subagent=label,
            stop_reason=result.stop_reason,
            turns=result.turns,
            chars=len(result.text),
        )

        head = (
            f"Subagent {label} {result.stop_reason} after {result.turns} "
            f"turn(s); tools: {', '.join(names)}."
        )
        body = result.text.strip()
        if result.stop_reason != STOP_COMPLETED:
            body += (
                "\n\n[The subagent did not finish. Treat this report as "
                "partial and verify the work before relying on it.]"
            )
        report = head + (("\n\n" + body) if body else "")
        self._last_subagent_report = report
        return report

    def _run_subagent(self, args: Dict[str, Any]) -> str:
        """Run one bounded child loop and return its report as tool text.

        The child shares this loop's completer, runtime (policy, approver,
        workspace, cancel event) and retry settings, but gets a fresh
        conversation, a filtered tool surface, its own turn budget and no
        session row. Its events are relayed under ``subagent_event`` so a host
        can still show what it is doing without mixing its turns into the
        parent transcript.
        """
        task = str(args.get("task") or "").strip()
        if not task:
            return "Not run: run_subagent needs a non-empty 'task'."
        template = ""
        template_name = ""
        requested_type = str(args.get("subagent_type") or "").strip()
        if requested_type:
            from . import subagent_library

            catalog = subagent_library.load()
            if catalog is None:
                return (
                    "error: no subagent template library is installed, so "
                    f"{requested_type!r} cannot be loaded. Call list_subagents "
                    "for the built-in delegation options, or omit "
                    "'subagent_type' to run a generic child."
                )
            entry = catalog.get(requested_type)
            if entry is None:
                closest = ", ".join(
                    f"`{hit.slug}`" for hit in catalog.suggest(requested_type)
                ) or "none"
                return (
                    f"error: no subagent template named {requested_type!r}. "
                    f"Closest slugs: {closest}. Call list_subagents with a "
                    "keyword to search, or omit 'subagent_type' for a generic "
                    "child."
                )
            try:
                template = catalog.template_body(entry)
            except OSError as exc:
                return (
                    f"error: template {entry.slug!r} could not be read: {exc}. "
                    "Omit 'subagent_type' to run a generic child."
                )
            template_name = entry.slug
        requested = args.get("tools")
        if isinstance(requested, str):
            requested = [requested]
        if not isinstance(requested, (list, tuple)):
            requested = None
        chosen = select_tools(self._tools, requested)
        if not chosen:
            return (
                "Not run: none of the requested tools are available to this "
                "agent. Omit 'tools' for the read-only set, or request names "
                "from this agent's own tool surface."
            )

        turns = clamp_turns(args.get("max_turns"), DEFAULT_SUBAGENT_TURNS)
        context_text = str(args.get("context") or "").strip()
        label = self._next_subagent_label()
        return self._execute_child_subagent(
            label=label,
            task=task,
            context_text=context_text,
            chosen=chosen,
            turns=turns,
            batch_readonly=False,
            template=template,
            template_name=template_name,
        )

    def _run_subagent_batch(self, args: Dict[str, Any]) -> str:
        """Run 1..MAX_BATCH_TASKS read-only, workspace-confined child loops in parallel (O-2)."""
        from concurrent.futures import ThreadPoolExecutor

        raw_tasks = args.get("tasks")
        if not isinstance(raw_tasks, (list, tuple)) or not raw_tasks:
            return "Not run: run_subagent_batch needs a non-empty 'tasks' list."
        if len(raw_tasks) > MAX_BATCH_TASKS:
            return (
                f"Not run: run_subagent_batch accepts at most {MAX_BATCH_TASKS} "
                f"tasks per call (got {len(raw_tasks)})."
            )

        shared_context = str(args.get("context") or "").strip()
        shared_tools = args.get("tools")
        if isinstance(shared_tools, str):
            shared_tools = [shared_tools]
        if not isinstance(shared_tools, (list, tuple)):
            shared_tools = None
        shared_turns = clamp_turns(args.get("max_turns"), DEFAULT_SUBAGENT_TURNS)

        prepared: List[Dict[str, Any]] = []
        for index, entry in enumerate(raw_tasks, start=1):
            if isinstance(entry, str):
                task_text = entry.strip()
                item_context = ""
                item_tools = shared_tools
                item_turns = shared_turns
            elif isinstance(entry, Mapping):
                task_text = str(entry.get("task") or "").strip()
                item_context = str(entry.get("context") or "").strip()
                raw_item_tools = entry.get("tools", shared_tools)
                if isinstance(raw_item_tools, str):
                    item_tools = [raw_item_tools]
                elif isinstance(raw_item_tools, (list, tuple)):
                    item_tools = list(raw_item_tools)
                else:
                    item_tools = None
                item_turns = clamp_turns(entry.get("max_turns", shared_turns), shared_turns)
            else:
                return f"Not run: run_subagent_batch task #{index} must be a string or an object."

            if not task_text:
                return f"Not run: run_subagent_batch task #{index} needs a non-empty 'task'."

            chosen = select_batch_tools(self._tools, item_tools)
            if not chosen:
                return (
                    f"Not run: none of the requested read-only workspace tools are "
                    f"available for task #{index}. Omit 'tools' for the default "
                    "read-only set, or request only read-class workspace tools."
                )

            combined_ctx = "\n\n".join(p for p in (shared_context, item_context) if p)
            prepared.append(
                {
                    "index": index,
                    "task": task_text,
                    "context": combined_ctx,
                    "chosen": chosen,
                    "turns": item_turns,
                }
            )

        for spec_item in prepared:
            spec_item["label"] = self._next_subagent_label()

        workers = min(
            len(prepared),
            clamp_batch_workers(args.get("max_concurrency"), DEFAULT_BATCH_WORKERS),
        )
        self._emit(
            "subagent_batch_start",
            count=len(prepared),
            max_concurrency=workers,
            subagents=[p["label"] for p in prepared],
        )

        def _run_spec(spec_item: Dict[str, Any]) -> str:
            if self._cancelled():
                return (
                    f"Subagent {spec_item['label']} cancelled before start: "
                    f"{CANCELLED_TOOL_OUTPUT}"
                )
            return self._execute_child_subagent(
                label=spec_item["label"],
                task=spec_item["task"],
                context_text=spec_item["context"],
                chosen=spec_item["chosen"],
                turns=spec_item["turns"],
                batch_readonly=True,
            )

        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="brahma-subagent-batch"
        ) as pool:
            futures = [pool.submit(_run_spec, spec_item) for spec_item in prepared]
            reports = [future.result() for future in futures]

        self._emit(
            "subagent_batch_end",
            count=len(prepared),
            subagents=[p["label"] for p in prepared],
        )

        sections = [
            f"=== Task {spec_item['index']} ({spec_item['label']}): {spec_item['task']} ===\n{report}"
            for spec_item, report in zip(prepared, reports)
        ]
        return f"Subagent batch completed ({len(prepared)} task(s)):\n\n" + "\n\n".join(sections)

    def _run_group(self, group: List[Tuple[Dict[str, Any], Tuple[str, Path]]]) -> None:
        """Run one fence-free group, starting each call as its blockers finish.

        ``group`` is a list of ``(item, effect)`` pairs. A call may start once
        every *earlier* call it conflicts with has completed, so a read of a
        file waits for an earlier write to it and a write waits for an earlier
        read of the same file, while unrelated calls overlap. Model order is
        preserved on conflict; results always land on their own item, so the
        transcript stays in submission order.

        Cancellation stops new submissions but drains everything already
        running. Unscheduled items keep ``output=None`` and the caller writes
        the cancelled filler, exactly as before.
        """
        from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

        workers = max(1, min(self.max_parallel_tools, len(group)))
        done: Set[int] = set()
        running: Dict[Any, int] = {}
        pending = list(range(len(group)))
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="brahma-tool"
        ) as pool:
            while pending or running:
                if not self._cancelled():
                    still_pending: List[int] = []
                    for index in pending:
                        blocked = any(
                            other not in done
                            and _effects_conflict(group[other][1], group[index][1])
                            for other in range(index)
                        )
                        if blocked:
                            still_pending.append(index)
                        else:
                            running[pool.submit(self._run_one, group[index][0])] = index
                    pending = still_pending
                if not running:
                    break
                finished, _ = wait(list(running), return_when=FIRST_COMPLETED)
                for future in finished:
                    index = running.pop(future)
                    group[index][0]["output"] = future.result()
                    done.add(index)

    def _dispatch_calls(self, parsed: List[Dict[str, Any]]) -> None:
        """Run calls in model order, overlapping the ones that can overlap.

        The unit is a call, not a segment. A read and a write may run together
        when their paths are provably unrelated; writes never overlap each
        other; a read and a write of the same path (or a write inside a scanned
        directory) keep model order so a read cannot observe a half-written
        file. `exec`, native calls, approvals, denials, writes with no path and
        repeats are fences: everything before them finishes, then they run
        alone, then the scheduler resumes.

        Outputs land on their own items, so the caller's transcript loop still
        emits one `tool` message per call in model order.
        """
        group: List[Tuple[Dict[str, Any], Tuple[str, Path]]] = []
        for item in parsed:
            if item["attempts"] > self.repeat_limit:
                item["output"] = self._repeat_output(item)
                continue
            effect = self._call_effect(item)
            if effect is not None:
                group.append((item, effect))
                continue
            if group:
                self._run_group(group)
                group = []
                if self._cancelled():
                    return
            if self._cancelled():
                return
            item["output"] = self._run_one(item)
        if group:
            self._run_group(group)

    def _persist(self, message: Dict[str, Any]) -> None:
        if self._session is not None:
            try:
                self._session.append_message(message)
            except Exception:
                pass

    def _load_directory_instructions(self, parsed: List[Dict[str, Any]]) -> None:
        """Append instructions for the directories this turn actually touched.

        Paths come from the tool arguments, not a recursive scan: reading
        ``packages/foo/app.py`` pulls in ``packages/AGENTS.md`` and
        ``packages/foo/AGENTS.md`` and nothing else. The text is appended to the
        **system prompt**, which keeps the OpenAI tool protocol untouched and
        means the next request sees the rules without a synthetic user message
        or a second copy in the transcript. A file is never loaded twice, and a
        per-run character budget stops a wander through a large tree from
        crowding the window.
        """
        if self._instruction_budget <= 0:
            return
        raw_paths = [
            value
            for item in parsed
            for key, value in item["args"].items()
            if key in PATH_ARG_KEYS and isinstance(value, str) and value.strip()
        ]
        if not raw_paths:
            return
        found = load_directory_instructions(
            self._runtime.workspace,
            raw_paths,
            seen=self._seen_instruction_files,
            budget=self._instruction_budget,
        )
        if not found.text:
            return
        self._instruction_budget = max(0, self._instruction_budget - len(found.text))
        ctx = self._context
        ctx.system = f"{ctx.system}\n\n{found.text}" if ctx.system else found.text
        self._emit(
            "instructions",
            loaded=list(found.labels),
            chars=len(found.text),
            truncated=found.truncated,
        )

    # -- run ---------------------------------------------------------------

    def _prepare_retrieval(self, prompt: Optional[str]) -> None:
        """Inject the user's curated notes and JIT task skills before the model sees the turn (§9, §10).

        Retrieval and skills are computed once per run, not once per model call: the user
        turn stays at the end of the transcript while tools run, so re-querying
        it would only rebuild the same block and pay for another embedding.
        """
        if not self._retrieve or self._is_child:
            return
        block = self._retrieval_override
        self._retrieval_override = None

        skill_block = self._skill_override
        self._skill_override = None

        text = str(prompt or "").strip()
        if not text:
            for message in reversed(self._context.messages):
                if message.get("role") == "user":
                    text = str(message.get("content") or "").strip()
                    break

        plan = None
        if block is None and skill_block is None:
            if not text:
                return
            try:
                from .turn_context import compile_turn_context

                kb = self._retrieval_budget if self._retrieval_budget is not None else 2_400
                sb = self._skill_budget if self._skill_budget is not None else 1_200
                plan = compile_turn_context(
                    text,
                    workspace=self._runtime.workspace,
                    knowledge_budget=kb,
                    skill_budget=sb,
                    include_skills=self._include_skills,
                )
                block = plan.knowledge_block
                skill_block = plan.skill_block
            except Exception:  # noqa: BLE001 - retrieval/skills must never fail a turn
                try:
                    from .second_brain import context_for_turn

                    block = context_for_turn(text, budget=self._retrieval_budget)
                except Exception:
                    block = ""
                skill_block = ""
        elif block is None:
            if text:
                try:
                    from .second_brain import context_for_turn

                    block = context_for_turn(text, budget=self._retrieval_budget)
                except Exception:
                    block = ""
            else:
                block = ""

        self._turn_plan = plan
        self._turn_query = text
        self._context.set_retrieval(block or "")
        if skill_block:
            self._context.set_skill(skill_block)

        if plan and (plan.selected_skill or plan.selected_script):
            sk_name = plan.selected_skill["name"] if plan.selected_skill else ""
            sc_name = plan.selected_script["name"] if plan.selected_script else ""
            self._emit(
                "skill_injected",
                skill=sk_name,
                script=sc_name,
                domain=plan.domain or "",
                chars=len(skill_block),
            )
        elif skill_block:
            self._emit(
                "skill_injected",
                skill="",
                script="",
                domain="",
                chars=len(skill_block),
            )

        if not block:
            return
        try:
            from .second_brain import retrieval_metadata

            meta = retrieval_metadata(block)
            self._retrieval_sources = meta.get("sources", [])
            kinds = meta.get("kinds", {})
            domains = meta.get("domains", [])
        except Exception:  # noqa: BLE001 - telemetry is not worth a failure
            self._retrieval_sources = []
            kinds = {}
            domains = []
        self._emit(
            "retrieval",
            chars=len(block),
            sources=self._retrieval_sources[:8],
            kinds=kinds,
            domains=domains,
        )

    _prepare_turn_context = _prepare_retrieval

    def run(
        self,
        prompt: Optional[str] = None,
        *,
        first_response: Optional[Any] = None,
    ) -> AgentResult:
        # Events describe *this* run: the result's `events`, the end-of-task
        # record's tool evidence and the model-load scan all read them, so a
        # second `run()` on the same loop must not inherit the first one's.
        self._events.clear()
        ctx = self._context
        if prompt:
            message = ctx.append_user(prompt)
            self._persist(message)
            if not self._is_child:
                _remember_turn_for_retrieval(prompt)
        self._prepare_retrieval(prompt)
        self._announce_stream()
        if self._session is not None:
            self._emit("session_start", session_id=self._session.id, cwd=str(self._runtime.workspace))

        turn = 0
        stop_reason = STOP_MAX_TURNS
        final_text = ""

        try:
            for turn in range(1, self.max_turns + 1):
                if self._cancelled():
                    stop_reason = STOP_CANCELLED
                    break

                if ctx.needs_compaction():
                    before = len(ctx.messages)
                    summary = ctx.compact(self._summarizer)
                    if summary:
                        self._emit(
                            "compaction",
                            dropped=before - len(ctx.messages) + 1,
                            summary_chars=len(summary),
                            total_chars=ctx.chars,
                        )
                        if self._session is not None:
                            try:
                                self._session.replace_messages(ctx.messages)
                                self._session.set_summary(summary)
                            except Exception:
                                pass

                self._emit("turn_start", turn=turn, chars=ctx.chars)
                self._turn = turn

                if first_response is not None:
                    response, first_response = first_response, None
                else:
                    response = self._request(ctx.as_payload(), self._openai_schemas())

                message = normalise_assistant_message(response, turn)
                reported = extract_usage(response)
                if reported:
                    add_usage(self._tokens, reported)
                    self._usage_reports += 1
                ctx.append(message)
                self._persist(message)

                calls = message.get("tool_calls") or []
                self._emit(
                    "assistant",
                    turn=turn,
                    text=message.get("content") or "",
                    tool_calls=[
                        {
                            "name": call["function"]["name"],
                            "arguments": call["function"]["arguments"],
                        }
                        for call in calls
                    ],
                    **({"usage": reported} if reported else {}),
                )

                # Interactive models often narrate first and call tools on the
                # next turn. Only a turn with no calls is a candidate for
                # completion.
                if not calls:
                    # 1. Plan Gate (C3): If an active plan has unresolved steps, reject early completion
                    if self._plan is not None and len(self._plan.steps) > 0 and not self._plan.is_completed():
                        unresolved = [s.id for s in self._plan.unresolved_steps()]
                        if turn < self.max_turns and not self._cancelled():
                            self._emit("plan_gate_rejected", turn=turn, unresolved=unresolved)
                            gate_msg = (
                                f"[Plan Gate] Cannot complete task: plan step(s) {', '.join(unresolved)} "
                                "are not yet marked completed. Complete the remaining steps or update the plan using update_plan."
                            )
                            user_msg = ctx.append({"role": "user", "content": gate_msg})
                            self._persist(user_msg)
                            continue
                        else:
                            final_text = str(message.get("content") or "").strip()
                            stop_reason = STOP_PLAN_INCOMPLETE
                            break

                    # 2. Verification Gate (B1 / B6): If mutating actions occurred, require proof-of-work/verification
                    if self._verify_policy and (
                        self._verify_policy.require_proof_of_work
                        or (self._verify_policy.enabled and self._mutations_occurred)
                    ):
                        if self._mutations_occurred:
                            verdict = self._last_verdict
                            if (verdict is None or not verdict.verified) and self._verify_policy.command:
                                from .verify import run_verification

                                verdict = run_verification(self._verify_policy, self._runtime)
                                self._last_verdict = verdict
                                self._emit(
                                    "verification",
                                    verified=verdict.verified,
                                    kind=verdict.kind,
                                    command=verdict.command,
                                    exit_code=verdict.exit_code,
                                )

                            if verdict is None or not verdict.verified:
                                if turn < self.max_turns and not self._cancelled():
                                    self._emit(
                                        "verification_gate_rejected",
                                        turn=turn,
                                        reason=verdict.reason if verdict else "No verification performed",
                                    )
                                    detail = (
                                        verdict.output
                                        if (verdict and verdict.output)
                                        else (verdict.reason if verdict else "Mutating changes occurred without verification.")
                                    )
                                    gate_msg = (
                                        f"[Verification Gate] Cannot complete: mutating changes were made but not verified.\n{detail}\n"
                                        "Please run a verification command (e.g. verify_work) or record proof of work before finishing."
                                    )
                                    user_msg = ctx.append({"role": "user", "content": gate_msg})
                                    self._persist(user_msg)
                                    continue
                                else:
                                    final_text = str(message.get("content") or "").strip()
                                    stop_reason = STOP_UNVERIFIED
                                    break

                    final_text = str(message.get("content") or "").strip()
                    stop_reason = STOP_COMPLETED
                    break

                if self.max_tool_calls is not None and self._tool_calls_made >= self.max_tool_calls:
                    final_text = str(message.get("content") or "").strip()
                    stop_reason = "max_tool_calls"
                    break

                skipped = 0
                denied = 0
                cancelled = 0

                # Parse every call before dispatching any: the transcript needs
                # one `tool_call` event per call in model order, and the
                # parallel/sequential decision has to see the whole turn.
                parsed: List[Dict[str, Any]] = []
                for call in calls:
                    name = call["function"]["name"]
                    raw_args = call["function"]["arguments"]
                    try:
                        args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                    except ValueError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {"value": args}

                    signature = f"{name}:{_canonical_args(args)}"
                    self._repeat_counts[signature] = self._repeat_counts.get(signature, 0) + 1
                    attempts = self._repeat_counts[signature]
                    parsed.append(
                        {
                            "call": call,
                            "name": name,
                            "args": args,
                            "attempts": attempts,
                            "output": None,
                            "ran": False,
                        }
                    )
                    self._emit("tool_call", name=name, call_id=call["id"], arguments=args, repeat=attempts)

                # Dispatch by path dependency: unrelated reads/writes overlap,
                # same-path pairs keep model order, fences run alone and
                # repeats are skipped. Outputs land on their items, so the
                # transcript below stays in model order.
                self._dispatch_calls(parsed)

                # One `tool` message per `tool_call`, in submission order. A
                # cancelled or never-dispatched call still gets a result:
                # resume and the next request require the pairing.
                for item in parsed:
                    if item["output"] is None:
                        output = CANCELLED_TOOL_OUTPUT
                        cancelled += 1
                    else:
                        output = item["output"]

                    if item["attempts"] > self.repeat_limit:
                        skipped += 1
                    elif item["ran"]:
                        self._tool_calls_made += 1
                        if self._looks_denied(output):
                            denied += 1

                    output = truncate_tool_output(output, ctx.budget.max_tool_output_chars)
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": item["call"]["id"],
                        "name": item["name"],
                        "content": output,
                    }
                    ctx.append(tool_message)
                    self._persist(tool_message)
                    self._emit(
                        "tool_result",
                        name=item["name"],
                        call_id=item["call"]["id"],
                        ok=not self._looks_failed(output) and item["output"] is not None,
                        chars=len(output),
                        preview=output[:400],
                    )

                self._load_directory_instructions(parsed)

                if cancelled or self._cancelled():
                    stop_reason = STOP_CANCELLED
                    break
                if calls and skipped == len(calls):
                    stop_reason = STOP_REPEAT
                    break
                if calls and denied == len(calls):
                    # Every call this turn was refused. One more turn would be
                    # the model rephrasing the same request; stop and let the
                    # caller surface it.
                    stop_reason = STOP_PERMISSION
                    break
            else:
                stop_reason = STOP_MAX_TURNS
        except Cancelled:
            # Ctrl-C / the HUD shortcut landed while waiting to retry. That is
            # a user decision, not an error: no traceback, no error text.
            stop_reason = STOP_CANCELLED
        except Exception as exc:  # noqa: BLE001 - the loop owns the boundary
            stop_reason = STOP_ERROR
            self._emit("error", error=f"{type(exc).__name__}: {exc}")
            final_text = f"Agent stopped after an error: {type(exc).__name__}: {exc}"

        if not final_text:
            if stop_reason == STOP_COMPLETED:
                final_text = ""
            else:
                last = self._last_assistant_text()
                # A cancelled parent may have finished a child but never got
                # to summarise it. The report is real output; return it rather
                # than an empty body.
                final_text = (
                    last
                    or self._last_subagent_report
                    or f"[Agent stopped: {stop_reason} after {turn} turn(s)]"
                )

        if self._session is not None:
            try:
                self._session.set_status(stop_reason, turns=turn)
            except Exception:
                pass
        is_verified = (
            self._last_verdict.verified
            if self._last_verdict is not None
            else not self._mutations_occurred
        )
        self._record_model_loads(turn)
        self._record_skill_selection(
            status=(
                "completed"
                if stop_reason == STOP_COMPLETED
                else ("cancelled" if stop_reason == STOP_CANCELLED else "error")
            ),
            stop_reason=stop_reason,
            turn=turn,
        )
        self._emit(
            "stop",
            reason=stop_reason,
            turns=turn,
            tool_calls=self._tool_calls_made,
            verified=is_verified,
        )

        return AgentResult(
            text=final_text,
            turns=turn,
            stop_reason=stop_reason,
            messages=list(ctx.messages),
            events=list(self._events),
            session_id=self._session.id if self._session else None,
            trace_id=self._tracer.trace_id if self._tracer else None,
            verified=is_verified,
            usage={
                "turns": turn,
                "tool_calls": self._tool_calls_made,
                "compactions": ctx.compactions,
                "retries": self._retries,
                "context_chars": ctx.chars,
                "estimated_tokens": ctx.estimated_tokens,
                # Provider-reported counts, summed. Zero when no response ever
                # carried usage; `tokens_reported` distinguishes that from a
                # provider that really charged nothing.
                "prompt_tokens": self._tokens.get("prompt_tokens", 0),
                "completion_tokens": self._tokens.get("completion_tokens", 0),
                "total_tokens": self._tokens.get("total_tokens", 0),
                "tokens_reported": self._usage_reports > 0,
                "elapsed_s": None,
            },
        )

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _looks_failed(output: str) -> bool:
        probe = (output or "").lstrip().lower()
        return probe.startswith(("tool '", "error", "not run:", "permission denied"))

    @staticmethod
    def _looks_denied(output: str) -> bool:
        probe = (output or "").lstrip().lower()
        return probe.startswith(("permission denied", "not run:"))

    def _last_assistant_text(self) -> str:
        for message in reversed(self._context.messages):
            if message.get("role") == "assistant" and str(message.get("content") or "").strip():
                return str(message["content"]).strip()
        return ""

    # -- skill-selection record (design §7) --------------------------------

    def _advertised_tool_names(self) -> List[str]:
        names: List[str] = []
        for item in self._tools:
            if isinstance(item, str):
                name = item
            elif isinstance(item, Mapping):
                name = str(item.get("name") or "")
            else:
                name = str(getattr(item, "name", "") or "")
            if name and name not in names:
                names.append(name)
        return names

    def _cookbook_tool_hints(self, plan: Any) -> List[str]:
        """Which advertised tools the injected cookbook actually names.

        A deterministic substring match — a hint for rule R3, never a verdict.
        Short names are skipped because ``ls``-shaped tokens appear in prose.
        """
        skill = getattr(plan, "selected_skill", None) or {}
        body = str(skill.get("cookbook") or skill.get("body") or "")
        if not body:
            return []
        hints: List[str] = []
        for name in self._advertised_tool_names():
            if len(name) >= 5 and name in body and name not in hints:
                hints.append(name)
        return hints[:8]

    def _tools_run_names(self) -> List[str]:
        names: List[str] = []
        for event in self._events:
            if event.get("type") != "tool_call":
                continue
            name = str(event.get("name") or "")
            if name and name not in names:
                names.append(name)
            if len(names) >= 24:
                break
        return names

    def _verify_evidence(self) -> List[str]:
        verdict = self._last_verdict
        if verdict is None:
            return []
        kind = str(getattr(verdict, "kind", "none") or "none")
        passed = "PASSED" if bool(getattr(verdict, "verified", False)) else "FAILED"
        reason = str(getattr(verdict, "reason", "") or "")[:120]
        return [f"{kind}:{passed}" + (f" ({reason})" if reason else "")]

    def _indexed_skill_names(self) -> set:
        try:
            from .turn_context import indexed_skill_names

            # Must be the index the router read: the runtime workspace, not cwd.
            return indexed_skill_names(self._runtime.workspace)
        except Exception:
            return set()

    def _record_model_loads(self, turn: int) -> None:
        """Record each model-initiated skill load for this run (design M5).

        This runs **before** the selection record and never returns early on an
        empty shortlist: a load while nothing was injected is the most
        informative miss, and it is exactly the case the selection record cannot
        describe. The outcome is a classification, not a verdict — whether a
        miss is the router's fault is for the audit and a human to decide.
        """
        try:
            from .skill_audit import extract_model_loads, model_load_record
        except Exception:  # noqa: BLE001 - telemetry must never fail a run
            return

        loads = extract_model_loads(self._events)
        if not loads:
            return

        plan = self._turn_plan
        injected = None
        if plan is not None and plan.selected_skill:
            injected = str(plan.selected_skill.get("name") or "") or None
        indexed = self._indexed_skill_names()
        trace_id = str(getattr(self._tracer, "trace_id", "") or "")
        session_id = self._session.id if self._session is not None else None

        rows: List[Dict[str, Any]] = []
        for item in loads:
            try:
                row = model_load_record(
                    tool=item["tool"],
                    name=item["name"],
                    source="harness",
                    injected=injected,
                    indexed=item["name"].strip().lower() in indexed,
                    routed=True,
                    trace_id=trace_id,
                    session_id=session_id,
                    turn=turn,
                    query=self._turn_query,
                )
            except Exception:  # noqa: BLE001
                continue
            rows.append(row)
            self._emit(
                "skill_load",
                name=row["name"],
                outcome=row["outcome"],
                injected=row["injected"] or "",
            )
            if self._skill_load_log is not None:
                try:
                    self._skill_load_log.save(row)
                except Exception:  # noqa: BLE001
                    pass

        tracer_record = getattr(self._tracer, "record", None)
        if tracer_record is not None and rows:
            try:
                tracer_record.attributes["model_skill_loads"] = rows
            except Exception:
                pass

    def _record_skill_selection(
        self,
        *,
        status: str,
        stop_reason: Optional[str],
        turn: int,
    ) -> None:
        """Persist one skill-selection record for this run (design §7).

        Reads the plan the turn already compiled and the events the loop already
        emitted, so it costs no model call and adds no scoring pass. The record
        is attached to the trace attributes (joinable on ``trace_id``) and, when
        a sink was injected, appended to the audit corpus.
        """
        plan = self._turn_plan
        if plan is None:
            return
        if not (plan.selected_skill or plan.selected_script or plan.shortlisted_skills):
            return
        try:
            from .trace import classify_failure
            from .turn_context import selection_record

            tracer_record = getattr(self._tracer, "record", None)
            record = selection_record(
                plan,
                trace_id=str(getattr(self._tracer, "trace_id", "") or ""),
                session_id=self._session.id if self._session is not None else None,
                turn=turn,
                query=self._turn_query,
                status=status,
                stop_reason=stop_reason,
                failure_label=classify_failure(
                    status=status,
                    stop_reason=stop_reason,
                    spans=list(getattr(tracer_record, "spans", []) or []),
                ),
                tools_run=self._tools_run_names(),
                verify=self._verify_evidence(),
                cookbook_tools=self._cookbook_tool_hints(plan),
                retain_query=bool(getattr(self._skill_log, "retain_query", True)),
            )
        except Exception:  # noqa: BLE001 - telemetry must never fail a run
            return

        if tracer_record is not None:
            try:
                tracer_record.attributes["skill_selection"] = record
            except Exception:
                pass
        if self._skill_log is not None:
            try:
                self._skill_log.save(record)
            except Exception:
                pass
        self._emit(
            "skill_selection",
            skill=record.get("selected", ""),
            tier=(record.get("retrieval") or {}).get("tier", "T1"),
            outcome=status,
            chars=record.get("injected_chars", 0),
        )


def run_agent(
    prompt: str,
    complete: Completer,
    **kwargs: Any,
) -> AgentResult:
    """Convenience wrapper: one call, one result."""
    started = time.monotonic()
    result = AgentLoop(complete, **kwargs).run(prompt)
    result.usage["elapsed_s"] = round(time.monotonic() - started, 2)
    return result
