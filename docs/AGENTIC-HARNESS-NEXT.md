# AGENTIC HARNESS — NEXT WORK TRACKER

The forward-looking companion to
[`AGENTIC-HARNESS.md`](AGENTIC-HARNESS.md) (architecture) and
[`AGENTIC-HARNESS-STATUS.md`](AGENTIC-HARNESS-STATUS.md) (history and
decisions). That file records what shipped; this file records what is left, why
it matters, and what "done" means.

Keep it short and current: when a batch lands, tick its boxes, add the decision
to the status doc, and re-rank what remains. A stale tracker is worse than none.

---

## 0. Snapshot

| Field | Value |
| --- | --- |
| Snapshot date | 2026-10-31 |
| `HEAD` | `b2c7d36 agent: add project skills with on-demand loading` |
| Working tree | clean when this snapshot was taken (this file is the only addition) |
| Harness batches on `main` | 20 commits, one per hardening batch (`git log --oneline`) |
| Last decision recorded | D39 (project skills, on-demand loading) |
| Gate result | `ruff` clean; `pytest tests` → **715 passed, 1 skipped** (offscreen) |

Reproduce the snapshot:

```powershell
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
py -3.11 -m pytest tests -q -k agent
```

All §1 rows in `AGENTIC-HARNESS-STATUS.md` are `Done`. The harness is **not**
mid-batch: the last batch (project skills) landed complete, and the work below
is new scope rather than cleanup of an unfinished change.

---

## 1. Status board

| ID | Task | Value | Effort | Status |
| --- | --- | --- | --- | --- |
| N1 | Give the GUI text and voice paths the project prompt | High | S–M | Open |
| N2 | Consume MCP `tools/list_changed` and revive a dead server | Medium | M | Open |
| N3 | Correct the stale claims in the two harness docs | Low | S | Open |
| N4 | Filesystem/network confinement for `run_command` | High (design) | XL | Blocked on decision |
| N5 | Parallel / model-selecting subagents | Medium (design) | L | Blocked on decision |
| N6 | MCP resources, prompts, sampling | Low | M | Deliberately deferred |
| N7 | Executable skills / `allowed-tools` enforcement | Low | M | Rejected (would be a second boundary) |
| N8 | Unify harness skills with the core Skills Library | Medium (design) | M | Blocked on naming story |

Ordered by value per unit of risk, not by size. N1 is the recommended next
commit; N2 is the natural follow-up; N3 rides along with whichever lands first.

---

## 2. N1 — Give the GUI text and voice paths the project prompt

### 2.1 The gap

`agent/prompts.py:build_system_prompt()` (`agent/prompts.py:364`) is the only
place that assembles the harness prompt layers:

- the resident `BASE_SYSTEM` rules;
- the `AGENTS.md` / `BRAHMA.md` upward walk (`load_project_instructions`);
- the project-skills catalogue (`agent/skills.py:404 prompt_block`);
- the permission note.

It has exactly three call sites:

| Caller | Surface |
| --- | --- |
| `agent/__main__.py:306, 380` | CLI `run` / `resume` |
| `agent/loop.py:553` | subagent child loop |

Neither user-facing GUI surface calls it:

- **GUI text path.** `main.py:3780 _agentic_seed_messages()` composes the app's
  core prompt plus `instruction_injector` layers (`main.py:3794-3846`). No
  `AGENTS.md`, no skills block, no harness permission note. The
  `advertise_discovery=False` branch (an endpoint that rejects tool arrays)
  also skips `resolve_tools`, so it has no skill context at all.
- **Voice path.** `core/session_lifecycle.py:489-533` builds the Gemini Live
  system instruction; its own comment says this is "the only place Gemini Live's
  system instruction is assembled". It appends `live_extra` from
  `instruction_injector` but no harness blocks.

### 2.2 Why it matters

1. **`AGENTS.md` is not actually read on the HUD or voice.** This repo's
   `AGENTS.md` states "The Brahma agent harness reads this file automatically
   (`agent/prompts.py`)". True for the CLI and subagents; false for the text
   path and Gemini Live.
2. **`load_project_skill` is advertised but undiscoverable.**
   `agent/runtime.py:29` registers the tool process-wide, so it appears in
   `all_specs()` and therefore on both surfaces. But neither prompt lists skill
   names or descriptions, so the model can only guess names. That defeats the
   progressive-disclosure design in D39: the catalogue is the whole point of
   the tool.
3. **The two surfaces behave differently from the CLI** on the same repo and
   the same instructions. That is exactly the drift D2/D3 were meant to avoid.

Subdirectory instructions (D25) still work on GUI text: the loop appends them to
the seeded `context.system` (`agent/loop.py:692-730`). Only the *base* layers
are missing, and they are missing before the first tool call.

### 2.3 Change plan

1. Extract a reusable helper in `agent/prompts.py`, e.g.
   `project_context(workspace) -> str`, returning the instruction walk plus the
   skills block (the two pieces that are workspace-scoped). Keep
   `build_system_prompt()` composed from it so CLI/subagent behaviour is
   byte-identical.
2. Call it from `main.py:_agentic_seed_messages()` and append the result to
   `system_prompt` **after** the core prompt and **before** `instruction_injector`
   layers, matching the documented ordering (project rules outrank generic
   advice; host `extra` stays last). Apply it in both the
   `advertise_discovery` branches.
3. Call it from `core/session_lifecycle.py` beside `live_extra`
   (`core/session_lifecycle.py:492-495`), using the runtime workspace.
4. Add a `instructions`/diagnostic event or startup log line naming what
   loaded, so a user can tell the file was read. Reuse the existing event name
   and payload shape from `agent/loop.py` where possible.
5. Do **not** copy the blocks into the transcript. They are system-prompt text;
   resume and compaction must stay untouched (D27).

### 2.4 Acceptance criteria

- [ ] A GUI text turn in a workspace with `AGENTS.md` sends that text in the
      `system` message of its **first** provider request.
- [ ] The same turn lists project skills (`name: description`) when
      `.brahma/skills/*/SKILL.md` exists.
- [ ] A voice session's `LiveConnectConfig.system_instruction` contains the same
      two blocks.
- [ ] A workspace with neither file produces no extra blank sections.
- [ ] CLI and subagent prompts are unchanged (existing prompt tests still pass).
- [ ] `agent/` still imports with only pytest installed; `agent/__init__.py`
      stays empty; no PyQt import from `agent/`.

### 2.5 Tests

- Pure-Python helper tests (extend `tests/test_agent_prompts.py` or
  `tests/test_agent_skills.py`): ordering, empty workspace, budget caps,
  instruction + skills combined.
- A GUI wiring test in the style of `tests/test_gui_mcp_wiring.py` (imports
  `main`, so **not** named `test_agent_*`): `_agentic_seed_messages()` includes
  the blocks for a temp workspace, in both discovery branches.
- A `session_lifecycle` prompt-assembly test if the existing test seam allows
  it without constructing a live Gemini session; otherwise assert the helper is
  called and keep the voice assertion to the composed string.

### 2.6 Risks / notes

- `agent/skills.py:prompt_block` reads up to 8 KB per skill and caps the block;
  it is already bounded and cheap. Do not re-implement it.
- The GUI's core prompt already contains app-level "full access" directives;
  append project blocks after them so the project's rules are the last word,
  consistent with `build_system_prompt`.
- Voice is mostly desktop actions. Loading project rules there is still correct
  (the same workspace may hold project skills), but if it turns out to bloat
  the Live instruction, the fallback is to inject only the skills catalogue and
  leave `AGENTS.md` to the text path — record that as a decision if taken.

---

## 3. N2 — Consume MCP `tools/list_changed` and revive a dead server

### 3.1 The gap

`agent/mcp.py:509` sets `self.tools_changed = True` when a server sends
`notifications/tools/list_changed`, and nothing ever reads it (the only other
occurrence is the initialiser at `agent/mcp.py:270`).

`WorkspaceMCP.ensure()` returns early when the `(workspace, config mtime, size)`
fingerprint is unchanged (`agent/mcp.py:947`), and never checks `alive()`. So:

- a server that adds or removes tools mid-session keeps advertising the stale
  catalogue;
- a crashed server stays failed until the workspace or config changes or the
  app restarts.

The docs list this as a limit; the unused flag shows the intended next step.

### 3.2 Change plan

1. In `WorkspaceMCP.ensure()`, after the fingerprint check, inspect the live
   session: if any server reports `tools_changed` or is not `alive()`, refresh
   instead of returning early.
2. Refresh = unregister that server's specs, re-run `tools/list`, register the
   new specs, clear the flag, and emit one `mcp_refresh` (or existing `error` on
   failure) event. Reuse `MCPSession.close()` / start semantics so a dead server
   is restarted exactly once per turn.
3. Keep the fingerprint as the *config* trigger; `tools_changed` is the
   *runtime* trigger. Both paths must leave the registry with no duplicate or
   orphaned spec names.
4. No background watcher and no retry loop: the next `ensure()` is the refresh
   point (D38's discipline).

### 3.3 Acceptance criteria

- [ ] A fake stdio server that emits `tools/list_changed` and answers a new
      `tools/list` has its new tool callable on the next `ensure()`.
- [ ] A removed tool is unregistered and no longer advertised.
- [ ] A killed server is restarted on the next `ensure()`, and a still-broken
      server fails one call with its stderr tail rather than hanging.
- [ ] Two servers, one changed and one not: the unchanged one is not restarted.
- [ ] `close()` still unregisters everything; no spec leaks across tests.

### 3.4 Tests

Extend `tests/test_agent_mcp.py` (it already runs against a real fake stdio
server): add a `list_changed` capability to the fake, plus restart and
partial-refresh cases. Keep it in the stdlib-only CI job.

---

## 4. N3 — Correct the stale claims

Cheap, and it prevents the next reader from re-deriving the wrong state.

| Location | Current claim | Reality |
| --- | --- | --- |
| `AGENTIC-HARNESS-STATUS.md:971` | "**MCP is tools-only and CLI-only.**" | D38 shipped the GUI text path; §1's table already says "CLI + GUI text path". The item's own body then describes the GUI manager. |
| `AGENTIC-HARNESS-STATUS.md:974` | `tools/list_changed` "is not handled" | The notification is received and latched (`agent/mcp.py:509`); it is only not acted on (N2). |
| `AGENTIC-HARNESS.md:493` | "no automatic restart or `tools/list_changed` refresh" | Accurate about *refresh*; clarify that the notification is latched, not ignored. |
| both docs | Skills/instructions implied to apply to "the harness" | They apply to CLI + subagents only until N1 lands. |

Also add, after N1/N2 land:

- a sentence in `AGENTIC-HARNESS.md` §10 that the GUI/voice prompts include the
  same project blocks;
- a status-doc decision entry (D40+) for each batch, following the existing
  `### Dn — …` shape.

---

## 5. Blocked / deliberately deferred

Do not start these without an explicit decision; each has a documented reason.

### N4 — Filesystem/network confinement

Windows has no portable stdlib sandbox. AppContainer silently fails against a
normal user install, and a wrapper that only looks enforcing is worse than none
(D36). `exec.sandbox.prefix` is the supported escape hatch for a container.
**Decision needed:** container-first vs AppContainer-first vs "prefix is
enough".

### N5 — Parallel / model-selecting subagents

`run_subagent` is a `native` fence: one child at a time, parent waits, child
shares the parent's model, transcript discarded, no session row (D33). Fan-out
or per-child models would need the event-stream coordinator the docs warn
against building as a second runtime. **Decision needed:** accept sequential, or
design a coordinator with one enforcement path.

### N6 — MCP resources, prompts, sampling

Deliberately omitted: a half-supported capability produces tools that fail on
first use (D37). Add only with a complete implementation and tests.

### N7 — Executable skills / `allowed-tools` enforcement

Rejected as a second permission boundary. The runtime policy stays the only
gate (D39).

### N8 — Unify harness skills with the core Skills Library

Two catalogues exist on purpose: `load_project_skill` (harness, `.brahma/skills`)
and `load_skill` (core Skills Library, `skill_id`). Sharing a name shadows the
legacy tool because the harness branch in `core/tool_exec.py` runs first.
**Decision needed:** one naming and precedence story before any merge.

---

## 6. Working agreement for the next batch

1. One commit per batch on `main`, message prefixed `agent: `, tests and docs in
   the same commit (the existing history is the template).
2. Run the gates from §0 before claiming done; paste the numbers into the status
   doc's §4 Verification.
3. Add a `### Dn — …` decision entry for anything that was a fork in the road.
4. Update this file: tick the acceptance boxes, move the item to a short
   "Landed" list, re-rank what remains.
5. Keep `agent/` stdlib-only; never add an `elif` to `core/tool_exec.py`; never
   register a harness tool under a core meta-tool name (`load_skill`,
   `search_tools`, `recall_memory`, …).

### Landed

_(empty — the next batch appends here)_
