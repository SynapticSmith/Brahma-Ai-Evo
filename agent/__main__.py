"""``python -m agent`` — the headless surface.

Everything the GUI can do with an agent, without starting a GUI:

    python -m agent run "fix the failing test" --cwd . --json
    python -m agent resume <session-id> "and run the suite"
    python -m agent sessions
    python -m agent tools
    python -m agent permissions init

``--json`` emits one JSON object per line (NDJSON) on stdout: every loop event
first, then a final ``{"type": "result", ...}``. That stream is the contract a
TUI, an editor plugin, or a subagent coordinator would subscribe to.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import ContextBudget
from .loop import AgentLoop
from .permissions import PermissionPolicy, write_template
from .prompts import build_system_prompt
from .providers import ProviderUnavailable, build_completer, build_summarizer
from .runtime import AgentRuntime, configure, policy_note
from .session import AgentSession, SessionNotFound

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PROVIDER = 2
EXIT_USAGE = 3


# --------------------------------------------------------------------------- #
# Human-readable event rendering
# --------------------------------------------------------------------------- #

def _render_event(event: Dict[str, Any]) -> Optional[str]:
    kind = event.get("type")
    if kind == "turn_start":
        return f"\n[turn {event.get('turn')}]"
    if kind == "assistant":
        text = (event.get("text") or "").strip()
        return text or None
    if kind == "tool_call":
        args = json.dumps(event.get("arguments") or {}, ensure_ascii=False)
        if len(args) > 160:
            args = args[:157] + "..."
        return f"  -> {event.get('name')}({args})"
    if kind == "tool_result":
        preview = str(event.get("preview") or "").strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:197] + "..."
        return f"  <- {'ok' if event.get('ok') else 'error'} ({event.get('chars')} chars) {preview}"
    if kind == "permission":
        return f"  !! permission {event.get('decision')}: {event.get('name')} ({event.get('rule')})"
    if kind == "compaction":
        return f"  ~~ compacted {event.get('dropped')} message(s), {event.get('summary_chars')} char summary"
    if kind == "error":
        return f"  !! {event.get('error')}"
    if kind == "stop":
        return f"\n[stopped: {event.get('reason')} after {event.get('turns')} turn(s)]"
    return None


def _make_printer(as_json: bool):
    def print_event(event: Dict[str, Any]) -> None:
        if as_json:
            sys.stdout.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()
            return
        line = _render_event(event)
        if line is not None:
            stream = sys.stderr if event.get("type") in ("permission", "compaction", "error") else sys.stdout
            stream.write(line + "\n")
            stream.flush()

    return print_event


# --------------------------------------------------------------------------- #
# Runtime assembly
# --------------------------------------------------------------------------- #

def _tty_approver(name: str, args: Dict[str, Any], detail: str) -> bool:
    if not sys.stdin.isatty():
        return False
    sys.stderr.write(f"\n[approval] {detail}\nAllow '{name}'? [y/N] ")
    sys.stderr.flush()
    try:
        answer = input().strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _build_policy(args: argparse.Namespace) -> PermissionPolicy:
    policy = PermissionPolicy.load(args.permissions)
    classes = dict(policy.data.get("classes") or {})

    if args.yolo:
        for name in ("read", "write", "exec", "network", "device", "native"):
            classes[name] = "allow"
        policy.data["classes"] = classes
        policy.data["paths"] = {"outside_workspace": "allow", "protected": "allow"}

    for name in (args.allow or []):
        for item in name.split(","):
            if item.strip():
                classes[item.strip()] = "allow"
    for name in (args.deny or []):
        for item in name.split(","):
            if item.strip():
                classes[item.strip()] = "deny"
    policy.data["classes"] = classes
    return policy


def _configure_runtime(args: argparse.Namespace, workspace: Path) -> AgentRuntime:
    policy = _build_policy(args)
    headless = bool(args.headless or args.json or not sys.stdin.isatty())
    if args.yolo:
        headless = True
    approver = None if headless else _tty_approver
    return configure(
        workspace=workspace,
        policy=policy,
        approver=approver,
        headless=headless,
        reset_approver=headless,
        timeout=args.timeout,
    )


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _cmd_run(args: argparse.Namespace) -> int:
    workspace = Path(args.cwd or os.getcwd()).expanduser().resolve()
    if not workspace.is_dir():
        sys.stderr.write(f"error: workspace {workspace} is not a directory\n")
        return EXIT_USAGE
    prompt = " ".join(args.prompt).strip()
    if not prompt and not sys.stdin.isatty():
        prompt = sys.stdin.read().strip()
    if not prompt:
        sys.stderr.write("error: no prompt (pass text or pipe it on stdin)\n")
        return EXIT_USAGE

    runtime = _configure_runtime(args, workspace)
    printer = _make_printer(args.json)
    runtime.event_sink = printer

    try:
        completer = build_completer(args.provider, args.model)
    except ProviderUnavailable as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_PROVIDER

    budget = ContextBudget(max_chars=args.max_context_chars)
    summarizer = build_summarizer(completer) if args.summarize else None

    session = None
    if not args.no_session:
        session = AgentSession.create(
            workspace,
            title=args.title or prompt[:60],
            model=args.model or "",
        )

    system = build_system_prompt(workspace, permission_note=policy_note())
    loop = AgentLoop(
        completer,
        native_dispatch=None,
        runtime=runtime,
        system=system,
        session=session,
        max_turns=args.max_turns,
        repeat_limit=args.repeat_limit,
        budget=budget,
        on_event=printer,
        summarizer=summarizer,
    )
    result = loop.run(prompt)

    if args.json:
        sys.stdout.write(json.dumps({"type": "result", **result.to_dict()}, ensure_ascii=False) + "\n")
    else:
        if result.text:
            sys.stdout.write("\n" + result.text + "\n")
        sys.stderr.write(
            f"\n[{result.stop_reason} · {result.turns} turn(s) · "
            f"{result.usage.get('tool_calls', 0)} tool call(s)"
            + (f" · session {result.session_id}" if result.session_id else "")
            + "]\n"
        )
    return EXIT_OK if result.ok else EXIT_ERROR


def _cmd_resume(args: argparse.Namespace) -> int:
    try:
        session = AgentSession.open(args.session_id)
    except SessionNotFound as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_USAGE

    workspace = Path(args.cwd or session.cwd or os.getcwd()).expanduser().resolve()
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        sys.stderr.write("error: resume needs a prompt\n")
        return EXIT_USAGE

    runtime = _configure_runtime(args, workspace)
    printer = _make_printer(args.json)
    runtime.event_sink = printer
    try:
        completer = build_completer(args.provider, args.model or session.model or None)
    except ProviderUnavailable as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_PROVIDER

    system = build_system_prompt(workspace, permission_note=policy_note())
    loop = AgentLoop(
        completer,
        runtime=runtime,
        system=system,
        session=session,
        max_turns=args.max_turns,
        repeat_limit=args.repeat_limit,
        budget=ContextBudget(max_chars=args.max_context_chars),
        on_event=printer,
        summarizer=build_summarizer(completer) if args.summarize else None,
    )
    result = loop.run(prompt)
    if args.json:
        sys.stdout.write(json.dumps({"type": "result", **result.to_dict()}, ensure_ascii=False) + "\n")
    else:
        if result.text:
            sys.stdout.write("\n" + result.text + "\n")
    return EXIT_OK if result.ok else EXIT_ERROR


def _cmd_sessions(args: argparse.Namespace) -> int:
    records = AgentSession.recent(args.limit)
    if args.json:
        sys.stdout.write(json.dumps([r.to_dict() for r in records], ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK
    if not records:
        sys.stdout.write("No agent sessions yet.\n")
        return EXIT_OK
    for record in records:
        sys.stdout.write(
            f"{record.id}  {record.status:<12} {record.turn_count:>3} turns  "
            f"{record.title[:60]}\n    {record.cwd}\n"
        )
    return EXIT_OK


def _cmd_tools(args: argparse.Namespace) -> int:
    from .spec import all_specs

    specs = all_specs()
    if args.json:
        sys.stdout.write(json.dumps([s.to_dict() for s in specs], ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK
    for spec in specs:
        sys.stdout.write(f"{spec.name:<14} [{spec.tool_class}] {spec.description.splitlines()[0][:100]}\n")
    return EXIT_OK


def _cmd_permissions(args: argparse.Namespace) -> int:
    if args.action == "init":
        target = Path(args.path or "agent_permissions.json")
        try:
            write_template(target, overwrite=args.force)
        except FileExistsError:
            sys.stderr.write(
                f"{target} already exists; pass --force to overwrite it.\n"
            )
            return EXIT_USAGE
        sys.stdout.write(f"Wrote {target}\n")
        return EXIT_OK
    policy = PermissionPolicy.load(args.path)
    sys.stdout.write(json.dumps(policy.describe(), indent=2) + "\n")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cwd", help="Workspace root (default: current directory)")
    parser.add_argument("--provider", default="auto", help="auto | local | custom | openrouter | stub")
    parser.add_argument("--model", help="Model id for the chosen provider")
    parser.add_argument("--max-turns", type=int, default=40, help="Hard stop for the loop (default 40)")
    parser.add_argument("--repeat-limit", type=int, default=3, help="Skip identical calls after N (default 3)")
    parser.add_argument("--max-context-chars", type=int, default=120_000, help="Compaction threshold")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-command tool timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Emit NDJSON events on stdout")
    parser.add_argument("--headless", action="store_true", help="Never prompt for approval")
    parser.add_argument("--yolo", action="store_true", help="Allow every tool class (use only in a sandbox)")
    parser.add_argument("--allow", action="append", metavar="CLASS", help="Allow a tool class, repeatable/comma-separated")
    parser.add_argument("--deny", action="append", metavar="CLASS", help="Deny a tool class")
    parser.add_argument("--permissions", help="Path to agent_permissions.json")
    parser.add_argument("--no-session", action="store_true", help="Do not persist this run")
    parser.add_argument("--summarize", action="store_true", help="Use the model for compaction summaries")
    parser.add_argument("--title", help="Session title")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent",
        description="Brahma agent harness (headless).",
    )
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="Run one agent task")
    run.add_argument("prompt", nargs="*", help="Task text (or pipe it on stdin)")
    _add_common(run)

    resume = sub.add_parser("resume", help="Continue a stored session")
    resume.add_argument("session_id")
    resume.add_argument("prompt", nargs="*")
    _add_common(resume)

    sessions = sub.add_parser("sessions", help="List stored sessions")
    sessions.add_argument("--limit", type=int, default=20)
    sessions.add_argument("--json", action="store_true")

    tools = sub.add_parser("tools", help="List registered harness tools")
    tools.add_argument("--json", action="store_true")

    perms = sub.add_parser("permissions", help="Show or create the policy")
    perms.add_argument("action", nargs="?", choices=("show", "init"), default="show")
    perms.add_argument("--path", help="Policy file to read/write")
    perms.add_argument(
        "--force", action="store_true", help="Overwrite an existing policy file"
    )
    # `show` already prints JSON; accepted so scripts can pass it uniformly.
    perms.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)

    # `python -m agent "do the thing"` -> treat the first bare word as a run.
    known = {"run", "resume", "sessions", "tools", "permissions", "-h", "--help"}
    if argv and argv[0] not in known:
        argv = ["run"] + argv

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    if args.command == "run":
        return _cmd_run(args)
    if args.command == "resume":
        return _cmd_resume(args)
    if args.command == "sessions":
        return _cmd_sessions(args)
    if args.command == "tools":
        return _cmd_tools(args)
    if args.command == "permissions":
        return _cmd_permissions(args)
    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
