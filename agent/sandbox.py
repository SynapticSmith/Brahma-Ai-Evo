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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


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
        return bool(self.prefix) or self.env_scrub or self.has_limits

    def describe(self) -> Dict[str, Any]:
        return {
            "prefix": list(self.prefix),
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

    return SandboxConfig(
        prefix=_strings(raw.get("prefix"), "exec.sandbox.prefix"),
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
