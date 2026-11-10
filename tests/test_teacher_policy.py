"""Tests for Teacher Escalation Policy and Capability Synthesis Safety Layer (§11.1, §11.2, Phase 7)."""
from __future__ import annotations

import json
from pathlib import Path

from agent.teacher_policy import (
    EgressScrubber,
    ExecutionGap,
    TeacherBudget,
    TeacherEscalationPolicy,
)


def test_teacher_budget_tracking(tmp_path: Path):
    ledger = tmp_path / "ledger.json"
    budget = TeacherBudget(
        max_daily_calls=3,
        max_daily_tokens=2_000,
        max_task_attempts=2,
        ledger_path=ledger,
    )

    # Initial check should pass
    ok, msg = budget.check_budget("task-1", estimated_tokens=500)
    assert ok is True

    # Record first usage
    budget.record_usage("task-1", tokens=800, calls=1)
    ok, _ = budget.check_budget("task-1", estimated_tokens=500)
    assert ok is True

    # Record second usage for task-1
    budget.record_usage("task-1", tokens=800, calls=1)
    # Per-task attempt cap (2) reached
    ok, msg = budget.check_budget("task-1", estimated_tokens=100)
    assert ok is False
    assert "Per-task escalation limit" in msg

    # Task-2 should still pass if token budget remains
    ok2, _ = budget.check_budget("task-2", estimated_tokens=300)
    assert ok2 is True

    # Exceeding token cap
    ok_exceed, msg_exceed = budget.check_budget("task-2", estimated_tokens=1_000)
    assert ok_exceed is False
    assert "token budget" in msg_exceed


def test_egress_scrubber_redaction(tmp_path: Path):
    ws = tmp_path / "my_project"
    home = tmp_path / "Users" / "TestUser"

    raw_text = f"""
    Encountered error in {ws}\\src\\main.py.
    User config is stored at {home}\\.config\\keys.
    Contact dev at admin@brahma.ai for support.
    Using OpenAI key sk-1234567890abcdef12345678 and Google key AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q.
    Authorization: Bearer mysecrettoken123456789012345
    """

    scrubbed = EgressScrubber.scrub(raw_text, workspace=ws, home=home)

    assert str(ws) not in scrubbed
    assert "<WORKSPACE_ROOT>" in scrubbed
    assert str(home) not in scrubbed
    assert "<USER_HOME>" in scrubbed
    assert "admin@brahma.ai" not in scrubbed
    assert "<EMAIL_REDACTED>" in scrubbed
    assert "sk-1234567890abcdef12345678" not in scrubbed
    assert "AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6Q" not in scrubbed
    assert "<TOKEN_REDACTED>" in scrubbed


def test_execution_gap_triggers():
    policy = TeacherEscalationPolicy()

    # First failure on a command does not immediately trigger escalation
    gap1 = policy.track_failure("task-abc", "python run_custom.py", "ImportError: no module")
    assert gap1.is_eligible_for_escalation() is False
    assert gap1.consecutive_failures == 1

    # Second failure on the exact same command triggers escalation (§11.1)
    gap2 = policy.track_failure("task-abc", "python run_custom.py", "ImportError: no module")
    assert gap2.is_eligible_for_escalation() is True
    assert gap2.consecutive_failures == 2

    # Missing capability immediately qualifies
    gap_missing = ExecutionGap(
        task_id="task-def",
        gap_type="missing_capability",
        description="Missing audio transcriber",
    )
    assert gap_missing.is_eligible_for_escalation() is True


def test_stage_synthesized_skill_provenance(tmp_path: Path):
    staged_root = tmp_path / "staged"
    policy = TeacherEscalationPolicy()

    manifest = {
        "name": "auto_crop_tool",
        "description": "Crops images deterministically",
        "parameters": {"type": "OBJECT", "properties": {}},
    }
    code = "def execute(**kwargs):\n    return {'status': 'cropped'}\n"
    test_cases = [{"input": {}, "expected": {"status": "cropped"}}]

    brief = "Please build an image cropper tool."

    res = policy.stage_synthesized_skill(
        skill_name="auto_crop_tool",
        manifest=manifest,
        code=code,
        test_cases=test_cases,
        teacher_model="gemini-2.5-pro",
        prompt_brief=brief,
        staged_dir=staged_root,
    )

    assert res["ok"] is True
    assert res["staged"] is True
    assert res["activated"] is False  # INVARIANT: never activated automatically

    skill_dir = staged_root / "auto_crop_tool"
    assert (skill_dir / "skill.py").is_file()
    assert (skill_dir / "manifest.json").is_file()
    assert (skill_dir / "test_cases.json").is_file()
    assert (skill_dir / "staged.json").is_file()
    assert (skill_dir / "provenance.json").is_file()

    prov = json.loads((skill_dir / "provenance.json").read_text(encoding="utf-8"))
    assert prov["teacher_model"] == "gemini-2.5-pro"
    assert len(prov["prompt_hash"]) == 64  # SHA-256
    assert prov["approved"] is False
