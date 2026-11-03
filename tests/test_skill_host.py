"""Tests for process-isolated SkillHost execution (SEC-04, SEC-05)."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import pytest

from core.dynamic_registry import DynamicSkill
from core.grants import issue_grant
from core.skill_host import SkillHostError, execute_in_skill_host


def test_skill_host_executes_sync_and_async_functions(tmp_path: Path):
    """SkillHost runs functions out-of-process and returns clean JSON results."""
    # 1. Synchronous skill
    sync_code = (
        "def execute(a: int, b: int):\n"
        "    return {'sum': a + b, 'product': a * b}\n"
    )
    sync_file = tmp_path / "sync_skill.py"
    sync_file.write_text(sync_code, encoding="utf-8")

    res_sync = execute_in_skill_host(sync_file, {"a": 6, "b": 7})
    assert res_sync == {"sum": 13, "product": 42}

    # 2. Async skill
    async_code = (
        "import asyncio\n"
        "async def execute(message: str):\n"
        "    await asyncio.sleep(0.01)\n"
        "    return f'Echo: {message}'\n"
    )
    async_file = tmp_path / "async_skill.py"
    async_file.write_text(async_code, encoding="utf-8")

    res_async = execute_in_skill_host(async_file, {"message": "Hello Brahma"})
    assert res_async == "Echo: Hello Brahma"


def test_skill_host_enforces_timeout_containment(tmp_path: Path):
    """Hanging skills are terminated when timeout expires without leaving orphan processes."""
    hang_code = (
        "import time\n"
        "def execute():\n"
        "    time.sleep(30)\n"
        "    return 'should_not_reach'\n"
    )
    hang_file = tmp_path / "hang_skill.py"
    hang_file.write_text(hang_code, encoding="utf-8")

    with pytest.raises(TimeoutError, match="timed out"):
        execute_in_skill_host(hang_file, {}, timeout=1.0)


def test_skill_host_handles_exceptions_safely(tmp_path: Path):
    """Exceptions raised in isolated skills are captured and formatted as SkillHostError."""
    err_code = (
        "def execute():\n"
        "    raise ValueError('Custom synthetic error occurred')\n"
    )
    err_file = tmp_path / "err_skill.py"
    err_file.write_text(err_code, encoding="utf-8")

    with pytest.raises(SkillHostError, match="Custom synthetic error occurred"):
        execute_in_skill_host(err_file, {})


def test_skill_host_verifies_grant_tampering(tmp_path: Path):
    """Grant verification catches code tampering before launching the child worker."""
    code = "def execute(): return 'original'\n"
    code_file = tmp_path / "grant_skill.py"
    code_file.write_text(code, encoding="utf-8")

    code_hash = hashlib.sha256(code_file.read_bytes()).hexdigest()
    grant = issue_grant(code_hash)

    # Clean run with valid grant
    res = execute_in_skill_host(code_file, {}, grant=grant)
    assert res == "original"

    # Tamper with code file
    code_file.write_text(code + "# modified byte\n", encoding="utf-8")

    # Second run must fail grant verification immediately
    with pytest.raises(PermissionError, match="grant verification failed"):
        execute_in_skill_host(code_file, {}, grant=grant)


def test_dynamic_skill_executes_synthetic_via_skill_host(tmp_path: Path):
    """Synthetic skills loaded into DynamicSkill run via SkillHost with zero in-process module import."""
    skill_code = "def execute(val: int):\n    return val * 10\n"
    skill_file = tmp_path / "skill.py"
    skill_file.write_text(skill_code, encoding="utf-8")

    manifest = {
        "name": "isolated_synthetic",
        "is_synthetic": True,
        "parameters": {"type": "OBJECT", "properties": {"val": {"type": "INTEGER"}}},
    }

    skill = DynamicSkill(skill_file, manifest)
    assert skill.is_synthetic is True
    assert skill.module is None

    # Execute sync
    result = skill.execute_sync(val=5)
    assert result == 50

    # In-process module was NEVER imported into current process!
    assert skill.module is None
    assert "isolated_synthetic" not in sys.modules
