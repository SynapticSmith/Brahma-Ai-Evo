"""Read-only, side-effect-free agent discovery detectors (ADR-018, Q2).

Invariants:
- PURE: Never spawns a subprocess, never executes commands, never raises exceptions.
- FAST: Socket probes are bounded to <= 0.25s on loopback.
- Complete: Discovers CLI agents on PATH, local model servers on standard ports,
  and vendor MCP configs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import socket
from typing import Callable, Dict, List, Optional

from core.agent_bridge.constants import KNOWN_CLI_AGENTS, KNOWN_LOCAL_SERVERS
from core.agent_bridge.models import AgentRecord, AgentTransport


class DetectionContext:
    """Dependency injection container for discovery probing."""

    def __init__(
        self,
        which_fn: Optional[Callable[[str], Optional[str]]] = None,
        port_check_fn: Optional[Callable[[str, int, float], bool]] = None,
        env: Optional[Dict[str, str]] = None,
        home_dir: Optional[Path] = None,
        search_roots: Optional[List[Path]] = None,
    ):
        self.which = which_fn or shutil.which
        self.check_port = port_check_fn or self._default_check_port
        self.env = env if env is not None else dict(os.environ)
        self.home_dir = home_dir if home_dir is not None else Path.home()
        self.search_roots = search_roots if search_roots is not None else self._default_search_roots()

    @staticmethod
    def _default_check_port(host: str, port: int, timeout: float = 0.25) -> bool:
        """Fast loopback port probe."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout)
                return s.connect_ex((host, int(port))) == 0
        except Exception:
            return False

    def _default_search_roots(self) -> List[Path]:
        roots: List[Path] = [self.home_dir]
        app_data = self.env.get("APPDATA")
        if app_data:
            roots.append(Path(app_data))
        local_app_data = self.env.get("LOCALAPPDATA")
        if local_app_data:
            roots.append(Path(local_app_data))
        return roots


def detect_cli_agents(ctx: DetectionContext) -> List[AgentRecord]:
    """Layer A / B: Detect CLI coding agents on PATH or standard directories."""
    records: List[AgentRecord] = []
    for item in KNOWN_CLI_AGENTS:
        matched_path: Optional[str] = None
        for binary in item["binaries"]:
            found = ctx.which(binary)
            if found:
                matched_path = found
                break

        if matched_path:
            argv = [matched_path] + list(item["default_argv"][1:])
            records.append(
                AgentRecord(
                    id=str(item["id"]),
                    display_name=str(item["display_name"]),
                    kind=str(item["kind"]),
                    tier=int(item["tier"]),
                    status="found",
                    executable=matched_path,
                    transport=AgentTransport(mode="stdio", argv=argv),
                    sources=["path"],
                )
            )
    return records


def detect_local_model_servers(ctx: DetectionContext) -> List[AgentRecord]:
    """Layer C: Detect running local model servers on standard loopback ports."""
    records: List[AgentRecord] = []
    for item in KNOWN_LOCAL_SERVERS:
        host = str(item.get("host") or "127.0.0.1")
        port = int(item.get("port") or 0)
        if port and ctx.check_port(host, port):
            records.append(
                AgentRecord(
                    id=str(item["id"]),
                    display_name=str(item["display_name"]),
                    kind=str(item["kind"]),
                    tier=int(item["tier"]),
                    status="found",
                    endpoint=str(item["endpoint"]),
                    transport=AgentTransport(mode=str(item["transport_mode"])),
                    sources=["local_port"],
                )
            )
    return records


def detect_vendor_configs(ctx: DetectionContext) -> List[AgentRecord]:
    """Layer D: Read-only scan of third-party vendor configurations for MCP servers."""
    records: List[AgentRecord] = []

    # Common vendor MCP config locations
    candidates: List[Path] = [
        ctx.home_dir / ".cursor" / "mcp.json",
        ctx.home_dir / ".codeium" / "windsurf" / "mcp_config.json",
    ]

    app_data = ctx.env.get("APPDATA")
    if app_data:
        candidates.append(Path(app_data) / "Claude" / "claude_desktop_config.json")

    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            mcp_servers = raw.get("mcpServers")
            if not isinstance(mcp_servers, dict):
                continue

            for name, cfg in mcp_servers.items():
                if not isinstance(cfg, dict):
                    continue
                cmd = cfg.get("command")
                if not cmd or not isinstance(cmd, str):
                    continue

                args = cfg.get("args") or []
                argv = [cmd] + [str(a) for a in args] if isinstance(args, list) else [cmd]
                env_names = {k: k for k in (cfg.get("env") or {}).keys()} if isinstance(cfg.get("env"), dict) else {}

                server_id = f"mcp_{name.lower().replace('-', '_')}"
                records.append(
                    AgentRecord(
                        id=server_id,
                        display_name=f"MCP: {name}",
                        kind="mcp_server",
                        tier=1,
                        status="found",
                        executable=cmd,
                        transport=AgentTransport(
                            mode="mcp_stdio",
                            argv=argv,
                            env=env_names,
                        ),
                        sources=["vendor_config"],
                    )
                )
        except Exception:
            continue

    return records


def detect_orchestrators(ctx: DetectionContext) -> List[AgentRecord]:
    """Layer E: Detect local orchestrators (Paperclip fleet, Orca ADE)."""
    records: List[AgentRecord] = []
    # 1. Paperclip
    paperclip_url = ctx.env.get("PAPERCLIP_API_URL")
    paperclip_dir = ctx.home_dir / ".paperclip"
    has_port = ctx.check_port("127.0.0.1", 3100)
    if has_port or paperclip_dir.exists() or paperclip_url:
        src = "loopback_probe" if has_port else ("user_home" if paperclip_dir.exists() else "env")
        records.append(
            AgentRecord(
                id="orchestrator_paperclip",
                display_name="Paperclip Fleet Orchestrator",
                kind="orchestrator",
                tier=2,
                endpoint=paperclip_url or "http://127.0.0.1:3100/api",
                status="found",
                sources=[src],
                orchestrator={
                    "connector": "paperclip",
                    "high_leverage_verbs": ["list_agents", "get_status", "create_issue", "run_heartbeat"],
                },
            )
        )

    # 2. Orca ADE
    orca_bin = ctx.env.get("ORCA_CLI_COMMAND") or ctx.which("orca") or ctx.which("orca.exe")
    if orca_bin:
        records.append(
            AgentRecord(
                id="orchestrator_orca",
                display_name="Orca ADE Orchestrator",
                kind="orchestrator",
                tier=2,
                executable=orca_bin,
                status="found",
                sources=["cli_path"],
                orchestrator={
                    "connector": "orca",
                    "high_leverage_verbs": ["list_worktrees", "assign_goal"],
                },
            )
        )

    return records


def detect_remote_agents(ctx: DetectionContext) -> List[AgentRecord]:
    """Layer F: Detect configured remote and decentralized agents (Tier 3)."""
    records: List[AgentRecord] = []

    # 1. Check environment variable
    raw_env = ctx.env.get("BRAHMA_REMOTE_AGENTS")
    if raw_env:
        try:
            parsed = json.loads(raw_env)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("endpoint"):
                        item.setdefault("kind", "remote_http")
                        item.setdefault("tier", 3)
                        rec = AgentRecord.from_dict(item)
                        rec.sources = sorted(list(set(rec.sources + ["env"])))
                        records.append(rec)
        except Exception:
            pass

    # 2. Check ~/.brahma/remote_agents.json
    cfg_file = ctx.home_dir / ".brahma" / "remote_agents.json"
    if cfg_file.is_file():
        try:
            parsed = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("endpoint"):
                        item.setdefault("kind", "remote_http")
                        item.setdefault("tier", 3)
                        rec = AgentRecord.from_dict(item)
                        rec.sources = sorted(list(set(rec.sources + ["remote_config"])))
                        records.append(rec)
        except Exception:
            pass

    return records


def run_all(ctx: Optional[DetectionContext] = None) -> List[AgentRecord]:
    """Run all discovery layers, union sources, and deduplicate records by id.

    Guaranteed to never raise an exception.
    """
    try:
        context = ctx or DetectionContext()
        discovered: Dict[str, AgentRecord] = {}

        detectors: List[Callable[[DetectionContext], List[AgentRecord]]] = [
            detect_cli_agents,
            detect_local_model_servers,
            detect_vendor_configs,
            detect_orchestrators,
            detect_remote_agents,
        ]

        for detector in detectors:
            try:
                for rec in detector(context):
                    if rec.id in discovered:
                        existing = discovered[rec.id]
                        existing.sources = sorted(list(set(existing.sources + rec.sources)))
                        if rec.executable and not existing.executable:
                            existing.executable = rec.executable
                        if rec.endpoint and not existing.endpoint:
                            existing.endpoint = rec.endpoint
                    else:
                        discovered[rec.id] = rec
            except Exception:
                continue

        return [discovered[k] for k in sorted(discovered.keys())]
    except Exception:
        return []
