"""Human-readable approval detail for the HUD banner and the CLI prompt.

The banner used to show a tool name and a raw JSON blob. For ``run_command``
that hides the only two things a person needs before saying yes: the exact
command and the directory it runs in. For ``edit_file`` it hides the change
itself. This module builds a short, bounded description instead.

It only *describes*: nothing here executes a tool or mutates a file. The
current contents are read (through the same helpers the tools use) so an edit
can be shown as a real unified diff; a read failure simply degrades to the raw
arguments.

The returned object is a ``str`` subclass so every existing approver — a
lambda, the TTY prompt, a test double — keeps working unchanged. The extra
attributes carry the banner heading and what "ALWAYS ALLOW" would remember.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional

from .code_tools import _diff, _read_text
from .permissions import PolicyResult
from .spec import ToolContext, ToolSpec

#: Arguments shown for a tool without a bespoke preview.
MAX_ARG_CHARS = 700
#: Diff text shown in a banner. The overlay scrolls, but a 20 000-char diff is
#: not an approval prompt, it is a wall.
MAX_DIFF_CHARS = 1_600
#: A command longer than this is almost certainly generated; truncate it.
MAX_COMMAND_CHARS = 500

#: Policy rules where the *path* is the exception being approved. A session
#: approval for one outside file must not become "read anything outside".
PATH_RULES = ("paths.outside_workspace", "paths.protected")

PATH_KEYS = ("path", "file", "target", "cwd", "dir", "directory", "root", "source", "destination")


class ApprovalDetail(str):
    """Approval text plus the metadata the banner needs to stay honest."""

    title: str
    remember: str

    def __new__(cls, text: str, *, title: str = "", remember: str = ""):
        obj = super().__new__(cls, text)
        obj.title = title
        obj.remember = remember
        return obj


def _short(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clip_arguments(args: Mapping[str, Any]) -> str:
    try:
        rendered = json.dumps(args, ensure_ascii=False, default=str, indent=2)
    except (TypeError, ValueError):
        rendered = str(dict(args))
    return _short("arguments:\n" + rendered, MAX_ARG_CHARS)


def _replace_preview(old: Any, new: Any) -> str:
    if not isinstance(old, str) or not isinstance(new, str):
        return "replace: (old_string/new_string missing)"
    removed = old.splitlines()[:20]
    added = new.splitlines()[:20]
    lines = [f"- {line}" for line in removed] + [f"+ {line}" for line in added]
    if len(old.splitlines()) > 20 or len(new.splitlines()) > 20:
        lines.append("... [preview truncated]")
    return "--- before ---\n" + "\n".join(lines)


def _workspace_context(workspace: Optional[Path]) -> ToolContext:
    root = Path(workspace) if workspace is not None else Path.cwd()
    try:
        root = root.expanduser().resolve()
    except OSError:
        root = Path(workspace or ".")
    return ToolContext(workspace=root)


def _display(context: ToolContext, raw: Any) -> str:
    try:
        return context.display_path(context.resolve_path(raw))
    except (OSError, ValueError):
        return str(raw)


def _display_diff(before: str, after: str, name: str) -> str:
    """Unified diff with banner-friendly line endings."""
    return _diff(before, after, name, limit=MAX_DIFF_CHARS).replace("\r\n", "\n").replace("\r", "\n")


def _path_argument(args: Mapping[str, Any]) -> Any:
    for key in PATH_KEYS:
        if args.get(key):
            return args[key]
    return None


def _remember(name: str, args: Mapping[str, Any], spec: Optional[ToolSpec], rule: str, context: ToolContext) -> str:
    tool_class = spec.tool_class if spec else "native"
    if tool_class == "exec":
        command = str(args.get("command") or args.get("cmd") or "").strip()
        return f"this exact command ({_short(command, 120)})"
    if rule in PATH_RULES:
        return f"this path ({_display(context, _path_argument(args))})"
    return f"every {name} call in this session"


def describe_call(
    name: str,
    args: Optional[Mapping[str, Any]],
    spec: Optional[ToolSpec],
    decision: PolicyResult,
    workspace: Optional[Path],
) -> ApprovalDetail:
    """Build the banner text for one gated call.

    Ordering is deliberate: the matched rule and its reason come first so a
    person can see *why* they are being asked, then the concrete subject (the
    command, cwd, path, diff). Raw arguments are appended only as a fallback.
    """
    args = dict(args or {})
    context = _workspace_context(workspace)
    rule = decision.rule or "unknown"
    reason = decision.reason or "no reason recorded"
    head = [f"rule: {rule} — {reason}"]
    body = []

    if name == "run_command":
        command = str(args.get("command") or args.get("cmd") or "").strip()
        cwd = _display(context, args.get("cwd"))
        body.append(f"command: {_short(command, MAX_COMMAND_CHARS)}")
        body.append(f"cwd: {cwd}")
        if args.get("timeout") is not None:
            body.append(f"timeout: {int(float(args['timeout']))}s")
        title = "Run this command?"
        return ApprovalDetail(
            "\n".join(head + [""] + body),
            title=title,
            remember=_remember(name, args, spec, rule, context),
        )
    elif name in ("read_file", "glob_files", "grep_files"):
        body.append(f"path: {_display(context, _path_argument(args))}")
        if name == "glob_files":
            body.append(f"pattern: {args.get('pattern') or '*'}")
        elif name == "grep_files":
            body.append(f"pattern: {args.get('pattern') or ''}")
            if args.get("include"):
                body.append(f"include: {args['include']}")
        else:
            start = args.get("start_line")
            end = args.get("end_line")
            if start or end:
                body.append(f"lines: {start or 1}-{end or 'end'}")
        title = "Read this?" if name == "read_file" else "Search these files?"
        return ApprovalDetail(
            "\n".join(head + [""] + body),
            title=title,
            remember=_remember(name, args, spec, rule, context),
        )
    elif name in ("write_file", "edit_file"):
        path = context.resolve_path(args.get("path"))
        body.append(f"file: {context.display_path(path)}")
        exists = False
        before: Optional[str] = None
        try:
            exists = path.is_file()
            if exists:
                before = _read_text(path)
        except (OSError, ValueError):
            exists = False

        if name == "write_file":
            content = args.get("content")
            content = content if isinstance(content, str) else ""
            body.append(f"write: {len(content)} chars, {content.count(chr(10)) + 1} lines")
            if before is not None:
                body.append("overwrites the existing file")
                body.append(_display_diff(before, content, path.name))
            else:
                body.append("creates a new file")
                preview = "\n".join(f"+ {line}" for line in content.splitlines()[:20])
                body.append("--- preview ---\n" + (preview or "(empty file)"))
        else:
            old = args.get("old_string")
            new = args.get("new_string")
            if isinstance(old, str) and isinstance(new, str) and before is not None:
                count = before.count(old)
                replace_all = bool(args.get("replace_all"))
                body.append(
                    f"replace: {count} occurrence(s)" + (" (all)" if replace_all else "")
                )
                after = before.replace(old, new) if replace_all else before.replace(old, new, 1)
                if count and before != after:
                    body.append(_display_diff(before, after, path.name))
                else:
                    body.append(_replace_preview(old, new))
            else:
                body.append("replace: old_string → new_string")
                body.append(_replace_preview(old, new))

        return ApprovalDetail(
            "\n".join(head + [""] + body),
            title="Edit this file?" if name == "edit_file" else "Write this file?",
            remember=_remember(name, args, spec, rule, context),
        )

    if args:
        body.append(_clip_arguments(args))
    text = "\n".join(head + ([""] + body if body else []))
    return ApprovalDetail(
        text,
        title=f"Approve {name}?",
        remember=_remember(name, args, spec, rule, context),
    )


def path_scoped_approval_key(
    name: str,
    args: Mapping[str, Any],
    rule: str,
    workspace: Optional[Path],
) -> Optional[str]:
    """Key for a path-based exception, or ``None`` when the rule is not one.

    ``Ask once, remember forever`` is only safe when "forever" means *this
    file*. For a class-level ask the policy is asking about the tool itself, so
    :class:`~agent.runtime.AgentRuntime` keeps the broader tool-name key.
    """
    if rule not in PATH_RULES:
        return None
    context = _workspace_context(workspace)
    try:
        resolved = context.resolve_path(_path_argument(args))
        return os.path.normcase(str(resolved))
    except (OSError, ValueError):
        return None
