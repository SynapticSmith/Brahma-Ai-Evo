"""MCP Server Adapter wrapping pure stdlib agent.mcp (ADR-018, Q2)."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

from agent.mcp import (
    MCPError,
    MCPServerConfig,
    MCPTimeout,
    StdioServer,
)
from core.agent_bridge.adapter import AgentAdapter
from core.agent_bridge.detectors import DetectionContext, detect_vendor_configs
from core.agent_bridge.errors import ConnectError
from core.agent_bridge.models import (
    AgentCallResult,
    AgentRecord,
    Capability,
    ProbeResult,
)


class MCPServerAdapter(AgentAdapter):
    """Adapter for Model Context Protocol (MCP) servers using stdio transport."""

    KIND = "mcp_server"
    TIER = 1

    def __init__(self, record: AgentRecord):
        super().__init__(record)
        self._server: Optional[StdioServer] = None
        self._capabilities: List[Capability] = []

    @classmethod
    def detect(cls, ctx: DetectionContext) -> Optional[AgentRecord]:
        """Discover MCP servers declared in vendor config files."""
        records = detect_vendor_configs(ctx)
        return records[0] if records else None

    def _build_server_config(self) -> MCPServerConfig:
        cmd = self.record.executable
        if not cmd and self.record.transport.argv:
            cmd = self.record.transport.argv[0]
        if not cmd:
            cmd = "mcp"

        args = list(self.record.transport.argv[1:]) if len(self.record.transport.argv) > 1 else []

        # Resolve env var names to current process values
        resolved_env: Dict[str, str] = {}
        for env_name in self.record.transport.env.keys():
            val = os.environ.get(env_name)
            if val is not None:
                resolved_env[env_name] = val

        return MCPServerConfig(
            name=self.record.id,
            command=cmd,
            args=args,
            env=resolved_env,
            cwd=self.record.transport.cwd,
            timeout=60.0,
        )

    def probe(self) -> ProbeResult:
        """3-stage probe: liveness, protocol, capabilities."""
        cmd = self.record.executable or (
            self.record.transport.argv[0] if self.record.transport.argv else None
        )
        if not cmd:
            return ProbeResult(ok=False, stage="liveness", detail="No command/executable configured")

        if not Path(cmd).is_file() and not shutil.which(cmd):
            return ProbeResult(ok=False, stage="liveness", detail=f"Command not found: {cmd}")

        # Test protocol & capabilities
        server = None
        try:
            config = self._build_server_config()
            server = StdioServer(config)
            server.start()
            tools = server.list_tools()
            tool_names = [t.get("name", "") for t in tools if isinstance(t, dict)]
            return ProbeResult(
                ok=True,
                stage="capability",
                detail=f"MCP server active: {len(tool_names)} tools found",
                models=tool_names,
            )
        except Exception as exc:
            return ProbeResult(ok=False, stage="protocol", detail=str(exc))
        finally:
            if server:
                try:
                    server.close()
                except Exception:
                    pass

    def connect(self) -> None:
        """Establish stdio connection and load advertised tools."""
        if self._server is not None:
            return

        try:
            config = self._build_server_config()
            self._server = StdioServer(config)
            self._server.start()
            raw_tools = self._server.list_tools()

            caps: List[Capability] = []
            for t in raw_tools:
                name = str(t.get("name") or "")
                desc = str(t.get("description") or "")
                params = t.get("inputSchema") or {"type": "object", "properties": {}}
                if name:
                    caps.append(Capability(name=name, description=desc, parameters=params))

            self._capabilities = caps
            self.record.status = "connected"
        except Exception as exc:
            self.record.status = "error"
            if self._server:
                try:
                    self._server.close()
                except Exception:
                    pass
                self._server = None
            raise ConnectError(f"Failed to connect to MCP server {self.record.id}: {exc}") from exc

    def verbs(self) -> List[Capability]:
        return list(self._capabilities)

    def call(self, verb: str, args: Dict[str, Any]) -> AgentCallResult:
        """Invoke a tool on the MCP server."""
        if self._server is None:
            try:
                self.connect()
            except Exception as exc:
                return AgentCallResult(ok=False, output="", error=f"Not connected: {exc}")

        try:
            assert self._server is not None
            text_output = self._server.call_tool(verb, args)
            return AgentCallResult(
                ok=True,
                output=text_output,
                bytes_total=len(text_output.encode("utf-8")),
            )
        except MCPTimeout:
            return AgentCallResult(
                ok=False,
                output="",
                error="Call timed out",
                timed_out=True,
            )
        except MCPError as exc:
            return AgentCallResult(ok=False, output="", error=str(exc))
        except Exception as exc:
            return AgentCallResult(ok=False, output="", error=f"Unexpected error: {exc}")

    def close(self) -> None:
        """Terminate the server process."""
        if self._server is not None:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        self.record.status = "found"
