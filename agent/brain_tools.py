"""Harness tools for the second brain.

Automatic retrieval happens in :mod:`agent.second_brain` before the model sees
a turn, so these tools are for *deliberate depth*: a targeted search, reading a
whole page, checking freshness, or rebuilding the index. A small model should
not need them for ordinary questions.

The reader takes ``page`` rather than ``path`` because it is contained to the
vault root by construction (see ``second_brain.read_page``): it cannot read
outside the vault, so it must not be mistaken for a general filesystem tool.
"""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from . import second_brain
from .spec import ToolContext, ToolSpec, register

#: A tool-triggered sync is bounded; the CLI is for a full backfill.
TOOL_EMBED_LIMIT = 64
TOOL_EMBED_LIMIT_MAX = 512

_SEARCH_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "What to look for in the user's Obsidian vault.",
        },
        "k": {
            "type": "integer",
            "description": "How many notes to return (1-12, default 6).",
            "minimum": 1,
            "maximum": 12,
        },
        "shelf": {
            "type": "string",
            "description": "Optional shelf to restrict to, e.g. 'wiki', 'raw', 'book-library'.",
        },
    },
    "required": ["query"],
}

_READ_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "page": {
            "type": "string",
            "description": "Vault-relative Markdown path, e.g. 'wiki/entities/calyx.md'.",
        },
        "max_chars": {
            "type": "integer",
            "description": "Maximum characters to return (default 8000).",
            "minimum": 400,
            "maximum": 40000,
        },
    },
    "required": ["page"],
}

_STATUS_SCHEMA: Dict[str, Any] = {"type": "object", "properties": {}}

_SYNC_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "embed": {
            "type": "boolean",
            "description": "Also compute embeddings for new chunks (default true).",
        },
        "limit": {
            "type": "integer",
            "description": "Index at most this many pages (for a quick partial build).",
            "minimum": 1,
        },
        "embed_limit": {
            "type": "integer",
            "description": (
                "Embed at most this many chunks this run. The tool defaults to "
                f"{TOOL_EMBED_LIMIT} and caps at {TOOL_EMBED_LIMIT_MAX}; a CPU needs "
                "about 1.7 s per chunk, so the CLI is the place for a full backfill."
            ),
            "minimum": 1,
        },
        "rebuild": {
            "type": "boolean",
            "description": "Discard and rebuild the whole index (slow).",
        },
    },
}


def brain_search(args: Dict[str, Any], context: ToolContext) -> str:
    query = str(args.get("query") or "").strip()
    if not query:
        return "brain_search needs a query."
    k = int(args.get("k") or 6)
    shelf = str(args.get("shelf") or "").strip() or None
    results = second_brain.search(query, k=k, shelf=shelf)
    if not results:
        return (
            "No matching notes. The index may be empty or stale: run brain_status, "
            "then brain_sync."
        )
    lines = [f"Second-brain results for {query!r}:"]
    for number, item in enumerate(results, start=1):
        heading = f" › {item['heading']}" if item.get("heading") else ""
        lines.append(f"\n[{number}] {item['path']}{heading}\n{item['snippet']}")
    return "\n".join(lines)


def brain_read(args: Dict[str, Any], context: ToolContext) -> str:
    return second_brain.read_page(
        str(args.get("page") or ""), int(args.get("max_chars") or 8_000)
    )


def brain_status(args: Dict[str, Any], context: ToolContext) -> str:
    return json.dumps(second_brain.status(), ensure_ascii=False, indent=2)


def brain_sync(args: Dict[str, Any], context: ToolContext) -> str:
    embed = bool(args.get("embed", True))
    # A tool-triggered sync must finish inside a turn. Embedding on this CPU is
    # ~1.7 s/chunk, so an uncapped run could pin the machine for hours. The
    # CLI (``tools/brain_sync.py``) has no cap for deliberate backfills.
    embed_limit: Optional[int] = None
    if embed:
        raw = args.get("embed_limit")
        embed_limit = TOOL_EMBED_LIMIT if raw is None else int(raw)
        embed_limit = max(1, min(TOOL_EMBED_LIMIT_MAX, embed_limit))
    stats = second_brain.sync(
        rebuild=bool(args.get("rebuild")),
        embed=embed,
        limit=int(args["limit"]) if args.get("limit") else None,
        embed_limit=embed_limit,
    )
    return json.dumps(stats, ensure_ascii=False, indent=2)


SPECS = (
    ToolSpec(
        name="brain_search",
        description=(
            "Search the user's curated Obsidian second brain (about 6,600 notes) by "
            "keyword and embedding. Use when the automatically retrieved SECOND BRAIN "
            "block is not enough, or for a targeted lookup. Returns note paths, "
            "headings and snippets."
        ),
        parameters=_SEARCH_SCHEMA,
        tool_class="read",
        handler=brain_search,
        tags=("brain", "search", "knowledge"),
        max_output_chars=12_000,
    ),
    ToolSpec(
        name="brain_read",
        description=(
            "Read one page from the user's Obsidian second brain. The page path is "
            "vault-relative (e.g. 'wiki/entities/calyx.md') and cannot leave the vault."
        ),
        parameters=_READ_SCHEMA,
        tool_class="read",
        handler=brain_read,
        tags=("brain", "read", "knowledge"),
        max_output_chars=40_000,
    ),
    ToolSpec(
        name="brain_status",
        description=(
            "Report the second-brain index: vault path, page/chunk/vector counts, "
            "last sync, embedding model and any embedding error."
        ),
        parameters=_STATUS_SCHEMA,
        tool_class="read",
        handler=brain_status,
        tags=("brain", "status"),
    ),
    ToolSpec(
        name="brain_sync",
        description=(
            "Incrementally re-index the Obsidian second brain (new and changed pages "
            "only). Writes Brahma's own index, never the vault. Use after the user "
            "edits notes; a full rebuild is slow."
        ),
        parameters=_SYNC_SCHEMA,
        tool_class="write",
        handler=brain_sync,
        tags=("brain", "sync", "index"),
        max_output_chars=4_000,
    ),
)


def register_all() -> None:
    """Idempotent registration, safe to call from every entry point."""
    for spec in SPECS:
        existing = None
        try:
            from . import spec as spec_module

            existing = spec_module.get(spec.name)
        except Exception:
            pass
        if existing is None:
            register(spec)


register_all()

BRAIN_TOOL_NAMES = tuple(spec.name for spec in SPECS)
