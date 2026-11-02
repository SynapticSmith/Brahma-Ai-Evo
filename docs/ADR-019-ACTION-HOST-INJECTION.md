# ADR-019 — Action host injection for the native-tool migration

**Status:** Accepted (ratified 2026-11-01) · waves 1–2 implemented 2026-11-01 · **Gates:** O-6 waves 1–3 (B6/B7) · **Supersedes:** nothing
**Related:** `AGENTIC-HARNESS.md` (ToolSpec/ToolContext), `ARCHITECTURE-SPEC.md` §2.2, §6.3, §12 Q1.4

## Context

Every native capability in `actions/` is dispatched today by the `elif` chain in
`core/tool_exec.py`, which is the one execution path with no policy check. O-6
says: migrate each action into a `ToolSpec` behind `AgentRuntime.execute`, in
risk order, until the chain is empty.

A `ToolSpec` handler has one signature — `(args: dict, context: ToolContext) -> str`
(`agent/spec.py:94`, `:278`) — and `ToolContext` is frozen with
`workspace`, `timeout`, `max_output_chars`, `policy`, `extra`
(`agent/spec.py:56`). `agent/` is stdlib-only and must stay that way.

The actions do not match that shape:

- 22 modules define `def something(parameters, player=None, speak=None)` or a
  variant (`session_memory`, `speak` only, `player` only).
- `core/tool_exec.py` passes `player=self._host.ui` and `speak=self._host.speak`
  at 43 call sites, reads `self._host._loop`, and performs HUD side effects
  around dispatch (`ui.set_state("THINKING")`, `ui.show_hud_operation(...)`,
  `ui.update_task_workspace(...)`, `speak("Working on …")`).

So the migration question is not "how do we call a function"; it is **how does a
desktop action get its host, without `agent/` learning about PyQt and without a
second permission boundary**. This ADR fixes that shape before any wave starts,
because a wrong first choice would have to be undone 59 times.

## Decision

1. **A narrow `HostServices` protocol owned by the host layer.** New module
   `core/desktop_host.py` defines a `Protocol` with only what actions actually
   use: `speak(text)`, `ui` (a facade exposing `set_state`,
   `show_hud_operation`, `update_task_workspace`), and `loop` /
   `submit(coro)` for the handful of actions that need the event loop. It lives
   in `core/`, never in `agent/`.
2. **Injection through `context.extra["host"]`.** The host builds the mapping
   once per turn and passes it into `AgentRuntime.execute(... context=...)`.
   `ToolContext` stays frozen and unchanged; `agent/` sees a `Mapping`.
3. **Adapters, not signature changes.** Each migrated action gets a thin
   wrapper in the host layer with the canonical `(args, ctx) -> str` shape,
   which pulls only the services it needs and calls the legacy function with
   its existing keyword arguments. The 22 module signatures are *not* rewritten:
   that churn buys nothing and would touch every caller again.
4. **HUD feedback leaves the action layer.** `AgentRuntime` already knows when a
   tool starts and ends; the host subscribes (`on_tool_start` / `on_tool_end`)
   and owns `set_state`/`speak`/`show_hud_operation`. An action does not call the
   UI, so the same action behaves identically in the CLI, in a test, and in the
   GUI, and the "Working on …" chatter stops being copy-pasted per branch.
5. **Registration is host-side.** `core/desktop_tools.py` (new) registers the
   migrated specs at startup, by wave, with an explicit `tool_class` and
   `requires_approval` for irreversible ones. `agent/` never imports PyQt, never
   imports `core/`, and never learns an action name by heart.
6. **A missing host is an error, not a fallback.** If `extra["host"]` is absent,
   the adapter returns a diagnostic string ("this tool needs the desktop host;
   run it from the GUI") instead of reaching for a global or silently doing
   nothing. That keeps "the tool surface differs per door" observable rather
   than mysterious.

## Consequences

- `player=`/`speak=` remain valid on the legacy functions for the duration of
  the migration; they become dead after the last wave and can be deleted then.
- A test can register the same specs with a null/recording host, which makes
  desktop actions testable without Qt — the current reason several cannot be
  unit-tested at all.
- The adapter layer is small but real (~10 lines per action). That is the price
  of keeping one dispatch boundary; the alternative (a second `elif`-like
  adapter switch) is explicitly rejected.
- `core/tool_exec.py` shrinks wave by wave; the ratchet in
  `tests/architecture_ratchet.json` is lowered in the same commit as each wave.

## Open questions (for ratification)

1. Is `extra["host"]` acceptable as the injection channel, or should
   `ToolContext` grow an explicit `host: Any = None` field? *(Recommendation:
   `extra`. It keeps `agent/spec.py` frozen and keeps the host concept out of
   the portable package.)*
2. Do we allow actions to `speak` at all, or only the runtime's event hook?
   *(Recommendation: event hook for status; `speak` reserved for results that
   are voice-first, e.g. reading an answer aloud.)*
3. Should the adapters live in `core/desktop_tools.py` (one module) or beside
   each action? *(Recommendation: one module per wave group, so imports stay in
   the host layer and `actions/` stays a leaf.)*

## Acceptance criteria for wave 1 (`exec` + `write`)

- Migrated tools carry a `ToolSpec` with the correct `tool_class` and go through
  `AgentRuntime.execute` (verified by a policy-denial test).
- `core/tool_exec.py` loses those branches and the ratchet lowers accordingly.
- `agent/` still imports with only pytest installed (CI canary).
- One action that previously needed a live Qt window is exercised in a test
  with a recording host.

## Implementation — wave 1 (landed 2026-11-01)

Nine names, chosen as the unambiguous slice of decision 5:

| Class | Names |
| --- | --- |
| `write` | `pdf_document`, `word_document`, `presentation_builder`, `spreadsheet_builder` |
| `exec` | `reminder` (persisted `schtasks` task), `auto_heal`, `self_patch`, `rollback`, `shutdown_brahma` — the last four `requires_approval=True` |

`self_patch` and `rollback` have no declaration of their own; they borrow
`auto_heal`'s schema and are described as aliases (`rollback` forces the
`rollback` action). Deferred to wave 2 on purpose: `file_controller` and
`smart_organizer` mix read and write per action and need a per-action class
decision; `send_message`/Instagram are `network`; `save_memory` returns a
`silent: True` `FunctionResponse` shape the spec layer does not model; and
`unlock_device`/`connect_*` are `device`.

Modules:

- `core/desktop_host.py` — `HostUI`/`HostServices` protocols, `require_host`,
  `present_start`/`present_end`, `HostEventSink`, `LiveHost`, and the
  `RecordingHost`/`RecordingUI` pair the tests use. `MISSING_HOST` is the
  diagnostic text decision 6 asks for.
- `core/desktop_tools.py` — the `WAVE1` class/approval table, `ALIASES`, nine
  adapters, `build_specs`/`register`/`desktop_names`, and `attach_host`.
  Schemas are derived from `main.TOOL_DECLARATIONS` through
  `agent.spec.lowercase_schema`, so the voice catalogue and the agent catalogue
  cannot drift.
- `core/tool_exec.py` — imports and ten migrated `elif` branches removed; the
  shared HUD blocks replaced by `desktop_host.present_start`/`present_end`, and
  the harness branch passes `extra={"host": ...}`. 865 → 716 lines, 80 → 70
  `elif`s (ratchet lowered in the same commit).
- `main.py` — `desktop_tools.register(TOOL_DECLARATIONS)` runs before
  `tool_bridge.prime`, so the specs exist before the first handshake.
- `agent/runtime.py` — `tool_end` now carries `output`, and the runtime gained
  a generic `default_extra` mapping (see the amendment below).

Two decisions were settled while implementing:

1. **`ToolContext.host` was not added.** `extra["host"]` is the channel, as
   recommended; `agent/spec.py` is unchanged.
2. **The "`<tool> completed`" utterance is gone.** Decision 4 says the host owns
   status speech, and a completion line is redundant: the result already
   reaches the model, which speaks when the result is worth speaking. The
   result is rendered by `present_end` from `tool_end.output`, which is why the
   event gained that field — the deliverables panel needs the file path and the
   summary, and a handler must not reach for the UI to supply them.

One amendment was needed in practice. Decision 2 assumed the host could pass
`extra={"host": …}` on every call, which is true for the voice door
(`core/tool_exec.py` calls `AgentRuntime.execute` directly) but not for the GUI
text door: `AgentLoop._run_one` builds the per-call mapping itself (it adds
`subagent_runner`). So the runtime gained a generic `default_extra` mapping,
merged underneath the per-call `extra`, and `attach_host` writes `{"host": …}`
into both the sink and that mapping. `BrahmaLive._finish_tool_turn` attaches
once per text turn; the voice executor attaches on construction. `agent/` still
only sees an opaque `Mapping` — the amendment adds no host concept to the
portable package, and a per-call `extra` still wins.

`LiveHost.submit` is the only bridge to the live event loop; wave 1 has no
async action, so it currently has no caller. `present_end(voice=...)` likewise
takes an optional one-liner the *caller* already produced; `core/tool_exec.py`
passes `_connect_tool_voice(...)`, which is `None` for every wave-1 name (it
only formats `connect_*`). Whether the host protocol grows a `tool_voice`
method is a wave-2 question, when the `device` names actually need one.

Tests: `tests/test_desktop_tools.py` (14) covers the class/approval table, alias
schemas, a real PDF run under a recording host, `current_file` threading into
`word_document`, a policy denial that never reaches the action, a headless
`exec` denial, `requires_approval` surviving "always allow", the missing-host
diagnostic, the host reaching a text turn through `default_extra` (with a
per-call override still winning), `tool_end.output`, the sink's name filter,
sink chaining/idempotency, the absence of the migrated `elif` branches, and that
`agent/` never imports the desktop layer. Suite: **790 passed, 1 skipped**.

## Implementation — wave 2 (landed 2026-11-01)

Eighteen names (`WAVE2` in `core/desktop_tools.py`, bringing the desktop registry
to 27 specs across waves 1–2):

| Class | Names |
| --- | --- |
| `write` / `read` (per-call classifier) | `file_controller`, `smart_organizer`, `desktop_organizer` (alias of `smart_organizer`) |
| `write` (`silent=True`) | `save_memory` |
| `device` | `unlock_device`, `connect_list_devices`, `connect_get_device`, `connect_get_capabilities`, `connect_execute`, `connect_pair_device`, `connect_disconnect_device` |
| `network` | `send_message`, `check_instagram_messages`, `instagram_send_dm`, `instagram_post_photo`, `instagram_post_reel`, `instagram_get_user_info`, `instagram_reply` |

Three small harness/host extensions keep the wave on the single dispatch path:

1. **Per-call capability classification (`ToolSpec.classifier` / `ToolSpec.class_for(args)`).**
   `file_controller` and `smart_organizer` mix read-only inspection (`list`,
   `read`, `find`, `largest`, `disk_usage`, `preview`, `find_duplicates`, `info`)
   with mutation (`delete`, `write`, `move`, `organize`, `undo`, `archive_old`).
   Rather than splitting the model-facing tool names, `ToolSpec` gained an
   optional `classifier: Callable[[Mapping[str, Any]], str]` with a fail-safe
   `class_for(args)` helper: if the classifier raises or returns an unknown
   class, it falls back to the declared `tool_class` (`"write"`). Declaring
   `"write"` as the static class also keeps read-only subagent defaults
   (`spec.tool_class == "read"`) from inheriting a mixed-verb tool unless
   explicitly requested. `AgentRuntime.decide`, `AgentRuntime._approval_key`,
   and `agent.approval_detail._remember` all use `spec.class_for(args)`, and
   class-level session approvals for classified tools are keyed as
   `f"{name}:{tool_class}"` so approving a `read` verb never silently approves a
   `write` verb on the same tool. In `AgentLoop._call_effect`, classified specs
   are treated as fences so multi-path/shortcut arguments never overlap.
2. **Silent tool responses (`ToolSpec.silent: bool = False`).** `save_memory`
   returns `{"result": "ok", "silent": True}` on the voice `FunctionResponse`
   and suppresses the `"Working on …"` utterance and deliverable card while
   still restoring the UI state to `LISTENING` on `tool_end`. `AgentRuntime`
   propagates `silent` on `tool_start`/`tool_end` events, `HostEventSink`
   suppresses `present_start`/`present_end` when `silent` is set, and
   `core/tool_exec.py` adds `"silent": True` to the `FunctionResponse` payload
   when `spec.silent` is true.
3. **`connect_*` spoken summaries in `core/desktop_host.py`.** The wave-1 open
   question on `_connect_tool_voice` was resolved without widening
   `HostServices`: `connect_tool_voice(name, raw_result)` moved into
   `core/desktop_host.py` and `present_end` computes it automatically when
   `voice` is not supplied (`core/session_lifecycle.py` delegates to the same
   helper). Moving `ig_gemini_reply` into `core/reply_flow.py` and removing the
   redundant wrapper on `BrahmaLive` also shrank `main.py` (`4,811 → 4,784`
   lines).

`core/tool_exec.py` dropped 16 `elif` branches plus the `save_memory` block
(`716 → 527` lines, `70 → 54` `elif`s); all three ratchets in
`tests/architecture_ratchet.json` were lowered in the same commit.

Tests: `tests/test_desktop_tools.py` (20) adds wave-2 spec/alias/silent shape
checks, `ToolSpec.class_for` fail-safe behavior, per-action `read` vs `write`
policy enforcement across `file_controller` and `smart_organizer`/`desktop_organizer`,
per-class session-approval isolation (`read` "always allow" does not approve
`write`), `device`/`network` policy denials plus `connect_list_devices` spoken
summary via `HostEventSink`, and `save_memory` silent HUD/event behavior.
Suite: **796 passed, 1 skipped**.

Wave 3 is the remaining ~32 native actions in `core/tool_exec.py` (system/UI/media/search/vision/dynamic skills) plus the turn-controller extraction from `main.py`.
