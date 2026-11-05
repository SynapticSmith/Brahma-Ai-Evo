"""Configuration for the OpenAI-compatible port.

Config is trusted input -- the owner authored it -- but it is still validated:
a typo must not silently widen a limit or move the listener onto the LAN. The
loader never raises. A broken file leaves the safe defaults in force and records
why, because a server that refuses to start is harder to diagnose than one that
starts on loopback and reports the problem.

Defaults, in order of importance: disabled, loopback, no token, port 8010.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.providers import PROVIDERS

#: 8000 is the dashboard, 8765 is the gateway. Reusing either would make the
#: "already running in another instance" check ambiguous.
DEFAULT_PORT = 8010
LOOPBACK_HOSTS = ("127.0.0.1", "::1", "localhost")
PROFILES = ("chat", "reader", "worker", "operator")
STREAM_MODES = ("final", "all")
API_VERSION = "1.1.1"

#: A fixed timestamp for /v1/models. Clients only use it for display; a value
#: that changes every restart would look like a new model to a cache.
MODEL_CREATED = 1793664000


def _int(value: Any, default: int, *, low: int, high: int, problems: List[str], name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        problems.append(f"{name}: {value!r} is not an integer; using {default}")
        return default
    if parsed < low or parsed > high:
        problems.append(f"{name}: {parsed} is outside {low}..{high}; using {default}")
        return default
    return parsed


def _float(value: Any, default: float, *, low: float, high: float, problems: List[str], name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        problems.append(f"{name}: {value!r} is not a number; using {default}")
        return default
    if parsed < low or parsed > high:
        problems.append(f"{name}: {parsed} is outside {low}..{high}; using {default}")
        return default
    return parsed


@dataclass(slots=True)
class Limits:
    requests_per_minute: int = 30
    concurrent_runs: int = 2
    concurrent_runs_per_token: int = 1
    max_turns: int = 12
    max_messages: int = 200
    max_body_bytes: int = 1_000_000
    wall_clock_s: float = 1800.0
    approval_timeout_s: float = 60.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "requests_per_minute": self.requests_per_minute,
            "concurrent_runs": self.concurrent_runs,
            "concurrent_runs_per_token": self.concurrent_runs_per_token,
            "max_turns": self.max_turns,
            "max_messages": self.max_messages,
            "max_body_bytes": self.max_body_bytes,
            "wall_clock_s": self.wall_clock_s,
            "approval_timeout_s": self.approval_timeout_s,
        }

    @classmethod
    def from_dict(cls, data: Any, problems: List[str]) -> "Limits":
        data = data if isinstance(data, dict) else {}

        def integer(name: str, default: int, low: int, high: int) -> int:
            # An absent key is not a mistake; only a present, bad value is.
            if name not in data:
                return default
            return _int(data[name], default, low=low, high=high, problems=problems, name=f"limits.{name}")

        def number(name: str, default: float, low: float, high: float, *, zero_ok: bool = False) -> float:
            if name not in data:
                return default
            if zero_ok:
                # 0 is a meaningful sentinel for wall_clock_s ("no watchdog"),
                # so it must survive the range check that rejects 1..low-1.
                try:
                    if float(data[name]) == 0:
                        return 0.0
                except (TypeError, ValueError):
                    pass
            return _float(data[name], default, low=low, high=high, problems=problems, name=f"limits.{name}")

        return cls(
            requests_per_minute=integer("requests_per_minute", 30, 1, 10_000),
            concurrent_runs=integer("concurrent_runs", 2, 1, 64),
            concurrent_runs_per_token=integer("concurrent_runs_per_token", 1, 1, 64),
            max_turns=integer("max_turns", 12, 1, 100),
            max_messages=integer("max_messages", 200, 1, 10_000),
            max_body_bytes=integer("max_body_bytes", 1_000_000, 1_024, 100_000_000),
            # 30 min matches the local brain's slow profile; a cloud reasoning
            # run can legitimately outlive the old 5 min. 0 disables the
            # watchdog entirely (the socket timeouts still bound each call).
            wall_clock_s=number("wall_clock_s", 1800.0, 5.0, 86_400.0, zero_ok=True),
            approval_timeout_s=number("approval_timeout_s", 60.0, 5.0, 600.0),
        )


@dataclass
class BrahmaApiConfig:
    """Everything the server reads, and nothing it infers."""

    base_dir: Path = field(default_factory=lambda: Path.cwd())
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    allow_remote_bind: bool = False
    auth_required: bool = True
    preselect_profile: str = "reader"
    default_provider: str = "auto"
    workspaces: Dict[str, str] = field(default_factory=dict)
    limits: Limits = field(default_factory=Limits)
    stream_mode: str = "final"
    keepalive_s: float = 15.0
    audit: bool = True
    audit_log: Optional[str] = None
    problems: List[str] = field(default_factory=list, repr=False)

    # -- paths -------------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return Path(self.base_dir) / "config" / "brahma_api.json"

    @property
    def tokens_path(self) -> Path:
        return Path(self.base_dir) / "config" / "brahma_api" / "tokens.json"

    # -- construction ------------------------------------------------------

    @classmethod
    def load(cls, base_dir: Optional[Path] = None) -> "BrahmaApiConfig":
        """Read ``config/brahma_api.json``; never raise.

        Unknown keys are ignored on purpose: a config written for a later phase
        must not stop an earlier build from starting, but the problems list
        records anything that was rejected so the operator can see it.
        """
        base = Path(base_dir or Path.cwd()).resolve()
        problems: List[str] = []
        data: Dict[str, Any] = {}
        path = base / "config" / "brahma_api.json"
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
                if isinstance(raw, dict):
                    data = raw
                else:
                    problems.append("brahma_api.json must be a JSON object; using defaults")
            except (OSError, ValueError) as exc:
                problems.append(f"cannot read {path}: {exc}; using defaults")

        enabled = bool(data.get("enabled", False))
        host = str(data.get("host") or "127.0.0.1").strip()
        allow_remote = bool(data.get("allow_remote_bind", False))
        if host.lower() not in LOOPBACK_HOSTS and not allow_remote:
            problems.append(
                f"host {host!r} is not loopback and allow_remote_bind is false; "
                "binding 127.0.0.1 instead"
            )
            host = "127.0.0.1"

        port = (
            _int(data["port"], DEFAULT_PORT, low=1024, high=65535, problems=problems, name="port")
            if "port" in data
            else DEFAULT_PORT
        )

        preselect = str(data.get("preselect_profile") or "reader").strip().lower()
        if preselect not in PROFILES:
            problems.append(f"preselect_profile {preselect!r} is not one of {PROFILES}; using reader")
            preselect = "reader"

        provider = str(data.get("default_provider") or "auto").strip().lower()
        if provider not in PROVIDERS:
            problems.append(f"default_provider {provider!r} is not one of {PROVIDERS}; using auto")
            provider = "auto"

        stream_mode = str(data.get("stream_mode") or "final").strip().lower()
        if stream_mode not in STREAM_MODES:
            problems.append(f"stream_mode {stream_mode!r} is not one of {STREAM_MODES}; using final")
            stream_mode = "final"

        workspaces, ws_problems = _parse_workspaces(data.get("workspaces"), base)
        problems.extend(ws_problems)
        if not workspaces:
            # No alias configured means no token can be minted for a workspace.
            # The default alias is the repository itself, which is what a first
            # run means by "this project"; it grants nothing until a token names
            # it.
            workspaces = {"brahma": str(base)}

        limits = Limits.from_dict(data.get("limits"), problems)

        return cls(
            base_dir=base,
            enabled=enabled,
            host=host,
            port=port,
            allow_remote_bind=allow_remote,
            auth_required=bool(data.get("auth_required", True)),
            preselect_profile=preselect,
            default_provider=provider,
            workspaces=workspaces,
            limits=limits,
            stream_mode=stream_mode,
            keepalive_s=(
                _float(
                    data["keepalive_s"], 15.0, low=1.0, high=120.0, problems=problems,
                    name="keepalive_s",
                )
                if "keepalive_s" in data
                else 15.0
            ),
            audit=bool(data.get("audit", True)),
            audit_log=str(data["audit_log"]) if data.get("audit_log") else None,
            problems=problems,
        )

    # -- lookup ------------------------------------------------------------

    def workspace_path(self, alias: Optional[str]) -> Optional[Path]:
        """Resolve an alias to a directory. Never parses a path from a client."""
        if not alias:
            return None
        raw = self.workspaces.get(str(alias))
        if not raw:
            return None
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(self.base_dir) / path
        return path.resolve()

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "allow_remote_bind": self.allow_remote_bind,
            "auth_required": self.auth_required,
            "preselect_profile": self.preselect_profile,
            "default_provider": self.default_provider,
            "workspaces": dict(self.workspaces),
            "limits": self.limits.to_dict(),
            "stream_mode": self.stream_mode,
            "keepalive_s": self.keepalive_s,
            "audit": self.audit,
            "audit_log": self.audit_log,
        }

    def save(self) -> Path:
        path = self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.parent / f"{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
        try:
            tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
            for attempt in range(15):
                try:
                    os.replace(tmp, path)
                    break
                except (PermissionError, OSError):
                    if attempt == 14:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        return path


def _parse_workspaces(raw: Any, base: Path) -> Tuple[Dict[str, str], List[str]]:
    """Validate ``workspaces``: alias -> existing directory.

    An alias is a name, never a path from the wire. Keeping the map
    human-authored is the same containment rule as ``paths.trusted_roots``
    (ADR-020): the API can only reach a directory a person wrote down here.
    """
    problems: List[str] = []
    workspaces: Dict[str, str] = {}
    if raw is None:
        return workspaces, problems
    if not isinstance(raw, dict):
        return workspaces, ["workspaces must be an object of alias -> path"]
    for alias, value in raw.items():
        name = str(alias).strip()
        if not name or len(name) > 32 or not all(ch.isalnum() or ch in "-_" for ch in name):
            problems.append(f"workspaces: alias {alias!r} must be 1-32 chars of [A-Za-z0-9_-]")
            continue
        if not isinstance(value, str) or not value.strip():
            problems.append(f"workspaces.{name}: expected a path string")
            continue
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        try:
            path = path.resolve()
        except OSError as exc:
            problems.append(f"workspaces.{name}: {exc}")
            continue
        if not path.is_dir():
            problems.append(f"workspaces.{name}: {path} is not a directory")
            continue
        workspaces[name] = str(path)
    return workspaces, problems
