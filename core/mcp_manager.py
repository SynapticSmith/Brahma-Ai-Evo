"""
Custom MCP Server Bridge for Brahma AI Evo.

Brahma historically shipped only hardcoded, per-feature MCP clients (Spotify,
Playwright, system diagnostics). This module adds a single central loader that
reads an arbitrary array of custom MCP server definitions from
``config/app_settings.json``, boots each one, and publishes every tool they
expose into :class:`core.dynamic_registry.DynamicToolRegistry` — so the agent can
discover and invoke them exactly like native Python features.

Design notes
------------
* **One loop, one thread.** Every server shares a single dedicated background
  event loop. The MCP SDK's ``ClientSession`` is bound to the loop that created
  it, so a session may only be awaited from that loop.
* **Loop-safe dispatch.** The registry dispatches tool calls from arbitrary
  worker threads via ``execute_sync``. The registered executor is therefore a
  *synchronous* bridge that hops onto the manager's loop with
  ``asyncio.run_coroutine_threadsafe`` and blocks for the result. Naively
  calling ``asyncio.run`` on a session from another thread deadlocks or raises
  "attached to a different loop".
* **Durable registration.** Tools go through
  ``DynamicToolRegistry.register_external_tool``, an overlay that survives
  ``DynamicToolRegistry.initialize()`` (which clears ``_skills``).
* **Failure isolation.** A broken or unreachable server logs and is skipped;
  it never prevents Brahma from starting.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.dynamic_registry import DynamicToolRegistry
from core.user_paths import get_user_data_dir

logger = logging.getLogger("MCPManager")

# Must match the file the Settings UI and main.py read/write. It used to be
# hardcoded to the source checkout's config/, so a server added in Settings was
# written to the per-user config while the manager loaded the repo copy — the
# server never appeared and its tools never populated.
SETTINGS_PATH = get_user_data_dir() / "config" / "app_settings.json"

#: The tracked, credentials-redacted settings template shipped with the app.
#: Never read as a live source; only used once to seed a brand-new user config.
TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "config" / "app_settings.json"

# Cap a single tool call so a wedged MCP server cannot hang a dispatch thread
# forever; the registry's execution paths are worker threads.
DEFAULT_CALL_TIMEOUT_SECONDS = 120.0

# Ingestion bounds. A server advertising an unbounded number of prompts must
# not be able to stall the boot sequence.
MAX_INGESTED_PROMPTS = 12
MAX_INGESTED_RESOURCES = 5

# Longest single manual body retained per server.
MAX_MANUAL_CHARS = 24_000

_VALID_NAME_CHARS = re.compile(r"[^0-9A-Za-z_]+")

# The only schema type enums Gemini's FunctionDeclaration accepts.
_GEMINI_TYPES = frozenset(
    {"STRING", "NUMBER", "INTEGER", "BOOLEAN", "ARRAY", "OBJECT", "NULL"}
)


def _sanitize_name(*parts: str) -> str:
    """Build a provider-safe tool name from arbitrary MCP/server name parts.

    Collapses separator runs so adjacent illegal characters cannot produce
    doubled underscores in the advertised tool name.
    """
    joined = "_".join(str(p or "") for p in parts)
    cleaned = _VALID_NAME_CHARS.sub("_", joined)
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("_")
    if not cleaned:
        cleaned = "tool"
    if not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"mcp_{cleaned}"
    return cleaned


def _scalar_type(types: Any) -> str:
    """Picks a single Gemini type enum from a JSON-Schema type value."""
    if isinstance(types, str):
        candidate = types.upper()
    elif isinstance(types, list) and types:
        candidates = [str(t).upper() for t in types if str(t).upper() != "NULL"]
        candidate = candidates[0] if candidates else "STRING"
    else:
        candidate = "STRING"
    return candidate if candidate in _GEMINI_TYPES else "STRING"


def normalize_parameters(schema: Any) -> Dict[str, Any]:
    """Normalize an MCP ``inputSchema`` into a Gemini ``FunctionDeclaration`` schema.

    MCP servers emit JSON Schema: lower-cased structural types and keywords
    (``anyOf``, ``additionalProperties``, ``$ref``, ...) that Gemini's validator
    rejects outright with a 400. We therefore translate into Gemini's supported
    subset rather than passing arbitrary third-party JSON Schema through.

    Translation rules:
      * structural types upper-cased; ``["string","null"]`` collapses to the
        first non-null type (JSON Schema has no Gemini equivalent of a type list);
      * ``anyOf``/``oneOf`` unions collapse to their first concrete branch, which
        is what Gemini can express;
      * unknown keywords are dropped, and a usable schema is always returned.
    """
    if not isinstance(schema, dict):
        return {"type": "OBJECT", "properties": {}}

    root_type = _scalar_type(schema.get("type", "object"))

    if root_type == "ARRAY":
        items = schema.get("items")
        return {"type": "ARRAY", "items": normalize_parameters(items) if isinstance(items, dict) else {"type": "STRING"}}

    properties: Dict[str, Any] = {}
    raw_props = schema.get("properties")
    if isinstance(raw_props, dict):
        for prop_name, prop_schema in raw_props.items():
            properties[str(prop_name)] = _normalize_property(prop_schema)

    required = schema.get("required")
    normalized: Dict[str, Any] = {
        "type": "OBJECT",
        "properties": properties,
    }
    if isinstance(required, list):
        names = [str(n) for n in required if str(n) in properties]
        if names:
            normalized["required"] = names
    if isinstance(schema.get("description"), str) and schema["description"]:
        normalized["description"] = schema["description"]
    return normalized


def _normalize_property(prop_schema: Any) -> Dict[str, Any]:
    """Normalizes one property schema into Gemini's supported subset."""
    if not isinstance(prop_schema, dict):
        return {"type": "STRING"}

    # JSON Schema unions: collapse to the first branch Gemini can express.
    for union_key in ("anyOf", "oneOf", "allOf"):
        union = prop_schema.get(union_key)
        if isinstance(union, list) and union:
            for candidate in union:
                if isinstance(candidate, dict):
                    resolved = _normalize_property(candidate)
                    if resolved.get("type") != "NULL":
                        return resolved
            return {"type": "STRING"}

    if "$ref" in prop_schema:
        # A named reference cannot be expanded without the server's schema
        # catalogue; a permissive string is the only honest translation.
        return {"type": "STRING"}

    declared = _scalar_type(prop_schema.get("type", "string"))
    if declared == "NULL":
        declared = "STRING"

    out: Dict[str, Any] = {"type": declared}
    if isinstance(prop_schema.get("description"), str) and prop_schema["description"]:
        out["description"] = prop_schema["description"]

    if declared == "OBJECT":
        raw_props = prop_schema.get("properties")
        if isinstance(raw_props, dict):
            out["properties"] = {str(k): _normalize_property(v) for k, v in raw_props.items()}
        required = prop_schema.get("required")
        if isinstance(required, list):
            names = [str(n) for n in required if str(n) in out.get("properties", {})]
            if names:
                out["required"] = names
    elif declared == "ARRAY":
        items = prop_schema.get("items")
        out["items"] = _normalize_property(items) if isinstance(items, dict) else {"type": "STRING"}

    enum = prop_schema.get("enum")
    if isinstance(enum, list) and enum and declared in ("STRING", "NUMBER", "INTEGER", "BOOLEAN"):
        coerced: List[Any] = []
        for item in enum:
            if isinstance(item, (str, bool, int, float)):
                coerced.append(item)
        if coerced:
            out["enum"] = coerced

    return out


def _extract_result_text(result: Any) -> str:
    """Flatten an MCP ``CallToolResult`` into plain text for the model."""
    try:
        content = getattr(result, "content", None)
        if content is None and isinstance(result, dict):
            content = result.get("content")
    except Exception:
        content = None

    parts: List[str] = []
    if isinstance(content, list):
        for block in content:
            block_type = getattr(block, "type", None)
            if block_type is None and isinstance(block, dict):
                block_type = block.get("type")
            text = getattr(block, "text", None)
            if text is None and isinstance(block, dict):
                text = block.get("text")
            if text:
                parts.append(str(text))
            elif block_type:
                parts.append(f"[{block_type}]")
    elif content is not None:
        parts.append(str(content))

    if not parts:
        # Structured-content / dict results.
        structured = getattr(result, "structuredContent", None)
        if structured is None and isinstance(result, dict):
            structured = result.get("structuredContent")
        if structured is None:
            structured = result if isinstance(result, (dict, list)) else None
        if structured is not None:
            try:
                parts.append(json.dumps(structured, indent=2, default=str))
            except Exception:
                parts.append(str(structured))

    if not parts:
        return "Tool returned no content."
    return "\n".join(parts)


class MCPServerManager:
    """Loads and supervises user-configured MCP servers."""

    _lock = threading.RLock()
    _loop: Optional[asyncio.AbstractEventLoop] = None
    _thread: Optional[threading.Thread] = None
    _loop_ready = threading.Event()
    _started = False
    _stopping = False
    _tasks: Dict[str, "asyncio.Task"] = {}
    _sessions: Dict[str, Any] = {}
    _registered_tools: Dict[str, str] = {}  # tool_name -> server_name
    _status: Dict[str, Dict[str, Any]] = {}
    _call_timeout = DEFAULT_CALL_TIMEOUT_SECONDS

    # ------------------------------------------------------------------ config

    @classmethod
    def _read_settings(cls, path: Path) -> Dict[str, Any]:
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}

    @classmethod
    def _seed_from_template(cls, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Copies the shipped template's servers into a fresh user config once.

        The tracked ``config/app_settings.json`` is a credentials-redacted
        *template*, not a live source. A new install keeps its real servers in
        the per-user config, which starts without any. We seed the template's
        servers in one time (guarded by a marker so an intentionally-cleared
        list is never re-seeded), so the template is never read as live config
        and the servers still show up in Settings and the tool index.
        """
        existing = data.get("custom_mcp_servers") or []
        if data.get("custom_mcp_servers_migrated") or existing:
            return existing if isinstance(existing, list) else []

        seeded: List[Dict[str, Any]] = []
        try:
            if TEMPLATE_PATH.exists() and TEMPLATE_PATH.resolve() != SETTINGS_PATH.resolve():
                seeded = cls._read_settings(TEMPLATE_PATH).get("custom_mcp_servers") or []
        except Exception as exc:
            logger.debug("[MCP Manager] Could not read template %s: %s", TEMPLATE_PATH, exc)
        if not isinstance(seeded, list):
            return []

        # Record the migration even when the template is empty, so we only ever
        # attempt this once and never overwrite a deliberate empty config later.
        data["custom_mcp_servers"] = data.get("custom_mcp_servers") or list(seeded)
        data["custom_mcp_servers_migrated"] = True
        try:
            SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS_PATH.write_text(json.dumps(data, indent=4), encoding="utf-8")
            if seeded:
                logger.info(
                    "[MCP Manager] Seeded %d server(s) from the shipped template into %s.",
                    len(seeded), SETTINGS_PATH,
                )
        except Exception as exc:
            logger.warning(f"[MCP Manager] Could not write {SETTINGS_PATH}: {exc}")
        return list(seeded)

    @classmethod
    def load_server_configs(cls) -> List[Dict[str, Any]]:
        """Reads ``custom_mcp_servers`` from the per-user app settings, defensively.

        On a brand-new install the servers only exist in the shipped
        ``config/app_settings.json`` template; they are seeded once (see
        :meth:`_seed_from_template`) so the template itself is never read as a
        live source and the user config stays the single source of truth.
        """
        try:
            data = cls._read_settings(SETTINGS_PATH)
        except Exception as exc:
            logger.warning(f"[MCP Manager] Could not read {SETTINGS_PATH}: {exc}")
            return []

        if not isinstance(data, dict):
            return []
        servers = cls._seed_from_template(data)
        if not isinstance(servers, list):
            logger.warning("[MCP Manager] 'custom_mcp_servers' is not a list; ignoring.")
            return []

        valid: List[Dict[str, Any]] = []
        for entry in servers:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            command = str(entry.get("command") or "").strip()
            url = str(entry.get("url") or "").strip()
            if not name:
                continue
            # stdio servers need a command; remote servers need a url.
            if not command and not url:
                logger.warning(f"[MCP Manager] Server '{name}' has neither 'command' nor 'url'; skipped.")
                continue
            valid.append(entry)
        return valid

    # --------------------------------------------------------------- lifecycle

    @classmethod
    def start_servers(cls, block_timeout: float = 10.0) -> Dict[str, Dict[str, Any]]:
        """Starts every configured MCP server in the background.

        Idempotent: calling it twice does not spawn duplicate loops or servers.
        Returns the per-server status snapshot (possibly still "starting",
        because MCP servers connect asynchronously).
        """
        with cls._lock:
            if cls._started:
                return cls.status()
            cls._started = True
            cls._stopping = False

            configs = cls.load_server_configs()
            if not configs:
                logger.info("[MCP Manager] No custom MCP servers configured.")
                return cls.status()

            cls._ensure_loop()
            cls._loop_ready.wait(timeout=block_timeout)

            for config in configs:
                name = str(config.get("name")).strip()
                cls._status[name] = {
                    "name": name,
                    "state": "starting",
                    "tools": [],
                    "error": None,
                    "transport": _transport_for(config),
                }

            assert cls._loop is not None
            for config in configs:
                name = str(config.get("name")).strip()
                try:
                    task = asyncio.run_coroutine_threadsafe(
                        cls._supervise(dict(config)), cls._loop
                    )
                    cls._tasks[name] = task
                except Exception as exc:
                    cls._set_state(name, "error", error=str(exc))

            logger.info(f"[MCP Manager] Startup requested for {len(cls._tasks)} MCP server(s).")
            return cls.status()

    @classmethod
    def _ensure_loop(cls) -> None:
        if cls._loop is not None and not cls._loop.is_closed():
            return

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            cls._loop = loop
            cls._loop_ready.set()
            try:
                loop.run_forever()
            finally:
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                except Exception:
                    pass
                finally:
                    loop.close()

        cls._loop_ready.clear()
        cls._thread = threading.Thread(target=_runner, name="BrahmaMCPLoop", daemon=True)
        cls._thread.start()

    @classmethod
    def stop_all(cls, timeout: float = 5.0) -> None:
        """Cancels every server task and stops the shared loop. Safe to call twice."""
        with cls._lock:
            if not cls._started:
                return
            cls._stopping = True
            loop = cls._loop
            tasks = list(cls._tasks.items())
            cls._tasks.clear()

        for name, _task in tasks:
            try:
                if loop and not loop.is_closed():
                    loop.call_soon_threadsafe(_cancel_task, name)
            except Exception:
                pass

        if loop is not None and not loop.is_closed():
            deadline = time.time() + timeout
            while time.time() < deadline:
                if not any(not t.done() for t in [t for _, t in tasks]):
                    break
                time.sleep(0.05)
            try:
                loop.call_soon_threadsafe(loop.stop)
            except Exception:
                pass

        thread = cls._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

        cls.unregister_all_tools()
        with cls._lock:
            cls._loop = None
            cls._thread = None
            cls._loop_ready.clear()
            cls._started = False
            cls._stopping = False
            cls._sessions.clear()
            for entry in cls._status.values():
                entry["state"] = "stopped"
        logger.info("[MCP Manager] All custom MCP servers stopped.")

    # -------------------------------------------------------------- execution

    @classmethod
    async def _supervise(cls, config: Dict[str, Any]) -> None:
        """Keeps one server connected, registering its tools and restarting on exit."""
        name = str(config.get("name")).strip()
        delay = 1.0
        while not cls._stopping:
            try:
                cls._set_state(name, "connecting")
                await cls._connect_once(config)
                # _connect_once only returns on failure or shutdown.
                delay = 1.0
            except asyncio.CancelledError:
                cls._set_state(name, "stopped")
                raise
            except Exception as exc:
                cls._set_state(name, "error", error=str(exc))
                logger.error(f"[MCP Manager] Server '{name}' failed: {exc}")

            cls.unregister_server_tools(name)
            if cls._stopping:
                break

            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    @classmethod
    async def _connect_once(cls, config: Dict[str, Any]) -> None:
        name = str(config.get("name")).strip()
        transport = _transport_for(config)
        command = str(config.get("command") or "").strip()
        args = config.get("args") or []
        url = str(config.get("url") or "").strip()
        env = config.get("env") or None

        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as exc:
            raise RuntimeError(
                "The Python 'mcp' package is not installed. Run: pip install mcp"
            ) from exc

        if transport == "http":
            connect = cls._http_connectors(url)
            if connect is None:
                raise RuntimeError(
                    "Installed 'mcp' SDK has no HTTP/SSE client. Upgrade with: pip install -U mcp"
                )
        else:
            connect = _stdio_connect(stdio_client, StdioServerParameters, command, args, env)

        try:
            async with connect() as streams:
                read, write = streams[0], streams[1]
                async with ClientSession(read, write) as session:
                    init = await session.initialize()
                    cls._sessions[name] = session
                    cls._set_state(
                        name,
                        "connected",
                        detail=_server_identity(init, config),
                    )

                    await cls._ingest_instructions(name, init, session)
                    await cls._register_server_tools(name, session)
                    cls._set_state(name, "ready", tools=sorted(cls._server_tool_names(name)))

                    # Park until cancelled / shutdown: the session must stay open.
                    while not cls._stopping:
                        await asyncio.sleep(0.5)
        finally:
            cls._sessions.pop(name, None)

    @staticmethod
    def _http_connectors(url: str):
        """Returns a zero-arg factory yielding (read, write) for an HTTP MCP server."""
        try:
            from mcp.client.streamable_http import streamablehttp_client
        except Exception:
            try:
                from mcp.client.sse import sse_client
            except Exception:
                return None

            def _sse():
                return sse_client(url)

            return _sse

        def _http():
            return streamablehttp_client(url)

        return _http

    @classmethod
    async def _ingest_instructions(cls, name: str, init: Any, session: Any) -> None:
        """Collects the server's own usage documentation into the tool index.

        This is what makes adding an MCP server zero-configuration. A well-behaved
        MCP server can describe itself in three standard places, and we read all
        of them without any per-server configuration:

        1. ``initialize`` -> ``instructions`` — the canonical field, a short guide
           the server author wrote for exactly this purpose.
        2. ``prompts/list`` -> ``prompts/get`` — prompt templates that are often
           de facto manuals ("how to use this server").
        3. ``resources/list`` — a ``usage``-type resource, used by some servers.

        Whatever is found becomes a :class:`~core.tool_index.SkillManual` keyed on
        ``mcp:<server>``, injected into the system prompt *only* on turns where a
        tool from that server is actually advertised. A server that provides none
        of these still works — it simply gets no manual, and the tool
        descriptions stand on their own.

        Every failure is swallowed: a server that rejects ``prompts/list`` (or a
        server old enough not to implement it) must still connect.
        """
        skill_id = f"mcp:{name}"
        sections: List[Tuple[str, str]] = []
        title = name

        # -- 1. initialize.instructions ------------------------------------ #
        try:
            raw = getattr(init, "instructions", None) or getattr(init, "server_instructions", None)
            if isinstance(raw, str) and raw.strip():
                sections.append(("Usage", raw.strip()))
        except Exception:
            pass

        info = getattr(init, "serverInfo", None) or getattr(init, "server_info", None)
        if info is not None and getattr(info, "name", None):
            title = str(getattr(info, "name"))

        # -- 2. prompts/list + prompts/get --------------------------------- #
        try:
            listed = await asyncio.wait_for(session.list_prompts(), timeout=10.0)
            prompts = getattr(listed, "prompts", None) or []
            # Bound the work: a server advertising 50 prompts must not stall boot.
            for prompt in list(prompts)[:MAX_INGESTED_PROMPTS]:
                p_name = getattr(prompt, "name", None)
                if not p_name:
                    continue
                desc = (getattr(prompt, "description", "") or "").strip()
                try:
                    got = await asyncio.wait_for(session.get_prompt(p_name), timeout=10.0)
                    body = _flatten_prompt_messages(got)
                    if body.strip():
                        sections.append((str(p_name), body.strip()))
                except Exception as exc:
                    logger.debug("[MCP Manager] Could not fetch prompt '%s' from '%s': %s", p_name, name, exc)
                    if desc:
                        sections.append((str(p_name), desc))
        except Exception as exc:
            logger.debug("[MCP Manager] Server '%s' exposes no usable prompts: %s", name, exc)

        # -- 3. resources/list (usage guides) ------------------------------ #
        try:
            listed = await asyncio.wait_for(session.list_resources(), timeout=10.0)
            resources = getattr(listed, "resources", None) or []
            for resource in list(resources)[:MAX_INGESTED_RESOURCES]:
                uri = str(getattr(resource, "uri", "") or "")
                if not any(token in uri.lower() for token in ("usage", "guide", "manual", "readme", "help")):
                    continue
                desc = (getattr(resource, "description", "") or "").strip()
                if desc:
                    sections.append((uri, desc))
        except Exception as exc:
            logger.debug("[MCP Manager] Server '%s' exposes no usable resources: %s", name, exc)

        if not sections:
            logger.info("[MCP Manager] '%s' provided no instructions; tools stand on their descriptions.", name)
            return

        body = _truncate_manual_body(
            "\n\n".join(f"## {heading}\n{text}" for heading, text in sections)
        )
        try:
            from core.tool_index import SkillManual

            manual = SkillManual(
                skill_id=skill_id,
                title=f"{title} ({name})",
                body=body,
                origin=skill_id,
            )
            _publish_manual(manual)
            logger.info(
                "[MCP Manager] Ingested %d instruction section(s) for '%s' (%d chars).",
                len(sections), name, len(body),
            )
        except Exception as exc:
            logger.warning("[MCP Manager] Could not publish manual for '%s': %s", name, exc)

    @classmethod
    async def _register_server_tools(cls, name: str, session: Any) -> None:
        listed = await session.list_tools()
        tools = getattr(listed, "tools", None) or []
        for tool in tools:
            raw_name = getattr(tool, "name", None)
            if not raw_name:
                continue
            description = getattr(tool, "description", "") or f"MCP tool '{raw_name}' from '{name}'"
            schema = getattr(tool, "inputSchema", None)
            tool_name = _sanitize_name("mcp", name, raw_name)

            try:
                DynamicToolRegistry.register_external_tool(
                    name=tool_name,
                    description=f"[MCP:{name}] {description}",
                    parameters=normalize_parameters(schema),
                    executor=_make_executor(session, raw_name, name),
                    source=f"mcp:{name}",
                )
                cls._registered_tools[tool_name] = name
                _index_tool(
                    name=tool_name,
                    raw_description=description,
                    parameters=normalize_parameters(schema),
                    server=name,
                )
            except Exception as exc:
                logger.error(f"[MCP Manager] Failed to register '{tool_name}': {exc}")

    @classmethod
    def _server_tool_names(cls, name: str) -> List[str]:
        return sorted(t for t, srv in cls._registered_tools.items() if srv == name)

    @classmethod
    def _set_state(
        cls,
        name: str,
        state: str,
        error: Optional[str] = None,
        tools: Optional[List[str]] = None,
        detail: Optional[str] = None,
    ) -> None:
        entry = cls._status.setdefault(
            name, {"name": name, "state": state, "tools": [], "error": None, "transport": None}
        )
        entry["state"] = state
        entry["updated_at"] = time.time()
        if error is not None:
            entry["error"] = error
        if tools is not None:
            entry["tools"] = tools
        if detail is not None:
            entry["detail"] = detail

    # ---------------------------------------------------------------- removal

    @classmethod
    def unregister_server_tools(cls, server_name: str) -> int:
        """Removes every tool contributed by one server. Returns the count removed."""
        doomed = [t for t, srv in cls._registered_tools.items() if srv == server_name]
        for tool_name in doomed:
            cls._registered_tools.pop(tool_name, None)
            try:
                DynamicToolRegistry.unregister_external_tool(tool_name)
            except Exception:
                pass
        # Drop the index entries too, or a server that crashed and restarts
        # leaves duplicate/stale rows the model could still retrieve.
        _drop_from_index({f"mcp:{server_name}"})
        return len(doomed)

    @classmethod
    def unregister_all_tools(cls) -> int:
        removed = 0
        sources = set()
        for tool_name in list(cls._registered_tools):
            server = cls._registered_tools.pop(tool_name, None)
            if server:
                sources.add(f"mcp:{server}")
            try:
                DynamicToolRegistry.unregister_external_tool(tool_name)
                removed += 1
            except Exception:
                pass
        _drop_from_index(sources)
        return removed

    # ------------------------------------------------------------------ status

    @classmethod
    def status(cls) -> Dict[str, Dict[str, Any]]:
        """Snapshot of every configured server, safe to call from the UI thread."""
        with cls._lock:
            return {
                name: {
                    "name": entry.get("name", name),
                    "state": entry.get("state", "unknown"),
                    "tools": list(entry.get("tools", [])),
                    "error": entry.get("error"),
                    "transport": entry.get("transport"),
                    "detail": entry.get("detail"),
                }
                for name, entry in cls._status.items()
            }

    @classmethod
    def is_running(cls) -> bool:
        return bool(cls._started and cls._loop is not None and not cls._loop.is_closed())


def _transport_for(config: Dict[str, Any]) -> str:
    declared = str(config.get("transport") or config.get("type") or "").strip().lower()
    if declared in ("http", "https", "sse", "streamable_http", "streamable-http", "remote", "url"):
        return "http"
    if config.get("url") and not config.get("command"):
        return "http"
    return "stdio"


def _server_identity(init: Any, config: Dict[str, Any]) -> str:
    info = getattr(init, "serverInfo", None) or getattr(init, "server_info", None)
    name = getattr(info, "name", None) if info is not None else None
    version = getattr(info, "version", None) if info is not None else None
    label = f"{name} {version}".strip() if name else str(config.get("name"))
    return label.strip()


def _stdio_connect(stdio_client, params_cls, command: str, args: List[str], env: Optional[Dict[str, str]]):
    def _factory():
        params = params_cls(command=command, args=list(args), env=env)
        return stdio_client(params)

    return _factory


def _drop_from_index(sources: Iterable[str]) -> None:
    """Removes every indexed tool (and manual) belonging to these sources."""
    sources = {s for s in sources if s}
    if not sources:
        return
    try:
        from core import tool_index as ti

        index = ti.get_index()
        for source in sources:
            index.remove_source(source)
    except Exception as exc:
        logger.debug("[MCP Manager] Could not drop indexed tools for %s: %s", sorted(sources), exc)


def _index_tool(name: str, raw_description: str, parameters: Dict[str, Any], server: str) -> None:
    """Publishes one freshly-connected MCP tool into the tool index.

    Done at registration time (rather than waiting for the startup resync) so a
    server that connects mid-session is searchable immediately. The server's own
    description is used verbatim for the provider-facing declaration; only the
    *routing* metadata (domain, keywords) is inferred locally, which is what
    keeps look-alike tools from different servers distinguishable.
    """
    try:
        from core import tool_index as ti

        ti.get_index().upsert(
            ti.ToolEntry(
                name=name,
                description=raw_description,
                parameters=parameters,
                source=f"mcp:{server}",
                skill_id=f"mcp:{server}",
                domain=f"mcp {server}",
                keywords=f"{server} {name}",
                declaration={
                    "name": name,
                    "description": f"[MCP:{server}] {raw_description}",
                    "parameters": parameters,
                },
            )
        )
    except Exception as exc:
        logger.debug("[MCP Manager] Could not index tool '%s': %s", name, exc)


def _flatten_prompt_messages(got: Any) -> str:
    """Flattens a ``GetPromptResult`` into plain text.

    Prompt payloads arrive either as a flat ``messages`` list or as
    ``chat_messages``/``messages`` with structured content blocks; both shapes
    are reduced to text here so the manual is provider-neutral. The result may
    itself be a model object or a plain dict, so both are accepted.
    """
    def _field(obj: Any, key: str) -> Any:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    parts: List[str] = []
    for attr in ("messages", "chat_messages"):
        for message in (_field(got, attr) or []):
            content = _field(message, "content")
            text = _field(content, "text")
            if text is None and isinstance(content, str):
                text = content
            if text is None and content is not None:
                text = str(content)
            if text and str(text).strip():
                parts.append(str(text).strip())
    return "\n\n".join(parts)


def _truncate_manual_body(body: str, limit: Optional[int] = None) -> str:
    """Clips a manual at a word boundary, marking that it was clipped.

    The limit is read at call time (not bound as a default) so it stays
    configurable, and the marker is counted against the budget so the returned
    body never exceeds ``limit``.
    """
    cap = MAX_MANUAL_CHARS if limit is None else int(limit)
    marker = "\n… (truncated)"
    if len(body) <= cap:
        return body
    if cap <= len(marker):
        return body[:cap]
    return body[: cap - len(marker)].rsplit(" ", 1)[0] + marker


def _publish_manual(manual: Any) -> None:
    """Hands a skill manual to the tool index, tolerating an unbuilt index.

    ``core.tool_bridge`` is imported lazily so this module stays usable on its
    own (and in tests) without dragging in the whole app.
    """
    try:
        from core import tool_bridge

        tool_bridge.put_manual(manual)
    except Exception as exc:
        logger.debug("[MCP Manager] Tool index not ready to accept a manual: %s", exc)


def _cancel_task(name: str) -> None:
    task = MCPServerManager._tasks.get(name)
    if task is not None and not task.done():
        task.cancel()


def _make_executor(session: Any, tool_name: str, server_name: str):
    """Builds a synchronous, loop-safe executor for one MCP tool.

    The registry may call this from any worker thread and possibly inside an
    event loop, so we never touch ``session`` directly: we hand the coroutine to
    the manager's loop and block on the resulting concurrent Future.
    """

    def _execute(**kwargs: Any) -> str:
        loop = MCPServerManager._loop
        if loop is None or loop.is_closed():
            return f"Error: MCP server '{server_name}' is not running."

        async def _call() -> Any:
            return await session.call_tool(tool_name, arguments=dict(kwargs))

        try:
            future = asyncio.run_coroutine_threadsafe(_call(), loop)
            result = future.result(timeout=MCPServerManager._call_timeout)
        except asyncio.TimeoutError:
            return f"Error: MCP tool '{tool_name}' timed out after {MCPServerManager._call_timeout:.0f}s."
        except Exception as exc:
            logger.error(f"[MCP Manager] Call to '{server_name}/{tool_name}' failed: {exc}")
            return f"Error: MCP tool '{tool_name}' failed: {exc}"

        is_error = getattr(result, "isError", None)
        if is_error is None and isinstance(result, dict):
            is_error = result.get("isError")
        text = _extract_result_text(result)
        if is_error:
            return f"Error: {text}"
        return text

    return _execute