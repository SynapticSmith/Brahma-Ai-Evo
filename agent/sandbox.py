"""Policy-controlled isolation for ``run_command``.

Three independent knobs, all under ``exec.sandbox`` in the permission policy:

* ``prefix`` — an argument vector that receives the shell command as one
  argument (or through a ``{command}`` item). This is the supported way to
  delegate to a real sandbox: Docker, ``bwrap``, a Windows Sandbox launcher, a
  CI runner wrapper. The harness cannot verify the prefix, so it is trusted
  configuration, not a security boundary of its own.
* ``env_scrub`` / ``env_keep`` — withhold secret-looking environment variables
  from the child, so a command cannot accidentally print or post the model's
  own credentials.
* ``max_memory_mb`` / ``max_processes`` / ``max_cpu_seconds`` — resource limits,
  applied with a Windows job object; POSIX applies memory and CPU with
  ``resource.prlimit`` when available. Zero means unlimited.

Why a prefix instead of a built-in sandbox: Windows has no portable, stdlib
primitive for filesystem or network confinement. An AppContainer needs ACLs on
the interpreter and workspace, silently fails for a normal Python install, and
would be worse than an honest hook. A configured prefix is explicit, auditable
and testable; the docs say plainly that it is only as strong as the program the
deployment chose.

This module stays stdlib-only and imports nothing from the GUI or providers.
"""
from __future__ import annotations

import os
import re
import shutil
import socket
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


class SandboxError(ValueError):
    """``exec.sandbox`` is malformed."""


#: Upper bounds keep a typo from producing an absurd job-object limit. They are
#: deliberately generous: 16 TiB, ten thousand processes, one day of CPU.
MAX_MEMORY_MB = 16_777_216
MAX_PROCESSES = 10_000
MAX_CPU_SECONDS = 86_400

#: A name is secret-looking when a whole underscore-delimited component is one
#: of these. Boundaries matter: ``TOKENIZERS_PARALLELISM`` and ``KEYBOARD`` must
#: survive. ``PWD`` is excluded because POSIX uses it for the current directory.
_SECRET_NAME = re.compile(
    r"(?:^|_)(?:"
    r"KEY|SECRET|TOKEN|PASSWORD|PASSWD|PASS|CREDENTIALS?|"
    r"DATABASE_URL|MYSQL_PWD|PGPASSWORD"
    r")(?:_|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SandboxConfig:
    """The parsed ``exec.sandbox`` section.

    Defaults match the policy defaults: scrub credentials, no prefix, no
    resource limits. A direct ``SandboxConfig()`` therefore behaves like the
    built-in policy, while ``run_captured(sandbox=None)`` keeps the historical
    "inherit everything" behaviour for callers that are not the tool.
    """

    prefix: Tuple[str, ...] = ()
    require_confined: bool = False
    env_scrub: bool = True
    env_keep: Tuple[str, ...] = ()
    max_memory_mb: int = 0
    max_processes: int = 0
    max_cpu_seconds: int = 0

    @property
    def has_limits(self) -> bool:
        return bool(self.max_memory_mb or self.max_processes or self.max_cpu_seconds)

    @property
    def active(self) -> bool:
        return (
            bool(self.prefix)
            or self.require_confined
            or self.env_scrub
            or self.has_limits
        )

    def describe(self) -> Dict[str, Any]:
        return {
            "prefix": list(self.prefix),
            "require_confined": self.require_confined,
            "env_scrub": self.env_scrub,
            "env_keep": list(self.env_keep),
            "max_memory_mb": self.max_memory_mb,
            "max_processes": self.max_processes,
            "max_cpu_seconds": self.max_cpu_seconds,
        }


def _strings(raw: Any, where: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise SandboxError(f"{where} must be a list of strings")
    values = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise SandboxError(f"{where} must contain only non-empty strings")
        values.append(item)
    return tuple(values)


def _limit(raw: Any, where: str, maximum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SandboxError(f"{where} must be an integer")
    if raw < 0:
        raise SandboxError(f"{where} cannot be negative")
    if raw > maximum:
        raise SandboxError(f"{where} cannot exceed {maximum}")
    return raw


def parse_sandbox(raw: Any) -> SandboxConfig:
    """Validate and normalise one ``exec.sandbox`` mapping.

    ``None`` means "use the policy defaults" (scrub on, everything else off).
    Unknown keys are ignored so a newer policy file does not make an older
    build refuse to start.
    """
    if raw is None:
        return SandboxConfig()
    if not isinstance(raw, Mapping):
        raise SandboxError("exec.sandbox must be an object")

    env_scrub = raw.get("env_scrub", True)
    if not isinstance(env_scrub, bool):
        raise SandboxError("exec.sandbox.env_scrub must be true or false")

    require_confined = raw.get("require_confined", False)
    if not isinstance(require_confined, bool):
        raise SandboxError("exec.sandbox.require_confined must be true or false")

    return SandboxConfig(
        prefix=_strings(raw.get("prefix"), "exec.sandbox.prefix"),
        require_confined=require_confined,
        env_scrub=env_scrub,
        env_keep=_strings(raw.get("env_keep"), "exec.sandbox.env_keep"),
        max_memory_mb=_limit(
            raw.get("max_memory_mb", 0), "exec.sandbox.max_memory_mb", MAX_MEMORY_MB
        ),
        max_processes=_limit(
            raw.get("max_processes", 0), "exec.sandbox.max_processes", MAX_PROCESSES
        ),
        max_cpu_seconds=_limit(
            raw.get("max_cpu_seconds", 0), "exec.sandbox.max_cpu_seconds", MAX_CPU_SECONDS
        ),
    )


def sandbox_from_policy(policy: Any) -> Optional[SandboxConfig]:
    """Read the sandbox section from a runtime policy, if there is one.

    ``None`` is not an error: direct handler tests often pass a bare
    ``ToolContext`` with no policy. In that case the historical inherit-all
    behaviour is kept rather than silently scrubbing a caller's environment.
    """
    if policy is None:
        return None
    data = getattr(policy, "data", None)
    if data is None and isinstance(policy, Mapping):
        data = policy
    if not isinstance(data, Mapping):
        return None
    exec_cfg = data.get("exec")
    if not isinstance(exec_cfg, Mapping):
        return SandboxConfig()
    return parse_sandbox(exec_cfg.get("sandbox"))


def is_secret_name(name: str) -> bool:
    """Whether ``name`` looks like it carries a credential."""
    return bool(_SECRET_NAME.search(name))


def build_env(
    config: SandboxConfig, environ: Optional[Mapping[str, str]] = None
) -> Tuple[Optional[Dict[str, str]], int]:
    """Return ``(env, withheld)`` for a child process.

    ``env`` is ``None`` when scrubbing is disabled, which tells ``Popen`` to
    inherit the parent environment unchanged. ``env_keep`` is compared
    case-insensitively because Windows environment names are.
    """
    if not config.env_scrub:
        return None, 0
    base = dict(os.environ if environ is None else environ)
    keep = {name.upper() for name in config.env_keep}
    env: Dict[str, str] = {}
    withheld = 0
    for name, value in base.items():
        if name.upper() in keep or not is_secret_name(name):
            env[name] = value
        else:
            withheld += 1
    return env, withheld


def prepare_command(
    command: str,
    prefix: Sequence[str],
    cwd: Optional[Path] = None,
) -> Tuple[Any, bool]:
    """Return ``(popen_args, use_shell)`` for one command.

    With no prefix the command is a shell string (unchanged behaviour). With a
    prefix the result is an argv list run *without* a shell: the prefix program
    receives the command as one exact argument, so quoting is the operating
    system's problem rather than ``cmd.exe``'s. That is why the documented
    prefix ends in ``sh -c`` or ``cmd /c``.

    ``{command}`` may appear once as a whole item; without it the command is
    appended. ``{cwd}`` is substituted inside any item, which is how a
    container mount gets the actual workspace path.
    """
    if not prefix:
        return command, True

    resolved_cwd = str(cwd if cwd is not None else Path.cwd())
    rendered = []
    inserted = False
    for item in prefix:
        if not isinstance(item, str) or not item:
            raise SandboxError("exec.sandbox.prefix must contain only non-empty strings")
        if item == "{command}":
            if inserted:
                raise SandboxError("exec.sandbox.prefix may contain {command} only once")
            rendered.append(command)
            inserted = True
            continue
        if "{command}" in item:
            raise SandboxError(
                "exec.sandbox.prefix must use {command} as a whole item, not inside another"
            )
        rendered.append(item.replace("{cwd}", resolved_cwd))
    if not inserted:
        rendered.append(command)
    return rendered, False


#: Prefix executables that are plain host shells or WSL2 launchers rather than
#: isolation boundaries. WSL2 mounts host drives under ``/mnt/c`` and shares
#: networking by default, so ADR-020 / O-1 explicitly classifies it as
#: non-confinement unless wrapped in a container runtime.
UNCONFINED_PREFIX_BASENAMES: frozenset[str] = frozenset(
    {
        "wsl",
        "wsl.exe",
        "wslconfig",
        "wslconfig.exe",
        "cmd",
        "cmd.exe",
        "powershell",
        "powershell.exe",
        "pwsh",
        "pwsh.exe",
        "sh",
        "bash",
        "zsh",
    }
)


def is_known_unconfined_prefix(prefix: Sequence[str]) -> Optional[str]:
    """Return a reason string when ``prefix`` starts with an unconfined shell/WSL launcher."""
    if not prefix:
        return "no sandbox prefix is configured"
    first = Path(str(prefix[0]).strip()).name.lower()
    if first in ("wsl", "wsl.exe", "wslconfig", "wslconfig.exe"):
        return (
            f"prefix executable '{prefix[0]}' is a plain WSL launcher; "
            "WSL2 mounts host drives and shares host networking by default, "
            "so it is not a confinement boundary"
        )
    if first in UNCONFINED_PREFIX_BASENAMES:
        return f"prefix executable '{prefix[0]}' is a plain host shell, not a confinement runtime"
    return None


@dataclass(frozen=True)
class SandboxProbeCheck:
    name: str
    passed: bool
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class SandboxDoctorReport:
    prefix: Tuple[str, ...]
    workspace: str
    confined: bool
    checks: Tuple[SandboxProbeCheck, ...] = ()
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return self.error
        failed = [c.name for c in self.checks if not c.passed]
        if not failed:
            return "all confinement checks passed"
        return "failed checks: " + ", ".join(failed)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "prefix": list(self.prefix),
            "workspace": self.workspace,
            "confined": self.confined,
            "checks": [c.to_dict() for c in self.checks],
            "error": self.error,
        }


_CONFINEMENT_CACHE: Dict[Tuple[Tuple[str, ...], str], SandboxDoctorReport] = {}
_CONFINEMENT_LOCK = threading.Lock()


def clear_confinement_cache() -> None:
    """Clear cached ``check_confinement`` probe results."""
    with _CONFINEMENT_LOCK:
        _CONFINEMENT_CACHE.clear()


def probe_confinement(
    config: SandboxConfig,
    workspace: Path | str,
    *,
    timeout: float = 15.0,
) -> SandboxDoctorReport:
    """Actively verify that ``config.prefix`` enforces filesystem, network, and UID isolation.

    Runs three behavioural probes inside the configured ``prefix``:
    1. ``write_outside`` — attempts to create a file outside the workspace mount.
       Passes only when the file does not appear on the host and the probe command
       reports failure.
    2. ``network_egress`` — opens an ephemeral loopback listener on the host and
       attempts to connect from inside the sandbox. Passes only when the sandbox
       cannot reach the listener.
    3. ``uid_isolation`` — verifies the sandbox does not run as unrestricted root
       (UID 0) or with visibility into a host-only sentinel outside the workspace.
    """
    from .process import run_captured

    ws_path = Path(workspace).expanduser().resolve()
    ws_str = str(ws_path)
    if not config.prefix:
        return SandboxDoctorReport(
            prefix=(),
            workspace=ws_str,
            confined=False,
            error="no exec.sandbox.prefix is configured",
        )

    unconfined_reason = is_known_unconfined_prefix(config.prefix)
    if unconfined_reason:
        return SandboxDoctorReport(
            prefix=config.prefix,
            workspace=ws_str,
            confined=False,
            checks=(SandboxProbeCheck("prefix_type", False, unconfined_reason),),
            error=unconfined_reason,
        )

    try:
        baseline = run_captured(
            "echo BRAHMA_SANDBOX_PROBE_OK",
            cwd=ws_path,
            timeout=timeout,
            sandbox=config,
        )
    except Exception as exc:
        return SandboxDoctorReport(
            prefix=config.prefix,
            workspace=ws_str,
            confined=False,
            error=f"failed to execute sandbox prefix: {exc}",
        )

    if baseline.timed_out or baseline.returncode != 0 or "BRAHMA_SANDBOX_PROBE_OK" not in baseline.stdout:
        detail = (baseline.stderr or baseline.stdout or f"exit {baseline.returncode}").strip()
        return SandboxDoctorReport(
            prefix=config.prefix,
            workspace=ws_str,
            confined=False,
            error=f"sandbox prefix failed baseline execution check: {detail}",
        )

    checks: List[SandboxProbeCheck] = []
    parent_dir = ws_path.parent if ws_path.parent != ws_path else Path(tempfile.gettempdir())
    outside_dir = Path(tempfile.mkdtemp(prefix="brahma-probe-", dir=str(parent_dir)))
    try:
        # 1. Write outside workspace mount
        probe_target = outside_dir / "escape_probe.txt"
        target_posix = str(probe_target).replace("\\", "/")
        write_cmd = (
            f'python -c "import pathlib; pathlib.Path({target_posix!r}).write_text(\'escaped\'); print(\'BRAHMA_OUTSIDE_WRITTEN\')" '
            f'|| sh -c "echo escaped > {target_posix!r} && echo BRAHMA_OUTSIDE_WRITTEN"'
        )
        write_res = run_captured(write_cmd, cwd=ws_path, timeout=timeout, sandbox=config)
        escaped = probe_target.exists() or ("BRAHMA_OUTSIDE_WRITTEN" in (write_res.stdout or ""))
        if escaped:
            checks.append(
                SandboxProbeCheck(
                    "write_outside",
                    False,
                    f"sandbox allowed writing outside workspace ({probe_target})",
                )
            )
        else:
            checks.append(
                SandboxProbeCheck(
                    "write_outside",
                    True,
                    "writes outside the workspace mount were blocked",
                )
            )

        # 2. Network egress probe
        accepted: List[bool] = []
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.listen(1)
        srv.settimeout(min(timeout, 3.0))

        def _accept_once() -> None:
            try:
                conn, _ = srv.accept()
                accepted.append(True)
                conn.close()
            except OSError:
                pass

        listener = threading.Thread(target=_accept_once, daemon=True)
        listener.start()
        try:
            net_cmd = (
                f'python -c "import socket; s=socket.create_connection((\'127.0.0.1\', {port}), 1.5); '
                f's.close(); print(\'BRAHMA_NET_CONNECTED\')"'
            )
            net_res = run_captured(net_cmd, cwd=ws_path, timeout=timeout, sandbox=config)
        finally:
            try:
                srv.close()
            except OSError:
                pass
            listener.join(timeout=1.0)

        net_escaped = bool(accepted) or ("BRAHMA_NET_CONNECTED" in (net_res.stdout or ""))
        if net_escaped:
            checks.append(
                SandboxProbeCheck(
                    "network_egress",
                    False,
                    f"sandbox reached host loopback listener on 127.0.0.1:{port}",
                )
            )
        else:
            checks.append(
                SandboxProbeCheck(
                    "network_egress",
                    True,
                    "network egress to host loopback was blocked",
                )
            )

        # 3. UID / host-namespace isolation probe
        sentinel = outside_dir / "host_identity.txt"
        sentinel.write_text("host-sentinel", encoding="utf-8")
        sentinel_posix = str(sentinel).replace("\\", "/")
        uid_cmd = (
            f'python -c "import os, pathlib, sys; '
            f'uid = getattr(os, \'geteuid\', lambda: -1)(); '
            f'host_visible = pathlib.Path({sentinel_posix!r}).exists(); '
            f'sys.exit(2 if uid == 0 else (3 if host_visible else 0))" '
            f'&& echo BRAHMA_UID_OK'
        )
        uid_res = run_captured(uid_cmd, cwd=ws_path, timeout=timeout, sandbox=config)
        uid_ok = (uid_res.returncode == 0) and ("BRAHMA_UID_OK" in (uid_res.stdout or ""))
        if uid_ok:
            checks.append(
                SandboxProbeCheck(
                    "uid_isolation",
                    True,
                    "non-root UID and isolated filesystem namespace verified",
                )
            )
        else:
            reason = (
                "sandbox runs as root (UID 0)"
                if uid_res.returncode == 2
                else "sandbox shares host filesystem namespace or privilege identity"
            )
            checks.append(SandboxProbeCheck("uid_isolation", False, reason))
    finally:
        shutil.rmtree(outside_dir, ignore_errors=True)

    confined = all(c.passed for c in checks)
    return SandboxDoctorReport(
        prefix=config.prefix,
        workspace=ws_str,
        confined=confined,
        checks=tuple(checks),
    )


def check_confinement(
    config: SandboxConfig,
    workspace: Path | str,
    *,
    timeout: float = 15.0,
) -> SandboxDoctorReport:
    """Cached wrapper around :func:`probe_confinement` keyed by ``(prefix, workspace)``."""
    ws_str = str(Path(workspace).expanduser().resolve())
    key = (config.prefix, ws_str)
    with _CONFINEMENT_LOCK:
        cached = _CONFINEMENT_CACHE.get(key)
    if cached is not None:
        return cached
    report = probe_confinement(config, ws_str, timeout=timeout)
    with _CONFINEMENT_LOCK:
        _CONFINEMENT_CACHE[key] = report
    return report

