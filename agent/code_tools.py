"""The six file/process primitives an agent needs.

The existing ``actions/file_controller.py`` is organised around a *user* who
says "put the report on my desktop": it is sandboxed to the shell folders,
writes whole files, and has no way to express "change lines 40-52". That is
the wrong primitive for an agent editing a repository.

This module is the repository-shaped set:

    read_file    numbered lines, offset/limit
    write_file   create or replace, undo-registered
    edit_file    exact-string replace with a uniqueness contract
    glob_files   pattern -> paths, newest first
    grep_files   regex over a glob, ``path:line: text``
    run_command  shell out in the workspace and read stdout/stderr

Everything resolves against an injected :class:`~agent.spec.ToolContext`
workspace and is gated by :mod:`agent.permissions` at the dispatch boundary,
not here: handlers stay pure enough to test directly.
"""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .spec import ToolContext, register, ToolSpec

#: Directories that are never worth walking. Kept in sync with the spirit of
#: ``pyproject.toml``'s ruff excludes. ``.git`` is here because grep over pack
#: files is both useless and slow.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "ENV", "env",
    "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist",
    "build", ".next", ".tox", "site-packages", ".idea", ".vscode",
}

#: Refuse to load files larger than this into a prompt. A 200MB log is a
#: denial-of-context, not a read.
MAX_READ_BYTES = 2_000_000

#: grep stops after this many *files* even if it has not hit the match cap.
MAX_GREP_FILES = 4_000


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _decode(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _read_text(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist")
    if path.is_dir():
        raise IsADirectoryError(f"{path} is a directory, not a file")
    size = path.stat().st_size
    if size > MAX_READ_BYTES:
        raise ValueError(f"{path} is {size:,} bytes; the read cap is {MAX_READ_BYTES:,}")
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError(f"{path} looks binary; use run_command for binary inspection")
    return _decode(data)


def _iter_files(base: Path, pattern: str, limit: int) -> Iterable[Path]:
    """Yield matching files under ``base``, skipping the usual noise."""
    count = 0
    if not base.exists():
        return
    if base.is_file():
        yield base
        return
    candidates = base.glob(pattern) if any(ch in pattern for ch in "*?[") else base.rglob(pattern)
    for path in candidates:
        if count >= limit:
            return
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        count += 1
        yield path


def _relative(context: ToolContext, path: Path) -> str:
    return context.display_path(path).replace("\\", "/")


def _push_undo(label: str, path: Path, previous: Optional[str]) -> None:
    """Register a reversible write with the app's existing undo stack.

    Imported lazily so this module stays importable in a headless test process
    that has no intention of bootstrapping the rest of the app.
    """
    try:
        from core.undo import push_undo
    except Exception:
        return

    if previous is None:
        def _undo() -> str:
            try:
                if path.exists():
                    path.unlink()
                return f"Removed {path.name}."
            except OSError as exc:
                return f"Could not remove {path.name}: {exc}"
    else:
        def _undo() -> str:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(previous, encoding="utf-8")
                return f"Restored {path.name} to its previous contents."
            except OSError as exc:
                return f"Could not restore {path.name}: {exc}"

    _push_safe(push_undo, label, _undo)


def _push_safe(push, label: str, fn) -> None:
    try:
        push(label, fn)
    except Exception:
        pass


def _diff(before: str, after: str, name: str, limit: int = 4_000) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
            n=2,
        )
    )
    text = "".join(lines)
    if len(text) > limit:
        text = text[:limit] + f"\n... [diff truncated at {limit} chars]"
    return text


# --------------------------------------------------------------------------- #
# read_file
# --------------------------------------------------------------------------- #

READ_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File to read, relative to the workspace."},
        "start_line": {"type": "integer", "description": "First line to return (1-based)."},
        "end_line": {"type": "integer", "description": "Last line to return (inclusive)."},
        "max_chars": {
            "type": "integer",
            "description": "Output cap; defaults to the tool budget. Narrow the range instead.",
        },
    },
    "required": ["path"],
}


def read_file(args: Dict[str, Any], context: ToolContext) -> str:
    path = context.resolve_path(args.get("path"))
    try:
        text = _read_text(path)
    except (OSError, ValueError) as exc:
        return f"Cannot read {_relative(context, path)}: {exc}"
    lines = text.splitlines()
    start = max(1, int(args.get("start_line") or 1))
    end = int(args.get("end_line") or len(lines))
    end = min(end, len(lines))
    if start > len(lines):
        return f"{_relative(context, path)} has {len(lines)} lines; start_line {start} is past the end."
    cap = int(args.get("max_chars") or context.max_output_chars or 20_000)
    out: List[str] = []
    used = 0
    for number in range(start, end + 1):
        entry = f"{number:>5}| {lines[number - 1]}"
        used += len(entry) + 1
        if used > cap:
            out.append(f"... [{end - number + 1} more lines; raise end_line or read a range] ...")
            break
        out.append(entry)
    header = f"{_relative(context, path)} (lines {start}-{min(end, len(lines))} of {len(lines)})"
    return header + "\n" + "\n".join(out)


# --------------------------------------------------------------------------- #
# write_file
# --------------------------------------------------------------------------- #

WRITE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File to create or overwrite, relative to the workspace."},
        "content": {"type": "string", "description": "Full new contents of the file."},
        "overwrite": {
            "type": "boolean",
            "description": "Must be true to replace an existing file. Defaults to true.",
        },
    },
    "required": ["path", "content"],
}


def write_file(args: Dict[str, Any], context: ToolContext) -> str:
    path = context.resolve_path(args.get("path"))
    if path.exists() and path.is_dir():
        return f"{_relative(context, path)} is a directory."
    content = args.get("content")
    if not isinstance(content, str):
        return "write_file needs 'content' as a string."
    overwrite = bool(args.get("overwrite", True))
    previous: Optional[str] = None
    if path.exists():
        if not overwrite:
            return f"{_relative(context, path)} already exists and overwrite=false."
        try:
            previous = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            previous = ""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(content, encoding="utf-8", newline="")
    except OSError as exc:
        return f"Cannot write {_relative(context, path)}: {exc}"
    _push_undo(f"write {_relative(context, path)}", path, previous)
    verb = "Overwrote" if previous is not None else "Created"
    detail = _diff(previous or "", content, path.name) if previous is not None else ""
    summary = f"{verb} {_relative(context, path)} ({len(content)} chars, {content.count(chr(10)) + 1} lines)."
    return summary + (("\n" + detail) if detail else "")


# --------------------------------------------------------------------------- #
# edit_file
# --------------------------------------------------------------------------- #

EDIT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File to edit, relative to the workspace."},
        "old_string": {
            "type": "string",
            "description": "Exact text to replace, including indentation. Must be unique unless replace_all is true.",
        },
        "new_string": {"type": "string", "description": "Replacement text."},
        "replace_all": {"type": "boolean", "description": "Replace every occurrence instead of requiring uniqueness."},
    },
    "required": ["path", "old_string", "new_string"],
}


def edit_file(args: Dict[str, Any], context: ToolContext) -> str:
    path = context.resolve_path(args.get("path"))
    old = args.get("old_string")
    new = args.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        return "edit_file needs 'old_string' and 'new_string' as strings."
    if old == "":
        return "old_string must not be empty; use write_file to create a file."
    if old == new:
        return "old_string and new_string are identical; nothing to do."

    try:
        before = _read_text(path)
    except (OSError, ValueError) as exc:
        return f"Cannot edit {_relative(context, path)}: {exc}"
    count = before.count(old)
    replace_all = bool(args.get("replace_all", False))
    if count == 0:
        hint = ""
        stripped = old.strip()
        if stripped and stripped in before:
            hint = " The text exists but the surrounding whitespace differs; include the exact indentation."
        return f"old_string was not found in {_relative(context, path)}.{hint} Read the file and retry."
    if count > 1 and not replace_all:
        return (
            f"old_string appears {count} times in {_relative(context, path)}. "
            "Include more surrounding context to make it unique, or pass replace_all=true deliberately."
        )
    after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
    path.write_text(after, encoding="utf-8", newline="")
    _push_undo(f"edit {_relative(context, path)}", path, before)
    changed = count if replace_all else 1
    return (
        f"Edited {_relative(context, path)} ({changed} replacement{'s' if changed != 1 else ''}).\n"
        + _diff(before, after, path.name)
    )


# --------------------------------------------------------------------------- #
# glob_files
# --------------------------------------------------------------------------- #

GLOB_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Glob such as '**/*.py' or 'src/**/test_*.ts'."},
        "path": {"type": "string", "description": "Directory to search. Defaults to the workspace root."},
        "limit": {"type": "integer", "description": "Maximum paths to return (default 200)."},
    },
    "required": ["pattern"],
}


def glob_files(args: Dict[str, Any], context: ToolContext) -> str:
    pattern = str(args.get("pattern") or "*")
    base = context.resolve_path(args.get("path"))
    limit = max(1, min(int(args.get("limit") or 200), 2_000))
    found = list(_iter_files(base, pattern, limit * 4))
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    found = found[:limit]
    if not found:
        return f"No files matching {pattern!r} under {_relative(context, base)}."
    lines = [_relative(context, p) for p in found]
    suffix = "" if len(found) < limit else f"\n... [stopped at {limit}; raise limit or narrow the pattern]"
    return f"{len(found)} file(s) matching {pattern!r}:\n" + "\n".join(lines) + suffix


# --------------------------------------------------------------------------- #
# grep_files
# --------------------------------------------------------------------------- #

GREP_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "description": "Python regular expression to search for."},
        "include": {"type": "string", "description": "Glob filter, e.g. '**/*.py'. Defaults to all text files."},
        "path": {"type": "string", "description": "Directory to search. Defaults to the workspace root."},
        "max_results": {"type": "integer", "description": "Maximum matching lines (default 100)."},
        "case_sensitive": {"type": "boolean", "description": "Defaults to true."},
    },
    "required": ["pattern"],
}


def grep_files(args: Dict[str, Any], context: ToolContext) -> str:
    pattern = str(args.get("pattern") or "")
    if not pattern:
        return "grep_files needs a 'pattern'."
    flags = 0 if args.get("case_sensitive", True) else re.IGNORECASE
    try:
        regex = re.compile(pattern, flags)
    except re.error as exc:
        return f"Invalid regular expression {pattern!r}: {exc}"
    include = str(args.get("include") or "**/*")
    base = context.resolve_path(args.get("path"))
    limit = max(1, min(int(args.get("max_results") or 100), 1_000))

    hits: List[str] = []
    scanned = 0
    for path in _iter_files(base, include, MAX_GREP_FILES):
        scanned += 1
        if path.stat().st_size > MAX_READ_BYTES:
            continue
        try:
            text = _read_text(path)
        except (OSError, ValueError, UnicodeError):
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(f"{_relative(context, path)}:{number}: {line.strip()[:400]}")
                if len(hits) >= limit:
                    return (
                        f"{len(hits)} match(es) for {pattern!r} (stopped at the cap; "
                        "narrow include/path):\n" + "\n".join(hits)
                    )
    if not hits:
        return f"No matches for {pattern!r} in {scanned} file(s) under {_relative(context, base)}."
    return f"{len(hits)} match(es) for {pattern!r}:\n" + "\n".join(hits)


# --------------------------------------------------------------------------- #
# run_command
# --------------------------------------------------------------------------- #

RUN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {"type": "string", "description": "Shell command to run in the workspace."},
        "cwd": {"type": "string", "description": "Working directory; defaults to the workspace root."},
        "timeout": {"type": "integer", "description": "Seconds before the command is killed (default 120)."},
    },
    "required": ["command"],
}


def run_command(args: Dict[str, Any], context: ToolContext) -> str:
    command = str(args.get("command") or "").strip()
    if not command:
        return "run_command needs a 'command'."
    cwd = context.resolve_path(args.get("cwd"))
    if not cwd.exists() or not cwd.is_dir():
        return f"Working directory does not exist: {_relative(context, cwd)}"
    timeout = max(1.0, min(float(args.get("timeout") or 120), 1_800.0))
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        return f"Command timed out after {timeout:.0f}s: {command}\n{partial[-2000:]}"
    except OSError as exc:
        return f"Could not start command: {exc}"

    cap = context.max_output_chars or 20_000
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if len(stdout) > cap:
        stdout = stdout[: cap - 800] + f"\n... [stdout truncated at {cap} chars] ...\n" + stdout[-600:]
    if len(stderr) > cap:
        stderr = stderr[: cap - 800] + f"\n... [stderr truncated at {cap} chars] ...\n" + stderr[-600:]

    parts = [f"exit_code: {completed.returncode}", f"cwd: {_relative(context, cwd)}"]
    if stdout.strip():
        parts.append("--- stdout ---\n" + stdout.rstrip())
    if stderr.strip():
        parts.append("--- stderr ---\n" + stderr.rstrip())
    if not stdout.strip() and not stderr.strip():
        parts.append("(no output)")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #

SPECS: Tuple[ToolSpec, ...] = (
    ToolSpec(
        name="read_file",
        description=(
            "Read a text file from the workspace, returned with line numbers. "
            "Use start_line/end_line for large files instead of reading everything."
        ),
        parameters=READ_SCHEMA,
        tool_class="read",
        handler=read_file,
        tags=("code", "files"),
    ),
    ToolSpec(
        name="write_file",
        description=(
            "Create a file or replace its entire contents. Prefer edit_file for "
            "changes to an existing file; a full rewrite loses unrelated edits."
        ),
        parameters=WRITE_SCHEMA,
        tool_class="write",
        handler=write_file,
        tags=("code", "files"),
    ),
    ToolSpec(
        name="edit_file",
        description=(
            "Replace an exact string in a file. old_string must match byte-for-byte, "
            "including indentation, and must be unique unless replace_all is true. "
            "Read the file first so the match is exact."
        ),
        parameters=EDIT_SCHEMA,
        tool_class="write",
        handler=edit_file,
        tags=("code", "files"),
        max_output_chars=12_000,
    ),
    ToolSpec(
        name="glob_files",
        description="Find files by glob pattern (e.g. '**/*.py'), newest first.",
        parameters=GLOB_SCHEMA,
        tool_class="read",
        handler=glob_files,
        tags=("code", "search"),
        max_output_chars=12_000,
    ),
    ToolSpec(
        name="grep_files",
        description=(
            "Search file contents with a regular expression. Returns path:line: text. "
            "Use it to locate a symbol before editing instead of reading the whole tree."
        ),
        parameters=GREP_SCHEMA,
        tool_class="read",
        handler=grep_files,
        tags=("code", "search"),
        max_output_chars=16_000,
    ),
    ToolSpec(
        name="run_command",
        description=(
            "Run a shell command in the workspace and return exit code, stdout and stderr. "
            "Use this to run tests, linters, builds and git. The output is capped."
        ),
        parameters=RUN_SCHEMA,
        tool_class="exec",
        handler=run_command,
        tags=("code", "verify"),
        max_output_chars=20_000,
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


def is_code_tool(name: str) -> bool:
    return any(spec.name == name for spec in SPECS)


def code_tool_names() -> Tuple[str, ...]:
    return tuple(spec.name for spec in SPECS)
