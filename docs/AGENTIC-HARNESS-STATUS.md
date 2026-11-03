# AGENTIC HARNESS — BUILD STATUS & DECISION LOG

Permanent record of the work that turned Brahma AI Evo's tool calling into a
real agent, why each choice was made, and how to pick the work back up.

Reference documentation lives in
[`AGENTIC-HARNESS.md`](AGENTIC-HARNESS.md). This file is the history and the
handover.

---

## 1. Status

**Shipped, tests green.** The harness is integrated into both the
GUI text path and the Gemini Live voice path, and has a headless CLI. No
database migration is required; the new tables are additive.

| Area | State |
| --- | --- |
| Bounded multi-turn loop | Done |
| File/shell primitives (6) | Done |
| Capability policy + approval gates | Done |
| Context budget + protocol-safe compaction | Done |
| Durable sessions + resume | Done |
| Headless CLI + NDJSON events | Done |
| GUI integration (text + voice) | Done |
| Cancellation (Ctrl-C / `Ctrl+Shift+X`) | Done |
| Transient-failure retries with backoff | Done |
| Instruction discovery (workspace + parents) | Done |
| Session "always allow" approvals | Done |
| Granular approval detail (rule, command/cwd, diff) | Done |
| Path-dependency parallel tool calls | Done |
| Bounded subagent delegation (`run_subagent` + read-only `run_subagent_batch`) | Done |
| Whole-tree command termination on timeout | Done |
| Policy `exec.sandbox`: credential scrubbing, trusted prefix, resource limits | Done |
| Verified container confinement (`sandbox doctor` probe + `require_confined`) | Done |
| MCP servers over stdio (tools only), CLI + GUI text path | Done |
| MCP in-place refresh (`tools/list_changed`, dead server) | Done |
| TTY colour for the approval diff (opt-out, pipe-safe) | Done |
| Cancellation-safe transcript | Done |
| Provider-reported token usage | Done |
| Policy hot-reload (fail-safe) | Done |
| Subdirectory instructions (path-driven) | Done |
| Project skills (`SKILL.md`, on-demand) | Done |
| Project skills shared with the core Skills Library (`.brahma/skills`) | Done |
| Project prompt on GUI text + voice (`AGENTS.md`, skills catalogue) | Done |
| Streaming output (text + tool calls) | Done |
| GUI session persistence + replay | Done |
| Voice-path session persistence | Done |
| Diff highlighting + CHANGES ONLY folding in the banner | Done |
| Custom-endpoint streaming in the GUI; rejected-stream fallback | Done |
| Python floor + CI matrix | Done |
| Docs | This file + `AGENTIC-HARNESS.md` |
| Commit | One commit per hardening batch on `main` (see `git log --oneline`) |

---

## 2. What was added

New package `agent/`:

```
agent/__init__.py       intentionally empty (fast `python -m agent`)
agent/spec.py           ToolSpec, ToolContext, registry, schema conversion
agent/permissions.py    policy document, evaluate(), defaults, template
agent/code_tools.py     read/write/edit/glob/grep/run_command
agent/subagent.py       run_subagent contract: schema, budget clamp, child surface
agent/process.py        run_captured: closed stdin + whole-tree termination on timeout
agent/sandbox.py        exec.sandbox: secret scrub, trusted prefix, job-object limits
agent/mcp.py            stdlib MCP client: config, JSON-RPC, tools, workspace manager
agent/term.py           ANSI view of an approval detail; TTY/NO_COLOR/FORCE_COLOR policy
agent/runtime.py        AgentRuntime, configure(), gui_approver, policy_note
agent/loop.py           AgentLoop, AgentResult, stop reasons, event stream
agent/context.py        ContextBudget, ConversationContext, compaction
agent/session.py        AgentSession (agent_* tables), SessionRecord
agent/session_bridge.py Qt-free GUI↔session seed, voice-turn archive, transcript, picker labels
agent/prompts.py        resident prompt + AGENTS.md/BRAHMA.md loader
agent/skills.py         project SKILL.md discovery, resident list, load_project_skill
agent/providers.py      local/custom/openrouter/stub completers + summariser
agent/streaming.py      delta assembler + SSE transport (stdlib urllib)
agent/retry.py          transient classifier + cancellation-aware backoff
agent/__main__.py       `python -m agent` CLI
```

Modified:

| File | Change |
| --- | --- |
| `main.py` | Appends harness declarations to `TOOL_DECLARATIONS` (all except `run_subagent` on the voice socket, which has no text completer for a child loop); `AGENT_MAX_TURNS = 24`; `_finish_tool_turn` now seeds `AgentLoop(first_response=res)` with the turn-one tool surface; new `_agent_event` (including relayed subagent events); `_ensure_agent_session` persists GUI text turns, `_record_voice_turn` archives completed Live turns, and `/sessions`, `/resume`, `/new` bind or drop the session; `main()` configures the runtime with the developer-mode workspace and the HUD approver. |
| `core/tool_exec.py` | Harness branch at the top of `ToolExecutor.execute`, dispatched via `asyncio.to_thread`. Falls through to the legacy path otherwise. |
| `core/confirm.py` | `is_bound()`, `_on_qt_main_thread()`, `ask_blocking()`; `_Pending` gained `waiter`/`verdict`; `resolve()` now completes the waiter (timeout ⇒ `False`). |
| `core/tool_index.py` | Pinned `read_file`, `edit_file`, `run_command` alongside `undo`/`recall_memory`. |
| `config/agent_permissions.example.json` | Generated from the built-in defaults as an editable reference. |
| `ui.py` | `Ctrl+Shift+X` → `MainWindow._cancel_agent()` → `agent.runtime.cancel()`. |
| `setup.py` | Refuses to run below Python 3.11 with a readable message. |
| `core/ui_shell_delegates.py` | `choose_session()` marshals the modal picker to the GUI thread; `replay_agent_session()` renders stored turns without re-persisting them. |
| `core/ui_chat.py` | `record_chat_event(..., persist=False)` makes a replay a view; the live path still writes to `workspace_store`. |
| `core/ui_shell_tray.py` | **Resume Agent Session…** submits `/resume` through the normal command path. |
| `core/diff_preview.py` | New pure-Python view model for banner diffs: `split_detail`, `count_changes`, `fold_diff`, `preview_detail`. |
| `core/ui_overlays.py` | `DetailHighlighter` (green/red/cyan diff lines) and the diff summary plus CHANGES ONLY toggle in `ConfirmationOverlay`. |
| `agent/providers.py` | Fixed the custom/OpenRouter adapters importing the non-existent `ai_client` from `llm_client`; added shared `custom_stream` used by the CLI and the GUI custom path. |
| `agent/loop.py` | A stream rejected before its first delta gets one plain request and a `stream_fallback`; transient errors still retry, and failures after output still stop. |
| `agent/loop.py` | `_run_subagent` builds the bounded child loop and relays its events; `_run_one` injects the runner through `ToolContext.extra`; `allow_subagents=False` makes recursion impossible in the child. |
| `main.py` | The GUI custom path streams through `custom_stream`; streaming is enabled for `source in ("local", "custom")`. |
| `agent/prompts.py` | Appends the resident project-skills block after the instruction files and before host `extra` text. `project_context()` is the workspace half (instructions + skills); `build_system_prompt` composes it, so CLI output is unchanged. |
| `main.py` | `_agentic_seed_messages` appends `project_context(runtime.workspace)`, so the HUD text path sends the repo's `AGENTS.md` and the skills catalogue on its first request (both discovery branches). |
| `core/session_lifecycle.py` | `_build_config` appends the same blocks to the Gemini Live system instruction, before `live_extra`. |
| `agent/runtime.py` | Imports `agent.skills`, which registers `load_project_skill` as an ordinary read-class tool. |
| `agent/__main__.py` | `skills [--cwd DIR] [--json]` prints the discovered roots, skills and diagnostics, mirroring `mcp list`. |
| `main.py` | `_with_agent_mcp` merges lazily started harness MCP tools into the local/custom first call; `main()`'s `finally` closes the manager beside the voice MCP manager. |
| `agent/mcp.py` | `MCPSession.refresh` re-lists a server that latched `tools/list_changed` or lost its transport, restarting a dead process once and re-registering only that server's specs; `WorkspaceMCP.ensure` calls it on the unchanged-fingerprint path. |
| `agent/skills.py` | `project_roots()` is now the one definition of the workspace skill walk; `default_roots()` composes it with the user-level directory, and a `BRAHMA_SKILLS_DIR` override replaces both. |
| `core/skills_library.py` | Scans workspace `.brahma/skills` roots last as `PROJECT_SOURCE` under the harness contract (`<id>/SKILL.md`, name = directory, instructional only), ignores `executable`/`executor.py` there, and exposes `set_project_dirs`/`configure_library(project_workspace=...)`. |
| `main.py` | Startup re-points the Skills Library at the runtime workspace, so `search_skills`/`load_skill` see the same project skills the harness prompt lists. |
| `main.py` | `_agent_event` prints the `mcp_refresh` event (re-listed vs restarted). |
| `agent/__main__.py` | The CLI event printer formats `mcp_refresh` like the existing `mcp` status line. |
| `.github/workflows/ci.yml` | `harness-pythons` job: harness suite on 3.11/3.12/3.13 with only pytest installed. |
| `.gitignore` | `agent_permissions.json` ignored — it is machine-local user data. |

New tests: `tests/test_agent_permissions.py`, `test_agent_code_tools.py`,
`test_agent_context.py`, `test_agent_session.py`, `test_agent_loop.py`,
`test_agent_cli.py`, `test_agent_retry.py`, `test_agent_prompts.py`,
`test_agent_approval.py`, `test_agent_parallel.py`, `test_agent_usage.py`,
`test_agent_policy_reload.py`, `test_agent_instructions.py`,
`test_agent_streaming.py`, `test_agent_session_bridge.py`,
`test_agent_diff_preview.py`, `test_agent_providers.py`,
`test_agent_subagent.py`, `test_agent_process.py`, `test_agent_sandbox.py`,
`test_agent_mcp.py`, `test_agent_term.py`, `test_agent_skills.py` — all pure-Python
(no display, no model). HUD-only behaviour lives in
`tests/test_confirmation_overlay.py`. Two host-wiring tests,
`test_gui_prompt_wiring.py` and `test_voice_prompt_wiring.py`, import `main` /
`core.session_lifecycle` and are therefore deliberately outside the
stdlib-only harness CI job.

---

## 3. Decisions and why

Each of these was a fork in the road; recording the reason so it is not
re-litigated by accident.

### D1 — Put the harness in `agent/`, not a new top-level package

The planner/executor already exist under `agent/`-adjacent names, but creating
a second top-level package (e.g. `brahma_agent/`) would have split tool
registration across two import paths and confused `main.py`'s bootstrap.
`agent/` was the smallest new surface that did not disturb anything existing.
`agent/__init__.py` stays empty so the CLI does not import PyQt.

### D2 — OpenAI lowercase JSON schema is canonical; convert up for Gemini

Three formats were in play (OpenAI, Gemini uppercase, loose dicts). Gemini is
the outlier, and its conversion is one recursive walk (`ToolSpec.gemini()`).
Choosing OpenAI's form means the stored spec, the loop, the CLI and every
OpenAI-compatible provider agree, and only the Live handshake pays the
conversion cost.

### D3 — One permission enforcement point: `AgentRuntime.execute`

Both the loop and `core/tool_exec.py` call it. If enforcement lived in the
loop, the voice path would bypass policy; if it lived in each handler, one
forgotten decorator would be a hole. Native (pre-existing) actions are still
delegated through `native_dispatch`, so they are subject to the same class
policy.

### D4 — "Headless" means "no human can be asked"

Implemented as `self.headless and self.approver is None` in
`AgentRuntime.decide`. Without this, the GUI (constructing the runtime before a
human can be reached, or after a CLI-ish default) would silently deny every
`exec`. With it, an injected approver always wins over the flag, and the CLI's
`--json` mode still gets hard denials.

### D5 — Default policy: read/write allow, exec ask, deny when headless

Rationale: reads and writes are reversible (`core/undo.py`), and an assistant
that asks before every file edit is unusable. `exec` is the one class that can
do arbitrary damage and is also the one the agent needs to verify its own work,
so it asks interactively and is denied when nobody is there. Path escapes are
`ask` interactively and `deny` headless. The command denylist is intentionally
narrow — it is a tripwire, not a sandbox.

### D6 — Only three of the six tools are pinned in the tool index

`core/tool_index.py` advertises a bounded surface. Pinning all six would add
six permanent declaration slots to every session. Pinned: `read_file`,
`edit_file`, `run_command` (read, change, verify). `glob_files`, `grep_files`
and `write_file` remain reachable through `search_tools`/`call_tool`, so the
capability is complete at a lower resident cost.

### D7 — Agent writes go on the existing undo stack

`write_file`/`edit_file` call `core/undo.push_undo`, so the pre-existing `undo`
tool reverses an agent change. This is what justifies not gating ordinary
writes behind a confirmation: recovery exists, and the user already knows the
word "undo".

### D8 — No repo-level `agent_permissions.json` was created

Only `config/agent_permissions.example.json` exists. Creating the live file in
the repo would pin defaults into git and make a future policy change look like
a code change. The loader already has a documented discovery order; the user
creates the file with `python -m agent permissions init`.

### D9 — `edit_file` requires uniqueness by default

A non-unique `old_string` is the most common cause of a silently wrong edit.
The handler refuses and tells the model to add context, or to opt in with
`replace_all=true`. It also distinguishes "not found" from "found but
whitespace differs", which is the second most common failure.

### D10 — Compaction summarises with the model when asked, deterministically otherwise

`--summarize` (CLI) / an injected summariser uses the same model. The fallback
always works and never raises, because compaction is a safety mechanism: it
must not be able to fail a turn. The deterministic summary keeps the user's
requests, tool frequencies and touched files — the facts needed to resume.

### D11 — Sessions are stored in the existing SQLite file, additively

`workspace_store.py` already owns `%LOCALAPPDATA%\BrahmaAI\config\workspace_store.sqlite3`
and a threading-safe connection pattern. New tables are `agent_*` so an older
build sees only tables it ignores. Messages are stored as JSON because
`tool_calls` / `tool_call_id` / `name` must round-trip byte-for-byte.

### D12 — Call the loop with `first_response`, don't re-request

`_finish_tool_turn` receives a completion the caller already paid for. Passing
it as `first_response` means turn one costs nothing extra and the transcript
stays exactly what the model produced. This also keeps the existing local
ladder's error handling intact (`LocalBrainError` still propagates).

### D13 — Voice path runs harness tools in a worker thread

`ToolExecutor.execute` is an async coroutine driving the live audio session, and
an approval can block until the user clicks. `asyncio.to_thread(...)` keeps the
audio loop alive and gets `ask_blocking` off the Qt main thread (where it
refuses by design).

### D14 — Follow-up turns reuse the turn-one tool surface

`AgentLoop` defaults to the six harness specs when no tool list is given. That
would have dropped every native action and the `search_tools` / `call_tool`
meta-tools from turn two onward — while the resident prompt instructs the model
to reach the catalogue through them. `_finish_tool_turn` therefore takes the
surface the first call used (already bounded by the tool index) and passes it
in, merging the harness primitives so an unpinned one (`glob_files`,
`grep_files`, `write_file`) stays callable even when the resolver did not rank
it for this request. The surface is converted once with
`local_brain.format_tools_for_local`, which is idempotent and accepts both
Gemini declarations and OpenAI tools, so the same list is valid for the local
transport and for a custom OpenAI-compatible endpoint.

### D15 — `permissions init` never clobbers, and seeds nothing by default

The repo ships no `agent_permissions.json`: defaults are already safe (write
allowed, `exec` asked, denied headless), and a checked-in policy would silently
become every user's policy. `init` therefore writes wherever `--path` points
(cwd by default) and refuses to overwrite unless `--force`, because the second
run is the one where a tuned file would be lost. The documented seeding target
is `%LOCALAPPDATA%\BrahmaAI\config\agent_permissions.json`, which is discovery
location 3 and keeps the checkout clean. `agent_permissions.json` and
`config/agent_permissions.json` are gitignored; only the example template is
tracked.

### D16 — Cancellation is a flag, never a killed thread

The loop runs in worker threads (GUI text path, the voice path via
`asyncio.to_thread`, the CLI's main thread). Killing one mid-write would leave a
truncated file or an orphaned child process, so stopping is cooperative: a
`threading.Event` on `AgentRuntime`, checked before each turn, each call, and
inside a retry wait. `runtime.cancel()` is idempotent and carried across
`configure()` so a Stop pressed after a re-configure still reaches a running
loop; `reset_cancel()` runs at the start of every new turn so a stale press
cannot kill it. Sources are the CLI's SIGINT handler (first press requests,
second raises `KeyboardInterrupt` → exit `130`) and `Ctrl+Shift+X` in the HUD.
A cancelled run reports `stop_reason="cancelled"` — no traceback, no "error"
text, because the user asked for it.

### D17 — Retries live in the loop, not in each provider adapter

Model calls fail transiently (429s, proxy 502s, dropped sockets) and a coding
loop makes many calls per task, so a single blip must not abort the run.
`agent/retry.py` classifies and backs off; `AgentLoop._request` is the only
caller. Putting it there rather than in `agent/providers.py` means the GUI path
and the CLI path get identical behaviour, and the retry is visible as an event
instead of hidden inside a transport wrapper. The classifier is deliberately
conservative: `401`/`403`/`404`/`422`, malformed requests, and
`connection refused` are not retried, because another attempt cannot change
those answers and a dead local server should fail in milliseconds. Backoff is
0.8 s doubling to 8 s plus up to 25 % jitter; the wait uses `Event.wait()` so a
cancel interrupts it rather than sleeping through it.

### D18 — Instruction discovery walks up, never down (yet)

A workspace is often a package inside a monorepo, so reading only
`<workspace>/AGENTS.md` silently discards the rules the outer repo already
wrote. `load_project_instructions` now walks up to three parent directories
(overridable with `BRAHMA_AGENT_INSTRUCTION_PARENTS`), emits root-most first so
the nearest file is read last and wins, and skips the drive/filesystem root.
Truncation keeps the **tail** for the same reason. A blind recursive scan for
subdirectory files was rejected: without knowing which paths the model will
touch it would pull in unrelated packages and burn the context budget. That was
left as a gap at the time; D25 closes it with per-turn path awareness rather
than a bigger glob.

### D19 — "Always allow" is session-scoped and scoped to the thing approved

Approval fatigue is real: a loop that asks before every `pytest` run is one
nobody leaves unattended. The HUD now offers ALWAYS ALLOW, and the runtime
remembers the answer in memory. The key is the tool name for file/native tools
(the policy already constrains paths) and the exact command string for
`run_command` — approving a test command must not approve a delete. A policy
`deny` is never widened because the remembered set is consulted only when the
decision was `ask`. Nothing persists to disk: `agent_permissions.json` is the
way to make a rule permanent, and a new process should not inherit a button
press from last week. `requires_approval=True` tools are deliberately excluded,
so irreversible actions keep asking.

### D22 — The approval banner says what will actually happen

"Approve run_command?" with a JSON blob is not informed consent: the command and
its working directory were the two things hidden. `agent/approval_detail.py`
now builds the detail as a rule line (why), then the concrete subject:
command + cwd for `run_command`, a real unified diff for `edit_file` and for
`write_file` overwrites, paths and patterns for the read tools, bounded JSON
only as a fallback. Diffs are capped at 1 600 characters and rendered in a
scroll box so the banner cannot grow past the window.

The detail is a `str` subclass carrying `title` and `remember`, so the approver
protocol did not change and every existing approver still works. The same work
fixed a real widening in D19: "always allow" for a `paths.outside_workspace` or
`paths.protected` ask is now keyed by the **resolved path**, not the tool name.
Approving one outside file no longer blesses every outside file for the
session. A class-level ask stays tool-wide, because that is what the policy is
actually asking about.

### D23 — Token usage comes from the provider, not from arithmetic

`usage["estimated_tokens"]` is a local estimate; treating it as a bill would be
inventing a number. `agent/usage.py` reads what the provider actually returned:
OpenAI `usage`, Gemini `usage_metadata`, and SDK objects with attributes. The
result is `{}` when nothing was reported, so "unknown" and "zero" stay distinct
— `tokens_reported` in `AgentResult.usage` says which one you have. A missing
total is summed only when both halves were present; a partial count is returned
without the total rather than completed by guesswork. Each `assistant` event
carries that turn's counts, and the loop sums them across turns. The CLI footer
prints the total only when it is real; `--json` already exposes the whole dict.

### D24 — Policy edits apply without a restart

The runtime held a policy object loaded at startup, so changing one rule meant
restarting Brahma. `AgentRuntime.reload_policy()` re-reads the file named by
`policy.source` when its `st_mtime_ns` changed, throttled by
`policy_check_interval` (default 1 s) and called at the top of `execute`.
`force=True` bypasses both checks for an explicit "reload now" control or a
test.

The failure mode matters more than the feature: a malformed edit must not
disarm the policy. The last good policy stays in force, a `policy_reload` event
with `ok=False` and the parser error is emitted, and the bad mtime is recorded
so the same broken file is not re-parsed on every tool call. A policy built
from a dict with no `source` is never re-read. Session approvals survive the
reload but are still consulted only after `evaluate()`, so a new `deny` is
immediate and a remembered "always allow" cannot widen it.

### D25 — Subdirectory instructions follow the touched paths

The upward instruction walk stops at the workspace, so `packages/foo/AGENTS.md`
was invisible to an agent working in `packages/foo`. A recursive scan is the
wrong fix: it reads `node_modules`, pays for it every turn, and lets rules from
untouched packages compete with the ones that matter.

The loop now derives the directories from the **paths a turn actually named**
(`path`, `cwd`, `file`, … in the tool arguments). It walks only between the
workspace and those paths, shallowest first, so reading
`packages/foo/app.py` appends `packages/AGENTS.md` then
`packages/foo/AGENTS.md`. Paths outside the workspace, the workspace itself, and
anything under vendored/generated directories are ignored. Depth (12), per-file
characters (4 000) and a per-run budget (6 000) bound the cost; a `seen` set
makes a second turn in the same package a `stat`, not a re-read.

The text goes into the **system prompt**, not the transcript: a synthetic
`system`/`user` message mid-conversation complicates resume and compaction,
while a rebuilt system prompt is invisible to the tool protocol and reaches the
model before its next action. The `instructions` event names what loaded, so
the CLI and HUD can show it.

### D26 — Streaming is additive, opt-in, and reset-aware

A coding loop makes several slow model calls and a local model can spend a
minute in prefill; a UI that shows nothing for that minute looks wedged. But
streaming must not become a second execution path: the transcript, repeat
detection, compaction and resume all assume one assistant message per call.

The loop therefore keeps `complete(messages, tools)` as the contract and treats
streaming as an **optional attribute** on the completer:
`complete.stream(messages, tools, on_delta)`. `AgentLoop(stream=True)` uses it
when present; when absent it calls `complete` and emits one `stream_fallback`
event. No capability flag to negotiate, no provider registry to extend, and a
third-party completer written before streaming existed keeps working.

`agent/streaming.py` owns the assembly: text appends in order, tool calls merge
by `index` with `arguments` concatenated (each delta is a fragment of the JSON),
the final message is sorted by index, and a usage-only tail chunk updates usage
without emitting anything. Both the local runtime's existing SSE reader and a
new stdlib `urllib` reader for OpenAI-compatible endpoints feed the same
assembler, so a garbled tool call has one place to be fixed.

The subtle case is **retry after first token**. The retry wrapper sees a
transient failure mid-stream and reissues the request, but the UI is already
showing text that will never be completed. `on_retry` emits `delta` with
`kind: "reset"` before the `retry` event; the CLI closes the line and prints
"partial output discarded", the HUD does the same, and the next attempt starts
from an empty assembler. Only the successful attempt reaches the transcript.

Two smaller fixes came with it: `AgentLoop._emit`'s first parameter is now
`event_type` (a delta's own `kind` field collided with it), and OpenRouter's
base URL is normalised with `_endpoint_base()` because `or_client.API_URL`
already ends in `/chat/completions` while the shared transport appends that
path itself.

### D27 — The GUI seeds `[system, history, user]`, never a stored system row

The CLI had `resume`; the HUD rebuilt `[system, user]` on every turn and wrote
no `agent_*` row at all, so closing the window was a hard reset. The fix could
not simply hand the GUI a `ConversationContext` built from `session.messages()`:
the seed also has to be the exact list the *first* provider request sends, or
the model answers the new sentence without the earlier conversation.

`agent/session_bridge.py` is the Qt-free seam. `history(session)` strips a
leading system row; `seed_context()` builds the turn context with a freshly
rebuilt system prompt; and `messages_after(history, seeded)` returns only the
tail, so a resumed turn appends one user message instead of duplicating the
whole transcript. `_finish_tool_turn` persists that tail once and then passes
the session to `AgentLoop`, which keeps its existing append/compaction/status
behaviour. The row is created against the runtime workspace, not the process
cwd, because a shortcut launch starts in the install directory.

The system prompt is deliberately **not** persisted. It names the workspace,
endpoint, instruction files and permission note of the current run; an old copy
would silently outrank the current one. The CLI already rebuilds it on resume,
so both surfaces now behave the same way.

Replay is a view, not a second write: `record_chat_event(..., persist=False)`
adds the stored user/assistant text to the feed without touching
`workspace_store`. `/resume` with no id opens a modal picker marshalled to the
GUI thread (the command runs on a worker); a unique id prefix is accepted. The
tray menu submits `/resume`, so the feature is reachable without typing.

### D28 — Diff folding is a view over the detail string, not a new payload

The banner already received a bounded unified diff from
`agent/approval_detail.py`, but on a HUD a two-hunk edit still spends most of a
150-pixel box on unchanged context, and a flat monochrome block makes `+` and
`-` easy to miss. Two temptations were rejected:

* **Passing structured data to the banner.** That would change the approver
  contract (a `str`) and force every approver — TTY, tests, third-party hosts —
  to learn a new type. The diff stays text; `core/diff_preview.py` derives the
  view from it.
* **Folding inside `approval_detail`.** The approver's string must remain the
  complete, exact detail: it is what the CLI prints and what a test asserts.
  Folding is a display choice, so it lives beside the widget.

`split_detail()` only recognises a diff when `--- ` is immediately followed by
`+++ `, which avoids misreading the edit preview's `--- before ---` heading.
`count_changes()` ignores file headers, so `+1 −1 in 1 hunk` describes changes
rather than header noise. `fold_diff()` keeps one line of context either side
of every change and replaces each dropped run with `… N unchanged line(s)` so an
elided gap is visible rather than silent. The toggle swaps the box text between
the two renderings; approving either one executes the same call, because the
underlying arguments never changed.

`DetailHighlighter` is a `QSyntaxHighlighter`, so it formats the document
without mutating it: `toPlainText()` still equals the string the approver was
given, and a test asserts that. The summary and toggle only appear when a diff
was actually found; a command-only ask keeps the plain box.

### D29 — A rejected stream falls back once; a broken one does not

Streaming on a custom endpoint is a bet that the server speaks SSE. When it
does not, the failure is a `400` on `stream: true` — not a transient error, and
not something a retry can fix. Leaving it to `call_with_retry` meant the whole
turn died on an endpoint that otherwise answers fine.

The loop now distinguishes three cases by *when* and *how* the stream fails:

* **before the first delta, terminal error** → one plain `complete` call, a
  `stream_fallback` event naming the error, and `self._streamer = None` so the
  rest of the run (including a retry of the fallback itself) stays plain;
* **before the first delta, transient error** → re-raised into the retry
  wrapper, because a timeout is a network problem, not a protocol mismatch;
* **after any delta** → re-raised and reported as a stop. Falling back would
  send a second request whose text duplicates what the user already saw.

The check reads `self._streamer`, not the closure's captured value, so the
fallback decision survives a retry. `custom_stream()` is the shared payload
builder for the CLI adapter and the GUI custom path, which removes the drift
that let the two transports diverge in the first place.

That work exposed a plain bug: `build_completer("custom")` and
`build_completer("openrouter")` did `from llm_client import ai_client`, but the
module only ever defines `client`. Every non-stub CLI or GUI provider path
failed at build time with an ImportError; the fake-module tests now mirror the
shipped name so it cannot regress. The GUI custom path also stopped announcing
`stream_fallback` — it streams through the same helper.

### D30 — Parallelism is fenced per segment, not decided per turn (superseded by D32)

D20's all-or-nothing rule was safe but wasteful. A real coding turn is usually
`read, read, read, edit, read, read`: one write in the middle serialised every
read around it, which is exactly when the latency hurts.

The unit of decision is now the **segment between fences**. Walking the calls
in model order:

* a run of policy-allowed, non-repeated `read` calls shares the pool;
* a write, `exec`, native call, denied call or `ask` runs alone and is a
  barrier — everything before it has completed, everything after it starts
  only when it is done;
* a call past `repeat_limit` is skipped without a worker, and because it has
  no side effect it does not split the burst around it.

That preserves the two properties D20 was protecting. Read-after-write stays
correct because a later read cannot start until the write returned, and
banners cannot stack because an `ask` is never inside a pool. It also keeps
the transcript invariant: results land on their items, and the caller still
emits one `tool` message per call in model order.

What remained was a *dependency graph*: reads after a write that did not touch
the written file still waited. D32 replaces the segment with a per-call
path-dependency scheduler.

### D31 — Voice turns are archived, not replayed into the live socket

Gemini owns the spoken conversation: the harness sees transcription chunks and
tool calls, never the model's live context. Not persisting them meant a HUD
restart lost everything that was said, and `/resume` could only ever show typed
turns — even though the rows have exactly the shape a voice pair needs.

On `turn_complete`, `AudioIO` hands the cleaned input/output transcriptions to
`BrahmaLive._record_voice_turn`, which binds the first spoken turn to the
current session (model label `gemini-live`) and appends the pair via
`session_bridge.record_voice_turn`. The helper collapses whitespace, skips a
blank side — a tool-only turn can have no spoken answer — and sets
`turn_count` from the stored user messages. Every failure returns `0`: audio
must never depend on SQLite.

This is archival, not context transfer. Nothing from disk is fed back into the
live socket; Gemini's session state is authoritative and replaying history
into it would duplicate turns. The win is one transcript across modalities:
`history()` seeds the next typed turn with what was said, `/sessions` lists the
row, and `/resume` replays it in the HUD.

### D32 — Tool dispatch follows per-call path dependencies

D30 improved on D20 but still treated a write as a turn-wide-ish barrier: any
write serialised every read around it, even a read of an unrelated file. The
common coding turn is `read, read, edit, read, read`, and the two trailing
reads usually touch different files from the edit.

The scheduler now derives an effect per call — `(kind, resolved path)` for a
policy-`allow`ed, non-repeated `read` or `write` — and starts a call as soon as
every *earlier* call it conflicts with has finished:

* two reads never conflict, so unrelated reads overlap;
* two writes always conflict (one writer keeps the undo stack coherent), so
  they serialise even when their paths differ;
* a read and a write conflict when their paths are equal or nested. Equal
  paths keep model order (`read` sees the old bytes, `write` sees the earlier
  read done); a write inside a directory that `glob_files`/`grep_files` is
  scanning waits for the scan.

`_run_group` submits ready calls from a bounded `ThreadPoolExecutor` and waits
for `FIRST_COMPLETED`, re-evaluating blockers after each completion. Fences are
unchanged: `exec`, native calls, approvals, denials, path-less writes and
repeats have no schedulable effect, so everything before them finishes, they
run alone in model order, and dispatch resumes. Cancellation stops new
submissions, drains running calls, and leaves the rest for the caller's
`CANCELLED_TOOL_OUTPUT` filler.

The bound is deliberate. A path argument is the only evidence the scheduler
has; it does not follow junctions or symlinks, and it treats a nested pair as a
conflict even when the read is a file and the write a sibling directory. That
errs toward serialising rather than racing.

### D33 — Delegation is one bounded child, never a second enforcement path

A single loop is right for most work, but some tasks deserve their own context:
"find every caller of this function" should not fill the parent's window with
search output. The tempting design — a coordinator that spawns agents with
their own runtimes — would create a second place where permissions can drift and
a recursion path that is hard to bound.

`run_subagent` is therefore one tool call, classified `native`, whose handler
only validates the request and hands it to `AgentLoop._run_subagent`. The child
is a normal `AgentLoop` sharing the parent's `AgentRuntime`, completer, retry
settings and cancel event, so there is still exactly one enforcement point. It
gets a fresh context, no session row, its own turn budget (default 8, clamped to
12), and a tool surface produced by intersecting the request with the parent's
own tools. The default surface is the parent's `read`-class tools; a model cannot
grant a child a tool the parent does not have, and `run_subagent` is always
removed. `allow_subagents=False` on the child is the second guard against
recursion.

Only the report crosses back. A child exception is caught and returned as tool
text, and a non-`completed` stop appends a "partial — verify" note. The
transcript is working memory, not history, which is why a child needs no session
row and why its events are relayed under `subagent_start` / `subagent_event` /
`subagent_end` instead of being spliced into the parent's turns. The voice
socket does not advertise the tool: Gemini's live session owns its transport and
cannot build a text child loop.

### D34 — A timed-out command loses its whole tree, and never waits on stdin

`subprocess.run(..., timeout=...)` raises after killing the immediate child. For
a coding agent that is the wrong unit: `pytest`, a build or a script starts
children, and those children keep writing files, holding ports and keeping the
capture pipes open. The next turn then races the previous call's orphans, and
on Windows the read can block until the grandchild exits on its own.

`agent/process.py` keeps `Popen` + `communicate(timeout=...)` but adds
containment at the moment the process starts. On Windows it `OpenProcess`es the
shell and assigns it to a fresh job object; `TerminateJobObject` on timeout ends
every process in that job. On POSIX the command starts a new session, so
`killpg` reaches the group. A failed job assignment falls back to
`taskkill /F /T`, then `Popen.kill()`: containment is best-effort, never a reason
to fail the call. The tree is terminated *before* the pipes are drained, so the
timeout text still includes the output printed before the deadline.

Two boundaries are deliberate. `stdin` is `DEVNULL`, because an agent call has
no human to answer a password prompt; without it the command burns the full
timeout and returns nothing. And normal completion does not kill survivors: a
command that intentionally starts a background process still leaves it running,
which preserves the pre-existing contract. This bounds lifetime, not filesystem
or network access — it is containment, not a sandbox, and the policy is still
the gate for whether a command may run at all.

### D35 — The approval prompt is coloured only when a person is looking at it

The HUD shows an approval diff with green additions and red removals, but the
TTY prompt printed the same detail as flat text. Adding colour there is useful
exactly once and harmful everywhere else: a redirected stderr feeds logs, CI
capture and editor plugins, and escape sequences in those are noise.

`agent/term.py` therefore splits policy from rendering. Colour requires *both*
`supports_color` (a TTY, unless `FORCE_COLOR=1`; `NO_COLOR` and `TERM=dumb`
always win) and `prepare_stream` (on Windows, `SetConsoleMode` must accept
`ENABLE_VIRTUAL_TERMINAL_PROCESSING`, or the console gets plain text instead of
literal escapes). The plain branch reproduces the historical prompt byte for
byte — leading newline, `[approval]`, detail, question with no trailing newline
— so a piped run cannot tell the feature exists. The coloured branch only wraps
runs of text, never rewrites them, and a test strips the escapes to prove it
equals the plain prompt.

Rendering knows one ordering rule: `--- ` / `+++ ` file headers are dim
metadata, checked before the single-character add/remove rules, so a header is
never painted as a change. `@@` hunks are cyan, `rule:` lines bold-yellow, and
known argument labels (`command:`, `cwd:`) bold. The module is stdlib-only and
imports nothing GUI-related, so `python -m agent` stays fast and headless.
`--json` never prompts on a TTY-less stream, so NDJSON is unaffected by design.

### D36 — Isolation is a policy hook, not a pretend sandbox

The honest gap after D34 was access, not lifetime: a command could still read
the model's API key out of the environment or consume the machine. Windows has
no portable, stdlib filesystem/network sandbox. AppContainer needs ACLs on the
Python install and workspace, silently fails against a normal user install, and
a wrapper that only *looks* enforcing is worse than none.

So `exec.sandbox` adds the controls this harness can actually enforce.
`env_scrub` (default on) withholds variables whose underscore-delimited name is
`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASSWD`, `PASS`, `CREDENTIAL(S)`,
`DATABASE_URL`, `MYSQL_PWD` or `PGPASSWORD`; `TOKENIZERS_PARALLELISM` and
`KEYBOARD` survive the boundaries, and `env_keep` names an exception. Withheld
counts are reported, because a command failing for a missing token should say
why rather than look like a bug. This is secret hygiene, not access control: a
command can still read a `.env` file the policy allows.

`prefix` is a trusted argv that receives the command as **one argument** with no
shell, so the documented shape ends in `sh -c` or `cmd /c`; `{cwd}` is
substituted for a container mount. The command is not re-parsed by `cmd.exe`,
which is what makes a Windows prefix path with spaces work. The harness cannot
verify what the prefix does, so it is exactly as strong as the program the
deployment chose; this is the supported way to get real confinement.

Resource limits use the job object D34 already creates: `max_memory_mb`,
`max_processes` and `max_cpu_seconds`, zero meaning unlimited. POSIX applies
address space and CPU through `resource.prlimit` when available and reports
process count as Windows-only. Any limit that cannot be applied is surfaced in
the tool output instead of being silently dropped. The sandbox section merges
one level deeper than the rest of the policy, so setting one limit keeps
`env_scrub`; a malformed value fails at load like every other policy error.

### D37 — MCP is a tool source behind the same boundary, not a second runtime

The app already talks to MCP servers for voice through `core/mcp_manager.py`
and the official SDK. Reusing it in the harness was not possible: `agent/` has
to stay importable on an interpreter with only pytest installed, and the SDK's
asyncio client would tie a synchronous loop to an event loop the loop does not
own. So `agent/mcp.py` speaks the wire protocol directly — newline-delimited
JSON-RPC 2.0 over stdio — and implements exactly `initialize`,
`notifications/initialized`, `tools/list` (with cursor pagination) and
`tools/call`. Resources, prompts, sampling and roots are omitted rather than
faked, because a half-supported capability produces tools that fail on first
use.

The important property is that an MCP tool is not a special case at execution
time. Discovery registers an ordinary `ToolSpec` with `tool_class="mcp"`, so
every call still walks `AgentRuntime.execute`: policy, path scoping, approvals,
output cap and exception-to-text behave exactly as they do for `read_file`. A
policy can allow, ask or deny the whole class, or override one tool by its
namespaced name. The class default is `allow` because adding a server to the
config is an explicit installation, like installing a plugin; `deny` or `ask`
is the switch for an untrusted one.

Names are `mcp_<server>_<tool>` with everything outside `[A-Za-z0-9_]`
collapsed to `_`, because both OpenAI and `ToolSpec` reject dots and dashes.
The prefix keeps provenance visible to the model and makes the per-tool
override obvious. Long names get a digest suffix and a collision gets a
numeric one, so two servers cannot silently shadow each other.

The config is trusted input in the same sense as `AGENTS.md`: a server is an
arbitrary program the user named. `env` merges over the inherited environment
(so a shell-exported token needs no disk copy) and a relative `cwd` resolves
against the config file, the only location its author can reason about.
Discovery is `BRAHMA_MCP_CONFIG`, then `.brahma/mcp.json`, then `.mcp.json`,
with `--mcp-config` overriding and `--no-mcp` disabling. A missing or malformed
explicit config stops the run (`EXIT_USAGE`) instead of quietly reducing the
tool surface — the failure mode where an agent looks broken because half its
tools vanished is worse than a clear error.

Lifecycle is per run. `close()` unregisters the specs before killing the
process, so a dead server's tools cannot haunt a long-lived process or a later
test. Each server gets the same job-object/POSIX tree kill as `run_command`; a
crash fails the pending call with the server's stderr tail rather than hanging.
Two honest limits: tools only, and no auto-restart or `tools/list_changed`
refresh, so a crashed server stays failed until the next run. D38 later added
the GUI text path; the CLI was the only consumer in this batch.

### D38 — The GUI keeps one MCP session, keyed by workspace and config identity

A CLI command has an obvious lifetime; a GUI turn does not. Starting servers in
`_finish_tool_turn` would pay npx/node startup on every message and, worse,
would be too late for the first model call: the loop merges `all_specs()` into
follow-up turns, but a model that never sees a tool in the initial declaration
array never asks for it. So the GUI starts a process-wide `WorkspaceMCP` before
`resolve_tools` builds the first surface and merges the MCP declarations into
that list (`with_mcp_tools`, deduplicated by name because the host's tool bridge
may already expose an MCP tool).

Reuse is keyed by `(resolved workspace, config path, mtime_ns, size)`. A second
turn with the same key is a stat and a return; changing the workspace in
Settings or editing the config restarts the session and unregisters the old
specs. That matters because the registry is process-global: leaving a dead
server's tools advertised is the failure mode D37's `close()` exists to avoid.
There is deliberately no timer or file watcher; the next turn is the refresh
point, which keeps the manager stdlib-only and thread-safe without a background
loop.

The manager lives at module scope in `main.py`, not on `BrahmaLive`: the app is
constructed inside the nested runner while teardown happens in `main()`'s
`finally`. Shutdown closes the harness manager beside the voice MCP manager.
A malformed config marks the manager unhealthy and emits one `error` event,
then returns the built-in tool list; the same file is not re-reported on every
turn because the fingerprint is unchanged. Fixing the file changes the
fingerprint and the next turn recovers.

### D39 — Project skills are listed cheaply and loaded on demand

A project skill is a folder under `.brahma/skills/<name>/` holding a `SKILL.md`
whose frontmatter names it and describes when it applies. Putting every body in
the system prompt would make the resident cost proportional to how much people
wrote, and a body the model does not need competes with the instructions it
does. So the prompt carries only `name: description`, and the model calls
`load_project_skill` when one matches. Discovery for the prompt reads an 8 KB
prefix (`with_body=False`); the loader re-discovers with full bodies so it
always returns the current file. The body cap is 256 KB, because a larger file
is a document, not a skill.

The name is deliberately **not** `load_skill`. That name already belongs to the
app's Skills Library (`skill_id`) in `core/tool_index.py`, and the harness
branch at the top of `core/tool_exec.py` runs first, so registering a harness
tool under the same name shadows the legacy tool for both voice and GUI text.
The failure was live, not theoretical: `load_skill` started demanding `name`
instead of `skill_id`. Two catalogues, two names; the harness loader is
`load_project_skill`, and `is_registered("load_skill")` must stay `False`.

Roots are the workspace, up to three ancestors (a package in a monorepo
inherits the monorepo's skills), then `%LOCALAPPDATA%\BrahmaAI\skills` (or
`~/.config/brahma/skills`). Nearest wins; a shadowed copy is reported as a
diagnostic rather than silently changing behaviour. `BRAHMA_SKILLS_DIR`
(`os.pathsep`-separated) overrides every root, and an empty value disables
discovery, mirroring `BRAHMA_MCP_CONFIG`. The resident block is bounded to 24
skills / 4 000 characters and says `N more skill(s)` rather than truncating a
line mid-description.

Skills are trusted input in the same sense as `AGENTS.md`: the body is
instructions, not code the harness runs. `allowed-tools` is parsed and shown
for humans and future policy work but is **not** enforced — the runtime policy
remains the only execution boundary, and a second filter here would be a hole,
not a feature. A malformed file is a diagnostic and never disables the rest; a
BOM is stripped with `utf-8-sig`. Discovery runs when a prompt is built and
when the loader is called, so a skill added mid-run appears on the next turn,
not immediately. At D39 this was separate from the richer core Skills Library
(`skills_library/`, `%LOCALAPPDATA%\BrahmaAI\skills_library`), which the voice
path and the GUI's existing tool surface still use; D42 later made the project
roots visible to that library too, without merging the two user-level layers.

### D40 — One project-prompt seam for every surface

`build_system_prompt` already composed the workspace's instruction files and
skills catalogue, but it had only two call sites: the CLI and the child loop.
The HUD built its own resident prompt and the Live path built its own, so
neither read `AGENTS.md` — despite `AGENTS.md` promising the harness does — and
both advertised `load_project_skill` while listing no skills. The tool was
therefore unguessable: the catalogue is the whole point of progressive
disclosure (D39).

The fix is one helper, not two copies. `project_context(workspace)` returns the
workspace-scoped blocks (the instruction walk plus `skills.prompt_block`), and
`build_system_prompt` is now composed from it. The HUD appends it in
`_agentic_seed_messages` — in both the normal and the `advertise_discovery=False`
branch, because this is prompt text, not a tool — and the Live path appends it
in `_build_config`, before the host's `instruction_injector` layer. Ordering is
deliberate: project rules outrank generic advice, host extras stay last, and the
text stays in the system prompt rather than the transcript, so resume and
compaction are untouched (D27).

Discovery stays silent. The prompt is rebuilt every turn, so a per-turn
`instructions` event would be noise; the catalogue is inspectable with
`python -m agent skills`, and subdirectory loads still emit `instructions` when
they actually add a file. A missing or unreadable workspace is non-fatal: the
helper returns `""` and the host prompt is unchanged.

### D41 — Refresh MCP at the turn boundary, not in a watcher

The client already received `notifications/tools/list_changed` and latched it in
`StdioServer.tools_changed`, but nothing read the flag, and `WorkspaceMCP.ensure`
returned early on an unchanged config fingerprint without checking the session.
A GUI process therefore advertised a stale catalogue after a server changed its
tools, and a crashed server's tools stayed in the registry (and failed on first
call) until the workspace, the config file or the app changed.

The fix keeps the fingerprint as the *config* trigger and adds a *runtime*
trigger: `MCPSession.needs_refresh()` is true when a server latched the
notification, lost its transport (`closed`), or its process is gone
(`alive`). `ensure` checks that predicate on the unchanged-fingerprint path and
calls `MCPSession.refresh`, which unregisters only the stale server's specs,
re-lists it, and re-registers them; a server whose process died is closed and
started again exactly once. Re-registration recomputes the `taken` name set from
the live registry, so refresh cannot duplicate a name or orphan an old spec.

Three deliberate limits. There is no background thread: the next turn is the
refresh point (D38's discipline), so a server that changes mid-turn is picked up
on the following turn. A re-list that fails drops that server's tools and emits
one error with the stderr tail instead of leaving a catalogue of calls that
cannot work; it is not retried on every turn, so a broken command cannot spawn
a process per turn. A server that failed to start at session start is still only
retried when the config fingerprint changes. `closed` is checked alongside
`alive` because the reader thread observes EOF before `poll()` necessarily
reports the exit — the first version of this change missed that race and
reported a dead transport as healthy.

### D42 — Project skills are one store with two doors

Two catalogues grew up separately: the harness discovers
`.brahma/skills/<id>/SKILL.md` for the prompt and `load_project_skill`, while
the core Skills Library behind `search_skills`/`load_skill` only scanned
`<repo>/skills_library` and `<user data>/skills_library`. The naming problem was
real — `load_skill` already belongs to the library, and a harness tool with that
name would shadow it because the harness branch in `core/tool_exec.py` runs
first — but the *store* did not have to stay separate. A repository's skills
were invisible to the voice path and to the older GUI tool surface, which is
where a user would most expect "the project's skills" to be.

The decision is one store, two doors, and one owner per concern.
`agent.skills.project_roots()` is the only definition of the workspace walk
(workspace plus three parents, nearest first, or `BRAHMA_SKILLS_DIR`).
`SkillsLibrary` appends those roots last, so a project copy overrides a builtin
and a user copy; it imports `agent.skills` lazily inside the host wiring rather
than from the library module, because that import registers a tool and the
library must stay usable in a process that is not the harness. Project roots
accept only the harness contract — `<dir>/SKILL.md`, directory name = id,
frontmatter `name` must match — and stay instructional even when the file says
`executable: true`: a repository may describe how work is done, but it may not
ship code the app runs (D39's boundary).

`configure_library(project_workspace=...)` reverses the harness's nearest-first
list once, inside the library, because the library's scan is "later root wins";
that reversal is the single point where the two conventions meet. Wiring is
startup-only because the runtime workspace is fixed at startup from
`developer_mode_workspace`, and re-calling it with a different workspace
re-points the catalogue, so a future workspace switch already has the seam.

Rejected alternatives: importing `core` into `agent/` (the harness must stay
stdlib-only), merging the two user-level directories (the harness's
`<userdata>/skills` and the library's `<userdata>/skills_library` mean different
things), sharing the `load_skill` name (shadowing, not unification), and
executing project skill code (a second boundary by the back door). The shared
contract is deliberately the *project*, where a repository has one obvious
answer.

### D43 — Verified container confinement is a behavioural probe, not a prefix label (O-1 / N4)

`exec.sandbox.prefix` lets a deployment wrap `run_command` in Docker, `bwrap`,
or another launcher, but a prefix name alone is not proof of isolation — notably,
plain `wsl` mounts host drives under `/mnt/c` and shares host networking by
default. `agent/sandbox.py:probe_confinement` therefore verifies a configured
prefix behaviourally before trusting it for unattended execution:

1. Rejects empty prefixes and known unconfined launchers (`wsl`, `cmd`,
   `powershell`, `pwsh`, `sh`, `bash`, `zsh`) outright via
   `is_known_unconfined_prefix`.
2. Runs three active probes inside the prefix: `write_outside` (attempts to
   write a sibling file outside the workspace mount), `network_egress`
   (attempts to connect to an ephemeral loopback listener on the host), and
   `uid_isolation` (checks that the child is not running as unrestricted UID 0
   and cannot see a host-only sentinel file outside the workspace).
3. `python -m agent sandbox doctor` runs the probe and reports `CONFINED` /
   `UNCONFINED` (or `--json`) without ever writing or mutating policy on disk.
4. `exec.sandbox.require_confined` defaults to `False`. When enabled in policy
   (or when `--yolo` is passed on the CLI), `PermissionPolicy.evaluate` calls
   the cached `check_confinement` and denies `exec` tools with
   `rule="exec.sandbox.require_confined"` unless all three checks pass.

### D44 — Read-only subagent fan-out (`run_subagent_batch`) shares one runtime and enforces strict workspace confinement (O-2 / N5)

Mutating delegation remains sequential through `run_subagent`, because concurrent
child writes or approval prompts would race the undo stack and stack HUD banners.
For independent research and review tasks, `run_subagent_batch` runs up to 8
bounded children concurrently on a `ThreadPoolExecutor` (`max_concurrency`
default 4, clamped to 8) and returns their reports in submission order.

Because `tool_class == "read"` alone is not a containment boundary (`read_file`
can read outside the workspace or read `.env` if the parent policy or session
approvals allow it), `run_subagent_batch` enforces a five-part contract across
`select_batch_tools` (`agent/subagent.py`) and `AgentRuntime.decide(..., batch_readonly=True)`
(`agent/runtime.py`):

1. **Read-only workspace tools only:** `spec.tool_class == "read"`,
   `spec.classifier is None`, and `"desktop" not in spec.tags` (no `mcp`,
   `native`, `write`, `exec`, `network`, or `device` tools).
2. **Strict workspace path confinement:** every path argument in `PATH_ARG_KEYS`
   must resolve inside `runtime.workspace`. Outside-workspace paths and
   `protected_globs` matches are denied outright (`DENY`, no approver prompt),
   even when the parent policy sets `outside_workspace="allow"`,
   `protected="allow"`, `trusted_roots`, or `session_allow`.
3. **No approval-gated tools:** any tool with `requires_approval=True` is
   excluded from the surface and denied at runtime.
4. **No recursion (`I-08`):** `DELEGATION_TOOLS = ("run_subagent", "run_subagent_batch")`
   are excluded from every child's advertised surface (both single and batch).
5. **Single runtime and cancel event:** all batch children share the parent's
   `AgentRuntime` instance and `cancel_event`, and are omitted from the voice
   socket (`main.py`) alongside `run_subagent`.

### D45 — Python runtime auto-forwarding, graceful sensorium fallbacks, and live session reconnect resilience

On developer machines with mixed Python versions (e.g. default Windows `python.exe` pointing to a preview version like Python 3.14 without packages, while project dependencies live in Python 3.11/3.12), launching directly via `python main.py` previously failed at module scope (`ModuleNotFoundError: No module named 'psutil'`).

Three coordinated mechanisms ensure boot resilience and clean console operation:

1. **Transparent Python runtime auto-forwarding (`core/qt_bootstrap.py`):** Before heavy imports execute, `ensure_supported_environment()` verifies if `PyQt6` is importable. If invoked under an unconfigured Python interpreter, it probes candidate runtimes (`py -3.11`, `py -3.12`, or `.venv`), logs a clear notice to `stderr`, and seamlessly forwards execution with all arguments preserved. Under `pytest` or non-script contexts, it returns immediately.
2. **Graceful Sensorium fallbacks (`core/sensorium.py`):** `psutil` is guarded with `try...except ImportError: psutil = None`. If missing, telemetry methods provide safe fallback metrics (`cpu_percent=0.0`, `ram_percent=0.0`, `battery_percent=None`) so the perception engine never crashes on boot.
3. **Clean Live session reconnect & warning suppression (`core/audio_io.py`, `core/qt_bootstrap.py`, `main.py`):**
   - Normal Gemini Live server session rotations (`server GoAway (time_left=50s)`) are recognized as expected control-flow transitions; `_LiveReconnect` is re-raised cleanly without printing unhandled tracebacks.
   - `_live_error_reason` in `main.py` unwraps `ExceptionGroup` to report the underlying disconnect reason.
   - Non-critical SDK warnings (`Revert to STA COM threading mode`, `Direct use of automatic function calling`, `there are non-data parts in the response`) are suppressed during startup via `configure_warning_filters()`.
   - `actions/weather_report.py` gains resilient dual-provider telemetry (wttr.in with Open-Meteo fallback).

### D46 — Tier 2 External Orchestrator Connector & Settings Hub Agent Bridge UI (ADR-018)

External agent fleet orchestrators (e.g. Paperclip REST services and Orca ADE CLI) expand Brahma's reach across multi-agent workflows while strictly preserving the single permission boundary of `AgentRuntime.execute` (ADR-018):

1. **Non-Blocking Settings Hub UI Integration (`core/ui_agent_panel.py`):**
   - Decoupled from `ui.py` into a leaf component (`AgentBridgeCard`) wired into `core/ui_connectivity.py`.
   - Runs asynchronous background discovery scans via `_AsyncBridgeWorker` (QThread) so GUI rendering and live audio are never blocked by filesystem or loopback socket probes.
   - Provides instant visual status indicators, enabled/disabled toggles, and live diagnostic re-probe actions.

2. **Tier 2 Orchestrator Adapter (`core/agent_bridge/adapters/orchestrator.py`):**
   - Implements `OrchestratorAdapter` supporting Paperclip (REST API) and Orca ADE (CLI).
   - **Strict Loopback Self-Target Recursion Defense:** Rejects calls or probes targeting Brahma's own Gateway port (`BRAHMA_GATEWAY_PORT`, default 8000) with `ForbiddenTarget` to prevent recursive agent-calling-agent deadlocks or infinite invocation loops.
   - **Per-Verb Mutation Approval Gating:** High-leverage mutating verbs (`create_issue`, `run_heartbeat`, `assign_goal`) are classified as `write` with `requires_approval=True`, ensuring human confirmation before state changes. Read-only inspection verbs (`list_agents`, `get_status`, `list_worktrees`) are classified as `read` with `requires_approval=False`.
   - **Tolerant Schema Parsing:** Parses JSON payloads into structured `data` dicts while retaining raw text output in `AgentCallResult` and persisting durable artifact references when output thresholds are exceeded.
   - Fully guarded by `tests/test_agent_orchestrator_adapter.py` (10/10 passing) and `tests/test_ui_agent_panel.py` (2/2 passing).

### D20 — Parallel dispatch is whole-turn and read-only (superseded by D30, then D32)

Model turns often contain several `read_file`/`glob_files`/`grep_files` calls,
and running them one after another wastes wall-clock time. The loop now runs
such a turn on a small `ThreadPoolExecutor`, but the decision is deliberately
all-or-nothing: **every** call must be a policy-`allow`ed, non-repeated `read`
spec. A single write, `exec`, native tool, or approval prompt serialises the
whole turn. Per-call scheduling would interleave side effects and stack
approval banners; this keeps the semantics obvious. `max_parallel_tools`
(default 4, CLI `--max-parallel-tools`, `1` disables) caps the pool. Results
are read back with `future.result()` in submission order, never as they
complete, because the transcript requires one `tool` message per `tool_call` in
model order.

### D21 — A cancelled turn still answers every call

Cancellation is checked between calls, so a turn can be interrupted after the
assistant message and several `tool_call` events have already been emitted.
Dropping the remaining calls would leave `tool_calls` without matching `tool`
messages, and every OpenAI-compatible endpoint rejects that on the next request
— including a CLI `resume` of the saved session. Undispatched calls therefore
receive `CANCELLED_TOOL_OUTPUT` as their result and the run stops with
`cancelled`. The same pairing guarantee now applies to parallel reads whose
siblings finish after a Stop press. `AgentLoop` also defaults its cancel event
to `runtime.cancel_event`, so a host that forgets `cancel=` still gets
cancellation.

---

## 4. Verification

Environment: Windows, CI interpreter **Python 3.11** (`py -3.11`). The `.venv`
and the default `python` are 3.14 and lack `ruff`/`pytest`.

```
py -3.11 -m ruff check .                 # All checks passed!
py -3.11 -m pytest tests -q              # 888 passed, 1 skipped  (QT_QPA_PLATFORM=offscreen)
py -3.11 -m pytest tests -q -k agent     # 460 passed, 1 skipped
py -3.11 -c "import main; ..."           # 60 voice declarations; six code tools, no run_subagent
py -3.11 -m agent tools                  # lists the eight tool specs (load_project_skill included)
py -3.11 -m agent permissions show       # reports built-in defaults
py -3.11 -m agent skills --json          # discovered roots, skills and diagnostics
```

Notes:

* The full suite needs `$env:QT_QPA_PLATFORM="offscreen"`.
* `import main` prints a pywinauto COM-threading warning on stderr; it is
  pre-existing and harmless. The import was used to assert
  `len(TOOL_DECLARATIONS) == 60` and that
  `{read_file, write_file, edit_file, glob_files, grep_files, run_command}` are
  all present, while `run_subagent` is intentionally absent from the voice
  surface (the text and CLI loops advertise it via `all_specs()`).
* The 460 harness & bridge tests run without a display or a model:
  `py -3.11 -m pytest tests -q -k agent`. The one skip is the POSIX-only
  `prepare_stream` test, which a Windows run cannot execute.
* `python -m agent run --provider stub --json` is the cheapest end-to-end
  smoke test (used by `test_agent_cli`).
* The stdlib-only harness was also run on **3.12.7** and **3.14.7** (same
  commands, same result) before the CI matrix was added; the matrix pins
  3.11/3.12/3.13 so the floor and forward compatibility stay honest.
* Cancellation and retries were verified by `tests/test_agent_retry.py`
  (16 tests) plus the loop's existing cancellation tests.
* Instruction discovery and the approval tri-state are covered by
  `tests/test_agent_prompts.py` (7) and `tests/test_agent_approval.py` (10).
* Path-dependency dispatch (D32) and protocol-safe cancellation are covered by
  `tests/test_agent_parallel.py` (14): unrelated reads and a write overlap,
  writes never overlap each other, a same-path read→write and write→read pair
  keeps model order, a write below a globbed directory waits for the scan,
  approval and deny calls fence without serialising the turn, repeats are
  skipped without a worker, the cap holds, results keep model order, and
  cancelled calls get fillers.
* Approval detail and path-scoped approvals are covered by
  `tests/test_agent_approval_detail.py` (10); the banner's remember-label hint,
  diff box, summary and fold/restore toggle are checked in
  `tests/test_confirmation_overlay.py` (6).
* Provider usage and policy hot-reload are covered by
  `tests/test_agent_usage.py` (11) and `tests/test_agent_policy_reload.py` (8):
  unknown vs zero, summed totals only from real halves, mid-session edits,
  malformed edits keeping the last good rules, the throttle, and a deny that a
  remembered approval cannot widen.
* Path-driven subdirectory instructions are covered by
  `tests/test_agent_instructions.py` (15): ancestor-chain order, directory vs
  file paths, ignored directories, outside paths, dedupe across turns, depth
  and character budgets, and the system-prompt injection the next request sees.
* Streaming is covered by `tests/test_agent_streaming.py` (30): text and
  fragmented tool-call assembly, usage-only tails, error chunks, SSE parsing and
  HTTP error bodies, the stub transport, loop `delta` / `reset` /
  `stream_fallback` events, rejection fallback vs transient retry vs
  post-output failure, and the CLI printer's no-duplicate rule.
* Provider adapters are covered by `tests/test_agent_providers.py` (5): the
  custom and OpenRouter completers build against `llm_client.client` (the old
  `ai_client` import fails the fake), and the shared custom stream posts the
  right URL, headers and payload with per-call settings reload.
* The GUI session bridge is covered by `tests/test_agent_session_bridge.py`
  (12): system-row stripping, prefix-only appends on resume, transcript
  filtering, one-line titles, picker/summary labels, and voice-turn archiving
  (whitespace normalisation, blank-side skipping, turn counts, replay and the
  seed a following typed turn sees, plus a broken store returning `0`). It is
  pure Python so the harness CI job still needs only pytest.
* Banner diff folding is covered by `tests/test_agent_diff_preview.py` (10):
  real-diff detection vs a `--- before ---` heading, add/remove/hunk counts,
  context retention and `… N unchanged` markers, zero-context folding, and the
  summary string. Still pure Python, still no Qt in the harness CI job.
* Subagents are covered by `tests/test_agent_subagent.py` (13): report-only
  return, the read-only default surface, no recursion, requested-tool
  intersection, fresh child context and briefing, the shared policy turning a
  write into a report, exception-to-report, the turn clamp, the handler's
  refusal without a loop, relayed child events, and empty/unknown-request
  refusals. A refused subagent call is the turn's only call, so it ends at
  `permission_denied` exactly like any other all-refused turn; no child starts.
* Project skills are covered by `tests/test_agent_skills.py` (26): frontmatter
  parsing (quoted scalars, block/inline lists, derived name/description, BOM,
  invalid or mismatched names, the size cap), discovery order and shadow
  diagnostics, `BRAHMA_SKILLS_DIR` override/disable, project-root ordering and
  the user-root split, metadata-only listing, resident-block budgets, prompt
  ordering, the loader through the runtime, and a guard that the harness name is
  `load_project_skill` and never shadows the core `load_skill`. Two CLI tests
  cover `agent skills` text and JSON output.
* The shared project store is covered by `tests/test_skills_library.py` (33):
  project roots are catalogued last (so a project overrides builtin and user
  copies), the nearest root wins, `executable: true` stays instructional and
  publishes nothing, a name/id mismatch or a non-harness file is skipped, and
  `configure_library(project_workspace=...)` follows the workspace and drops the
  old roots. One drift guard asserts the duplicated name pattern equals
  `agent.skills.NAME_RE`. `tests/test_skills_bridge.py` (24) loads a project
  skill end to end through `search_skills`/`load_skill`.
* The shared project prompt is covered by `tests/test_agent_prompts.py` (10):
  `project_context` composes the instruction walk and the skills catalogue,
  returns `""` for a missing workspace or one with neither file, and is the
  exact workspace half of `build_system_prompt`. The two host wirings sit
  outside the stdlib-only harness job because they import the host modules:
  `tests/test_gui_prompt_wiring.py` (4, imports `main`) checks
  `_agentic_seed_messages` in both discovery branches, the empty workspace, and
  that the runtime workspace wins over the process cwd;
  `tests/test_voice_prompt_wiring.py` (2, imports `core.session_lifecycle`)
  checks the assembled `LiveConnectConfig.system_instruction`.
* Command containment is covered by `tests/test_agent_process.py` plus one
  test in `tests/test_agent_code_tools.py`: output and exit code, stderr,
  timeout with partial output, closed stdin, a grandchild that does not survive
  the timeout, and (Windows only) proof that the job object is actually
  assigned rather than silently falling back to `taskkill`.
* `exec.sandbox` is covered by `tests/test_agent_sandbox.py` (18),
  `tests/test_agent_process.py` (5 more) and three runtime tests in
  `tests/test_agent_code_tools.py`: secret-name component boundaries, `env_keep`
  case handling, withheld-count reporting, prefix execution as one argument with
  no shell, `{command}`/`{cwd}` shaping, malformed values failing at policy load,
  the nested merge keeping `env_scrub`, `describe()` exposing the section, and a
  Windows memory limit stopping a large allocation.
* Approval colour is covered by `tests/test_agent_term.py` (12, 11 run on
  Windows): diff/rule/label line rules, `+++`/`---` headers not being mistaken
  for changes, plain lines untouched, TTY/`NO_COLOR` (including an empty
  value)/`TERM=dumb`/`FORCE_COLOR` policy, the plain prompt being byte-identical
  to the historical text, and the coloured prompt stripping back to it exactly.
* MCP is covered by `tests/test_agent_mcp.py` (58), all against a real fake
  stdio server: config defaults/validation/discovery and relative `cwd`, name
  sanitising/truncation/collision, listing and calling through a `ToolSpec`,
  text/binary/resource/structured/error rendering, per-request timeout, a crash
  failing the pending call with the stderr tail, per-server start failure,
  allow/deny/per-tool policy through the runtime, spec unregistration on close,
  `mcp list` JSON, no-config and broken-config behaviour, workspace-manager
  reuse/restart/close across workspace and config changes, duplicate-free
  merging into the model's first tool surface, and in-place refresh: a latched
  `tools/list_changed` re-lists without restarting, a killed server is
  restarted exactly once and its tools come back, a second unchanged server is
  left alone, a failed re-list drops that server's tools and reports the error
  (with the stderr tail) exactly once, a healthy session is never refreshed,
  and refresh leaves no duplicate or orphaned specs.
* The GUI-only MCP wiring is covered by `tests/test_gui_mcp_wiring.py` (3):
  the first-surface merge, a broken config degrading to the built-in tools with
  one error event, and the process-wide manager identity. It imports `main`,
  so it is deliberately not named `test_agent_*` and stays out of the
  stdlib-only harness CI job.

---

## 5. Known gaps / next phases

Ordered by value, with the honest reason each was left out. The tracked,
re-ranked version of this list — with code evidence, acceptance criteria and
tests for each item — lives in
[`AGENTIC-HARNESS-NEXT.md`](AGENTIC-HARNESS-NEXT.md). Update both when a batch
lands; this section stays the historical summary.

1. **No built-in filesystem/network confinement (addressed in D43 / O-1).**
   Timeouts end the whole process tree, credential-shaped variables are withheld,
   and `sandbox doctor` provides active behavioural probing (`write_outside`,
   `network_egress`, `uid_isolation`) backed by `exec.sandbox.require_confined`.
2. **Subagents are sequential and share the parent's model (addressed in D44 / O-2).**
   `run_subagent` remains sequential for mutation to protect the undo stack, while
   `run_subagent_batch` provides read-only parallel fan-out under strict workspace
   confinement and single-runtime authority.
3. **Streaming is not universal.** The local, custom, OpenRouter and stub
   transports stream in both the CLI and the GUI text path; only the voice path
   does not, because Gemini's live socket owns its delivery. A custom server
   that rejects `stream: true` falls back to one plain request (see D29). The
   assembler follows the protocol (argument fragments are concatenated), so a
   non-compliant server that repeats a full arguments blob would produce
   invalid JSON.
4. **MCP is tools-only and refreshes only at the turn boundary.** The client
   implements `initialize`, `tools/list`, `tools/call` and acts on
   `notifications/tools/list_changed`; resources, prompts and sampling are left
   out because a half-supported capability produces tools that fail on first
   use. There is no background watcher: the GUI's `WorkspaceMCP` re-lists a
   changed server and restarts a dead one on the next `ensure()` (D41), so a
   change mid-turn lands on the following turn. A re-list that fails drops that
   server's tools and reports once; a server that failed its very first start is
   retried only when the config file changes. The CLI has no turn to refresh at
   — its session lives for one command — and the live voice path still uses its
   own SDK-based manager.
5. **Project skills are instructional, not executable.** `load_project_skill`
   returns text the model reads; there are no scripts, no per-skill tools and no
   `allowed-tools` enforcement (the policy is still the only boundary). There is
   no watcher: discovery runs when a prompt is built and when the loader is
   called, so a skill added mid-run appears on the next turn. Since D40 the
   catalogue also reaches the HUD text and Gemini Live prompts; before that it
   was in the CLI and child-loop prompts only. Since D42 the same
   `.brahma/skills` roots are also the highest-precedence source in the core
   Skills Library, so `search_skills`/`load_skill` (voice and GUI) see them;
   the two user-level directories stay separate, and a project skill that
   claims `executable: true` is still loaded as text.

---

## 6. How to resume this work

Read in this order: `AGENTIC-HARNESS.md` §2–§6, then `agent/runtime.py`, then
`agent/loop.py`. That is enough to change anything safely.

Practical loop:

```
py -3.11 -m pytest tests -q -k agent
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
```

Gotchas that cost time the first time:

* Run everything with `py -3.11`. The default `python` is 3.14 and cannot run
  the dev gates.
* `python -m agent` will not import PyQt — do not "fix" the empty
  `agent/__init__.py`.
* The loop's transcript must keep the OpenAI tool protocol: one `tool` message
  per call, in order, right after the assistant message. Compaction is the only
  place allowed to cut, and it walks past `tool` messages.
* `ask_blocking` returns `False` on the Qt main thread. If an approval
  "mysteriously fails", check the thread first.
* `run_command` timeouts are clamped to 1800 s; tests use 1 s.
* Isolation for `run_command` is read from `exec.sandbox` by
  `agent/sandbox.py` only. Do not add a second scrub or prefix path in the tool;
  the default `env_scrub=True` is why a command may no longer see
  `OPENAI_API_KEY` — that is intended, and `env_keep` is the exception.
* MCP lives in `agent/mcp.py` only. Do not reuse `core/mcp_manager.py` (SDK +
  asyncio) and do not spawn a server anywhere else: spec registration and
  `close()` unregistering are what keep the catalogue honest. The config is
  trusted input, so a new field needs validation in `parse_servers`, not a
  silent default.
* Never register a harness tool under a core meta-tool name (`load_skill`,
  `search_tools`, `recall_memory`, …). The harness branch at the top of
  `core/tool_exec.py` runs first, so the harness spec shadows the legacy one for
  voice and GUI text instead of adding a second tool. This is why the project
  skills loader is `load_project_skill`: the core `load_skill` takes `skill_id`,
  and sharing the name made it demand `name`.
* If `import main` fails after editing `TOOL_DECLARATIONS`, check that the
  harness import block landed *after* the closing `]` and that
  `_pcm_block` still follows it.

If you add a tool: `ToolSpec` + registration + a `tool_class` + tests. Then
decide whether it deserves a pin in `core/tool_index.py` — most do not.

### Reverting

Everything is additive and lands in one commit (`agent: add the agentic
harness`). `git revert <sha>` is enough. Manually:

1. Delete `agent/`, `tests/test_agent_*.py`,
   `config/agent_permissions.example.json`, `docs/AGENTIC-HARNESS*.md`,
   `AGENTS.md`.
2. Revert the integration edits (`main.py` ×3 blocks,
   `core/tool_exec.py` harness branch, `core/confirm.py` additions,
   `core/tool_index.py` pin list, the `.gitignore` entry).
3. The `agent_*` SQLite tables are inert if left behind; dropping them is
   optional.

No existing behaviour was deleted: `_finish_tool_turn` still handles the
no-tool-call case and still propagates `LocalBrainError`, and
`ToolExecutor.execute` falls through to the legacy path for every name it does
not own.
