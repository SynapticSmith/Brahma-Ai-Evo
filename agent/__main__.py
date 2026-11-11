"""``python -m agent`` — the headless surface.

Everything the GUI can do with an agent, without starting a GUI:

    python -m agent run "fix the failing test" --cwd . --json
    python -m agent resume <session-id> "and run the suite"
    python -m agent sessions
    python -m agent tools
    python -m agent skills
    python -m agent subagents --query "code review"
    python -m agent mcp list
    python -m agent permissions init

``--json`` emits one JSON object per line (NDJSON) on stdout: every loop event
first, then a final ``{"type": "result", ...}``. That stream is the contract a
TUI, an editor plugin, or a subagent coordinator would subscribe to.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import ContextBudget
from .loop import AgentLoop
from .mcp import MCPConfigError, MCPSession, discover_config, load_config
from .permissions import PermissionPolicy, write_template
from .prompts import build_system_prompt
from .providers import PROVIDERS, ProviderUnavailable, build_completer, build_summarizer
from .runtime import AgentRuntime, configure, policy_note
from .session import AgentSession, SessionNotFound
from .spec import TOOL_CLASSES
from .term import approval_prompt, colour_enabled

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PROVIDER = 2
EXIT_USAGE = 3
EXIT_INTERRUPTED = 130


def _configure_stdio() -> None:
    """Keep model text from crashing a cp1252 Windows console.

    A retrieved note or a model answer can contain an arrow, an em dash, CJK or
    an emoji. ``sys.stdout`` defaults to the ANSI code page when redirected, so
    ``json.dumps(..., ensure_ascii=False)`` and a plain ``result.text`` write
    both raise ``UnicodeEncodeError``. UTF-8 is also the encoding the MCP
    stdio transport requires.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


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
    if kind == "retry":
        return (
            f"  ~~ retry {event.get('attempt')} in {event.get('delay_s')}s "
            f"({event.get('error')})"
        )
    if kind == "policy_reload":
        if event.get("ok"):
            return f"  ~~ policy reloaded from {event.get('source')}"
        return f"  !! policy reload failed, keeping the old rules: {event.get('error')}"
    if kind == "mcp":
        if event.get("ok"):
            return f"  ~~ mcp {event.get('name')}: {event.get('tools')} tool(s)"
        return f"  !! mcp {event.get('name')}: {event.get('error')}"
    if kind == "mcp_refresh":
        state = "restarted" if event.get("restarted") else "re-listed"
        return f"  ~~ mcp {event.get('server')} {state}: {event.get('tools')} tool(s)"
    if kind == "mcp_untrusted":
        return f"  !! mcp untrusted: {event.get('message')}"
    if kind == "instructions":
        labels = ", ".join(event.get("loaded") or [])
        suffix = " (truncated)" if event.get("truncated") else ""
        return f"  ~~ loaded instructions: {labels}{suffix}"
    if kind == "stream_fallback":
        return f"  ~~ streaming unavailable: {event.get('reason')}"
    if kind == "error":
        return f"  !! {event.get('error')}"
    if kind == "stop":
        return f"\n[stopped: {event.get('reason')} after {event.get('turns')} turn(s)]"
    return None


def _make_printer(as_json: bool):
    # Streaming writes text without a newline, so the printer owns "is a line
    # open?". Every line-shaped event closes it first, and the `assistant`
    # event that finishes a streamed turn is suppressed (its text already
    # reached the terminal one delta at a time).
    state = {"open": False, "streamed_turn": None}

    def close_line() -> None:
        if state["open"]:
            sys.stdout.write("\n")
            sys.stdout.flush()
            state["open"] = False

    def print_event(event: Dict[str, Any]) -> None:
        if as_json:
            sys.stdout.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            sys.stdout.flush()
            return

        if event.get("type") == "delta":
            kind = event.get("kind")
            if kind == "text":
                text = str(event.get("text") or "")
                if text:
                    sys.stdout.write(text)
                    sys.stdout.flush()
                    state["open"] = True
                    state["streamed_turn"] = event.get("turn")
                    print_event.streamed_text = True
            elif kind == "reset":
                close_line()
                sys.stderr.write("  ~~ partial output discarded (retrying the request)\n")
                sys.stderr.flush()
                state["streamed_turn"] = None
            # Tool-call fragments are noise on a terminal; the `assistant`
            # event that follows names the finished call.
            return

        close_line()
        if event.get("type") == "assistant" and event.get("turn") == state["streamed_turn"]:
            state["streamed_turn"] = None
            return

        line = _render_event(event)
        if line is not None:
            stream = sys.stderr if event.get("type") in (
                "permission", "compaction", "policy_reload", "error", "stream_fallback", "mcp", "mcp_untrusted"
            ) else sys.stdout
            stream.write(line + "\n")
            stream.flush()

    #: True once a text delta reached the terminal, so the caller does not
    #: print the same answer again after the run (and does when streaming fell
    #: back and only the `assistant` event carried it).
    print_event.streamed_text = False
    return print_event


# --------------------------------------------------------------------------- #
# Runtime assembly
# --------------------------------------------------------------------------- #

def _tty_approver(name: str, args: Dict[str, Any], detail: str) -> Any:
    if not sys.stdin.isatty():
        return False
    sys.stderr.write(approval_prompt(detail, name, colour=colour_enabled(sys.stderr)))
    sys.stderr.flush()
    try:
        answer = input().strip().lower()
    except EOFError:
        return False
    if answer in ("a", "always"):
        return "always"
    return answer in ("y", "yes")


def _build_policy(args: argparse.Namespace) -> PermissionPolicy:
    policy = PermissionPolicy.load(args.permissions)
    classes = dict(policy.data.get("classes") or {})

    if args.yolo:
        for name in TOOL_CLASSES:
            classes[name] = "allow"
        policy.data["classes"] = classes
        existing_roots = list((policy.data.get("paths") or {}).get("trusted_roots") or [])
        policy.data["paths"] = {
            "outside_workspace": "allow",
            "protected": "allow",
            "trusted_roots": existing_roots,
        }
        exec_cfg = dict(policy.data.get("exec") or {})
        sandbox_cfg = dict(exec_cfg.get("sandbox") or {})
        sandbox_cfg["require_confined"] = True
        exec_cfg["sandbox"] = sandbox_cfg
        policy.data["exec"] = exec_cfg

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


def _start_mcp(
    args: argparse.Namespace, workspace: Path, emit: Any
) -> tuple[Optional[MCPSession], bool]:
    """Start the configured MCP servers, if any.

    Returns ``(session, ok)``: ``ok=False`` means the config exists but is
    unusable, and the caller must stop rather than run with a silently reduced
    tool surface. "No config" is not an error.
    """
    if getattr(args, "no_mcp", False):
        return None, True
    explicit = getattr(args, "mcp_config", None)
    repo_local = not explicit and not (os.getenv("BRAHMA_MCP_CONFIG") or "").strip()
    path = Path(explicit).expanduser() if explicit else discover_config(workspace)
    if path is None:
        return None, True
    try:
        configs = load_config(path)
    except MCPConfigError as exc:
        emit({"type": "error", "error": str(exc)})
        return None, False
    if not configs:
        return None, True
    if repo_local:
        from .mcp import _mark_workspace_trusted, _workspace_is_trusted

        if not _workspace_is_trusted(workspace):
            names = [cfg.name for cfg in configs]
            count = len(names)
            noun = "server" if count == 1 else "servers"
            msg = (
                f"This workspace wants to start {count} MCP {noun} "
                f"({', '.join(names)}). Trust the workspace to load {path.name}."
            )
            headless = bool(args.headless or args.json or not sys.stdin.isatty())
            approved = False
            if not headless:
                approved = bool(
                    _tty_approver(
                        "mcp_workspace_trust",
                        {"workspace": str(workspace), "servers": names},
                        msg,
                    )
                )
                if approved:
                    _mark_workspace_trusted(workspace)
            if not approved:
                emit(
                    {
                        "type": "mcp_untrusted",
                        "workspace": str(workspace),
                        "config": str(path),
                        "count": count,
                        "servers": names,
                        "message": msg,
                    }
                )
                return None, True
    session = MCPSession()
    session.start(configs, on_event=emit)
    return session, True


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def _install_cancel(runtime: AgentRuntime):
    """Make Ctrl-C a request to stop, not an unwind through a tool call.

    First press sets the runtime's cancel event: the loop stops between calls,
    so an in-flight file write or command finishes instead of being torn in
    half. Second press raises ``KeyboardInterrupt`` so a wedged provider call
    can still be abandoned. Returns a callable that restores the old handler.
    """
    try:
        previous = signal.getsignal(signal.SIGINT)

        def handler(signum, frame):  # noqa: ANN001 - signal protocol
            if runtime.cancel_event.is_set():
                raise KeyboardInterrupt
            runtime.cancel()
            sys.stderr.write(
                "\n[cancelling after the current call; Ctrl-C again to abort]\n"
            )
            sys.stderr.flush()

        signal.signal(signal.SIGINT, handler)
    except (AttributeError, OSError, ValueError):
        # Not the main thread, or a platform without SIGINT: no handler.
        return lambda: None
    return lambda: signal.signal(signal.SIGINT, previous)


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

    mcp_session, mcp_ok = _start_mcp(args, workspace, printer)
    if not mcp_ok:
        return EXIT_USAGE

    try:
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
        from .trace import TraceCollector, TraceSink

        trace_collector = TraceCollector(
            sink=TraceSink() if getattr(args, "trace", True) else None,
            task_prompt=prompt,
            session_id=session.id if session else None,
        )

        loop = AgentLoop(
            completer,
            native_dispatch=None,
            runtime=runtime,
            system=system,
            session=session,
            max_turns=args.max_turns,
            repeat_limit=args.repeat_limit,
            max_parallel_tools=args.max_parallel_tools,
            retry_attempts=args.retries,
            budget=budget,
            on_event=printer,
            summarizer=summarizer,
            cancel=runtime.cancel_event,
            stream=args.stream,
            tracer=trace_collector,
        )
        runtime.reset_cancel()
        restore = _install_cancel(runtime)
        try:
            result = loop.run(prompt)
        finally:
            restore()
    finally:
        if mcp_session is not None:
            mcp_session.close()

    if args.json:
        sys.stdout.write(json.dumps({"type": "result", **result.to_dict()}, ensure_ascii=False) + "\n")
    else:
        if result.text and not getattr(printer, "streamed_text", False):
            sys.stdout.write("\n" + result.text + "\n")
        usage = result.usage or {}
        tokens = (
            f" · {usage.get('total_tokens', 0)} tokens"
            if usage.get("tokens_reported")
            else ""
        )
        sys.stderr.write(
            f"\n[{result.stop_reason} · {result.turns} turn(s) · "
            f"{usage.get('tool_calls', 0)} tool call(s){tokens}"
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

    mcp_session, mcp_ok = _start_mcp(args, workspace, printer)
    if not mcp_ok:
        return EXIT_USAGE

    try:
        system = build_system_prompt(workspace, permission_note=policy_note())
        loop = AgentLoop(
            completer,
            runtime=runtime,
            system=system,
            session=session,
            max_turns=args.max_turns,
            repeat_limit=args.repeat_limit,
            max_parallel_tools=args.max_parallel_tools,
            retry_attempts=args.retries,
            budget=ContextBudget(max_chars=args.max_context_chars),
            on_event=printer,
            summarizer=build_summarizer(completer) if args.summarize else None,
            cancel=runtime.cancel_event,
            stream=args.stream,
        )
        runtime.reset_cancel()
        restore = _install_cancel(runtime)
        try:
            result = loop.run(prompt)
        finally:
            restore()
    finally:
        if mcp_session is not None:
            mcp_session.close()
    if args.json:
        sys.stdout.write(json.dumps({"type": "result", **result.to_dict()}, ensure_ascii=False) + "\n")
    else:
        if result.text and not getattr(printer, "streamed_text", False):
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


def _cmd_subagents(args: argparse.Namespace) -> int:
    """Show the specialist-template library the model can address by slug."""
    from .subagent_library import LIBRARY_ENV, load

    catalog = load()
    if catalog is None:
        sys.stdout.write("Subagent template library: not installed.\n")
        sys.stdout.write(
            f"Set {LIBRARY_ENV} (or config/subagents.json) to the folder that "
            "holds catalog.json.\n"
        )
        return EXIT_OK

    rows = catalog.search(
        args.query or "",
        category=args.category or "",
        limit=args.limit,
    )
    if args.json:
        payload = {
            "root": str(catalog.root),
            "source": str(catalog.source),
            "categories": [
                {"name": name, "count": count}
                for name, count in catalog.categories()
            ],
            "templates": [row.to_dict() for row in rows],
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK

    sys.stdout.write(f"root: {catalog.root}\n")
    sys.stdout.write(f"{len(catalog.agents)} templates\n")
    if not (args.query or args.category) and args.limit is None:
        for name, count in catalog.categories():
            sys.stdout.write(f"  {name:<16} {count}\n")
        sys.stdout.write(
            "\nPass --query or --category to list slugs, or --json for the whole index.\n"
        )
        return EXIT_OK
    for row in rows:
        sys.stdout.write(f"{row.slug:<36} [{row.category}] {row.description}\n")
    if not rows:
        sys.stdout.write("No template matched that query/category.\n")
    return EXIT_OK


def _cmd_skills(args: argparse.Namespace) -> int:
    """Show what discovery found before blaming the model for ignoring a skill."""
    from .skills import discover
    from .turn_context import _load_skills_index

    workspace = Path(args.cwd or os.getcwd()).expanduser().resolve()
    catalog = discover(workspace, with_body=False)

    domain_filter = (getattr(args, "domain", None) or "").strip().lower()
    skills_index = _load_skills_index()
    domain_by_name = {s.get("name", "").lower(): s.get("domain", "general") for s in skills_index}

    skills_to_show = catalog.skills
    if domain_filter:
        skills_to_show = [s for s in catalog.skills if domain_filter in domain_by_name.get(s.name.lower(), "").lower()]

    if args.json:
        payload = catalog.to_dict()
        if domain_filter:
            payload["skills"] = [s.to_dict() for s in skills_to_show]
        sys.stdout.write(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        )
        return EXIT_OK
    sys.stdout.write(f"workspace: {workspace}\n")
    sys.stdout.write(
        "roots: "
        + (", ".join(str(root) for root in catalog.roots) or "(none)")
        + "\n"
    )
    if domain_filter:
        sys.stdout.write(f"filter (domain): {domain_filter}\n")
    if not skills_to_show:
        sys.stdout.write(
            f"No project skills found matching domain '{domain_filter}'.\n" if domain_filter else "No project skills found. Create .brahma/skills/<name>/SKILL.md.\n"
        )
    for skill in skills_to_show:
        domain = domain_by_name.get(skill.name.lower(), "general")
        sys.stdout.write(f"{skill.name:<32} [{domain}] {skill.description}\n    {skill.path}\n")
    for diagnostic in catalog.diagnostics:
        sys.stderr.write(f"warning: {diagnostic}\n")
    return EXIT_OK


def _cmd_scripts(args: argparse.Namespace) -> int:
    """Show verified scripts from config/scripts.json."""
    from .turn_context import _load_scripts_catalogue

    catalogue = _load_scripts_catalogue()
    domain_filter = (args.domain or "").strip().lower()
    if domain_filter:
        catalogue = [s for s in catalogue if domain_filter in str(s.get("domain") or "").lower()]

    if args.json:
        sys.stdout.write(json.dumps(catalogue, ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK

    if not catalogue:
        sys.stdout.write("No matching verified scripts found.\n")
        return EXIT_OK

    sys.stdout.write(f"Verified scripts ({len(catalogue)}):\n")
    for s in catalogue:
        safety = s.get("safety", "read_only")
        domain = s.get("domain", "")
        summary = s.get("summary") or s.get("description") or ""
        sys.stdout.write(f"  {s['name']:<28} [{safety}] ({domain})\n    {summary}\n")
    return EXIT_OK


def _cmd_cookbook(args: argparse.Namespace) -> int:
    """Display the OpenMonoAgent cookbook recipe for a skill."""
    from .turn_context import _load_skills_index

    skill_name = (args.name or "").strip().lower()
    index = _load_skills_index()
    matched = None
    for sk in index:
        if sk.get("name", "").lower() == skill_name:
            matched = sk
            break

    if not matched:
        sys.stderr.write(f"Error: skill '{skill_name}' not found in skills index.\n")
        return EXIT_USAGE

    if args.json:
        sys.stdout.write(json.dumps(matched, ensure_ascii=False, indent=2) + "\n")
        return EXIT_OK

    cookbook = matched.get("cookbook") or "No cookbook recipe provided."
    sys.stdout.write(f"=== COOKBOOK: {matched.get('name', skill_name)} ===\n")
    sys.stdout.write(f"Domain: {matched.get('domain', 'general')}\n")
    sys.stdout.write(f"Summary: {matched.get('description', '')}\n\n")
    sys.stdout.write(f"{cookbook}\n")
    return EXIT_OK


def _cmd_mcp(args: argparse.Namespace) -> int:
    workspace = Path(args.cwd or os.getcwd()).expanduser().resolve()
    explicit = Path(args.config).expanduser() if args.config else None
    path = explicit or discover_config(workspace)
    if path is None:
        if args.json:
            sys.stdout.write(json.dumps({"config": None, "servers": [], "tools": []}, indent=2) + "\n")
        else:
            sys.stdout.write(
                "No MCP config found. Set BRAHMA_MCP_CONFIG, create "
                f"{workspace / '.brahma' / 'mcp.json'}, or pass --config.\n"
            )
        return EXIT_OK
    try:
        configs = load_config(path)
    except MCPConfigError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return EXIT_USAGE

    session = MCPSession()
    try:
        statuses = session.start(configs)
        tools = session.tools
    finally:
        session.close()

    if args.json:
        sys.stdout.write(
            json.dumps(
                {
                    "config": str(path),
                    "servers": [status.to_dict() for status in statuses],
                    "tools": tools,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        return EXIT_OK if all(status.ok for status in statuses) else EXIT_ERROR
    if not statuses:
        sys.stdout.write(f"{path}: no enabled servers\n")
        return EXIT_OK
    for status in statuses:
        if status.ok:
            sys.stdout.write(
                f"{status.name:<20} ok    {status.tools:>3} tool(s)  {status.command}\n"
            )
        else:
            sys.stderr.write(f"{status.name:<20} FAIL  {status.error}\n")
    for name in tools:
        sys.stdout.write(f"  {name}\n")
    return EXIT_OK if all(status.ok for status in statuses) else EXIT_ERROR


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


def _cmd_sandbox(args: argparse.Namespace) -> int:
    from .sandbox import SandboxConfig, parse_sandbox, probe_confinement

    workspace = Path(args.cwd or os.getcwd()).expanduser().resolve()
    policy = PermissionPolicy.load(args.permissions)
    sandbox_raw = dict(((policy.data.get("exec") or {}).get("sandbox")) or {})
    if args.prefix:
        sandbox_raw["prefix"] = list(args.prefix)
    sandbox_cfg: SandboxConfig = parse_sandbox(sandbox_raw)
    report = probe_confinement(sandbox_cfg, workspace, timeout=args.timeout)
    if args.json:
        sys.stdout.write(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n")
    else:
        status = "CONFINED" if report.confined else "UNCONFINED"
        sys.stdout.write(f"sandbox status: {status}\n")
        sys.stdout.write(f"workspace:      {report.workspace}\n")
        sys.stdout.write(f"prefix:         {' '.join(report.prefix) if report.prefix else '(none)'}\n")
        if report.error:
            sys.stdout.write(f"error:          {report.error}\n")
        for check in report.checks:
            mark = "PASS" if check.passed else "FAIL"
            sys.stdout.write(f"  [{mark}] {check.name}: {check.detail}\n")
    return EXIT_OK if report.confined else EXIT_ERROR


def _cmd_mcp_server(args: argparse.Namespace) -> int:
    from .mcp_server import MCPServer

    workspace = Path(args.cwd or os.getcwd()).expanduser().resolve()
    if not workspace.is_dir():
        sys.stderr.write(f"error: workspace {workspace} is not a directory\n")
        return EXIT_USAGE

    args.headless = True
    args.json = False
    runtime = _configure_runtime(args, workspace)
    server = MCPServer(runtime=runtime, workspace=workspace, name=args.name or "brahma-agent")
    try:
        server.serve(sys.stdin, sys.stdout)
        return EXIT_OK
    except KeyboardInterrupt:
        return EXIT_INTERRUPTED


def _cmd_eval(args: argparse.Namespace) -> int:
    from .evals import EvalRunner, load_scenarios_from_dir
    from .trace import TraceSink

    scenarios_dir = (
        Path(args.scenarios_dir).resolve()
        if args.scenarios_dir
        else (Path.cwd() / "evals" / "scenarios")
    )
    scenarios = load_scenarios_from_dir(scenarios_dir, category=args.category)
    if not scenarios:
        sys.stderr.write(f"error: no scenarios found in {scenarios_dir}\n")
        return EXIT_USAGE

    runner = EvalRunner(
        trace_sink=TraceSink() if getattr(args, "trace", True) else None,
        default_provider=args.provider,
    )
    suite_result = runner.run_suite(scenarios, custom_provider=args.provider)

    if args.json:
        sys.stdout.write(json.dumps(suite_result.to_dict(), indent=2) + "\n")
    else:
        sys.stdout.write(suite_result.summary_text() + "\n")

    return EXIT_OK if suite_result.failed == 0 else EXIT_ERROR


def _cmd_trace(args: argparse.Namespace) -> int:
    from .trace import TraceSink

    sink = TraceSink()
    if args.trace_action == "list":
        traces = sink.list_traces(limit=args.limit)
        if args.json:
            sys.stdout.write(json.dumps([t.to_dict() for t in traces], indent=2) + "\n")
            return EXIT_OK
        if not traces:
            sys.stdout.write("no traces recorded.\n")
            return EXIT_OK
        sys.stdout.write(f"{'trace_id':<34} {'status':<10} {'turns':<7} {'prompt':<30}\n")
        for t in traces:
            turn_count = len([s for s in t.spans if s.kind == 'turn'])
            sys.stdout.write(f"{t.trace_id:<34} {t.status:<10} {turn_count:<7} {t.task_prompt[:28]:<30}\n")
        return EXIT_OK

    if args.trace_action == "show":
        trace = sink.get_trace(args.trace_id)
        if not trace:
            sys.stderr.write(f"error: trace {args.trace_id!r} not found\n")
            return EXIT_USAGE
        if args.json:
            sys.stdout.write(json.dumps(trace.to_dict(), indent=2) + "\n")
            return EXIT_OK
        sys.stdout.write(f"Trace: {trace.trace_id}\n")
        sys.stdout.write(f"Status: {trace.status} ({trace.stop_reason or '-'})\n")
        if trace.failure_label:
            sys.stdout.write(f"Failure Label: {trace.failure_label}\n")
        sys.stdout.write(f"Prompt: {trace.task_prompt}\n")
        sys.stdout.write(f"Spans ({len(trace.spans)}):\n")
        for s in trace.spans:
            dur = f"{s.duration_ms:.1f}ms" if s.duration_ms is not None else "-"
            sys.stdout.write(f"  [{s.kind}] {s.name:<25} {s.status:<8} {dur}\n")
        return EXIT_OK
    return EXIT_USAGE


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #

def _trace_default() -> bool:
    """Traces are on by default; ``BRAHMA_TRACE=0`` or ``--no-trace`` opts out."""
    return os.environ.get("BRAHMA_TRACE", "").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cwd", help="Workspace root (default: current directory)")
    parser.add_argument("--provider", default="auto", help="auto | local | custom | openrouter | stub")
    parser.add_argument("--model", help="Model id for the chosen provider")
    parser.add_argument("--max-turns", type=int, default=40, help="Hard stop for the loop (default 40)")
    parser.add_argument("--repeat-limit", type=int, default=3, help="Skip identical calls after N (default 3)")
    parser.add_argument(
        "--max-parallel-tools",
        type=int,
        default=4,
        help="Run up to N read-only calls at once (default 4; 1 disables)",
    )
    parser.add_argument("--retries", type=int, default=3, help="Attempts per model call on transient errors (default 3)")
    parser.add_argument("--max-context-chars", type=int, default=120_000, help="Compaction threshold")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-command tool timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Emit NDJSON events on stdout")
    parser.add_argument(
        "--stream",
        action="store_true",
        help="Render model text as it arrives (falls back when unsupported)",
    )
    parser.add_argument("--headless", action="store_true", help="Never prompt for approval")
    parser.add_argument("--yolo", action="store_true", help="Allow every tool class (use only in a sandbox)")
    parser.add_argument("--allow", action="append", metavar="CLASS", help="Allow a tool class, repeatable/comma-separated")
    parser.add_argument("--deny", action="append", metavar="CLASS", help="Deny a tool class")
    parser.add_argument("--permissions", help="Path to agent_permissions.json")
    parser.add_argument(
        "--mcp-config",
        help="MCP server config JSON (default: BRAHMA_MCP_CONFIG, .brahma/mcp.json, .mcp.json)",
    )
    parser.add_argument("--no-mcp", action="store_true", help="Do not start configured MCP servers")
    parser.add_argument("--no-session", action="store_true", help="Do not persist this run")
    parser.add_argument(
        "--trace",
        action=argparse.BooleanOptionalAction,
        default=_trace_default(),
        help="Record an execution trace to the local trace log (default: on; --no-trace opts out)",
    )
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

    skills = sub.add_parser("skills", help="List discovered project skills")
    skills.add_argument("--cwd", help="Workspace root used for discovery")
    skills.add_argument("--domain", help="Filter by taxonomy domain (e.g. cognitive, devops, coding)")
    skills.add_argument("--json", action="store_true")

    scripts = sub.add_parser("scripts", help="List verified repository scripts")
    scripts.add_argument("--domain", help="Filter by taxonomy domain")
    scripts.add_argument("--json", action="store_true")

    cookbook = sub.add_parser("cookbook", help="Show executable cookbook recipe for a skill")
    cookbook.add_argument("name", help="Skill name (e.g. inversion-thinking, api-mock-engine)")
    cookbook.add_argument("--json", action="store_true")

    subagents = sub.add_parser(
        "subagents", help="List specialist subagent templates"
    )
    subagents.add_argument("--query", help="Keyword(s) to match")
    subagents.add_argument("--category", help="Filter by category")
    subagents.add_argument("--limit", type=int, help="Max rows (default: all)")
    subagents.add_argument("--json", action="store_true")

    mcp = sub.add_parser("mcp", help="List configured MCP servers and their tools")
    mcp.add_argument("action", nargs="?", choices=("list",), default="list")
    mcp.add_argument("--config", help="MCP config file (default: discovery)")
    mcp.add_argument("--cwd", help="Workspace root used for discovery")
    mcp.add_argument("--json", action="store_true")

    perms = sub.add_parser("permissions", help="Show or create the policy")
    perms.add_argument("action", nargs="?", choices=("show", "init"), default="show")
    perms.add_argument("--path", help="Policy file to read/write")
    perms.add_argument(
        "--force", action="store_true", help="Overwrite an existing policy file"
    )
    # `show` already prints JSON; accepted so scripts can pass it uniformly.
    perms.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    sandbox = sub.add_parser("sandbox", help="Verify exec.sandbox confinement")
    sandbox.add_argument("action", nargs="?", choices=("doctor",), default="doctor")
    sandbox.add_argument("--cwd", help="Workspace root used for probe mounts")
    sandbox.add_argument("--permissions", help="Policy file to read exec.sandbox from")
    sandbox.add_argument(
        "--prefix",
        nargs="+",
        help="Override exec.sandbox.prefix for this probe without writing policy",
    )
    sandbox.add_argument("--timeout", type=float, default=15.0, help="Per-probe timeout in seconds")
    sandbox.add_argument("--json", action="store_true", help="Emit JSON report")

    mcp_server = sub.add_parser("mcp-server", help="Run outbound Model Context Protocol stdio server")
    mcp_server.add_argument("--cwd", help="Workspace root (default: current directory)")
    mcp_server.add_argument("--permissions", help="Path to agent_permissions.json")
    mcp_server.add_argument(
        "--yolo", action="store_true", help="Allow every tool class (use only in a sandbox)"
    )
    mcp_server.add_argument(
        "--allow", action="append", metavar="CLASS", help="Allow a tool class, repeatable/comma-separated"
    )
    mcp_server.add_argument(
        "--deny", action="append", metavar="CLASS", help="Deny a tool class"
    )
    mcp_server.add_argument(
        "--name", default=None, help="MCP server name (default: brahma-agent)"
    )
    mcp_server.add_argument("--timeout", type=float, default=120.0, help="Per-command tool timeout in seconds")

    eval_parser = sub.add_parser("eval", help="Run scenario evaluation suite and scorecard")
    eval_parser.add_argument("--scenarios-dir", default=None, help="Directory containing scenario JSON files")
    eval_parser.add_argument(
        "--category",
        choices=("tool_choice", "multi_step", "recovery", "safety", "memory"),
        default=None,
        help="Filter scenarios by category",
    )
    eval_parser.add_argument("--provider", default="stub", choices=PROVIDERS, help="Provider for evaluation")
    eval_parser.add_argument("--json", action="store_true", help="Emit JSON scorecard")
    eval_parser.add_argument(
        "--trace",
        action=argparse.BooleanOptionalAction,
        default=_trace_default(),
        help="Record evaluation traces to the local trace log (default: on; --no-trace opts out)",
    )

    trace_parser = sub.add_parser("trace", help="List or inspect execution traces")
    trace_sub = trace_parser.add_subparsers(dest="trace_action", required=True)
    trace_list = trace_sub.add_parser("list", help="List recent execution traces")
    trace_list.add_argument("--limit", type=int, default=20)
    trace_list.add_argument("--json", action="store_true")
    trace_show = trace_sub.add_parser("show", help="Show details of a specific trace")
    trace_show.add_argument("trace_id", help="Trace ID to inspect")
    trace_show.add_argument("--json", action="store_true")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    _configure_stdio()
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)

    # `python -m agent "do the thing"` -> treat the first bare word as a run.
    known = {
        "run", "resume", "sessions", "tools", "skills", "scripts", "cookbook", "subagents", "mcp", "mcp-server", "permissions", "sandbox", "eval", "trace",
        "-h", "--help",
    }
    if argv and argv[0] not in known:
        argv = ["run"] + argv

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return EXIT_USAGE

    try:
        if args.command == "run":
            return _cmd_run(args)
        if args.command == "resume":
            return _cmd_resume(args)
        if args.command == "sessions":
            return _cmd_sessions(args)
        if args.command == "tools":
            return _cmd_tools(args)
        if args.command == "skills":
            return _cmd_skills(args)
        if args.command == "scripts":
            return _cmd_scripts(args)
        if args.command == "cookbook":
            return _cmd_cookbook(args)
        if args.command == "subagents":
            return _cmd_subagents(args)
        if args.command == "mcp":
            return _cmd_mcp(args)
        if args.command == "permissions":
            return _cmd_permissions(args)
        if args.command == "sandbox":
            return _cmd_sandbox(args)
        if args.command == "mcp-server":
            return _cmd_mcp_server(args)
        if args.command == "eval":
            return _cmd_eval(args)
        if args.command == "trace":
            return _cmd_trace(args)
    except KeyboardInterrupt:
        sys.stderr.write("\n[interrupted]\n")
        return EXIT_INTERRUPTED
    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
