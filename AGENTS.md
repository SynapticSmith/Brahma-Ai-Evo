# AGENTS.md

Working notes for coding agents in this repository. The Brahma agent harness
reads this file automatically (`agent/prompts.py`); other tools do too.

## What this is

A Windows-first PyQt6 desktop assistant. `main.py` is the entry point and owns
`BrahmaLive` (Gemini Live audio, plus the local/custom/OpenRouter text paths).
Tool implementations live in `actions/`; cross-cutting seams live in `core/`.

`docs/ARCHITECTURE-SPEC.md` is the system map: document authority, invariants,
implemented-vs-designed status, the security workstream, and the ranked work
queues. Read it before starting a non-trivial batch.

## Environment

- **Use Python 3.11 for every gate**: `py -3.11 -m ruff …`, `py -3.11 -m pytest …`.
  The repo `.venv` and the default `python` are 3.14 and do not have the dev
  tools installed.
- Shell is PowerShell; do not chain commands with `&&`.
- The stack is Windows-only (PyQt6, pywin32, comtypes, SAPI).

## Verify before claiming done

```powershell
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
```

The full suite takes about 95 seconds and needs the offscreen Qt platform.
For agent work, the fast subset is:

```powershell
py -3.11 -m pytest tests -q -k agent
py -3.11 -m agent run "say hello" --provider stub --json --no-session
```

The harness is stdlib-only and must stay that way: CI collects only
`tests/test_agent_*.py` on 3.11, 3.12 and 3.13 with just pytest installed. Do
not import PyQt, a provider SDK, or a Windows COM module from `agent/` at module
scope.

## Cancelling a run

- CLI: Ctrl-C requests a stop after the current call; a second Ctrl-C aborts
  (exit `130`).
- GUI: `Ctrl+Shift+X` sets `agent.runtime.cancel()`.
- Code: pass `cancel=runtime.cancel_event` to `AgentLoop`; a new turn calls
  `reset_cancel()`. Never kill the worker thread — writes and child processes
  must finish.

## Instructions

`AGENTS.md` (also `BRAHMA.md`, `.brahma/AGENTS.md`) is read from the workspace
and up to three parent directories, plus any subdirectory the run actually
touches through a tool argument. Keep package-specific rules next to the package
they govern; `agent/prompts.py` owns discovery and its budgets.

## Approvals

- The HUD's ALWAYS ALLOW button and the CLI's `a` answer remember an approval
  in-process only (`AgentRuntime.session_allow`): tool name for a class-level
  ask, resolved path for a `paths.outside_workspace`/`paths.protected` ask, and
  exact command for `run_command`. They never override a policy `deny`, never
  apply to `requires_approval` specs, and never touch disk.
- To make an approval permanent, edit the policy file; do not persist a clicked
  button. `AgentRuntime.execute` re-reads that file at most once a second
  (`policy_check_interval`), so an edit needs no restart; a malformed edit keeps
  the last good policy and emits `policy_reload` with `ok=False`.

## Commands

- `run_command` goes through `agent.process.run_captured`. Do not add a second
  `subprocess.run(shell=True, timeout=...)` call for a tool: that kills only the
  shell and leaks grandchildren.
- `run_captured` closes `stdin` (no prompt can block an agent call) and, on
  timeout, terminates the whole tree — a Windows job object, or a POSIX session
  group, with `taskkill /F /T` and `Popen.kill()` as fallbacks. Normal
  completion does not kill background survivors.
- Containment bounds lifetime, not access: there is still no filesystem or
  network sandbox, so the permission policy remains the gate. Keep timeout
  clamping (1 s to 1800 s) at the tool.
- Isolation is a single path: `agent/sandbox.py` reads `exec.sandbox` and
  produces the env and argv `run_captured` uses. Do not add a second scrub,
  prefix or limit path in a tool. `env_scrub` is policy-controlled and defaults
  to true, so a command does not see `OPENAI_API_KEY` unless `env_keep` names
  it; report a withheld count rather than failing silently. `prefix` is trusted
  configuration, not a verified sandbox, and POSIX cannot enforce
  `max_processes` — report unapplied limits instead of pretending.

## MCP servers

- `agent/mcp.py` is the only MCP client in the harness. It is stdlib only and
  speaks newline-delimited JSON-RPC over stdio (`initialize`, `tools/list`,
  `tools/call`) because `agent/` must import with only pytest installed. Do not
  reuse `core/mcp_manager.py` (SDK + asyncio) or spawn a server from a tool.
- Config is trusted input: servers are arbitrary programs the user named. Add a
  field only with validation in `parse_servers`; `env` merges over the inherited
  environment and a relative `cwd` resolves against the config file.
- Every tool registers as an ordinary `ToolSpec` (`tool_class="mcp"`, name
  `mcp_<server>_<tool>` sanitised to `[A-Za-z0-9_]`), so calls go through
  `AgentRuntime.execute` like any other tool. `MCPSession.close()` must
  unregister the specs before killing the process.
- The GUI uses one `WorkspaceMCP` (`_harness_mcp_manager()` in `main.py`), not
  a session per turn: it is keyed by workspace + config identity and closed in
  `main()`'s `finally`. Merge its tools with `with_mcp_tools` so the first model
  call advertises them; the loop's `all_specs()` merge only covers follow-ups.
- A server that latched `tools/list_changed`, lost its transport (`closed`) or
  whose process is gone is refreshed at the turn boundary: `WorkspaceMCP.ensure`
  calls `MCPSession.refresh`, which re-lists that server only and restarts a
  dead one once. No background watcher and no per-turn retry loop — a failed
  re-list drops that server's tools and reports the stderr tail once. Check
  `closed` as well as `alive`: the reader thread sees EOF before `poll()` does.

## Sessions

- The CLI (`run`/`resume`/`sessions`) and the GUI text path share
  `agent/session.py`. The GUI creates a row on its first agentic turn against
  the runtime workspace and seeds later turns as
  `[fresh system, stored history, new user]` via `agent/session_bridge.py`.
  A completed Gemini Live turn is archived into that same row as a cleaned
  user/assistant pair (`record_voice_turn`), so voice conversations are listed
  and replayed too; the live socket itself is never re-seeded from disk.
- HUD commands: `/sessions` lists recent rows, `/resume` opens a picker,
  `/resume <id>` binds a row (unique prefix accepted), `/new` starts fresh.
  The tray menu's **Resume Agent Session…** submits `/resume`.
- The system prompt is never persisted; only the transcript and tail-added
  messages are. A repersisted replay would duplicate the earlier turns, so use
  `messages_after(history, seeded)` rather than appending the whole seed.

## Parallel tool calls

- Dispatch is decided per **call**, not per turn or segment. A policy-allowed,
  non-repeated read or write becomes a `(kind, path)` effect: unrelated
  reads/writes overlap, writes never overlap each other, and a read/write pair
  whose paths are equal or nested keeps model order. `exec`, a native call, an
  approval prompt, a denied call, a write with no path, and a repeated call are
  fences (earlier calls finish, the fence runs alone, then dispatch resumes).
  A repeated call is skipped without a worker.
- Results are appended in submission order. A cancelled turn still writes one
  `tool` message per `tool_call` (`CANCELLED_TOOL_OUTPUT`). Keep both rules.

## Subagents

- `run_subagent` is a `native` fence handled in `agent/subagent.py`. The child
  loop is built by `AgentLoop._run_subagent`; the runner reaches the handler
  through `ToolContext.extra["subagent_runner"]` injected by `_run_one`. Never
  add a second path that constructs a child.
- A child shares the parent's runtime (policy, approvals, workspace, cancel
  event) and completer, gets a filtered tool surface that never contains
  `run_subagent`, a fresh context, no session row, and a turn budget clamped to
  12. Its transcript is discarded; only the report crosses back, so a child
  exception becomes tool text and must not abort the parent.
- The voice surface deliberately omits `run_subagent`: the Gemini live session
  has no text completer to build a child loop. Text and CLI advertise it.

## Layout

| Path | Contents |
| --- | --- |
| `main.py` | App entry, `BrahmaLive`, `TOOL_DECLARATIONS`, provider routing. |
| `core/` | Seams: `tool_exec.py` (dispatch), `tool_index.py` (advertised surface), `confirm.py` (approval banner), `undo.py`, `user_paths.py`. |
| `actions/` | One module per capability. |
| `agent/` | The agentic harness: loop, code tools, subagents, permissions, context, sessions. Docs in `docs/AGENTIC-HARNESS.md`. |
| `tests/` | Pytest suite; no display or model required for the portable tests. |
| `docs/` | Design records and build logs. |

## Rules

1. New harness tool ⇒ a `ToolSpec` (canonical lowercase schema) and tests. Do
   **not** add an `elif` branch to `core/tool_exec.py` for it.
2. Never bypass `AgentRuntime.execute` — it is the single permission boundary
   for GUI, voice and CLI.
3. Keep the OpenAI tool protocol in any transcript: one `tool` message per
   `tool_calls` entry, in order, immediately after the assistant message.
4. Leave `agent/__init__.py` empty; `python -m agent` must not import PyQt.
5. `agent_permissions.json` is user data. Do not commit it — only
   `config/agent_permissions.example.json` belongs in the repo.
6. CLI colour goes through `agent/term.py`; never write raw ANSI in
   `agent/__main__.py`. The plain branch must stay byte-identical to the
   historical prompt, and colour must be off for a non-TTY, `NO_COLOR` or
   `TERM=dumb` (it is the reason redirected logs stay parseable).
7. Prefer targeted edits over whole-file rewrites, and run the project's own
   checks rather than asserting that a change works.
8. Never register a harness tool under a core meta-tool name (`load_skill`,
   `search_tools`, `recall_memory`, …). The harness branch in
   `core/tool_exec.py` runs first, so a shared name shadows the legacy tool in
   the voice and GUI paths. The project-skills loader is `load_project_skill`
   for exactly this reason; `load_skill` remains the core Skills Library.
