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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set

from .context import ContextBudget, ConversationContext, truncate_tool_output
from .prompts import (
    MAX_SUBDIR_INSTRUCTION_CHARS,
    PATH_ARG_KEYS,
    load_directory_instructions,
)
from .retry import Cancelled, call_with_retry, is_transient
from .runtime import AgentRuntime, current as current_runtime
from .session import AgentSession
from .spec import ToolSpec, all_specs, get as get_spec, lowercase_schema
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
            "usage": self.usage,
        }


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
    ):
        self._complete = complete
        self._tools: List[Any] = list(tools) if tools is not None else list(all_specs())
        self._native_dispatch = native_dispatch
        self._runtime = runtime or current_runtime()
        self._session = session
        self.max_turns = max(1, int(max_turns))
        self.repeat_limit = max(1, int(repeat_limit))
        #: 1 disables parallel dispatch. Only read-only calls are ever run
        #: concurrently; see ``_can_run_parallel``.
        self.max_parallel_tools = max(1, int(max_parallel_tools))
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self.retry_max_delay = max(0.0, float(retry_max_delay))
        self._on_event = on_event
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
        self._stream_fallback_reported = False
        self._events: List[Dict[str, Any]] = []
        self._repeat_counts: Dict[str, int] = {}
        self._tool_calls_made = 0
        self._retries = 0
        #: Sum of provider-reported counts. Empty until a response carries
        #: usage; ``estimated_tokens`` stays as the local fallback.
        self._tokens: Dict[str, int] = {}
        self._usage_reports = 0
        #: Absolute paths of directory instruction files already in the system
        #: prompt, and the characters still available for new ones. Per run:
        #: a resumed session starts from a freshly built prompt.
        self._seen_instruction_files: Set[str] = set()
        self._instruction_budget = MAX_SUBDIR_INSTRUCTION_CHARS

        if context is not None:
            self._context = context
            if system and not self._context.system:
                self._context.system = system
        else:
            self._context = ConversationContext(
                system=system,
                budget=budget or ContextBudget(),
            )

    # -- plumbing ----------------------------------------------------------

    @property
    def context(self) -> ConversationContext:
        return self._context

    def _emit(self, event_type: str, **data: Any) -> None:
        event = {"type": event_type, **data}
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

    def _parallel_safe(self, item: Dict[str, Any]) -> bool:
        """True when this call may share a pool with its neighbours.

        A read is safe only if it is not a repeat and the policy would allow it
        without asking. Anything else — a write, `exec`, a native call, a
        denied call, an approval prompt — is a **fence** in
        ``_dispatch_calls``: everything before it has finished and everything
        after it waits, so a read can never observe a half-written file and
        banners never stack.
        """
        if self.max_parallel_tools <= 1:
            return False
        if item["attempts"] > self.repeat_limit:
            return False
        spec = get_spec(item["name"])
        if spec is None or spec.tool_class != "read":
            return False
        _, decision = self._runtime.decide(item["name"], item["args"])
        return bool(decision.allowed)

    def _repeat_output(self, item: Dict[str, Any]) -> str:
        return (
            f"Identical call skipped: '{item['name']}' with these arguments has already "
            f"run {item['attempts'] - 1} times. Change the arguments or the approach, "
            "or explain to the user why you are blocked."
        )

    def _run_one(self, item: Dict[str, Any]) -> str:
        """Dispatch one prepared call. Never raises: the loop owns the boundary."""
        item["ran"] = True
        try:
            return self._runtime.execute(
                item["name"],
                item["args"],
                native_dispatch=self._native_dispatch,
                call_id=item["call"]["id"],
            )
        except Exception as exc:  # noqa: BLE001 - a worker must not kill the turn
            return f"Tool '{item['name']}' failed: {type(exc).__name__}: {exc}"

    def _execute_parallel(self, parsed: List[Dict[str, Any]]) -> List[str]:
        from concurrent.futures import ThreadPoolExecutor

        workers = max(1, min(self.max_parallel_tools, len(parsed)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="brahma-tool") as pool:
            futures = [pool.submit(self._run_one, item) for item in parsed]
            # Read results in submission order: the transcript requires one
            # `tool` message per call, in the order the model emitted them.
            return [future.result() for future in futures]

    def _dispatch_calls(self, parsed: List[Dict[str, Any]]) -> None:
        """Run each consecutive burst of safe reads concurrently.

        The whole turn used to be the unit of the decision: one write anywhere
        serialised three unrelated reads before it. A coding turn is usually
        reads with one write in the middle, so the decision belongs to the
        segment between fences:

        * a repeated call is skipped in place and never dispatched — and, being
          side-effect free, it does not split a burst of reads around it;
        * a fence (write/exec/native/ask/deny) runs alone, and its completion
          is a barrier for everything after it;
        * a run of two or more safe reads shares the pool, capped by
          ``max_parallel_tools``.

        Outputs land on their own items, so the caller's transcript loop still
        emits one `tool` message per call in model order.
        """
        index = 0
        while index < len(parsed):
            item = parsed[index]
            if item["attempts"] > self.repeat_limit:
                item["output"] = self._repeat_output(item)
                index += 1
                continue
            if self._cancelled():
                return
            if not self._parallel_safe(item):
                item["output"] = self._run_one(item)
                index += 1
                continue

            # Collect the burst, skipping repeats in place: a skipped call has
            # no side effect, so it must not split two reads that could
            # otherwise overlap.
            segment: List[Dict[str, Any]] = []
            while index < len(parsed):
                member = parsed[index]
                if member["attempts"] > self.repeat_limit:
                    member["output"] = self._repeat_output(member)
                    index += 1
                    continue
                if not self._parallel_safe(member):
                    break
                segment.append(member)
                index += 1

            if len(segment) == 1:
                segment[0]["output"] = self._run_one(segment[0])
            else:
                for member, output in zip(segment, self._execute_parallel(segment)):
                    member["output"] = output

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

    def run(
        self,
        prompt: Optional[str] = None,
        *,
        first_response: Optional[Any] = None,
    ) -> AgentResult:
        ctx = self._context
        if prompt:
            message = ctx.append_user(prompt)
            self._persist(message)
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
                    final_text = str(message.get("content") or "").strip()
                    stop_reason = STOP_COMPLETED
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

                # Dispatch in fences: read bursts overlap, writes and approval
                # prompts are barriers, repeats are skipped. Outputs land on
                # their items, so the transcript below stays in model order.
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
                final_text = last or f"[Agent stopped: {stop_reason} after {turn} turn(s)]"

        if self._session is not None:
            try:
                self._session.set_status(stop_reason, turns=turn)
            except Exception:
                pass
        self._emit("stop", reason=stop_reason, turns=turn, tool_calls=self._tool_calls_made)

        return AgentResult(
            text=final_text,
            turns=turn,
            stop_reason=stop_reason,
            messages=list(ctx.messages),
            events=list(self._events),
            session_id=self._session.id if self._session else None,
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
