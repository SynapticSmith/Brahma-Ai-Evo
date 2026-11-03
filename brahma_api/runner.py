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

        runtime = AgentRuntime(
            workspace=workspace,
            policy=policy,
            headless=True,
            approver=None,
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

        watchdog = threading.Timer(self.config.limits.wall_clock_s, runtime.cancel)
        watchdog.daemon = True
        watchdog.start()
        try:
            result = loop.run(seed.prompt)
        finally:
            watchdog.cancel()

        elapsed = round(time.monotonic() - started, 2)
        result.usage["elapsed_s"] = elapsed
        fingerprint = policy_fingerprint(policy)
        self._audit_run(principal, resolved, result, events, elapsed, provider, upstream_model)

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
            provider=provider,
            upstream_model=upstream_model,
            policy_fingerprint=fingerprint,
            elapsed_s=elapsed,
            events=events,
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
