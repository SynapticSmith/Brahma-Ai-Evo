"""Tests for conversational voice and GUI background agent delegation (core/agent_delegation.py)."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import MagicMock

import pytest

from agent.permissions import PermissionPolicy
from agent.runtime import AgentRuntime, configure
from agent.spec import get as get_spec
from core.agent_delegation import (
    TaskDelegationManager,
    build_delegation_specs,
    register_delegation_tools,
)


class DummyStubCompleter:
    """Minimal completer for testing AgentLoop runs."""

    def __init__(self, responses: Optional[List[str]] = None) -> None:
        self.responses = list(responses or ["Task completed successfully."])
        self.call_count = 0

    def __call__(self, messages: Any, tools: Any = None) -> Any:
        self.call_count += 1
        text = self.responses.pop(0) if self.responses else "Done."
        return {"role": "assistant", "content": text}

    def complete(self, messages: Any, tools: Any = None) -> Any:
        return self(messages, tools)


@pytest.fixture
def test_runtime(tmp_path: Path):
    runtime = configure(
        workspace=tmp_path,
        policy=PermissionPolicy.from_dict({
            "classes": {"exec": "allow", "read": "allow", "write": "allow"},
            "headless": {"exec": "allow"},
        }),
        headless=True,
    )
    register_delegation_tools()
    yield runtime


def test_delegation_manager_lifecycle(tmp_path: Path):
    manager = TaskDelegationManager(completer_factory=lambda: DummyStubCompleter())
    mock_host = MagicMock()

    task = manager.delegate(
        prompt="Write a hello world script",
        workspace=tmp_path,
        host=mock_host,
        max_turns=5,
    )

    assert task.task_id.startswith("task_")
    assert task.status in ("pending", "running", "completed")

    # Wait for completion (stub completer is instantaneous)
    timeout = time.time() + 5.0
    while task.status in ("pending", "running") and time.time() < timeout:
        time.sleep(0.05)

    assert task.status == "completed"
    assert "Task completed successfully." in (task.result or "")
    assert task.turns >= 1
    assert task.finished_at is not None

    # Verify notification to host
    assert mock_host.speak.called
    speak_args, speak_kwargs = mock_host.speak.call_args
    assert "completed the background task" in speak_args[0]
    assert speak_kwargs.get("proactive") is True


def test_delegation_manager_cancellation(tmp_path: Path):
    # Completer that sleeps to allow cancellation
    class SlowCompleter:
        def __call__(self, messages: Any, tools: Any = None) -> Any:
            time.sleep(1.0)
            return {"role": "assistant", "content": "Done"}

    manager = TaskDelegationManager(completer_factory=lambda: SlowCompleter())
    mock_host = MagicMock()

    task = manager.delegate(
        prompt="Long running simulation",
        workspace=tmp_path,
        host=mock_host,
    )

    # Cancel immediately
    time.sleep(0.05)
    cancelled = manager.cancel_task(task.task_id)
    assert cancelled is True

    timeout = time.time() + 5.0
    while task.finished_at is None and time.time() < timeout:
        time.sleep(0.05)

    assert task.status == "cancelled"


def test_delegation_tool_specs_registered(test_runtime: AgentRuntime):
    specs = build_delegation_specs()
    names = [s.name for s in specs]
    assert "delegate_agent_task" in names
    assert "get_agent_task_status" in names
    assert "cancel_agent_task" in names

    for name in names:
        spec = get_spec(name)
        assert spec is not None
        assert "delegation" in spec.tags


def test_agent_runtime_execute_delegation(test_runtime: AgentRuntime, tmp_path: Path):
    # Test dispatching through AgentRuntime.execute
    out = test_runtime.execute(
        "delegate_agent_task",
        {"task": "Analyze security logs"},
    )
    assert "launched successfully" in out
    assert "task_" in out

    # Status query with no task_id returns recent tasks
    status_summary = test_runtime.execute("get_agent_task_status", {})
    assert "Recent delegated tasks:" in status_summary
    assert "Analyze security logs" in status_summary

    # Status query with invalid task_id
    not_found = test_runtime.execute("get_agent_task_status", {"task_id": "nonexistent_999"})
    assert "No task found" in not_found


def test_cancel_agent_task_execution(test_runtime: AgentRuntime):
    # Cancel missing task
    res = test_runtime.execute("cancel_agent_task", {"task_id": "task_missing"})
    assert "Could not cancel" in res

    # Cancel empty
    res_empty = test_runtime.execute("cancel_agent_task", {})
    assert "Error:" in res_empty
