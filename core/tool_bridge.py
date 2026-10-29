"""
Bridges the tool index into Brahma's Live session.

Responsibilities
----------------
* Populate :mod:`core.tool_index` from every tool source: the native
  ``TOOL_DECLARATIONS`` list, filesystem skills from ``DynamicToolRegistry``,
  and everything the custom MCP bridge registers.
* Decide what the model actually sees, and assemble the matching
  ``[ACTIVE SKILL CONTEXT]`` block.
* Execute the discovery meta-tools (``search_tools``, ``list_tools``,
  ``call_tool``, ``get_skill_manual``, and — for :mod:`core.skills_library` —
  ``search_skills``, ``list_skills`` and ``load_skill``).

Why the discovery layer exists
------------------------------
Gemini Live fixes the tool list at connect time. ``BidiGenerateContentSetup`` is
documented as first-and-only-first message; a second ``setup`` frame mid-stream
closes the socket with ``1007 Request contains an invalid argument`` — the exact
error that broke the 130-tool handshake. So the advertised surface has to be
small and constant, and everything else has to be reachable *through* a tool
rather than *as* a tool. That is what these meta-tools do.

The advertised surface is therefore:

    pinned core tools  +  search_tools  +  list_tools  +  call_tool
                     +  get_skill_manual +  search_skills  +  list_skills
                     +  load_skill +  top-k retrieved tools

…whose size is bounded by ``tool_index_budget`` regardless of whether 50 or
50,000 tools are installed.

Each catalogue is reachable two ways: *search-shaped* (``search_tools`` /
``search_skills`` rank an intent) and *list-shaped* (``list_tools`` /
``list_skills`` enumerate in pages, for "what do you have?" questions where
there is no intent to rank against). The list pair is optional for the model —
it never has to call it — but it costs a fixed slot in the budget, so
``search_limit_cap`` and ``resolve`` both subtract ``len(META_TOOLS)``.

The Skills Library
------------------
``search_skills`` / ``list_skills`` / ``load_skill`` reach
:mod:`core.skills_library`, a *peer* of the tool catalogue rather than a part of
it. The library holds task playbooks — mostly instructional, so most of them
never become tools. Only the library's **executable** skills are published into
``DynamicToolRegistry`` (and therefore indexed here); instructional skills are
returned solely by ``load_skill``, on request, so a library of tens of thousands
of skills costs the context almost nothing.
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

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

#: Ceiling on a single ``search_skills`` call. Skills are metadata, not tool
#: declarations, so this is a library-side cap rather than a budget one.
SKILLS_SEARCH_MAX = 25


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

    # The Skills Library is a peer of the tool index and is configured the same
    # way: an on/off switch plus a persisted set of disabled skills.
    try:
        from core import skills_library

        lib = skills_library.get_library()
        lib.set_enabled(_truthy(_settings.get("skills_library_enabled", True), True))
        lib.set_disabled(_settings.get("disabled_skills") or [])
    except Exception as exc:  # never fatal: the index alone is still usable
        logger.warning("[ToolBridge] Skills library not configured: %s", exc)

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

    Tracks the session budget minus the always-advertised meta-tools
    (``len(ti.META_TOOLS)`` — search_tools, list_tools, call_tool,
    get_skill_manual, get_tool_surface, search_skills, list_skills,
    load_skill), so widening "Tools sent per session" in Settings widens
    discovery too: a budget of 64 allows 56 results, 128 allows 120.
    Deriving the subtraction from ``META_TOOLS`` keeps the formula correct if
    another meta-tool is ever added.
    The floor of 6 keeps the default reachable even at the smallest budget.
    """
    return max(6, budget() - len(ti.META_TOOLS))


def enabled() -> bool:
    return get_index().enabled and _truthy(_settings.get("tool_index_enabled", True), True)


def _meta_declarations() -> List[Dict[str, Any]]:
    """META_DECLARATIONS with the ``limit`` texts made truthful.

    The ``search_tools`` maximum depends on the live session budget
    (:func:`search_limit_cap`), and the ``search_skills`` maximum on
    :data:`SKILLS_SEARCH_MAX`, so the static descriptions in
    ``tool_index`` would lie the moment either value changes. The copy is
    deep so the shared declarations are never mutated across sessions or
    budgets.
    """
    cap = search_limit_cap()
    out: List[Dict[str, Any]] = []
    for decl in META_DECLARATIONS:
        decl = copy.deepcopy(decl)
        props = (decl.get("parameters") or {}).get("properties") or {}
        limit_spec = props.get("limit")
        if not isinstance(limit_spec, dict):
            out.append(decl)
            continue
        if decl.get("name") == ti.META_SEARCH_TOOL:
            limit_spec["description"] = (
                f"How many tools to return. Default 6, maximum {cap} "
                "(this session's tool budget minus the always-on discovery "
                "meta-tools, set in Settings → Tool & Skill Index)."
            )
        elif decl.get("name") == ti.META_SEARCH_SKILLS_TOOL:
            limit_spec["description"] = (
                f"How many skills to return. Default 6, maximum {SKILLS_SEARCH_MAX}."
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
# Skills Library
# --------------------------------------------------------------------------- #


def skills_library() -> Any:
    """The process-wide :class:`core.skills_library.SkillsLibrary`."""
    from core import skills_library as sl

    return sl.get_library()


def sync_skills(registry: Any = None) -> Dict[str, int]:
    """Publishes the library's *executable* skills and indexes them as tools.

    Instructional skills are deliberately never published: they are pure
    context and are reached only through ``load_skill``. Publishing therefore
    costs almost nothing even when the library holds tens of thousands of
    skills, because only the executable minority become tools.
    """
    if registry is None:
        try:
            from core.dynamic_registry import DynamicToolRegistry

            registry = DynamicToolRegistry
        except Exception:
            registry = None

    try:
        from core import skills_library

        lib = skills_library.get_library()
    except Exception as exc:
        logger.warning("[ToolBridge] Skills library unavailable: %s", exc)
        return {"skills": 0, "executable": 0, "published": 0, "indexed": 0}

    published = 0
    indexed = 0
    # Always publish, even when the library is switched off: ``publish`` then
    # un-registers anything it had previously exposed, so turning the master
    # switch off hides the skills' tools too.
    try:
        published = lib.publish(registry)
    except Exception as exc:
        logger.warning("[ToolBridge] Publishing skills failed: %s", exc)
    if registry is not None:
        try:
            indexed = sync_registry(registry)
            get_index().rebuild()
        except Exception as exc:
            logger.warning("[ToolBridge] Indexing published skills failed: %s", exc)

    stats = lib.stats()
    logger.info(
        "[ToolBridge] skills synced: %d skill(s), %d executable, %d published, %d indexed",
        stats.skills,
        stats.executable,
        published,
        indexed,
    )
    return {
        "skills": stats.skills,
        "executable": stats.executable,
        "published": published,
        "indexed": indexed,
    }


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

    declarations = list(resolved["declarations"])
    # Aggressively promote MCP tools that match common intents to avoid 1008
    try:
        intent_lower = (intent or "").lower()
        if any(k in intent_lower for k in ["chrome", "browser", "synapse", "cdp", "open tab", "navigate"]):
            for entry in index.entries():
                name = entry.name or ""
                if name.startswith("mcp_Synapse") or "cdp" in name.lower():
                    if entry.declaration not in declarations:
                        declarations.append(entry.declaration)
    except Exception:
        pass
    declarations = declarations + _meta_declarations()
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
    """Records the native declarations and performs the first catalog sync.

    The Skills Library is synced *first* so that executable skills are already
    registered when the catalog is built, and so a library of any size is
    reflected in the very first session payload.
    """
    TOOL_DECLARATIONS_CACHE.clear()
    TOOL_DECLARATIONS_CACHE.append(list(native_declarations))
    sync_skills(registry)
    sync_all(native_declarations, registry)


# --------------------------------------------------------------------------- #
# Meta-tool execution
# --------------------------------------------------------------------------- #


def is_meta_tool(name: str) -> bool:
    return name in {d["name"] for d in META_DECLARATIONS}


# --------------------------------------------------------------------------- #
# The agent's own operating manual (``get_tool_surface``)
# --------------------------------------------------------------------------- #


def _prompt_manual() -> str:
    """The on-demand half of the two-tier prompt, with a readable fallback.

    Reads ``core/prompt.txt`` through :mod:`core.prompt_parts` so the manual
    always matches the file the resident core was taken from. Returns "" when
    the file is unreadable, which the handler turns into a plain message.
    """
    try:
        from core import prompt_parts

        _core, manual = prompt_parts.load_prompt_tiers()
        return manual
    except Exception:
        return ""


def _manual_sections(manual: str) -> "List[Tuple[str, str]]":
    """Split the manual into ``(heading, body)`` pairs at blank-line boundaries.

    ``body`` includes its own heading line, so joining bodies back together
    reproduces a faithful excerpt of the file.
    """
    sections: "List[Tuple[str, str]]" = []
    current: "List[str]" = []

    def _flush() -> None:
        if current:
            sections.append((current[0].strip(), "\n".join(current).strip()))
            current.clear()

    for line in manual.splitlines():
        if not line.strip():
            _flush()
            continue
        current.append(line)
    _flush()
    return sections


async def _handle_tool_surface(args: Dict[str, Any]) -> str:
    """Read the agent's own operating manual (the on-demand prompt tier).

    ``section`` filters by heading substring; omitting it returns everything.
    Like the other discovery handlers this never raises - a miss returns a
    readable line the model can act on.
    """
    want = str(args.get("section") or "").strip().lower()
    manual = _prompt_manual().strip()
    if not manual:
        return (
            "No operating manual is available (core/prompt.txt could not be read). "
            "Use search_tools to find tools by goal."
        )
    if not want:
        return manual
    hits = [
        body for heading, body in _manual_sections(manual)
        if want in heading.lower()
    ]
    if not hits:
        return (
            f"No manual section matches '{want}'. "
            "Call get_tool_surface with no arguments to read the whole manual, "
            "or use search_tools to find tools by goal."
        )
    return "\n\n".join(hits)


async def execute_meta(name: str, args: Dict[str, Any]) -> str:
    """Runs one discovery meta-tool and returns model-facing text.

    Each handler fails soft: a retrieval miss or an unknown tool must come back
    as a readable message the model can act on, never as an exception, because
    the model's next move depends on getting an answer either way.
    """
    try:
        if name == ti.META_SEARCH_TOOL:
            return _handle_search(args)
        if name == ti.META_LIST_TOOLS_TOOL:
            return _handle_list_tools(args)
        if name == ti.META_MANUAL_TOOL:
            return _handle_manual(args)
        if name == ti.META_SURFACE_TOOL:
            return await _handle_tool_surface(args)
        if name == ti.META_CALL_TOOL:
            return await _handle_call(args)
        if name == ti.META_SEARCH_SKILLS_TOOL:
            return _handle_search_skills(args)
        if name == ti.META_LIST_SKILLS_TOOL:
            return _handle_list_skills(args)
        if name == ti.META_LOAD_SKILL_TOOL:
            return _handle_load_skill(args)
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
            f"\n{rank}. ⚠️ DISCOVERED TOOL (NOT DIRECTLY CALLABLE) - tool_name: {entry.name}"
            f"\n   group: {entry.skill_id}"
            f"\n   does: {entry.short_description(300)}"
            f"\n   arguments: {argspec}{req}"
        )
        if entry.skill_id and index.manual(entry.skill_id) is not None:
            lines.append(f"   manual: call get_skill_manual with skill_id='{entry.skill_id}'")

    lines.append(
        "\n🚨 ABSOLUTELY CRITICAL: THESE TOOLS ARE NOT IN YOUR DECLARED FUNCTION LIST. "
        "YOU CANNOT CALL THEM DIRECTLY. IF YOU CALL THEM DIRECTLY, THE SESSION WILL CRASH IMMEDIATELY. "
        "YOU MUST USE: call_tool(tool_name=\"<exact name from above>\", arguments={<args from schema>}) "
        "FOR EVERY TOOL LISTED ABOVE. DO NOT CALL THEM BY THEIR NAMES DIRECTLY."
    )
    return "\n".join(lines)


def _page_window(args: Dict[str, Any], total: int) -> Tuple[int, int]:
    """``(offset, limit)`` for the enumeration meta-tools, clamped.

    The window is bounded by :data:`tool_index.LIST_PAGE_MAX` so a single
    ``list_*`` call can never dump the whole catalogue into the model's
    context — enumerating is deliberately a paged operation.
    """
    try:
        offset = int(args.get("offset") or 0)
    except (TypeError, ValueError):
        offset = 0
    offset = max(0, min(offset, total))
    try:
        limit = int(args.get("limit") or ti.LIST_PAGE_DEFAULT)
    except (TypeError, ValueError):
        limit = ti.LIST_PAGE_DEFAULT
    return offset, max(1, min(limit, ti.LIST_PAGE_MAX))


def _handle_list_tools(args: Dict[str, Any]) -> str:
    """Enumerates the tool catalogue in pages — the browse twin of search_tools.

    Where ``search_tools`` answers "which tool for this goal?", this answers
    "what tools do you have?", including the ones no intent would have matched.
    """
    index = get_index()
    all_entries = index.entries()

    source = str(args.get("source") or args.get("group") or "").strip().lower()
    entries = all_entries
    if source:
        entries = [
            e for e in all_entries
            if source in {e.source.lower(), (e.skill_id or "").lower()}
        ]
        if not entries:
            known = sorted(
                {e.source for e in all_entries}
                | {e.skill_id for e in all_entries if e.skill_id}
            )
            return (
                f"No tools with source or group {source!r}. "
                f"Known sources: {', '.join(known) or '(none)'}. "
                "Call list_tools() without 'source' to list everything."
            )

    entries = sorted(entries, key=lambda e: e.name.lower())
    offset, limit = _page_window(args, len(entries))
    page = entries[offset:offset + limit]
    if not page:
        return (
            f"offset {offset} is past the end: the catalogue holds "
            f"{len(entries)} tool(s). Call list_tools(offset=0) to start over."
        )

    scope = f" matching source/group {source!r}" if source else ""
    lines = [f"Listing {len(page)} of {len(entries)} tool(s){scope} "
             f"— positions {offset + 1}-{offset + len(page)}:"]
    for position, entry in enumerate(page, start=offset + 1):
        schema = entry.parameters or {}
        props = schema.get("properties") if isinstance(schema, dict) else None
        argspec = ", ".join(sorted(props.keys())) if isinstance(props, dict) and props else "none"
        lines.append(
            f"{position}. {entry.name} [{entry.skill_id or entry.source}] — "
            f"{entry.short_description(160)} (args: {argspec})"
        )

    next_offset = offset + len(page)
    if next_offset < len(entries):
        lines.append(
            f"\n{len(entries) - next_offset} more. Next page: "
            f"list_tools(offset={next_offset})."
        )
    lines.append(
        "⚠️ CRITICAL: Tools not in your declared function list MUST be called via call_tool(tool_name=<exact name>, arguments={...}). "
        "Do NOT call them directly or the session will crash. Or use search_tools(intent=...) to rank the catalogue against a specific goal."
    )
    return "\n".join(lines)


def _handle_search_skills(args: Dict[str, Any]) -> str:
    """Searches the Skills Library. Metadata only — nothing is loaded here."""
    intent = str(args.get("intent") or args.get("query") or "").strip()
    if not intent:
        return "Error: search_skills requires an 'intent' describing the task."

    try:
        limit = int(args.get("limit") or 6)
    except (TypeError, ValueError):
        limit = 6
    limit = max(1, min(limit, SKILLS_SEARCH_MAX))
    executable_only = _truthy(args.get("executable_only"), False)

    try:
        from core import skills_library

        lib = skills_library.get_library()
    except Exception as exc:
        return f"Error: the Skills Library is unavailable: {exc}"

    if not lib.enabled:
        return "The Skills Library is switched off in Settings, so no skills are available."

    hits = lib.search(intent, limit=limit, executable_only=executable_only)
    if not hits:
        return (
            f"No skill matches {intent!r}. The library holds {lib.stats().skills} skill(s). "
            "Try different wording, or proceed without a skill."
        )

    lines = [f"Found {len(hits)} skill(s) for {intent!r}:"]
    for rank, hit in enumerate(hits, start=1):
        meta = hit.meta
        kind = (
            f"executable (run as tool '{meta.resolved_tool_name()}')"
            if meta.executable
            else "instructional"
        )
        lines.append(
            f"\n{rank}. skill_id: {meta.skill_id}"
            f"\n   does: {meta.description or meta.name}"
            f"\n   type: {kind}"
            f"\n   category: {meta.category}"
        )
    lines.append("\nTo use one, call: load_skill(skill_id=<exact id>). If it's executable, use call_tool with the tool name it returns - NEVER call it directly.")
    return "\n".join(lines)


def _handle_list_skills(args: Dict[str, Any]) -> str:
    """Enumerates the Skills Library in pages — the browse twin of search_skills.

    Metadata only: nothing is loaded here, so listing a library of thousands of
    skills costs one line each and leaves ``load_skill`` as the sole path that
    pulls instructions into context.
    """
    try:
        from core import skills_library

        lib = skills_library.get_library()
    except Exception as exc:
        return f"Error: the Skills Library is unavailable: {exc}"

    if not lib.enabled:
        return "The Skills Library is switched off in Settings, so no skills are available."

    metas = lib.entries(include_disabled=False)

    filters: List[str] = []
    category = str(args.get("category") or "").strip()
    if category:
        filters.append(f"category {category!r}")
        lowered = category.lower()
        metas = [m for m in metas if m.category.lower() == lowered]
    if _truthy(args.get("executable_only"), False):
        filters.append("executable only")
        metas = [m for m in metas if m.executable]

    if not metas:
        if filters:
            known = sorted({m.category for m in lib.entries(include_disabled=False)})
            return (
                f"No skills match ({', '.join(filters)}). "
                f"Categories in the library: {', '.join(known) or '(none)'}. "
                "Call list_skills() with no filters to list everything."
            )
        return (
            "The Skills Library is empty — no skills installed. "
            "Add skill folders under skills_library/ in the app folder."
        )

    metas = sorted(metas, key=lambda m: m.skill_id.lower())
    offset, limit = _page_window(args, len(metas))
    page = metas[offset:offset + limit]
    if not page:
        return (
            f"offset {offset} is past the end: {len(metas)} skill(s) match. "
            "Call list_skills(offset=0) to start over."
        )

    scope = f" ({', '.join(filters)})" if filters else ""
    lines = [f"Listing {len(page)} of {len(metas)} skill(s){scope} "
             f"— positions {offset + 1}-{offset + len(page)}:"]
    for position, meta in enumerate(page, start=offset + 1):
        kind = (
            f"executable, run as tool '{meta.resolved_tool_name()}'"
            if meta.executable
            else "instructional"
        )
        lines.append(
            f"{position}. {meta.skill_id} [{meta.category}] — "
            f"{meta.description or meta.name} ({kind})"
        )

    next_offset = offset + len(page)
    if next_offset < len(metas):
        lines.append(
            f"\n{len(metas) - next_offset} more. Next page: "
            f"list_skills(offset={next_offset})."
        )
    lines.append(
        "⚠️ CRITICAL: Metadata only — call load_skill(skill_id=<exact id>) for the instructions. "
        "If executable, you MUST use call_tool - never call discovered tools directly. "
        "Or search_skills(intent=...) to rank the library against a task."
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Tool-group manuals (``get_skill_manual``)
# --------------------------------------------------------------------------- #


def _handle_load_skill(args: Dict[str, Any]) -> str:
    """Loads one skill into context, on request.

    For an executable skill this also ensures the underlying tool is published
    and indexed, so the agent can run it immediately with ``call_tool`` even if
    the skill was added after the session connected.
    """
    skill_id = str(args.get("skill_id") or args.get("skill") or args.get("name") or "").strip()
    if not skill_id:
        return "Error: load_skill requires a 'skill_id' (from search_skills)."

    try:
        from core import skills_library

        lib = skills_library.get_library()
    except Exception as exc:
        return f"Error: the Skills Library is unavailable: {exc}"

    if not lib.enabled:
        return "The Skills Library is switched off in Settings, so no skills are available."

    spec = lib.get(skill_id, include_disabled=False)
    if spec is None:
        if lib.has(skill_id):
            return f"Skill '{skill_id}' is disabled. Enable it in Settings to use it."
        suggestions = [h.skill_id for h in lib.search(skill_id, limit=3)]
        hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
        return f"No skill named '{skill_id}'.{hint} Use search_skills to find the right id."

    # Executable skills added after startup would not be in the tool index yet;
    # publishing and indexing here keeps load_skill sufficient on its own to make
    # the skill runnable, without a full registry re-enumeration.
    if spec.executable:
        try:
            lib.publish()
            _index_skill_tool(spec)
        except Exception as exc:
            logger.warning("[ToolBridge] Could not publish skill '%s' on load: %s", skill_id, exc)

    return spec.rendered()


def _index_skill_tool(spec: Any) -> None:
    """Make a just-loaded executable skill reachable through ``call_tool``.

    A Live session fixes its tool list at connect time, so a skill published
    afterwards can only be reached through the meta-tools. Upserting it here is
    what lets ``call_tool`` find it mid-session.
    """
    from core.skills_library import SKILL_SOURCE

    index = get_index()
    index.upsert(
        ToolEntry(
            name=spec.resolved_tool_name(),
            description=spec.description or f"Skill {spec.name or spec.skill_id}",
            parameters=spec.parameters or {"type": "OBJECT", "properties": {}},
            source=SKILL_SOURCE,
        )
    )
    index.rebuild()


def _handle_manual(args: Dict[str, Any]) -> str:
    skill_id = str(args.get("skill_id") or args.get("skill") or args.get("name") or "").strip()
    if not skill_id:
        return "Error: get_skill_manual requires a 'skill_id' (shown as 'group' by search_tools)."

    manual = get_index().manual(skill_id)
    if manual is None:
        # A Skills Library skill is also a legitimate manual target, so
        # get_skill_manual keeps working for one id space instead of two.
        fallback = _skill_manual_fallback(skill_id)
        if fallback:
            return fallback
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


def _skill_manual_fallback(skill_id: str) -> str:
    """Serve a Skills Library skill through ``get_skill_manual`` as well.

    Keeps the two discovery surfaces consistent: any id ``search_skills`` can
    return is also readable by the manual tool.
    """
    try:
        from core import skills_library

        lib = skills_library.get_library()
    except Exception:
        return ""
    if not lib.enabled:
        return ""
    spec = lib.get(skill_id, include_disabled=False)
    if spec is None:
        return ""
    return spec.rendered()


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
        "skills": _skills_snapshot(),
    }


def _skills_snapshot() -> Dict[str, Any]:
    """Read-only Skills Library status, or ``{}`` when it cannot be read."""
    try:
        from core import skills_library

        return skills_library.get_library().manifest()
    except Exception:
        return {}


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


# --------------------------------------------------------------------------- #
# Skills Library management surface
# --------------------------------------------------------------------------- #


def skills_catalog(limit: int = 500) -> Dict[str, Any]:
    """Grouped view of the Skills Library, for the Settings UI.

    Every skill is listed with its ``enabled`` flag, so a disabled skill can be
    switched back on. ``limit`` bounds the response because the library is built
    for tens of thousands of entries; the count is always reported in full.
    """
    try:
        from core import skills_library

        lib = skills_library.get_library()
    except Exception as exc:
        return {"groups": [], "skills": [], "total": 0, "error": str(exc)}

    disabled = lib.disabled()
    groups: Dict[str, Dict[str, Any]] = {}
    listed = 0
    for meta in lib.entries(include_disabled=True):
        listed += 1
        if listed > max(1, int(limit)):
            continue
        key = meta.category or "general"
        group = groups.get(key)
        if group is None:
            group = {"id": key, "label": key.title(), "skills": []}
            groups[key] = group
        group["skills"].append(
            {
                "skill_id": meta.skill_id,
                "name": meta.name,
                "description": meta.description,
                "executable": meta.executable,
                "source": meta.source,
                "tags": list(meta.tags),
                "tool_name": meta.resolved_tool_name() if meta.executable else "",
                "enabled": meta.active and meta.skill_id not in disabled,
            }
        )

    ordered = sorted(groups.values(), key=lambda g: g["id"].lower())
    for group in ordered:
        group["skills"].sort(key=lambda s: s["skill_id"].lower())
    return {
        "groups": ordered,
        "total": len(lib.entries(include_disabled=True)),
        "enabled": lib.enabled,
        "disabled": sorted(disabled),
        "limit": max(1, int(limit)),
    }


def disabled_skills() -> List[str]:
    """Skill ids currently switched off."""
    try:
        from core import skills_library

        return sorted(skills_library.get_library().disabled())
    except Exception:
        return []


def set_disabled_skills(skill_ids: Iterable[str]) -> None:
    """Replaces the switched-off skill set. Takes effect on the next search/load."""
    try:
        from core import skills_library

        skills_library.get_library().set_disabled(list(skill_ids or []))
    except Exception as exc:
        logger.warning("[ToolBridge] Could not set disabled skills: %s", exc)


def set_skills_enabled(enabled: bool) -> None:
    """Turns the whole Skills Library on or off."""
    try:
        from core import skills_library

        skills_library.get_library().set_enabled(bool(enabled))
    except Exception as exc:
        logger.warning("[ToolBridge] Could not toggle skills library: %s", exc)