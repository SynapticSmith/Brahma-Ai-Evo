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
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .context import ContextBudget, ConversationContext, truncate_tool_output
from .runtime import AgentRuntime, current as current_runtime
from .session import AgentSession
from .spec import ToolSpec, all_specs, get as get_spec, lowercase_schema

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
        budget: Optional[ContextBudget] = None,
        on_event: Optional[EventSink] = None,
        cancel: Optional[threading.Event] = None,
        summarizer: Optional[Summarizer] = None,
        context: Optional[ConversationContext] = None,
    ):
        self._complete = complete
        self._tools: List[Any] = list(tools) if tools is not None else list(all_specs())
        self._native_dispatch = native_dispatch
        self._runtime = runtime or current_runtime()
        self._session = session
        self.max_turns = max(1, int(max_turns))
        self.repeat_limit = max(1, int(repeat_limit))
        self._on_event = on_event
        self._cancel = cancel
        self._summarizer = summarizer
        self._events: List[Dict[str, Any]] = []
        self._repeat_counts: Dict[str, int] = {}
        self._tool_calls_made = 0

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

    def _emit(self, kind: str, **data: Any) -> None:
        event = {"type": kind, **data}
        if len(self._events) < 4000:
            self._events.append(event)
        if self._session is not None:
            self._session.log_event(kind, data)
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

    def _persist(self, message: Dict[str, Any]) -> None:
        if self._session is not None:
            try:
                self._session.append_message(message)
            except Exception:
                pass

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

                if first_response is not None:
                    response, first_response = first_response, None
                else:
                    response = self._complete(ctx.as_payload(), self._openai_schemas())

                message = normalise_assistant_message(response, turn)
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
                for call in calls:
                    if self._cancelled():
                        stop_reason = STOP_CANCELLED
                        break
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

                    self._emit("tool_call", name=name, call_id=call["id"], arguments=args, repeat=attempts)

                    if attempts > self.repeat_limit:
                        output = (
                            f"Identical call skipped: '{name}' with these arguments has already "
                            f"run {attempts - 1} times. Change the arguments or the approach, "
                            "or explain to the user why you are blocked."
                        )
                        skipped += 1
                    else:
                        output = self._runtime.execute(
                            name,
                            args,
                            native_dispatch=self._native_dispatch,
                            call_id=call["id"],
                        )
                        self._tool_calls_made += 1
                        if self._looks_denied(output):
                            denied += 1

                    output = truncate_tool_output(output, ctx.budget.max_tool_output_chars)
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call["id"],
                        "name": name,
                        "content": output,
                    }
                    ctx.append(tool_message)
                    self._persist(tool_message)
                    self._emit(
                        "tool_result",
                        name=name,
                        call_id=call["id"],
                        ok=not self._looks_failed(output),
                        chars=len(output),
                        preview=output[:400],
                    )

                if stop_reason == STOP_CANCELLED:
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
                "context_chars": ctx.chars,
                "estimated_tokens": ctx.estimated_tokens,
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
