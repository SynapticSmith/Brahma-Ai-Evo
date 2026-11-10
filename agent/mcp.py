"""Minimal MCP client for the harness: stdio, JSON-RPC 2.0, tools only.

The app's voice path already talks to MCP servers through
``core/mcp_manager.py`` and the official SDK. The harness cannot import that
module: ``agent/`` is kept importable on an interpreter that has only pytest
installed, and the SDK's asyncio client would tie the loop to an event loop the
agent does not own. This module therefore speaks the wire protocol directly.

Scope covers the key client capabilities needed by the agent:

* ``initialize`` / ``notifications/initialized``
* ``tools/list`` (with cursor pagination) and ``tools/call``
* ``resources/list`` and ``resources/read`` (with cursor pagination)
* ``prompts/list`` and ``prompts/get`` (with cursor pagination)
* ``notifications/tools/list_changed``, ``notifications/resources/list_changed``,
  and ``notifications/prompts/list_changed`` — latched by the reader thread and
  acted on by :meth:`MCPSession.refresh`, which the workspace manager calls
  once per turn. There is no background watcher.

Sampling, roots and logging are omitted; tools, resources and prompts are
governed identically under :class:`~agent.runtime.AgentRuntime`.

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
from typing import (
    Any,
    Deque,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from .process import _kill_tree, _windows_job
from .spec import ToolSpec, all_specs, register, unregister

#: The protocol revision this client implements. A server may answer with a
#: different one; the negotiation is advisory, and the three methods below are
#: stable across revisions, so a mismatch is recorded rather than fatal.
PROTOCOL_VERSION = "2024-11-05"

#: Guard against a server that never stops paginating. Fifty pages of tools is
#: far past any legitimate catalogue.
MAX_TOOL_PAGES = 50
MAX_RESOURCE_PAGES = 50
MAX_PROMPT_PAGES = 50

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


def _workspace_is_trusted(workspace: str | Path) -> bool:
    try:
        from core.user_paths import is_workspace_trusted

        return is_workspace_trusted(workspace)
    except Exception:
        return False


def _mark_workspace_trusted(workspace: str | Path) -> None:
    from core.user_paths import trust_workspace

    trust_workspace(workspace)


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
        self.capabilities: Dict[str, Any] = {}
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
            self.capabilities = dict(result.get("capabilities") or {})
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

    def list_resources(self) -> List[Dict[str, Any]]:
        resources: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(MAX_RESOURCE_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("resources/list", params)
            if not isinstance(result, dict):
                raise MCPError(
                    f"{self.config.name!r}: resources/list returned {type(result).__name__}"
                )
            page = result.get("resources") or []
            if not isinstance(page, list):
                raise MCPError(f"{self.config.name!r}: resources/list.resources must be a list")
            for item in page:
                if isinstance(item, dict) and isinstance(item.get("uri"), str) and item["uri"]:
                    resources.append(item)
            cursor = result.get("nextCursor")
            if not cursor or not isinstance(cursor, str):
                break
        return resources

    def read_resource(self, uri: str, *, timeout: Optional[float] = None) -> str:
        result = self.request(
            "resources/read",
            {"uri": uri},
            timeout=timeout,
        )
        return format_resource_result(result)

    def list_prompts(self) -> List[Dict[str, Any]]:
        prompts: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(MAX_PROMPT_PAGES):
            params = {"cursor": cursor} if cursor else {}
            result = self.request("prompts/list", params)
            if not isinstance(result, dict):
                raise MCPError(
                    f"{self.config.name!r}: prompts/list returned {type(result).__name__}"
                )
            page = result.get("prompts") or []
            if not isinstance(page, list):
                raise MCPError(f"{self.config.name!r}: prompts/list.prompts must be a list")
            for item in page:
                if isinstance(item, dict) and isinstance(item.get("name"), str) and item["name"]:
                    prompts.append(item)
            cursor = result.get("nextCursor")
            if not cursor or not isinstance(cursor, str):
                break
        return prompts

    def get_prompt(
        self, name: str, arguments: Optional[Mapping[str, Any]] = None, *, timeout: Optional[float] = None
    ) -> str:
        params: Dict[str, Any] = {"name": name}
        if arguments:
            params["arguments"] = dict(arguments)
        result = self.request("prompts/get", params, timeout=timeout)
        return format_prompt_result(result)

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
                    elif method in (
                        "notifications/tools/list_changed",
                        "notifications/resources/list_changed",
                        "notifications/prompts/list_changed",
                    ):
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


def format_resource_result(result: Any) -> str:
    """Render MCP ``resources/read`` content as model-readable text."""
    if not isinstance(result, Mapping):
        return json.dumps(result, ensure_ascii=False, default=str)
    contents = result.get("contents") or []
    if not isinstance(contents, list):
        return "(no contents)"
    parts: List[str] = []
    for item in contents:
        if isinstance(item, Mapping):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
            else:
                uri = item.get("uri") or "(unknown)"
                mime = item.get("mimeType") or "unknown type"
                blob = item.get("blob")
                size = len(str(blob)) if blob is not None else 0
                parts.append(f"[resource {uri} ({mime})" + (f", {size} base64 chars" if size else "") + "]")
        else:
            parts.append(str(item))
    return "\n\n".join(parts).strip() or "(empty resource)"


def format_prompt_result(result: Any) -> str:
    """Render MCP ``prompts/get`` content as model-readable text."""
    if not isinstance(result, Mapping):
        return json.dumps(result, ensure_ascii=False, default=str)
    parts: List[str] = []
    desc = result.get("description")
    if isinstance(desc, str) and desc.strip():
        parts.append(f"# {desc.strip()}\n")
    messages = result.get("messages") or []
    if isinstance(messages, list):
        for msg in messages:
            if isinstance(msg, Mapping):
                role = str(msg.get("role") or "user").upper()
                content = msg.get("content")
                text = ""
                if isinstance(content, Mapping):
                    text = str(content.get("text") or "")
                elif isinstance(content, list):
                    text = "\n".join(_format_content_item(c) for c in content)
                elif isinstance(content, str):
                    text = content
                if text.strip():
                    parts.append(f"[{role}]\n{text.strip()}")
            else:
                parts.append(str(msg))
    return "\n\n".join(parts).strip() or "(empty prompt)"


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
    prompts: int = 0
    resources: int = 0
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "ok": self.ok,
            "tools": self.tools,
            "prompts": self.prompts,
            "resources": self.resources,
            "error": self.error,
        }


def _app_manager_owns(name: str) -> bool:
    """True when the app's own MCP manager already publishes ``name``.

    Both clients name tools ``mcp_<server>_<tool>`` and publish them under the
    same ``mcp:<server>`` source, so a server configured in both places would
    otherwise register a phantom collision (``call_tool`` would refuse to pick
    a server) and closing this session would delete the app manager's live
    entry. The app manager is the older, always-configured client, so its entry
    wins; this session still advertises its own spec.
    """
    try:
        from core.dynamic_registry import DynamicToolRegistry

        return DynamicToolRegistry.is_external_tool(name)
    except Exception:
        return False


def _index_spec(spec: ToolSpec, server: str, raw_tool: str) -> bool:
    """Publishes one registered spec into :mod:`core.tool_index`.

    Done at registration time, matching the app manager: a server that
    connects mid-session becomes searchable and ``call_tool``-able
    immediately instead of waiting for the next ``sync_all``. Everything is
    lazy and fail-soft — ``agent`` must stay importable with only pytest
    installed, and a broken index must never prevent a server from starting.
    """
    if _app_manager_owns(spec.name):
        return False
    try:
        from core import tool_index as ti

        ti.get_index().upsert(
            ti.ToolEntry(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                source=f"mcp:{server}",
                skill_id=f"mcp:{server}",
                domain=f"mcp {server}",
                keywords=f"{server} {raw_tool}",
                declaration={
                    "name": spec.name,
                    "description": f"[MCP:{server}] {spec.description}",
                    "parameters": spec.parameters,
                },
            )
        )
        return True
    except Exception:
        return False


def _unindex_spec(name: str, server: str) -> bool:
    """Removes an entry this session published, never another owner's."""
    if _app_manager_owns(name):
        return False
    try:
        from core import tool_index as ti

        return ti.get_index().remove(name, source=f"mcp:{server}")
    except Exception:
        return False


class MCPSession:
    """A set of running servers plus the tool specs they contributed.

    ``close`` unregisters the specs. That matters in a long-lived process: a
    test or a restarted session must not leave a dead server's tools in the
    catalogue, where the next model call would advertise a tool that always
    fails.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._servers: Dict[str, StdioServer] = {}
        self._specs: List[str] = []
        self._calls: Dict[str, Tuple[str, str]] = {}
        self._by_server: Dict[str, List[str]] = {}
        #: Spec name -> server for entries this session published into the
        #: shared tool index. Tracked so close/refresh retract exactly what
        #: this session added, never the app manager's same-named entries.
        self._indexed: Dict[str, str] = {}
        self.statuses: List[MCPServerStatus] = []

    def start(
        self,
        configs: Sequence[MCPServerConfig],
        *,
        on_event: Optional[Any] = None,
    ) -> List[MCPServerStatus]:
        with self._lock:
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

                caps = server.capabilities or {}
                num_prompts = 0
                prompt_specs: List[str] = []
                if "prompts" in caps:
                    try:
                        p_list = server.list_prompts()
                        num_prompts = len(p_list)
                        prompt_specs = self._register_prompts(config, p_list, taken)
                    except MCPError:
                        pass

                num_resources = 0
                resource_specs: List[str] = []
                if "resources" in caps:
                    try:
                        r_list = server.list_resources()
                        num_resources = len(r_list)
                        resource_specs = self._register_resources(config, r_list, taken)
                    except MCPError:
                        pass

                self._servers[config.name] = server
                tool_specs = self._register_tools(config, tools, taken)
                names = tool_specs + prompt_specs + resource_specs
                self._by_server[config.name] = names
                status = MCPServerStatus(
                    name=config.name,
                    command=config.command,
                    ok=True,
                    tools=len(tool_specs),
                    prompts=num_prompts,
                    resources=num_resources,
                )
                self.statuses.append(status)
                self._emit(on_event, status)
            return list(self.statuses)

    def close(self) -> None:
        with self._lock:
            for name in list(self._specs):
                unregister(name)
                self._unindex(name)
            self._specs.clear()
            self._calls.clear()
            self._by_server.clear()
            self._indexed.clear()
            for server in list(self._servers.values()):
                server.close()
            self._servers.clear()

    def __enter__(self) -> "MCPSession":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    @property
    def tools(self) -> List[str]:
        with self._lock:
            return list(self._specs)

    def call(
        self,
        spec_name: str,
        arguments: Optional[Mapping[str, Any]],
        *,
        timeout: Optional[float] = None,
    ) -> str:
        """Invoke one discovered tool by its namespaced spec name.

        ``timeout`` overrides the server's configured request timeout for this
        call; a caller with a latency budget of its own (the per-turn provenance
        lookup) uses it instead of changing the server config.
        """
        with self._lock:
            target = self._calls.get(spec_name)
            if target is None:
                raise MCPError(f"no MCP tool named {spec_name!r}")
            server_name, tool_name = target
            server = self._servers.get(server_name)
            if server is None:
                raise MCPError(f"MCP server {server_name!r} is not running")
        return server.call_tool(tool_name, arguments, timeout=timeout)

    def list_resources(self, server: Optional[str] = None) -> List[Dict[str, Any]]:
        """List resources from a specific server or all running servers."""
        with self._lock:
            if server is not None:
                s = self._servers.get(server)
                if s is None:
                    raise MCPError(f"MCP server {server!r} is not running")
                return s.list_resources()
            servers = list(self._servers.items())
        all_res: List[Dict[str, Any]] = []
        for s_name, s in servers:
            if "resources" in s.capabilities:
                try:
                    for item in s.list_resources():
                        entry = dict(item)
                        entry["server"] = s_name
                        all_res.append(entry)
                except MCPError:
                    pass
        return all_res

    def read_resource(self, server: str, uri: str) -> str:
        """Read a resource by URI from the named server."""
        with self._lock:
            s = self._servers.get(server)
            if s is None:
                raise MCPError(f"MCP server {server!r} is not running")
        return s.read_resource(uri)

    def list_prompts(self, server: Optional[str] = None) -> List[Dict[str, Any]]:
        """List prompts from a specific server or all running servers."""
        with self._lock:
            if server is not None:
                s = self._servers.get(server)
                if s is None:
                    raise MCPError(f"MCP server {server!r} is not running")
                return s.list_prompts()
            servers = list(self._servers.items())
        all_prompts: List[Dict[str, Any]] = []
        for s_name, s in servers:
            if "prompts" in s.capabilities:
                try:
                    for item in s.list_prompts():
                        entry = dict(item)
                        entry["server"] = s_name
                        all_prompts.append(entry)
                except MCPError:
                    pass
        return all_prompts

    def get_prompt(self, server: str, name: str, arguments: Optional[Mapping[str, Any]] = None) -> str:
        """Get a prompt template from the named server."""
        with self._lock:
            s = self._servers.get(server)
            if s is None:
                raise MCPError(f"MCP server {server!r} is not running")
        return s.get_prompt(name, arguments)

    # -- refresh -----------------------------------------------------------

    def needs_refresh(self) -> bool:
        """True when a server died or announced that its tool list changed.

        The notification is only latched by the reader thread; deciding *when*
        to act on it belongs to the host, so this is the cheap predicate the
        workspace manager calls on every turn. ``closed`` matters as well as
        ``alive``: the reader thread sees EOF before ``poll()`` necessarily
        reports the exit, and a closed transport can never answer again.
        """
        with self._lock:
            return any(
                server.tools_changed or server.closed or not server.alive
                for server in self._servers.values()
            )

    def refresh(self, *, on_event: Optional[Any] = None) -> bool:
        """Re-list tools for the stale servers, restarting dead processes.

        Returns True when at least one process was restarted. A server whose
        re-list fails loses its tools (a stale catalogue would advertise calls
        that cannot work) and is reported once; it is not retried on every
        turn, which keeps a broken command from spawning a process per turn.
        """
        with self._lock:
            restarted_any = False
            for name in list(self._servers):
                server = self._servers.get(name)
                if server is None:
                    continue
                if not (server.tools_changed or server.closed or not server.alive):
                    continue

                old_tools = self._tool_names(name)
                self._unregister_server(name)
                # Clear before the request: a notification that arrives *during*
                # the re-list must schedule another refresh, not be swallowed.
                server.tools_changed = False
                restarted = server.closed or not server.alive
                if restarted:
                    server.close()
                    server = StdioServer(server.config)
                    restarted_any = True
                    try:
                        server.start()
                    except MCPError as exc:
                        self._report_failed_refresh(name, server.config, exc, on_event)
                        continue
                try:
                    tools = server.list_tools()
                except MCPError as exc:
                    server.close()
                    self._report_failed_refresh(name, server.config, exc, on_event)
                    continue

                self._servers[name] = server
                taken = {spec.name for spec in all_specs()}
                caps = server.capabilities or {}
                num_prompts = 0
                prompt_specs: List[str] = []
                if "prompts" in caps:
                    try:
                        p_list = server.list_prompts()
                        num_prompts = len(p_list)
                        prompt_specs = self._register_prompts(server.config, p_list, taken)
                    except MCPError:
                        pass

                num_resources = 0
                resource_specs: List[str] = []
                if "resources" in caps:
                    try:
                        r_list = server.list_resources()
                        num_resources = len(r_list)
                        resource_specs = self._register_resources(server.config, r_list, taken)
                    except MCPError:
                        pass

                tool_specs = self._register_tools(server.config, tools, taken)
                names = tool_specs + prompt_specs + resource_specs
                self._by_server[name] = names
                new_tools = {self._calls[spec][1] for spec in tool_specs}
                self._replace_status(
                    MCPServerStatus(
                        name=name,
                        command=server.config.command,
                        ok=True,
                        tools=len(tool_specs),
                        prompts=num_prompts,
                        resources=num_resources,
                    )
                )
                _emit_event(
                    on_event,
                    {
                        "type": "mcp_refresh",
                        "server": name,
                        "ok": True,
                        "tools": len(names),
                        "restarted": restarted,
                        "added": sorted(new_tools - old_tools),
                        "removed": sorted(old_tools - new_tools),
                    },
                )
            return restarted_any

    # -- internals ---------------------------------------------------------

    def _register_tools(
        self,
        config: MCPServerConfig,
        tools: Sequence[Mapping[str, Any]],
        taken: Set[str],
    ) -> List[str]:
        """Register one server's tools and return their spec names."""
        names: List[str] = []
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
            self._publish(spec, config.name, tool_name)
            self._calls[spec_name] = (config.name, tool_name)
            names.append(spec_name)
        return names

    def _register_resources(
        self,
        config: MCPServerConfig,
        resources: Sequence[Mapping[str, Any]],
        taken: Set[str],
    ) -> List[str]:
        """Register helper ToolSpecs to read/list resources on this server."""
        names: List[str] = []
        server_name = config.name

        spec_name = tool_spec_name(server_name, "read_resource", taken=taken)
        taken.add(spec_name)

        def read_handler(arguments: Dict[str, Any], context: Any) -> str:
            uri = str((arguments or {}).get("uri") or "").strip()
            if not uri:
                return "error: missing required argument 'uri'"
            server = self._servers.get(server_name)
            if server is None:
                return f"MCP server {server_name!r} is not running"
            try:
                return server.read_resource(uri)
            except MCPError as exc:
                return f"MCP resource read failed: {exc}"

        spec = ToolSpec(
            name=spec_name,
            description=f"Read a resource by URI from MCP server '{server_name}'.",
            parameters={
                "type": "object",
                "properties": {
                    "uri": {"type": "string", "description": "URI of the resource to read"},
                },
                "required": ["uri"],
            },
            tool_class="mcp",
            handler=read_handler,
            tags=("mcp", server_name, "resource"),
            max_output_chars=20_000,
        )
        register(spec)
        self._specs.append(spec_name)
        self._publish(spec, server_name, "read_resource")
        names.append(spec_name)

        list_name = tool_spec_name(server_name, "list_resources", taken=taken)
        taken.add(list_name)

        def list_handler(arguments: Dict[str, Any], context: Any) -> str:
            server = self._servers.get(server_name)
            if server is None:
                return f"MCP server {server_name!r} is not running"
            try:
                items = server.list_resources()
                return json.dumps(items, ensure_ascii=False, indent=2)
            except MCPError as exc:
                return f"MCP resource list failed: {exc}"

        list_spec = ToolSpec(
            name=list_name,
            description=f"List available resources on MCP server '{server_name}'.",
            parameters={"type": "object", "properties": {}},
            tool_class="mcp",
            handler=list_handler,
            tags=("mcp", server_name, "resource"),
            max_output_chars=20_000,
        )
        register(list_spec)
        self._specs.append(list_name)
        self._publish(list_spec, server_name, "list_resources")
        names.append(list_name)

        return names

    def _register_prompts(
        self,
        config: MCPServerConfig,
        prompts: Sequence[Mapping[str, Any]],
        taken: Set[str],
    ) -> List[str]:
        """Register helper ToolSpecs to get/list prompts on this server."""
        names: List[str] = []
        server_name = config.name

        spec_name = tool_spec_name(server_name, "get_prompt", taken=taken)
        taken.add(spec_name)

        def get_handler(arguments: Dict[str, Any], context: Any) -> str:
            name = str((arguments or {}).get("name") or "").strip()
            if not name:
                return "error: missing required argument 'name'"
            prompt_args = (arguments or {}).get("arguments")
            server = self._servers.get(server_name)
            if server is None:
                return f"MCP server {server_name!r} is not running"
            try:
                return server.get_prompt(name, prompt_args)
            except MCPError as exc:
                return f"MCP get_prompt failed: {exc}"

        spec = ToolSpec(
            name=spec_name,
            description=f"Get a prompt template by name from MCP server '{server_name}'.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the prompt template"},
                    "arguments": {"type": "object", "description": "Optional prompt arguments"},
                },
                "required": ["name"],
            },
            tool_class="mcp",
            handler=get_handler,
            tags=("mcp", server_name, "prompt"),
            max_output_chars=20_000,
        )
        register(spec)
        self._specs.append(spec_name)
        self._publish(spec, server_name, "get_prompt")
        names.append(spec_name)

        list_name = tool_spec_name(server_name, "list_prompts", taken=taken)
        taken.add(list_name)

        def list_handler(arguments: Dict[str, Any], context: Any) -> str:
            server = self._servers.get(server_name)
            if server is None:
                return f"MCP server {server_name!r} is not running"
            try:
                items = server.list_prompts()
                return json.dumps(items, ensure_ascii=False, indent=2)
            except MCPError as exc:
                return f"MCP prompt list failed: {exc}"

        list_spec = ToolSpec(
            name=list_name,
            description=f"List available prompts on MCP server '{server_name}'.",
            parameters={"type": "object", "properties": {}},
            tool_class="mcp",
            handler=list_handler,
            tags=("mcp", server_name, "prompt"),
            max_output_chars=20_000,
        )
        register(list_spec)
        self._specs.append(list_name)
        self._publish(list_spec, server_name, "list_prompts")
        names.append(list_name)

        return names

    def _publish(self, spec: ToolSpec, server: str, raw_tool: str) -> None:
        """Indexes one freshly registered spec, remembering what we own."""
        if _index_spec(spec, server, raw_tool):
            self._indexed[spec.name] = server

    def _unindex(self, spec_name: str) -> None:
        """Retracts one spec's index entry if this session published it."""
        server = self._indexed.pop(spec_name, None)
        if server is not None:
            _unindex_spec(spec_name, server)

    def _tool_names(self, server_name: str) -> Set[str]:
        """The un-namespaced tool names one server currently contributes."""
        return {
            tool
            for spec_name, (owner, tool) in self._calls.items()
            if owner == server_name
        }

    def _unregister_server(self, server_name: str) -> None:
        for spec_name in self._by_server.pop(server_name, []):
            unregister(spec_name)
            self._calls.pop(spec_name, None)
            self._unindex(spec_name)
            try:
                self._specs.remove(spec_name)
            except ValueError:
                pass

    def _replace_status(self, status: MCPServerStatus) -> None:
        for index, existing in enumerate(self.statuses):
            if existing.name == status.name:
                self.statuses[index] = status
                return
        self.statuses.append(status)

    def _report_failed_refresh(
        self,
        name: str,
        config: MCPServerConfig,
        exc: MCPError,
        on_event: Optional[Any],
    ) -> None:
        self._servers.pop(name, None)
        self._by_server.pop(name, None)
        self._replace_status(
            MCPServerStatus(
                name=name,
                command=config.command,
                ok=False,
                error=str(exc),
            )
        )
        _emit_event(
            on_event,
            {"type": "error", "error": f"MCP server {name!r} refresh failed: {exc}"},
        )

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


def mcp_openai_tools() -> List[Dict[str, Any]]:
    """The registered MCP tools in OpenAI tool shape.

    The GUI text path has to merge these into the *first* model call: the loop
    merges ``all_specs()`` for follow-ups, but a model that never learns a tool
    exists will never call it.
    """
    return [spec.openai() for spec in all_specs() if spec.tool_class == "mcp"]


def with_mcp_tools(tools: Optional[Sequence[Mapping[str, Any]]]) -> List[Dict[str, Any]]:
    """Append registered MCP tools, skipping names the surface already has.

    The host's tool bridge may already expose an MCP tool (the app's own manager
    predates this client). Duplicate function names are rejected by providers,
    so the merge is by name rather than a blind concatenation.
    """
    merged: List[Dict[str, Any]] = [dict(tool) for tool in (tools or [])]
    seen = set()
    for tool in merged:
        function = tool.get("function")
        name = function.get("name") if isinstance(function, Mapping) else tool.get("name")
        if isinstance(name, str) and name:
            seen.add(name)
    for tool in mcp_openai_tools():
        name = tool["function"]["name"]
        if name not in seen:
            merged.append(tool)
            seen.add(name)
    return merged


class WorkspaceMCP:
    """A lazily started MCP session bound to one workspace and config file.

    The CLI starts a session for the length of one command. A GUI process has
    no such boundary: turns arrive repeatedly, the workspace can change in
    Settings, and every turn must not pay the server's startup cost again.
    This manager starts on first use, reuses the session while the config file
    is unchanged, and tears it down and restarts when either the workspace or
    the config's identity changes.

    Between restarts it also refreshes *in place*: a server that announced
    ``tools/list_changed`` or whose process died is re-listed (and, if dead,
    restarted) on the next :meth:`ensure`, so a long-lived GUI process does not
    advertise a stale catalogue or a dead server's tools.

    It is thread-safe because the text turn runs on a worker thread while
    application shutdown closes the manager from the main thread. A malformed
    config returns ``False`` rather than raising: one bad file must not take
    down the reply ladder, and the fingerprint means the same broken file is
    reported once instead of on every turn.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._session: Optional[MCPSession] = None
        self._workspace: Optional[str] = None
        self._fingerprint: Optional[Tuple[str, int, int]] = None
        self._trusted: Optional[bool] = None
        self._untrusted_servers: List[str] = []
        self._ok = True
        self._error = ""
        self._generation = 0

    @property
    def generation(self) -> int:
        """How many times a config was loaded and servers started."""
        with self._lock:
            return self._generation

    @property
    def ok(self) -> bool:
        with self._lock:
            return self._ok

    @property
    def error(self) -> str:
        with self._lock:
            return self._error

    @property
    def trusted(self) -> bool:
        with self._lock:
            return bool(self._trusted)

    @property
    def untrusted_servers(self) -> List[str]:
        with self._lock:
            return list(self._untrusted_servers)

    @property
    def statuses(self) -> List[MCPServerStatus]:
        with self._lock:
            return list(self._session.statuses) if self._session is not None else []

    @property
    def tools(self) -> List[str]:
        with self._lock:
            return self._session.tools if self._session is not None else []

    def ensure(
        self,
        workspace: str | Path,
        *,
        config_path: Optional[str | Path] = None,
        on_event: Optional[Any] = None,
        trust_prompt: Optional[Any] = None,
    ) -> bool:
        """Start or reuse the session. Returns False only for an unusable config."""
        root = str(Path(workspace).expanduser().resolve())
        explicit = str(Path(config_path).expanduser()) if config_path else None
        repo_local = (
            explicit is None and not (os.getenv("BRAHMA_MCP_CONFIG") or "").strip()
        )
        with self._lock:
            path = Path(explicit).resolve() if explicit else discover_config(root)
            fingerprint: Optional[Tuple[str, int, int]] = None
            if path is not None:
                try:
                    stat = path.stat()
                    fingerprint = (str(path), stat.st_mtime_ns, stat.st_size)
                except OSError:
                    # A named-but-missing file is load_config's error to report,
                    # not a silent "no servers".
                    fingerprint = (str(path), -1, -1)

            trusted = True
            if path is not None and repo_local:
                trusted = _workspace_is_trusted(root)

            if (
                self._workspace == root
                and self._fingerprint == fingerprint
                and self._trusted == trusted
                and not (not trusted and trust_prompt is not None)
            ):
                if self._session is not None and self._session.needs_refresh():
                    self._refresh_locked(on_event)
                return self._ok

            self._close_locked()
            self._workspace = root
            self._fingerprint = fingerprint
            self._trusted = trusted
            self._untrusted_servers = []
            self._ok = True
            self._error = ""

            if path is None:
                return True

            try:
                configs = load_config(path)
            except MCPConfigError as exc:
                self._ok = False
                self._error = str(exc)
                _emit_event(on_event, {"type": "error", "error": str(exc)})
                return False

            if not configs:
                return True

            if repo_local and not trusted and callable(trust_prompt):
                try:
                    approved = bool(trust_prompt(Path(root), list(configs)))
                except Exception:
                    approved = False
                if approved:
                    _mark_workspace_trusted(root)
                    trusted = True
                    self._trusted = True

            if repo_local and not trusted:
                names = [cfg.name for cfg in configs]
                self._untrusted_servers = names
                count = len(names)
                noun = "server" if count == 1 else "servers"
                msg = (
                    f"This workspace wants to start {count} MCP {noun} "
                    f"({', '.join(names)}). Trust the workspace to load {path.name}."
                )
                _emit_event(
                    on_event,
                    {
                        "type": "mcp_untrusted",
                        "workspace": root,
                        "config": str(path),
                        "count": count,
                        "servers": names,
                        "message": msg,
                    },
                )
                return True

            self._session = MCPSession()
            self._session.start(configs, on_event=on_event)
            self._generation += 1
            return True

    def close(self) -> None:
        with self._lock:
            self._close_locked()
            self._workspace = None
            self._fingerprint = None
            self._trusted = None
            self._untrusted_servers = []
            self._ok = True
            self._error = ""

    def _close_locked(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            session.close()

    def _refresh_locked(self, on_event: Optional[Any]) -> None:
        """Refresh the live session without touching the config fingerprint.

        The fingerprint is the *config* trigger; a latched notification or a
        dead process is the *runtime* trigger. Refreshing must never break the
        turn, so an unexpected error becomes one event and the turn continues.
        """
        session = self._session
        if session is None:
            return
        try:
            if session.refresh(on_event=on_event):
                self._generation += 1
        except Exception as exc:  # noqa: BLE001 - a refresh must never break a turn
            _emit_event(
                on_event,
                {"type": "error", "error": f"MCP refresh failed: {exc}"},
            )


def _emit_event(sink: Optional[Any], event: Mapping[str, Any]) -> None:
    if sink is None:
        return
    try:
        sink(dict(event))
    except Exception:  # noqa: BLE001 - telemetry must never break a turn
        pass


__all__ = [
    "MAX_PROMPT_PAGES",
    "MAX_RESOURCE_PAGES",
    "MAX_TOOL_PAGES",
    "MCPConfigError",
    "MCPError",
    "MCPServerConfig",
    "MCPServerStatus",
    "MCPSession",
    "MCPTimeout",
    "PROTOCOL_VERSION",
    "StdioServer",
    "WorkspaceMCP",
    "discover_config",
    "format_prompt_result",
    "format_resource_result",
    "format_tool_result",
    "load_config",
    "mcp_openai_tools",
    "parse_servers",
    "registered_mcp_tools",
    "session_from_config",
    "start_session",
    "tool_spec_name",
    "with_mcp_tools",
]
