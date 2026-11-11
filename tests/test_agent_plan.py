"""Tests for durable plan state, plan tools, and plan completion gate (C1 / C3)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.loop import AgentLoop, STOP_COMPLETED, STOP_PLAN_INCOMPLETE
from agent.plan import (
    Plan,
    PlanStep,
    get_plan,
    update_plan,
)
from agent.runtime import AgentRuntime
from agent.session import AgentSession
from agent.spec import ToolContext


class Script:
    def __init__(self, responses: List[Dict[str, Any]]):
        self.responses = list(responses)
        self.calls: List[Any] = []

    def __call__(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        self.calls.append((messages, tools))
        if not self.responses:
            return {"role": "assistant", "content": "done"}
        return self.responses.pop(0)


def _runtime(tmp_path: Path) -> AgentRuntime:
    return AgentRuntime(workspace=tmp_path)


def test_plan_step_and_plan_round_trip():
    step1 = PlanStep(id="step_1", description="Read specs", status="completed", evidence="spec verified")
    step2 = PlanStep(id="step_2", description="Implement code", dependencies=["step_1"])
    plan = Plan(title="Wave 1 implementation", steps=[step1, step2], acceptance_criteria=["all tests pass"])

    data = plan.to_dict()
    assert data["title"] == "Wave 1 implementation"
    assert not data["is_completed"]
    assert data["unresolved_count"] == 1
    assert data["acceptance_criteria"] == ["all tests pass"]

    restored = Plan.from_dict(data)
    assert restored.title == "Wave 1 implementation"
    assert len(restored.steps) == 2
    assert restored.steps[0].id == "step_1"
    assert restored.steps[0].status == "completed"
    assert restored.steps[0].evidence == "spec verified"
    assert restored.steps[1].dependencies == ["step_1"]


def test_plan_step_lifecycle():
    plan = Plan(title="Feature X")
    assert plan.is_completed()

    plan.add_or_update_step("1", "Design API")
    assert not plan.is_completed()
    assert [s.id for s in plan.unresolved_steps()] == ["1"]

    plan.add_or_update_step("1", status="in_progress")
    assert not plan.is_completed()

    plan.add_or_update_step("1", status="completed", evidence="API spec accepted")
    assert plan.is_completed()
    assert plan.unresolved_steps() == []

    # Cancelled counts as resolved
    plan.add_or_update_step("2", "Legacy migration", status="cancelled")
    assert plan.is_completed()


def test_render_for_prompt():
    plan = Plan(title="Migration", acceptance_criteria=["No regressions"])
    plan.add_or_update_step("1", "Check DB", status="completed", evidence="verified rows")
    plan.add_or_update_step("2", "Run script", status="in_progress", dependencies=["1"])

    rendered = plan.render_for_prompt()
    assert "Plan: Migration" in rendered
    assert "Acceptance criteria: No regressions" in rendered
    assert "[x] 1: Check DB" in rendered
    assert "evidence: verified rows" in rendered
    assert "[ ] 2: Run script" in rendered
    assert "deps: 1" in rendered


def test_update_plan_and_get_plan_tools(tmp_path: Path):
    db_path = tmp_path / "plan_test.sqlite3"
    session = AgentSession.create(tmp_path, title="Plan session", db_path=db_path)
    extra: Dict[str, Any] = {"session": session}
    ctx = ToolContext(workspace=tmp_path, extra=extra)

    # Initially empty
    empty_res = get_plan({}, ctx)
    assert "No active plan found" in empty_res

    # Update plan
    res = update_plan(
        {
            "title": "Refactor codebase",
            "acceptance_criteria": ["Ruff clean", "Pytest pass"],
            "steps": [
                {"id": "step_1", "description": "Fix lint errors", "status": "in_progress"},
                {"id": "step_2", "description": "Run tests", "dependencies": ["step_1"]},
            ],
        },
        ctx,
    )
    assert "Plan updated: 2 total step(s)" in res
    assert "[ ] step_1: Fix lint errors" in res

    # Check persistence in session
    stored = session.load_plan_steps()
    assert len(stored) == 2
    assert stored[0].id == "step_1"
    assert stored[0].status == "in_progress"

    # Query get_plan
    get_res = get_plan({}, ctx)
    assert "Plan Status: IN PROGRESS (2 remaining: step_1, step_2)" in get_res

    # Complete step_1
    update_plan(
        {
            "steps": [
                {"id": "step_1", "status": "completed", "evidence": "ruff passed clean"},
            ]
        },
        ctx,
    )
    get_res2 = get_plan({}, ctx)
    assert "Plan Status: IN PROGRESS (1 remaining: step_2)" in get_res2


def test_agent_loop_plan_gate_rejects_premature_completion(tmp_path: Path):
    plan = Plan(title="Must complete all steps")
    plan.add_or_update_step("step_1", "Do critical work", status="pending")

    # Turn 1: model tries to stop ("I am done!") without resolving plan step_1.
    # Plan gate should reject and inject guidance.
    # Turn 2: model marks step_1 completed via update_plan.
    # Turn 3: model finishes cleanly.
    responses = [
        {"role": "assistant", "content": "I am done!"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "update_plan",
                        "arguments": json.dumps({
                            "steps": [{"id": "step_1", "status": "completed", "evidence": "verified work"}]
                        }),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "All steps done."},
    ]

    script = Script(responses)
    events: List[Dict[str, Any]] = []
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path),
        plan=plan,
        on_event=events.append,
        max_turns=5,
    )

    result = loop.run("start")
    assert result.stop_reason == STOP_COMPLETED
    assert result.ok

    # Verify plan_gate_rejected was emitted on turn 1
    rejected = [e for e in events if e.get("type") == "plan_gate_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["unresolved"] == ["step_1"]


def test_agent_loop_plan_gate_stops_with_plan_incomplete_on_max_turns(tmp_path: Path):
    plan = Plan(title="Incomplete plan")
    plan.add_or_update_step("step_1", "Unresolved task", status="pending")

    # Model insists it is done on every turn, but never resolves step_1
    script = Script([
        {"role": "assistant", "content": "I am done turn 1"},
        {"role": "assistant", "content": "I am done turn 2"},
    ])

    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path),
        plan=plan,
        max_turns=2,
    )

    result = loop.run("start")
    assert result.stop_reason == STOP_PLAN_INCOMPLETE
    assert not result.ok
