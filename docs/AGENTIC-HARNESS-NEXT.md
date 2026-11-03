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
| Snapshot date | 2026-11-03 |
| `HEAD` | `cc1e970 docs: record D46 for Tier 2 OrchestratorAdapter and Settings Hub Agent Bridge UI` |
| Working tree | clean; Queue 1, Queue 2, Queue 3, and Queue 4 complete |
| Harness batches on `main` | N1–N5, ADR-018–021, Bridge v2 (B14–B22), D45 (boot resilience), D46 (Tier 2 Orchestrator & UI) |
| Last decision recorded | D46 (Tier 2 External Orchestrator Connector & Settings Hub Agent Bridge UI) |
| Gate result | `ruff` clean; `pytest tests` → **1,134 passed, 5 skipped** (offscreen); `-k agent` → **483 passed, 1 skipped** |

Reproduce the snapshot:

```powershell
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
py -3.11 -m pytest tests -q -k agent
```

All §1 rows in `AGENTIC-HARNESS-STATUS.md` are `Done`. The harness is **not**
mid-batch: the last batch (project skills) landed complete, and the work below
is new scope rather than cleanup of an unfinished change. N1, N3, N2 and N8
have since landed (D40, D41, D42); the remaining items are blocked on a decision,
deliberately deferred, or rejected, so the next batch starts with N4 or N5.

---

## 1. Status board

| ID | Task | Value | Effort | Status |
| --- | --- | --- | --- | --- |
| N1 | Give the GUI text and voice paths the project prompt | High | S–M | Done (D40) |
| N2 | Consume MCP `tools/list_changed` and revive a dead server | Medium | M | Done (D41) |
| N3 | Correct the stale claims in the two harness docs | Low | S | Done (with N1) |
| N4 | Filesystem/network confinement for `run_command` | High (design) | XL | Done (D43 / O-1) |
| N5 | Parallel / model-selecting subagents | Medium (design) | L | Done (D44 / O-2) |
| N6 | MCP resources, prompts, sampling | Low | M | Deliberately deferred |
| N7 | Executable skills / `allowed-tools` enforcement | Low | M | Rejected (would be a second boundary) |
| N8 | Unify harness skills with the core Skills Library | Medium (design) | M | Done (D42) |

Ordered by value per unit of risk, not by size. N1, N3, N2 and N8 landed in
three batches. The remaining items are blocked on a decision, deliberately
deferred, or rejected: the next batch starts with N4 or N5.

---

## 2. N1 — Give the GUI text and voice paths the project prompt

**Landed.** Decision D40 in `AGENTIC-HARNESS-STATUS.md`; helper in
`agent/prompts.py:project_context()`, wired into `main.py:_agentic_seed_messages`
and `core/session_lifecycle.py:_build_config`. The section below is kept as the
record of what was verified.

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
4. (Landed as silence.) No per-turn `instructions` event: the base prompt is
   rebuilt every turn, so it would be noise. The catalogue is inspectable with
   `python -m agent skills`; subdirectory loads still emit `instructions` when
   they actually add a file.
5. Do **not** copy the blocks into the transcript. They are system-prompt text;
   resume and compaction must stay untouched (D27).

### 2.4 Acceptance criteria

- [x] A GUI text turn in a workspace with `AGENTS.md` sends that text in the
      `system` message of its **first** provider request.
- [x] The same turn lists project skills (`name: description`) when
      `.brahma/skills/*/SKILL.md` exists.
- [x] A voice session's `LiveConnectConfig.system_instruction` contains the same
      two blocks.
- [x] A workspace with neither file produces no extra blank sections.
- [x] CLI and subagent prompts are unchanged (existing prompt tests still pass).
- [x] `agent/` still imports with only pytest installed; `agent/__init__.py`
      stays empty; no PyQt import from `agent/`.

### 2.5 Tests

- `tests/test_agent_prompts.py`: `project_context` composition, the empty
  workspace, and the guarantee that it is the workspace half of
  `build_system_prompt`.
- `tests/test_gui_prompt_wiring.py` (imports `main`, outside the harness CI
  job): the blocks in `_agentic_seed_messages`, both discovery branches, a
  workspace with no files, and runtime workspace over process cwd.
- `tests/test_voice_prompt_wiring.py` (imports `core.session_lifecycle`,
  outside the harness CI job): the blocks in the assembled
  `LiveConnectConfig.system_instruction`, and no section when the workspace has
  none.

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

**Landed.** Decision D41 in `AGENTIC-HARNESS-STATUS.md`;
`MCPSession.needs_refresh`/`refresh` in `agent/mcp.py`, called from
`WorkspaceMCP.ensure` on the unchanged-fingerprint path. The section below is
kept as the record of what was verified.

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

- [x] A fake stdio server that emits `tools/list_changed` and answers a new
      `tools/list` has its new tool callable on the next `ensure()`.
- [x] A removed tool is unregistered and no longer advertised.
- [x] A killed server is restarted on the next `ensure()`, and a still-broken
      server fails one call with its stderr tail rather than hanging.
- [x] Two servers, one changed and one not: the unchanged one is not restarted.
- [x] `close()` still unregisters everything; no spec leaks across tests.

### 3.4 Tests

Extend `tests/test_agent_mcp.py` (it already runs against a real fake stdio
server): add a `list_changed` capability to the fake, plus restart and
partial-refresh cases. Keep it in the stdlib-only CI job. Landed as six tests:
a healthy session is never refreshed, a notification re-lists without
restarting (added and removed tools both checked), a killed server restarts
once and is callable again, only the changed server in a two-server config is
refreshed, a failed re-list drops the tools and reports the stderr tail once,
and a refresh leaves no duplicate or orphaned specs.

---

## 4. N3 — Correct the stale claims

**Landed with N1.** Both docs were corrected to say the notification is latched
(and, since N2, acted on at the turn boundary) and that the project blocks reach
the HUD text and Live prompts. The table below is the record of what was wrong.

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
N8 was decided and is kept below as the record of the disagreement it resolved.

### N4 — Filesystem/network confinement

**Landed (D43 / O-1).** `agent/sandbox.py` implements active behavioural
confinement probing (`probe_confinement`, `check_confinement`) and the
`python -m agent sandbox doctor` CLI subcommand. Plain host shells and WSL2
launchers are rejected up front (`is_known_unconfined_prefix`), and the probe
actively tests (1) writing outside the workspace mount (`write_outside`),
(2) loopback network egress (`network_egress`), and (3) UID / host-namespace
isolation (`uid_isolation`). `exec.sandbox.require_confined` defaults to `False`
(the doctor reports only and never writes policy); when `require_confined` is
`True` (or `--yolo` is passed), `PermissionPolicy.evaluate` denies `exec` calls
with `rule="exec.sandbox.require_confined"` unless `check_confinement` passes.

### N5 — Parallel / model-selecting subagents

**Landed (D44 / O-2).** Mutation remains sequential through `run_subagent`,
while `run_subagent_batch` (`agent/subagent.py`, `agent/loop.py:_run_subagent_batch`,
and `AgentRuntime.decide(..., batch_readonly=True)`) provides parallel read-only
fan-out (1..8 tasks) under a strict single-runtime safety contract:
(1) child tool surface restricted to `tool_class == "read"` with `classifier is None`
and `"desktop" not in tags` (`select_batch_tools`),
(2) path arguments restricted strictly to inside `runtime.workspace` (`paths.outside_workspace`
and `paths.protected` denied without prompt, ignoring `trusted_roots` and `session_allow`),
(3) `requires_approval=True` tools excluded,
(4) both `run_subagent` and `run_subagent_batch` (`DELEGATION_TOOLS`) excluded from
every child's surface (`I-08`), and
(5) all children share the parent's `AgentRuntime` and `cancel_event`.

---

### Landed

| Batch | Commit | Summary |
| --- | --- | --- |
| N1 + N3 | `f42f4b2` | `agent.prompts.project_context()`; wired into the HUD text seed and the Live `_build_config`; docs corrected (D40). |
| N2 | `4f81a65` | `MCPSession.needs_refresh`/`refresh`; `WorkspaceMCP.ensure` re-lists or restarts a stale server on the unchanged-fingerprint path; `mcp_refresh` event (D41). |
| N8 | `531b961` | `agent.skills.project_roots()`; `SkillsLibrary` scans workspace `.brahma/skills` as the last, instructional-only source; `configure_library(project_workspace=...)` wired at startup (D42). |
| N4 (O-1) | `8214ca4` | `probe_confinement`/`check_confinement` active behavioural probe (write-outside, network egress, UID/namespace isolation), `python -m agent sandbox doctor`, and `exec.sandbox.require_confined` policy gate (D43). |
| N5 (O-2) | `0b48614` | `run_subagent_batch` parallel read-only fan-out under strict workspace-confinement, no-approval, and non-recursive (`DELEGATION_TOOLS`) enforcement (D44). |
