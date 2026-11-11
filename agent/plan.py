"""Durable multi-step plan state and plan management tools (C1 / C3).

Autonomy requires structured planning: an agent tackling multi-step tasks
needs explicit decomposition, tracking of pending/in-progress/completed/failed
states, dependencies between steps, and attached verification evidence.

Completion gate (C3):
When an active plan exists, completion requires all steps to be resolved
('completed' or 'cancelled'). Refusing ungrounded completion stops the agent
from prematurely claiming victory before steps are done.

Stdlib-only, no Qt, no provider SDK.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .spec import ToolContext, ToolSpec, register

STEP_STATUSES: Tuple[str, ...] = ("pending", "in_progress", "completed", "blocked", "failed", "cancelled")
RESOLVED_STATUSES: Tuple[str, ...] = ("completed", "cancelled")


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class PlanStep:
    id: str
    description: str
    status: str = "pending"
    dependencies: List[str] = field(default_factory=list)
    evidence: str = ""
    created_at: int = field(default_factory=_now_ms)
    updated_at: int = field(default_factory=_now_ms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "dependencies": list(self.dependencies),
            "evidence": self.evidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanStep:
        return cls(
            id=str(data.get("id") or ""),
            description=str(data.get("description") or ""),
            status=str(data.get("status") or "pending"),
            dependencies=list(data.get("dependencies") or []),
            evidence=str(data.get("evidence") or ""),
            created_at=int(data.get("created_at") or _now_ms()),
            updated_at=int(data.get("updated_at") or _now_ms()),
        )


@dataclass
class Plan:
    session_id: str = ""
    title: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    acceptance_criteria: List[str] = field(default_factory=list)
    created_at: int = field(default_factory=_now_ms)
    updated_at: int = field(default_factory=_now_ms)

    def get_step(self, step_id: str) -> Optional[PlanStep]:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None

    def add_or_update_step(
        self,
        step_id: str,
        description: str = "",
        status: Optional[str] = None,
        dependencies: Optional[Sequence[str]] = None,
        evidence: Optional[str] = None,
    ) -> PlanStep:
        self.updated_at = _now_ms()
        existing = self.get_step(step_id)
        if existing is not None:
            if description:
                existing.description = description
            if status:
                st = status.lower()
                if st in STEP_STATUSES:
                    existing.status = st
            if dependencies is not None:
                existing.dependencies = list(dependencies)
            if evidence is not None:
                existing.evidence = str(evidence)
            existing.updated_at = self.updated_at
            return existing
        step = PlanStep(
            id=step_id,
            description=description,
            status=status.lower() if status and status.lower() in STEP_STATUSES else "pending",
            dependencies=list(dependencies or []),
            evidence=str(evidence or ""),
            created_at=self.updated_at,
            updated_at=self.updated_at,
        )
        self.steps.append(step)
        return step

    def is_completed(self) -> bool:
        if not self.steps:
            return True
        return all(step.status in RESOLVED_STATUSES for step in self.steps)

    def unresolved_steps(self) -> List[PlanStep]:
        return [step for step in self.steps if step.status not in RESOLVED_STATUSES]

    def render_for_prompt(self) -> str:
        if not self.steps:
            return "(No active plan)"
        lines = []
        if self.title:
            lines.append(f"Plan: {self.title}")
        if self.acceptance_criteria:
            lines.append("Acceptance criteria: " + "; ".join(self.acceptance_criteria))
        for step in self.steps:
            mark = "x" if step.status == "completed" else ("-" if step.status == "cancelled" else " ")
            extra = []
            if step.status not in ("completed", "pending"):
                extra.append(f"status: {step.status}")
            if step.dependencies:
                extra.append(f"deps: {', '.join(step.dependencies)}")
            if step.evidence:
                extra.append(f"evidence: {step.evidence}")
            suffix = f" ({'; '.join(extra)})" if extra else ""
            lines.append(f"- [{mark}] {step.id}: {step.description}{suffix}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "title": self.title,
            "steps": [s.to_dict() for s in self.steps],
            "acceptance_criteria": list(self.acceptance_criteria),
            "is_completed": self.is_completed(),
            "unresolved_count": len(self.unresolved_steps()),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Plan:
        steps = [PlanStep.from_dict(s) for s in data.get("steps") or []]
        return cls(
            session_id=str(data.get("session_id") or ""),
            title=str(data.get("title") or ""),
            steps=steps,
            acceptance_criteria=list(data.get("acceptance_criteria") or []),
            created_at=int(data.get("created_at") or _now_ms()),
            updated_at=int(data.get("updated_at") or _now_ms()),
        )


# -- Tool Specifications ---------------------------------------------------

UPDATE_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "description": "List of plan steps to create or update.",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Step identifier, e.g. 'step_1'."},
                    "description": {"type": "string", "description": "What this step accomplishes."},
                    "status": {
                        "type": "string",
                        "description": "Status: pending, in_progress, completed, blocked, failed, cancelled.",
                        "enum": ["pending", "in_progress", "completed", "blocked", "failed", "cancelled"],
                    },
                    "dependencies": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "IDs of steps that must be completed before this step.",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "Verification evidence or proof that the step succeeded.",
                    },
                },
                "required": ["id"],
            },
        },
        "title": {"type": "string", "description": "Optional title for the overall plan."},
        "acceptance_criteria": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional criteria that must be verified before completion.",
        },
    },
    "required": ["steps"],
}

GET_PLAN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {},
}


def update_plan(args: Dict[str, Any], context: ToolContext) -> str:
    extra = context.extra if isinstance(context.extra, dict) else {}
    plan: Optional[Plan] = extra.get("plan")
    session = extra.get("session")
    if plan is None:
        plan = Plan(session_id=session.id if session and hasattr(session, "id") else "")
        extra["plan"] = plan

    title = args.get("title")
    if title:
        plan.title = str(title).strip()
    ac = args.get("acceptance_criteria")
    if isinstance(ac, list):
        plan.acceptance_criteria = [str(c).strip() for c in ac if str(c).strip()]

    raw_steps = args.get("steps") or []
    for raw in raw_steps:
        if not isinstance(raw, dict):
            continue
        step_id = str(raw.get("id") or "").strip()
        if not step_id:
            continue
        desc = str(raw.get("description") or "").strip()
        status = raw.get("status")
        deps = raw.get("dependencies")
        evidence = raw.get("evidence")
        plan.add_or_update_step(
            step_id=step_id,
            description=desc,
            status=str(status) if status else None,
            dependencies=deps if isinstance(deps, list) else None,
            evidence=str(evidence) if evidence else None,
        )

    if session is not None and hasattr(session, "save_plan_steps"):
        try:
            session.save_plan_steps(plan.steps)
        except Exception:
            pass

    unresolved = [s.id for s in plan.unresolved_steps()]
    summary_line = (
        f"Plan updated: {len(plan.steps)} total step(s), {len(unresolved)} unresolved "
        f"({', '.join(unresolved) if unresolved else 'all resolved'})."
    )
    return f"{summary_line}\n\n{plan.render_for_prompt()}"


def get_plan(args: Dict[str, Any], context: ToolContext) -> str:
    extra = context.extra if isinstance(context.extra, dict) else {}
    plan: Optional[Plan] = extra.get("plan")
    session = extra.get("session")
    if plan is None and session is not None and hasattr(session, "load_plan_steps"):
        try:
            steps = session.load_plan_steps()
            if steps:
                plan = Plan(session_id=session.id, steps=steps)
                extra["plan"] = plan
        except Exception:
            pass
    if plan is None or not plan.steps:
        return "No active plan found. Use update_plan to create an execution plan."
    unresolved = [s.id for s in plan.unresolved_steps()]
    status = "COMPLETED" if plan.is_completed() else f"IN PROGRESS ({len(unresolved)} remaining: {', '.join(unresolved)})"
    return f"Plan Status: {status}\n\n{plan.render_for_prompt()}"


UPDATE_PLAN_SPEC = ToolSpec(
    name="update_plan",
    description=(
        "Create or update a structured execution plan with steps, status, dependencies, "
        "and verification evidence. Use this for multi-step tasks to track progress "
        "and ensure all requirements are verified before completion."
    ),
    parameters=UPDATE_PLAN_SCHEMA,
    tool_class="write",
    handler=update_plan,
    tags=("plan", "autonomy"),
    max_output_chars=8_000,
)

GET_PLAN_SPEC = ToolSpec(
    name="get_plan",
    description="Retrieve the current execution plan, step statuses, and unresolved items.",
    parameters=GET_PLAN_SCHEMA,
    tool_class="read",
    handler=get_plan,
    tags=("plan", "autonomy"),
    max_output_chars=8_000,
)

SPECS: Tuple[ToolSpec, ...] = (UPDATE_PLAN_SPEC, GET_PLAN_SPEC)


def register_all() -> None:
    for spec in SPECS:
        existing = None
        try:
            from . import spec as spec_module

            existing = spec_module.get(spec.name)
        except Exception:
            pass
        if existing is None:
            register(spec)


register_all()
