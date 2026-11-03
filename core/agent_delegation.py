"""
core/agent_delegation.py — Conversational Voice & GUI Background Agent Delegation.

Permits conversational doors (notably the Gemini Live audio session) to delegate
lengthy, multi-step autonomous tasks (e.g. codebase analysis, debugging, editing,
running commands) to a background AgentLoop without blocking the audio stream or
crashing the Live WebSocket protocol.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.spec import ToolContext, ToolSpec, register as register_spec

logger = logging.getLogger("AgentDelegation")


@dataclass
class DelegatedTask:
    task_id: str
    prompt: str
    workspace: Path
    status: str = "pending"  # "pending", "running", "completed", "failed", "cancelled"
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    turns: int = 0
    result: Optional[str] = None
    error: Optional[str] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)


class TaskDelegationManager:
    """Thread-safe coordinator for background autonomous agent tasks."""

    def __init__(self, completer_factory: Optional[Callable[[], Any]] = None) -> None:
        self._tasks: Dict[str, DelegatedTask] = {}
        self._lock = threading.Lock()
        self._completer_factory = completer_factory

    def get_task(self, task_id: str) -> Optional[DelegatedTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 10) -> List[DelegatedTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
        if not task:
            return False
        if task.status in ("pending", "running"):
            task.cancel_event.set()
            task.status = "cancelled"
            task.finished_at = time.time()
            return True
        return False

    def delegate(
        self,
        prompt: str,
        workspace: Optional[Path] = None,
        host: Any = None,
        max_turns: int = 24,
    ) -> DelegatedTask:
        from agent import runtime as agent_runtime

        active_runtime = agent_runtime.current()
        ws = workspace or active_runtime.workspace
        task_id = f"task_{uuid.uuid4().hex[:8]}"

        task = DelegatedTask(
            task_id=task_id,
            prompt=prompt,
            workspace=ws,
        )

        with self._lock:
            self._tasks[task_id] = task

        worker = threading.Thread(
            target=self._run_task,
            args=(task, host, max_turns),
            name=f"delegated-{task_id}",
            daemon=True,
        )
        worker.start()
        return task

    def _resolve_completer(self) -> Any:
        if self._completer_factory:
            return self._completer_factory()

        from agent.providers import ProviderUnavailable, build_completer, resolve_provider

        try:
            pid = resolve_provider("auto")
            return build_completer(pid)
        except ProviderUnavailable:
            # Fallback to stub provider if nothing is configured
            if os.getenv("BRAHMA_AGENT_PROVIDER") == "stub":
                return build_completer("stub")
            raise

    def _run_task(self, task: DelegatedTask, host: Any, max_turns: int) -> None:
        task.status = "running"
        try:
            from agent.context import ConversationContext
            from agent.loop import AgentLoop
            from agent import prompts
            from agent import runtime as agent_runtime
            from agent.spec import all_specs

            runtime = agent_runtime.current()
            completer = self._resolve_completer()

            # Prevent recursion: exclude delegation tools from child loop
            available_specs = [
                s
                for s in all_specs()
                if s.name
                not in (
                    "delegate_agent_task",
                    "get_agent_task_status",
                    "cancel_agent_task",
                    "run_subagent",
                    "run_subagent_batch",
                )
            ]

            sys_prompt = prompts.build_system_prompt(task.workspace)
            context = ConversationContext(system=sys_prompt)
            context.append_user(task.prompt)

            def on_event(event: Dict[str, Any]) -> None:
                if host and hasattr(host, "_agent_event"):
                    try:
                        host._agent_event(event)
                    except Exception:
                        pass

            loop = AgentLoop(
                completer,
                tools=available_specs,
                native_dispatch=getattr(host, "_execute_tool_sync", None),
                runtime=runtime,
                context=context,
                max_turns=max_turns,
                on_event=on_event,
                cancel=task.cancel_event,
            )

            result = loop.run()
            task.turns = result.turns
            task.finished_at = time.time()

            if task.cancel_event.is_set():
                task.status = "cancelled"
                task.result = "Task was cancelled."
            elif result.stop_reason == "completed":
                task.status = "completed"
                task.result = result.text or "Task completed successfully with no additional output."
            else:
                task.status = "failed"
                task.error = f"Stopped early: {result.stop_reason}"
                task.result = result.text

            self._notify_completion(task, host)

        except Exception as exc:
            task.status = "failed"
            task.error = str(exc)
            task.finished_at = time.time()
            logger.exception("Delegated task %s failed", task.task_id)
            if host:
                try:
                    if hasattr(host, "ui") and hasattr(host.ui, "write_log"):
                        host.ui.write_log(f"ERR: Delegated agent task failed ({task.task_id}): {exc}")
                    if hasattr(host, "speak"):
                        host.speak("Sir, the background agent task encountered an error.", proactive=True)
                except Exception:
                    pass

    def _notify_completion(self, task: DelegatedTask, host: Any) -> None:
        if not host:
            return
        summary = (task.result or "").strip()
        short_summary = summary.split("\n")[0][:140] if summary else "Task finished."

        try:
            if hasattr(host, "ui") and hasattr(host.ui, "write_log"):
                status_label = task.status.upper()
                host.ui.write_log(f"SYS: [{status_label}] Delegated agent task {task.task_id}: {short_summary}")

            if hasattr(host, "speak"):
                if task.status == "completed":
                    host.speak(f"Sir, I have completed the background task: {short_summary}", proactive=True)
                elif task.status == "cancelled":
                    host.speak("The background agent task has been cancelled.", proactive=True)
        except Exception as e:
            logger.debug("Failed to deliver task completion notification: %s", e)


_default_manager = TaskDelegationManager()


def get_manager() -> TaskDelegationManager:
    return _default_manager


def _handle_delegate(args: Dict[str, Any], context: ToolContext) -> str:
    prompt = str(args.get("task") or args.get("prompt") or "").strip()
    if not prompt:
        return "Error: A 'task' description must be provided."

    raw_ws = args.get("workspace")
    target_ws = context.resolve_path(raw_ws) if raw_ws else context.workspace

    host = context.extra.get("host")
    if hasattr(host, "_live"):
        host = host._live

    task = _default_manager.delegate(prompt, workspace=target_ws, host=host)
    return (
        f"Background agent task '{task.task_id}' launched successfully. "
        f"Objective: '{prompt[:100]}'. "
        f"Workspace: '{task.workspace}'. "
        "The agent loop is now operating autonomously in the background. "
        "You will receive a spoken and visual notification upon completion."
    )


def _handle_status(args: Dict[str, Any], context: ToolContext) -> str:
    task_id = args.get("task_id")
    if task_id:
        task = _default_manager.get_task(str(task_id).strip())
        if not task:
            return f"No task found with ID '{task_id}'."
        elapsed = (task.finished_at or time.time()) - task.created_at
        lines = [
            f"Task ID: {task.task_id}",
            f"Status: {task.status}",
            f"Turns: {task.turns}",
            f"Elapsed: {elapsed:.1f}s",
            f"Objective: {task.prompt}",
        ]
        if task.result:
            lines.append(f"Result:\n{task.result}")
        if task.error:
            lines.append(f"Error: {task.error}")
        return "\n".join(lines)

    tasks = _default_manager.list_tasks(limit=5)
    if not tasks:
        return "No delegated agent tasks recorded in this session."
    lines = ["Recent delegated tasks:"]
    for t in tasks:
        elapsed = (t.finished_at or time.time()) - t.created_at
        lines.append(f"- {t.task_id} [{t.status}]: '{t.prompt[:60]}' ({t.turns} turns, {elapsed:.1f}s)")
    return "\n".join(lines)


def _handle_cancel(args: Dict[str, Any], context: ToolContext) -> str:
    task_id = str(args.get("task_id") or "").strip()
    if not task_id:
        return "Error: A 'task_id' must be specified to cancel."
    success = _default_manager.cancel_task(task_id)
    if success:
        return f"Task '{task_id}' cancellation signal sent."
    return f"Could not cancel task '{task_id}' (not found or already finished)."


def build_delegation_specs() -> List[ToolSpec]:
    return [
        ToolSpec(
            name="delegate_agent_task",
            description=(
                "Delegates a complex, multi-step autonomous task to a background "
                "agent loop. Runs asynchronously in the background so conversational "
                "voice sessions do not block, and announces completion when done."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The full task prompt, objective, or instruction to execute autonomously.",
                    },
                    "workspace": {
                        "type": "string",
                        "description": "Optional directory path for the agent to execute within.",
                    },
                },
                "required": ["task"],
            },
            handler=_handle_delegate,
            tool_class="exec",
            requires_approval=False,
            tags=("desktop", "agent", "delegation"),
        ),
        ToolSpec(
            name="get_agent_task_status",
            description="Queries the status, elapsed time, and results of background delegated agent tasks.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Optional specific task ID to inspect.",
                    },
                },
            },
            handler=_handle_status,
            tool_class="read",
            tags=("desktop", "agent", "delegation"),
        ),
        ToolSpec(
            name="cancel_agent_task",
            description="Cancels an active background delegated agent task.",
            parameters={
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "The ID of the running task to cancel.",
                    },
                },
                "required": ["task_id"],
            },
            handler=_handle_cancel,
            tool_class="exec",
            requires_approval=False,
            tags=("desktop", "agent", "delegation"),
        ),
    ]


def get_delegation_declarations() -> List[Dict[str, Any]]:
    """Return Gemini function declarations for the delegation tools."""
    from agent.spec import gemini_declarations

    return gemini_declarations(["delegate_agent_task", "get_agent_task_status", "cancel_agent_task"])


def register_delegation_tools() -> List[ToolSpec]:
    """Register the 3 delegation tools in the process-wide AgentRuntime spec registry."""
    specs = build_delegation_specs()
    for s in specs:
        register_spec(s, replace=True)
    return specs


register_delegation_tools()

