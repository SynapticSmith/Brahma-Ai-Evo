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

The full suite takes about 70 seconds and needs the offscreen Qt platform.
For agent work, the fast subset is:

```powershell
py -3.11 -m pytest tests -q -k agent
py -3.11 -m agent run "say hello" --provider stub --json --no-session
```

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
