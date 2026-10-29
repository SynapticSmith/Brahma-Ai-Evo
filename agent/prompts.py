"""System prompt assembly for the agent harness.

The resident prompt is deliberately short. Long tool lists and long
instructions compete for the same window, and a model that has read forty
rules remembers none of them. What is here is the irreducible set: work until
verified, read before writing, never invent tool output.

Project instructions (``AGENTS.md`` / ``BRAHMA.md``) are appended last and are
the only per-workspace input. That ordering is intentional: project rules
should win over generic advice.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

#: Instruction files looked for in the workspace root, in priority order.
PROJECT_FILES = ("AGENTS.md", "BRAHMA.md", ".brahma/AGENTS.md")

#: A project instruction file bigger than this is almost certainly a brain
#: dump; truncate rather than blow the window on the first turn.
MAX_PROJECT_CHARS = 8_000


BASE_SYSTEM = """You are Brahma, an autonomous agent running on the user's Windows machine.

You operate in a loop: you call tools, read their results, and continue until
the task is actually done. A turn that ends with a tool call is not a turn
that ends the task — keep going.

Rules that matter more than sounding helpful:

1. Read before you write. Never edit a file you have not read in this session.
2. Prefer edit_file over write_file. A whole-file rewrite silently deletes
   work you did not look at.
3. Verify. After changing code, run the project's tests or linter with
   run_command and read the failures. If you cannot verify, say what you could
   not check instead of claiming success.
4. Never invent tool output. If a tool failed, report the failure. If you did
   not run a command, do not describe its output.
5. Paths are relative to the workspace root. Do not escape it; if you need
   something outside, explain why and let the policy ask the user.
6. Do not repeat a call that returned the same result. Change the approach.
7. Finish with a short plain-language summary of what changed and what you
   verified — no headers, no bullet-point theatre, no restating the request.

You have file read/write/edit, glob, grep and shell tools. Use grep and glob to
find things; do not read a directory one file at a time."""


def load_project_instructions(workspace: Optional[Path]) -> str:
    """Concatenate the workspace's instruction files, if any exist."""
    if workspace is None:
        return ""
    chunks = []
    for name in PROJECT_FILES:
        path = workspace / name
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    chunks.append(f"# Project instructions ({name})\n{text}")
        except OSError:
            continue
    if not chunks:
        return ""
    joined = "\n\n".join(chunks)
    if len(joined) > MAX_PROJECT_CHARS:
        joined = joined[:MAX_PROJECT_CHARS] + "\n... [project instructions truncated]"
    return joined


def build_system_prompt(
    workspace: Optional[Path] = None,
    *,
    extra: str = "",
    permission_note: str = "",
) -> str:
    parts = [BASE_SYSTEM]
    if workspace is not None:
        parts.append(f"Workspace root: {workspace}")
    if permission_note:
        parts.append(permission_note)
    project = load_project_instructions(workspace)
    if project:
        parts.append(project)
    if extra:
        parts.append(extra)
    return "\n\n".join(part for part in parts if part)
