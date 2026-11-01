# Brahma Evo — Architecture & Engineering Specification

**Status:** Living document — the orientation layer above the per-area design docs.
**Created:** 2026-11-01
**Baseline:** `main` @ `531b961` (2026-10-31), working tree clean, `main == origin/main`.
**Scope:** the whole repository: app shell, providers, tool surface, agent harness, skills, voice, avatars, gateway, security posture, and the two large unbuilt proposals (agent bridge, upstream port).
**Audience:** the next engineer or model that has to change this system without re-deriving it from eighteen overlapping documents.

---

## 0. How to use this document

Read §1–§4 before touching code. §5–§7 are the subsystem reference. §8–§10 are the two big proposals and how they must be re-based. §11–§14 are the operating model, decisions, and the work queues.

Four rules govern this document:

1. **Code wins over prose.** Every claim here was checked against the tree at the baseline commit. If code and this document disagree, the code is current and this document is a bug to fix.
2. **Symbol names are authoritative; line numbers are evidence.** Line numbers rot within a week. Where a line is cited, it is a pointer, not a contract.
3. **Status words are load-bearing.** Every capability is labelled `Built`, `Built-Partial`, `Designed`, `Aspirational`, `Deferred`, `Rejected`, or `Historical`. A design document does not become an implementation because it is long.
4. **One boundary, one door per concern.** The system's safety and comprehensibility come from a small number of chokepoints. A proposal that adds a second chokepoint is a redesign, not a feature.

### Status vocabulary

| Word | Meaning |
|---|---|
| **Built** | Implemented on `main`, covered by tests, exercised through the normal surfaces. |
| **Built-Partial** | Implemented and usable, with a documented limit that changes how you should rely on it. |
| **Designed** | A specification and/or ADRs exist. No production code. |
| **Aspirational** | Agent-authored or early design, built against an older tree, not reviewed or accepted by the owner. |
| **Deferred** | A decision was taken to wait; a named ADR/task resumes it. |
| **Rejected** | A decision was taken not to do it, for a stated reason. |
| **Historical** | Superseded; retained for provenance, not guidance. |

---

## 1. Executive summary

Brahma Evo is a **Windows-first, single-process PyQt6 desktop assistant** with four ways to act:

- a **Gemini Live voice session** (bidirectional audio),
- a **GUI text path** through local / custom / OpenRouter models,
- a **headless agent CLI** (`python -m agent`),
- and a set of **~60 native desktop actions** dispatched by the legacy tool executor.

On top of that, 1.0.1 and the harness batches that followed added:

- a vendor-agnostic **provider chain** with kill switches and failover,
- a **budgeted tool index** that keeps ~173 tools down to 24 declarations on the Gemini setup frame,
- a **JIT Skills Library** plus project skills (`SKILL.md`),
- four interchangeable **avatar renderers** behind one three-method contract,
- and an **agent harness**: bounded loop, permissions, approval banner, process containment, MCP client, sessions, subagents.

The system is **functionally strong and structurally uneven**. Three facts dominate the current situation:

1. **The harness is real and is the best-built part of the repo.** `agent/` is stdlib-only, tested by ~387 tests, and owns one dispatch boundary, one loop, and one policy engine.
2. **The rest of the app is still pre-harness.** Native tools are dispatched by an `elif` chain in `core/tool_exec.py` with no policy check, and a second, older autonomous executor (`agent_task` → `TaskQueue` → `AgentExecutor`) still bypasses the runtime entirely.
3. **All eight critical security findings from upstream issue #4 are still present**, verified on the baseline. The security roadmap is `Proposed`, not implemented. Two of the ten roadmap workstreams overlap the harness directly.

The two large proposals are in different states and must not be conflated:

| Proposal | State | Verdict |
|---|---|---|
| **Agentic harness** | Built, 23+ batches, D1–D42 recorded | Continue; decide N4/N5. |
| **Agent & orchestrator bridge** | Aspirational; no code; ADRs accepted 2026-10-17; built against a pre-harness tree | Do **not** implement as written. Re-base onto the harness first. |
| **Upstream integration** | Planning only; upstream now 7 commits ahead | Port after the fork's security and boundary work, selectively, never wholesale. |
| **Security roadmap** | Proposed; 8 findings open | Phase 0 is small, independent, and should be the next engineering batch. |

---

## 2. System map

### 2.1 Runtime topology

```
┌──────────────────────────────────────────────────────────────────────────┐
│ One Python process (PyQt6 GUI thread + worker/asyncio threads)           │
│                                                                          │
│  UI shell (core/ui_*.py, ui.py)                                          │
│    ├── chat / HUD / tray / settings / avatar cards                       │
│    └── core/confirm.py  ← the only interactive approval surface          │
│                                                                          │
│  Voice: Gemini Live asyncio loop ── core/session_lifecycle.py            │
│    ├── setup frame: ≤ budget declarations (core/tool_bridge.py)          │
│    └── tool calls → core/tool_exec.py                                    │
│                                                                          │
│  Text:  provider chain ── core/provider_router.py / core/llm_gateway.py  │
│    ├── Gemini / OpenRouter / local / custom                              │
│    └── tool calls → _finish_tool_turn → agent/loop.py                    │
│                                                                          │
│  CLI:   python -m agent ── agent/__main__.py → agent/loop.py             │
│                                                                          │
│  Dispatch: core/tool_exec.py                                             │
│    ├── registered harness tool? → AgentRuntime.execute  (policy)         │
│    └── else → legacy elif chain → actions/*.py        (no policy)         │
│                                                                          │
│  Legacy autonomous path: agent_task tool → agent/task_queue.py           │
│    └── AgentExecutor → agent/planner.py → direct action calls            │
│        (bypasses both AgentRuntime and the legacy executor)              │
│                                                                          │
│  Side systems: Skills Library, Skills Forge/Crucible, plugins,           │
│                gateway (brahma_connect), updaters, avatars, smart home   │
└──────────────────────────────────────────────────────────────────────────┘
```

**There are three supported doors and one unsupported door.**

| Door | Entry point | Harness tools | Native tools | Policy |
|---|---|---|---|---|
| Voice | Gemini Live session | `AgentToolExecutor` → `AgentRuntime` | legacy `elif` chain | policy for harness tools only |
| GUI text | `main.py:_finish_tool_turn` → `AgentLoop` | `AgentRuntime` | legacy `elif` chain | policy for harness tools only |
| CLI | `python -m agent` | `AgentRuntime` | not advertised | policy |
| **Legacy autonomous** | `agent_task` tool | **none** | direct calls / `DynamicToolRegistry` | **none** |

The fourth door is the single largest structural inconsistency in the repo. §6.10 specifies it and §13 proposes its retirement.

### 2.2 Directory map

| Path | Contents | Notes |
|---|---|---|
| `main.py` | App entry, `BrahmaLive`, `TOOL_DECLARATIONS` (60 native tools), provider routing, GUI text loop wiring | 4,833 lines. Bridge-era guard was ≤3,903; already violated. |
| `ui.py` | Thin `MainWindow` composition root over `core/ui_*` modules | 827 lines. Bridge-era guard was ≤692; already violated. |
| `core/` | Cross-cutting seams: tool dispatch/index, provider chain, confirmation, sessions, skills, avatars, UI modules | ~90 modules. The de-facto architecture layer. |
| `actions/` | One module per native capability (~48) | Legacy tool implementations, called by the `elif` chain. |
| `agent/` | **Two things:** the stdlib-only agent harness (20 modules) **and** the pre-fork autonomous executor (4 modules) | Names collide conceptually; see §6.10. |
| `features/` | Forge output, hot-loaded at startup | Untrusted model-generated code; see §7. |
| `brahma_connect/` | Phone/device gateway (HTTP + WebSocket + mDNS) | Binds `0.0.0.0`; HTTP control routes unauthenticated. |
| `tests/` | Pytest suite; 744 passed / 1 skipped at baseline | `test_agent_*` are display- and SDK-free. |
| `docs/` | Design records, build logs, proposals | Eighteen documents; see §3. |

### 2.3 Subsystem inventory

| Subsystem | Primary modules | Status |
|---|---|---|
| App shell / HUD | `main.py`, `ui.py`, `core/ui_*` | Built-Partial (god-object guards already breached) |
| Provider chain | `core/provider_router.py`, `core/provider_failover.py`, `core/llm_gateway.py` | Built |
| Tool surface | `core/tool_index.py`, `core/tool_bridge.py`, `core/dynamic_registry.py`, `core/tool_exec.py` | Built-Partial (legacy dispatch has no policy) |
| Agent harness | `agent/*` (harness subset) | Built |
| Sessions | `agent/session.py`, `agent/session_bridge.py` | Built |
| Project skills | `agent/skills.py`, `agent/prompts.py` | Built |
| Skills Library | `core/skills_library.py`, `core/skill_discovery.py` | Built |
| Skills Forge / Crucible | `core/skill_forge.py`, `core/skill_crucible.py`, `features/` | Built-Partial, **unsafe by design today** |
| Plugins | `plugin_manager.py`, `plugins/` | Built, **untrusted auto-import** |
| Voice | `core/session_lifecycle.py`, `core/mcp_manager.py` | Built-Partial (deferred-tool `1008`) |
| Avatars | `core/avatar_runtime.py`, `core/avatar_catalog.py`, `assets/avatar_*` | Built (4/4 renderers; Live2D runtime user-supplied) |
| Gateway | `brahma_connect/gateway/*` | Built-Partial, **unauthenticated HTTP, LAN bind** |
| Updaters | `updater.py`, `core/updater.py` | Built, **unsigned, hardcoded remote** |
| Secrets | `smart_home/storage.py`, `config/api_keys.json` | Built, **recoverable local keys** |

---

## 3. Document map and authority

The `docs/` tree mixes five kinds of document. Treating them as one authority level is the main cause of confusion.

| Tier | Meaning | Documents |
|---|---|---|
| **0. Current truth** | The tree and `AGENTS.md` | code, `AGENTS.md`, this document |
| **1. Authoritative current design** | Kept in sync with code; safe to implement against | `AGENTIC-HARNESS.md`, `AGENTIC-HARNESS-STATUS.md`, `AGENTIC-HARNESS-NEXT.md`, `ONE-391-tool-index.md`, `skills-library-jit-on-demand-skills.md`, `ONE-391-avatar-backends.md` |
| **2. Proposed, not implemented** | Good analysis; no code; decisions open | `SECURITY-ANALYSIS-ISSUE-4.md`, `SECURITY-ARCHITECTURE-ROADMAP.md`, `UPSTREAM-INTEGRATION-PLAN.md`, `DEFERRED-TOOL-PROXY-MISSION.md` |
| **3. Historical record** | True at the time; counts and line numbers are stale | `VERSION-1.0.1-LOG.md`, `VERSION-1.0.1-CHANGES.md`, `CODE-REVIEW-TODO.md`, `ONE-391-changes.md`, `ONE-391-next-steps.md` |
| **4. Aspirational** | Agent-authored, owner-unreviewed, built against a pre-harness tree | `agent-orchestrator-bridge.md`, `AGENT-BRIDGE-BUILD-TODO.md`, `AGENT-BRIDGE-ADR.md` |

### 3.1 Per-document disposition

| Document | Date | What it is | How to use it |
|---|---|---|---|
| `ONE-391-tool-index.md` | 2026-10 | The 173→24 budgeted tool surface, RRF retrieval, measured latency | **Authoritative** for the tool surface. Numbers are design-time; re-measure when changing backends. |
| `ONE-391-changes.md` | 2026-10 | Complete change record for ONE-391 | Historical. Says "nothing committed" — false now. Read for rationale only. |
| `ONE-391-next-steps.md` | 2026-10 | Unblock list from the ONE-391 era | Historical. All items are now either done or superseded. |
| `ONE-391-avatar-backends.md` | 2026-10 | Contract, transports, per-renderer state, licensing | **Authoritative** for avatars. Live2D runtime and VMC host remain user-supplied. |
| `skills-library-jit-on-demand-skills.md` | 2026-10-17 | JIT skill catalogue, two roots at authoring time | **Authoritative** for the library; root count is now three (D42). |
| `CODE-REVIEW-TODO.md` | 2026-10 | P0–P4 review items, all `[x]`; work log | Historical, but its engineering rules survive as §4. Do not re-open items without evidence. |
| `VERSION-1.0.1-LOG.md` | 2026-10-20 | 1.0.1 changelog: providers, review hardening, decomposition, CI, addenda | Historical. Best source for *why* 1.0.1 looks the way it does. |
| `VERSION-1.0.1-CHANGES.md` | 2026-10-20 | Companion narrative, overlapping scope | Historical; less complete than the LOG in places, more narrative in others. |
| `AGENTIC-HARNESS.md` | 2026-10-31 | Architecture of the harness: loop, tools, permissions, sessions, prompts, tests | **Authoritative.** The single best document in the repo. |
| `AGENTIC-HARNESS-STATUS.md` | 2026-10-31 | D1–D42 decision log, verification, known gaps | **Authoritative** for decisions. |
| `AGENTIC-HARNESS-NEXT.md` | 2026-10-31 | N1–N8 tracker; N1/N2/N3/N8 landed | **Authoritative** for what is left. |
| `SECURITY-ANALYSIS-ISSUE-4.md` | 2026-10-24 | Eight findings against this fork, with evidence | **Current and accurate** (re-verified 2026-11-01). |
| `SECURITY-ARCHITECTURE-ROADMAP.md` | 2026-10-28 | WS-1…WS-10, SEC-01…SEC-18, Phase 0–3, target process model | **Proposed.** Use as the security plan; nothing is implemented. |
| `UPSTREAM-INTEGRATION-PLAN.md` | 2026-10-28 | Hybrid selective port of 7 upstream commits | **Proposed/planning.** Commit list needs refreshing; strategy stands. |
| `DEFERRED-TOOL-PROXY-MISSION.md` | 2026-10-29 | Gemini Live `1008` on undeclared tool calls | **Open investigation.** Prevention-via-prompting is the current recommendation, unproven. |
| `agent-orchestrator-bridge.md` | rev 3 + 2026-10-17 amendments | 87 KB "god orchestrator" design | **Aspirational.** §8 re-bases it. |
| `AGENT-BRIDGE-BUILD-TODO.md` | 2026-10-17 | WBS with frozen contracts, phases 0–5 | **Aspirational.** ~3% built (ADRs only). Its guards are already violated. |
| `AGENT-BRIDGE-ADR.md` | 2026-10-17 | ADR-001…017 | **Accepted decisions, but not yet architecture.** Several conflict with the harness; see §8.3. |

### 3.2 Known drift (do not "fix" by trusting the doc)

| Claim in an older doc | Current reality |
|---|---|
| "Nothing has been committed" (`ONE-391-changes`) | 1.0.1 and 23+ harness batches are on `main`. |
| Test counts 167 / 221 / 331 | 744 passed, 1 skipped at baseline. |
| `main.py` ≤ 3,903; `ui.py` ≤ 692 (bridge guards) | 4,833 / 827. |
| "MCP is CLI-only" | GUI text path, voice manager, and CLI all use MCP. |
| "Skills Library has two roots" | Three: project `.brahma/skills` is highest precedence (D42). |
| "Upstream is 3 commits ahead" | 7 commits, `upstream/main` @ `bfb2868` (2026-10-27). |
| "Discovery is default-on" (bridge §4.10) | ADR-014: fully opt-in, every layer ships `false`. |
| "The harness is `agent/`" | `agent/` is also the legacy planner/executor. See §6.10. |

---

## 4. Invariants

These are the rules a change must not break. Each has an enforcement point and, where one exists, a test. An invariant marked **scoped** is true only where stated — do not over-claim it.

### 4.1 Execution and permissions

| ID | Invariant | Enforced by | Verified by |
|---|---|---|---|
| **I-01** | Every harness tool call on every door passes through `AgentRuntime.execute`. | `core/tool_exec.py` harness branch; `AgentLoop`; CLI | `tests/test_agent_code_tools.py` (gating), `test_agent_loop.py` |
| **I-02** | *(scoped)* Native (non-harness) tools have **no** policy engine; only `core/confirm.py` gates a handful of destructive actions. | legacy `elif` chain | not universally tested — **gap G-1** |
| **I-03** | A policy `deny` is final. A session "always allow" is consulted only after `evaluate()` and only when the decision was `ask`; it can never widen a deny. | `agent/runtime.py`, `agent/permissions.py` | `test_agent_approval.py`, `test_agent_policy_reload.py` |
| **I-04** | A `requires_approval` spec is never remembered and asks every time. | `agent/runtime.py` | `test_agent_approval.py` |
| **I-05** | The GUI approver refuses to run on the Qt main thread; a caller must be on a worker. | `core/confirm.py:ask_blocking_choice` | `test_confirmation_overlay.py`, `test_agent_approval.py` |
| **I-06** | A malformed policy never widens access: the last good policy stays and `policy_reload(ok=False)` is emitted. | `agent/runtime.reload_policy` | `test_agent_policy_reload.py` |
| **I-07** | No harness tool is registered under a core meta-tool name (`load_skill`, `search_tools`, `recall_memory`, …); the harness branch runs first and would shadow it. | naming + `agent/skills.py` guard | `test_agent_skills.py` collision guard |
| **I-08** | Delegation is one bounded child, never recursive, sharing the parent's runtime. No second child-construction path. | `agent/subagent.py`, `AgentLoop._run_subagent` | `test_agent_subagent.py` |

### 4.2 Process, protocol, and data

| ID | Invariant | Enforced by | Verified by |
|---|---|---|---|
| **I-09** | Every `assistant` `tool_calls` message is followed by exactly one `tool` message per call, in order, with matching ids — including on cancellation. | `AgentLoop`, `context.py` | `test_agent_loop.py`, `test_agent_parallel.py` |
| **I-10** | The transcript is always OpenAI-shaped; Gemini declarations are converted only at the edge, and schemas are canonical lowercase. | `agent/spec.py`, `normalise_assistant_message` | `test_agent_loop.py`, `test_agent_spec*` |
| **I-11** | Compaction never orphans a tool result: the cut walks forward past `tool` messages. | `agent/context.py` | `test_agent_context.py` |
| **I-12** | Shell commands run with closed stdin; a timeout terminates the whole tree (Windows job object / POSIX session group); normal completion does not kill survivors. | `agent/process.run_captured` | `test_agent_process.py` |
| **I-13** | `exec.sandbox` is the only isolation path: env scrub, optional trusted prefix, resource limits. The tool does not re-implement any of it. | `agent/sandbox.py` | `test_agent_sandbox.py` |
| **I-14** | Credential-shaped environment variables are withheld by default and their count is reported, not silently dropped. | `agent/sandbox.py` | `test_agent_process.py`, `test_agent_sandbox.py` |
| **I-15** | `agent/` imports with only pytest installed: no PyQt, provider SDK, or Windows COM at module scope; `agent/__init__.py` stays empty. | CI `harness-pythons` job (3.11/3.12/3.13) | import canary |
| **I-16** | The system prompt is never persisted; resume appends only the new tail (`messages_after`), never a duplicated history. | `agent/session_bridge.py` | `test_agent_session_bridge.py` |
| **I-17** | Writes are atomic where user data is persisted (`core/user_paths.atomic_write_json`); a crash cannot leave a half-written config. | `core/user_paths.py` | review-time rule; targeted tests |
| **I-18** | One MCP client per layer: `agent/mcp.py` (stdlib) for the harness, `core/mcp_manager.py` (SDK) for the voice path. No third client. | module ownership | `test_agent_mcp.py`; see §8.3 conflict C-2 |

### 4.3 Surface and cost

| ID | Invariant | Enforced by | Verified by |
|---|---|---|---|
| **I-19** | The advertised Gemini surface is small and **constant**: pinned tools + meta-tools + top-k, never the whole catalogue. | `core/tool_index.py`, `core/tool_bridge.py` | `test_tool_index.py`, `test_tool_bridge.py` |
| **I-20** | Gemini Live tools cannot be swapped mid-session; no code may assume a second setup frame. | transport constraint (D/ONE-391) | design constraint |
| **I-21** | Every provider egress is gated by its kill switch **before** the call, not after a failure. | `core/provider_router.guard`, `core/llm_gateway.py` | provider tests, egress acceptance |
| **I-22** | Token usage comes from the provider; "unknown" is never rendered as zero. | `agent/usage.py` | `test_agent_usage.py` |
| **I-23** | Cancellation is a flag, never a killed thread: in-flight writes and child processes finish. | `agent/runtime.cancel_event` | `test_agent_loop.py`, `test_agent_retry.py` |

### 4.4 Engineering rules (from the review era)

| ID | Rule |
|---|---|
| **I-24** | One subprocess path per concern. `run_command` goes through `run_captured`; do not add a second `subprocess.run(shell=True, timeout=…)`. |
| **I-25** | One marshaller per boundary: Qt-thread-only calls are emitted through queued signals, never invoked inline from a worker or asyncio thread. |
| **I-26** | No import-time side effects: importing a UI/core module must not start a thread, open a socket, or read user data. |
| **I-27** | Prefer a targeted edit over a whole-file rewrite; run the project's own gates rather than asserting success. |
| **I-28** | A new harness tool is a `ToolSpec` plus tests — never a new `elif` in `core/tool_exec.py`. |

---

## 5. Capability matrix

### 5.1 Implemented

| Domain | What works | Anchor |
|---|---|---|
| Voice | Gemini Live audio, tool calls, turn archival into sessions | `core/session_lifecycle.py`, `main.py` |
| Text | Gemini / OpenRouter / local / custom, streaming, failover, retry | `core/provider_router.py`, `core/provider_failover.py`, `agent/providers.py` |
| Agent loop | Multi-turn, repeat detection, turn caps, compaction, cancellation, events | `agent/loop.py`, `agent/context.py` |
| Tools | 8 harness primitives (6 code/exec, `run_subagent`, `load_project_skill`) + MCP-discovered tools, parallel path-dependency scheduling | `agent/spec.py`, `agent/code_tools.py`, `agent/mcp.py` |
| Permissions | Policy file, classes, path scoping, protected globs, headless rules, hot reload, session approvals | `agent/permissions.py`, `agent/runtime.py` |
| Containment | Whole-tree timeout, closed stdin, env scrub, prefix, resource limits | `agent/process.py`, `agent/sandbox.py` |
| Sessions | SQLite rows, resume, GUI binding, voice turn archive | `agent/session.py`, `agent/session_bridge.py` |
| Prompts | Resident rules, `AGENTS.md` upward walk, path-driven subdirectory walk, project-skill catalogue | `agent/prompts.py`, `agent/skills.py` |
| Subtasks | One bounded child, filtered surface, shared policy, report-only return | `agent/subagent.py`, `agent/loop.py` |
| Tool index | 173→24 declarations, RRF hybrid retrieval, per-source cap, exact-id boost, manuals | `core/tool_index.py`, `core/tool_bridge.py` |
| MCP | Voice SDK manager; harness stdio client; GUI workspace session; turn-boundary refresh | `core/mcp_manager.py`, `agent/mcp.py` |
| Skills | Core JIT library (instructional + executable), project skills (instructional only), three roots | `core/skills_library.py`, `agent/skills.py` |
| Avatars | `three_reactive`, `live2d` (runtime missing), `vrm` (fully available), `osc_vmc` (needs host) | `core/avatar_runtime.py`, `assets/avatar_*` |
| Security primitives | Approval banner with real diffs; provider kill switches; gateway device pairing over WebSocket | `core/confirm.py`, `core/diff_preview.py`, `brahma_connect/` |
| CLI | `run`, `resume`, `sessions`, `tools`, `skills`, `mcp list`, `permissions show/init`, NDJSON | `agent/__main__.py` |

### 5.2 Built-partial (usable, with a stated limit)

| Capability | Limit |
|---|---|
| Native tool dispatch | No policy engine; only three destructive actions are confirm-gated. |
| `agent_task` autonomous execution | Bypasses the harness entirely; planner is prompt-driven and unverified. |
| Gemini Live + deferred tools | The model can call an unadvertised tool and the socket closes with `1008`. Mitigation is prompt-only today. |
| Voice MCP | Separate SDK manager; no turn-boundary refresh parity with the harness. |
| Subagents | Sequential, same model, lossy report, no session row. |
| Skills Forge | Writes and immediately executes model-generated code; no trust boundary, no review, runtime `pip`. |
| Plugins | Any `.py` in `plugins/` is imported at startup, unsigned. |
| Gateway | WebSocket authenticates; HTTP control routes do not; binds `0.0.0.0`. |
| Updates | Startup `git pull` / `reset --hard` from a hardcoded upstream, unsigned. |
| Secrets | Fernet key stored beside the ciphertext; plaintext `api_keys.json`. |

### 5.3 Designed / deferred / rejected

| Item | Status | Resume condition |
|---|---|---|
| Filesystem/network confinement for `run_command` (N4) | **Blocked on decision** | Pick container-first, AppContainer-first, or "prefix is enough". |
| Parallel / model-selecting subagents (N5) | **Blocked on decision** | Accept sequential, or design a coordinator that keeps one enforcement path. |
| MCP resources/prompts/sampling (N6) | **Deferred** | Add only with a complete implementation and tests. |
| Executable project skills / `allowed-tools` enforcement (N7) | **Rejected** | Would be a second permission boundary. |
| Security roadmap WS-1…WS-10 | **Designed** | Phase 0 can start now; later phases need the process split. |
| Agent & orchestrator bridge | **Aspirational** | Re-base onto `AgentRuntime`; see §8. |
| Upstream port | **Planned** | After the fork's security batch; selective only. |
| Peer/LAN agents | **Deferred** (ADR-002/013) | Phase 5, after recursion guard and Phases 0–4. |
| AppContainer sandbox | **Rejected for now** | Fails silently against a normal Python install (D36). |

---

## 6. Subsystem specifications

Each section states the contract and the one thing most likely to break.

### 6.1 App shell and threading

- **Responsibility:** own the Qt application, the HUD, settings, tray, and thread marshalling.
- **Shape:** `ui.py` is a thin `MainWindow`; behavior lives in ~50 `core/ui_*` modules. `main.py` still owns `BrahmaLive` and the native declarations.
- **Contract:** all Qt widget mutation happens on the GUI thread. Worker/asyncio paths emit queued signals (`_emotion_sig`, `_state_sig`, `_audio_sig`) and never call `runJavaScript` or widget methods inline.
- **Known debt:** both god-object guards from the bridge WBS are already breached (`main.py` 4,833 vs 3,903; `ui.py` 827 vs 692). Do not add new behavior to `main.py`; put it behind a `core/` seam.
- **Anchor tests:** `test_confirmation_overlay.py`, avatar acceptance harness, UI wiring tests.

### 6.2 Provider chain

- **Responsibility:** choose and police the model link.
- **Order:** `gemini > openrouter > local > custom`, each gated by `provider_enabled_*` **before** the call.
- **Why Gemini is special:** Gemini Live is a bidirectional audio WebSocket; the other three share an OpenAI-compatible adapter.
- **Failover:** `core/provider_failover.py` narrows throttle detection to `429` / `resource_exhausted` / quota / rate-limit wording; it deliberately does not treat `1008` or auth errors as throttling.
- **Invariant:** no provider call may bypass `core/llm_gateway.py` or the kill switch (I-21).
- **Gap:** a provider that is enabled but misconfigured fails at the call, not at configuration time. Diagnostics are spread across the router, gateway, and settings UI.

### 6.3 Tool surface and index

- **Responsibility:** keep the advertised surface small and make everything else reachable through a tool.
- **Shape:** pinned (`undo`, `recall_memory`, `read_file`, `edit_file`, `run_command`) + meta-tools (`search_tools`, `call_tool`, `get_skill_manual`, `get_tool_surface`, `list_tools`, `search_skills`, `load_skill`, `list_skills`) + top-k retrieval, budget 24.
- **Retrieval:** SQLite FTS5 + n-gram TF-IDF + optional ONNX embeddings, fused with RRF (k=60), exact-identifier boost, per-source cap, multi-clause fusion.
- **Why it exists:** Gemini Live rejects >128 declarations and cannot swap tools mid-session (I-19/I-20).
- **Contract:** every tool is a `ToolSpec` with a canonical lowercase schema; `gemini()` converts up at the edge.
- **Failure mode to respect:** description collisions are a data problem. Fix them in `%LOCALAPPDATA%\BrahmaAI\tool_routing.json`, not in the retriever.
- **Known gap:** the legacy `elif` chain is still the executor for native tools, so "one registry" is aspirational for that half.

### 6.4 Agent harness

The authoritative description is `AGENTIC-HARNESS.md`. This is the orientation:

- **Loop:** bounded turns, repeat detection by `name + canonical JSON`, stop reasons (`completed`, `max_turns`, `repeat_loop`, `cancelled`, `error`, `permission_denied`), retries on transient provider failures, protocol-safe compaction.
- **Dispatch:** per-call path-dependency scheduling. Unrelated reads overlap; writes never overlap; read/write conflicts follow model order; `exec`, native calls, approvals, denials, and path-less writes are fences.
- **Tools:** `read_file`, `write_file`, `edit_file`, `glob_files`, `grep_files`, `run_command`, `run_subagent`, `load_project_skill`, plus `mcp_<server>_<tool>`.
- **Runtime:** `AgentRuntime.execute` is the only permission boundary for harness tools; policy → approval → handler. Hot reload is fail-safe.
- **Subagents:** one child, shared runtime, read-default surface, no recursion, 12-turn clamp, report-only return.
- **Events:** a public contract (`turn_start`, `assistant`, `tool_call`, `tool_start/end`, `tool_result`, `permission`, `compaction`, `retry`, `delta`, `policy_reload`, `instructions`, `subagent_*`, `error`, `stop`).

**The one thing most likely to break:** a new host that calls a handler directly, or an approval reached from the GUI thread (I-05).

### 6.5 Sessions

- **Storage:** additive tables in the existing `workspace_store.sqlite3` (`agent_sessions`, `agent_messages`, `agent_events`).
- **GUI contract:** first agentic turn creates a row against the runtime workspace; later turns seed `[fresh system, stored history, new user]`; only the new tail is appended.
- **Voice:** completed Live turns are archived as cleaned user/assistant pairs; the socket is never re-seeded from disk.
- **Invariant:** never persist the system prompt (I-16); replace the stored transcript after compaction.

### 6.6 Skills — two systems, on purpose

| | Core Skills Library | Project skills |
|---|---|---|
| Module | `core/skills_library.py` | `agent/skills.py` |
| Tool | `search_skills` / `load_skill` / `list_skills` | `load_project_skill` |
| Roots | builtin, user, then project `.brahma/skills` (highest precedence, D42) | `.brahma/skills` in workspace + 3 ancestors + user dir |
| Shapes | instructional **and** executable (`executor.py`) | **instructional only**, even if a file claims `executable: true` |
| Trust | user-installed; executable skills are code | trusted input, like `AGENTS.md`; `allowed-tools` advisory |

**One store, two doors, two names.** Do not unify the names: `core/tool_exec.py` runs the harness branch first, so a harness `load_skill` would shadow the library tool on voice/GUI.

**The forge is separate and unsafe.** `core/skill_forge.py` synthesizes code, `SkillCrucible` runs a narrow denylist and a same-user subprocess, `features/*.py` is hot-loaded, and `main.py:_forge_skill` executes the new skill immediately. All eight security findings touch this area.

### 6.7 Voice path and the deferred-tool problem

- **Setup:** one `BidiGenerateContentSetup` per session; tools fixed at connect time.
- **Why deferred tools exist:** 173 tools exceed the 128 ceiling, so most are reachable only via `search_tools` → `call_tool`.
- **The `1008` failure:** the model sometimes calls a discovered-but-unadvertised tool directly; Gemini closes the socket with `1008 (policy violation) Requested entity was not found`.
- **Current mitigation:** prompt the model to call through `call_tool`; not proven. **No code fix is committed.**
- **Constraint for any future fix:** it must remain backend-agnostic (Gemini Live, local, custom) and must not create a second execution path.

### 6.8 Avatar backends

- **Contract:** any object with `probe()`, `set_emotion(tag, intensity=)`, `status()`. Core never imports a backend by name.
- **Transports:** `webengine` (drawn in `QWebEngineView`) and `osc` (UDP/VMC).
- **State:** all four renderers implemented. `vrm` is fully available; `three_reactive` is safe to disable pixel-identically; `live2d` needs the user-supplied Cubism runtime; `osc_vmc` needs a VMC host.
- **Rules worth preserving:** emotion never writes the state palette; every factor is a multiplier around 1.0; `NEUTRAL`/intensity 0 is an exact pass-through; the sink is marshalled to the GUI thread; the tag is `json.dumps`-escaped.
- **Licensing is a feature:** the VRM classifier reads permission flags from the file, not the licence name; the fetcher refuses anything it cannot prove permissive.

### 6.9 Gateway and egress

- **Gateway:** FastAPI + WebSocket + mDNS in `brahma_connect/gateway/`. WebSocket connections authenticate a paired device. **HTTP control routes do not** (`/gateway/info`, `/pair`, `/devices`, `/logs`, `/pending`, approve/reject), and the default host is `0.0.0.0`.
- **Egress:** `core/llm_gateway.py` is the single choke point for auxiliary model calls; provider kill switches apply.
- **Rule:** the gateway must default to loopback and every control route must require a paired device before it is considered safe.

### 6.10 Legacy autonomous executor (architectural debt)

This is the part the harness docs do not describe, and it matters.

- **Files:** `agent/planner.py`, `agent/executor.py`, `agent/task_queue.py`, `agent/error_handler.py`. They are from the pre-fork/upstream line (initial commit), not the harness.
- **Entry point:** the `agent_task` native tool (`main.py` declaration; `core/tool_exec.py` handler) submits a goal to a `TaskQueue`, which runs `AgentExecutor`.
- **Behavior:** `create_plan()` asks a model for up to five steps; `_call_tool()` dispatches directly to `actions/*`; `analyze_error()` replans; `_run_skill_forge()` can forge and immediately execute a new feature.
- **Why it is debt:** it bypasses `AgentRuntime`, the policy, the approval banner, process containment, sessions, and the event stream. It is a fourth door with no gate.
- **Recommendation:** disable `agent_task` in the advertised surface now, then either delete the path or re-implement it as a harness workflow (`run_subagent` + planner schema) behind the same runtime. Do not port it forward as-is.

---

## 7. Security workstream

### 7.1 Finding status (re-verified 2026-11-01)

| # | Finding | Status | Evidence in the current tree |
|---|---|---|---|
| 1 | Forge writes and executes model code with no trust boundary | **Open** | `core/skill_forge.py` writes `features/`; `main.py:_forge_skill` calls `DynamicToolRegistry.execute_sync` immediately. |
| 2 | Any `.py` in `plugins/` is imported at startup | **Open** | `plugin_manager.py:14` `load_plugins`, `:26` `exec_module`; no signature/allowlist. |
| 3 | Unsigned self-update / hard reset | **Open** | `updater.py:11` hardcoded `Brahma---personal`; `core/updater.py` `git reset --hard`. |
| 4 | Forge prompt instructs TLS bypass | **Open** | `core/skill_forge.py:255`, `:380` (`verify=False`, `_create_unverified_context`). |
| 5 | Unpinned runtime `pip install` | **Open** | `core/skill_crucible.py:191` (`pip install`), `:189` uninstall/reinstall. |
| 6 | Secrets recoverable (Fernet key beside DB; plaintext API keys) | **Open** | `smart_home/storage.py`; `config/api_keys.json`. |
| 7 | Crucible validation is a denylist, not a sandbox | **Open** | 12 string checks + same-user subprocess + timeout. |
| 8 | High-risk automation without privilege separation | **Open** | One process, one token, all tools in-process. |
| + | Gateway `0.0.0.0`, unauthenticated HTTP control routes | **Open** | `server.py:33` default host; routes at `:358–407` have no auth dependency. |
| + | Model-facing `confirmed` parameter | **Open** | `main.py:1237`; `core/confirm.py:7` accepts model-supplied `confirmed=yes`. |

**Conclusion:** the fork's security posture is still upstream's. The 1.0.1 work improved providers, tests, and decomposition, not trust boundaries.

### 7.2 The target model

> **The model may propose. The user may authorize. The policy decides. The sandbox executes. The broker holds the keys.**

The roadmap's process split — UI (only approval surface) · Orchestrator (model loop, policy, taint) · Model client (network only) · SkillHost (AppContainer/restricted token) · ToolHost (bounded actions) · SecretBroker (DPAPI, no network) · Gateway (loopback) · Updater (signed, separate) — is **Designed**, not built. Its IPC rules (named pipes with explicit DACLs, length-prefixed JSON, capability grant per request, audit every decision) are the right direction and should not be diluted into "just add a confirmation".

### 7.3 Workstreams and the Phase 0 batch

WS-1 skill lifecycle · WS-2 SkillHost sandbox · WS-3 dependency supply chain · WS-4 plugin trust · WS-5 shell/Python/MCP/`SKILL.md` containment · WS-6 signed updates · WS-7 secrets broker · WS-8 gateway · WS-9 prompt injection/trust policy · WS-10 privilege separation and audit.

**Phase 0 is the only part that can land without a new architecture, and it is the highest value per line of code:**

1. `_forge_skill` returns a draft; never `execute_sync`.
2. `DynamicToolRegistry.initialize()` loads `features/*.py` only from a signed/approved index.
3. Crucible executes module-level code only in a subprocess with a timeout.
4. `resolve_dependencies()` returns a plan; never calls `pip`.
5. Remove `verify=False` / `_create_unverified_context` from the forge prompts.
6. Gateway binds `127.0.0.1`, disabled by default; HTTP control routes require a paired device.
7. Disable startup updates for clones; delete the hardcoded upstream and default-Yes `reset --hard`.
8. Move `api_keys.json` and `smart_home.key` behind DPAPI (minimal migration, one release of read-only fallback).
9. Delete the model-facing `confirmed` parameter; keep the HUD gate.
10. Disable `agent_task` until it is re-based (§6.10).

**Exit criteria:** a forged skill does not execute automatically; no runtime `pip`; no plaintext keys; no unauthenticated gateway on the LAN; no silent startup pull; no ungated autonomous door.

### 7.4 How security intersects the harness

- **N4 is the same decision as WS-2/WS-5.** The harness already has the right shape (`exec.sandbox` is a policy hook, not a pretend sandbox; D36). The choice is whether `require_confined` + a `sandbox doctor` probe + a container recipe is enough, or whether SkillHost/AppContainer is required first. Recommendation: **delegated and verified confinement** — make the container the supported path, verify it with a probe, and refuse `--yolo` without it.
- **Approvals should converge.** `core/confirm.py` is the only human surface today. The roadmap wants every mutating capability (including forged skills and orchestrator verbs) to route through it. The harness already does this for its tools; native tools and the forge do not.
- **One policy, not two.** Do not build a second allowlist for plugins/forge/MCP. Extend the existing policy vocabulary; a second gate is the failure mode N7 rejected.

---

## 8. Agent & orchestrator bridge — assessment and re-basing

### 8.1 What it wanted

Brahma becomes a **top-level orchestrator**: discover every installed/reachable agent and orchestrator, publish each as a tool, route work to it, supervise it, and report completion. Tiers: local model servers and MCP servers and CLI coding agents (Tier 1); Orca/Multica/Paperclip (Tier 2); remote runtimes (Tier 3). Six discovery layers, a uniform `AgentAdapter` ABC, an `AgentRecord` registry, durable artifacts, per-agent confirmation, fan-out budgets, and a UI page.

The design is serious, internally coherent, and was reviewed once (2026-10-16) for symbol survival. **None of it is implemented.** `core/agent_bridge/` does not exist; the only "agent" code that predates the harness is the legacy planner/executor (§6.10), which is a different thing.

### 8.2 What survives

Keep these ideas; they are independently good:

- **Uniform adapter contract** (`detect/probe/connect/verbs/call/close`) and a golden-fixture contract test per adapter.
- **Read-only discovery** that never spawns, never raises, and is fully opt-in at every layer (ADR-014).
- **Durable artifacts** with `output_ref` and one pinned retrieval tool (ADR-015) — this is the right answer to "the model needs the full output but the transcript must stay bounded".
- **Per-agent mutation gating**, `dry_run` for `assign*`/`run_*`, and fan-out budgets (ADR-011/016).
- **Self-target refusal**, recursion guard, and degraded state on schema drift.
- **Vendor catalog as data**, not code (ADR-017).
- **Union of discovery evidence** (`sources`) so the UI can explain *why* an agent appeared.

### 8.3 What conflicts with the current architecture

| ID | Conflict | Why it matters | Resolution |
|---|---|---|---|
| **C-1** | The design routes agents through `DynamicToolRegistry` + `core/confirm.py`, not `AgentRuntime.execute`. | It creates a second permission path for exactly the highest-risk capability (spawning autonomous agents). | Re-base every published agent tool as a `ToolSpec` (`tool_class="native"` or `"exec"`) behind `AgentRuntime`. `core/confirm.py` remains the UI, not the policy. |
| **C-2** | It proposes extracting/using an MCP client transport as a third client alongside `core/mcp_manager.py` and `agent/mcp.py`. | Two clients are already a documented split by layer; a third makes refresh, timeouts, and provenance inconsistent. | Choose by layer: harness-discovered agents use `agent/mcp.py`; voice-only agents use `core/mcp_manager.py`. Do not introduce a third. If a shared transport is wanted, extract it *under* those two, not beside them. |
| **C-3** | CLI agents spawn outside `run_subagent`. | A second delegation path violates I-08 and duplicates budget, approval, and recursion rules. | A CLI agent invocation is a tool call; if it needs a loop, it is a `run_subagent` child with a different adapter. No third construction path. |
| **C-4** | `main.py` ≤ 3,903 and `ui.py` ≤ 692 guards are already violated. | The design's "zero core-file growth" premise is false; a new 4,000-line surface would make the shell unmaintainable. | Build the bridge as `core/agent_bridge/` only after the security batch; keep `main.py` wiring to a single `configure()` call. |
| **C-5** | The design assumes a 128-tool ceiling and a fixed surface, but N agents × M verbs is unbounded. | Publishing every verb re-creates the original `1007` failure. | Adapter verbs are unadvertised by default (reachable through `search_tools`/`call_tool`); pin at most one `run_task` per agent. |
| **C-6** | `AgentRecord` persists endpoint/env data. | Tokens in a registry file are a new secret store. | Store references (env var names, keyring ids), never values; short-lived JWTs are re-fetched, never persisted. |
| **C-7** | The design predates the harness's `AgentRuntime` and treats `agent/executor.py` as the decomposition loop. | That executor is the ungated legacy path, not the architecture. | Ignore the old executor; build on `AgentLoop`. |

### 8.4 Re-base plan (do not start before Phase 0 + the N4 decision)

1. **Decide the boundary.** Confirm that every discovered agent becomes a `ToolSpec` behind `AgentRuntime`, with `tool_class` and per-verb mutation defaults. Write this as ADR-018 (it supersedes the bridge's execution assumptions).
2. **Discovery only.** `core/agent_bridge/detectors.py` + `AgentRecord` registry, opt-in, no spawns, contract tests. No publishing.
3. **One adapter family at a time.** Start with MCP servers (already have transports), then local OpenAI-compatible servers, then one CLI adapter behind `run_subagent`/`run_command`. Each adapter gets a golden fixture and a degraded state.
4. **Artifacts before fan-out.** Durable output + `read_agent_artifact` before any orchestrator connector, so a report can never be lost.
5. **Orchestrator connectors last.** They are the guessed-contract, non-idempotent, spend-capable tier. No connector ships without `dry_run`, a budget, a confirmation, and a recursion guard.
6. **Never port the WBS verbatim.** Its phase numbering and task IDs are a planning artifact; the architecture above is the contract.

**Gate to start:** Phase 0 security batch landed; N4 decided; `agent_task` disabled; one written decision that the bridge is a tool source, not a second runtime.

---

## 9. Upstream integration

### 9.1 State

- Merge base `e0bd3c5`; our `main` is 23+ harness batches ahead; `upstream/main` @ `bfb2868` (2026-10-27), **7 commits** ahead of the merge base.
- Upstream files: installers/CI (`987f47d`, `c5e8162`, `c8d9c0f`), a voice-pipeline overhaul (`9dd02cf`), package bundling (`b3c4160`), OpenRouter key reload (`62b1591`), privacy policy (`bfb2868`).
- The original plan (`+29,857/−3,186`, 81 files) still describes the strategy, but its commit list stops at 3; use §9.2 for the current set.

### 9.2 Strategy

**Hybrid selective port. Never `git merge upstream/main`.** The fork's decomposition and safety rails (kill switches, budgeted tool surface, gateway enforcement) were built against a monolith; a merge would silently undo them.

| Order | Upstream work | Action | Gate |
|---|---|---|---|
| 1 | Installer/CI (`c5e8162`, `c8d9c0f`, `b3c4160`, `bfb2868`) | Copy with adaptation; treat installers as unsigned until proven otherwise. | No code-surface change. |
| 2 | Self-contained new features (image generation, REA/web cloning, sentinel HUD) | Port into `actions/`/`core/` behind existing seams; add `ToolSpec`s, not `elif`s. | Provider/egress gate; no new raw shell. |
| 3 | `62b1591` OpenRouter key reload | Port as provider plumbing. | Provider tests. |
| 4 | `9dd02cf` voice overhaul | **Port selectively; do not merge wholesale.** It adds `bash`, `python_repl`, an MCP manager, and an unauthenticated `/gateway/execute`, plus auto-approved pairing. | Security Phase 0 first; any new execution surface goes through `AgentRuntime`, not raw model tools. |
| 5 | `core/prompt.txt`, `llm_client.py`, `main.py`, `ui.py`, `dynamic_registry.py` | Surgical adaptation only. | Full gates + manual smoke. |

**Hard rules:** no wholesale merge of `9dd02cf`; no `/gateway/execute` without auth; no `shell=True` MCP spawn; no raw `bash`/`python_repl` advertised to a model; no unsigned update path.

### 9.3 Why upstream is mid-priority, not first

Every upstream feature adds surface. The fork's own boundary work (Phase 0, N4) reduces surface. Doing the reduction first means the port lands into a system that can contain it; doing the port first means defending new surface with the old, absent boundary.

---

## 10. Operating model

### 10.1 Batch workflow

The batch history is the template:

1. One concern per batch, one commit on `main`, message prefixed `agent:` (or `security:`, `docs:`).
2. Tests and docs in the same commit.
3. Run the gates before claiming done; paste the numbers into the status doc.
4. Add a `### Dn — …` entry for any fork in the road.
5. Update the tracker: tick acceptance boxes, move landed items, re-rank what remains.
6. Do not leave a half-landed batch; if it must stop, record the exact resume point.

### 10.2 Gates

```powershell
py -3.11 -m ruff check .
$env:QT_QPA_PLATFORM="offscreen"; py -3.11 -m pytest tests -q
py -3.11 -m pytest tests -q -k agent
py -3.11 -m agent run "say hello" --provider stub --json --no-session
```

Baseline: ruff clean; **744 passed, 1 skipped**; `-k agent` **387 passed, 1 skipped**; full suite ~110 s. Python 3.11 is mandatory for the gates; the repo `.venv` and default `python` are 3.14 and lack dev tools.

### 10.3 Definition of done

A batch is done when:

- the behavior is reachable from its real door (voice, GUI text, or CLI) — not just unit-tested;
- the policy/approval path is exercised if the tool can mutate;
- the failure mode is a diagnostic, not a crash or a silent downgrade;
- docs state the limit honestly;
- the gates pass on 3.11 with the offscreen platform;
- `agent/` still imports with only pytest installed;
- no new `elif` in `core/tool_exec.py`, no second subprocess path, no second permission boundary.

### 10.4 Documentation maintenance

- This document is the **map**; the area docs are the **territory**. When a batch changes an invariant, a status word, or a subsystem contract, update this document in the same commit.
- Never update an old changelog to "correct" it. Add a dated note or supersede it here.
- When a proposal lands, move its row from Tier 2/4 to Tier 1 and say which sections are now historical.
- Keep line numbers out of new prose where a symbol will do.

---

## 11. Decision register

### 11.1 Harness decisions (D1–D42, full text in `AGENTIC-HARNESS-STATUS.md`)

| Theme | Load-bearing decisions |
|---|---|
| Placement and shape | D1 harness in `agent/`; D2 OpenAI lowercase schema canonical; D3 one enforcement point; D15 `init` never clobbers |
| Permissions | D4 headless = no human; D5 default policy; D8 no repo policy file; D19 session approvals scoped; D24 hot reload; D36 isolation is a policy hook |
| Tools and loop | D6 three primitives pinned; D9 edit uniqueness; D12 `first_response`; D14 reuse turn-one surface; D16 cancellation flag; D17 retries in the loop; D20/D30/D32 dispatch evolution (per-call path dependencies wins); D33 delegation is one child |
| Context and session | D10 compaction; D11 additive tables; D18 upward instruction walk; D25 path-driven subdirectory walk; D27 no stored system row; D31 voice archived, not replayed |
| Providers and UX | D13 voice tools in a worker; D22 banner says what happens; D23 real usage; D26 streaming additive; D28 diff folding is a view; D29 one fallback; D35 colour only for a person |
| MCP and skills | D37 MCP behind the same boundary; D38 one GUI session; D39 skills listed cheaply; D40 one project-prompt seam; D41 refresh at turn boundary; D42 one store, two doors |

### 11.2 Bridge ADRs (ADR-001…017)

Accepted 2026-10-17, but **acceptance is not authorization to build**: they were accepted against a design that predates the harness. ADR-006 (MCP extraction) and ADR-011 (route through `core/confirm`) are the ones that conflict with §8.3 and must be superseded by ADR-018 before implementation. ADR-001's "no core growth" premise also needs revisiting, since both shell guards are already breached.

### 11.3 Open decisions requiring the owner

| # | Decision | Options | Recommendation |
|---|---|---|---|
| **O-1** | Confinement for `run_command` (N4) | container-first / AppContainer-first / prefix-is-enough | **Container-first with a verified probe** (`require_confined`, `sandbox doctor`). AppContainer silently fails (D36); prefix alone is unverified. |
| **O-2** | Subagent fan-out (N5) | keep sequential / coordinator with model selection | **Keep sequential** for mutation; allow a read-only fan-out batch over one runtime later. A coordinator that owns its own enforcement is a second runtime. |
| **O-3** | Security Phase 0 scope | all ten items in one batch / split | **Split into two batches:** (a) forge + pip + TLS + confirmed + `agent_task`; (b) gateway + updates + secrets + plugins. Each is independently verifiable. |
| **O-4** | Bridge disposition | build as designed / re-base / shelve | **Re-base** (§8.4) after O-1 and Phase 0. Do not shelve: discovery + artifacts + adapter contract are valuable. |
| **O-5** | Upstream timing | port now / after Phase 0 | **After Phase 0**, and never merge `9dd02cf` wholesale. |
| **O-6** | Native-tool policy | leave `elif` chain / migrate incrementally | **Migrate by capability class**, starting with `exec` and `write`, wrapping each in a `ToolSpec` behind `AgentRuntime`. Do not attempt a big-bang rewrite. |
| **O-7** | `agent_task` | keep / disable / re-implement | **Disable now**, re-implement as a harness workflow later. It is an ungated fourth door. |
| **O-8** | Deferred-tool `1008` | prompt-only / backend-agnostic proxy | **Measure prompt-only first**; if it still fails, build a routing shim inside the existing executor, not a new path. |

---

## 12. Work queues (ranked by value per unit of risk)

### Q0 — Stop the bleeding (days; no new architecture)

1. Forge draft-only: remove `execute_sync` from `main.py:_forge_skill` and `agent/executor._run_skill_forge`.
2. Remove TLS-bypass instructions from both forge prompts.
3. `resolve_dependencies()` returns a plan; never `pip`.
4. Delete the model-facing `confirmed` parameter; keep the HUD gate.
5. Gateway: bind `127.0.0.1`, default off, authenticate HTTP control routes.
6. Disable startup updates for clones; remove the hardcoded remote and the unconditional `git reset --hard` (the UI applies it without a confirmation step).
7. Plugin loading: opt-in allowlist; do not import unsigned `.py` from a writable directory.
8. Secrets: DPAPI for `api_keys.json` and `smart_home.key`; one release of read-only fallback.
9. Disable the `agent_task` declaration and its handler.
10. Update `SECURITY-ARCHITECTURE-ROADMAP.md` checkboxes and this document's status table.

### Q1 — Boundary and harness correctness (1–2 weeks)

1. Decide O-1/O-2 (N4/N5) and record ADR-018 if the bridge boundary changes.
2. Migrate the `exec` and `write` native tools to `ToolSpec`s behind `AgentRuntime` (O-6).
3. Bring the voice MCP manager to refresh parity, or document the difference as permanent.
4. Resolve the deferred-tool `1008` with a measured prompt-only trial.
5. Correct any remaining doc drift (counts, guards, roots).

### Q2 — Bridge v2 (after Q0/Q1)

Discovery → registry → MCP adapter → local-model adapter → one CLI adapter behind `run_subagent` → artifacts → orchestrator connectors last. One adapter per batch, each with a contract test and a degraded state.

### Q3 — Upstream port (after Q0)

Installer/CI → self-contained features → provider plumbing → selective voice work. No wholesale merge.

### Q4 — Assurance (continuous)

Red-team suite in CI, audit log, reproducible builds/SBOM, external review of sandbox/updater/gateway, key-rotation drill.

---

## 13. Open architectural questions (not yet decisions)

These are the questions this synthesis could not answer from the documents or the code. They should be answered before Q2.

1. **What is the long-term relationship between the native action catalogue and the harness?** The repo has two tool worlds: ~60 hand-written actions with no policy, and 8 primitives with a policy. Is the end state "everything is a `ToolSpec`", or "native actions are a trusted, separately-reviewed surface"?
2. **Is the gateway a product feature or a development convenience?** That determines whether it needs device auth only (product) or full pairing + audit + loopback (both).
3. **Does "orchestrator" mean Brahma supervises other agents, or Brahma is a peer in a fleet?** The bridge says strict hierarchy; ADR-013 defers peers. The answer changes the recursion guard, the budget model, and whether Tier 3 exists at all.
4. **What is the trusted-input policy for a cloned repository?** `AGENTS.md` and `SKILL.md` already speak with project authority; the security roadmap wants taint tracking. Today there is no answer between "trust the tree" and "dual-LLM".
5. **How much of the forge should survive?** Phase 0 makes it draft-only. Is the product goal a reviewed skill marketplace, a local experiment, or removal?
6. **What is the release model?** Unsigned clones, an unsigned installer, and a hardcoded update remote cannot coexist with a security claim. Pick one: developer-only, signed releases, or no self-update.
7. **Does the harness own the voice loop eventually?** Today Gemini's session is a separate loop with shared tools. If voice gains multi-step reasoning, the loop split becomes the next architectural fork.

---

## Appendix A — Document inventory

| File | Lines | Tier | Status |
|---|---:|---|---|
| `ARCHITECTURE-SPEC.md` | this | 1 | Living map |
| `AGENTIC-HARNESS.md` | 1,166 | 1 | Authoritative |
| `AGENTIC-HARNESS-STATUS.md` | 1,197 | 1 | Authoritative decisions |
| `AGENTIC-HARNESS-NEXT.md` | 328 | 1 | Authoritative tracker |
| `ONE-391-tool-index.md` | 182 | 1 | Authoritative design |
| `ONE-391-avatar-backends.md` | 448 | 1 | Authoritative design |
| `skills-library-jit-on-demand-skills.md` | 263 | 1 | Authoritative, root count stale |
| `SECURITY-ANALYSIS-ISSUE-4.md` | 173 | 2 | Current findings |
| `SECURITY-ARCHITECTURE-ROADMAP.md` | 1,190 | 2 | Proposed |
| `UPSTREAM-INTEGRATION-PLAN.md` | 777 | 2 | Planning; commit list stale |
| `DEFERRED-TOOL-PROXY-MISSION.md` | 110 | 2 | Open investigation |
| `CODE-REVIEW-TODO.md` | 899 | 3 | Historical rules |
| `VERSION-1.0.1-LOG.md` | 1,287 | 3 | Historical |
| `VERSION-1.0.1-CHANGES.md` | 1,037 | 3 | Historical |
| `ONE-391-changes.md` | 332 | 3 | Historical |
| `ONE-391-next-steps.md` | 184 | 3 | Historical/superseded |
| `agent-orchestrator-bridge.md` | 1,330 | 4 | Aspirational |
| `AGENT-BRIDGE-BUILD-TODO.md` | 991 | 4 | Aspirational |
| `AGENT-BRIDGE-ADR.md` | 353 | 4 | Accepted but superseded in part |

## Appendix B — Glossary

| Term | Meaning |
|---|---|
| **Door** | A user-facing entry point that can cause a tool call: voice, GUI text, CLI. The legacy autonomous path is an unsupported fourth. |
| **Harness tool** | A `ToolSpec` registered in `agent/spec.py` and dispatched through `AgentRuntime`. |
| **Native tool** | A declaration in `main.py:TOOL_DECLARATIONS` dispatched by the `core/tool_exec.py` `elif` chain. |
| **Fence** | A call with no schedulable effect (`exec`, native, approval, denial, path-less write) that serializes dispatch around it. |
| **Pinned** | Always advertised, regardless of retrieval. |
| **Meta-tool** | A discovery primitive (`search_tools`, `call_tool`, …) that reaches the unadvertised catalogue. |
| **Skill (core)** | A catalogue entry in the Skills Library; instructional or executable. |
| **Skill (project)** | A `SKILL.md` playbook under `.brahma/skills`; instructional only. |
| **Forge** | Model-generated code written to `features/` and hot-loaded. The untrusted path. |
| **Crucible** | The forge's quality gate (AST denylist + same-user subprocess). Not a sandbox. |
| **Bridge** | The unbuilt agent/orchestrator discovery-and-routing proposal. |
| **Session** | A durable `agent_*` row group in `workspace_store.sqlite3`. |
| **Turn** | One model call plus its tool calls/results. |

## Appendix C — Baseline verification record

| Check | Result |
|---|---|
| `git log -1` | `531b961 agent: share project skills with the Skills Library` (2026-10-31) |
| Working tree | clean; `main` in sync with `origin/main` |
| `ruff check .` | clean |
| `pytest tests -q` (offscreen) | 744 passed, 1 skipped |
| `pytest tests -q -k agent` | 387 passed, 1 skipped |
| Focused skills tests | 84 passed |
| `upstream/main` | `bfb2868` (2026-10-27); 7 commits past merge base |
| Security findings | all 8 present; gateway and `confirmed` extras present |
| Bridge implementation | none; `core/agent_bridge/` absent |
| Legacy executor | present and reachable via `agent_task` |

---

*This document is the orientation layer. When a batch changes an invariant, a status word, or a subsystem contract, change it here in the same commit. When it disagrees with the code, believe the code and fix this file.*
