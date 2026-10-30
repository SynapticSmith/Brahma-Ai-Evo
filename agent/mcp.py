"""Minimal MCP client for the harness: stdio, JSON-RPC 2.0, tools only.

The app's voice path already talks to MCP servers through
``core/mcp_manager.py`` and the official SDK. The harness cannot import that
module: ``agent/`` is kept importable on an interpreter that has only pytest
installed, and the SDK's asyncio client would tie the loop to an event loop the
agent does not own. This module therefore speaks the wire protocol directly.

Scope is deliberately small — the part the agent actually needs:

* ``initialize`` / ``notifications/initialized``
* ``tools/list`` (with cursor pagination)
* ``tools/call``

Resources, prompts, sampling, roots and logging are not implemented. They are
separate capabilities, and pretending to support them would produce tools that
fail at the first call.

A server is an arbitrary program named in a config file the user wrote, so the
config is trusted input in the same sense as ``AGENTS.md``. Tools are exposed
through the normal :class:`~agent.spec.ToolSpec` registry, which means the one
:meth:`agent.runtime.AgentRuntime.execute` boundary (policy, approvals, output
cap) applies to them exactly as it does to the built-in six. The tool class is
``mcp``; a policy can allow, ask or deny that class, or override one tool by its
namespaced name.

Everything here is stdlib only and module-scope import safe: no Qt, no provider
SDK, no Windows COM.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .process import _kill_tree, _windows_job
from .spec import ToolSpec, all_specs, register, unregister

#: The protocol revision this client implements. A server may answer with a
#: different one; the negotiation is advisory, and the three methods below are
#: stable across revisions, so a mismatch is recorded rather than fatal.
PROTOCOL_VERSION = "2024-11-05"

#: Guard against a server that never stops paginating. Fifty pages of tools is
#: far past any legitimate catalogue.
MAX_TOOL_PAGES = 50

#: A model-facing description is prompt budget, not documentation. Some servers
#: ship multi-kilobyte tool descriptions; the first paragraph is enough to pick
#: a tool, and the full schema is still sent.
MAX_DESCRIPTION_CHARS = 2_000

#: Tool names have to satisfy the OpenAI function-name grammar (and this
#: project's stricter ``ToolSpec`` check), so they are namespaced and sanitised.
MAX_TOOL_NAME_CHARS = 64


class MCPError(RuntimeError):
    """A server failed, refused a request, or went away."""


class MCPTimeout(MCPError):
    """A request did not answer before its deadline."""


class MCPConfigError(ValueError):
    """The MCP config file is missing, malformed, or names no usable server."""


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MCPServerConfig:
    """One ``mcpServers`` entry, validated."""

    name: str
    command: str
    args: Tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    cwd: Optional[str] = None
    timeout: float = 60.0

    def argv(self) -> List[str]:
        return [self.command, *self.args]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "args": list(self.args),
            "env": dict(self.env),
            "cwd": self.cwd,
            "timeout": self.timeout,
        }


def _string_list(raw: Any, where: str) -> Tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
        raise MCPConfigError(f"{where} must be a list of strings")
    values: List[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise MCPConfigError(f"{where} must contain only strings")
        values.append(item)
    return tuple(values)


def _env_map(raw: Any, where: str) -> Dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise MCPConfigError(f"{where} must be an object of name -> value")
    env: Dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name:
            raise MCPConfigError(f"{where} has an empty variable name")
        if not isinstance(value, str):
            raise MCPConfigError(f"{where}.{name} must be a string")
        env[name] = value
    return env


def parse_servers(raw: Any, *, source: str = "mcp config") -> List[MCPServerConfig]:
    """Validate one config document.

    The canonical shape mirrors the ecosystem's ``mcpServers`` object. A
    ``mcp_servers`` alias is accepted because the app's own settings already
    use that spelling. Entries with ``"enabled": false`` are skipped rather
    than deleted, so a user can park a server without losing its command line.
    """
    if not isinstance(raw, Mapping):
        raise MCPConfigError(f"{source}: expected an object")
    servers = raw.get("mcpServers")
    if servers is None:
        servers = raw.get("mcp_servers")
    if servers is None:
        raise MCPConfigError(f"{source}: missing 'mcpServers'")
    if not isinstance(servers, Mapping):
        raise MCPConfigError(f"{source}: 'mcpServers' must be an object")

    configs: List[MCPServerConfig] = []
    for name, entry in servers.items():
        if not isinstance(name, str) or not name.strip():
            raise MCPConfigError(f"{source}: server names must be non-empty strings")
        where = f"{source}: mcpServers.{name}"
        if not isinstance(entry, Mapping):
            raise MCPConfigError(f"{where} must be an object")
        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise MCPConfigError(f"{where}.enabled must be true or false")
        if not enabled:
            continue
        command = entry.get("command")
        if not isinstance(command, str) or not command.strip():
            raise MCPConfigError(f"{where}.command must be a non-empty string")
        cwd = entry.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.strip()):
            raise MCPConfigError(f"{where}.cwd must be a non-empty string")
        timeout = entry.get("timeout", 60.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise MCPConfigError(f"{where}.timeout must be a number")
        if not (0 < float(timeout) <= 3_600):
            raise MCPConfigError(f"{where}.timeout must be between 0 and 3600 seconds")
        configs.append(
            MCPServerConfig(
                name=name,
                command=command,
                args=_string_list(entry.get("args"), f"{where}.args"),
                env=_env_map(entry.get("env"), f"{where}.env"),
                cwd=cwd,
                timeout=float(timeout),
            )
        )
    return configs


def load_config(path: str | Path) -> List[MCPServerConfig]:
    """Read and validate a config file.

    A missing or unreadable file is a configuration error, not "no servers":
    silently running without the tools the user asked for is the failure mode
    that makes an agent look broken. A relative ``cwd`` is resolved against the
    config file's directory, because that is the only location the author can
    reason about; the process's own cwd depends on where the agent was started.
    """
    target = Path(path).expanduser().resolve()
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise MCPConfigError(f"{target}: {exc}") from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise MCPConfigError(f"{target}: invalid JSON: {exc}") from exc
    configs = parse_servers(document, source=str(target))
    resolved: List[MCPServerConfig] = []
    for config in configs:
        if config.cwd and not Path(config.cwd).is_absolute():
            resolved.append(
                MCPServerConfig(
                    name=config.name,
                    command=config.command,
                    args=config.args,
                    env=config.env,
                    cwd=str((target.parent / config.cwd).resolve()),
                    timeout=config.timeout,
                )
            )
        else:
            resolved.append(config)
    return resolved


def discover_config(workspace: str | Path) -> Optional[Path]:
    """Find the conventional config file, if one exists.

    Order: ``BRAHMA_MCP_CONFIG`` (explicit, even when missing — the caller
    reports that error), then ``<workspace>/.brahma/mcp.json``, then the
    cross-tool ``<workspace>/.mcp.json``. Project-local first means a repository
    can ship its own servers to every agent that works in it.
    """
    env = os.getenv("BRAHMA_MCP_CONFIG")
    if env and env.strip():
        return Path(env).expanduser()
    root = Path(workspace)
    for candidate in (root / ".brahma" / "mcp.json", root / ".mcp.json"):
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- #
# Wire protocol
# --------------------------------------------------------------------------- #

class _Pending:
    __slots__ = ("done", "result", "error")

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[str] = None


class StdioServer:
    """One running MCP server, spoken to over newline-delimited JSON.

    A reader thread owns stdout and routes each response to the waiting
    caller by id. That is what lets several tool calls be in flight without a
    second process or an event loop; the agent's tool scheduler already
    serialises ``exec``-shaped calls, but the transport should not depend on
    that.
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.server_info: Dict[str, Any] = {}
        self.protocol_version = ""
        self.tools_changed = False
        self._process: Optional[subprocess.Popen] = None
        self._job: Any = None
        self._reader: Optional[threading.Thread] = None
        self._stderr_reader: Optional[threading.Thread] = None
        self._write_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._id_lock = threading.Lock()
        self._pending: Dict[str, _Pending] = {}
        self._next_id = 0
        self._closed = False
        self._state_lock = threading.Lock()
        self._stderr: Deque[str] = deque(maxlen=25)
        self._protocol_noise: Deque[str] = deque(maxlen=10)

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the server and complete the handshake."""
        env = dict(os.environ)
        env.update(self.config.env)
        cwd = self.config.cwd
        try:
            process = subprocess.Popen(
                self.config.argv(),
                cwd=cwd or None,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                start_new_session=(os.name != "nt"),
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
                ),
            )
        except OSError as exc:
            raise MCPError(f"could not start {self.config.command!r}: {exc}") from exc

        self._process = process
        self._job = _windows_job(process)
        self._reader = threading.Thread(
            target=self._read_loop, name=f"mcp-{self.config.name}-out", daemon=True
        )
        self._stderr_reader = threading.Thread(
            target=self._stderr_loop, name=f"mcp-{self.config.name}-err", daemon=True
        )
        self._reader.start()
        self._stderr_reader.start()

        try:
            result = self.request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "brahma-agent", "version": "1.0"},
                },
            )
        except MCPError:
            self.close()
            raise
        if isinstance(result, dict):
            self.server_info = dict(result.get("serverInfo") or {})
            self.protocol_version = str(result.get("protocolVersion") or "")
        self.notify("notifications/initialized")

    def close(self) -> None:
        """Stop the server and fail anything still waiting on it."""
        with self._state_lock:
            already = self._closed and self._process is None
            self._closed = True
        if already:
            return
        self._fail_pending("client closed")
        process = self._process
        self._process = None
        if process is not None:
            _kill_tree(process, self._job)
            try:
                process.wait(timeout=5)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        if self._job is not None:
            try:
                self._job.close()
            except Exception:  # noqa: BLE001 - defensive
                pass
            self._job = None
        for thread in (self._reader, self._stderr_reader):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=2)

    def __enter__(self) -> "StdioServer":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._closed

    @property
    def alive(self) -> bool:
        process = self._process
        return process is not None and process.poll() is None

    def stderr_tail(self, limit: int = 800) -> str:
        text = "\n".join(list(self._stderr)[-8:]).strip()
        if not text:
            return ""
        if len(text) > limit:
            text = text[-limit:]
        return f" (server stderr: {text})"

    # -- requests ----------------------------------------------------------

    def request(
        self, method: str, params: Optional[Mapping[str, Any]] = None, *, timeout: Optional[float] = None
    ) -> Any:
        if self._process is None or self.closed:
            raise MCPError(f"MCP server {self.config.name!r} is not running")
        deadline = self.config.timeout if timeout is None else float(timeout)
        with self._id_lock:
            self._next_id += 1
            request_id = self._next_id
        pending = _Pending()
        with self._pending_lock:
            self._pending[str(request_id)] = pending
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        try:
            self._write(payload)
        except MCPError:
            with self._pending_lock:
                self._pending.pop(str(request_id), None)
            raise
        if not pending.done.wait(deadline):
            with self._pending_lock:
                self._pending.pop(str(request_id), None)
            raise MCPTimeout(
                f"'{method}' timed out after {deadline:g}s on {self.config.name!r}"
                + self.stderr_tail()
            )
        if pending.error is not None:
            raise MCPError(
                f"'{method}' failed on {self.config.name!r}: {pending.error}"
                + self.stderr_tail()
            )
        return pending.result

    def notify(self, method: str, params: Optional[Mapping[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = dict(params)
        self._write(payload)

    def list_tools(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(MAX_TOOL_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("tools/list", params)
            if not isinstance(result, dict):
                raise MCPError(
                    f"{self.config.name!r}: tools/list returned {type(result).__name__}"
                )
            page = result.get("tools") or []
            if not isinstance(page, list):
                raise MCPError(f"{self.config.name!r}: tools/list.tools must be a list")
            for tool in page:
                if isinstance(tool, dict) and isinstance(tool.get("name"), str) and tool["name"]:
                    tools.append(tool)
            cursor = result.get("nextCursor")
            if not cursor or not isinstance(cursor, str):
                break
        return tools

    def call_tool(
        self, name: str, arguments: Optional[Mapping[str, Any]] = None, *, timeout: Optional[float] = None
    ) -> str:
        result = self.request(
            "tools/call",
            {"name": name, "arguments": dict(arguments or {})},
            timeout=timeout,
        )
        return format_tool_result(result)

    # -- internals ---------------------------------------------------------

    def _write(self, payload: Mapping[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None:
            raise MCPError(f"MCP server {self.config.name!r} is not running")
        line = json.dumps(dict(payload), ensure_ascii=False) + "\n"
        with self._write_lock:
            try:
                process.stdin.write(line)
                process.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise MCPError(
                    f"MCP server {self.config.name!r} stopped accepting input: {exc}"
                    + self.stderr_tail()
                ) from exc

    def _read_loop(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for raw in iter(process.stdout.readline, ""):
                text = raw.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    self._protocol_noise.append(text[:200])
                    continue
                if not isinstance(message, dict):
                    continue
                method = message.get("method")
                if isinstance(method, str):
                    if message.get("id") is not None:
                        self._answer_unsupported(message)
                    elif method == "notifications/tools/list_changed":
                        self.tools_changed = True
                    continue
                self._deliver(message)
        except (OSError, ValueError):
            pass
        finally:
            tail = self.stderr_tail()
            self._fail_pending(f"server exited{tail}")
            with self._state_lock:
                self._closed = True

    def _deliver(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        if request_id is None:
            return
        with self._pending_lock:
            pending = self._pending.pop(str(request_id), None)
        if pending is None:
            return
        error = message.get("error")
        if error is not None:
            if isinstance(error, Mapping):
                pending.error = f"{error.get('message') or error} (code {error.get('code')})"
            else:
                pending.error = str(error)
        else:
            pending.result = message.get("result")
        pending.done.set()

    def _answer_unsupported(self, message: Mapping[str, Any]) -> None:
        """Refuse server-initiated requests instead of leaving them hanging."""
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32601, "message": "method not supported by this client"},
                }
            )
        except MCPError:
            pass

    def _fail_pending(self, reason: str) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for item in pending:
            item.error = reason
            item.done.set()

    def _stderr_loop(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for raw in iter(process.stderr.readline, ""):
                text = raw.rstrip()
                if text:
                    self._stderr.append(text)
        except (OSError, ValueError):
            pass


# --------------------------------------------------------------------------- #
# Result rendering
# --------------------------------------------------------------------------- #

def _format_content_item(item: Any) -> str:
    if not isinstance(item, Mapping):
        return json.dumps(item, ensure_ascii=False, default=str)
    kind = item.get("type")
    if kind == "text":
        return str(item.get("text") or "")
    if kind == "resource":
        resource = item.get("resource")
        if isinstance(resource, Mapping):
            text = resource.get("text")
            if isinstance(text, str):
                return text
            uri = resource.get("uri") or "(unknown)"
            mime = resource.get("mimeType") or "unknown type"
            blob = resource.get("blob")
            size = len(str(blob)) if blob is not None else 0
            return f"[resource {uri} ({mime})" + (f", {size} base64 chars" if size else "") + "]"
        return "[resource]"
    if kind in ("image", "audio"):
        mime = item.get("mimeType") or "unknown type"
        data = item.get("data")
        size = len(str(data)) if data is not None else 0
        return f"[{kind} {mime}" + (f", {size} base64 chars" if size else "") + "]"
    return json.dumps(dict(item), ensure_ascii=False, default=str)


def format_tool_result(result: Any) -> str:
    """Render MCP ``tools/call`` content as model-readable text.

    Text parts are joined in order. Binary payloads are summarised rather than
    base64-dumped into the transcript: the model cannot use the bytes, and a
    screenshot would otherwise eat the whole context window. ``isError`` is
    prefixed so a failed call cannot be mistaken for a successful empty one.
    """
    if not isinstance(result, Mapping):
        return json.dumps(result, ensure_ascii=False, default=str)
    parts: List[str] = []
    content = result.get("content") or []
    if isinstance(content, list):
        for item in content:
            rendered = _format_content_item(item)
            if rendered:
                parts.append(rendered)
    structured = result.get("structuredContent")
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    body = "\n".join(parts).strip() or "(no content)"
    if result.get("isError"):
        return "error: " + body
    return body


# --------------------------------------------------------------------------- #
# Tool exposure
# --------------------------------------------------------------------------- #

def tool_spec_name(server: str, tool: str, *, taken: Iterable[str] = ()) -> str:
    """A registry-safe, namespaced name for one MCP tool.

    ``mcp_<server>_<tool>`` keeps provenance visible to the model and makes a
    ``tools`` policy override obvious. Everything outside ``[A-Za-z0-9_]``
    becomes ``_`` because both OpenAI and ``ToolSpec`` reject dots and dashes;
    a digest suffix keeps two long names from colliding after truncation.
    """
    raw = f"mcp_{server}_{tool}"
    base = re.sub(r"[^A-Za-z0-9_]+", "_", raw)
    base = re.sub(r"_+", "_", base).strip("_") or "mcp_tool"
    if not base[0].isalnum():
        base = "mcp_" + base
    if len(base) > MAX_TOOL_NAME_CHARS:
        digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:8]
        base = base[: MAX_TOOL_NAME_CHARS - 9] + "_" + digest
    used = set(taken)
    candidate = base
    suffix = 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = base[: MAX_TOOL_NAME_CHARS - len(tail)] + tail
        suffix += 1
    return candidate


def _clean_schema(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        return {"type": "object", "properties": {}}
    schema = dict(raw)
    if "type" not in schema:
        schema["type"] = "object"
    if not isinstance(schema.get("properties"), Mapping):
        schema["properties"] = {}
    return schema


def _clean_description(raw: Any, server: str, tool: str) -> str:
    text = raw if isinstance(raw, str) else ""
    text = text.strip() or f"Run the {tool!r} tool on the {server!r} MCP server."
    if len(text) > MAX_DESCRIPTION_CHARS:
        text = text[: MAX_DESCRIPTION_CHARS - 3].rstrip() + "..."
    return text


@dataclass(frozen=True)
class MCPServerStatus:
    """The outcome of one server startup, for diagnostics and event streams."""

    name: str
    command: str
    ok: bool
    tools: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "ok": self.ok,
            "tools": self.tools,
            "error": self.error,
        }


class MCPSession:
    """A set of running servers plus the tool specs they contributed.

    ``close`` unregisters the specs. That matters in a long-lived process: a
    test or a restarted session must not leave a dead server's tools in the
    catalogue, where the next model call would advertise a tool that always
    fails.
    """

    def __init__(self) -> None:
        self._servers: Dict[str, StdioServer] = {}
        self._specs: List[str] = []
        self._calls: Dict[str, Tuple[str, str]] = {}
        self.statuses: List[MCPServerStatus] = []

    def start(
        self,
        configs: Sequence[MCPServerConfig],
        *,
        on_event: Optional[Any] = None,
    ) -> List[MCPServerStatus]:
        taken = {spec.name for spec in all_specs()}
        for config in configs:
            server = StdioServer(config)
            try:
                server.start()
                tools = server.list_tools()
            except MCPError as exc:
                server.close()
                status = MCPServerStatus(
                    name=config.name,
                    command=config.command,
                    ok=False,
                    error=str(exc),
                )
                self.statuses.append(status)
                self._emit(on_event, status)
                continue

            self._servers[config.name] = server
            count = 0
            for tool in tools:
                tool_name = str(tool["name"])
                spec_name = tool_spec_name(config.name, tool_name, taken=taken)
                taken.add(spec_name)
                spec = ToolSpec(
                    name=spec_name,
                    description=_clean_description(tool.get("description"), config.name, tool_name),
                    parameters=_clean_schema(tool.get("inputSchema")),
                    tool_class="mcp",
                    handler=self._handler(spec_name),
                    tags=("mcp", config.name),
                    max_output_chars=20_000,
                )
                register(spec)
                self._specs.append(spec_name)
                self._calls[spec_name] = (config.name, tool_name)
                count += 1
            status = MCPServerStatus(
                name=config.name,
                command=config.command,
                ok=True,
                tools=count,
            )
            self.statuses.append(status)
            self._emit(on_event, status)
        return list(self.statuses)

    def close(self) -> None:
        for name in self._specs:
            unregister(name)
        self._specs.clear()
        self._calls.clear()
        for server in self._servers.values():
            server.close()
        self._servers.clear()

    def __enter__(self) -> "MCPSession":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    @property
    def tools(self) -> List[str]:
        return list(self._specs)

    def call(self, spec_name: str, arguments: Optional[Mapping[str, Any]]) -> str:
        """Invoke one discovered tool by its namespaced spec name."""
        target = self._calls.get(spec_name)
        if target is None:
            raise MCPError(f"no MCP tool named {spec_name!r}")
        server_name, tool_name = target
        server = self._servers.get(server_name)
        if server is None:
            raise MCPError(f"MCP server {server_name!r} is not running")
        return server.call_tool(tool_name, arguments)

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _emit(sink: Optional[Any], status: MCPServerStatus) -> None:
        if sink is None:
            return
        try:
            sink({"type": "mcp", **status.to_dict()})
        except Exception:  # noqa: BLE001 - telemetry must never break startup
            pass

    def _handler(self, spec_name: str):
        def handle(arguments: Dict[str, Any], context: Any) -> str:
            try:
                return self.call(spec_name, arguments)
            except MCPError as exc:
                return f"MCP tool '{spec_name}' failed: {exc}"

        return handle


def start_session(
    configs: Sequence[MCPServerConfig],
    *,
    on_event: Optional[Any] = None,
) -> MCPSession:
    """Convenience wrapper: a started session the caller must close."""
    session = MCPSession()
    session.start(configs, on_event=on_event)
    return session


def session_from_config(
    path: str | Path,
    *,
    on_event: Optional[Any] = None,
) -> MCPSession:
    """Load ``path`` and start every enabled server."""
    return start_session(load_config(path), on_event=on_event)


def registered_mcp_tools() -> List[str]:
    """Namespaced names currently contributed by MCP sessions."""
    return [spec.name for spec in all_specs() if spec.tool_class == "mcp"]


__all__ = [
    "MAX_TOOL_PAGES",
    "MCPConfigError",
    "MCPError",
    "MCPServerConfig",
    "MCPServerStatus",
    "MCPSession",
    "MCPTimeout",
    "PROTOCOL_VERSION",
    "StdioServer",
    "discover_config",
    "format_tool_result",
    "load_config",
    "parse_servers",
    "registered_mcp_tools",
    "session_from_config",
    "start_session",
    "tool_spec_name",
]
