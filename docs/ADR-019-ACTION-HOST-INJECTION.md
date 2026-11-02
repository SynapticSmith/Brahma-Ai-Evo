# ADR-019 — Action host injection for the native-tool migration

**Status:** Accepted (ratified 2026-11-01) · **Gates:** O-6 waves 1–3 (B6/B7) · **Supersedes:** nothing
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
