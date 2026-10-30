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
| Parallel read-only tool calls | Done |
| Cancellation-safe transcript | Done |
| Provider-reported token usage | Done |
| Policy hot-reload (fail-safe) | Done |
| Subdirectory instructions (path-driven) | Done |
| Streaming output (text + tool calls) | Done |
| GUI session persistence + replay | Done |
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
agent/runtime.py        AgentRuntime, configure(), gui_approver, policy_note
agent/loop.py           AgentLoop, AgentResult, stop reasons, event stream
agent/context.py        ContextBudget, ConversationContext, compaction
agent/session.py        AgentSession (agent_* tables), SessionRecord
agent/session_bridge.py Qt-free GUI↔session seed, transcript, picker labels
agent/prompts.py        resident prompt + AGENTS.md/BRAHMA.md loader
agent/providers.py      local/custom/openrouter/stub completers + summariser
agent/streaming.py      delta assembler + SSE transport (stdlib urllib)
agent/retry.py          transient classifier + cancellation-aware backoff
agent/__main__.py       `python -m agent` CLI
```

Modified:

| File | Change |
| --- | --- |
| `main.py` | Appends harness declarations to `TOOL_DECLARATIONS`; `AGENT_MAX_TURNS = 24`; `_finish_tool_turn` now seeds `AgentLoop(first_response=res)` with the turn-one tool surface; new `_agent_event`; `_ensure_agent_session` persists GUI text turns and `/sessions`, `/resume`, `/new` bind or drop the session; `main()` configures the runtime with the developer-mode workspace and the HUD approver. |
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
| `main.py` | The GUI custom path streams through `custom_stream`; streaming is enabled for `source in ("local", "custom")`. |
| `.github/workflows/ci.yml` | `harness-pythons` job: harness suite on 3.11/3.12/3.13 with only pytest installed. |
| `.gitignore` | `agent_permissions.json` ignored — it is machine-local user data. |

New tests: `tests/test_agent_permissions.py`, `test_agent_code_tools.py`,
`test_agent_context.py`, `test_agent_session.py`, `test_agent_loop.py`,
`test_agent_cli.py`, `test_agent_retry.py`, `test_agent_prompts.py`,
`test_agent_approval.py`, `test_agent_parallel.py`, `test_agent_usage.py`,
`test_agent_policy_reload.py`, `test_agent_instructions.py`,
`test_agent_streaming.py`, `test_agent_session_bridge.py`,
`test_agent_diff_preview.py`, `test_agent_providers.py` — all pure-Python
(no display, no model). HUD-only behaviour lives in
`tests/test_confirmation_overlay.py`.

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

### D20 — Parallel dispatch is whole-turn and read-only

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
py -3.11 -m pytest tests -q              # 559 passed  (QT_QPA_PLATFORM=offscreen)
py -3.11 -m pytest tests -q -k agent     # 222 passed
py -3.11 -c "import main; ..."           # 66 declarations, all six tools present
py -3.11 -m agent tools                  # lists the six primitives
py -3.11 -m agent permissions show       # reports built-in defaults
```

Notes:

* The full suite needs `$env:QT_QPA_PLATFORM="offscreen"`.
* `import main` prints a pywinauto COM-threading warning on stderr; it is
  pre-existing and harmless. The import was used to assert
  `len(TOOL_DECLARATIONS) == 66` and that
  `{read_file, write_file, edit_file, glob_files, grep_files, run_command}` are
  all present.
* The 222 harness tests run without a display or a model:
  `py -3.11 -m pytest tests -q -k agent`.
* `python -m agent run --provider stub --json` is the cheapest end-to-end
  smoke test (used by `test_agent_cli`).
* The stdlib-only harness was also run on **3.12.7** and **3.14.7** (same
  commands, same result) before the CI matrix was added; the matrix pins
  3.11/3.12/3.13 so the floor and forward compatibility stay honest.
* Cancellation and retries were verified by `tests/test_agent_retry.py`
  (16 tests) plus the loop's existing cancellation tests.
* Instruction discovery and the approval tri-state are covered by
  `tests/test_agent_prompts.py` (7) and `tests/test_agent_approval.py` (10).
* Parallel dispatch and protocol-safe cancellation are covered by
  `tests/test_agent_parallel.py` (8): overlap, the cap, write/`ask`
  serialisation, submission order, and filler results for undispatched calls.
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
  (8): system-row stripping, prefix-only appends on resume, transcript
  filtering, one-line titles, and the picker/summary labels. It is pure Python
  so the harness CI job still needs only pytest.
* Banner diff folding is covered by `tests/test_agent_diff_preview.py` (10):
  real-diff detection vs a `--- before ---` heading, add/remove/hunk counts,
  context retention and `… N unchanged` markers, zero-context folding, and the
  summary string. Still pure Python, still no Qt in the harness CI job.

---

## 5. Known gaps / next phases

Ordered by value, with the honest reason each was left out.

1. **Subagents / a tool scheduler.** Read-only calls within a turn now run in
   parallel, but the decision is whole-turn: one write or approval prompt
   serialises everything. A per-call scheduler and a subagent layer can be built
   on the event stream.
2. **Streaming is not universal.** The local, custom, OpenRouter and stub
   transports stream in both the CLI and the GUI text path; only the voice path
   does not, because Gemini's live socket owns its delivery. A custom server
   that rejects `stream: true` falls back to one plain request (see D29). The
   assembler follows the protocol (argument fragments are concatenated), so a
   non-compliant server that repeats a full arguments blob would produce
   invalid JSON.
3. **Sandboxing.** `run_command` is `shell=True` with policy as the only
   barrier. A Windows job-object / container option would let `--yolo` be safe
   in more places.
4. **Voice-path session persistence.** Gemini owns the live history, so voice
   turns do not write `agent_*` rows. Unifying them would need a transcript
   bridge from the live session; a GUI text session cannot be replayed into a
   live socket.
5. **The TTY prompt has no colour.** The HUD highlights diffs and folds
   context, but `python -m agent` prints the same detail as plain text. That is
   fine for piping and logs; adding ANSI colour would need a TTY check so a
   redirected run stays parseable.

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
