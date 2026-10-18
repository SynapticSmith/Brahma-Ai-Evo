"""
Bridges the tool index into Brahma's Live session.

Responsibilities
----------------
* Populate :mod:`core.tool_index` from every tool source: the native
  ``TOOL_DECLARATIONS`` list, filesystem skills from ``DynamicToolRegistry``,
  and everything the custom MCP bridge registers.
* Decide what the model actually sees, and assemble the matching
  ``[ACTIVE SKILL CONTEXT]`` block.
* Execute the three discovery meta-tools (``search_tools``, ``call_tool``,
  ``get_skill_manual``).

Why the discovery layer exists
------------------------------
Gemini Live fixes the tool list at connect time. ``BidiGenerateContentSetup`` is
documented as first-and-only-first message; a second ``setup`` frame mid-stream
closes the socket with ``1007 Request contains an invalid argument`` — the exact
error that broke the 130-tool handshake. So the advertised surface has to be
small and constant, and everything else has to be reachable *through* a tool
rather than *as* a tool. That is what these three do.

The advertised surface is therefore:

    pinned core tools  +  search_tools  +  call_tool  +  get_skill_manual
                     +  top-k retrieved tools

…whose size is bounded by ``tool_index_budget`` regardless of whether 50 or
50,000 tools are installed.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from core import tool_index as ti
from core.tool_index import (
    DEFAULT_ADVERTISED_BUDGET,
    DEFAULT_PINNED_TOOLS,
    GEMINI_MAX_TOOLS,
    META_DECLARATIONS,
    SkillManual,
    ToolEntry,
    get_index,
)

logger = logging.getLogger("ToolBridge")

#: Set by :func:`configure` at startup. Executes any catalogued tool by name and
#: returns its result as text. ``main.py`` installs the dispatcher that closes
#: over its own loop/UI, so this module never has to know about asyncio or Qt.
_executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None

#: Settings, refreshed from app_settings.json on load.
_settings: Dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def configure(settings: Optional[Dict[str, Any]] = None,
              executor: Optional[Callable[[str, Dict[str, Any]], Any]] = None) -> None:
    """Installs the tool dispatcher and applies settings from app_settings.json.

    ``executor(name, args)`` must run the named tool and return a result
    (string, or a dict carrying one). Called from the meta-tool handlers.
    """
    global _executor, _settings
    if executor is not None:
        _executor = executor
    if settings is not None:
        _settings = dict(settings)

    index = get_index()
    index.set_enabled(bool(_settings.get("tool_index_enabled", True)))
    index.set_disabled(_settings.get("disabled_tools") or [])
    logger.info(
        "[ToolBridge] configured (enabled=%s, budget=%s, disabled=%d)",
        _settings.get("tool_index_enabled", True),
        budget(),
        len(index.disabled()),
    )


def _truthy(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return default


def budget() -> int:
    """How many declarations may be advertised, clamped to the hard ceiling.

    The budget counts *everything* sent in the setup frame — pinned core,
    meta-tools and retrieved tools. ``resolve()`` subtracts the pinned and meta
    slots itself and clamps against the provider ceiling, so the full
    ``GEMINI_MAX_TOOLS`` is a safe value here: a budget of 128 advertises at
    most 128 declarations, never more.
    """
    raw = _settings.get("tool_index_budget", DEFAULT_ADVERTISED_BUDGET)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = DEFAULT_ADVERTISED_BUDGET
    return max(6, min(value, GEMINI_MAX_TOOLS))


def search_limit_cap() -> int:
    """Largest ``limit`` the ``search_tools`` meta-tool will honour.

    Tracks the session budget (budget − 2) so widening "Tools sent per
    session" in Settings widens discovery too: a budget of 62+ allows 60
    results, 128 allows 126. The floor of 6 keeps the default reachable even
    at the smallest budget.
    """
    return max(6, budget() - 2)


def enabled() -> bool:
    return get_index().enabled and _truthy(_settings.get("tool_index_enabled", True), True)


def _meta_declarations() -> List[Dict[str, Any]]:
    """META_DECLARATIONS with the search_tools ``limit`` text made truthful.

    The advertised maximum depends on the live session budget
    (:func:`search_limit_cap`), so the static description in
    ``tool_index.SEARCH_TOOLS_DECLARATION`` would lie the moment the user
    changes the setting. The copy is deep so the shared declaration is never
    mutated across sessions or budgets.
    """
    cap = search_limit_cap()
    out: List[Dict[str, Any]] = []
    for decl in META_DECLARATIONS:
        decl = copy.deepcopy(decl)
        if decl.get("name") == ti.META_SEARCH_TOOL:
            props = (decl.get("parameters") or {}).get("properties") or {}
            limit_spec = props.get("limit")
            if isinstance(limit_spec, dict):
                limit_spec["description"] = (
                    f"How many tools to return. Default 6, maximum {cap} "
                    "(this session's tool budget minus 2, set in Settings → "
                    "Tool & Skill Index)."
                )
        out.append(decl)
    return out


# --------------------------------------------------------------------------- #
# Catalog population
# --------------------------------------------------------------------------- #


def sync_native(native_declarations: Sequence[Dict[str, Any]]) -> int:
    """Indexes the built-in ``TOOL_DECLARATIONS`` list."""
    index = get_index()
    pinned = set(_settings.get("tool_index_pinned_tools") or DEFAULT_PINNED_TOOLS)
    count = 0
    for decl in native_declarations:
        name = decl.get("name")
        if not name:
            continue
        index.upsert(
            ToolEntry(
                name=name,
                description=decl.get("description", "") or "",
                parameters=decl.get("parameters") or {"type": "OBJECT", "properties": {}},
                declaration=decl,
                source="native",
                pinned=name in pinned,
            )
        )
        count += 1
    return count


def sync_registry(registry: Any) -> int:
    """Indexes every active tool the dynamic registry exposes.

    Covers filesystem skills *and* tools contributed by connected MCP servers,
    which arrive asynchronously after startup.
    """
    index = get_index()
    count = 0
    try:
        declarations = registry.get_tool_declarations()
    except Exception as exc:
        logger.warning("[ToolBridge] Could not enumerate registry tools: %s", exc)
        return 0

    for decl in declarations:
        name = decl.get("name")
        if not name:
            continue
        source = "skill"
        pinned = False
        try:
            skill = registry.get_skill(name)
            manifest = getattr(skill, "manifest", {}) or {}
            source = str(manifest.get("source") or source)
            pinned = bool(manifest.get("pinned", False))
        except Exception:
            pass
        index.upsert(
            ToolEntry(
                name=name,
                description=decl.get("description", "") or "",
                parameters=decl.get("parameters") or {"type": "OBJECT", "properties": {}},
                declaration=decl,
                source=source,
                pinned=pinned,
            )
        )
        count += 1
    return count


def sync_all(native_declarations: Sequence[Dict[str, Any]], registry: Any) -> Dict[str, int]:
    """One-shot catalog refresh. Safe to call repeatedly; cheap when unchanged."""
    natives = sync_native(native_declarations)
    skills = sync_registry(registry)
    index = get_index()
    index.rebuild()
    stats = index.stats()
    logger.info(
        "[ToolBridge] catalog synced: %d native + %d dynamic = %d tools (backends: %s)",
        natives, skills, stats.tools, ", ".join(stats.backends) or "none",
    )
    return {"native": natives, "dynamic": skills, "total": stats.tools}


def put_manual(manual: SkillManual) -> None:
    """Registers a usage guide for a tool group."""
    get_index().put_manual(manual)


# --------------------------------------------------------------------------- #
# Session payload
# --------------------------------------------------------------------------- #


def build_session_payload(intent: str = "") -> Dict[str, Any]:
    """Produces the advertised declarations plus the JIT skill context block.

    Returns a dict with ``declarations`` (provider-ready), ``skill_context``
    (appended to the system instruction), and counts for logging. Never raises:
    a failure here would be indistinguishable from the handshake bug we are
    fixing, so the worst case is "advertise everything", which is what happens
    if the index itself is broken.
    """
    index = get_index()

    if not enabled():
        # Index disabled: advertise the full legacy surface. This is the
        # pre-index behaviour and it is exactly what broke the handshake, so the
        # UI should steer users back to the index rather than here.
        return _legacy_payload()

    try:
        index.rebuild()
        resolved = index.resolve(intent, budget=budget())
    except Exception as exc:
        logger.error("[ToolBridge] Index resolution failed (%s); falling back to legacy payload.", exc)
        return _legacy_payload()

    declarations = list(resolved["declarations"]) + _meta_declarations()
    if len(declarations) > GEMINI_MAX_TOOLS:  # belt and braces
        declarations = declarations[:GEMINI_MAX_TOOLS]

    logger.info(
        "[ToolBridge] session payload: %d advertised, %d deferred (~%d tokens), intent=%r",
        len(declarations), resolved["deferred"], index.stats().total_tokens_estimate,
        (intent or "")[:60],
    )
    return {
        "declarations": declarations,
        "skill_context": resolved["skill_context"],
        "deferred": resolved["deferred"],
        "indexed": enabled(),
    }


def _legacy_payload() -> Dict[str, Any]:
    """The pre-index behaviour: every tool, no discovery layer.

    Only reachable when the user turns the index off. Kept because it is a
    useful escape hatch when diagnosing an indexing problem, and because it is
    the exact configuration that produced the reported crash.
    """
    try:
        from core.dynamic_registry import DynamicToolRegistry

        declarations = [dict(d) for d in TOOL_DECLARATIONS_CACHE[0]] if TOOL_DECLARATIONS_CACHE else []
        seen = {d.get("name") for d in declarations}
        for decl in DynamicToolRegistry.get_tool_declarations():
            if decl.get("name") not in seen:
                declarations.append(decl)
                seen.add(decl.get("name"))
    except Exception as exc:
        logger.warning("[ToolBridge] Legacy payload assembly failed: %s", exc)
        declarations = list(TOOL_DECLARATIONS_CACHE[0]) if TOOL_DECLARATIONS_CACHE else []

    if len(declarations) > GEMINI_MAX_TOOLS:
        logger.warning(
            "[ToolBridge] Tool index is OFF and %d tools exceed the Gemini limit of %d; "
            "the connection will be rejected. Re-enable the tool index in Settings.",
            len(declarations), GEMINI_MAX_TOOLS,
        )
    return {
        "declarations": declarations,
        "skill_context": "",
        "deferred": 0,
        "indexed": False,
    }


#: Populated by :func:`prime` so the legacy fallback has something to return
#: without importing main.py (which would be circular).
TOOL_DECLARATIONS_CACHE: List[Sequence[Dict[str, Any]]] = []


def prime(native_declarations: Sequence[Dict[str, Any]], registry: Any) -> None:
    """Records the native declarations and performs the first catalog sync."""
    TOOL_DECLARATIONS_CACHE.clear()
    TOOL_DECLARATIONS_CACHE.append(list(native_declarations))
    sync_all(native_declarations, registry)


# --------------------------------------------------------------------------- #
# Meta-tool execution
# --------------------------------------------------------------------------- #


def is_meta_tool(name: str) -> bool:
    return name in {d["name"] for d in META_DECLARATIONS}


async def execute_meta(name: str, args: Dict[str, Any]) -> str:
    """Runs one discovery meta-tool and returns model-facing text.

    Each handler fails soft: a retrieval miss or an unknown tool must come back
    as a readable message the model can act on, never as an exception, because
    the model's next move depends on getting an answer either way.
    """
    try:
        if name == ti.META_SEARCH_TOOL:
            return _handle_search(args)
        if name == ti.META_MANUAL_TOOL:
            return _handle_manual(args)
        if name == ti.META_CALL_TOOL:
            return await _handle_call(args)
    except Exception as exc:
        logger.error("[ToolBridge] Meta-tool %s failed: %s", name, exc, exc_info=True)
        return f"Error: {name} failed: {exc}"
    return f"Error: unknown meta-tool '{name}'."


def _handle_search(args: Dict[str, Any]) -> str:
    intent = str(args.get("intent") or args.get("query") or "").strip()
    if not intent:
        return "Error: search_tools requires an 'intent' describing what you want to do."

    try:
        limit = int(args.get("limit") or 6)
    except (TypeError, ValueError):
        limit = 6
    limit = max(1, min(limit, search_limit_cap()))

    index = get_index()
    hits = index.search(intent, k=limit)

    if not hits:
        return (
            f"No tool matches {intent!r}. "
            f"The catalogue has {index.stats().tools} tools. "
            "Try different wording, or handle this yourself."
        )

    lines = [f"Found {len(hits)} tool(s) for {intent!r}:"]
    for rank, hit in enumerate(hits, start=1):
        entry = hit.entry
        schema = entry.parameters or {}
        props = schema.get("properties") if isinstance(schema, dict) else None
        argspec = ", ".join(sorted(props.keys())) if isinstance(props, dict) and props else "none"
        required = schema.get("required") if isinstance(schema, dict) else None
        req = f" (required: {', '.join(required)})" if required else ""
        lines.append(
            f"\n{rank}. tool_name: {entry.name}"
            f"\n   group: {entry.skill_id}"
            f"\n   does: {entry.short_description(300)}"
            f"\n   arguments: {argspec}{req}"
        )
        if entry.skill_id and index.manual(entry.skill_id) is not None:
            lines.append(f"   manual: call get_skill_manual with skill_id='{entry.skill_id}'")

    lines.append(
        "\nTo run one, call: call_tool(tool_name=<exact name>, arguments={...})"
    )
    return "\n".join(lines)


def _handle_manual(args: Dict[str, Any]) -> str:
    skill_id = str(args.get("skill_id") or args.get("skill") or args.get("name") or "").strip()
    if not skill_id:
        return "Error: get_skill_manual requires a 'skill_id' (shown as 'group' by search_tools)."

    manual = get_index().manual(skill_id)
    if manual is None:
        index = get_index()
        known = sorted(set(index.skill_ids()) | {m.skill_id for m in index.manuals()})
        return (
            f"No manual registered for '{skill_id}'. "
            f"Registered groups: {', '.join(known) if known else '(none)'}"
        )

    rendered = manual.rendered()
    if not rendered:
        return f"The manual for '{skill_id}' is empty."
    return f"[SKILL MANUAL: {manual.title or manual.skill_id}]\n{rendered}"


async def _handle_call(args: Dict[str, Any]) -> str:
    tool_name = str(args.get("tool_name") or args.get("name") or "").strip()
    if not tool_name:
        return "Error: call_tool requires 'tool_name' (the exact name from search_tools)."

    call_args = args.get("arguments")
    if call_args is None:
        call_args = args.get("args")
    if not isinstance(call_args, dict):
        call_args = {}

    entry = get_index().get(tool_name)
    if entry is None:
        hits = get_index().search(tool_name, k=5)
        suggestions = [h.name for h in hits if h.name != tool_name]
        hint = f" Did you mean: {', '.join(suggestions[:3])}?" if suggestions else ""
        return f"Error: no tool named '{tool_name}'.{hint} Use search_tools to find the right name."

    if _executor is None:
        return (
            f"Error: '{tool_name}' is known but no executor is installed yet. "
            "This is a wiring fault in the tool bridge."
        )

    # Run off the event loop: MCP tools block on a subprocess round trip and
    # filesystem skills can take seconds.
    result = await __import__("asyncio").to_thread(_executor, tool_name, dict(call_args))
    return _stringify(result)


def _stringify(result: Any) -> str:
    if result is None:
        return "Done."
    if isinstance(result, dict):
        for key in ("result", "output", "summary", "text", "message"):
            value = result.get(key)
            if value:
                return str(value)
        import json

        try:
            return json.dumps(result, indent=2, default=str)
        except (TypeError, ValueError):
            return str(result)
    return str(result)


# --------------------------------------------------------------------------- #
# Diagnostics (Settings UI / logs)
# --------------------------------------------------------------------------- #


def snapshot() -> Dict[str, Any]:
    """Read-only status for the settings UI."""
    index = get_index()
    stats = index.stats()
    try:
        from core.mcp_manager import MCPServerManager

        mcp = MCPServerManager.status()
    except Exception:
        mcp = {}
    return {
        "enabled": enabled(),
        "budget": budget(),
        "tools": stats.tools,
        "advertised": stats.advertised,
        "deferred": max(0, stats.tools - stats.advertised),
        "backends": stats.backends,
        "embed_model": stats.embed_model,
        "manuals": stats.manuals,
        "last_build_ms": stats.last_build_ms,
        "last_query_ms": stats.last_query_ms,
        "token_estimate": stats.total_tokens_estimate,
        "gemini_limit": GEMINI_MAX_TOOLS,
        "disabled": len(index.disabled()),
        "mcp_servers": mcp,
    }


def explain(query: str, k: int = 8) -> List[Dict[str, Any]]:
    """Why a query ranked the way it did. Used by the settings 'test' box."""
    return get_index().explain(query, k=k)


def disabled_tools() -> List[str]:
    """Names currently switched off in the index."""
    return sorted(get_index().disabled())


def set_disabled_tools(names: Iterable[str]) -> None:
    """Replaces the switched-off set. Takes effect on the next search/advertise."""
    get_index().set_disabled(list(names or []))


def tool_catalog() -> Dict[str, Any]:
    """Grouped view of every catalogued tool, for the Settings UI.

    Default tools (native + feature skills) come first, then one section per MCP
    server. Disabled tools are still listed — with ``enabled=False`` — so they
    can be switched back on; the definitions are hidden from the model, not
    discarded. Servers that are configured but have not connected yet still get
    an (empty) section, so a freshly-added server is visible before its tools
    arrive rather than leaving the card blank.
    """
    index = get_index()
    disabled = index.disabled()
    groups: Dict[str, Dict[str, Any]] = {}

    for entry in index.all_entries():
        source = entry.source or "native"
        if source.startswith("mcp:"):
            key = source[4:] or "MCP"
            label, kind = key, "mcp"
        else:
            key, label, kind = "__default__", "Default tools", "default"
        group = groups.get(key)
        if group is None:
            group = {"id": key, "label": label, "kind": kind, "state": "", "tools": []}
            groups[key] = group
        group["tools"].append(
            {
                "name": entry.name,
                "source": source,
                "description": entry.short_description(140),
                "enabled": entry.name not in disabled,
            }
        )

    # Live connection state + any server that is configured but tool-less yet.
    try:
        from core.mcp_manager import MCPServerManager

        status = MCPServerManager.status()
    except Exception:
        status = {}

    server_names: List[str] = []
    for entry in _settings.get("custom_mcp_servers") or []:
        if isinstance(entry, dict) and str(entry.get("name") or "").strip():
            server_names.append(str(entry["name"]).strip())
    server_names.extend(str(name) for name in status)

    for name in server_names:
        if name not in groups:
            groups[name] = {"id": name, "label": name, "kind": "mcp", "state": "", "tools": []}

    default_keys = [k for k, g in groups.items() if g["kind"] == "default"]
    mcp_keys = sorted((k for k, g in groups.items() if g["kind"] == "mcp"), key=str.lower)
    for key in mcp_keys:
        groups[key]["state"] = (status.get(key) or {}).get("state", "not connected")
        groups[key]["tools"].sort(key=lambda t: t["name"].lower())

    return {
        "groups": [groups[k] for k in default_keys + mcp_keys],
        "disabled": sorted(disabled),
        "default_count": sum(len(groups[k]["tools"]) for k in default_keys),
    }