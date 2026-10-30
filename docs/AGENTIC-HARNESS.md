# AGENTIC HARNESS

How Brahma AI Evo became an agent — a bounded, permissioned, resumable
tool-calling loop — and where every piece lives.

Written for the next engineer (or model) who has to change it without
re-deriving the design. The decision history and verification log are in
[`AGENTIC-HARNESS-STATUS.md`](AGENTIC-HARNESS-STATUS.md).

---

## 1. The problem this fixes

Before this work Brahma had tools, but it was not an agent. Concretely:

| Symptom | Where it lived | Consequence |
| --- | --- | --- |
| One tool round-trip per user turn | `main.py:_finish_tool_turn` ran every call, then took **exactly one** follow-up | The model could never react to a second observation. "Read the file, fix it, run the tests" was impossible in one request. |
| Tools declared in two places | `TOOL_DECLARATIONS` in `main.py` + an `elif` chain in `core/tool_exec.py` | Adding a tool meant editing two ~200 KB files and hoping the strings matched. |
| No file-editing primitive | `actions/file_controller.py` is organized around "put the report on my Desktop" | No line ranges, no exact-string edit, no way to change a repository safely. |
| No shell | — | The agent could not run tests, linters, git, or a build. Unverifiable work. |
| No durable session | `workspace_store.py` persisted UI transcripts that nothing ever read back | Restart was a hard reset; long tasks could not be resumed. |
| No capability policy | `core/confirm.py` gated exactly two OS actions | An agent that can write files and run commands needs a rule engine, not two banners. |
| No headless surface | — | Nothing could be scripted, tested, or supervised by another process. |

The goal was parity with a real coding harness (OpenCode-shaped): loop,
filesystem/shell primitives, context management, sessions, permissions, CLI.

---

## 2. Architecture

```
                        ┌──────────────────────────────────────────┐
   Gemini Live (voice)  │  core/tool_exec.py  ToolExecutor.execute  │
   ───────────────────► │  harness branch: is_registered(name)?    │
                        └───────────────┬──────────────────────────┘
                                        │ asyncio.to_thread(...)
   text path (local /                   ▼
   custom / OpenRouter)   ┌─────────────────────────────────────────┐
   ─────────────────────► │  agent/runtime.py  AgentRuntime.execute │
   main.py:               │  policy → approval → handler            │
   _finish_tool_turn      └───────┬─────────────────────┬───────────┘
                                  │                     │
                     agent/spec.py registry      native_dispatch
                     (six code tools)            (pre-existing actions)
                                  │                     │
                                  ▼                     ▼
                        agent/code_tools.py      actions/*.py

   headless:  python -m agent run|resume|sessions|tools|permissions
              └── agent/__main__.py → agent/loop.py → same runtime
```

Three invariants make the rest of the design work:

1. **One dispatch boundary.** Every tool call — voice, text, CLI — passes
   through `AgentRuntime.execute`. Permission enforcement cannot be bypassed by
   choosing a different entry point.
2. **One tool description.** A tool is a `ToolSpec` in `agent/spec.py`
   (name, model-facing schema, capability class, handler). The registry is the
   single source of truth; `main.py` derives its Gemini declarations from it.
3. **One loop.** `agent/loop.py` is used by the text path and the CLI. The voice
   path keeps Gemini's own session loop but shares tools, policy and approval.

### Request flow (text path)

1. The user speaks/types. `BrahmaLive` picks a provider and gets a completion.
2. If the completion has `tool_calls`, `_finish_tool_turn` builds a
   `ConversationContext` from the existing message list and seeds an
   `AgentLoop` with the response the caller already paid for
   (`run(first_response=res)`) and the tool surface turn one advertised. Reusing
   that surface keeps the native actions and the `search_tools` / `call_tool`
   meta-tools callable; the harness primitives are merged in so an unpinned one
   (glob/grep/write_file) stays available even when the resolver did not rank
   it.
3. The loop asks the model again, executes calls through the runtime, appends
   `tool` messages, and repeats until the model stops calling tools.
4. Events stream out (`_agent_event`) and, if a session is attached, into
   SQLite.
5. The final assistant text is spoken.

---

## 3. Module map

| Module | Responsibility | Read this if… |
| --- | --- | --- |
| `agent/spec.py` | `ToolSpec`, `ToolContext`, registry, schema conversion, output capping, exception-to-string at the tool boundary | …you are adding a tool. |
| `agent/permissions.py` | Policy document, defaults, `evaluate()`, path scoping, headless rules | …a call was allowed/denied and you need to know why. |
| `agent/code_tools.py` | `read_file`, `write_file`, `edit_file`, `glob_files`, `grep_files`, `run_command` | …you are changing a primitive's behaviour. |
| `agent/runtime.py` | The single dispatch boundary; approval callbacks; process-wide `configure()` | …you are wiring the harness into a new host. |
| `agent/approval_detail.py` | Banner text: rule, command/cwd, unified diffs, and the ALWAYS ALLOW label | …an approval banner is unhelpful or names the wrong thing. |
| `agent/usage.py` | Provider-reported token counts, normalised and summed | …you need real cost numbers instead of the local estimate. |
| `agent/streaming.py` | Delta assembler (text + fragmented tool calls) and an SSE transport over `urllib` | …streamed text or a tool call is garbled. |
| `agent/loop.py` | Bounded agent loop, repeat detection, stop reasons, event stream | …the agent stops early or will not stop. |
| `agent/context.py` | Character budget, protocol-safe compaction, output truncation | …context is overflowing or a resumed prompt is rejected. |
| `agent/session.py` | Durable messages/events in SQLite; list/open/resume | …resume loses messages. |
| `agent/session_bridge.py` | Qt-free bridge between a stored session and the GUI's `[system, history, user]` seed | …the HUD restarts from a hard reset or a resumed turn duplicates history. |
| `agent/prompts.py` | Resident system prompt + `AGENTS.md` / `BRAHMA.md` loader, path-driven subdirectory discovery | …the model behaves against project conventions. |
| `agent/providers.py` | Loop-compatible completers for local/custom/OpenRouter/stub; shared `custom_stream`; summariser | …tool calling fails on one provider only. |
| `agent/retry.py` | Transient-error classifier, exponential backoff with jitter, cancellation-aware wait | …a run stalled on a 503 or retried something it should not. |
| `agent/__main__.py` | `python -m agent` CLI | …you need a scriptable surface. |

`agent/__init__.py` is deliberately almost empty so `python -m agent` does not
import PyQt, the tool index, or a provider client. Import submodules directly.

### Touch points outside `agent/`

| File | Change |
| --- | --- |
| `main.py` | `TOOL_DECLARATIONS.extend(_agent_gemini_declarations())`; `AGENT_MAX_TURNS = 24`; `_finish_tool_turn` now runs the loop; `_agent_event` mirrors telemetry; `_ensure_agent_session` and `/sessions`, `/resume`, `/new` bind the GUI text path to durable sessions; `main()` configures the runtime (workspace + HUD approver). |
| `core/tool_exec.py` | Harness branch at the top of `ToolExecutor.execute`: registered tools go to the runtime in a worker thread, then fall through to the legacy path. |
| `core/confirm.py` | `is_bound()`, `ask_blocking()`, `_on_qt_main_thread()`, and waiter/verdict plumbing on `_Pending`. |
| `core/tool_index.py` | `read_file`, `edit_file`, `run_command` added to `DEFAULT_PINNED_TOOLS`. |
| `ui.py` | `Ctrl+Shift+X` → `MainWindow._cancel_agent()` → `agent.runtime.cancel()`. |
| `core/diff_preview.py` | Finds the diff in an approval detail, counts it, and folds unchanged context for the banner's CHANGES ONLY view. Pure Python. |
| `core/ui_overlays.py` | `DetailHighlighter` colours diff lines; the banner adds the diff summary and fold toggle. |
| `setup.py` | Refuses to run below Python 3.11 with a readable message. |

---

## 4. The loop contract

`AgentLoop.run()` returns an `AgentResult`:

```python
AgentResult(
    text,          # final assistant text (or an explanation of the stop)
    turns,         # model calls made
    stop_reason,   # see below
    messages,      # the exact OpenAI-shaped transcript
    events,        # ordered telemetry
    session_id,
    usage,         # turns / tool_calls / compactions / retries / context_chars /
                   # estimated_tokens / prompt_tokens / completion_tokens /
                   # total_tokens / tokens_reported
)
```

`estimated_tokens` is a local estimate from the transcript size; it drives
compaction and is available even when a provider reports nothing.
`prompt_tokens` / `completion_tokens` / `total_tokens` are the **provider's own
counts**, summed across turns by `agent/usage.py`. They stay `0` with
`tokens_reported is False` when no response carried usage — "unknown" is never
rendered as "zero spent". The CLI footer prints the total only when it is real.

### Provider usage

`extract_usage(response)` accepts the three shapes in use — OpenAI
`{"usage": {...}}`, Gemini `{"usage_metadata": {...}}`, and SDK objects with
attributes — and returns `{}` rather than guessing. A missing total is summed
only when both halves were reported; a partial count is kept without inventing
the rest. Each `assistant` event carries that turn's counts under `usage` when
the provider supplied them.

### Stop reasons

| Reason | Meaning | Caller should |
| --- | --- | --- |
| `completed` | A turn produced no tool calls | Speak `text`. |
| `max_turns` | Hit the turn budget | Surface it; the task may be half-done. |
| `repeat_loop` | A whole turn was identical calls already past `repeat_limit` | Surface it; the model is stuck. |
| `cancelled` | `cancel` event set between turns/calls | Treat as user-initiated. |
| `error` | Provider or unexpected exception | Report `text`; it contains the exception. |
| `permission_denied` | **Every** call in a turn was refused | Tell the user what needs approving; do not retry. |

### Guardrails

* **Turn budget.** `max_turns` (GUI: `AGENT_MAX_TURNS = 24`; CLI: `--max-turns`,
  default 40) is enforced by the loop, not by prompt instruction.
* **Repeat detection.** Signature is `name + canonicalised JSON arguments`
  (sorted keys). After `repeat_limit` (default 3) the call is short-circuited
  with a "change your approach" message; a turn made entirely of skipped calls
  ends the run with `repeat_loop`. Prompt rules alone do not stop this.
* **Cancellation.** Checked before each turn, each call, and during a retry
  backoff. The flag is a `threading.Event` on `AgentRuntime`
  (`runtime.cancel()` / `reset_cancel()`), and `AgentLoop` uses it by default
  when no `cancel=` is passed. The loop never kills a thread, so an in-flight
  file write or command finishes instead of being torn in half. Calls that were
  emitted to the model but never dispatched still receive a `tool` message
  (`CANCELLED_TOOL_OUTPUT`) so the transcript remains resumable. Sources: Ctrl-C
  in the CLI (first press cancels, second aborts with exit `130`),
  `Ctrl+Shift+X` in the GUI, or `agent.runtime.cancel()` from any host. A new
  turn calls `reset_cancel()` so a stale Stop press cannot kill it.
* **Provider retries.** `AgentLoop` routes every model call through
  `agent/retry.py`, so both the CLI and the GUI get it. Transient statuses
  (`408`, `425`, `429`, `500`, `502`, `503`, `504`), timeouts, resets, and
  "overloaded"/"rate limit" messages are retried; `401`/`403`/`404`/`422`, a
  malformed request, and `connection refused` are not — retrying cannot change
  those answers and a dead local port should fail fast. Defaults: 3 attempts,
  0.8 s base delay doubling to an 8 s cap plus up to 25 % jitter. Each retry
  emits a `retry` event and increments `usage["retries"]`.
* **Compaction.** When `context.chars > budget.max_chars`, the oldest messages
  are folded into one summary. The cut walks forward past `tool` messages so an
  `assistant` `tool_calls` block is never orphaned — that would be an HTTP 400
  from every OpenAI-compatible endpoint. The summary replaces the stored
  transcript, so resume does not re-send what was already summarised.
* **Errors never unwind the turn.** A raised handler becomes
  `Tool 'x' failed: …`; a raised completer becomes `stop_reason=error`. The
  loop owns the boundary.

### Parallel read-only calls

A turn that contains two or more calls may run them concurrently — but only
when **every** call is a policy-`allow`ed, non-repeated `read` spec
(`read_file`, `glob_files`, `grep_files`). One write, one `exec`, one native
tool, or one `ask` anywhere in the turn serialises the whole turn. That is
deliberate: stacked approval banners and interleaved side effects are worse
than the latency saved, and `native_dispatch` is single-threaded.

`max_parallel_tools` (GUI default 4; CLI `--max-parallel-tools`, `1` disables)
caps the pool. Results are collected with `future.result()` in **submission
order**, never `as_completed`, because the transcript must contain one `tool`
message per `tool_call` in model order. `tool_start` / `tool_end` events may
interleave between workers; `tool_call` and `tool_result` events stay ordered.
An event sink must therefore tolerate being called from a worker thread (the
CLI writes one line per event; the GUI's sink re-emits through a Qt signal).

### Streaming output

Streaming is **opt-in and additive**. The loop contract stays one assistant
message per call; `stream=True` only changes when the text becomes visible. A
completer may expose a callable `stream` attribute:

```python
complete(messages, tool_schemas) -> assistant message
complete.stream(messages, tool_schemas, on_delta) -> assistant message
```

The loop uses `stream` instead of `complete` when it exists. A completer
without one is still called normally and the run emits a single
`stream_fallback` event saying why — never a crash, never a silent downgrade.

`agent/streaming.py` owns the two halves of the work:

* `StreamAssembler` merges OpenAI `choices[0].delta` chunks. Text accumulates
  in order; `tool_calls` merge **by `index`** and their `arguments` strings are
  concatenated, because each delta carries a fragment of the JSON. The result
  is sorted by index and shaped like any other assistant message, so the
  transcript, repeat detection and session store do not know streaming
  happened. A usage-only tail chunk (`choices: []`) updates usage and emits
  nothing.
* `openai_stream(url, payload)` posts the request with `urllib` and parses
  server-sent events. It is stdlib-only on purpose: the harness CI installs
  only pytest, and a custom endpoint or OpenRouter must stream without pulling
  `requests` (or an SDK) into `agent/`.

Each update is normalised to one of two deltas and forwarded as a `delta`
event:

| delta `kind` | Fields | Meaning |
| --- | --- | --- |
| `text` | `text` | Append this to the visible answer. |
| `tool_call` | `index`, `id?`, `name?`, `arguments?` | A fragment of a call being assembled. |
| `reset` | `reason` | Drop what you have shown for this turn. |

`reset` matters because retries happen **after** the first token: if a stream
fails mid-answer and the request is retried, the UI is holding text that will
never be completed. `on_retry` emits `reset` before the `retry` event, and the
assembler starts empty on the next attempt, so the finished `assistant` event
and the transcript contain the successful attempt only.

A stream can also be **rejected** before it produces anything — typically a
server that answers `400` to `stream: true`. That is not a transient failure and
retrying cannot fix it, so the loop makes exactly one plain `complete` call and
emits `stream_fallback` naming the error. The run stays on the non-streaming
path from then on. A failure *after* the first delta is different: falling back
would duplicate text the user already saw, so it propagates as a normal stop.
Transient errors (`timeout`, `429`, `5xx`) still go to the retry wrapper first,
so a flaky network is retried rather than downgraded. `AgentLoop` checks
`self._streamer` on each attempt, not the closure, so a retry after a fallback
does not reach for the rejected transport again.

The CLI's `--stream` renders deltas as they arrive, suppresses the duplicate
`assistant` text for that turn, and prints an explicit "partial output
discarded" line on reset. `--json` emits every `delta` unchanged, which is the
hook a TUI or an editor plugin uses. The GUI streams on both OpenAI-compatible
text paths: local uses `local_brain.generate_chat_stream`, custom reuses
`agent.providers.custom_stream` (the same helper as the CLI adapter). The voice
path does not stream: Gemini's live socket has its own delivery.

### Protocol shape

Every assistant message carrying `tool_calls` is followed by exactly one
`tool` message per call, in order, each with the matching `tool_call_id`.
`normalise_assistant_message()` accepts three provider shapes:

* `{"choices": [{"message": {...}}]}` — OpenAI / local / OpenRouter
* `{"message": {...}}`
* `{"content": ..., "function_call": {...}}` — legacy single-call

Arguments are always normalised to a JSON **string** in the transcript, as the
OpenAI protocol requires.

Tool *lists* are normalised the same way. Entries may be `ToolSpec` objects,
registered names, OpenAI `{"type": "function", ...}` objects, or bare Gemini
declarations (`name` at the top level); the last is converted to the OpenAI
shape with lowercase schema types. `AgentLoop(tools=TOOL_DECLARATIONS)` is
therefore valid.

---

## 5. The tool contract

```python
ToolSpec(
    name="edit_file",
    description="...",                # model-facing, one paragraph
    parameters={...},                 # canonical lowercase JSON schema
    tool_class="write",               # read | write | exec | network | device | native
    handler=edit_file,                # (args: dict, ctx: ToolContext) -> str
    tags=("code", "files"),
    requires_approval=False,          # escalate allow -> ask
    max_output_chars=12_000,
)
```

* **Canonical schema is lowercase.** `ToolSpec.gemini()` uppercases `type`
  values on the way out; nothing else converts. Gemini is the odd format, so
  the conversion lives at the edge.
* **Handlers are pure.** Everything ambient comes from `ToolContext`
  (`workspace`, `timeout`, `max_output_chars`, `policy`). They never read
  settings or globals, which is what makes them testable against `tmp_path`.
* **Containment is not the handler's job.** `ToolContext.resolve_path` resolves
  against the workspace but may return an outside path; the policy decides
  whether that is asked about or denied. Silently rewriting an escape would
  hide the attempt from the user.
* **Output is capped twice.** At the tool boundary (`spec.execute`) and again
  when it enters the transcript (`context.truncate_tool_output`, which keeps
  the tail — test runners put the verdict there).

### The six primitives

| Tool | Class | Behaviour worth knowing |
| --- | --- | --- |
| `read_file` | read | Line-numbered; `start_line`/`end_line`; refuses binaries and >2 MB files; returns a sentence instead of raising. |
| `write_file` | write | Creates parents; `overwrite=false` refuses to clobber; pushes an undo entry; returns a unified diff on overwrite. |
| `edit_file` | write | Exact-string replace; **requires uniqueness** unless `replace_all=true`; explains whitespace-only mismatches; pushes undo. |
| `glob_files` | read | Newest first; skips `node_modules`, `.venv`, `.git`, build dirs, caches. |
| `grep_files` | read | Python regex; `path:line: text`; glob filter; caps files and matches. |
| `run_command` | exec | `shell=True` in the workspace; exit code + stdout + stderr; timeout (default 120 s, max 1800 s); output capped. |

Edits and writes register with **`core/undo.py`**, so the existing `undo` tool
reverses agent changes too. That is the recovery path, which is why writes are
not gated behind a confirmation by default.

---

## 6. Permissions

Policy file: `agent_permissions.json`. Resolution order:

1. explicit path (`--permissions`, `PermissionPolicy.load(path)`)
2. `BRAHMA_AGENT_PERMISSIONS` environment variable
3. `%LOCALAPPDATA%\BrahmaAI\config\agent_permissions.json`
4. `config/agent_permissions.json` in the repo
5. built-in defaults

Missing files are fine; defaults apply. A **malformed** file raises at load —
a typo in a decision must fail loudly, not at the moment an agent tries to
delete something. An `agent_permissions.json` in the repo is gitignored (it is
machine-local user data); only `config/agent_permissions.example.json` is
tracked.

### Hot reload

`AgentRuntime.execute` calls `reload_policy()` first, so editing the policy
file lands without restarting Brahma. The check is throttled
(`policy_check_interval`, default 1 s) and compares the file's `st_mtime_ns`;
a policy built from a dict with no `source` is never re-read. `force=True`
skips both the throttle and the mtime comparison (tests, or an explicit
"reload now" control).

A reload is **fail-safe**: if the new file is malformed, the last good policy
stays in force and a `policy_reload` event with `ok=False` and the parser error
is emitted. Session approvals are not cleared — they are still consulted only
after `evaluate()`, so a reload that turns `ask` into `deny` takes effect
immediately and cannot be widened by a remembered "always allow".

### Schema

```json
{
  "version": 1,
  "default": "allow",
  "classes": {
    "read": "allow",
    "write": "allow",
    "exec": "ask",
    "network": "allow",
    "device": "allow",
    "native": "allow"
  },
  "tools": {
    "run_command": "deny",
    "write_file": { "decision": "ask", "reason": "repo is read-only today" }
  },
  "paths": {
    "outside_workspace": "ask",
    "protected": "ask"
  },
  "protected_globs": [".git/config", ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*.pfx"],
  "exec": {
    "deny_patterns": ["rm\\s+-[a-z]*r[a-z]*f[a-z]*\\s+/(?:\\s|$)", "..."],
    "allow_patterns": []
  },
  "headless": {
    "exec": "deny",
    "outside_workspace": "deny"
  }
}
```

`tools.<name>` accepts either a bare decision string or
`{"decision": ..., "reason": ...}`. Partial documents merge over the defaults —
you only write what you want to change.

### Resolution order (first match wins)

1. `tools.<name>` — explicit per-tool rule. An `ask` here still becomes `deny`
   when no approver exists.
2. **Hard deny** — an `exec` command matching `exec.deny_patterns`
   (`allow_patterns` short-circuits first). Deliberately narrow: this is a
   tripwire for the catastrophic (`rm -rf /`, `mkfs`, `format c:`, fork bomb,
   `reg delete hklm`), not a sandbox.
3. **Path scoping** — for every path-bearing argument (`path`, `file`, `cwd`,
   `target`, …), resolve it against the workspace:
   * outside the workspace → `paths.outside_workspace` (or
     `headless.outside_workspace`)
   * matching a protected glob → `paths.protected`
4. `headless.<class>`, when this runtime has no human to ask.
5. `classes.<class>`
6. `default`

Finally, `requires_approval=True` on a spec escalates `allow` → `ask` (and
`deny` when headless).

### `ask` vs `deny`

* `ask` goes to the approver. The model receives *"a human must approve this
  before it runs; do not claim it happened"* and the loop counts it as a
  denial for the `permission_denied` stop.
* `deny` is final. The model receives *"hard policy denial; do not retry"*.

### Session approvals ("always allow")

A human may answer an `ask` with **always allow** instead of a one-off yes. The
runtime remembers the approval in memory for the rest of the process:

* `run_command` is remembered by the **exact command string**, so approving
  `pytest -q` does not approve `del /s`;
* a **path-based** ask (`paths.outside_workspace`, `paths.protected`) is
  remembered by the **resolved path**, so approving one outside file does not
  bless every outside file for the session;
* any other file/native ask is remembered **by tool name** — the policy is
  asking about the tool itself (`classes.read = ask`), so a narrower key would
  just re-ask for every file;
* a `requires_approval=True` spec is never remembered: irreversible actions ask
  every time.

A remembered approval only replaces the *human step*. It is consulted after
`evaluate()`, and only when the decision was `ask`, so a policy `deny` (a
protected file, a catastrophic command) can never be widened by pressing a
button. Nothing is written to disk: a new process asks again. To make an
approval permanent, edit the policy file instead.

The HUD shows the third button only for session-approvable calls
(`confirm.pending_allows_always()`) and prints what the button would remember
(`confirm.pending_remember_label()`); the CLI's TTY prompt accepts `a`.

### What the banner shows

`agent/approval_detail.py` builds the text handed to the approver. It is still
a `str` (a subclass), so every existing approver keeps working, but it also
carries a `title` and a `remember` label:

* `run_command` → the matched rule and reason, the **exact command**, the
  resolved **cwd**, and any timeout;
* `edit_file` / `write_file` → the resolved path and a real **unified diff**
  against the file on disk (create vs overwrite is stated explicitly), capped
  at 1 600 characters;
* `read_file` / `glob_files` / `grep_files` → the resolved path plus the
  pattern or line range;
* anything else → the rule and bounded JSON arguments.

The rule and its reason come first so a person can see *why* they are being
asked before reading *what* will run. A multi-line detail is rendered in a
read-only scroll box, not a label that grows the banner off screen.

The HUD colours that box: additions green, removals red, hunk headers cyan,
file headers bold, and rule/command keys bold. Highlighting is presentation
only — `toPlainText()` still returns exactly the string the approver was
given. When the detail contains a unified diff, the banner also shows a
one-line summary (`+2 −1 in 1 hunk`) and a **CHANGES ONLY** button that folds
away unchanged lines more than one line from a change, replacing each dropped
run with `… N unchanged line(s)`. The toggle switches back to **SHOW FULL
DETAIL**; the underlying diff is untouched, so approving either view executes
the same call. The parser and folder are pure Python in `core/diff_preview.py`
so they are tested without Qt.

### "Headless" means "no human can be asked"

`AgentRuntime.decide` computes:

```python
headless=self.headless and self.approver is None
```

so the three hosts differ only in what they inject:

| Host | `headless` | `approver` | Effective |
| --- | --- | --- | --- |
| GUI | `False` | `runtime.gui_approver` (HUD banner) | interactive; `exec` asks |
| CLI on a TTY | `False` | `_tty_approver` (stdin) | interactive; `exec` asks |
| CLI `--json` / CI | `True` | `None` | `exec` denied |

The GUI approver calls `core.confirm.ask_blocking_choice`, which **refuses when
called on the Qt main thread** — the banner's button handler would run on the
thread that is waiting for it. It therefore must be reached from a worker
thread (the `asyncio.to_thread` branch in `core/tool_exec.py`, or the text
path's worker). It returns `"deny"` if no UI is bound, on timeout, and on
cancel; `ask_blocking` remains as a bool wrapper.

---

## 7. CLI

```
python -m agent run "fix the failing test" --cwd . --provider auto
python -m agent resume <session-id> "now run the suite"
python -m agent sessions --limit 10
python -m agent tools --json
python -m agent permissions show | init [--path ...] [--force]
```

A bare prompt is treated as `run`:

```
python -m agent "add a test for parse()" --cwd .
```

Common flags (all of `run` and `resume`):

| Flag | Default | Notes |
| --- | --- | --- |
| `--cwd` | cwd | Workspace root; containment is enforced against it. |
| `--provider` | `auto` | `auto \| local \| custom \| openrouter \| stub`. `auto` = local → custom → openrouter. |
| `--model` | provider default | |
| `--stream` | off | Render text as it arrives; falls back to one request when the provider has no streaming transport. |
| `--max-turns` | 40 | Loop hard stop. |
| `--repeat-limit` | 3 | Identical-call threshold. |
| `--max-parallel-tools` | 4 | Concurrent read-only calls; `1` disables parallelism. |
| `--retries` | 3 | Attempts per model call on a transient failure. |
| `--max-context-chars` | 120000 | Compaction threshold. |
| `--timeout` | 120 | Per-command tool timeout. |
| `--json` | off | NDJSON events on **stdout**, one object per line, then `{"type": "result", ...}`. Implies headless. |
| `--headless` | auto | Never prompt; non-TTY stdin implies this. |
| `--yolo` | off | Allow every class and path rule. Sandbox only. |
| `--allow` / `--deny` | — | Override classes, repeatable/comma-separated. |
| `--permissions` | discovery | Policy file path. |
| `--no-session` | off | Do not persist. |
| `--summarize` | off | Use the model for compaction summaries. |
| `--title` | prompt[:60] | Session title. |

Exit codes: `0` completed, `1` any non-`completed` stop (turn cap, repeat,
permission denial, error), `2` no provider reachable, `3` usage error, `130`
interrupted (second Ctrl-C, or Ctrl-C while already cancelling). `stub` exists
so the CLI and loop can be tested without a model.

### Seeding a policy (nothing is required)

A clone runs on the built-in defaults: read/write allowed, `exec` asked (and
denied when headless), outside-workspace and protected files asked, catastrophic
commands denied. No file has to exist.

To customise, write a policy where discovery will find it — the per-user
location, so the checkout stays clean:

```powershell
py -3.11 -m agent permissions init --path "$env:LOCALAPPDATA\BrahmaAI\config\agent_permissions.json"
```

Or keep a project-specific policy inside the repo at `config/agent_permissions.json`
(also gitignored). `init` refuses to overwrite an existing file; pass `--force`
when you mean it.

---

## 8. Sessions and resume

Tables are **additive and namespaced** in the existing
`%LOCALAPPDATA%\BrahmaAI\config\workspace_store.sqlite3`:

```sql
agent_sessions(id, title, cwd, model, status, summary, turn_count, created_at, updated_at)
agent_messages(id, session_id, seq, role, payload_json, created_at)
agent_events(id, session_id, seq, kind, payload_json, created_at)
```

A message is stored as JSON because the payload *is* the protocol:
`tool_calls`, `tool_call_id` and `name` must round-trip exactly or a resumed
session fails its first follow-up request.

`AgentSession.replace_messages()` is called after a compaction, so the stored
transcript is the compacted one. Resume is therefore O(summary + recent), not
O(everything ever said).

Resume:

```
python -m agent resume <session-id> "continue"
```

The CLI reopens the session, reuses its `cwd` when `--cwd` is absent, rebuilds
the system prompt, and continues appending to the same transcript.

### The GUI text path is a session too

The HUD used to rebuild `[system, user]` on every turn and never created an
`agent_*` row, so closing the window was a hard reset even though the tables
were already there. `BrahmaLive` now binds the first agentic text turn to a
session created against the runtime workspace; later turns seed
`[fresh system, stored history, new user]` and reuse the same row. The first
provider request of a resumed turn therefore sees the earlier conversation,
not just the new sentence.

Two invariants make that safe:

* **The system prompt is never persisted.** It names this run's workspace,
  endpoint and instruction files; a stale copy would be worse than rebuilding
  it. `agent/session_bridge.history()` strips a stored system row defensively.
* **Only the new tail is appended.** `messages_after(history, seeded)` compares
  the common prefix, so re-seeding a resumed transcript adds one user message
  instead of duplicating every earlier turn. The loop keeps appending assistant
  and tool messages and rewrites the row after compaction, exactly as the CLI
  path does.

Commands (typing them in the HUD command bar; they never reach a model):

| Command | Effect |
| --- | --- |
| `/sessions` | List the 20 most recent sessions with full id, status and turn count. |
| `/resume` | Open a modal picker on the GUI thread; the worker waits for the choice. |
| `/resume <id>` | Bind that session directly. A unique id prefix is accepted. |
| `/new` | Drop the binding; the next message starts a fresh session. |

A replay of the stored user/assistant text goes into the chat feed with
`persist=False`: those turns were already written to `workspace_store` when
they happened, so a resume is a view, not a second copy. Tool calls and results
are execution detail and are not replayed. The tray menu has a
**Resume Agent Session…** item that submits `/resume` through the normal
command path.

---

## 9. Event stream

Every event is a dict with `type` plus fields. The same objects go to
`on_event`, the runtime's `event_sink`, `agent_events`, and `--json` stdout.

| `type` | Fields | When |
| --- | --- | --- |
| `session_start` | `session_id`, `cwd` | Once, if a session is attached. |
| `turn_start` | `turn`, `chars` | Before each model call. |
| `assistant` | `turn`, `text`, `tool_calls[{name, arguments}]`, `usage?` | After each model response; `usage` is present only when the provider reported it. |
| `tool_call` | `name`, `call_id`, `arguments`, `repeat` | Before dispatch; `repeat` is the occurrence count. |
| `tool_start` / `tool_end` | `name`, `call_id`, `args` / `chars` | From the runtime, around the handler. |
| `tool_result` | `name`, `call_id`, `ok`, `chars`, `preview` | After truncation. |
| `permission` | `name`, `decision` (`ask\|allow\|always\|denied\|deny\|error`), `rule`, `reason` | Only when a call was gated or served from a session approval. |
| `compaction` | `dropped`, `summary_chars`, `total_chars` | After folding. |
| `retry` | `attempt`, `delay_s`, `error` | Before waiting to retry a transient model-call failure. |
| `delta` | `turn`, `kind` (`text\|tool_call\|reset`), `text` / `index`, `id?`, `name?`, `arguments?` / `reason` | Only while streaming; `reset` says to drop the partial answer. |
| `stream_fallback` | `reason` | Once per run, when streaming was requested but the provider has no transport, or the stream was rejected before producing output. |
| `policy_reload` | `ok`, `source`, `error` (when `ok=False`) | When an edited policy file was adopted or rejected. |
| `instructions` | `loaded[labels]`, `chars`, `truncated` | When a turn touched a directory whose instruction file was not in the prompt yet. |
| `error` | `error` | Loop-level fatal. |
| `stop` | `reason`, `turns`, `tool_calls` | Last event of every run. |

Treat this as a public contract: the CLI's machine mode, a future TUI, and a
subagent coordinator all read it.

---

## 10. Prompt assembly

`agent/prompts.py` keeps the resident prompt short on purpose: rules that are
actually load-bearing (read before writing, prefer edit over rewrite, verify
with the project's own command, never invent tool output, stay in the
workspace, do not repeat a failing call, finish with a plain summary) plus the
workspace path and a one-line permission note.

Project instructions are appended **last** so they win: `AGENTS.md`,
`BRAHMA.md` and `.brahma/AGENTS.md` are read from the workspace **and up to
three parent directories** (root-most first), so a package inside a monorepo
inherits the monorepo's rules while its own file remains the last word. The
drive/filesystem root is never included, and
`BRAHMA_AGENT_INSTRUCTION_PARENTS=0` disables the walk. The combined text is
capped at 8 000 characters; the **tail** is kept when it overflows, because the
nearest instructions are the ones that matter. The harness never edits these
files on its own.

### Subdirectory instructions are path-driven

A workspace-wide recursive scan would read `node_modules`, cost a stat-and-read
per turn, and let rules from packages nobody is touching outrank the ones that
matter. Instead the loop uses the paths a turn actually named: after the tool
results are appended, `AgentLoop._load_directory_instructions` collects every
string argument whose key is a path (`path`, `cwd`, `file`, …) and walks only
the directories **between the workspace and that path**, shallowest first. A
file contributes its ancestors; an existing directory contributes itself. So
reading `packages/foo/app.py` loads `packages/AGENTS.md` and
`packages/foo/AGENTS.md`, in that order, and nothing else.

* Paths outside the workspace, the workspace itself, and anything under a
  vendored/generated directory (`node_modules`, `.git`, `dist`, `build`,
  `.venv`, `site-packages`, …) contribute nothing.
* The walk stops after 12 levels below the workspace, a single file is capped
  at 4 000 characters (head kept, `[truncated]` marker), and each run has a
  6 000-character budget for dynamically loaded text; when it is exhausted a
  `[directory instructions truncated]` marker is appended.
* Each file is loaded **once per run** (`seen` holds the normalised paths), so
  a second turn in the same package costs one `stat`, not another copy in the
  prompt.
* The text is appended to the **system prompt** for the next request, not
  inserted as a message. That keeps the OpenAI tool protocol intact and means
  the model sees the rules before it acts, without a synthetic user turn or a
  second copy in the transcript. An `instructions` event records the labels,
  character count, and whether anything was truncated.
* Discovery is per run, not persisted: a resumed session rebuilds the base
  prompt and re-discovers as it touches paths again.

The CLI builds the prompt with
`build_system_prompt(workspace, permission_note=policy_note())`. The GUI text
path reuses the app's existing core prompt (tool-index tiering and manuals are
unchanged); only the loop around it is new.

---

## 11. Tests

| File | Covers |
| --- | --- |
| `tests/test_agent_permissions.py` | Defaults, headless escalation, path escapes, protected files, denylist, per-tool overrides, malformed policy, template immutability. |
| `tests/test_agent_code_tools.py` | All six primitives against `tmp_path`, including ambiguity, whitespace hints, binary refusal, timeouts, and runtime gating (revoke/approve/unknown/native delegation). |
| `tests/test_agent_context.py` | Token estimate, tail-preserving truncation, safe-boundary compaction, no orphaned tool results, summariser fallback. |
| `tests/test_agent_loop.py` | Multi-turn continuation, protocol integrity, repeat stop, turn cap, denial stop, approval, cancellation, completer failure, `first_response`, in-loop compaction, event order, session parity, provider shapes. |
| `tests/test_agent_session.py` | Round-trip incl. `tool_calls`, listing order, rewrite after compaction, status/summary, events, missing DB. |
| `tests/test_agent_session_bridge.py` | GUI↔session seed: system stripping, prefix-only append on resume, transcript filtering, picker labels, one-line titles. |
| `tests/test_agent_cli.py` | Stub run, stdin prompt, missing prompt, bare-word run, tools/permissions/sessions, NDJSON shape, exit codes. |
| `tests/test_agent_retry.py` | Transient vs permanent classification, backoff shape, retry budget, cancellation during backoff, loop `retry` events and `usage["retries"]`, runtime flags, CLI Ctrl-C handler. |
| `tests/test_agent_prompts.py` | Instruction-file priority, bounded upward walk and its env override, root exclusion, tail-preserving truncation, prompt ordering. |
| `tests/test_agent_instructions.py` | Path-driven subdirectory discovery: ancestor chain order, directory-vs-file paths, ignored/vendored dirs, outside paths, dedupe and `seen`, depth and character budgets, loop injection into the next request, one load per file. |
| `tests/test_agent_approval.py` | Session "always allow" keying (tool vs exact command), bool answers not remembered, deny never widened, reconfigure survival, confirm tri-state and timeout. |
| `tests/test_agent_approval_detail.py` | Banner text: rule-first ordering, command/cwd, real diffs, create vs overwrite, bounded output, path-scoped session keys. |
| `tests/test_agent_diff_preview.py` | Diff detection in a detail, add/remove/hunk counts, context folding and its `… N unchanged` markers, no false diff for a `--- before ---` heading. |
| `tests/test_agent_parallel.py` | Concurrent reads, `max_parallel_tools` cap, write/`ask` serialisation, submission-order results, cancellation fillers for undispatched calls. |
| `tests/test_agent_usage.py` | OpenAI/Gemini/SDK usage shapes, unknown vs zero, summed totals only from real halves, accumulation, per-turn `assistant.usage`. |
| `tests/test_agent_policy_reload.py` | An edit takes effect mid-session, malformed edits keep the last good policy, no-file policies never reload, throttle/`force`, event payload, deny not widened by a session approval. |
| `tests/test_agent_streaming.py` | Delta assembly (text, fragmented tool calls, usage tails, error chunks), SSE parsing and HTTP error bodies, endpoint normalisation, stub streaming, loop delta/`reset`/`stream_fallback` events, rejection fallback vs retry, and the CLI printer's no-duplicate rule. |
| `tests/test_agent_providers.py` | Custom/OpenRouter adapters build against the shipped `llm_client.client` name; custom streaming payload, URL normalisation, auth headers and per-call settings reload. |
| `tests/test_confirmation_overlay.py` | HUD banner: ALWAYS ALLOW only for session-approvable calls, remembers-what hint, diff box, diff summary and CHANGES ONLY fold/restore, irreversible header. |

They do not need a model. The `test_agent_*` files are pure-Python and need
neither a display nor PyQt; only `test_confirmation_overlay.py` constructs a Qt
widget (offscreen). Run them with the CI interpreter:

```
py -3.11 -m pytest tests -q -k agent
```

### Python support

The floor is **3.11**: `numpy 2.4` (and therefore the pinned runtime stack)
ships no older wheels. The harness itself is stdlib-only and is exercised on
3.11, 3.12 and 3.13 by the `harness-pythons` CI job, which installs pytest and
nothing else — a deliberate canary that no GUI, provider SDK, or Windows COM
module has leaked into `agent/`. `setup.py` refuses to start below 3.11 instead
of letting pip dissolve into "no matching distribution" noise.

---

## 12. Current limitations

These are known and deliberate, not surprises:

* **OpenRouter tools go through `llm_client._openai_chat_completion`.**
  `chat_as_completion("openrouter", ...)` walks `or_client`, which drops the
  `tools` array. The adapter reuses the shared OpenAI transport directly, and
  `_endpoint_base()` strips `or_client.API_URL`'s `/chat/completions` because
  that transport appends the path itself. If that private method changes shape,
  fix `agent/providers.py` first.
* **The GUI approval path needs a worker thread.** `ask_blocking` refuses on
  the Qt main thread by design. The text path already runs in a worker; the
  voice path uses `asyncio.to_thread`. A new caller must do the same or the
  approval silently becomes a denial.
* **Instruction discovery follows the paths the model names.** A subdirectory
  `AGENTS.md` is loaded when a tool call touches a path inside that directory;
  a convention that only matters through an import the model has not mentioned
  yet is not loaded until it does. The upward walk is bounded (three levels,
  `BRAHMA_AGENT_INSTRUCTION_PARENTS` to change it) and the downward walk is
  bounded by depth and characters, but there is no import-graph analysis.
* **Instruction files are trusted input.** They are project conventions, not
  data: a cloned third-party tree that ships an `AGENTS.md` is speaking to the
  model with the same authority as the repository's own file. Vendored and
  generated directories are skipped, but review an unfamiliar tree before
  letting an agent work inside it.
* **Streaming is wired only where a transport exists.** Local (the existing
  Ollama-compatible SSE reader), custom OpenAI-compatible endpoints,
  OpenRouter and the stub stream, in both the CLI and the GUI text path. The
  voice path does not (Gemini's live socket has its own delivery). A custom
  server that rejects `stream: true` is retried once as a plain request and
  then stays non-streaming for the run. The assembler follows the protocol and
  *concatenates* argument fragments, so a non-compliant server that repeats the
  whole `arguments` blob on every chunk would produce invalid JSON. (A repeated
  full tool *name* is tolerated.)
* **Parallelism is read-only and whole-turn.** A turn with a single write,
  `exec`, native call, or approval prompt runs entirely sequentially; there is
  no per-call scheduler, and no subagent layer yet. A coordinator can be built
  on the event stream without touching the loop.
* **`run_command` is `shell=True`.** The policy is the only barrier; there is
  no OS sandbox. Treat `--yolo` as "run me in a container".
* **The voice path is not session-persisted.** Gemini's live session owns its
  own history; the text path and CLI write `agent_*` rows, but a voice turn
  still does not. Resuming a GUI text session does not replay it into a live
  Gemini socket.
* **Windows-first.** The code is portable Python but the denylist, paths and
  host integration are Windows-shaped, matching the rest of the app.

---

## 13. Where to read first

1. `agent/runtime.py` — the boundary everything else depends on.
2. `agent/loop.py` — the actual agent behaviour.
3. `agent/permissions.py` — why a call was refused.
4. `agent/code_tools.py` — what the model can physically do.

If you are adding a tool: write the `ToolSpec` in `agent/spec.py` or a sibling
module, import it for registration, decide its `tool_class`, and add tests. Do
not add an `elif` to `core/tool_exec.py`.
