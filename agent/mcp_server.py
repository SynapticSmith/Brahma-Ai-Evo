"""agent/mcp_server.py — Outbound MCP stdio server for the Brahma agent harness.

Exposes Brahma's canonical ToolSpecs, project skills (as prompts), and instructions
(as resources) outwards over standard Model Context Protocol (MCP) JSON-RPC 2.0 over stdio.

Enables external AI coding environments (Claude Desktop, Cursor, Windsurf, Zed,
Cline) to consume Brahma's desktop capabilities and project playbooks natively.

All tool executions strictly route through AgentRuntime.execute, ensuring that
permission policy, approval requirements, workspace containment, and audit logging
are enforced identically across external MCP calls.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, TextIO
from urllib.parse import unquote, urlparse

from .runtime import AgentRuntime, default_workspace
from .skills import discover
from .spec import all_specs
from . import code_tools as _code_tools  # noqa: F401 - ensures code tool specs are registered

logger = logging.getLogger("agent.mcp_server")


def _force_utf8(stream: TextIO) -> None:
    """Reconfigure a text stream to UTF-8/LF when the platform allows it."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except (ValueError, OSError):  # detached or already-closed stream
        pass

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "brahma-agent"
SERVER_VERSION = "1.1.1"

# Standard JSON-RPC 2.0 error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class MCPServer:
    """Outbound stdio MCP server exposing Brahma tools, prompts, and resources."""

    def __init__(
        self,
        runtime: Optional[AgentRuntime] = None,
        workspace: Optional[Path] = None,
        *,
        name: str = SERVER_NAME,
        version: str = SERVER_VERSION,
    ) -> None:
        self.workspace = (workspace or (runtime.workspace if runtime else None) or default_workspace()).resolve()
        self.runtime = runtime or AgentRuntime(workspace=self.workspace)
        self.name = name
        self.version = version
        self.initialized = False

    # -- JSON-RPC 2.0 request router -----------------------------------------

    def handle_request(self, message: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
        """Dispatch a single JSON-RPC message, returning a response dict or None."""
        if not isinstance(message, Mapping):
            return self._error_response(None, INVALID_REQUEST, "Message must be an object")

        if message.get("jsonrpc") != "2.0":
            return self._error_response(message.get("id"), INVALID_REQUEST, "Invalid JSON-RPC version (expected '2.0')")

        method = message.get("method")
        req_id = message.get("id")

        if not method or not isinstance(method, str):
            return self._error_response(req_id, INVALID_REQUEST, "Missing or invalid 'method'")

        # Notifications (no 'id' field)
        if req_id is None:
            self._handle_notification(method, message.get("params"))
            return None

        # Request handlers
        params = message.get("params") or {}
        if not isinstance(params, Mapping):
            return self._error_response(req_id, INVALID_PARAMS, "'params' must be an object")

        try:
            if method == "initialize":
                return self._handle_initialize(req_id, params)
            if method == "ping":
                return {"jsonrpc": "2.0", "id": req_id, "result": {}}
            if method == "tools/list":
                return self._handle_tools_list(req_id, params)
            if method == "tools/call":
                return self._handle_tools_call(req_id, params)
            if method == "prompts/list":
                return self._handle_prompts_list(req_id, params)
            if method == "prompts/get":
                return self._handle_prompts_get(req_id, params)
            if method == "resources/list":
                return self._handle_resources_list(req_id, params)
            if method == "resources/read":
                return self._handle_resources_read(req_id, params)

            return self._error_response(req_id, METHOD_NOT_FOUND, f"Method {method!r} not found")
        except Exception as exc:  # noqa: BLE001 - boundary containment
            logger.exception("Error handling MCP method %s: %s", method, exc)
            return self._error_response(req_id, INTERNAL_ERROR, f"Internal server error: {exc}")

    # -- Notification handlers ------------------------------------------------

    def _handle_notification(self, method: str, params: Any) -> None:
        if method == "notifications/initialized":
            self.initialized = True
            logger.info("MCP client initialized successfully")
        else:
            logger.debug("Received unhandled notification: %s", method)

    # -- Method implementations ----------------------------------------------

    def _handle_initialize(self, req_id: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
        self.initialized = True
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "prompts": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                },
                "serverInfo": {
                    "name": self.name,
                    "version": self.version,
                },
            },
        }

    def _handle_tools_list(self, req_id: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
        tools: List[Dict[str, Any]] = []
        for spec in all_specs():
            tools.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "inputSchema": spec.parameters or {"type": "object", "properties": {}},
                }
            )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": tools},
        }

    def _handle_tools_call(self, req_id: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
        name = str(params.get("name") or "").strip()
        if not name:
            return self._error_response(req_id, INVALID_PARAMS, "Missing tool 'name'")

        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            return self._error_response(req_id, INVALID_PARAMS, "'arguments' must be an object")

        # Execute through single AgentRuntime authority
        output = self.runtime.execute(name, dict(arguments))

        # Check for policy denial or failure markers
        lowered = output.lower()
        is_error = (
            "permission denied" in lowered
            or "denied by policy" in lowered
            or f"tool '{name}' failed" in lowered
            or f"tool '{name}' is not registered" in lowered
            or "rejected its arguments" in lowered
            or output.lower().startswith("error:")
            or output.lower().startswith("not run:")
        )

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "content": [{"type": "text", "text": output}],
                "isError": is_error,
            },
        }

    def _handle_prompts_list(self, req_id: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
        catalog = discover(self.workspace)
        prompts = [
            {
                "name": skill.name,
                "description": skill.description,
                "arguments": [],
            }
            for skill in catalog.skills
        ]
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"prompts": prompts},
        }

    def _handle_prompts_get(self, req_id: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
        name = str(params.get("name") or "").strip()
        if not name:
            return self._error_response(req_id, INVALID_PARAMS, "Missing prompt 'name'")

        catalog = discover(self.workspace)
        skill = catalog.get(name)
        if not skill:
            return self._error_response(req_id, INVALID_PARAMS, f"Prompt skill {name!r} not found")

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "description": skill.description,
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": skill.body},
                    }
                ],
            },
        }

    def _handle_resources_list(self, req_id: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
        resources: List[Dict[str, Any]] = []
        candidates = ("AGENTS.md", "BRAHMA.md", ".brahma/AGENTS.md", ".brahma/mcp.json")
        for fname in candidates:
            p = (self.workspace / fname).resolve()
            if p.is_file():
                resources.append(
                    {
                        "uri": p.as_uri(),
                        "name": p.name,
                        "description": f"Workspace instructions and configuration ({fname})",
                        "mimeType": "text/markdown" if fname.endswith(".md") else "application/json",
                    }
                )
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"resources": resources},
        }

    def _handle_resources_read(self, req_id: Any, params: Mapping[str, Any]) -> Dict[str, Any]:
        uri = str(params.get("uri") or "").strip()
        if not uri:
            return self._error_response(req_id, INVALID_PARAMS, "Missing resource 'uri'")

        parsed = urlparse(uri)
        if parsed.scheme != "file":
            return self._error_response(req_id, INVALID_PARAMS, f"Unsupported resource scheme {parsed.scheme!r}")

        path_str = unquote(parsed.path)
        # Windows file:///C:/path adjustment
        if sys.platform == "win32" and path_str.startswith("/") and len(path_str) > 2 and path_str[2] == ":":
            path_str = path_str[1:]
        file_path = Path(path_str).resolve()

        # Enforce workspace boundary check
        try:
            file_path.relative_to(self.workspace)
        except ValueError:
            return self._error_response(req_id, INVALID_PARAMS, f"Access denied: {file_path} is outside workspace")

        if not file_path.is_file():
            return self._error_response(req_id, INVALID_PARAMS, f"Resource not found: {uri}")

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return self._error_response(req_id, INTERNAL_ERROR, f"Failed to read {file_path}: {exc}")

        mime = "text/markdown" if file_path.name.endswith(".md") else "text/plain"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": mime,
                        "text": text,
                    }
                ]
            },
        }

    # -- helpers ---------------------------------------------------------------

    def _error_response(self, req_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def handle_line(self, line: str) -> Optional[str]:
        """Process one line of JSON-RPC text; return response text or None."""
        line = line.strip()
        if not line:
            return None
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            return json.dumps(self._error_response(None, PARSE_ERROR, f"Parse error: {exc}"))

        response = self.handle_request(payload)
        return json.dumps(response, ensure_ascii=False) if response is not None else None

    def serve(self, reader: Optional[TextIO] = None, writer: Optional[TextIO] = None) -> None:
        """Run the stdio loop until EOF."""
        in_stream = reader or sys.stdin
        out_stream = writer or sys.stdout

        # MCP framing is UTF-8 with LF line endings. A Windows console pipe
        # defaults to a legacy code page (cp1252) and CRLF, so a tool result
        # containing any other character would raise UnicodeEncodeError and
        # kill the server mid-session.
        _force_utf8(in_stream)
        _force_utf8(out_stream)

        for raw_line in in_stream:
            resp_str = self.handle_line(raw_line)
            if resp_str:
                out_stream.write(resp_str + "\n")
                out_stream.flush()
