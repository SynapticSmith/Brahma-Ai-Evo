"""Verification gate and proof-of-work completion (B1 / B6).

Verification turns a model's claim that a task is 'done' into evidence.
A completion is verified when:
- A configured verify command ran and exited 0 (tests, lint, build).
- Explicit proof of work / read-back evidence was provided.
- Or the task was purely read-only and cited its sources.

When mutating actions occur and proof-of-work is required, the loop refuses
to stop with 'completed' unless a verification check has succeeded, stopping
with STOP_UNVERIFIED ('unverified_stop') instead.

Stdlib-only, no Qt, no provider SDK.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .spec import ToolContext, ToolSpec, register


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class VerifyVerdict:
    verified: bool
    kind: str = "none"  # "command", "proof_of_work", "readonly", "postcondition", "human", "none"
    command: Optional[str] = None
    exit_code: Optional[int] = None
    output: str = ""
    evidence: str = ""
    reason: str = ""
    at: int = field(default_factory=_now_ms)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "verified": self.verified,
            "kind": self.kind,
            "command": self.command,
            "exit_code": self.exit_code,
            "output": self.output,
            "evidence": self.evidence,
            "reason": self.reason,
            "at": self.at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VerifyVerdict:
        return cls(
            verified=bool(data.get("verified", False)),
            kind=str(data.get("kind") or "none"),
            command=data.get("command"),
            exit_code=data.get("exit_code"),
            output=str(data.get("output") or ""),
            evidence=str(data.get("evidence") or ""),
            reason=str(data.get("reason") or ""),
            at=int(data.get("at") or _now_ms()),
        )


@dataclass
class VerifyPolicy:
    enabled: bool = False
    command: str = ""
    timeout_seconds: float = 30.0
    require_proof_of_work: bool = False

    @classmethod
    def from_dict(cls, data: Optional[Mapping[str, Any]]) -> VerifyPolicy:
        if not data:
            return cls()
        return cls(
            enabled=bool(data.get("enabled", False)),
            command=str(data.get("command") or "").strip(),
            timeout_seconds=max(1.0, min(float(data.get("timeout_seconds") or 30.0), 600.0)),
            require_proof_of_work=bool(data.get("require_proof_of_work", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "command": self.command,
            "timeout_seconds": self.timeout_seconds,
            "require_proof_of_work": self.require_proof_of_work,
        }


def run_verification(
    policy: VerifyPolicy,
    runtime: Any,
    command: Optional[str] = None,
) -> VerifyVerdict:
    """Execute a verification command through the captured process runner.

    Uses the runtime's workspace, policy, and sandbox containment.
    Never raises an uncaught exception.
    """
    cmd = (command or policy.command or "").strip()
    if not cmd:
        return VerifyVerdict(
            verified=False,
            kind="none",
            reason="No verification command specified",
        )
    timeout = policy.timeout_seconds
    try:
        from .process import run_captured
        from .sandbox import sandbox_from_policy

        perm_policy = getattr(runtime, "policy", None)
        sandbox = sandbox_from_policy(perm_policy) if perm_policy else None
        workspace = getattr(runtime, "workspace", None) or Path.cwd()
        completed = run_captured(cmd, cwd=workspace, timeout=timeout, sandbox=sandbox)
    except Exception as exc:
        return VerifyVerdict(
            verified=False,
            kind="command",
            command=cmd,
            reason=f"Failed to execute verification command: {exc}",
        )

    if completed.timed_out:
        return VerifyVerdict(
            verified=False,
            kind="command",
            command=cmd,
            output=completed.stdout or "",
            reason=f"Verification command timed out after {timeout:.0f}s",
        )

    exit_code = completed.returncode
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    combined = (stdout + ("\n" + stderr if stderr else "")).strip()

    verified = (exit_code == 0)
    return VerifyVerdict(
        verified=verified,
        kind="command",
        command=cmd,
        exit_code=exit_code,
        output=combined[-2000:],
        reason="Verification command passed (exit 0)" if verified else f"Verification command failed with exit code {exit_code}",
    )


# -- Tool Specification ----------------------------------------------------

VERIFY_WORK_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "Verification command to run (e.g. 'pytest -q' or 'ruff check .'). Defaults to policy command.",
        },
        "evidence": {
            "type": "string",
            "description": "Proof of work explanation or readback verification notes.",
        },
    },
}


def verify_work(args: Dict[str, Any], context: ToolContext) -> str:
    policy_dict = {}
    if context.policy and hasattr(context.policy, "verify"):
        policy_dict = context.policy.verify
    policy = VerifyPolicy.from_dict(policy_dict)

    cmd = args.get("command")
    evidence = str(args.get("evidence") or "").strip()
    extra = context.extra if isinstance(context.extra, dict) else {}

    if cmd:
        from .runtime import current as current_runtime

        runtime = current_runtime()
        verdict = run_verification(policy, runtime or context, command=cmd)
        extra["last_verdict"] = verdict

        plan = extra.get("plan")
        if plan is not None and hasattr(plan, "steps"):
            for s in plan.steps:
                if s.status == "in_progress":
                    s.evidence = f"Verified with {cmd}: {'PASSED' if verdict.verified else 'FAILED'}"

        status = "PASSED" if verdict.verified else "FAILED"
        return f"Verification {status} (exit code {verdict.exit_code}):\n{verdict.output or verdict.reason}"
    elif evidence:
        verdict = VerifyVerdict(
            verified=True,
            kind="proof_of_work",
            evidence=evidence,
            reason="Provided proof of work evidence",
        )
        extra["last_verdict"] = verdict

        plan = extra.get("plan")
        if plan is not None and hasattr(plan, "steps"):
            for s in plan.steps:
                if s.status == "in_progress":
                    s.evidence = evidence

        return f"Recorded verification proof of work: {evidence}"
    else:
        if policy.command:
            from .runtime import current as current_runtime

            runtime = current_runtime()
            verdict = run_verification(policy, runtime or context)
            extra["last_verdict"] = verdict
            status = "PASSED" if verdict.verified else "FAILED"
            return f"Verification {status} (exit code {verdict.exit_code}):\n{verdict.output or verdict.reason}"
        return "verify_work requires either a 'command' to run or 'evidence' proof of work."


VERIFY_WORK_SPEC = ToolSpec(
    name="verify_work",
    description=(
        "Run a verification command (tests, linter, build check) or record proof-of-work "
        "evidence to confirm that changes are correct before completing a task."
    ),
    parameters=VERIFY_WORK_SCHEMA,
    tool_class="native",
    handler=verify_work,
    tags=("verify", "test", "gate"),
    max_output_chars=12_000,
)

SPECS: Tuple[ToolSpec, ...] = (VERIFY_WORK_SPEC,)


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
