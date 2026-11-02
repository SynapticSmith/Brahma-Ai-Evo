# ADR-021 — `pinned_extra` semantics for the deferred-tool budget

**Status:** Accepted (ratified 2026-11-01) · **Landed:** 2026-11-01 (`voice:`) · **Closes:** O-8 layer 3 / Q1.3
**Related:** `core/tool_index.py:1326` (`resolve`), `core/tool_bridge.py:362`
(`build_session_payload`), `ARCHITECTURE-SPEC.md` I-19, §6.7, §12 Q1.3

## Context

I-19 says the advertised surface never exceeds `budget()` declarations. Today
`ToolIndex.resolve()` obeys it, and then `build_session_payload()` appends every
`mcp_Synapse*`/`cdp` declaration for browser-ish intents
(`core/tool_bridge.py:386–398`) with only a `GEMINI_MAX_TOOLS` clamp — an active
violation, and the reason a session can advertise 40 tools against a budget of
24.

The fix requires a session-scoped pin: when a Live socket drops with `1008`
after the model learned about a tool through `search_tools`, the reconnect must
advertise that tool again *without* replaying the user turn. That means
`resolve(query, budget, pinned_extra=...)`.

Three semantics were never decided, and the promotion loop cannot be deleted
until they are, because it is currently the only thing keeping Synapse/cdp tools
visible for browser intents.

## Decision

### Q1 — Does `pinned_extra` count against the budget?

**Yes, unconditionally.** Extras are selected *inside* `resolve()` and consume
the same slots as retrieval hits:

```
room = budget - |global pinned| - |extra| - |META_TOOLS|
room = min(room, GEMINI_MAX_TOOLS - |global pinned| - |extra| - |META_TOOLS|)
```

No caller appends after `resolve()`. `build_session_payload` may add only the
meta declarations it already reserved for, and a test asserts the returned
declaration count is `<= budget()` for every intent, including the browser case
that used to trip it.

### Q2 — Pinned-global vs `pinned_extra` vs top-k precedence

Deterministic order, no scoring between them:

1. **Global pinned** (`entry.pinned`, user configuration) — always present,
   ordered by catalogue order, never evicted.
2. **`pinned_extra`** (this call only, session recency) — next, in the order
   given (most recent last, so a stable "last learned wins the last slot").
3. **Retrieval hits** — fill the remaining room, excluding names already chosen.

`entry.pinned` is never mutated by `resolve()`. Two concurrent sessions with
different extras must not see each other's pins; the parameter is per call and
there is no session state inside `ToolIndex`.

### Q3 — Eviction order when the extras do not fit

1. Retrieval hits are evicted first (they are the least specific).
2. Then `pinned_extra`, **oldest first** — the model most likely still remembers
   the tool it just used, and a ring buffer of recent names means the newest
   entries are the ones worth keeping.
3. Global pinned is never evicted; if the configuration alone exceeds the
   budget, that is a startup diagnostic, not a per-turn surprise.

`resolve()` reports `pinned_extra_dropped: int` (and keeps `deferred`) so the
reconnect path can log "advertised 6 of 9 recently learned tools" instead of
failing silently.

### Replacing the promotion loop without losing the behaviour

The loop exists because retrieval under-ranks `mcp_Synapse_cdp_*` for a bare
"open a tab" intent. Replace the **append** with a declaration-driven,
budgeted hint:

- A small table (host config, `core/tool_bridge.py`) maps an intent keyword
  (`chrome`, `browser`, `synapse`, `cdp`, `open tab`, `navigate`) to tool-name
  prefixes.
- `build_session_payload` resolves those prefixes to names, truncates to a fixed
  cap (recommend 6), and passes them as `pinned_extra`. They compete for slots
  like everything else, so `budget()` still holds at 24 (or whatever the user
  configured).
- Reconnects pass the session's recent-search names as `pinned_extra` the same
  way. One mechanism, two sources, one budget.

## Consequences

- The `1008` fix no longer needs an exception to I-19, and the browser-intent
  behaviour is preserved rather than deleted.
- A tight budget may now surface fewer Synapse tools than the old append did —
  which is the honest trade, and the deferred count makes it visible instead of
  hidden in a log line.
- `ToolIndex.resolve` gains one keyword argument and two counters; the shared
  index stays stateless with respect to sessions.

## Open questions (for ratification)

1. Cap for intent hints: 6, or `budget // 4`? *(Recommendation: a fixed 6 — it
   is small enough to leave retrieval room on a 24-tool budget and predictable
   across users.)*
2. Should `pinned_extra` survive *two* reconnects or only one? *(Recommendation:
   the ring buffer holds the last N learned names for the life of the Live
   connection, so a second `1008` reuses the same list, not just the previous
   turn's.)*
3. Where do intent hints live — code constant or user config? *(Recommendation:
   code for v1; a user-editable hint file is a later amendment if the table
   proves wrong.)*

## Acceptance criteria for the implementing batch

- `build_session_payload("chrome browser synapse cdp open tab")` returns `<=
  budget()` declarations (regression test for I-19).
- `resolve(..., pinned_extra=["a"])` advertises `a` without mutating
  `entry.pinned`; a second call without the argument does not.
- With more extras than room, the newest survive and `pinned_extra_dropped`
  reports the count.
- `core/tool_bridge.py` no longer contains an append after `resolve()`.

## Implementation (landed 2026-11-01)

| Piece | Location |
| --- | --- |
| Budgeted extras, global-pin-first ordering, oldest-first eviction, `pinned_extra_dropped` | `ToolIndex.resolve()` (`core/tool_index.py`) |
| Intent-hint table, `INTENT_HINT_CAP = 6`, learned-name ring buffer (`LEARNED_BUFFER_MAX = 24`) | `core/tool_bridge.py` (`intent_pins`, `note_learned`, `recent_learned`, `reset_session_learning`) |
| Literal `call_tool(...)` templates per hit | `_handle_search` / `_handle_list_tools` |
| Reconnect pins, explicit-end reset | `build_session_payload` default + `SessionLifecycle.run` standby tail |
| Guards | `tests/test_tool_index.py` (3), `tests/test_tool_bridge.py` (6, incl. the no-append static check) |
