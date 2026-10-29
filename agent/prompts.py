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

Files *below* the workspace are loaded on demand: the loop knows which paths a
turn touched, so ``load_directory_instructions`` walks only the directories
between the workspace and those paths. A blind recursive scan would pull in
``node_modules`` and unrelated packages, cost a read per turn, and let an
``AGENTS.md`` nobody is working near outrank the one that matters.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

#: Instruction files looked for in each directory, in priority order.
PROJECT_FILES = ("AGENTS.md", "BRAHMA.md", ".brahma/AGENTS.md")

#: How many directories above the workspace to inspect. Three covers the usual
#: ``monorepo/packages/tool`` layout without wandering into the filesystem root
#: (and the user's home directory) by accident.
DEFAULT_MAX_INSTRUCTION_PARENTS = 3

#: A project instruction file bigger than this is almost certainly a brain
#: dump; truncate rather than blow the window on the first turn.
MAX_PROJECT_CHARS = 8_000

#: Directories that never carry project instructions worth loading. A path
#: inside one of these is ignored outright, so reading a vendored file cannot
#: inject a package's own agent rules.
IGNORED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "node_modules",
        "site-packages",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "env",
        "dist",
        "build",
        "target",
        "bin",
        "obj",
    }
)

#: How far below the workspace to walk for a single touched path. Deeper than
#: this is usually generated or vendored, not hand-written project structure.
MAX_INSTRUCTION_DEPTH = 12

#: Total characters of dynamically discovered instructions per run. The root
#: instructions have their own budget; this one stops a wander through a large
#: tree from crowding the window.
MAX_SUBDIR_INSTRUCTION_CHARS = 6_000

#: Per-file cap for directory instructions. The head is kept: unlike the root
#: budget (where the nearest file is last), a single file's rules come first.
MAX_SUBDIR_FILE_CHARS = 4_000

#: Argument names that carry a path. Same set the policy uses, plus ``cwd``.
PATH_ARG_KEYS = (
    "path",
    "file",
    "filename",
    "file_path",
    "target",
    "cwd",
    "dir",
    "directory",
    "root",
    "source",
    "destination",
)


@dataclass
class DirectoryInstructions:
    """What one discovery pass found, ready to append to a system prompt."""

    text: str = ""
    labels: List[str] = field(default_factory=list)
    truncated: bool = False


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


def _workspace_root(workspace: Path) -> Path:
    try:
        return Path(workspace).expanduser().resolve()
    except OSError:
        return Path(workspace).expanduser()


def root_instruction_files(
    workspace: Optional[Path],
    *,
    max_parents: Optional[int] = None,
) -> List[Path]:
    """Instruction files from the workspace and its ancestors, root-most first."""
    if workspace is None:
        return []
    files: List[Path] = []
    seen = set()
    for directory in instruction_dirs(workspace, max_parents=max_parents):
        for name in PROJECT_FILES:
            path = directory / name
            try:
                if not path.is_file():
                    continue
                key = os.path.normcase(str(path.resolve()))
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            files.append(path)
    return files


def _relative_parts(path: Path, root: Path) -> Tuple[str, ...]:
    try:
        return path.relative_to(root).parts
    except ValueError:
        return ()


def _ignored(parts: Iterable[str]) -> bool:
    return any(part.lower() in IGNORED_DIRS for part in parts)


def _read_instruction(path: Path, root: Path) -> Optional[Tuple[str, str]]:
    """``(label, text)`` for one instruction file, or ``None`` when unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except (OSError, ValueError):
        return None
    if not text:
        return None
    label = "/".join(_relative_parts(path, root)) or str(path)
    return label, text


def load_project_instructions(
    workspace: Optional[Path],
    *,
    max_parents: Optional[int] = None,
) -> str:
    """Concatenate instruction files from the workspace and its ancestors."""
    if workspace is None:
        return ""
    workspace_root = _workspace_root(workspace)
    chunks = []
    for path in root_instruction_files(workspace_root, max_parents=max_parents):
        loaded = _read_instruction(path, workspace_root)
        if loaded is None:
            continue
        label, text = loaded
        chunks.append(f"# Project instructions ({label})\n{text}")
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


def touched_directories(
    workspace: Path,
    raw_paths: Iterable[str],
) -> List[Path]:
    """Directories between the workspace and each touched path, shallow first.

    A file contributes its ancestors; an existing directory contributes itself
    too (a package's own ``AGENTS.md`` is in the package directory). Paths
    outside the workspace, the workspace itself, and paths under
    :data:`IGNORED_DIRS` contribute nothing. The result is deduplicated and
    ordered shallowest-first so the nearest instructions are appended last.
    """
    root = _workspace_root(workspace)
    dirs: List[Path] = []
    seen: Set[str] = set()
    for raw in raw_paths:
        if not isinstance(raw, str) or not raw.strip():
            continue
        try:
            target = Path(raw).expanduser()
            if not target.is_absolute():
                target = root / target
            target = target.resolve()
        except (OSError, ValueError):
            continue
        if target == root:
            continue
        parts = _relative_parts(target, root)
        if not parts or _ignored(parts):
            continue
        try:
            is_dir = target.is_dir()
        except OSError:
            is_dir = False
        dir_parts = parts if is_dir else parts[:-1]
        for depth in range(1, min(len(dir_parts), MAX_INSTRUCTION_DEPTH) + 1):
            directory = root.joinpath(*dir_parts[:depth])
            key = os.path.normcase(str(directory))
            if key in seen:
                continue
            seen.add(key)
            dirs.append(directory)
    dirs.sort(key=lambda path: len(_relative_parts(path, root)))
    return dirs


def load_directory_instructions(
    workspace: Path,
    raw_paths: Iterable[str],
    *,
    seen: Optional[Set[str]] = None,
    budget: int = MAX_SUBDIR_INSTRUCTION_CHARS,
) -> DirectoryInstructions:
    """Load instruction files for the directories a turn actually touched.

    ``seen`` holds normalised absolute paths already loaded (root files and
    earlier passes); every candidate inspected is added to it, so a second turn
    touching the same package costs one stat, not another read and another copy
    in the prompt. The caller owns the set and its remaining ``budget``.
    """
    root = _workspace_root(workspace)
    result = DirectoryInstructions()
    if budget <= 0:
        return result
    seen = seen if seen is not None else set()
    chunks: List[str] = []
    used = 0

    for directory in touched_directories(root, raw_paths):
        for name in PROJECT_FILES:
            path = directory / name
            try:
                if not path.is_file():
                    continue
                key = os.path.normcase(str(path.resolve()))
            except OSError:
                continue
            if key in seen:
                continue
            seen.add(key)
            loaded = _read_instruction(path, root)
            if loaded is None:
                continue
            label, text = loaded
            if len(text) > MAX_SUBDIR_FILE_CHARS:
                text = text[:MAX_SUBDIR_FILE_CHARS].rstrip() + "\n... [truncated]"
                result.truncated = True
            chunk = f"# Project instructions ({label})\n{text}"
            if used + len(chunk) > budget:
                result.truncated = True
                return DirectoryInstructions(
                    text="\n\n".join(chunks + ["... [directory instructions truncated]"]),
                    labels=result.labels,
                    truncated=True,
                )
            used += len(chunk)
            result.labels.append(label)
            chunks.append(chunk)

    result.text = "\n\n".join(chunks)
    return result



def build_system_prompt(
    workspace: Optional[Path] = None,
    *,
    extra: str = "",
    permission_note: str = "",
    max_parents: Optional[int] = None,
) -> str:
    parts = [BASE_SYSTEM]
    if workspace is not None:
        parts.append(f"Workspace root: {workspace}")
    if permission_note:
        parts.append(permission_note)
    project = load_project_instructions(workspace, max_parents=max_parents)
    if project:
        parts.append(project)
    if extra:
        parts.append(extra)
    return "\n\n".join(part for part in parts if part)
