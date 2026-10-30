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

The optional :class:`~agent.sandbox.SandboxConfig` adds the three things this
module can enforce: a command prefix that delegates to an external sandbox,
credential-shaped environment variables withheld from the child, and job-object
resource limits (memory, process count, CPU time). The prefix is trusted
configuration; the module never pretends it built a filesystem sandbox.

Stdlib only, no Qt, no provider SDK — the harness CI installs just pytest.
"""
from __future__ import annotations

import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .sandbox import SandboxConfig, build_env, prepare_command


@dataclass(frozen=True)
class ProcessResult:
    """What a finished (or timed-out) command produced."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False
    #: True when a sandbox prefix was used rather than the bare shell.
    sandboxed: bool = False
    #: Number of credential-shaped variables withheld from the child.
    env_scrubbed: int = 0
    #: Non-empty only when limits were requested but could not be applied.
    limits_note: str = ""


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

    def set_limits(self, config: SandboxConfig) -> str:
        """Apply memory/process/CPU limits; return a note only on failure.

        ``SetInformationJobObject`` can reject a limit on an old or restricted
        Windows. That must not silently look enforced, so the caller turns the
        returned sentence into model-readable text.
        """
        if self._handle is None or not config.has_limits:
            return ""
        try:
            import ctypes

            class _BasicLimits(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32),
                ]

            class _IoCounters(ctypes.Structure):
                _fields_ = [
                    ("ReadOperationCount", ctypes.c_uint64),
                    ("WriteOperationCount", ctypes.c_uint64),
                    ("OtherOperationCount", ctypes.c_uint64),
                    ("ReadTransferCount", ctypes.c_uint64),
                    ("WriteTransferCount", ctypes.c_uint64),
                    ("OtherTransferCount", ctypes.c_uint64),
                ]

            class _ExtendedLimits(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", _BasicLimits),
                    ("IoInfo", _IoCounters),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            # JobObjectExtendedLimitInformation == 9
            info = _ExtendedLimits()
            flags = 0
            if config.max_memory_mb:
                memory = config.max_memory_mb * 1024 * 1024
                info.ProcessMemoryLimit = memory
                info.JobMemoryLimit = memory
                flags |= 0x00000100 | 0x00000200  # PROCESS_MEMORY | JOB_MEMORY
            if config.max_processes:
                info.BasicLimitInformation.ActiveProcessLimit = config.max_processes
                flags |= 0x00000008  # ACTIVE_PROCESS
            if config.max_cpu_seconds:
                ticks = config.max_cpu_seconds * 10_000_000
                info.BasicLimitInformation.PerProcessUserTimeLimit = ticks
                info.BasicLimitInformation.PerJobUserTimeLimit = ticks
                flags |= 0x00000002 | 0x00000004  # PROCESS_TIME | JOB_TIME
            info.BasicLimitInformation.LimitFlags = flags

            kernel32 = self._kernel32
            kernel32.SetInformationJobObject.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_void_p,
                ctypes.c_uint32,
            ]
            kernel32.SetInformationJobObject.restype = ctypes.c_int
            applied = kernel32.SetInformationJobObject(
                self._handle, 9, ctypes.byref(info), ctypes.sizeof(info)
            )
            if applied:
                return ""
            return "resource limits were not applied (the job object rejected them)"
        except Exception:  # pragma: no cover - old Windows / ctypes layout
            return "resource limits were not applied (the job object was unavailable)"


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


def _apply_posix_limits(process: subprocess.Popen, config: SandboxConfig) -> str:
    """Apply what POSIX can: address space and CPU time.

    ``RLIMIT_NPROC`` is per *user*, not per tree, so it is deliberately not
    applied: one command would then limit every other process the user starts.
    A partial application is reported rather than hidden.
    """
    try:
        import resource
    except ImportError:  # pragma: no cover - Windows never reaches this
        return "resource limits were not applied (the resource module is unavailable)"

    def _set(name: str, value: int) -> bool:
        limit = getattr(resource, name, None)
        prlimit = getattr(resource, "prlimit", None)
        if limit is None or prlimit is None:  # pragma: no cover - exotic POSIX
            return False
        try:
            prlimit(process.pid, limit, (value, value))
            return True
        except (OSError, ValueError):  # pragma: no cover - permission/race
            return False

    missed = []
    if config.max_memory_mb and not _set("RLIMIT_AS", config.max_memory_mb * 1024 * 1024):
        missed.append("memory")
    if config.max_cpu_seconds and not _set("RLIMIT_CPU", config.max_cpu_seconds):
        missed.append("cpu")
    if config.max_processes:
        missed.append("process count (Windows-only)")
    if missed:
        return "resource limits were not applied: " + ", ".join(missed)
    return ""


def run_captured(
    command: str,
    *,
    cwd: Path,
    timeout: float,
    sandbox: Optional[SandboxConfig] = None,
) -> ProcessResult:
    """Run ``command`` through the shell with the whole tree under control.

    ``stdin`` is closed so a prompt fails fast instead of blocking the run to
    the deadline. On timeout the tree is terminated before the remaining output
    is drained, so the returned text includes whatever the command managed to
    print first. An ``OSError`` from process creation is propagated; the tool
    boundary turns it into model-readable text.

    ``sandbox=None`` preserves the historical behaviour: inherit the parent
    environment, run the command through the shell. A configured sandbox
    scrubs credential-shaped variables, optionally prepends a trusted command
    prefix, and applies resource limits.
    """
    if sandbox is None:
        argv: Any = command
        use_shell = True
        env = None
        withheld = 0
    else:
        argv, use_shell = prepare_command(command, sandbox.prefix, cwd)
        env, withheld = build_env(sandbox)

    process = subprocess.Popen(
        argv,
        shell=use_shell,
        cwd=str(cwd),
        env=env,
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
    limits_note = ""
    if sandbox is not None and sandbox.has_limits:
        if os.name == "nt":
            limits_note = (
                job.set_limits(sandbox)
                if job is not None
                else "resource limits were not applied (no job object)"
            )
        else:
            limits_note = _apply_posix_limits(process, sandbox)
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
        sandboxed=bool(sandbox is not None and sandbox.prefix),
        env_scrubbed=withheld,
        limits_note=limits_note,
    )
