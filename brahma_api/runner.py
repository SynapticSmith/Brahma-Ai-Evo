"""One agent run, on behalf of one key.

The runner is the only place that constructs an ``AgentRuntime`` and an
``AgentLoop``. It owns no policy of its own: the preset arrives already parsed
as a ``PermissionPolicy``, and every tool call the loop makes still goes through
``AgentRuntime.execute``. That is the whole "transport, not a door" rule.

Phase 0 is synchronous. FastAPI runs a ``def`` route in its threadpool, which is
the correct place for a blocking loop built on ``urllib`` and ``subprocess``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.loop import AgentLoop, AgentResult
from agent.prompts import build_system_prompt
from agent.providers import ProviderUnavailable, build_completer
from agent.runtime import AgentRuntime, policy_note
from agent.spec import get as get_spec

from .auth import Principal
from .config import BrahmaApiConfig
from .models import ResolvedModel, policy_fingerprint, profile_policy
from .translate import Seed, compose_system

CompleterFactory = Callable[..., Any]


@dataclass
class RunOutcome:
    result: AgentResult
    provider: str
    upstream_model: Optional[str]
    policy_fingerprint: str
    elapsed_s: float
    events: List[Dict[str, Any]] = field(default_factory=list)
    tool_calls: Optional[List[Dict[str, Any]]] = None


class AgentRunner:
    def __init__(
        self,
        config: BrahmaApiConfig,
        *,
        completer_factory: Optional[CompleterFactory] = None,
        audit: Optional[Any] = None,
    ) -> None:
        self.config = config
        self._factory: CompleterFactory = completer_factory or build_completer
        self._audit = audit
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._runs_lock = threading.Lock()

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._runs_lock:
            run = self._runs.get(run_id)
            return dict(run) if run else None

    def _record_run(self, run_id: str, data: Dict[str, Any]) -> None:
        with self._runs_lock:
            # Bound memory to last 200 runs
            if len(self._runs) > 200:
                oldest = next(iter(self._runs))
                del self._runs[oldest]
            self._runs[run_id] = data

    def _build_gui_approver(self, principal: Principal) -> Callable[..., Any]:
        def approver(name: str, args: Dict[str, Any], detail: Any) -> Any:
            started = time.monotonic()
            caller_label = principal.token.label
            heading = f'API token "{caller_label}" requested {name}'
            remember = getattr(detail, "remember", "")
            detail_str = getattr(detail, "detail", str(detail))
            decision = "deny"
            try:
                from core import confirm

                if confirm.is_bound():
                    choice = confirm.ask_blocking_choice(
                        name,
                        detail_str,
                        timeout=float(self.config.limits.approval_timeout_s),
                        heading=heading,
                        remember=remember,
                    )
                    decision = choice or "deny"
            except Exception:
                decision = "deny"

            wait_ms = int((time.monotonic() - started) * 1000)
            if self._audit is not None:
                try:
                    self._audit.log(
                        "API_APPROVAL",
                        "brahma_api",
                        caller=caller_label,
                        decision=decision,
                        wait_ms=wait_ms,
                    )
                except Exception:
                    pass
            return decision

        return approver

    # -- public ------------------------------------------------------------

    def run(
        self,
        principal: Principal,
        resolved: ResolvedModel,
        seed: Seed,
        *,
        temperature: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
        sink_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        run_id: Optional[str] = None,
        approval_channel: str = "deny",
    ) -> RunOutcome:
        started = time.monotonic()
        policy = profile_policy(resolved.profile)
        workspace = resolved.workspace or Path(self.config.base_dir)
        events: List[Dict[str, Any]] = []

        if run_id:
            self._record_run(
                run_id,
                {
                    "run_id": run_id,
                    "token_id": principal.token.id,
                    "model": resolved.model_id,
                    "lane": resolved.lane,
                    "profile": resolved.profile,
                    "status": "running",
                    "stop_reason": None,
                    "turns": 0,
                    "tool_calls": 0,
                    "elapsed_s": 0.0,
                    "created_at": time.time(),
                    "events": [],
                },
            )

        def sink(event: Dict[str, Any]) -> None:
            record = dict(event)
            record["_at"] = time.monotonic()
            events.append(record)
            if sink_callback:
                try:
                    sink_callback(record)
                except Exception:
                    pass

        approver = self._build_gui_approver(principal) if approval_channel == "gui" else None
        runtime = AgentRuntime(
            workspace=workspace,
            policy=policy,
            headless=approval_channel != "gui",
            approver=approver,
            event_sink=sink,
        )
        if cancel is not None:
            # Share the caller's cancellation flag rather than owning a second
            # one; a client disconnect must reach the loop.
            runtime.cancel_event = cancel

        provider = principal.token.provider or self.config.default_provider
        upstream_model = principal.token.upstream_model
        try:
            completer = self._factory(
                provider,
                upstream_model,
                temperature=0.2 if temperature is None else float(temperature),
            )
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - provider construction boundary
            raise ProviderUnavailable(str(exc)) from exc

        if resolved.profile == "chat":
            # No workspace and no instruction files: a public bot must not be
            # able to read AGENTS.md just because the process happens to live
            # in a repository.
            harness = build_system_prompt(None)
        else:
            harness = build_system_prompt(
                workspace,
                permission_note=policy_note(runtime),
            )
        system = compose_system(harness, seed.directives)

        loop = AgentLoop(
            completer,
            tools=[] if not resolved.tools else None,
            runtime=runtime,
            system=system,
            max_turns=resolved.max_turns,
            on_event=sink,
            cancel=runtime.cancel_event,
        )
        for message in seed.history:
            loop.context.append(dict(message))

        watchdog: Optional[threading.Timer] = None
        if self.config.limits.wall_clock_s and self.config.limits.wall_clock_s > 0:
            # 0 means "no outer bound": the per-call socket timeouts still keep
            # a wedged provider from hanging forever, but a long reasoning run
            # is allowed to finish.
            watchdog = threading.Timer(self.config.limits.wall_clock_s, runtime.cancel)
            watchdog.daemon = True
            watchdog.start()
        try:
            result = loop.run(seed.prompt)
        finally:
            if watchdog is not None:
                watchdog.cancel()

        elapsed = round(time.monotonic() - started, 2)
        result.usage["elapsed_s"] = elapsed
        # ``auto`` may have failed over; audit the provider that actually
        # answered rather than the first candidate the chain named.
        provider_used = str(getattr(completer, "last_provider", provider) or provider)
        fingerprint = policy_fingerprint(policy)
        self._audit_run(principal, resolved, result, events, elapsed, provider_used, upstream_model)

        if run_id:
            tool_rows = _tool_rows(events)
            self._record_run(
                run_id,
                {
                    "run_id": run_id,
                    "token_id": principal.token.id,
                    "model": resolved.model_id,
                    "lane": resolved.lane,
                    "profile": resolved.profile,
                    "status": "completed" if result.stop_reason != "error" else "failed",
                    "stop_reason": result.stop_reason,
                    "turns": result.turns,
                    "tool_calls": int(result.usage.get("tool_calls") or 0),
                    "elapsed_s": elapsed,
                    "created_at": time.time(),
                    "events": tool_rows,
                },
            )

        return RunOutcome(
            result=result,
            provider=provider_used,
            upstream_model=upstream_model,
            policy_fingerprint=fingerprint,
            elapsed_s=elapsed,
            events=events,
        )

    def run_passthrough(
        self,
        principal: Principal,
        resolved: ResolvedModel,
        wire_messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: Optional[float] = None,
        cancel: Optional[threading.Event] = None,
        sink_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        run_id: Optional[str] = None,
    ) -> RunOutcome:
        started = time.monotonic()
        events: List[Dict[str, Any]] = []

        if run_id:
            self._record_run(
                run_id,
                {
                    "run_id": run_id,
                    "token_id": principal.token.id,
                    "model": resolved.model_id,
                    "lane": resolved.lane,
                    "profile": resolved.profile,
                    "status": "running",
                    "stop_reason": None,
                    "turns": 0,
                    "tool_calls": 0,
                    "elapsed_s": 0.0,
                    "created_at": time.time(),
                    "events": [],
                },
            )

        provider = principal.token.provider or self.config.default_provider
        upstream_model = principal.token.upstream_model
        try:
            completer = self._factory(
                provider,
                upstream_model,
                temperature=0.2 if temperature is None else float(temperature),
            )
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - provider construction boundary
            raise ProviderUnavailable(str(exc)) from exc

        deltas_seen: List[Dict[str, Any]] = []

        def on_delta(delta: Dict[str, Any]) -> None:
            deltas_seen.append(delta)
            if sink_callback:
                try:
                    sink_callback({"type": "delta", "delta": delta})
                except Exception:
                    pass

        try:
            if sink_callback is not None and hasattr(completer, "stream"):
                resp = completer.stream(wire_messages, tools or [], on_delta)
            else:
                resp = completer(wire_messages, tools or [])
        except ProviderUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(str(exc)) from exc

        provider_used = str(getattr(completer, "last_provider", provider) or provider)

        if isinstance(resp, dict):
            text = str(resp.get("content") or "")
            tool_calls = resp.get("tool_calls")
        else:
            text = str(resp or "")
            tool_calls = None

        elapsed = round(time.monotonic() - started, 2)
        usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "tokens_reported": False,
            "elapsed_s": elapsed,
            "turns": 1,
            "tool_calls": len(tool_calls or []),
        }
        if isinstance(resp, dict):
            from agent.usage import extract_usage as _extract_tokens

            tokens = _extract_tokens(resp)
            if tokens:
                usage["prompt_tokens"] = int(tokens.get("prompt_tokens") or 0)
                usage["completion_tokens"] = int(tokens.get("completion_tokens") or 0)
                usage["total_tokens"] = int(tokens.get("total_tokens") or 0)
                usage["tokens_reported"] = True

        result = AgentResult(
            text=text,
            stop_reason="completed",
            turns=1,
            usage=usage,
        )

        if run_id:
            self._record_run(
                run_id,
                {
                    "run_id": run_id,
                    "token_id": principal.token.id,
                    "model": resolved.model_id,
                    "lane": resolved.lane,
                    "profile": resolved.profile,
                    "status": "completed",
                    "stop_reason": "completed",
                    "turns": 1,
                    "tool_calls": len(tool_calls or []),
                    "elapsed_s": elapsed,
                    "created_at": time.time(),
                    "events": [],
                },
            )

        return RunOutcome(
            result=result,
            provider=provider_used,
            upstream_model=upstream_model,
            policy_fingerprint="passthrough",
            elapsed_s=elapsed,
            events=events,
            tool_calls=tool_calls,
        )

    # -- audit -------------------------------------------------------------

    def audit_cancelled(
        self,
        principal: Principal,
        resolved: ResolvedModel,
        reason: str,
    ) -> None:
        if self._audit is None:
            return
        try:
            self._audit.log(
                "API_CANCELLED",
                "brahma_api",
                token_id=principal.token.id,
                profile=resolved.profile,
                model=resolved.model_id,
                lane=resolved.lane,
                reason=reason,
            )
        except Exception:
            pass

    def _audit_run(
        self,
        principal: Principal,
        resolved: ResolvedModel,
        result: AgentResult,
        events: List[Dict[str, Any]],
        elapsed: float,
        provider: str,
        upstream_model: Optional[str],
    ) -> None:
        if self._audit is None:
            return
        base = {
            "token_id": principal.token.id,
            "profile": resolved.profile,
            "model": resolved.model_id,
            "lane": resolved.lane,
            "provider": provider,
            "upstream_model": upstream_model,
        }
        try:
            self._audit.log("API_REQUEST", "brahma_api", **base)
            for row in _tool_rows(events):
                self._audit.log("API_TOOL", "brahma_api", **base, **row)
            self._audit.log(
                "API_STOP",
                "brahma_api",
                **base,
                stop_reason=result.stop_reason,
                turns=result.turns,
                tool_calls=int(result.usage.get("tool_calls") or 0),
                elapsed_s=elapsed,
            )
        except Exception:
            # Auditing must never fail a run. The log is a record, not a gate.
            pass


def _tool_rows(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pair tool_start/tool_end and attach the last permission decision.

    Only names, classes, decisions, durations, and output lengths are recorded.
    Tool output is deliberately absent: it can contain a file body or a secret
    the model read, and the audit log is not a transcript.
    """
    started: Dict[str, float] = {}
    decisions: Dict[str, str] = {}
    rows: List[Dict[str, Any]] = []
    for event in events:
        kind = event.get("type")
        if kind == "permission":
            decisions[str(event.get("name") or "")] = str(event.get("decision") or "")
        elif kind == "tool_start":
            started[str(event.get("call_id") or "")] = float(event.get("_at") or 0.0)
        elif kind == "tool_end":
            name = str(event.get("name") or "")
            spec = get_spec(name)
            try:
                tool_class = spec.class_for(event.get("args") or {}) if spec else "native"
            except Exception:
                tool_class = "native"
            start = started.get(str(event.get("call_id") or ""))
            rows.append(
                {
                    "tool": name,
                    "tool_class": tool_class,
                    "decision": decisions.get(name, "allow"),
                    "duration_ms": int((float(event.get("_at") or 0.0) - start) * 1000)
                    if start
                    else None,
                    "output_chars": int(event.get("chars") or 0),
                }
            )
    return rows
