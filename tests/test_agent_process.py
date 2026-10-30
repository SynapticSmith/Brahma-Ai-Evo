"""Process containment: a timeout must end the tree, not just the shell.

Pure Python on purpose. The Windows path uses a job object through ``ctypes``;
the POSIX path starts a new session. Both are exercised by the tree test on the
platform the suite is running on.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent.process import ProcessResult, _windows_job, run_captured


def _cmd(code: str) -> str:
    """A shell command that runs ``code`` in a fresh interpreter."""
    return f'"{sys.executable}" -c "{code}"'


def test_run_captured_returns_output_and_exit_code(tmp_path: Path):
    result = run_captured(_cmd("print(123)"), cwd=tmp_path, timeout=20)

    assert isinstance(result, ProcessResult)
    assert result.returncode == 0
    assert "123" in result.stdout
    assert result.stderr == ""
    assert not result.timed_out


def test_run_captured_reports_stderr_and_a_nonzero_exit(tmp_path: Path):
    result = run_captured(
        _cmd("import sys; sys.stderr.write('boom'); sys.exit(7)"),
        cwd=tmp_path,
        timeout=20,
    )

    assert result.returncode == 7
    assert "boom" in result.stderr
    assert not result.timed_out


def test_run_captured_times_out_and_keeps_partial_output(tmp_path: Path):
    result = run_captured(
        _cmd("import time; print('partial', flush=True); time.sleep(30)"),
        cwd=tmp_path,
        timeout=1,
    )

    assert result.timed_out
    assert "partial" in result.stdout


def test_stdin_is_closed_so_a_prompt_cannot_block(tmp_path: Path):
    result = run_captured(
        _cmd("import sys; print('stdin=' + repr(sys.stdin.read()))"),
        cwd=tmp_path,
        timeout=10,
    )

    assert result.returncode == 0
    assert "stdin=''" in result.stdout


def test_a_timeout_kills_the_whole_process_tree(tmp_path: Path):
    """The shell starts a grandchild; only the grandchild writes the marker."""
    inner = "import time; time.sleep(2.0); open('marker.txt', 'w').write('x')"
    helper = tmp_path / "spawn.py"
    helper.write_text(
        "import subprocess, sys, time\n"
        f"code = {inner!r}\n"
        "subprocess.Popen([sys.executable, '-c', code])\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    result = run_captured(
        f'"{sys.executable}" "{helper}"',
        cwd=tmp_path,
        timeout=1,
    )

    assert result.timed_out
    # The grandchild would write at t+2s. A kill that stopped at the shell
    # would leave it running.
    time.sleep(2.5)
    assert not (tmp_path / "marker.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows job objects")
def test_a_live_process_is_assigned_to_a_job():
    """Prove the containment path is used, not just the taskkill fallback."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"]
    )
    job = _windows_job(process)
    try:
        assert job is not None
    finally:
        if job is not None:
            job.terminate()
            job.close()
        process.kill()
        process.wait(timeout=10)
