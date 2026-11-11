"""Tests for verification gate, proof-of-work completion, and verify tools (B1 / B6)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.loop import AgentLoop, STOP_COMPLETED, STOP_UNVERIFIED
from agent.plan import Plan
from agent.runtime import AgentRuntime
from agent.spec import ToolContext
from agent.verify import (
    VerifyPolicy,
    VerifyVerdict,
    run_verification,
    verify_work,
)


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


def test_verify_policy_and_verdict_round_trip():
    policy = VerifyPolicy(
        enabled=True,
        command="pytest -q",
        timeout_seconds=45.0,
        require_proof_of_work=True,
    )
    d = policy.to_dict()
    assert d["enabled"]
    assert d["command"] == "pytest -q"
    assert d["timeout_seconds"] == 45.0
    assert d["require_proof_of_work"]

    restored = VerifyPolicy.from_dict(d)
    assert restored.enabled
    assert restored.command == "pytest -q"
    assert restored.timeout_seconds == 45.0
    assert restored.require_proof_of_work

    verdict = VerifyVerdict(
        verified=True,
        kind="command",
        command="pytest -q",
        exit_code=0,
        output="3 passed",
        reason="ok",
    )
    vd = verdict.to_dict()
    assert vd["verified"]
    assert vd["exit_code"] == 0

    restored_v = VerifyVerdict.from_dict(vd)
    assert restored_v.verified
    assert restored_v.output == "3 passed"


def test_run_verification_executes_command_safely(tmp_path: Path):
    runtime = _runtime(tmp_path)
    policy = VerifyPolicy(timeout_seconds=10.0)

    # 1. Success exit 0
    res_pass = run_verification(policy, runtime, command='python -c "print(\'tests passed\'); exit(0)"')
    assert res_pass.verified
    assert res_pass.exit_code == 0
    assert "tests passed" in res_pass.output

    # 2. Failure non-zero
    res_fail = run_verification(policy, runtime, command='python -c "print(\'syntax error\'); exit(2)"')
    assert not res_fail.verified
    assert res_fail.exit_code == 2
    assert "syntax error" in res_fail.output

    # 3. Empty command
    res_empty = run_verification(policy, runtime, command="")
    assert not res_empty.verified
    assert res_empty.kind == "none"


def test_verify_work_tool(tmp_path: Path):
    plan = Plan(title="Feature")
    plan.add_or_update_step("step_1", "Write file", status="in_progress")
    extra: Dict[str, Any] = {"plan": plan}
    ctx = ToolContext(workspace=tmp_path, extra=extra)

    # 1. Verification with command
    res1 = verify_work(
        {"command": 'python -c "print(\'unit tests passed\'); exit(0)"'},
        ctx,
    )
    assert "PASSED" in res1
    assert "exit code 0" in res1
    assert ctx.extra["last_verdict"].verified
    assert "unit tests passed" in plan.get_step("step_1").evidence

    # 2. Verification with proof-of-work evidence
    res2 = verify_work(
        {"evidence": "Read back config file and confirmed line 14 has port=8443."},
        ctx,
    )
    assert "Recorded verification proof of work" in res2
    assert ctx.extra["last_verdict"].verified
    assert ctx.extra["last_verdict"].kind == "proof_of_work"
    assert "port=8443" in plan.get_step("step_1").evidence


def test_agent_loop_readonly_turn_is_verified_automatically(tmp_path: Path):
    # Pure conversational or read task has no mutations; it completes verified
    policy = VerifyPolicy(require_proof_of_work=True)
    script = Script([{"role": "assistant", "content": "The answer is 42."}])

    events: List[Dict[str, Any]] = []
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path),
        verify_policy=policy,
        on_event=events.append,
    )

    result = loop.run("what is the answer?")
    assert result.stop_reason == STOP_COMPLETED
    assert result.ok
    assert result.verified

    stop_events = [e for e in events if e.get("type") == "stop"]
    assert len(stop_events) == 1
    assert stop_events[0]["verified"] is True


def test_agent_loop_verification_gate_rejects_unverified_mutating_turn(tmp_path: Path):
    policy = VerifyPolicy(require_proof_of_work=True)

    # Turn 1: model calls write_file
    # Turn 2: model says "I'm done" without verifying. Gate rejects!
    # Turn 3: model calls verify_work with evidence
    # Turn 4: model says "Now done." Completes verified.
    test_file = tmp_path / "hello.txt"
    responses = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": str(test_file), "content": "world"}),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "File written, done!"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c2",
                    "type": "function",
                    "function": {
                        "name": "verify_work",
                        "arguments": json.dumps({"evidence": "Read back hello.txt, verified content is 'world'."}),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "Verified and finished."},
    ]

    script = Script(responses)
    events: List[Dict[str, Any]] = []
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path),
        verify_policy=policy,
        on_event=events.append,
        max_turns=6,
    )

    result = loop.run("write hello.txt")
    assert result.stop_reason == STOP_COMPLETED
    assert result.ok
    assert result.verified

    # Verification gate rejection was emitted on turn 2
    rejections = [e for e in events if e.get("type") == "verification_gate_rejected"]
    assert len(rejections) == 1


def test_agent_loop_verification_gate_stops_with_unverified_on_max_turns(tmp_path: Path):
    policy = VerifyPolicy(require_proof_of_work=True)
    test_file = tmp_path / "foo.txt"

    # Turn 1: model writes file
    # Turn 2: model claims done without verification. Max turns reached (2).
    responses = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": str(test_file), "content": "bar"}),
                    },
                }
            ],
        },
        {"role": "assistant", "content": "Done without proof."},
    ]

    script = Script(responses)
    loop = AgentLoop(
        script,
        runtime=_runtime(tmp_path),
        verify_policy=policy,
        max_turns=2,
    )

    result = loop.run("write foo.txt")
    assert result.stop_reason == STOP_UNVERIFIED
    assert not result.ok
    assert not result.verified
