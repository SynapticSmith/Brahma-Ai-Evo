"""System prompt assembly for the agent harness.

The resident prompt is deliberately short. Long tool lists and long
instructions compete for the same window, and a model that has read forty
rules remembers none of them. What is here is the irreducible set: work until
verified, read before writing, never invent tool output.

Project instructions (``AGENTS.md`` / ``BRAHMA.md``) are appended last and are
the only per-workspace input. That ordering is intentional: project rules
should win over generic advice.

Instruction files are also read from the workspace's parent directories (a
repo nested inside a monorepo should inherit the monorepo's rules). The walk is
bounded — see ``DEFAULT_MAX_INSTRUCTION_PARENTS`` — and can be switched off with
``BRAHMA_AGENT_INSTRUCTION_PARENTS=0``. Parent files are emitted first, so the
nearest (workspace) file is the last thing the model reads and therefore wins.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

#: Instruction files looked for in each directory, in priority order.
PROJECT_FILES = ("AGENTS.md", "BRAHMA.md", ".brahma/AGENTS.md")

#: How many directories above the workspace to inspect. Three covers the usual
#: ``monorepo/packages/tool`` layout without wandering into the filesystem root
#: (and the user's home directory) by accident.
DEFAULT_MAX_INSTRUCTION_PARENTS = 3

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


def _max_parents() -> int:
    """Parent levels to read, honouring the operator override."""
    raw = os.getenv("BRAHMA_AGENT_INSTRUCTION_PARENTS", "").strip()
    if not raw:
        return DEFAULT_MAX_INSTRUCTION_PARENTS
    try:
        return max(0, min(10, int(raw)))
    except ValueError:
        return DEFAULT_MAX_INSTRUCTION_PARENTS


def instruction_dirs(
    workspace: Path,
    *,
    max_parents: Optional[int] = None,
) -> List[Path]:
    """Workspace plus up to ``max_parents`` ancestors, root-most first.

    The drive/filesystem root is never included: ``AGENTS.md`` directly at
    ``C:\\`` is not project context. The order matters — callers emit in this
    order so the most specific file is written last.
    """
    try:
        current = Path(workspace).expanduser().resolve()
    except OSError:
        current = Path(workspace).expanduser()
    levels = _max_parents() if max_parents is None else max(0, int(max_parents))

    dirs = [current]
    parent = current.parent
    for _ in range(levels):
        if parent == parent.parent or parent in dirs:
            break
        dirs.append(parent)
        parent = parent.parent
    dirs.reverse()
    return dirs


def load_project_instructions(
    workspace: Optional[Path],
    *,
    max_parents: Optional[int] = None,
) -> str:
    """Concatenate instruction files from the workspace and its ancestors."""
    if workspace is None:
        return ""
    try:
        workspace_root = Path(workspace).expanduser().resolve()
    except OSError:
        workspace_root = Path(workspace).expanduser()
    seen = set()
    chunks = []
    for directory in instruction_dirs(workspace_root, max_parents=max_parents):
        for name in PROJECT_FILES:
            path = directory / name
            try:
                if not path.is_file():
                    continue
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                text = path.read_text(encoding="utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    label = str(path.relative_to(workspace_root)).replace("\\", "/")
                except ValueError:
                    label = str(path)
                chunks.append(f"# Project instructions ({label})\n{text}")
            except (OSError, ValueError):
                continue
    if not chunks:
        return ""
    joined = "\n\n".join(chunks)
    if len(joined) > MAX_PROJECT_CHARS:
        # Keep the tail: the most specific (nearest) instructions are last, and
        # dropping them is exactly the wrong trade.
        joined = (
            "... [earlier project instructions truncated]\n"
            + joined[-MAX_PROJECT_CHARS:]
        )
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
