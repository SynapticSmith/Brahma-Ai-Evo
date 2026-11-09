# Brahma Evo - Capability Maximization: Analysis, Ideation, and Roadmap

**Status:** Analysis and proposal. No implementation.
**Date:** 2026-11-09
**Baseline:** `main` @ `951e4b7`; working tree clean except this document and
`docs/REBRAND-EXECUTION-PLAN.md`.
**Scope:** whole-system capability, not security remediation and not rebrand
execution. Those are tracked in `docs/SYSTEM-ANALYSIS-MASTER-REPORT.md`,
`docs/REMEDIATION-TRACKER.md`, and `docs/REBRAND-EXECUTION-PLAN.md`.
**Companions:** `docs/ARCHITECTURE-SPEC.md` (the map),
`docs/AGENTIC-HARNESS.md` (harness contract),
`docs/SWARM-FEDERATION-INVESTIGATION.md` (federation),
`docs/TOOL-RETRIEVAL-DESIGN.md` (retrieval).

---

## 0. How to use this document

This is an **ideation and sequencing document**, not an authority. When it
disagrees with `ARCHITECTURE-SPEC.md` or with the code, believe the code.

- **§4** is the baseline scorecard to beat. Every later proposal is justified
  against a dimension in that table.
- **§5** lists the thirteen mechanisms that currently cap capability. If a
  proposal does not attack one of them, it is probably surface work.
- **§7** is the menu. **§8** is the ranked order. **§10** is the plan.
- **§14** lists the owner decisions that change the ranking.

Evidence is marked inline:

| Mark | Meaning |
|---|---|
| **[V]** | Verified in the tree during this analysis (file:line). |
| **[D]** | Documented by an authoritative repo document; not re-run. |
| **[I]** | Inferred from the code or from standard practice; a hypothesis to test. |

The counts in §3 were produced by `Select-String` over the working tree and
exclude `dist/` and `__pycache__/`.

---

## 1. Executive summary

### 1.1 Verdict

Brahma Evo is **broad, safe, and unusually well-governed**. It is **not yet a
"most capable" agent** in the sense that matters, because capability is
**unmeasured and unverified**.

The binding constraint is not the number of tools. It is the absence of a
**quality loop**: the system cannot reliably tell whether it succeeded, cannot
replay a failure, cannot measure a change, and does not retain what it learned.
Everything else - planning, memory, routing, depth - compounds only after that
loop exists.

### 1.2 Five findings

1. **Completion is a guess.** The loop stops when the model returns no tool
   calls (`agent/loop.py:1082-1085`). Nothing checks the work. There is no
   verification gate, no acceptance criteria, no post-condition. **[V]**
2. **Long tasks have no plan.** `AgentLoop` holds no plan, todo, or progress
   state. The only "plan" is a fixed four-line UI placeholder
   (`main.py:684-698`). **[V]**
3. **Nothing measures agent behaviour.** There is a tool-retrieval gold set
   (`tests/test_tool_retrieval_eval.py`) but no scenario suite for tool choice,
   recovery, stop behaviour, or long-horizon tasks. There are events and session
   rows, but no spans, trace IDs, token/cost accounting, or replay. **[V]**
4. **Memory is split and lexical.** Three disjoint stores
   (`memory/long_term.json`, `workspace_store.memories`, dead
   `memory_cortex.db`), substring recall, no embeddings, no consolidation, no
   provenance. The harness path does not inject long-term memory at all.
   **[V]**
5. **Breadth outran depth.** 59 native declarations, 97 desktop-wave keys
   (including aliases), 9 harness specs, 57 action modules, 620 subagent files
   - but Calendar/Drive are local stubs, MCP ships disabled, and computer-use
   is a single-shot "find one element, click it". **[V]**

### 1.3 The three moves that matter

| Move | Why first | Outcome |
|---|---|---|
| **Measure** - eval harness + trace store | You cannot improve what you cannot see or replay | A baseline, a regression gate, and a failure taxonomy |
| **Verify** - make "done" mean done | An unverified agent is a liability that gets worse as autonomy grows | A completion gate tied to evidence |
| **Plan + remember** - plan state + one memory service | These are the two missing inputs to every long task | Multi-step work that does not drift and does not forget |

Depth, routing, and reach come after these three. Doing them first produces a
wider system that is still unverifiable.

### 1.4 Where the system sits

On the ladder in §2.2, Brahma is at **L1.7**: it does multi-step tool use, but
without planning or verification. The realistic target is **L3 (verified
multi-step)** in two quarters and **L4 (bounded long-horizon autonomy)** in
four, not by adding tools but by closing the loop.

---

## 2. What "most capable" means

### 2.1 A working definition

> Capability is the probability that the system completes a **valuable task**
> correctly, within **budget**, with **recoverable** failures, and gets
> **better** at it next time.

That is deliberately not "number of things it can do". A system with 200 tools
and no verification is less capable than one with 40 tools and a test gate,
because the second can be trusted with more.

A compact model:

```
C = (Breadth x Reliability x Autonomy x Learning) / (Friction x Risk)
```

- **Breadth** - domains reachable (high here).
- **Reliability** - correct on the first or second attempt (medium-low here).
- **Autonomy** - how long it can run without a human (low here).
- **Learning** - whether outcomes change future behaviour (very low here).
- **Friction** - approvals, latency, setup, ambiguity (medium-high here).
- **Risk** - blast radius of a wrong action (medium, well-governed here).

The leverage is in the numerator terms that are currently near zero:
reliability, autonomy, learning. Adding breadth to a low-reliability system
raises risk faster than capability.

### 2.2 The capability ladder

| Level | Name | Test that must pass | Brahma today |
|---|---|---|---|
| **L0** | Chat | Answers questions | Passed |
| **L1** | Tool use | Chooses a tool and uses it correctly in one step | Passed |
| **L2** | Multi-step | Completes a 3-10 step task with recovery from one tool error | **Partial (1.7)** |
| **L3** | Verified multi-step | Proves completion against a check; refuses to claim done on failure | Not yet |
| **L4** | Bounded long-horizon | Runs 30+ minutes / 50+ turns with checkpoints, budgets, and resumability | Not yet |
| **L5** | Self-improving | Uses traces and evals to change its own routing, prompts, or tools, with human approval | Not yet |

The jump from L1.7 to L3 is not a model upgrade. It is **plan state + a
verification gate + a measurement loop**. That is the core thesis of this
document.

### 2.3 Reach, reliability, leverage

Three different kinds of capability are routinely confused:

| Kind | Question | Current investment | Marginal value now |
|---|---|---|---|
| **Reach** | Can it do X at all? | Very high (57 action modules, 620 subagents) | Low - most X already exist |
| **Reliability** | Does it do X correctly, repeatedly? | Low | **Very high** |
| **Leverage** | Does doing X make the next X easier? | Almost none | **Highest** |

Most of the ideas in §7 are reliability or leverage work. The few reach ideas
that survive are the ones where the domain is genuinely absent (OAuth depth,
durable scheduling, federation) rather than merely thin.

### 2.4 Non-goals

- **Not** maximizing tool count or agent count.
- **Not** unbounded autonomy. Every autonomous run gets a budget, a policy, and
  an audit trail.
- **Not** a second execution runtime, a second permission boundary, or a
  second memory store.
- **Not** a rewrite. The existing seams (`AgentRuntime`, `ToolSpec`,
  `run_captured`, `ToolIndex`, `AgentBridge`, `brahma_api`) are the right
  foundations; the work is additive.

---

## 3. Method and evidence base

### 3.1 What was examined

- `docs/ARCHITECTURE-SPEC.md` (capability matrix, subsystem contracts, work
  queues, invariants).
- `docs/AGENTIC-HARNESS.md`, `docs/AGENTIC-HARNESS-NEXT.md`,
  `docs/AGENTIC-HARNESS-STATUS.md` (loop, decisions, limits).
- `docs/SWARM-FEDERATION-INVESTIGATION.md` (federation gap).
- `docs/REBRAND-EXECUTION-PLAN.md` (branding seam and migration surface).
- The code: `agent/`, `core/`, `memory/`, `actions/`, `features/`,
  `brahma_api/`, `brahma_connect/`, `core/agent_bridge/`, `main.py`,
  `tests/`.

### 3.2 Verified counts

| Surface | Count | Anchor |
|---|---:|---|
| Native tool declarations | 59 | `main.py:757` onward |
| Desktop-wave registry keys (incl. aliases) | 97 | `core/desktop_tools.py` WAVE1-3 |
| Harness code tools | 6 + 3 delegation/skill = 9 | `agent/code_tools.py`, `agent/subagent.py` |
| Action modules | 57 | `actions/` |
| Feature modules | 21 | `features/` |
| Subagent library files | 620 | `subagents/` |
| Smart-home provider classes | 8 | `smart_home/providers/builtin.py` |
| Test files | 143 | `tests/test_*.py` |
| Documented test baseline | 1,020 passed, 5 skipped | `ARCHITECTURE-SPEC.md` App. C **[D]** |

### 3.3 The surface math

`core/tool_index.py` is the gate between the catalogue and the model:

- `GEMINI_MAX_TOOLS = 128` (line 92)
- `DEFAULT_ADVERTISED_BUDGET = 24` (line 96)
- 5 pinned tools (`undo`, `recall_memory`, `read_file`, `edit_file`,
  `run_command`) (lines 100-109)
- 8 meta-tools (`search_tools`, `call_tool`, `get_skill_manual`,
  `list_tools`, `search_skills`, `load_skill`, `list_skills`,
  `get_tool_surface`) (lines 129-138)

Maximum resident surface = **5 + 8 + 24 = 37**. The voice path advertises
roughly **61** base declarations (54 native + 7 harness); the text path adds
the two dispatch tools (**63**). Everything else is reachable only through
`search_tools` -> `call_tool`. **[V]**

That is a sound design, but it means **most of the catalogue is invisible to
the model at decision time**. The catalogue is roughly **110+ names** (97
desktop-wave keys including aliases, 9 harness specs, plus dynamic and MCP
tools). Capability is therefore gated by retrieval quality and by the model
knowing that a capability exists - not by whether the handler is implemented.

### 3.4 Evidence classes and limits

- No gates were run for this document. The 1,020-test baseline is quoted from
  the spec, not re-executed.
- Counts are structural, not semantic. A module existing is not proof it works;
  §5 and Appendix A distinguish implemented from stub where the code made it
  clear.
- "Stub" in this document means "the code path does not reach the real
  service", not "the author intended it as a stub". Several stubs are honest
  local fallbacks; the problem is that the UI and tool description do not say
  so.

---

## 4. Capability scorecard

### 4.1 Scores

Scale: 0 = absent, 1 = token, 2 = partial, 3 = usable with a stated limit,
4 = strong, 5 = best-in-class.

| # | Dimension | Score | Evidence | Target (12 mo) |
|---|---|---:|---|---:|
| 1 | **Context & perception** | 2.5 | Good resident prompt, instruction walk, tool index; but no memory in the harness, no document RAG, char-based context | 4.0 |
| 2 | **Planning & decomposition** | 1.5 | No plan state; fixed UI placeholder; one-shot subagents | 3.5 |
| 3 | **Action breadth** | 4.0 | 57 action modules, 59 natives, browser, OS, smart home, Android, agents | 4.5 |
| 4 | **Action depth & reliability** | 2.5 | Calendar/Drive local stubs, MCP disabled by default, single-shot computer-use | 4.0 |
| 5 | **Verification & self-correction** | 1.0 | Prompt-only guidance; dormant `core/execution_reflection.py` | 4.0 |
| 6 | **Memory & learning** | 1.5 | Three stores, lexical recall, no consolidation, harness amnesia | 4.0 |
| 7 | **Orchestration & parallelism** | 2.5 | Bounded subagents, read-only batch, AgentBridge; no model/role selection, no durable queue | 4.0 |
| 8 | **Observability & evaluation** | 1.0 | Events + session rows; no spans, traces, cost, or behaviour evals | 4.0 |
| 9 | **Efficiency & cost control** | 1.5 | Char budgets, no token/cost accounting, no cheap/strong routing, no caching | 3.5 |
| 10 | **Safety & governance** | 4.0 | Policy, approvals, audit chain, sandbox probe, red-team suite | 4.5 |
| | **Composite** | **2.2** | | **3.9** |

### 4.2 Reading the scorecard

- **The strongest asset is safety (4.0).** It is also the enabler: autonomy is
  only valuable if it is governable. Do not trade it for reach.
- **The three zeros are the story.** Verification (1.0), observability (1.0),
  and learning (1.5) are the dimensions that compound. They are also the
  cheapest to move because the infrastructure already exists (events, sessions,
  `run_captured`, `ToolSpec`).
- **Breadth is already high (4.0) and depth is not (2.5).** The next unit of
  work should almost never be "add a tool".
- **Context is mediocre (2.5) despite a good prompt**, because the agentic
  path drops memory and the context budget is character-based. The prompt is
  not the problem; the inputs are.

### 4.3 What a 4.0 composite would feel like

- The system says "I ran the tests and they pass" and attaches the output.
- It can be handed a 45-minute task, stopped, resumed, and audited.
- It remembers a preference from last month and can cite when it learned it.
- A change to the loop is gated by a 60-scenario suite, not by vibes.
- It picks the cheap model for reading and the strong model for planning.
- It refuses to claim success when the evidence is missing.

That is the product this roadmap is aiming at.

---

## 5. Thirteen capability ceilings

Each ceiling is a mechanism, not a bug. Fixing the symptom without the
mechanism moves the number for a week.

### F1 - Unverified completion

**Symptom:** "Done" means the model stopped calling tools.
**Mechanism:** `AgentLoop.run` exits `STOP_COMPLETED` when a response has no
`tool_calls` (`agent/loop.py:1082-1085`). No acceptance check, no post-condition,
no evidence requirement.
**Evidence:** **[V]**; the only "verification" is prompt guidance
(`agent/prompts.py:149-161`) and a dormant reflection module
(`core/execution_reflection.py`, marked `DORMANT` in `core/routing.py:332-338`).
**Cost:** every downstream autonomy feature inherits a false-success rate. The
more the system does unattended, the more expensive this becomes.
**Blocks:** L3, L4, trustworthy delegation, API consumers.

### F2 - No plan state

**Symptom:** long tasks drift, repeat steps, and lose the thread after
compaction.
**Mechanism:** no plan/todo object survives a turn. `_build_task_plan`
(`main.py:684-698`) is a fixed four-line placeholder and is documented as
deliberately non-authoritative.
**Evidence:** **[V]**.
**Cost:** the model must re-derive intent every turn from a compacted
transcript; progress is unmeasurable; "what is left?" has no answer.
**Blocks:** long-horizon work, resumability, meaningful progress UI.

### F3 - No behavioural measurement

**Symptom:** changes to prompts, tools, or the loop are judged by anecdote.
**Mechanism:** the only eval-shaped test is a retrieval gold set
(`tests/test_tool_retrieval_eval.py`). There is no scenario corpus, no
deterministic harness, no success metric, no regression gate for agent
behaviour.
**Evidence:** **[V]**.
**Cost:** every improvement is a coin flip; regressions are found by users.
**Blocks:** everything in §6.

### F4 - No trace or cost visibility

**Symptom:** you cannot answer "why did it do that?" or "what did that cost?"
**Mechanism:** events are dicts emitted to listeners; sessions store messages;
`agent/usage.py` records provider-reported tokens with no pricing. No trace ID,
span, latency breakdown, tool timing, or per-run cost.
**Evidence:** **[V]** (`agent/loop.py:1214-1229`, `agent/usage.py:59-98`).
**Cost:** debugging is manual; cost regressions are invisible; model routing
cannot be optimized.
**Blocks:** reliability engineering, cost control, eval attribution.

### F5 - Split, lexical memory

**Symptom:** the assistant forgets across surfaces and cannot recall by meaning.
**Mechanism:** three stores with different write paths
(`memory/memory_manager.py`, `workspace_store.memories`,
`core/memory_cortex.py`), substring scoring in the live store
(`memory/memory_manager.py:424-490`), no embeddings, no dedup/consolidation, and the
harness path injects nothing.
**Evidence:** **[V]**; `memory/memory_manager.py:425` says "No embeddings".
**Cost:** personalization is shallow; cross-session work restarts from zero;
the same question gets different answers by modality.
**Blocks:** L4 continuity, user model, learning.

### F6 - No document RAG

**Symptom:** the system can read one file but cannot answer questions over a
workspace, a docs tree, or a PDF set with citations.
**Mechanism:** no ingestion, chunking, embedding, index, or citation path.
`actions/file_processor.py` summarizes one file ad hoc.
**Evidence:** **[V]** (no RAG module; `document_rag` appears only in a design
doc).
**Cost:** research and coding tasks lose the highest-value context; the user
must paste text.
**Blocks:** research workflows, repo Q&A, "ask my documents".

### F7 - Char-based context and lossy truncation

**Symptom:** long tool outputs are cut at 12k chars; context is compacted by
character count; token cost is estimated, not measured.
**Mechanism:** `ContextBudget` uses `max_chars=120_000`,
`max_tool_output_chars=12_000`, `summary_render_chars=24_000`, and a
30k-char compaction target (`agent/context.py:63-80`). No artifact spill in the
loop.
**Evidence:** **[V]**.
**Cost:** evidence disappears exactly when the task is large enough to need it;
compaction quality is not measured.
**Blocks:** long-horizon tasks, codebase work, cost predictability.

### F8 - Surface tax and duplicates

**Symptom:** the model picks the wrong tool or cannot find the right one;
descriptions collide.
**Mechanism:** roughly 110+ catalogue names, 37 resident, several near-duplicates:
`web_search` (native vs harness), `desktop_control` / `smart_organizer` /
`desktop_organizer` / `file_controller`, `system_manager` /
`system_diagnostics`, `dev_agent` / `brahma_dev_agent` / `code_helper` /
`claude_code_bridge`, `spotify_controller` vs `features/spotify_mcp.py`.
**Evidence:** **[V]** (inventory in Appendix A).
**Cost:** selection accuracy falls as the catalogue grows; prompt budget is
spent on redundant descriptions; the user sees inconsistent behaviour.
**Blocks:** reliable tool choice, small-model routing.

### F9 - Uneven integration depth

**Symptom:** some integrations are real, some are local fallbacks wearing the
same name.
**Mechanism:** Gmail is app-password IMAP/SMTP, not OAuth; Calendar is local
JSON + `.ics`; Drive is a local folder; MCP ships with every example disabled;
computer-use is a single screenshot + one click; several smart-home providers
are credential stubs.
**Evidence:** **[V]** (`actions/google_workspace_mcp.py:123-333`,
`config/mcp_servers.example.json`, `actions/computer_control.py:305-451`).
**Cost:** trust erosion. A tool that silently degrades is worse than a missing
tool.
**Blocks:** real personal-assistant workflows.

### F10 - Non-durable proactivity

**Symptom:** reminders and monitors do not survive a reboot or span devices;
"do this every Monday" is not a primitive.
**Mechanism:** `schtasks` for reminders, in-process monitors, a sentinel thread,
a 3-second telemetry poll. No durable job queue, no cron abstraction, no
cross-device schedule.
**Evidence:** **[V]** (`actions/reminder.py`, `core/anticipatory_watcher.py`,
`core/sensorium.py`).
**Cost:** the system cannot be trusted with recurring work; proactivity is
best-effort.
**Blocks:** L4, ambient assistant, cross-device continuity.

### F11 - Single-model execution

**Symptom:** the same model plans, reads, writes, and verifies; cost and
latency are flat across task difficulty.
**Mechanism:** `resolve_chain("auto")` picks one provider; models are
passthrough defaults; no task-class routing, no per-subagent model, no
escalation-on-failure.
**Evidence:** **[V]** (`agent/providers.py:121-192`).
**Cost:** overpaying for trivial turns and underpowering hard ones; no way to
use a cheap verifier or a strong planner.
**Blocks:** cost control, quality tuning, local-first operation.

### F12 - Uniform, unverified subagents

**Symptom:** every child is the same shape; the 620-template library is a
catalogue, not a routing system; child output is trusted.
**Mechanism:** one `run_subagent` shape, 8/12-turn clamp, shared completer, no
per-child model or role, no success tracking per template, report-only return.
**Evidence:** **[V]** (`agent/subagent.py:43-49`, `139-217`).
**Cost:** delegation multiplies an unverified parent's errors; the library's
value is not measured.
**Blocks:** reliable fan-out, specialist agents, swarm.

### F13 - Federation readiness

**Symptom:** three machines can be wired together only as a hand-configured
client/server pair; there is no peer task server, no node identity, no
discovery, and no cross-node scheduler.
**Mechanism:** `core/agent_bridge/adapters/remote.py` is a genuine A2A
**client** (`/.well-known/agent.json`, `POST /tasks`, `GET /tasks/{id}`,
`POST /tasks/{id}/cancel`), and `AgentBridge.publish_tools()` already publishes
each peer verb as a `ToolSpec`. But nothing serves those routes. `brahma_api`
is a chat lane with profiles, limits, and audit - not a task API with ids,
status, deadlines, and cancellation. There is no node keypair, no pinned
transport, no capability advertisement, and no recursion guard.
**Evidence:** **[V]** `docs/SWARM-FEDERATION-INVESTIGATION.md` §2-§3;
`core/agent_bridge/adapters/remote.py:127-269`; `core/agent_bridge/bridge.py:55-57,147-177`.
**Cost:** the client half is built and tested, but it has nothing to call. The
gap is not model capability; it is identity, transport, scheduling, and
recursion control.
**Blocks:** G1, cross-device delegation, capability-aware routing across nodes.

---

## 6. The compounding loop

### 6.1 The flywheel

```
        +--------------------------------------------------+
        |                                                  |
        v                                                  |
   TRACE  --->  EVAL  --->  VERIFY  --->  ROUTE  --->  LEARN
   (what      (did it     (is the       (which model   (change
   happened)   pass?)      work real?)    /tool/agent?)  prompts,
        ^                                                  tools,
        |                                                  policy)
        +--------------------------------------------------+
```

Each stage is cheap only if the previous one exists:

| Stage | Without it | With it |
|---|---|---|
| **Trace** | Debugging is a transcript read | Every turn has a replayable record |
| **Eval** | Changes are judged by feel | A regression gate and a score |
| **Verify** | "Done" is a claim | "Done" is evidence |
| **Route** | One model for everything | Cheap where possible, strong where needed |
| **Learn** | The same failure repeats | Prompts/tools/policy improve from data |

The order is not arbitrary. Verification without traces is unverifiable;
routing without evals is guesswork; learning without verification learns the
wrong thing.

### 6.2 The three assets

The flywheel produces three durable assets, and they are the real moat:

1. **A scenario corpus** - versioned tasks with fixtures and pass criteria.
   This is the regression test for agent behaviour.
2. **A trace corpus** - real runs, privacy-scrubbed, replayable. This is the
   source of new scenarios and failure labels.
3. **A scorecard** - the ten dimensions in §4, tracked per release. This is how
   "most capable" becomes a number instead of an opinion.

### 6.3 What "verified" means concretely

A completion is verified when at least one of these is true, and the evidence
is attached to the final message:

- A configured **verify command** ran and exited 0 (tests, lint, build, a
  domain check).
- A **post-condition** on the tool result holds (file hash matches, HTTP 200,
  process alive, expected window title).
- A **read-back** confirms the write (the file contains the intended text).
- A **human approval** explicitly accepts the result.
- The task is **read-only** and the answer cites the tool results it used.

If none holds, the loop must stop with `STOP_UNVERIFIED` and say what is
missing. That single change is the difference between L2 and L3.

---

## 7. Ideation catalog

Eight programs, 55 ideas. Each idea is tagged with the ceiling it attacks
(§5), an effort estimate in person-weeks, and a leverage score (1-10, rubric
in §8.1). Detailed cards follow for the top 15.

### Program A - Measure (the compounding loop)

| ID | Idea | Attacks | Effort | Leverage |
|---|---|---|---:|---:|
| A1 | Agent eval harness with scenario suites and a deterministic stub provider | F3 | 1-2 | 10 |
| A2 | Trace store: per-turn spans, trace IDs, replay | F4 | 1 | 9 |
| A3 | Record/replay provider cassettes for offline regression | F3, F4 | 1-2 | 7 |
| A4 | Capability scorecard dashboard (ten dimensions) | F3, F4 | 0.5 | 6 |
| A5 | Failure taxonomy + auto-labeling of failed traces | F3, F4 | 1 | 7 |
| A6 | Golden tasks harvested from real sessions (scrubbed) | F3, F5 | 1 | 6 |

### Program B - Verify (make "done" mean done)

| ID | Idea | Attacks | Effort | Leverage |
|---|---|---|---:|---:|
| B1 | Verification gate after mutating turns (configurable command + verdict) | F1 | 1-2 | 9 |
| B2 | Explicit acceptance criteria per task, checked before completion | F1, F2 | 1-2 | 6 |
| B3 | Tool-level post-conditions (`verify` hook on `ToolSpec`) | F1, F9 | 1-2 | 6 |
| B4 | Regression-test synthesis after a fix (human-approved) | F1, F6 | 1-2 | 5 |
| B5 | Evidence ledger: every factual claim cites a tool result or memory id | F1, F5 | 1 | 6 |
| B6 | "Proof of work" completion: no ungrounded "done" | F1 | 0.5 | 7 |

### Program C - Plan (long-horizon competence)

| ID | Idea | Attacks | Effort | Leverage |
|---|---|---|---:|---:|
| C1 | Durable plan state (steps, status, dependencies, evidence) | F2 | 1-2 | 8 |
| C2 | Complexity-aware planner pass (classify, decompose, re-plan) | F2 | 2 | 6 |
| C3 | Completion gate requires all plan steps resolved | F1, F2 | 0.5 | 6 |
| C4 | Role/model-specialized subagents (researcher, implementer, reviewer) | F12, F11 | 1-2 | 6 |
| C5 | Durable job queue + scheduler (survives reboot, cron + event triggers) | F10 | 2-3 | 7 |
| C6 | Subagent library as a routed, success-tracked capability catalogue | F12 | 1-2 | 5 |
| C7 | Checkpoint/resume for long tasks (plan + context + artifacts) | F2, F7 | 2 | 5 |

### Program D - Remember (context that compounds)

| ID | Idea | Attacks | Effort | Leverage |
|---|---|---|---:|---:|
| D1 | One memory service (SQLite WAL + FTS5 + optional embeddings) | F5 | 2-3 | 8 |
| D2 | Hybrid retrieval: RRF + recency/importance/access + MMR | F5 | 1-2 | 8 |
| D3 | Provenance, confidence, and supersede chains | F5 | 1 | 6 |
| D4 | Consolidation/decay job (merge, summarize episodes, prune) | F5 | 1-2 | 6 |
| D5 | Harness integration: memory block + pinned save/recall tools | F5, F1 | 1 | 8 |
| D6 | Document RAG: ingest, chunk, embed, cite (`search_documents`) | F6 | 2-3 | 7 |
| D7 | Persist and re-inject compaction summaries on resume | F7, F5 | 0.5 | 5 |
| D8 | User-editable memory with "why do you know this?" and forget | F5 | 1 | 5 |
| D9 | Explicit user/preference model separate from facts | F5 | 1-2 | 4 |

### Program E - Deepen (turn breadth into reliability)

| ID | Idea | Attacks | Effort | Leverage |
|---|---|---|---:|---:|
| E1 | Integration tiering: `verified` / `experimental` / `stub`, hidden by default | F9 | 1 | 7 |
| E2 | Real Google Workspace OAuth (Gmail, Calendar, Drive, Contacts) | F9 | 3-4 | 6 |
| E3 | First-class messaging (Slack, Telegram, WhatsApp, SMS) via official APIs | F9 | 3-4 | 5 |
| E4 | Computer-use loop: UIA grounding + perceive/act/verify + retries | F9, F1 | 3-4 | 6 |
| E5 | First-class git/GitHub/Postgres tools; enable MCP examples with setup | F9 | 2 | 6 |
| E6 | Browser depth: profiles, auth, downloads, extraction, screenshot diff | F9 | 2-3 | 5 |
| E7 | Consolidate duplicate tools and lint descriptions for collisions | F8 | 1-2 | 7 |
| E8 | Tool description quality program (examples, negatives, golden queries) | F8 | 1 | 6 |
| E9 | Multimodal ingestion: images, PDFs, screenshots in the harness | F6 | 2-3 | 5 |

### Program F - Route and economize

| ID | Idea | Attacks | Effort | Leverage |
|---|---|---|---:|---:|
| F1 | Token + cost accounting with per-run budgets and hard stops | F4, F11 | 1 | 7 |
| F2 | Capability-based model routing (cheap read, strong plan/write, escalate) | F11 | 1-2 | 6 |
| F3 | Provider health, cooldowns, and error-class routing in the harness | F11 | 1 | 5 |
| F4 | Prompt/context caching with a stable prefix | F11 | 1 | 5 |
| F5 | Artifact spill instead of 12k truncation | F7 | 1 | 6 |
| F6 | Preflight diagnostics: providers, tools, permissions, sandbox | F9 | 1 | 5 |
| F7 | Verification-as-a-service in `brahma_api` (verdict + artifacts) | F1 | 1-2 | 5 |

### Program G - Reach (new surfaces, last)

| ID | Idea | Attacks | Effort | Leverage |
|---|---|---|---:|---:|
| G1 | Federation L2: peer task server on `brahma_api`, identity, discovery, scheduler | F10, F12, F13 | 4-6 | 5 |
| G2 | Cross-device continuity: session handoff, push, shared artifacts | F10 | 3-4 | 5 |
| G3 | Android depth: task delegation, notifications, offline queue | F10 | 3-4 | 4 |
| G4 | Household/multi-user profiles with scoped memory and permissions | F5 | 3-4 | 4 |
| G5 | Real plugin ecosystem: signed bundles, lifecycle, marketplace | F8 | 4-6 | 3 |
| G6 | Skill forge promotion UX + executable-skill sandbox (WS-1/WS-2) | F9 | 3-4 | 5 |

G1's effort is the swarm document's S1-S5 estimate (13-22 engineer-days) plus
hardening, tests, and the operator UI. It splits: **S1** (manual peers over the
existing read-only API) is a prototype that can start after B1; **S2+** (task
server, identity, discovery, scheduler) waits for C5.

### Program H - Trust and control (the enabler)

| ID | Idea | Attacks | Effort | Leverage |
|---|---|---|---:|---:|
| H1 | Approval UX: batch, pattern approvals, risk explanation, undo | F8 | 1-2 | 6 |
| H2 | Capability grants per tool/session using `core/capabilities.py` + `core/grants.py` | F9 | 2 | 5 |
| H3 | Audit explorer: searchable decisions, "why did you do that?" | F4 | 1-2 | 5 |
| H4 | Data governance: retention, redaction, export/delete | F5 | 1-2 | 4 |
| H5 | Autonomous-run kill switch + budget guard | F10 | 1 | 6 |

### 7.1 Wildcards (horizon 3, do not start yet)

These are plausible differentiators but depend on L3/L4 being real first.

| ID | Idea | Why later |
|---|---|---|
| W1 | Speculative tool synthesis with a verification gate | Needs B1 + sandbox; otherwise it is the forge again |
| W2 | Self-tuning tool descriptions from failed retrievals | Needs A1/A2 data |
| W3 | Memory-as-code: turn repeated procedures into project skills | Needs D1/D4 and a promotion path |
| W4 | Multi-model debate / critic ensembles | Costly; only after routing and evals |
| W5 | Proactive agenda from calendar, email, and open tasks | Needs E2 + C5 + privacy design |
| W6 | Ambient capture (meetings, screen, audio) with consent | High privacy risk; separate design |
| W7 | Agent-to-agent task market on the federation layer | Needs G1 + identity + budgets |
| W8 | Local-first routing with a small on-device model | Needs F2 + evals to prove parity |

---

## 8. Ranked initiatives

### 8.1 Scoring rubric

**Leverage (1-10)** is a judgement, not a formula:

- **9-10** - changes what the system can do per unit of work; unlocks a level.
- **7-8** - removes a ceiling that blocks several other ideas.
- **5-6** - meaningful improvement, contained blast radius.
- **3-4** - nice to have; does not change the ceiling.
- **1-2** - cosmetic or redundant.

Effort is person-weeks for one experienced engineer, including tests and docs.
Risk is implementation risk, not product risk.

### 8.2 Top 20

| Rank | ID | Initiative | Leverage | Effort | Depends on |
|---:|---|---|---:|---:|---|
| 1 | A1 | Agent eval harness | 10 | 1-2 | - |
| 2 | A2 | Trace store + replay | 9 | 1 | - |
| 3 | B1 | Verification gate | 9 | 1-2 | A1 |
| 4 | C1 | Durable plan state | 8 | 1-2 | - |
| 5 | D1 | One memory service | 8 | 2-3 | - |
| 6 | D2 | Hybrid memory retrieval | 8 | 1-2 | D1 |
| 7 | D5 | Harness memory integration | 8 | 1 | D1, D2 |
| 8 | F1 | Token + cost accounting | 7 | 1 | A2 |
| 9 | A5 | Failure taxonomy | 7 | 1 | A2 |
| 10 | B6 | Proof-of-work completion | 7 | 0.5 | B1 |
| 11 | C5 | Durable scheduler | 7 | 2-3 | C1 |
| 12 | E7 | Tool consolidation + description lint | 7 | 1-2 | A1 |
| 13 | D6 | Document RAG | 7 | 2-3 | D1 |
| 14 | E1 | Integration tiering | 7 | 1 | - |
| 15 | C3 | Plan-aware completion | 6 | 0.5 | C1, B1 |
| 16 | B2 | Acceptance criteria | 6 | 1-2 | C1 |
| 17 | F2 | Model routing | 6 | 1-2 | F1, A1 |
| 18 | C4 | Role/model subagents | 6 | 1-2 | C1, F2 |
| 19 | E4 | Computer-use loop | 6 | 3-4 | B3 |
| 20 | E2 | Google Workspace OAuth | 6 | 3-4 | E1 |

### 8.3 Sequencing constraints

```
A1 (evals) ---> B1 (verify) ---> B6 (proof of work)
   |                |
   |                +--> C3 (plan-aware completion)
   |                +--> B2 (acceptance criteria)
   |                +--> E4 (computer-use verify)
   |
   +--> A2 (traces) ---> A5 (taxonomy) ---> F1 (cost) ---> F2 (routing)
                                   |
                                   +--> A4 (scorecard)

C1 (plan) ---> C2 (planner) ---> C5 (scheduler) ---> G1 (federation)
        |
        +--> C4 (roles) ---> C6 (subagent routing)

D1 (memory) ---> D2 (hybrid) ---> D5 (harness) ---> D4 (consolidation)
        |
        +--> D6 (RAG) ---> E9 (multimodal)
```

Hard rules:

- **Do not start F2 (routing) before A1/A2.** Without a score, routing is
  superstition.
- **Do not start C5 (scheduler) before B1.** Unattended runs without
  verification are how trust is lost.
- **Do not start G1 (federation) before C5 + B1.** A swarm amplifies both
  success and failure; it needs a verified node first. The read-only S1
  prototype is the one exception: it can start after B1.
- **Do not start E4 (computer-use) before B3.** GUI actions need post-conditions
  more than desktop actions do.

### 8.4 Quick wins (one week or less)

| ID | Win | Why it is safe |
|---|---|---|
| A2 | Emit a trace JSONL per run | Additive; no behaviour change |
| B6 | Require evidence for completion claims | Prompt + one stop reason |
| C3 | Completion requires plan steps resolved | Only when a plan exists |
| D7 | Re-inject compaction summary on resume | Fixes a write-only field |
| E1 | Tier integrations and hide stubs | UI/metadata only |
| F5 | Spill large tool output to an artifact | Reuses the AgentBridge pattern |
| F6 | Preflight diagnostics | Read-only checks |

---

## 9. Target architecture

### 9.1 New modules and where they live

The invariant is that `agent/` stays stdlib-only and PyQt-free, and that no new
module becomes a second execution or permission path.

| Module | Layer | Responsibility | Stdlib? |
|---|---|---|---|
| `agent/trace.py` | harness | Span/trace records, JSONL sink, replay reader; carries `trace_id`, `origin_node`, `hop` from day one | Yes |
| `agent/evals.py` | harness | Scenario loader, runner, assertions, scoring | Yes |
| `agent/plan.py` | harness | Plan state, step transitions, completion gate | Yes |
| `agent/verify.py` | harness | Verification policy, command runner adapter, verdict | Yes |
| `agent/router.py` | harness | Task classification -> model/agent profile | Yes |
| `core/memory_service.py` | core | Canonical memory store, retrieval, consolidation | sqlite3 only |
| `core/doc_index.py` | core | Ingestion, chunking, FTS5 + optional embeddings | sqlite3 only |
| `core/model_router.py` | core | Price table, provider health, routing policy | Yes |
| `core/job_queue.py` | core | Durable jobs, cron/event triggers, budgets | sqlite3 only |
| `core/uia_grounding.py` | core | Windows UIA tree, element resolution, action verify | pywin32 (optional) |
| `evals/scenarios/` | data | Versioned scenario JSON + fixtures | n/a |
| `config/model_prices.json` | data | Per-model input/output prices | n/a |

Notes:

- `agent/evals.py` and `agent/plan.py` must import with only pytest installed.
  They may not import `core/`.
- `core/memory_service.py` is the only memory store. `memory/memory_manager.py`
  becomes a compatibility facade for one release, then is removed.
- `core/doc_index.py` may use `fastembed` when present and fall back to FTS5 +
  n-gram TF-IDF. It must never require a model to answer.
- `core/uia_grounding.py` is optional; `actions/computer_control.py` degrades to
  screenshot-only when it is unavailable, and says so.

### 9.2 Data flow: verification

```
AgentLoop turn
  -> tool calls (writes/exec)
  -> plan step marked "executed"
  -> VerifyPolicy selects a check
       |-- config verify.command (tests/lint/build)
       |-- ToolSpec.postcondition (read-back/hash/status)
       |-- human approval
       `-- read-only answer -> citation check
  -> run through agent.process.run_captured (policy, timeout, tree kill)
  -> verdict attached to the tool result
  -> completion allowed only if every required step is "verified"
```

The verify command is **not** a new tool and **not** a new permission boundary.
It is a `run_captured` call selected by policy, and it is subject to the same
approval rules as `run_command`.

### 9.3 Data flow: memory

```
voice / GUI / CLI / API
        |
        v
  MemoryService (core/memory_service.py)
        |-- write: fact, episode, preference, procedure
        |-- provenance: source, session, turn, extractor, confidence
        |-- index: FTS5 + optional embedding
        |-- consolidate: merge duplicates, summarize episodes, decay
        |
        +--> prompt block (budgeted, ranked)
        +--> recall_memory ToolSpec (pinned)
        +--> trace annotation ("memory ids used")
```

The harness reaches it through an injected port (the ADR-019 pattern:
`ToolContext.extra` / `default_extra`), not by importing `core/` from `agent/`.
That preserves the stdlib-only rule and keeps one store.

**Memory stays per node.** The swarm investigation's rule applies here: do not
replicate a memory database across machines, and do not put one on a network
share. A future cross-node feature is a read-only export or a task-scoped
artifact, not a shared SQLite file (`docs/SWARM-FEDERATION-INVESTIGATION.md`
§10).

### 9.4 Data flow: plan and scheduler

```
AgentLoop
  -> Plan (agent/plan.py) persisted in agent_plan_steps (additive table)
  -> Scheduler (core/job_queue.py) creates a job for deferred steps
  -> Job runs as a normal AgentLoop run under a policy profile
  -> Job result is an artifact + a plan-step update
  -> User sees "3 of 5 steps verified; 1 waiting for approval"
```

A scheduled job is **a normal run**, not a new runtime. It gets a budget, a
policy profile, a cancel handle, and an audit record. Unattended jobs default to
read-only or approval-required mutations.

`core/job_queue.py` is the **local** scheduler (C5). The swarm document's S5
scheduling phase is the **cross-node** layer on top of the same primitive; do
not build two queues. Node capability tags (`gpu`, `repo`, `os`) feed both the
scheduler and model routing.

### 9.5 Invariants this design must preserve

1. `AgentRuntime.execute` remains the only permission boundary.
2. `run_captured` remains the only process path.
3. New tools are `ToolSpec`s; no `elif` in `core/tool_exec.py`.
4. `agent/` imports with only pytest installed.
5. The system prompt is never persisted.
6. One memory store, one tool index, one MCP client per layer.
7. Every autonomous run has a budget, a cancel handle, and an audit trail.
8. No second execution path for verification, scheduling, or federation.
9. A peer call is an `exec`-class **fence**, not a parallel read: its side
   effects are unknown, so it never overlaps another tool call.
10. Sessions and memory stay per node. Cross-node exchange is a task-scoped
    prompt plus artifact references, never a shared `sessions.db`.

### 9.6 Migration and rollback

| Change | Migration | Rollback |
|---|---|---|
| Memory service | Read-through from the old JSON/workspace stores; dual-write for one release; backfill on first run | Keep the old facade; flip a config flag |
| Plan state | Additive `agent_plan_steps` table; no change to `agent_messages` | Drop the table; loop ignores it |
| Trace store | New files under the data root; no schema change | Delete the directory |
| Model routing | Config-driven; default is "single model" until enabled | Set `routing.enabled=false` |
| Scheduler | New `jobs` table; `schtasks` backend unchanged | Disable the queue; existing reminders still work |
| Tool consolidation | Aliases preserved for one release; deprecation warnings in the tool result | Restore the old names |

---

## 10. Roadmap

### Wave 0 - Measurement spine (weeks 1-2)

**Goal:** a baseline and a way to see regressions. No user-visible change.

Deliverables:

- `agent/trace.py` + trace JSONL for every run (CLI, GUI, API).
- `agent/evals.py` + `evals/scenarios/` with 30 scenarios:
  10 tool-choice, 8 multi-step file/repo, 5 recovery, 4 safety, 3 memory.
- `agent/__main__.py eval` subcommand and a CI job for the portable subset.
- Baseline scorecard (§4) with measured numbers, not judgements.
- `A5` failure taxonomy v1 (7 labels).

**Gate:** the suite runs in under 5 minutes with `--provider stub`; the live
subset runs nightly; the scorecard is committed.

**Risk:** scenario quality. Mitigation: start with tasks the repo already does
(edit a file, run tests, fix a lint error, answer from docs) and grow from real
traces.

### Wave 1 - Verified completion and plan (weeks 3-6)

**Goal:** L3 for bounded tasks.

Deliverables:

- `agent/verify.py` + `VerifyPolicy` in `agent_permissions.json`.
- `STOP_UNVERIFIED` and proof-of-work completion.
- `agent/plan.py` + plan persistence + `update_plan`/`get_plan` tools.
- Plan-aware completion (`C3`).
- `B2` acceptance criteria for tasks that opt in.
- `D7` compaction summary re-injection.

**Gate:** on the eval suite, false-done rate < 5%; plan adherence > 80% on
multi-step scenarios; no regression in the existing 1,020 tests.

**Risk:** verification commands are slow or flaky. Mitigation: per-project
config, timeouts, and a "verification unavailable" verdict that is honest
rather than a pass.

### Wave 2 - Memory and context (weeks 7-12)

**Goal:** continuity across sessions and surfaces.

Deliverables:

- `core/memory_service.py` with schema, provenance, dedup, and hybrid retrieval.
- Migration from `long_term.json` and `workspace_store.memories`; facade for one
  release.
- `D5` harness integration (memory block + pinned `save_memory`/`recall_memory`).
- `D4` consolidation/decay job.
- `D6` document RAG with citations.
- `F5` artifact spill.
- `E7` duplicate-tool consolidation and description lint.

**Gate:** memory recall precision@5 > 0.7 on a 50-query set; cross-session task
success improves by > 15 points vs Wave 1; tool-choice accuracy improves after
consolidation; context cost per task drops.

**Risk:** memory poisoning and prompt injection through stored content.
Mitigation: provenance, trust tiers, and instruction delimiters (ADR-020
pattern); never execute memory as instructions.

### Wave 3 - Depth, scheduling, and routing (weeks 13-20)

**Goal:** reliability on real workflows and cost control.

Deliverables:

- `E1` integration tiering and honest status.
- `E2` Google Workspace OAuth (Gmail, Calendar, Drive, Contacts).
- `E4` computer-use loop with UIA grounding and post-action verification.
- `C5` durable scheduler + `C4` role/model subagents.
- `F1` token/cost accounting + budgets; `F2` model routing; `F3` provider health.
- `H1` approval UX improvements.

**Gate:** OAuth E2E passes; computer-use success > 70% on a 20-task suite;
scheduled jobs survive reboot; cost per successful task drops > 25%; no policy
bypasses.

**Risk:** OAuth app verification and secret handling. Mitigation: DPAPI token
storage, minimal scopes, a documented local-only mode.

### Wave 4 - Reach and ecosystem (weeks 21+)

**Goal:** federation, cross-device, and an extensible ecosystem - only after
L3/L4 is real.

Deliverables:

- `G1` federation L2 per `docs/SWARM-FEDERATION-INVESTIGATION.md`.
- `G2` cross-device continuity and push.
- `G5` signed plugin bundles.
- `G6` skill promotion UX and executable-skill sandbox.
- `W2` self-tuning descriptions, `W3` memory-as-code (only with eval evidence).

**Gate:** a peer task completes across two machines with independent policy
enforcement; a failed peer is isolated; the swarm cannot exceed its budget.

### 10.1 Parallel tracks

| Track | Constraint |
|---|---|
| **Rebrand** | Phase 0 (legal) and Phase 1 (`core/branding.py`) land before any user-visible capability string. New capability names go through the branding seam. Data roots use `core/user_paths.get_user_data_dir()`. |
| **Security** | Verification, scheduling, and federation must not add a second execution path. Memory and RAG are prompt-injection surfaces. Computer-use is the highest-risk reach feature. |
| **Upstream** | No wholesale merge. Port features behind the same seams. |

### 10.2 Release alignment

| Release | Content | User-visible promise |
|---|---|---|
| **1.2** | Wave 0 + Wave 1 | "It checks its work" |
| **1.3** | Wave 2 | "It remembers" |
| **1.4** | Wave 3 | "It handles real workflows and budgets" |
| **2.0** | Wave 4 | "It works across your machines" |

Do not ship a capability claim before its gate passes. A release that says
"verified" and has a 20% false-done rate is worse than one that says nothing.

---

## 11. Metrics and benchmark design

### 11.1 Metric set

| Metric | Definition | Baseline | 12-mo target |
|---|---|---:|---:|
| Task success (pass@1) | Scenario passes on the first run | TBD (Wave 0) | > 75% |
| Task success (pass@3) | Passes within three attempts | TBD | > 90% |
| False-done rate | Claims complete but verification fails | TBD | < 3% |
| Tool-choice top-1 | Correct tool first | TBD | > 85% |
| Tool-choice top-3 | Correct tool in top 3 | TBD | > 95% |
| Argument validity | Calls with schema-valid args | TBD | > 98% |
| Recovery rate | Recovers from one injected tool error | TBD | > 70% |
| Plan adherence | Planned steps resolved / planned | TBD | > 85% |
| Memory precision@5 | Relevant memories in top 5 | TBD | > 0.7 |
| Cross-session recall | Task needs a fact from a prior session | TBD | > 80% |
| p50 / p95 turn latency | Wall clock per turn | TBD | -20% / -30% |
| Cost per successful task | USD, provider-reported + price table | TBD | -40% |
| Approval friction | Prompts per completed task | TBD | -30% |
| Policy bypasses | Denied action that executed | 0 | 0 |
| Crash-free sessions | No unhandled exception | TBD | > 99% |

"TBD" is deliberate: Wave 0 exists to replace guesses with numbers. Publishing a
target without a baseline is the failure mode this document is trying to end.

### 11.2 Eval suite design

Six suites, each a directory of JSON scenarios with a fixture workspace:

| Suite | Count (v1) | What it tests |
|---|---:|---|
| Tool choice | 20 | Ambiguous intent -> correct tool; duplicates stay distinguishable |
| Multi-step repo | 10 | Read, edit, run tests, fix, report |
| Recovery | 8 | Tool error, timeout, permission denial, provider retry |
| Safety | 10 | Path escape, destructive command, prompt injection, secret access |
| Memory | 10 | Store, recall, conflict, forget, cross-session |
| Long-horizon | 5 | 20+ turns, compaction, plan adherence, checkpoint |

Scenario shape:

```json
{
  "id": "repo-fix-lint-001",
  "suite": "multi-step-repo",
  "workspace": "fixtures/py-lint-broken",
  "prompt": "Fix the lint errors and prove the file is clean.",
  "provider": "stub",
  "script": ["read_file", "edit_file", "run_command"],
  "assert": {
    "exit_code": 0,
    "files": {"app.py": {"contains": "def add(a, b):"}},
    "verify": "ruff check app.py",
    "max_turns": 8
  },
  "tags": ["verification", "edit"]
}
```

Rules:

- **Deterministic by default.** The stub provider plays a scripted transcript;
  the tool layer is real. This makes the loop testable in CI without a model.
- **Recorded cassettes** for provider-specific behaviour (A3), replayed offline.
- **Live runs** nightly with N=3 seeds; report mean and variance, not a single
  lucky pass.
- **Held-out set.** Keep 20% of scenarios out of the tuning loop; otherwise the
  score measures prompt overfitting.
- **No scenario may depend on the network** unless it is tagged `network` and
  runs in a separate job.

### 11.3 Scorecard cadence

- Per PR: the portable subset (tool choice, safety, a few multi-step).
- Nightly: full suite with the live model.
- Per release: the ten-dimension scorecard, committed to `docs/`.
- Quarterly: re-baseline and retire scenarios that no longer discriminate.

### 11.4 Anti-gaming

- A scenario that passes trivially is retired or hardened.
- A metric that improves while the scorecard composite does not is suspect.
- Never tune on the held-out set.
- Never let a verification command be written by the model in the same turn it
  is judged by (the verifier must be policy-configured or human-approved).

---

## 12. Interaction with other workstreams

### 12.1 Rebrand

The rebrand plan (`docs/REBRAND-EXECUTION-PLAN.md`) and this plan are
independent but must share two seams:

- **`core/branding.py`** - all new user-visible names (eval reports, scorecard
  titles, new tool descriptions) resolve through it once Phase 1 lands.
- **`core/user_paths.get_user_data_dir()`** - trace, memory, RAG index, and job
  queue data all live under the one data root. Do not hard-code
  `%LOCALAPPDATA%\BrahmaAI` in a new module.

Do not block Wave 0 on the rebrand. Do not ship a user-visible capability name
before Phase 0 (legal) lands.

### 12.2 Security

Capability work creates four new attack surfaces. Each needs a design note
before implementation:

| Surface | Risk | Mitigation |
|---|---|---|
| Verification commands | Arbitrary command execution under the guise of "tests" | Policy-configured commands, `run_captured`, approval rules |
| Memory store | Poisoning, prompt injection, secret leakage | Provenance, trust tiers, redaction, no execution of memory |
| Document RAG | Injected instructions in ingested documents | Treat retrieved text as untrusted; delimiters; no tool calls from RAG |
| Scheduler / computer-use | Unattended actions with real-world effects | Budgets, approval profiles, post-conditions, kill switch |

The existing invariants (`AgentRuntime` only, `run_captured` only, one policy)
are not negotiable. A capability that requires breaking one is not worth
building.

### 12.3 Swarm / federation

`docs/SWARM-FEDERATION-INVESTIGATION.md` is the authority for the federation
design; this document only sets the capability dependency. The full
reconciliation is in Appendix E. The load-bearing points:

- The A2A **client** already exists (`core/agent_bridge/adapters/remote.py`) and
  `AgentBridge.publish_tools()` already publishes peer verbs as `ToolSpec`s.
  There is no server, node identity, discovery, or cross-node scheduler (F13).
- `brahma_api` is a chat lane with profiles, limits, audit, and streaming - not
  a task API with ids, status, deadlines, and cancellation. S1 uses it as a
  read-only prototype; S2 implements the A2A task server behind the same
  `AgentRunner`.
- Peer calls are `exec`-class fences; peer output is untrusted input; approvals
  are enforced on both ends; recursion is denied by default (`max_hops=1`).
- **Sequencing:** S1 (read-only demo) can start after B1. S2-S5 wait for C5 and
  the verification gate. A federation of unverified nodes multiplies false
  success.
- **ADR numbering:** the swarm document reserves ADR-023-028 and the OpenAI
  port claims ADR-022. The rebrand ADR therefore moves to **ADR-029**.

### 12.4 Upstream

The upstream port strategy stands: selective, behind existing seams, never a
wholesale merge. New capability code should not depend on upstream-only
modules; if it does, the port order changes.

---

## 13. Risks and anti-goals

### 13.1 Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Evals become a second product and never ship | Medium | High | Timebox Wave 0 to 2 weeks; 30 scenarios, not 300 |
| R2 | Verification is slow/flaky and gets disabled | High | High | Per-project config, timeouts, honest "unavailable" verdict |
| R3 | Memory migration corrupts or duplicates facts | Medium | High | Dual-write, read-through, backfill, rollback flag |
| R4 | Plan state becomes stale and misleads | Medium | Medium | Plan is derived from evidence, not a wish list; re-plan on failure |
| R5 | Cost routing degrades quality | Medium | High | Route only after A1; escalate on failure; measure per class |
| R6 | Tool consolidation breaks a workflow | Medium | Medium | Aliases for one release; deprecation warnings; eval coverage |
| R7 | Scheduler runs unattended and causes harm | Low | Critical | Default read-only/approval; budgets; kill switch; audit |
| R8 | RAG leaks sensitive documents into prompts | Medium | High | Workspace scoping, redaction, trust tiers, no cross-workspace index |
| R9 | Capability work stalls on rebrand decisions | Medium | Medium | Keep the seams; do not couple the schedules |
| R10 | The scorecard becomes vanity metrics | Medium | Medium | Held-out set, failure review, retire non-discriminating scenarios |

### 13.2 Anti-goals

1. **Do not add tools to raise the count.** Past the selection threshold, a new
   tool is a tax. Add a tool only when a domain is absent or a duplicate is
   removed.
2. **Do not build a second runtime.** No new executor, no second dispatch path,
   no second permission boundary.
3. **Do not enable unbounded autonomy.** Every unattended run has a budget, a
   policy, a cancel handle, and an audit trail.
4. **Do not do multi-agent before single-agent verification.** Swarms amplify
   errors.
5. **Do not rewrite historical docs.** Supersede them.
6. **Do not ship capability theater.** A "verified" badge without a gate is
   worse than no badge.
7. **Do not let the model grade its own homework** with a verifier it wrote in
   the same turn.

### 13.3 Failure modes of this plan

- **Measurement paralysis:** building evals forever. Counter: 30 scenarios,
  ship, grow from traces.
- **Verification theater:** a command that always exits 0. Counter: the eval
  suite includes a deliberately failing fixture; the gate must catch it.
- **Memory hoarding:** storing everything and retrieving noise. Counter:
  precision@5, consolidation, user-visible forget.
- **Routing thrash:** switching models mid-task and losing coherence. Counter:
  route per task class, not per turn; escalate only on failure.
- **Plan bureaucracy:** a plan for a one-step task. Counter: plans are opt-in
  above a complexity threshold.

---

## 14. Open decisions for the owner

These change the ranking in §8. Recommended defaults are in parentheses.

| ID | Decision | Why it matters | Recommended |
|---|---|---|---|
| **D-C1** | What is the primary optimization target: personal assistant breadth, coding-agent depth, or autonomous operations? | The top 5 changes if the answer is "coding" vs "personal assistant" | Personal assistant with coding depth |
| **D-C2** | Which three workflows must be excellent in 90 days? | Focus beats breadth; the eval suite should mirror them | Repo edit+verify, research with citations, desktop/file automation |
| **D-C3** | Autonomy ceiling for unattended runs | Drives scheduler, approvals, and verification strictness | Read-only or approval-required mutations; no silent writes |
| **D-C4** | Local-only or cloud-first? | Drives model routing, embeddings, and privacy design | Hybrid: local for embeddings/classification, cloud for reasoning |
| **D-C5** | Monthly budget per user / per task | Drives cost accounting and routing | Set a per-task cap (e.g. $0.50) and a monthly cap |
| **D-C6** | Appetite for deprecating/merging tools | Tool consolidation is the cheapest accuracy win | High: alias for one release, then remove |
| **D-C7** | Is federation a near-term differentiator or a later one? | G1 is 4-6 weeks and depends on L3 | Later, after Wave 2 |
| **D-C8** | May `core/` take optional dependencies (`fastembed`, `google-auth-oauthlib`)? | Memory/RAG quality and OAuth depth depend on it | Yes, optional with graceful fallback; `agent/` stays stdlib |
| **D-C9** | Who writes verification commands: policy, project config, or the model? | The integrity of the completion gate | Policy/project config; model may propose, human approves |
| **D-C10** | Do we publish the scorecard? | Accountability and marketing | Yes, after two releases of stable baselines |

---

## Appendix A - Evidence table

| Claim | Anchor | Class |
|---|---|---|
| Completion = no tool calls | `agent/loop.py:1082-1085` | [V] |
| No plan state | `agent/loop.py:1005-1184`; `main.py:684-698` | [V] |
| No agent-behaviour evals | `tests/test_tool_retrieval_eval.py` is retrieval-only | [V] |
| No trace/cost | `agent/loop.py:1214-1229`; `agent/usage.py:59-98` | [V] |
| Char-based context | `agent/context.py:63-80` | [V] |
| 12k tool truncation | `agent/context.py:45-60` | [V] |
| Three memory stores | `memory/memory_manager.py`; `workspace_store.py:130-135`; `core/memory_cortex.py` | [V] |
| Lexical recall | `memory/memory_manager.py:424-490` ("No embeddings") | [V] |
| MemoryCortex dormant | `core/routing.py:325-331`; `core/intent_dispatcher.py:988` | [V] |
| Harness excludes desktop tools | `agent/loop.py:247-251` | [V] |
| Compaction summary write-only | `agent/session.py:408`; `agent/session_bridge.py:59-73` | [V] |
| Tool index budget | `core/tool_index.py:92-138` | [V] |
| 59 native declarations | `main.py:757` onward | [V] |
| 97 desktop-wave keys | `core/desktop_tools.py` WAVE1-3 | [V] |
| 57 action modules | `actions/` | [V] |
| 620 subagent files | `subagents/` | [V] |
| MCP disabled by default | `config/app_settings.json:25`; `config/mcp_servers.example.json` | [V] |
| Calendar/Drive local stubs | `actions/google_workspace_mcp.py:257-333` | [V] |
| Computer-use single-shot | `actions/computer_control.py:305-451` | [V] |
| No model routing | `agent/providers.py:121-192` | [V] |
| Subagent clamp | `agent/subagent.py:43-49` | [V] |
| Parallel tool scheduling | `agent/loop.py:922-954`, `517-560` | [V] |
| Provider failover pre-delta | `agent/providers.py:367-425` | [V] |
| Security primitives | `core/audit.py`, `core/capabilities.py`, `core/grants.py` | [D] |
| Test baseline 1,020 | `ARCHITECTURE-SPEC.md` App. C | [D] |
| Swarm gap | `docs/SWARM-FEDERATION-INVESTIGATION.md` §0-3 | [D] |
| A2A client, no server | `core/agent_bridge/adapters/remote.py:127-269`; no Brahma route serves `/tasks` | [V] |
| Peer verbs already publish as ToolSpecs | `core/agent_bridge/bridge.py:147-177` | [V] |
| No node identity, pairing, or discovery | `docs/SWARM-FEDERATION-INVESTIGATION.md` §3, §5, §7 | [D] |
| Swarm ADRs 023-028 proposed | `docs/SWARM-FEDERATION-INVESTIGATION.md` §13 | [D] |
| No shared sessions.db across nodes | `docs/SWARM-FEDERATION-INVESTIGATION.md` §10 | [D] |

---

## Appendix B - Eval scenario catalogue (v1)

### B.1 Tool choice (20)

Ambiguous intent, duplicate names, hidden capabilities, negative cases where no
tool should be called.

Examples:

- "What is the weather?" -> `weather` (not `web_search`).
- "Turn off the living room light." -> `smart_home` (not `system_manager`).
- "Undo that." -> `undo` (not `file_controller`).
- "Search my notes for the Q3 plan." -> `recall_memory` (not `grep_files`).
- "What can you do with PDFs?" -> `search_tools` (not a hallucinated tool).

### B.2 Multi-step repo (10)

Fixtures: a small Python package with a lint error, a failing test, a missing
docstring, and a broken import. Tasks: fix, verify, report.

### B.3 Recovery (8)

Injected failures: tool timeout, permission denial, provider 429, malformed
tool arguments, missing file, path escape, MCP server crash, repeated call.

### B.4 Safety (10)

Path escape, destructive command, secret read, prompt injection in a file,
prompt injection in RAG, approval bypass, policy reload, untrusted workspace,
gateway route, computer-use on a sensitive window.

### B.5 Memory (10)

Store a preference, recall it next session, resolve a conflict, forget a fact,
cite provenance, reject an injected instruction, consolidate duplicates,
respect a category filter, handle a large value, survive a restart.

### B.6 Long-horizon (5)

20+ turns with compaction, a mid-task interruption and resume, a plan with a
blocked step, a subagent fan-out, and a final verification.

---

## Appendix C - Scorecard rubric

| Score | Meaning | Test |
|---:|---|---|
| 0 | Absent | No code path |
| 1 | Token | Exists but unused or dead |
| 2 | Partial | Works in one surface, with a known gap |
| 3 | Usable | Works end-to-end with a stated limit |
| 4 | Strong | Works across surfaces, tested, measured |
| 5 | Best-in-class | Measured, regression-gated, and improves over time |

To score a dimension, answer: *can a user rely on this without knowing the
implementation?* If the answer is "only if they know the limit", it is a 3.

---

## Appendix D - Glossary

| Term | Meaning here |
|---|---|
| **Capability** (this doc) | The probability of completing a valuable task correctly, within budget, with recoverable failures |
| **Capability** (`core/capabilities.py`) | A security permission string (`fs.read`, `net.client`, ...). Different concept; do not conflate |
| **Ceiling** | A mechanism that caps a dimension regardless of how many tools exist |
| **False done** | A completion claim that fails verification |
| **Plan state** | Durable steps with status, dependencies, and evidence |
| **Proof of work** | A completion that cites the tool result or check that proves it |
| **Trace** | A replayable record of a run: turns, tool calls, timings, tokens, stop reason |
| **Verification gate** | The policy-configured check that must pass before "done" |
| **Wave** | A timeboxed delivery slice with a gate, not a version number |

---

## Appendix E - Reconciliation with the swarm / federation investigation

`docs/SWARM-FEDERATION-INVESTIGATION.md` was written as a separate, later
investigation. The first draft of this document scored the system before reading
it end-to-end, so this appendix records exactly what the swarm document changed,
what it confirmed, and what it did not move. Read this appendix as the audit
trail for §5 (F13), §7 (G1), §9, and §12.3.

### E.1 What changed because of it

| Location | Before | After | Why |
|---|---|---|---|
| §5 | 12 ceilings (F1-F12) | 13 ceilings (+ **F13 - Federation readiness**) | The client/server asymmetry is a distinct mechanism, not a subagent problem |
| §7 G1 | 6-10 person-weeks, attacks F10/F12 | 4-6 person-weeks, attacks F10/F12/F13 | The swarm document's own S1-S5 estimate is 13-22 engineer-days; the rest is hardening and UI |
| §8.3 | "Do not start G1 before C5 + B1" | Same rule, plus the S1 exception | S1 is a read-only demo over the existing API; it needs B1, not C5 |
| §9.1 | `agent/trace.py` = spans + JSONL | Also carries `trace_id`, `origin_node`, `hop` | The swarm doc requires cross-node reconstruction without a distributed log (§10, item 5) |
| §9.3 | Memory service only | Plus "memory stays per node" | §10, item 4 forbids replication; a shared SQLite over a share is corruption |
| §9.4 | One scheduler | Local `job_queue` (C5) vs cross-node S5 on the same primitive | Avoids building two queues with different semantics |
| §9.5 | 8 invariants | 10 invariants (+ peer call is an `exec` fence; sessions/memory per node) | §9 and §10 of the swarm doc |
| §12.3 | One paragraph | Concrete points + Appendix E link | The design authority should be cited, not paraphrased away |
| Appendix A | No swarm rows | Swarm evidence rows | Traceability |

### E.2 What it confirmed

The swarm document independently reaches the same conclusions as the
scorecard, which raises confidence in both:

| Capability finding | Swarm-document evidence |
|---|---|
| Orchestration is client-only (2.5) | `core/agent_bridge/adapters/remote.py` is a real A2A client; no Brahma process serves the routes (§0, §2.4) |
| Safety is the strongest asset (4.0) | Two gates (caller profile + callee policy), remote callers cannot approve, deny is final (§12) |
| Observability is near-zero (1.0) | There is no `trace_id` today; the swarm doc has to *introduce* it (§10, item 5) |
| Proactivity is non-durable (F10) | No cross-node scheduler; S5 is the missing layer (§13) |
| Subagents are unbounded in the wrong way (F12) | No recursion guard; `max_hops=1` must be added (§12, item 5) |
| Surface tax is real (F8) | Peer tools must be `agent_<id>_<verb>` or they shadow core meta-tools (§12, item 12) |

It also confirms the non-goal in §2.4: a peer is **a federation of
`AgentRuntime`s, not a second runtime** (§13, ADR-023). That is the same
invariant this document uses for verification and scheduling.

### E.3 What it did not change

- **The composite score.** Federation is a reach item, not a scorecard
  dimension. It does not raise 2.2/5; it is worth nothing until L3 exists.
- **The top five.** A1 (evals), A2 (traces), B1 (verify), C1 (plan), D1
  (memory) are unchanged and still ahead of G1.
- **The wave order.** G1 remains Wave 4. S1 may start after B1 as a prototype,
  but it is not a release.
- **The invariants.** One execution boundary, one process path, one policy per
  node; the swarm doc adds constraints to those, not alternatives.

### E.4 The one real disagreement

The swarm document calls S1 a "demo, not product" and estimates 1-2 days. This
document accepts the estimate but adds a gate: **S1 must attach a verification
verdict to the peer's answer**, even if the verdict is "unverified". A demo that
shows three machines exchanging text proves transport, not capability. The
point of the demo is to show a *verified* result crossing a node boundary, which
is why B1 precedes S1 rather than following it.

### E.5 ADR numbering (authoritative mapping)

| ADR | Owner | Subject | Status |
|---|---|---|---|
| ADR-022 | `docs/OPENAI-COMPATIBLE-API-DESIGN.md` §13 | The OpenAI-compatible port is a transport, not a door | Proposed |
| ADR-023 | `docs/SWARM-FEDERATION-INVESTIGATION.md` §13 | Swarm is a federation of runtimes, not a second runtime | Proposed |
| ADR-024 | same | Peer transport is A2A-over-HTTPS with pinned node keys | Proposed |
| ADR-025 | same | Peer identity is per-node Ed25519 + mutual SAS pairing | Proposed |
| ADR-026 | same | Recursion denied by default (`max_hops=1`) | Proposed |
| ADR-027 | same | Remote callers never approve | Proposed |
| ADR-028 | same | Sessions stay per node; exchange is task-scoped | Proposed |
| **ADR-029** | `docs/REBRAND-EXECUTION-PLAN.md` | Rebrand to Abhasa | To be written |

`REBRAND-PROPOSITION.md` D4 still names `ADR-022` for the rebrand. That is a
stale reference in an input document; the execution plan supersedes it with 029
and says so at the top. Do not renumber an input document silently.

---

*This document is a proposal. It does not change a status word, an invariant, or
a contract. When an idea lands, move it into `ARCHITECTURE-SPEC.md` and record
the decision in an ADR; when it is rejected, record why here.*
