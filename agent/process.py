"""Run one shell command and take its whole process tree down on timeout.

``subprocess.run(..., timeout=...)`` kills only the immediate child. A tool that
runs ``pytest``, a build, or a script that starts another process therefore
leaves the grandchildren behind when it times out: they keep writing files,
hold ports, and on Windows keep the pipes open. That is especially bad for an
agent, which retries the same command and now races its own orphan.

This module keeps the familiar ``Popen`` + ``communicate(timeout=...)`` shape
but adds one piece of containment:

* **Windows**: the shell is assigned to a job object immediately after it
  starts. ``TerminateJobObject`` kills every process in that job, including
  children that were started indirectly.
* **POSIX**: the command starts a new session, so a timeout can ``killpg`` the
  whole group rather than one pid.
* **Fallback**: ``taskkill /T`` on Windows, then ``Popen.kill()``. Containment
  is best-effort, never a reason to fail the call.

Containment is not a sandbox. It constrains *lifetime*, not filesystem or
network access; the permission policy still decides whether a command may run
at all. ``stdin`` is ``DEVNULL`` on purpose: an agent call must not hang on a
password prompt that no human can see.

Stdlib only, no Qt, no provider SDK — the harness CI installs just pytest.
"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class ProcessResult:
    """What a finished (or timed-out) command produced."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


class _JobHandle:
    """A Windows job object and the few calls needed to end it."""

    def __init__(self, handle: Any, kernel32: Any) -> None:
        self._handle = handle
        self._kernel32 = kernel32

    def terminate(self) -> None:
        if self._handle is None:
            return
        try:
            self._kernel32.TerminateJobObject(self._handle, 1)
        except Exception:  # pragma: no cover - defensive, per-platform API
            pass

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._kernel32.CloseHandle(self._handle)
        except Exception:  # pragma: no cover - defensive, per-platform API
            pass
        self._handle = None


def _windows_job(process: subprocess.Popen) -> Optional[_JobHandle]:
    """Assign ``process`` to a fresh job object, or return ``None``.

    ``OpenProcess`` is used instead of ``Popen._handle`` so this stays on the
    public surface. The process is assigned as soon as it exists; a child that
    it spawns afterwards inherits the job.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        # PROCESS_SET_QUOTA is required by AssignProcessToJobObject;
        # PROCESS_TERMINATE is the fallback the caller may need.
        process_handle = kernel32.OpenProcess(
            0x0100 | 0x0001, False, int(process.pid)
        )
        if not process_handle:
            return None

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            kernel32.CloseHandle(process_handle)
            return None

        try:
            assigned = kernel32.AssignProcessToJobObject(job, process_handle)
        finally:
            kernel32.CloseHandle(process_handle)

        if not assigned:
            kernel32.CloseHandle(job)
            return None
        return _JobHandle(job, kernel32)
    except Exception:  # pragma: no cover - old Windows, restricted token, WOW64
        return None


def _kill_tree(process: subprocess.Popen, job: Optional[_JobHandle]) -> None:
    """Terminate the whole tree. Best-effort: never raise from cleanup."""
    if job is not None:
        job.terminate()
    elif os.name == "nt":
        # No job object (old Windows or a restricted token). taskkill /T walks
        # the tree; if it is unavailable, the single kill below is still done.
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except Exception:  # pragma: no cover - taskkill missing/denied
            pass
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    try:
        process.kill()
    except OSError:
        pass


def run_captured(command: str, *, cwd: Path, timeout: float) -> ProcessResult:
    """Run ``command`` through the shell with the whole tree under control.

    ``stdin`` is closed so a prompt fails fast instead of blocking the run to
    the deadline. On timeout the tree is terminated before the remaining output
    is drained, so the returned text includes whatever the command managed to
    print first. An ``OSError`` from process creation is propagated; the tool
    boundary turns it into model-readable text.
    """
    process = subprocess.Popen(
        command,
        shell=True,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        # POSIX: a new session is what makes killpg safe. Windows ignores it;
        # the job object provides the same containment there.
        start_new_session=(os.name != "nt"),
    )
    job = _windows_job(process)
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(process, job)
            # The tree is gone, so the pipes close and this returns promptly.
            stdout, stderr = process.communicate()
    finally:
        if job is not None:
            job.close()

    return ProcessResult(
        returncode=int(process.returncode if process.returncode is not None else -1),
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
    )
