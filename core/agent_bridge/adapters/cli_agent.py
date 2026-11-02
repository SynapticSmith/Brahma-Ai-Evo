"""CLI Coding Agent Adapter using agent.process containment (ADR-018, Q2).

Supports external CLI coding agents (Claude Code, Codex, Aider, Gemini CLI)
using agent.process.run_captured for Windows Job Object process-tree isolation,
stdin closure, and sandbox containment.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

from agent.process import run_captured
from core.agent_bridge.adapter import AgentAdapter
from core.agent_bridge.detectors import DetectionContext, detect_cli_agents
from core.agent_bridge.errors import ConnectError
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    Capability,
    ProbeResult,
)


class CLIAgentAdapter(AgentAdapter):
    """Adapter for CLI-based autonomous coding agents."""

    KIND = "cli_agent"
    TIER = 1

    def __init__(self, record: AgentRecord):
        super().__init__(record)
        self._executable = record.executable or (
            record.transport.argv[0] if record.transport.argv else record.id
        )
        self._version: Optional[str] = record.version
        self._capabilities: List[Capability] = []

    @classmethod
    def detect(cls, ctx: DetectionContext) -> Optional[AgentRecord]:
        """Detect CLI agents installed on PATH."""
        records = detect_cli_agents(ctx)
        return records[0] if records else None

    def _get_resolved_path(self) -> Optional[str]:
        if Path(self._executable).is_file():
            return str(Path(self._executable).resolve())
        return shutil.which(self._executable)

    def _build_command(self, extra_args: List[str]) -> str:
        """Assemble full shell command string with proper quoting."""
        resolved = self._get_resolved_path() or self._executable
        base_argv = list(self.record.transport.argv) if self.record.transport.argv else [resolved]

        if base_argv and (base_argv[0] == self._executable or not Path(base_argv[0]).is_file()):
            base_argv[0] = resolved

        full_argv = base_argv + extra_args
        quoted = []
        for arg in full_argv:
            if " " in arg or '"' in arg:
                escaped = arg.replace('"', '\\"')
                quoted.append(f'"{escaped}"')
            else:
                quoted.append(arg)
        return " ".join(quoted)

    def probe(self) -> ProbeResult:
        """3-stage probe: liveness, protocol, capabilities."""
        resolved = self._get_resolved_path()
        if not resolved:
            return ProbeResult(
                ok=False,
                stage="liveness",
                detail=f"CLI agent executable not found: {self._executable}",
            )

        # Probe version with a short deadline
        try:
            cwd = Path(self.record.transport.cwd) if self.record.transport.cwd else Path.cwd()
            cmd_str = self._build_command(["--version"])
            res = run_captured(cmd_str, cwd=cwd, timeout=5.0)
            if res.returncode == 0:
                ver_text = (res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr) else "unknown"
                self._version = ver_text
                return ProbeResult(
                    ok=True,
                    stage="capability",
                    detail=f"CLI agent responsive: {ver_text}",
                    version=ver_text,
                    models=["cli"],
                )
            return ProbeResult(
                ok=False,
                stage="protocol",
                detail=f"CLI exited with code {res.returncode}: {res.stderr or res.stdout}",
            )
        except Exception as exc:
            return ProbeResult(ok=False, stage="protocol", detail=str(exc))

    def connect(self) -> None:
        """Probe the agent and register its run_task capability."""
        probe_res = self.probe()
        if not probe_res.ok:
            self.record.status = "error"
            raise ConnectError(f"Failed to connect to CLI agent {self.record.id}: {probe_res.detail}")

        self.record.version = self._version
        self._capabilities = [
            Capability(
                name="run_task",
                description=f"Execute an autonomous task using {self.record.display_name}.",
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The instructions or coding task to execute",
                        },
                        "cwd": {
                            "type": "string",
                            "description": "Optional working directory for task execution",
                        },
                    },
                    "required": ["prompt"],
                },
                mutating=True,  # Gated by permission policy (tool_class="exec")
            )
        ]
        self.record.capabilities = [
            {"name": c.name, "description": c.description, "parameters": c.parameters}
            for c in self._capabilities
        ]
        self.record.status = "connected"

    def verbs(self) -> List[Capability]:
        return list(self._capabilities)

    def call(self, verb: str, args: Dict[str, Any]) -> AgentCallResult:
        """Execute a task through the CLI agent."""
        if not self._capabilities:
            try:
                self.connect()
            except Exception as exc:
                return AgentCallResult(ok=False, output="", error=f"Not connected: {exc}")

        resolved = self._get_resolved_path()
        if not resolved:
            return AgentCallResult(ok=False, output="", error=f"Executable missing: {self._executable}")

        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return AgentCallResult(ok=False, output="", error="No prompt provided")

        # Resolve working directory safely
        raw_cwd = args.get("cwd") or self.record.transport.cwd
        cwd = Path(raw_cwd).resolve() if raw_cwd else Path.cwd()

        cmd_str = self._build_command([prompt])

        try:
            res = run_captured(cmd_str, cwd=cwd, timeout=120.0)
            ok = res.returncode == 0
            output_text = res.stdout if ok else (res.stderr or res.stdout)
            return AgentCallResult(
                ok=ok,
                output=output_text,
                error=res.stderr if not ok else None,
                bytes_total=len(output_text.encode("utf-8")),
            )
        except Exception as exc:
            return AgentCallResult(ok=False, output="", error=str(exc))

    def close(self) -> None:
        """Idempotent teardown."""
        self.record.status = "found"
