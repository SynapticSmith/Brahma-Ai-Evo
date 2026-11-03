"""SkillHost client: process-isolated execution for synthetic skills (SEC-04, SEC-05).

Ensures model-synthesized code runs out-of-process under Windows Job Object
containment, closed standard input, and environment credential scrubbing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
import tempfile
from typing import Any, Dict, Optional

from agent.process import run_captured
from agent.sandbox import SandboxConfig
from core.grants import verify_grant

logger = logging.getLogger("SkillHost")


class SkillHostError(RuntimeError):
    """Raised when isolated skill execution fails."""


def execute_in_skill_host(
    code_path: Path | str,
    kwargs: Dict[str, Any],
    grant: Optional[Dict[str, Any]] = None,
    timeout: float = 45.0,
) -> Any:
    """Executes a skill function out-of-process under Windows Job Object & scrubbed env.

    Args:
        code_path: Path to the Python file containing `execute(**kwargs)`.
        kwargs: Keyword arguments dictionary for `execute`.
        grant: Optional cryptographic grant to verify before execution.
        timeout: Maximum seconds allowed before terminating the process tree.

    Returns:
        The return value of the skill function.

    Raises:
        SkillHostError: If the skill raises an exception or fails.
        TimeoutError: If execution exceeds the timeout limit.
        PermissionError: If grant verification fails.
    """
    code_path = Path(code_path).resolve()
    if not code_path.exists():
        raise FileNotFoundError(f"Skill code file does not exist: {code_path}")

    # Optional grant check
    if grant:
        import hashlib
        code_bytes = code_path.read_bytes()
        actual_hash = hashlib.sha256(code_bytes).hexdigest()
        ok, reason = verify_grant(grant, actual_hash)
        if not ok:
            raise PermissionError(f"SkillHost grant verification failed: {reason}")

    with tempfile.TemporaryDirectory(prefix="brahma_skill_run_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        params_file = tmp_path / "params.json"
        output_file = tmp_path / "output.json"

        params_file.write_text(json.dumps(kwargs), encoding="utf-8")

        host_main_path = (Path(__file__).resolve().parent.parent / "sandbox" / "host_main.py").resolve()
        runner_path = host_main_path if host_main_path.exists() else (Path(__file__).resolve().parent / "skill_runner.py").resolve()
        cmd = (
            f'"{sys.executable}" "{runner_path}" '
            f'--code "{code_path}" '
            f'--params "{params_file}" '
            f'--output "{output_file}"'
        )

        sandbox = SandboxConfig(env_scrub=True)
        res = run_captured(
            cmd,
            cwd=tmp_path,
            timeout=timeout,
            sandbox=sandbox,
        )

        if res.timed_out:
            raise TimeoutError(f"Skill execution timed out after {timeout} seconds")

        if not output_file.exists():
            err_msg = res.stderr.strip() or res.stdout.strip() or f"Process exited with code {res.returncode}"
            raise SkillHostError(f"SkillHost execution failed without producing output: {err_msg}")

        try:
            data = json.loads(output_file.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SkillHostError(f"Failed to parse skill output: {exc}")

        if not data.get("success"):
            err = data.get("error") or "Unknown skill execution error"
            raise SkillHostError(f"Skill error: {err}")

        return data.get("result")
