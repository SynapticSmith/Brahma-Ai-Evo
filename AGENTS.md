# AGENTS.md

Working notes for coding agents in this repository. The Brahma agent harness
reads this file automatically (`agent/prompts.py`); other tools do too.

## What this is

A Windows-first PyQt6 desktop assistant. `main.py` is the entry point and owns
`BrahmaLive` (Gemini Live audio, plus the local/custom/OpenRouter text paths).
Tool implementations live in `actions/`; cross-cutting seams live in `core/`.

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

## Layout

| Path | Contents |
| --- | --- |
| `main.py` | App entry, `BrahmaLive`, `TOOL_DECLARATIONS`, provider routing. |
| `core/` | Seams: `tool_exec.py` (dispatch), `tool_index.py` (advertised surface), `confirm.py` (approval banner), `undo.py`, `user_paths.py`. |
| `actions/` | One module per capability. |
| `agent/` | The agentic harness: loop, code tools, permissions, context, sessions. Docs in `docs/AGENTIC-HARNESS.md`. |
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
6. Prefer targeted edits over whole-file rewrites, and run the project's own
   checks rather than asserting that a change works.
