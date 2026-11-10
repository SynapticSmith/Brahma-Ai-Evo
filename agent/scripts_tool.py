"""Harness tool for searching verified automation and utility scripts (§9.9, §16 Decision 11).

Provides the ``search_scripts`` ToolSpec, allowing agents and local models to find
deterministic, argv-driven repository scripts from config/scripts.json by intent or domain.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from .spec import ToolContext, ToolSpec, register
from .turn_context import shortlist_scripts

_SEARCH_SCRIPTS_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Functional intent or task description "
                "(e.g. 'convert docx to markdown', 'rebuild brain index', 'lint taxonomy notes')."
            ),
        },
        "domain": {
            "type": "string",
            "description": "Optional taxonomy domain to filter or boost results (e.g. 'applied-sciences/computing', 'meta-knowledge').",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of matching scripts to return (1 to 10; default 5).",
            "default": 5,
        },
    },
    "required": ["query"],
}


def search_scripts(args: Dict[str, Any], context: ToolContext) -> str:
    """Search verified automation and utility scripts matching intent and domain."""
    query = str(args.get("query") or "").strip()
    if not query:
        return "search_scripts requires a 'query' parameter."

    domain = args.get("domain")
    if domain is not None:
        domain = str(domain).strip() or None

    try:
        limit = int(args.get("limit", 5))
        limit = max(1, min(10, limit))
    except (ValueError, TypeError):
        limit = 5

    matches = shortlist_scripts(query, domain=domain, limit=limit)
    if not matches:
        return json.dumps({
            "query": query,
            "count": 0,
            "scripts": [],
            "message": "No matching scripts found in verified scripts catalog.",
        }, ensure_ascii=False, indent=2)

    results: List[Dict[str, Any]] = []
    for m in matches:
        path = m.get("path") or m.get("name") or ""
        safety = m.get("safety", "read_only")
        summary = m.get("summary") or m.get("description") or ""
        suggested = f"python {path}"
        results.append({
            "name": m.get("name", ""),
            "path": path,
            "summary": summary,
            "domain": m.get("domain", ""),
            "safety": safety,
            "tags": m.get("tags", []),
            "suggested_command": suggested,
            "args": m.get("args", []),
        })

    return json.dumps({
        "query": query,
        "count": len(results),
        "scripts": results,
    }, ensure_ascii=False, indent=2)


SEARCH_SCRIPTS_SPEC = ToolSpec(
    name="search_scripts",
    description=(
        "Search verified repository automation and utility scripts by functional intent "
        "or domain. Returns script filenames, paths, safety classifications, and suggested "
        "command lines. Run the selected script via run_command."
    ),
    parameters=_SEARCH_SCRIPTS_SCHEMA,
    tool_class="read",
    handler=search_scripts,
    tags=("scripts", "search", "tools", "automation"),
    max_output_chars=4_000,
)


def register_all() -> None:
    """Idempotent registration of search_scripts."""
    try:
        from . import spec as spec_module

        if spec_module.get("search_scripts") is None:
            register(SEARCH_SCRIPTS_SPEC)
    except Exception:
        register(SEARCH_SCRIPTS_SPEC)


register_all()
